from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse


router = APIRouter(
    prefix="/api/photo-factory-v31",
    tags=["photo-factory-v31"],
)


ROOT = Path("/root/BORIS")

RUNTIME = (
    ROOT
    / "data"
    / "photo_factory"
    / "runtime"
)

HEARTBEAT = (
    RUNTIME
    / "free_notebook_heartbeat.json"
)

ENROLL = (
    RUNTIME
    / "enrollment"
)

TOKEN_FILE = (
    ROOT
    / "secrets"
    / "photo_factory_worker_token"
)

PUBLIC_ORIGIN = (
    "https://boris-ai.pro"
)

HEARTBEAT_TTL = 120


class HeartbeatBody(BaseModel):
    worker_id: str = Field(
        min_length=1,
        max_length=120,
    )

    engine: str = Field(
        default="free_sdxl_ipadapter",
        max_length=120,
    )

    gpu_name: Optional[str] = Field(
        default=None,
        max_length=200,
    )

    vram_gb: Optional[float] = None

    state: str = Field(
        default="ready",
        max_length=80,
    )


class EnrollBody(BaseModel):
    code: str = Field(
        min_length=20,
        max_length=200,
    )


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def read_json(
    path: Path,
) -> dict[str, Any] | None:

    if not path.exists():
        return None

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None


def write_json(
    path: Path,
    value: dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = Path(
        str(path) + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(path)


def expected_token() -> str:
    if not TOKEN_FILE.exists():
        return ""

    return TOKEN_FILE.read_text(
        encoding="utf-8"
    ).strip()


def require_worker(
    supplied: Optional[str],
) -> None:

    wanted = expected_token()

    if not wanted:
        raise HTTPException(
            503,
            "Worker token unavailable",
        )

    if not secrets.compare_digest(
        supplied or "",
        wanted,
    ):
        raise HTTPException(
            401,
            "Invalid worker token",
        )


def heartbeat_status() -> dict[str, Any]:
    data = read_json(
        HEARTBEAT
    )

    if not data:
        return {
            "active": False,
            "reason": "NO_HEARTBEAT",
            "age_seconds": None,
        }

    epoch = data.get(
        "epoch"
    )

    if not epoch:
        return {
            "active": False,
            "reason": "INVALID_HEARTBEAT",
            "age_seconds": None,
        }

    age = max(
        0.0,
        time.time()
        - float(epoch),
    )

    active = (
        age <= HEARTBEAT_TTL
    )

    return {
        "active": active,
        "reason": (
            "ACTIVE"
            if active
            else "HEARTBEAT_EXPIRED"
        ),
        "age_seconds": round(
            age,
            1,
        ),
        "worker": data,
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "boris-photo-factory-v31",
        "version": "3.1",
        "paid_calls": False,
        "free_worker": True,
    }


@router.get("/worker-status")
def worker_status() -> dict[str, Any]:
    status = heartbeat_status()

    return {
        "ok": True,
        "generation_ready": status["active"],
        "provider": (
            "free_notebook"
            if status["active"]
            else "disabled"
        ),
        **status,
    }


@router.get("/notebook-config")
def notebook_config() -> dict[str, Any]:
    return {
        "ok": True,
        "public_origin": PUBLIC_ORIGIN,
        "engine": "free_sdxl_ipadapter",
        "paid_calls": False,
        "max_tasks_default": 1,
        "heartbeat_ttl_seconds": HEARTBEAT_TTL,
        "notebook_path": (
            "/downloads/"
            "boris_photo_factory_free_worker.ipynb"
        ),
    }


@router.post("/worker-heartbeat")
def worker_heartbeat(
    body: HeartbeatBody,
    x_boris_worker_token: Optional[str] = Header(
        default=None
    ),
) -> dict[str, Any]:

    require_worker(
        x_boris_worker_token
    )

    payload = {
        "worker_id": body.worker_id,
        "engine": body.engine,
        "gpu_name": body.gpu_name,
        "vram_gb": body.vram_gb,
        "state": body.state,
        "updated_at": now_iso(),
        "epoch": time.time(),
    }

    write_json(
        HEARTBEAT,
        payload,
    )

    return {
        "ok": True,
        "accepted": True,
    }


@router.post("/enroll")
def enroll_worker(
    body: EnrollBody,
) -> dict[str, Any]:

    code = body.code.strip()

    digest = __import__(
        "hashlib"
    ).sha256(
        code.encode(
            "utf-8"
        )
    ).hexdigest()

    path = (
        ENROLL
        / f"{digest}.json"
    )

    record = read_json(path)

    if not record:
        raise HTTPException(
            401,
            "Invalid enrollment code",
        )

    if record.get("used"):
        raise HTTPException(
            410,
            "Enrollment code already used",
        )

    expires = float(
        record.get(
            "expires_epoch",
            0,
        )
    )

    if time.time() > expires:
        raise HTTPException(
            410,
            "Enrollment code expired",
        )

    token = expected_token()

    if not token:
        raise HTTPException(
            503,
            "Worker token unavailable",
        )

    record["used"] = True
    record["used_at"] = now_iso()

    write_json(
        path,
        record,
    )

    return {
        "ok": True,
        "worker_token": token,
        "public_origin": PUBLIC_ORIGIN,
        "engine": "free_sdxl_ipadapter",
        "paid_calls": False,
    }

# BORIS_V312_PUBLIC_NOTEBOOK

@router.get(
    "/free-notebook",
    include_in_schema=True,
)
def free_notebook():
    path = Path('/root/BORIS/frontend/public/downloads/boris_photo_factory_free_worker.ipynb')

    if not path.is_file():
        raise HTTPException(
            404,
            "Free notebook unavailable",
        )

    return FileResponse(
        path=str(path),
        media_type="application/x-ipynb+json",
        filename="boris_photo_factory_free_worker.ipynb",
    )

