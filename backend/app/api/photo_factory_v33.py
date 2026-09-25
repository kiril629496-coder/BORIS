from __future__ import annotations

import json
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.services.platform_roles import is_platform_owner


router = APIRouter(
    prefix="/api/photo-factory-v33",
    tags=["photo-factory-v33"],
)


ROOT = Path("/root/BORIS")

DATA_ROOT = (
    ROOT
    / "data"
    / "photo_factory"
)

IMAGES_ROOT = (
    ROOT
    / "backend"
    / "images"
    / "photo_factory"
)


class RetryRequest(BaseModel):
    scenario_index: int


def _require_job_access(job: dict[str, Any], user) -> None:
    if is_platform_owner(user):
        return
    owner_user_id = job.get("owner_user_id")
    try:
        allowed = owner_user_id is not None and int(owner_user_id) == int(user.id)
    except (TypeError, ValueError, AttributeError):
        allowed = False
    if not allowed:
        raise HTTPException(403, "Нет доступа к данным Photo Factory")


def _walk_json_candidates():

    roots = [
        DATA_ROOT,
        ROOT / "backend" / "data" / "photo_factory",
    ]

    seen = set()

    for root in roots:

        if not root.exists():
            continue

        for path in root.rglob("*.json"):

            resolved = str(
                path.resolve()
            )

            if resolved in seen:
                continue

            seen.add(resolved)

            yield path


def _extract_jobs(
    payload: Any,
):

    if isinstance(
        payload,
        dict,
    ):

        if (
            isinstance(
                payload.get("id"),
                str,
            )
            and payload.get(
                "id",
                "",
            ).startswith("pf_")
            and isinstance(
                payload.get("scenarios"),
                list,
            )
        ):
            yield payload
            return

        for value in payload.values():

            if isinstance(
                value,
                (dict, list),
            ):
                yield from _extract_jobs(
                    value
                )

    elif isinstance(
        payload,
        list,
    ):

        for item in payload:

            if isinstance(
                item,
                (dict, list),
            ):
                yield from _extract_jobs(
                    item
                )


def _find_persisted_job(
    job_id: str,
):

    for path in _walk_json_candidates():

        try:

            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

        except Exception:
            continue

        for job in _extract_jobs(
            payload
        ):

            if job.get("id") == job_id:
                return (
                    path,
                    payload,
                    deepcopy(job),
                )

    return None


def _scenario_view(
    scenario: dict,
):

    output_url = scenario.get(
        "output_url"
    )

    output_exists = False
    output_bytes = None

    if (
        isinstance(
            output_url,
            str,
        )
        and output_url.startswith(
            "/images/"
        )
    ):

        physical = (
            ROOT
            / "backend"
            / output_url.lstrip("/")
        )

        if physical.is_file():

            output_exists = True

            output_bytes = (
                physical.stat().st_size
            )

    return {
        **scenario,
        "output_exists":
            output_exists,
        "output_bytes":
            output_bytes,
    }


def _normalize_job(
    job: dict,
):

    scenarios = [
        _scenario_view(x)
        for x in (
            job.get("scenarios")
            or []
        )
    ]

    stats = {
        "queued": 0,
        "processing": 0,
        "complete": 0,
        "rejected": 0,
        "failed": 0,
    }

    for scenario in scenarios:

        status = scenario.get(
            "status"
        )

        if status in stats:
            stats[status] += 1

    return {
        **job,
        "scenarios": scenarios,
        "stats": stats,
    }


def _replace_job_in_payload(
    payload,
    job_id: str,
    replacement: dict,
):

    if isinstance(
        payload,
        dict,
    ):

        if payload.get("id") == job_id:
            return (
                deepcopy(replacement),
                True,
            )

        changed = False

        out = {}

        for key, value in payload.items():

            new_value, item_changed = (
                _replace_job_in_payload(
                    value,
                    job_id,
                    replacement,
                )
            )

            out[key] = new_value

            changed = (
                changed
                or item_changed
            )

        return out, changed

    if isinstance(
        payload,
        list,
    ):

        changed = False

        out = []

        for value in payload:

            new_value, item_changed = (
                _replace_job_in_payload(
                    value,
                    job_id,
                    replacement,
                )
            )

            out.append(
                new_value
            )

            changed = (
                changed
                or item_changed
            )

        return out, changed

    return payload, False


def _atomic_json_write(
    path: Path,
    payload,
):

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as file:

            file.write(raw)
            file.flush()
            os.fsync(
                file.fileno()
            )

        os.replace(
            tmp_name,
            path,
        )

    finally:

        try:
            os.unlink(
                tmp_name
            )
        except FileNotFoundError:
            pass


@router.get("/health")
def health():

    return {
        "ok": True,
        "service":
            "boris-photo-factory-v33",
        "version": "3.3",
        "gallery": True,
        "live_status": True,
        "download": True,
        "retry": True,
        "paid_calls": False,
    }


@router.get(
    "/jobs/{job_id}"
)
def job_view(
    job_id: str,
    _user=Depends(get_current_user),
):

    found = _find_persisted_job(
        job_id
    )

    if not found:

        raise HTTPException(
            404,
            "Job not found in persistent Photo Factory storage",
        )

    _path, _payload, job = found
    _require_job_access(job, _user)

    return {
        "ok": True,
        "job":
            _normalize_job(
                job
            ),
    }


@router.post(
    "/jobs/{job_id}/retry"
)
def retry_scenario(
    job_id: str,
    body: RetryRequest,
    _user=Depends(get_current_user),
):

    found = _find_persisted_job(
        job_id
    )

    if not found:

        raise HTTPException(
            404,
            "Job not found",
        )

    path, payload, job = found
    _require_job_access(job, _user)

    scenarios = (
        job.get("scenarios")
        or []
    )

    target = None

    for scenario in scenarios:

        if (
            int(
                scenario.get(
                    "index",
                    -1,
                )
            )
            == body.scenario_index
        ):
            target = scenario
            break

    if target is None:

        raise HTTPException(
            404,
            "Scenario not found",
        )

    old_status = target.get(
        "status"
    )

    if old_status not in {
        "failed",
        "rejected",
    }:

        raise HTTPException(
            409,
            (
                "Retry allowed only for "
                "failed/rejected scenarios"
            ),
        )

    stamp = time.strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        path.name
        + f".before_retry_{stamp}"
    )

    backup.write_bytes(
        path.read_bytes()
    )

    target["status"] = "queued"
    target["output_url"] = None
    target["output_sha256"] = None
    target["identity_score"] = None
    target["quality_score"] = None
    target["worker_id"] = None
    target["claimed_at"] = None
    target["claimed_epoch"] = None
    target["completed_at"] = None
    target["last_error"] = None

    job["status"] = "queued"
    job["updated_at"] = (
        time.strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        + "Z"
    )

    new_payload, changed = (
        _replace_job_in_payload(
            payload,
            job_id,
            job,
        )
    )

    if not changed:

        raise HTTPException(
            500,
            "Persistent payload replacement failed",
        )

    _atomic_json_write(
        path,
        new_payload,
    )

    return {
        "ok": True,
        "job_id": job_id,
        "scenario_index":
            body.scenario_index,
        "old_status":
            old_status,
        "new_status":
            "queued",
        "backup":
            str(backup),
    }
