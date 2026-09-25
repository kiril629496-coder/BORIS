#!/usr/bin/env python3

from __future__ import annotations

import io
import json
import logging
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from PIL import Image


LOG_LEVEL = os.environ.get(
    "BORIS_WORKER_LOG_LEVEL",
    "INFO",
).upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    ),
)

log = logging.getLogger(
    "boris-photo-worker"
)


BASE_URL = os.environ.get(
    "BORIS_BASE_URL",
    "",
).rstrip("/")

TOKEN = os.environ.get(
    "BORIS_WORKER_TOKEN",
    "",
).strip()

WORKER_ID = os.environ.get(
    "BORIS_WORKER_ID",
    "qwen-gpu-1",
).strip()

MODEL_ID = os.environ.get(
    "BORIS_QWEN_MODEL",
    "Qwen/Qwen-Image-Edit-2511",
)

CACHE_DIR = Path(
    os.environ.get(
        "HF_HOME",
        "/opt/boris-photo-worker/hf-cache",
    )
)

OUTPUT_DIR = Path(
    os.environ.get(
        "BORIS_WORKER_OUTPUT",
        "/opt/boris-photo-worker/output",
    )
)

POLL_SECONDS = int(
    os.environ.get(
        "BORIS_WORKER_POLL_SECONDS",
        "10",
    )
)

MAX_REFERENCES = int(
    os.environ.get(
        "BORIS_MAX_REFERENCES",
        "3",
    )
)

INFERENCE_STEPS = int(
    os.environ.get(
        "BORIS_INFERENCE_STEPS",
        "40",
    )
)

TRUE_CFG_SCALE = float(
    os.environ.get(
        "BORIS_TRUE_CFG_SCALE",
        "4.0",
    )
)

GUIDANCE_SCALE = float(
    os.environ.get(
        "BORIS_GUIDANCE_SCALE",
        "1.0",
    )
)

REQUEST_TIMEOUT = int(
    os.environ.get(
        "BORIS_HTTP_TIMEOUT",
        "120",
    )
)

STOP = False


def stop_handler(*_: Any) -> None:
    global STOP
    STOP = True


signal.signal(
    signal.SIGTERM,
    stop_handler,
)

signal.signal(
    signal.SIGINT,
    stop_handler,
)


def check_config() -> None:
    if not BASE_URL:
        raise RuntimeError(
            "BORIS_BASE_URL is required"
        )

    if not TOKEN:
        raise RuntimeError(
            "BORIS_WORKER_TOKEN is required"
        )

    if len(TOKEN) < 40:
        raise RuntimeError(
            "BORIS_WORKER_TOKEN looks invalid"
        )


def headers() -> Dict[str, str]:
    return {
        "X-Boris-Worker-Token": TOKEN,
    }


def claim() -> Dict[str, Any] | None:
    response = requests.post(
        f"{BASE_URL}/api/photo-factory/worker/claim",
        params={
            "worker_id": WORKER_ID,
        },
        headers=headers(),
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("claimed"):
        return None

    return data


def download_reference(
    relative_url: str,
) -> Image.Image:

    if relative_url.startswith(
        "http://"
    ) or relative_url.startswith(
        "https://"
    ):
        url = relative_url
    else:
        url = (
            BASE_URL
            + relative_url
        )

    response = requests.get(
        url,
        headers=headers(),
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    image = Image.open(
        io.BytesIO(
            response.content
        )
    )

    return image.convert("RGB")


def reject(
    job_id: str,
    scenario_index: int,
    reason: str,
) -> None:

    response = requests.post(
        f"{BASE_URL}/api/photo-factory/worker/reject",
        headers={
            **headers(),
            "Content-Type":
                "application/json",
        },
        json={
            "job_id": job_id,
            "scenario_index":
                scenario_index,
            "worker_id": WORKER_ID,
            "reason": reason[:1000],
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()


def upload_result(
    job_id: str,
    scenario_index: int,
    output_path: Path,
    identity_score: float | None = None,
    quality_score: float | None = None,
) -> Dict[str, Any]:

    params: Dict[str, Any] = {
        "worker_id": WORKER_ID,
    }

    if identity_score is not None:
        params["identity_score"] = (
            identity_score
        )

    if quality_score is not None:
        params["quality_score"] = (
            quality_score
        )

    with output_path.open("rb") as fh:
        response = requests.post(
            (
                f"{BASE_URL}/api/photo-factory/"
                f"worker/result/"
                f"{job_id}/"
                f"{scenario_index}"
            ),
            params=params,
            headers=headers(),
            files={
                "file": (
                    output_path.name,
                    fh,
                    "image/jpeg",
                )
            },
            timeout=max(
                REQUEST_TIMEOUT,
                300,
            ),
        )

    response.raise_for_status()

    return response.json()


class QwenEngine:
    def __init__(self) -> None:
        self.pipe = None

    def load(self) -> None:
        import torch
        from diffusers import (
            QwenImageEditPlusPipeline,
        )

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is not available"
            )

        log.info(
            "GPU: %s",
            torch.cuda.get_device_name(0),
        )

        log.info(
            "Loading model %s",
            MODEL_ID,
        )

        self.pipe = (
            QwenImageEditPlusPipeline
            .from_pretrained(
                MODEL_ID,
                torch_dtype=torch.bfloat16,
                cache_dir=str(CACHE_DIR),
            )
        )

        self.pipe.to("cuda")

        self.pipe.set_progress_bar_config(
            disable=False
        )

        log.info(
            "Qwen pipeline loaded"
        )

    def generate(
        self,
        references: List[Image.Image],
        prompt: str,
        seed: int,
    ) -> Image.Image:

        import torch

        if self.pipe is None:
            raise RuntimeError(
                "Pipeline not loaded"
            )

        generator = torch.Generator(
            device="cuda"
        ).manual_seed(seed)

        inputs = {
            "image": references,
            "prompt": prompt,
            "generator": generator,
            "true_cfg_scale":
                TRUE_CFG_SCALE,
            "negative_prompt": " ",
            "num_inference_steps":
                INFERENCE_STEPS,
            "guidance_scale":
                GUIDANCE_SCALE,
            "num_images_per_prompt": 1,
        }

        with torch.inference_mode():
            result = self.pipe(
                **inputs
            )

        return result.images[0]


def main() -> int:
    check_config()

    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    engine = QwenEngine()

    engine.load()

    log.info(
        "Worker started: %s",
        WORKER_ID,
    )

    while not STOP:
        task = None

        try:
            task = claim()

        except Exception:
            log.exception(
                "Claim failed"
            )

            time.sleep(
                POLL_SECONDS
            )

            continue

        if task is None:
            time.sleep(
                POLL_SECONDS
            )

            continue

        job_id = task["job_id"]

        scenario = task["scenario"]

        index = int(
            scenario["index"]
        )

        prompt = scenario["prompt"]

        log.info(
            "Claimed %s #%s",
            job_id,
            index,
        )

        try:
            refs = []

            for reference in (
                task.get("references", [])
                [:MAX_REFERENCES]
            ):
                refs.append(
                    download_reference(
                        reference[
                            "download_url"
                        ]
                    )
                )

            if not refs:
                raise RuntimeError(
                    "No references downloaded"
                )

            seed = random.randint(
                1,
                2_147_483_647,
            )

            log.info(
                "Generating %s #%s, "
                "refs=%s seed=%s",
                job_id,
                index,
                len(refs),
                seed,
            )

            image = engine.generate(
                refs,
                prompt,
                seed,
            )

            output = (
                OUTPUT_DIR
                / f"{job_id}_{index:03d}.jpg"
            )

            image.convert(
                "RGB"
            ).save(
                output,
                format="JPEG",
                quality=94,
                optimize=True,
            )

            result = upload_result(
                job_id,
                index,
                output,
            )

            log.info(
                "Uploaded %s #%s -> %s",
                job_id,
                index,
                result.get(
                    "output_url"
                ),
            )

            try:
                output.unlink()
            except Exception:
                pass

        except Exception as exc:
            log.exception(
                "Generation failed %s #%s",
                job_id,
                index,
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

            except Exception:
                log.exception(
                    "Failed to reject task"
                )

    log.info(
        "Worker stopped"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
