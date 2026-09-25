#!/usr/bin/env python3

from __future__ import annotations

import io
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image


BASE_URL = os.environ.get(
    "BORIS_BASE_URL",
    "https://boris-ai.pro",
).rstrip("/")

ENROLL_CODE = os.environ.get(
    "BORIS_ENROLL_CODE",
    "",
).strip()

TOKEN = os.environ.get(
    "BORIS_WORKER_TOKEN",
    "",
).strip()

WORKER_ID = os.environ.get(
    "BORIS_WORKER_ID",
    "free-notebook-1",
)

MAX_TASKS = int(
    os.environ.get(
        "BORIS_MAX_TASKS",
        "1",
    )
)

POLL_SECONDS = int(
    os.environ.get(
        "BORIS_POLL_SECONDS",
        "5",
    )
)

MODEL = os.environ.get(
    "BORIS_FREE_MODEL",
    "stabilityai/stable-diffusion-xl-base-1.0",
)

IP_ADAPTER = os.environ.get(
    "BORIS_IP_ADAPTER",
    "h94/IP-Adapter",
)

OUTPUT = Path(
    os.environ.get(
        "BORIS_OUTPUT",
        "/content/boris-output",
    )
)

OUTPUT.mkdir(
    parents=True,
    exist_ok=True,
)


def enroll() -> str:
    global TOKEN

    if TOKEN:
        return TOKEN

    if not ENROLL_CODE:
        raise RuntimeError(
            "BORIS_ENROLL_CODE is required"
        )

    response = requests.post(
        BASE_URL
        + "/api/photo-factory-v31/enroll",
        json={
            "code": ENROLL_CODE,
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    TOKEN = data[
        "worker_token"
    ]

    return TOKEN


def headers() -> dict[str, str]:
    return {
        "X-Boris-Worker-Token":
            TOKEN,
    }


def heartbeat(
    state: str,
    gpu_name: str,
    vram_gb: float,
) -> None:

    try:
        response = requests.post(
            BASE_URL
            + "/api/photo-factory-v31/worker-heartbeat",
            headers=headers(),
            json={
                "worker_id": WORKER_ID,
                "engine":
                    "free_sdxl_ipadapter",
                "gpu_name": gpu_name,
                "vram_gb": vram_gb,
                "state": state,
            },
            timeout=20,
        )

        response.raise_for_status()

    except Exception as exc:
        print(
            "heartbeat warning:",
            repr(exc),
        )


def claim() -> dict[str, Any] | None:

    response = requests.post(
        BASE_URL
        + "/api/photo-factory/worker/claim",
        params={
            "worker_id": WORKER_ID,
        },
        headers=headers(),
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get(
        "claimed"
    ):
        return None

    return data


def get_image(
    path: str,
) -> Image.Image:

    url = (
        path
        if path.startswith("http")
        else BASE_URL + path
    )

    response = requests.get(
        url,
        headers=headers(),
        timeout=60,
    )

    response.raise_for_status()

    return Image.open(
        io.BytesIO(
            response.content
        )
    ).convert("RGB")


def reject(
    job: str,
    index: int,
    reason: str,
) -> None:

    requests.post(
        BASE_URL
        + "/api/photo-factory/worker/reject",
        headers={
            **headers(),
            "Content-Type":
                "application/json",
        },
        json={
            "job_id": job,
            "scenario_index": index,
            "worker_id": WORKER_ID,
            "reason": reason[:900],
        },
        timeout=30,
    ).raise_for_status()


def upload(
    job: str,
    index: int,
    image_path: Path,
) -> None:

    with image_path.open(
        "rb"
    ) as fh:

        response = requests.post(
            (
                BASE_URL
                + "/api/photo-factory/"
                + f"worker/result/{job}/{index}"
            ),
            params={
                "worker_id": WORKER_ID,
            },
            headers=headers(),
            files={
                "file": (
                    image_path.name,
                    fh,
                    "image/jpeg",
                )
            },
            timeout=180,
        )

    response.raise_for_status()


def prepare_image(
    image: Image.Image,
) -> Image.Image:

    image = image.copy()

    max_side = 1024

    scale = min(
        1.0,
        max_side
        / max(
            image.size
        ),
    )

    if scale < 1:
        image = image.resize(
            (
                int(
                    image.width
                    * scale
                ),
                int(
                    image.height
                    * scale
                ),
            ),
            Image.LANCZOS,
        )

    # SDXL likes dimensions divisible by 8.
    w = (
        image.width
        // 8
        * 8
    )

    h = (
        image.height
        // 8
        * 8
    )

    return image.resize(
        (w, h),
        Image.LANCZOS,
    )


def build_engine():
    import torch

    from diffusers import (
        AutoPipelineForImage2Image,
    )

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA GPU unavailable"
        )

    props = (
        torch.cuda
        .get_device_properties(0)
    )

    vram = (
        props.total_memory
        / 1024**3
    )

    if vram < 11:
        raise RuntimeError(
            f"GPU VRAM too small: {vram:.1f}GB"
        )

    print(
        "GPU:",
        torch.cuda.get_device_name(0),
    )

    print(
        "VRAM:",
        round(vram, 1),
        "GB",
    )

    print(
        "Loading SDXL...",
    )

    pipe = (
        AutoPipelineForImage2Image
        .from_pretrained(
            MODEL,
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
        )
    )

    print(
        "Loading IP-Adapter...",
    )

    pipe.load_ip_adapter(
        IP_ADAPTER,
        subfolder="sdxl_models",
        weight_name=(
            "ip-adapter_sdxl.bin"
        ),
    )

    pipe.set_ip_adapter_scale(
        0.82
    )

    pipe.enable_model_cpu_offload()

    try:
        pipe.enable_vae_slicing()
        pipe.enable_vae_tiling()
    except Exception:
        pass

    return (
        pipe,
        torch.cuda.get_device_name(0),
        round(vram, 2),
    )


def generate(
    pipe,
    task: dict[str, Any],
) -> Image.Image:

    import torch

    refs = task.get(
        "references",
        [],
    )

    if not refs:
        raise RuntimeError(
            "No reference images"
        )

    # V3.1 free profile:
    # use the first real reference for img2img
    # and the same identity reference for IP-Adapter.
    #
    # Multi-reference ranking comes in V3.2.
    reference = get_image(
        refs[0][
            "download_url"
        ]
    )

    reference = prepare_image(
        reference
    )

    scenario = task[
        "scenario"
    ]

    prompt = scenario[
        "prompt"
    ]

    prompt += """

Keep the exact same physical product identity.
Strong photorealism.
Marketplace commercial photography.
No invented badges, text, logos or extra physical parts.
"""

    negative = (
        "different model, different product, "
        "deformed geometry, duplicate parts, "
        "extra wheels, extra doors, malformed, "
        "cartoon, illustration, CGI, low quality, "
        "text, watermark"
    )

    seed = random.randint(
        1,
        2_000_000_000,
    )

    generator = (
        torch.Generator(
            device="cpu"
        )
        .manual_seed(
            seed
        )
    )

    result = pipe(
        prompt=prompt,
        negative_prompt=negative,
        image=reference,
        ip_adapter_image=reference,
        strength=0.58,
        guidance_scale=6.5,
        num_inference_steps=28,
        generator=generator,
    )

    return result.images[0]


def main() -> int:

    enroll()

    pipe, gpu_name, vram = (
        build_engine()
    )

    heartbeat(
        "ready",
        gpu_name,
        vram,
    )

    print()
    print(
        "BORIS FREE WORKER READY"
    )
    print(
        "MAX_TASKS=",
        MAX_TASKS,
    )
    print()

    completed = 0

    while completed < MAX_TASKS:

        heartbeat(
            "ready",
            gpu_name,
            vram,
        )

        task = claim()

        if task is None:

            print(
                "No queued task. Waiting..."
            )

            time.sleep(
                POLL_SECONDS
            )

            continue

        job = task["job_id"]

        scenario = task[
            "scenario"
        ]

        index = int(
            scenario["index"]
        )

        print()
        print(
            "CLAIMED",
            job,
            "#",
            index,
        )

        heartbeat(
            "generating",
            gpu_name,
            vram,
        )

        try:

            image = generate(
                pipe,
                task,
            )

            target = (
                OUTPUT
                / f"{job}_{index:03d}.jpg"
            )

            image.convert(
                "RGB"
            ).save(
                target,
                "JPEG",
                quality=94,
            )

            upload(
                job,
                index,
                target,
            )

            print(
                "RESULT_UPLOAD=PASS",
                target,
            )

            completed += 1

        except Exception as exc:

            print(
                "GENERATION_FAIL:",
                repr(exc),
            )

            try:
                reject(
                    job,
                    index,
                    (
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    ),
                )
            except Exception as reject_exc:
                print(
                    "REJECT_FAIL:",
                    repr(
                        reject_exc
                    ),
                )

        heartbeat(
            "ready",
            gpu_name,
            vram,
        )

    print()
    print(
        "FREE_WORKER_TASK_LIMIT_REACHED"
    )
    print(
        "completed=",
        completed,
    )

    heartbeat(
        "finished",
        gpu_name,
        vram,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
