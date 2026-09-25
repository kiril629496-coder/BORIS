from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="/api/photo-factory-v3",
    tags=["photo-factory-v3"],
)


ROOT = Path("/root/BORIS")

DATA_ROOT = ROOT / "data" / "photo_factory"
JOBS_ROOT = DATA_ROOT / "jobs"
OBJECTS_ROOT = DATA_ROOT / "objects"

WORKER_TOKEN = (
    ROOT
    / "secrets"
    / "photo_factory_worker_token"
)

GPU_BUNDLE = (
    ROOT
    / "deploy"
    / "photo_factory_gpu_worker"
    / "boris_qwen_worker.py"
)


ProviderName = Literal[
    "disabled",
    "external_gpu",
    "free_notebook",
]


class ProviderSelectRequest(BaseModel):
    provider: ProviderName


class LaunchGuardRequest(BaseModel):
    requested_images: int = Field(
        default=1,
        ge=1,
        le=30,
    )

    provider: ProviderName = "disabled"


def _env_bool(
    name: str,
    default: bool = False,
) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _zero_cost_mode() -> bool:
    # Default is deliberately TRUE.
    #
    # Nothing in V3 may silently turn paid generation on.
    return _env_bool(
        "BORIS_PHOTO_ZERO_COST_ONLY",
        True,
    )


def _paid_api_enabled() -> bool:
    return _env_bool(
        "BORIS_PHOTO_PAID_API_ENABLED",
        False,
    )


def _read_json(
    path: Path,
) -> dict[str, Any] | None:
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except Exception:
        return None


def _job_inventory() -> dict[str, int]:
    result = {
        "jobs": 0,
        "queued": 0,
        "processing": 0,
        "complete": 0,
        "failed": 0,
        "rejected": 0,
        "parked": 0,
    }

    if not JOBS_ROOT.exists():
        return result

    for path in JOBS_ROOT.glob(
        "*/job.json"
    ):
        data = _read_json(path)

        if not data:
            continue

        result["jobs"] += 1

        for row in data.get(
            "scenarios",
            [],
        ):
            status = row.get(
                "status"
            )

            if status == "parked_pre_real_gpu":
                result["parked"] += 1

            elif status in result:
                result[status] += 1

    return result


def _object_inventory() -> dict[str, int]:
    objects = 0
    references = 0

    if not OBJECTS_ROOT.exists():
        return {
            "objects": 0,
            "references": 0,
        }

    for directory in OBJECTS_ROOT.iterdir():
        if not directory.is_dir():
            continue

        objects += 1

        meta = (
            directory
            / "object.json"
        )

        data = _read_json(meta)

        if data:
            references += len(
                data.get(
                    "references",
                    [],
                )
            )

    return {
        "objects": objects,
        "references": references,
    }


def _provider_state() -> dict[str, Any]:
    zero_cost = _zero_cost_mode()

    external_gpu = {
        "id": "external_gpu",
        "label": "Свой GPU worker",
        "available": GPU_BUNDLE.exists(),
        "cost_policy": "external_compute",
        "paid_api": False,
        "description": (
            "Наш Qwen worker на отдельной NVIDIA-машине."
        ),
    }

    free_notebook = {
        "id": "free_notebook",
        "label": "Бесплатный notebook GPU",
        "available": GPU_BUNDLE.exists(),
        "cost_policy": "zero_cost",
        "paid_api": False,
        "description": (
            "Тот же worker можно запускать вручную "
            "на доступном бесплатном GPU runtime."
        ),
    }

    disabled = {
        "id": "disabled",
        "label": "Генерация выключена",
        "available": True,
        "cost_policy": "zero_cost",
        "paid_api": False,
        "description": (
            "Безопасный режим: подготовка объектов "
            "и заданий без генерации."
        ),
    }

    return {
        "zero_cost_only": zero_cost,
        "paid_api_enabled": _paid_api_enabled(),
        "providers": [
            disabled,
            free_notebook,
            external_gpu,
        ],
    }


def _active_worker_state() -> dict[str, Any]:
    """Reuse the existing V3.1 heartbeat as the authoritative worker state."""
    try:
        from app.api.photo_factory_v31 import heartbeat_status

        status = heartbeat_status()
        return status if isinstance(status, dict) else {"active": False, "reason": "INVALID_WORKER_STATE"}
    except Exception:
        return {"active": False, "reason": "WORKER_STATE_UNAVAILABLE", "age_seconds": None}


def _recommended_provider(worker_state: dict[str, Any] | None = None) -> str:
    # A connected V3.1 notebook worker is the only currently proven live
    # generator. Never advertise a provider solely because its bundle exists.
    state = worker_state or _active_worker_state()
    return "free_notebook" if bool(state.get("active")) else "disabled"


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "boris-photo-factory-v3",
        "version": "3.0",
        "zero_cost_only": _zero_cost_mode(),
        "paid_api_enabled": _paid_api_enabled(),
        "v2_preserved": True,
        "generator_abstraction": True,
    }


@router.get("/readiness")
def readiness() -> dict[str, Any]:
    jobs = _job_inventory()
    objects = _object_inventory()
    providers = _provider_state()
    worker_state = _active_worker_state()
    worker_active = bool(worker_state.get("active"))

    return {
        "ok": True,
        "mode": (
            "ZERO_COST"
            if _zero_cost_mode()
            else "STANDARD"
        ),
        "generation": {
            "ready": worker_active,
            "reason": "ACTIVE_WORKER" if worker_active else str(worker_state.get("reason") or "NO_ACTIVE_GPU_WORKER"),
            "recommended_provider": (
                _recommended_provider(worker_state)
            ),
        },
        "storage": {
            "jobs_root": JOBS_ROOT.exists(),
            "objects_root": OBJECTS_ROOT.exists(),
        },
        "worker": {
            "token_present": WORKER_TOKEN.exists(),
            "bundle_present": GPU_BUNDLE.exists(),
        },
        "inventory": {
            **jobs,
            **objects,
        },
        **providers,
    }


@router.get("/providers")
def providers() -> dict[str, Any]:
    state = _provider_state()

    return {
        "ok": True,
        **state,
    }


@router.post("/launch-guard")
def launch_guard(
    body: LaunchGuardRequest,
) -> dict[str, Any]:

    zero_cost = _zero_cost_mode()

    if body.provider == "disabled":
        return {
            "ok": True,
            "allowed": False,
            "reason": "GENERATOR_DISABLED",
            "requested_images": body.requested_images,
            "estimated_paid_cost_usd": 0.0,
        }

    if body.provider == "free_notebook":
        return {
            "ok": True,
            "allowed": True,
            "reason": "ZERO_COST_PROVIDER",
            "requested_images": body.requested_images,
            "estimated_paid_cost_usd": 0.0,
            "requires_connected_worker": True,
        }

    if body.provider == "external_gpu":
        # External GPU is our own worker contract.
        # V3 does not purchase/rent anything automatically.
        return {
            "ok": True,
            "allowed": True,
            "reason": "EXTERNAL_GPU_MANUAL_BILLING",
            "requested_images": body.requested_images,
            "estimated_paid_cost_usd": 0.0,
            "requires_connected_worker": True,
            "note": (
                "BORIS itself performs no paid API call "
                "and cannot rent a GPU automatically."
            ),
        }

    if zero_cost:
        raise HTTPException(
            status_code=403,
            detail="ZERO_COST_ONLY",
        )

    raise HTTPException(
        status_code=400,
        detail="UNKNOWN_PROVIDER",
    )


@router.get("/eco-summary")
def eco_summary() -> dict[str, Any]:
    jobs = _job_inventory()
    objects = _object_inventory()

    return {
        "ok": True,
        "title": "AI Фотостудия",
        "location": "ЕЦО → Объявления",
        "workflow": [
            "Загрузить 3–5 реальных фото",
            "Зафиксировать объект",
            "Выбрать 1 / 5 / 10 / 30 кадров",
            "BORIS подготовит ракурсы и сцены",
            "Generator worker обработает очередь",
            "BORIS примет результаты",
            "Проверка качества",
            "Галерея готовых фотографий",
        ],
        "inventory": {
            **jobs,
            **objects,
        },
        "billing": {
            "boris_paid_calls": False,
            "paid_api_enabled": _paid_api_enabled(),
            "zero_cost_only": _zero_cost_mode(),
        },
    }
