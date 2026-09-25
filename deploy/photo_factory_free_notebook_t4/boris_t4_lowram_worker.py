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
from PIL import Image, ImageOps


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
    "free-t4-lowram-1",
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

WIDTH = int(
    os.environ.get(
        "BORIS_IMAGE_WIDTH",
        "640",
    )
)

HEIGHT = int(
    os.environ.get(
        "BORIS_IMAGE_HEIGHT",
        "448",
    )
)

STEPS = int(
    os.environ.get(
        "BORIS_STEPS",
        "18",
    )
)

MODEL = os.environ.get(
    "BORIS_FREE_MODEL",
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
)

IP_ADAPTER_REPO = os.environ.get(
    "BORIS_IP_ADAPTER_REPO",
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
            "BORIS_ENROLL_CODE missing"
        )

    response = requests.post(
        BASE_URL
        + "/api/photo-factory-v31/enroll",
        json={
            "code": ENROLL_CODE,
        },
        timeout=30,
    )

    print(
        "ENROLL_HTTP=",
        response.status_code,
        flush=True,
    )

    if response.status_code != 200:
        print(
            response.text,
            flush=True,
        )

    response.raise_for_status()

    data = response.json()

    TOKEN = data[
        "worker_token"
    ]

    print(
        "ENROLL=PASS",
        flush=True,
    )

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
                    "free_sd15_ipadapter_t4_lowram",
                "gpu_name": gpu_name,
                "vram_gb": vram_gb,
                "state": state,
            },
            timeout=20,
        )

        print(
            "HEARTBEAT",
            state,
            "HTTP",
            response.status_code,
            flush=True,
        )

        response.raise_for_status()

    except Exception as exc:

        print(
            "HEARTBEAT_WARNING",
            repr(exc),
            flush=True,
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

    print(
        "CLAIM_HTTP=",
        response.status_code,
        flush=True,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get(
        "claimed"
    ):
        return None

    return data


def download_reference(
    url: str,
) -> Image.Image:

    full_url = (
        url
        if url.startswith("http")
        else BASE_URL + url
    )

    response = requests.get(
        full_url,
        headers=headers(),
        timeout=60,
    )

    response.raise_for_status()

    image = Image.open(
        io.BytesIO(
            response.content
        )
    )

    image = image.convert(
        "RGB"
    )

    return image


def prepare_reference(
    image: Image.Image,
) -> Image.Image:

    # Fit exact inference size without keeping a huge
    # source image in RAM.
    image = ImageOps.exif_transpose(
        image
    )

    image = ImageOps.fit(
        image,
        (WIDTH, HEIGHT),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    return image


def reject(
    job_id: str,
    index: int,
    reason: str,
) -> None:

    response = requests.post(
        BASE_URL
        + "/api/photo-factory/worker/reject",
        headers={
            **headers(),
            "Content-Type":
                "application/json",
        },
        json={
            "job_id": job_id,
            "scenario_index": index,
            "worker_id": WORKER_ID,
            "reason": reason[:900],
        },
        timeout=30,
    )

    print(
        "REJECT_HTTP=",
        response.status_code,
        flush=True,
    )


def upload_result(
    job_id: str,
    index: int,
    image_path: Path,
) -> None:

    with image_path.open(
        "rb"
    ) as file:

        response = requests.post(
            (
                BASE_URL
                + "/api/photo-factory/"
                + f"worker/result/{job_id}/{index}"
            ),
            params={
                "worker_id":
                    WORKER_ID,
            },
            headers=headers(),
            files={
                "file": (
                    image_path.name,
                    file,
                    "image/jpeg",
                )
            },
            timeout=180,
        )

    print(
        "RESULT_HTTP=",
        response.status_code,
        flush=True,
    )

    if response.status_code != 200:
        print(
            response.text[:2000],
            flush=True,
        )

    response.raise_for_status()

    print(
        "RESULT_UPLOAD=PASS",
        flush=True,
    )


def build_engine():
    import torch

    from diffusers import (
        StableDiffusionImg2ImgPipeline,
    )

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA unavailable"
        )

    gpu_name = (
        torch.cuda
        .get_device_name(0)
    )

    props = (
        torch.cuda
        .get_device_properties(0)
    )

    vram = (
        props.total_memory
        / 1024**3
    )

    print(
        "GPU=",
        gpu_name,
        flush=True,
    )

    print(
        "VRAM_GB=",
        round(vram, 2),
        flush=True,
    )

    if vram < 10:
        raise RuntimeError(
            f"Not enough VRAM: {vram:.1f}GB"
        )

    print(
        "LOW_RAM_PROFILE=SD15_IP_ADAPTER",
        flush=True,
    )

    print(
        "IMAGE_SIZE=",
        f"{WIDTH}x{HEIGHT}",
        flush=True,
    )

    print(
        "STEPS=",
        STEPS,
        flush=True,
    )

    heartbeat(
        "loading_model",
        gpu_name,
        round(vram, 2),
    )

    pipe = (
        StableDiffusionImg2ImgPipeline
        .from_pretrained(
            MODEL,
            torch_dtype=torch.float16,
            use_safetensors=True,
            safety_checker=None,
            requires_safety_checker=False,
        )
    )

    print(
        "SD15_LOAD=PASS",
        flush=True,
    )

    # V3.2.3:
    # DO NOT call enable_attention_slicing() with this IP-Adapter profile.
    #
    # diffusers load_ip_adapter() recreates attention processors.
    # SlicedAttnProcessor requires slice_size in its constructor and
    # cannot safely be reconstructed by this loader path.
    #
    # SD1.5 at 640x448 fits Tesla T4 without attention slicing.
    print(
        "ATTENTION_SLICING=OFF",
        flush=True,
    )

    pipe.load_ip_adapter(
        IP_ADAPTER_REPO,
        subfolder="models",
        weight_name="ip-adapter_sd15.bin",
    )

    print(
        "IP_ADAPTER_LOAD=PASS",
        flush=True,
    )

    # Identity weight.
    pipe.set_ip_adapter_scale(0.88)

    # IMPORTANT:
    # For SD1.5 this model set fits a T4 directly.
    # Keeping it on GPU avoids huge CPU offload RAM use.
    pipe = pipe.to(
        "cuda"
    )

    try:
        pipe.enable_vae_slicing()
    except Exception:
        pass

    try:
        pipe.enable_vae_tiling()
    except Exception:
        pass

    print(
        "MODEL_TO_CUDA=PASS",
        flush=True,
    )

    heartbeat(
        "ready",
        gpu_name,
        round(vram, 2),
    )

    return (
        pipe,
        gpu_name,
        round(vram, 2),
    )


def generate(
    pipe,
    task: dict[str, Any],
) -> Image.Image:

    import torch

    references = task.get(
        "references",
        [],
    )

    if not references:
        raise RuntimeError(
            "No reference images"
        )

    # Use first real reference for initial proof.
    reference = download_reference(
        references[0][
            "download_url"
        ]
    )

    reference = prepare_reference(
        reference
    )

    scenario = task[
        "scenario"
    ]

    prompt = scenario.get(
        "prompt",
        "",
    )

    prompt += """
CRITICAL IDENTITY REQUIREMENT:
This must be the exact same physical vehicle shown in the reference images.

Do not redesign, reinterpret or modernize the vehicle.
Preserve exact generation, body shell, proportions, wheelbase,
roof line, hood geometry, trunk geometry, bumper geometry,
headlight shape, taillight shape, grille geometry, mirrors,
window geometry, door geometry, wheels, tire proportions,
chrome trim and all distinctive physical details.

Preserve exact black paint color and realistic automotive clear-coat reflections.

REALISM REQUIREMENT:
Photorealistic real-world automotive marketplace photography.
Real camera optics.
Natural perspective.
Physically correct wheels and tires.
Straight body panels.
Correct panel gaps.
Correct glass.
Correct headlights.
Correct grille.
Correct badges.
Realistic asphalt and environment.
Natural shadows.
Natural reflections.
Realistic exposure.
No CGI appearance.
No concept-car styling.
No artistic reinterpretation.

The result must look like a normal photograph taken with a real camera,
not an AI-generated vehicle.

No text overlay.
No watermark.
"""

    negative = """
different vehicle,
different generation,
different trim,
different headlights,
different taillights,
different grille,
different wheels,
different mirrors,
changed body proportions,
changed wheelbase,
changed roofline,
concept car,
futuristic car,
modified car,
body kit,
fake vents,
extra lights,
missing lights,
warped body,
melted body,
asymmetric vehicle,
bent wheels,
oval wheels,
deformed tires,
incorrect wheel perspective,
floating wheels,
extra wheels,
duplicate wheels,
crooked grille,
deformed headlights,
deformed taillights,
incorrect badge,
fake badge,
gibberish license plate,
distorted license plate,
unreadable plate,
AI artifacts,
CGI,
3D render,
video game,
plastic texture,
cartoon,
illustration,
painting,
overprocessed,
oversharpened,
oversaturated,
unrealistic reflections,
impossible reflections,
incorrect shadows,
floating vehicle,
blurred vehicle,
low detail,
low resolution,
compression artifacts,

different vehicle, different generation, different body shape,
changed headlights, changed grille, changed wheels,
extra wheels, extra doors, duplicate parts,
deformed car, melted geometry, cartoon, illustration,
CGI, unrealistic reflections, watermark, text
"""

    generator = (
        torch.Generator(
            device="cuda"
        )
        .manual_seed(
            random.randint(
                1,
                2_000_000_000,
            )
        )
    )

    print(
        "INFERENCE_START",
        flush=True,
    )

    result = pipe(
        prompt=prompt,
        negative_prompt=negative,
        image=reference,
        ip_adapter_image=reference,
        strength=0.28,
        guidance_scale=6.0,
        num_inference_steps=STEPS,
        generator=generator,
    )

    print(
        "INFERENCE=PASS",
        flush=True,
    )

    return result.images[0]


def main() -> int:

    print()
    print("=" * 70)
    print(" BORIS FREE T4 LOW-RAM WORKER")
    print("=" * 70)
    print()

    enroll()

    pipe, gpu_name, vram = (
        build_engine()
    )

    print()
    print(
        "BORIS T4 LOW-RAM WORKER READY",
        flush=True,
    )

    print(
        "MAX_TASKS=",
        MAX_TASKS,
        flush=True,
    )

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
                "NO_QUEUED_TASK",
                flush=True,
            )

            time.sleep(
                POLL_SECONDS
            )

            continue

        job_id = task[
            "job_id"
        ]

        scenario = task[
            "scenario"
        ]

        index = int(
            scenario[
                "index"
            ]
        )

        print()
        print(
            "CLAIMED",
            job_id,
            "#",
            index,
            flush=True,
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
                / f"{job_id}_{index:03d}.jpg"
            )

            image.convert(
                "RGB"
            ).save(
                target,
                "JPEG",
                quality=93,
                optimize=True,
            )

            print(
                "IMAGE_SAVE=PASS",
                target,
                target.stat().st_size,
                flush=True,
            )

            upload_result(
                job_id,
                index,
                target,
            )

            completed += 1

        except Exception as exc:

            print(
                "GENERATION_FAIL",
                type(exc).__name__,
                str(exc),
                flush=True,
            )

            try:
                reject(
                    job_id,
                    index,
                    (
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    ),
                )
            except Exception as reject_exc:
                print(
                    "REJECT_FAIL",
                    repr(reject_exc),
                    flush=True,
                )

            raise

    heartbeat(
        "finished",
        gpu_name,
        vram,
    )

    print()
    print("=" * 70)
    print(" FIRST LOW-RAM TASK COMPLETE")
    print("=" * 70)
    print("completed=", completed)
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
