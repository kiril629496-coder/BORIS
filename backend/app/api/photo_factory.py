from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
)

from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services.platform_roles import is_platform_owner


router = APIRouter(
    prefix="/api/photo-factory",
    tags=["photo-factory"],
)


BORIS_ROOT = Path("/root/BORIS")

DATA_ROOT = Path(
    os.environ.get(
        "BORIS_PHOTO_FACTORY_DATA",
        str(BORIS_ROOT / "data" / "photo_factory"),
    )
)

OBJECTS_ROOT = DATA_ROOT / "objects"
JOBS_ROOT = DATA_ROOT / "jobs"

IMAGES_ROOT = Path(
    os.environ.get(
        "BORIS_PHOTO_FACTORY_IMAGES",
        "/root/BORIS/backend/images/photo_factory",
    )
)

TOKEN_FILE = Path(
    os.environ.get(
        "BORIS_PHOTO_WORKER_TOKEN_FILE",
        "/root/BORIS/secrets/photo_factory_worker_token",
    )
)

OBJECTS_ROOT.mkdir(parents=True, exist_ok=True)
JOBS_ROOT.mkdir(parents=True, exist_ok=True)
IMAGES_ROOT.mkdir(parents=True, exist_ok=True)


ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

MAX_REFERENCE_FILES = 10
MAX_REFERENCE_BYTES = 25 * 1024 * 1024
MAX_RESULT_BYTES = 30 * 1024 * 1024

MAX_ATTEMPTS = 4
CLAIM_TIMEOUT_SECONDS = 45 * 60


ANGLES = [
    (
        "front_left_30",
        "Спереди слева 30°",
        "front-left three-quarter, about 30 degree camera angle",
    ),
    (
        "front_left_45",
        "Спереди слева 45°",
        "front-left three-quarter, about 45 degree camera angle",
    ),
    (
        "front_low",
        "Спереди низко",
        "low front three-quarter automotive camera",
    ),
    (
        "front",
        "Строго спереди",
        "strict straight front view",
    ),
    (
        "front_right_30",
        "Спереди справа 30°",
        "front-right three-quarter, about 30 degree camera angle",
    ),
    (
        "front_right_45",
        "Спереди справа 45°",
        "front-right three-quarter, about 45 degree camera angle",
    ),
    (
        "left",
        "Левый профиль",
        "strict left side profile",
    ),
    (
        "right",
        "Правый профиль",
        "strict right side profile",
    ),
    (
        "rear_left_30",
        "Сзади слева 30°",
        "rear-left three-quarter, about 30 degree camera angle",
    ),
    (
        "rear_left_45",
        "Сзади слева 45°",
        "rear-left three-quarter, about 45 degree camera angle",
    ),
    (
        "rear",
        "Строго сзади",
        "strict straight rear view",
    ),
    (
        "rear_right_30",
        "Сзади справа 30°",
        "rear-right three-quarter, about 30 degree camera angle",
    ),
    (
        "rear_right_45",
        "Сзади справа 45°",
        "rear-right three-quarter, about 45 degree camera angle",
    ),
    (
        "front_close",
        "Передняя часть крупно",
        "commercial close front detail view",
    ),
    (
        "rear_close",
        "Задняя часть крупно",
        "commercial close rear detail view",
    ),
]


SCENES = [
    (
        "business_wet",
        "Бизнес-центр после дождя",
        (
            "modern premium business center, wet asphalt after rain, "
            "soft overcast daylight, natural realistic reflections"
        ),
    ),
    (
        "business",
        "Современный бизнес-центр",
        (
            "modern premium business center, clean pavement, "
            "soft neutral daylight"
        ),
    ),
    (
        "residential",
        "Современный ЖК",
        (
            "premium modern residential complex, landscaped exterior, "
            "natural commercial photography"
        ),
    ),
    (
        "parking",
        "Нейтральная парковка",
        (
            "clean neutral parking area, uncluttered background, "
            "realistic daylight"
        ),
    ),
    (
        "underground",
        "Подземный паркинг",
        (
            "premium underground parking garage, "
            "realistic architectural lighting"
        ),
    ),
    (
        "city",
        "Современный город",
        (
            "modern city architecture, clean street environment, "
            "realistic commercial photography"
        ),
    ),
]


IDENTITY_PROMPT = """
The input reference photographs show ONE EXACT SAME physical object.

Preserve the identity of that exact object strictly.

Do not replace it with a similar object.
Do not redesign it.

Preserve:
- exact body / product geometry
- generation and proportions
- lights
- grille
- bumpers
- mirrors
- windows
- trim
- wheels
- badges
- paint color
- distinctive physical details

Change only:
- requested camera viewpoint
- framing
- environment
- realistic lighting

Output must look like a genuine photograph of the exact same physical object.
""".strip()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_token(value: str) -> str:
    value = re.sub(
        r"[^0-9A-Za-zА-Яа-яЁё_.-]+",
        "_",
        value.strip(),
    )

    return value.strip("._")[:120] or "object"


def _record_access_allowed(record: Dict[str, Any], user) -> bool:
    if is_platform_owner(user):
        return True

    owner_user_id = record.get("owner_user_id")
    try:
        return owner_user_id is not None and int(owner_user_id) == int(user.id)
    except (TypeError, ValueError, AttributeError):
        return False


def _require_record_access(record: Dict[str, Any], user) -> None:
    if not _record_access_allowed(record, user):
        raise HTTPException(403, "Нет доступа к данным Photo Factory")


def read_json(path: Path) -> Any:
    if not path.exists():
        return None

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

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

    os.replace(tmp, path)


def object_root(object_id: str) -> Path:
    return (
        OBJECTS_ROOT
        / safe_token(object_id)
    )


def job_file(job_id: str) -> Path:
    return (
        JOBS_ROOT
        / safe_token(job_id)
        / "job.json"
    )


def result_dir(job_id: str) -> Path:
    return (
        IMAGES_ROOT
        / safe_token(job_id)
    )


def worker_token() -> str:
    if not TOKEN_FILE.exists():
        return ""

    return TOKEN_FILE.read_text(
        encoding="utf-8",
    ).strip()


def require_worker(
    x_boris_worker_token: Optional[str],
) -> None:

    expected = worker_token()

    if not expected:
        raise HTTPException(
            503,
            "Photo worker token not configured",
        )

    supplied = (
        x_boris_worker_token
        or ""
    )

    if not secrets.compare_digest(
        supplied,
        expected,
    ):
        raise HTTPException(
            401,
            "Invalid worker token",
        )


def update_stats(job: Dict[str, Any]) -> None:
    stats = {
        "queued": 0,
        "processing": 0,
        "complete": 0,
        "rejected": 0,
        "failed": 0,
    }

    for scenario in job.get(
        "scenarios",
        [],
    ):
        state = scenario.get(
            "status",
            "queued",
        )

        if state in stats:
            stats[state] += 1

    job["stats"] = stats

    if stats["complete"] >= job.get(
        "count",
        0,
    ):
        job["status"] = "complete"

    elif (
        stats["queued"] == 0
        and stats["processing"] == 0
        and (
            stats["failed"]
            + stats["rejected"]
        ) > 0
    ):
        job["status"] = "needs_retry"

    elif stats["processing"] > 0:
        job["status"] = "processing"

    else:
        job["status"] = "queued"

    job["updated_at"] = utcnow()


def reclaim_timed_out(
    job: Dict[str, Any],
) -> None:

    now_epoch = time.time()

    for scenario in job.get(
        "scenarios",
        [],
    ):
        if scenario.get("status") != "processing":
            continue

        claimed_epoch = scenario.get(
            "claimed_epoch"
        )

        if not claimed_epoch:
            continue

        age = (
            now_epoch
            - float(claimed_epoch)
        )

        if age > CLAIM_TIMEOUT_SECONDS:
            scenario["status"] = "queued"
            scenario["worker_id"] = None
            scenario["claimed_at"] = None
            scenario["claimed_epoch"] = None
            scenario["last_error"] = "worker_claim_timeout"


def build_scenarios(
    count: int,
    object_type: str,
    instruction: Optional[str],
) -> List[Dict[str, Any]]:

    rows: List[Dict[str, Any]] = []

    for index in range(count):
        angle = ANGLES[
            index % len(ANGLES)
        ]

        scene = SCENES[
            (index // len(ANGLES))
            % len(SCENES)
        ]

        prompt_parts = [
            IDENTITY_PROMPT,
            f"Object category: {object_type}.",
            f"Requested camera: {angle[2]}.",
            f"Requested environment: {scene[2]}.",
            (
                "Photorealistic commercial marketplace photography. "
                "Natural optics, shadows, reflections and materials. "
                "No watermark. No text overlay."
            ),
        ]

        if instruction:
            prompt_parts.append(
                instruction.strip()
            )

        rows.append(
            {
                "index": index + 1,
                "angle_code": angle[0],
                "angle_label": angle[1],
                "scene_code": scene[0],
                "scene_label": scene[1],
                "prompt": "\n\n".join(
                    prompt_parts
                ),
                "status": "queued",
                "attempts": 0,
                "output_url": None,
                "output_sha256": None,
                "identity_score": None,
                "quality_score": None,
                "worker_id": None,
                "claimed_at": None,
                "claimed_epoch": None,
                "completed_at": None,
                "last_error": None,
            }
        )

    return rows


class CreateJobRequest(BaseModel):
    object_id: str = Field(
        min_length=1,
        max_length=120,
    )

    object_type: str = Field(
        default="product",
        max_length=80,
    )

    count: int = Field(
        default=10,
        ge=1,
        le=30,
    )

    preserve_identity: bool = True

    custom_instruction: Optional[str] = Field(
        default=None,
        max_length=4000,
    )


class RejectRequest(BaseModel):
    job_id: str
    scenario_index: int
    worker_id: str
    reason: str = "generation_failed"


@router.get("/health")
def health() -> Dict[str, Any]:
    from app.api.photo_factory_v3 import readiness as v3_readiness

    generation = v3_readiness().get("generation", {})

    return {
        "ok": True,
        "service": "boris-photo-factory",
        "version": "2.0",
        "engine": "external_gpu_worker",
        "generation_ready": bool(generation.get("ready")),
        "generation_reason": generation.get("reason"),
        "recommended_provider": generation.get("recommended_provider"),
        "worker_contract": True,
        "paid_calls": False,
    }


@router.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    return {
        "max_references": MAX_REFERENCE_FILES,
        "max_output_count": 30,
        "max_attempts": MAX_ATTEMPTS,
        "worker_timeout_seconds": CLAIM_TIMEOUT_SECONDS,
        "angles": [
            {
                "code": item[0],
                "label": item[1],
            }
            for item in ANGLES
        ],
        "scenes": [
            {
                "code": item[0],
                "label": item[1],
            }
            for item in SCENES
        ],
    }


@router.post("/objects/{object_id}/references")
async def upload_references(
    object_id: str,
    files: List[UploadFile] = File(...),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    if not files:
        raise HTTPException(
            400,
            "No files supplied",
        )

    if len(files) > MAX_REFERENCE_FILES:
        raise HTTPException(
            400,
            f"Maximum {MAX_REFERENCE_FILES} files",
        )

    oid = safe_token(object_id)

    root = object_root(oid)

    refs = (
        root
        / "references"
    )

    refs.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_file = (
        root
        / "object.json"
    )

    manifest = read_json(
        manifest_file
    )

    if manifest:
        _require_record_access(manifest, _user)
    else:
        manifest = {
            "object_id": oid,
            "owner_user_id": int(_user.id),
            "created_at": utcnow(),
            "references": [],
        }

    current = len(
        manifest.get(
            "references",
            [],
        )
    )

    if current + len(files) > MAX_REFERENCE_FILES:
        raise HTTPException(
            400,
            f"Maximum {MAX_REFERENCE_FILES} references total",
        )

    uploaded = []

    for file in files:
        original = (
            file.filename
            or "reference.jpg"
        )

        suffix = (
            Path(original)
            .suffix
            .lower()
        )

        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                400,
                f"Unsupported extension: {suffix}",
            )

        file_id = uuid.uuid4().hex[:16]

        target = (
            refs
            / f"{file_id}{suffix}"
        )

        total = 0

        with target.open("wb") as out:
            while True:
                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                total += len(chunk)

                if total > MAX_REFERENCE_BYTES:
                    target.unlink(
                        missing_ok=True,
                    )

                    raise HTTPException(
                        413,
                        "Reference image too large",
                    )

                out.write(chunk)

        digest = hashlib.sha256(
            target.read_bytes()
        ).hexdigest()

        record = {
            "id": file_id,
            "original_name": original,
            "filename": target.name,
            "size": total,
            "sha256": digest,
            "created_at": utcnow(),
        }

        manifest["references"].append(
            record
        )

        uploaded.append(
            record
        )

    manifest["updated_at"] = utcnow()

    write_json(
        manifest_file,
        manifest,
    )

    return {
        "ok": True,
        "object_id": oid,
        "uploaded": uploaded,
        "reference_count": len(
            manifest["references"]
        ),
    }


@router.get("/objects/{object_id}")
def get_object(
    object_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    value = read_json(
        object_root(object_id)
        / "object.json"
    )

    if not value:
        raise HTTPException(
            404,
            "Object not found",
        )

    _require_record_access(value, _user)
    return value


@router.post("/jobs")
def create_job(
    request: CreateJobRequest,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    oid = safe_token(
        request.object_id
    )

    manifest = read_json(
        object_root(oid)
        / "object.json"
    )

    if not manifest:
        raise HTTPException(
            409,
            "Upload references first",
        )

    _require_record_access(manifest, _user)

    references = (
        manifest.get(
            "references"
        )
        or []
    )

    if not references:
        raise HTTPException(
            409,
            "Object has no reference images",
        )

    job_id = (
        f"pf_{int(time.time())}_"
        f"{uuid.uuid4().hex[:10]}"
    )

    scenarios = build_scenarios(
        request.count,
        request.object_type,
        request.custom_instruction,
    )

    payload = {
        "id": job_id,
        "owner_user_id": int(_user.id),
        "object_id": oid,
        "object_type": request.object_type,
        "count": request.count,
        "preserve_identity": request.preserve_identity,
        "status": "queued",
        "engine": "external_gpu_worker",
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "references": references,
        "scenarios": scenarios,
    }

    update_stats(
        payload
    )

    write_json(
        job_file(job_id),
        payload,
    )

    return payload


@router.get("/jobs")
def list_jobs(
    limit: int = 20,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    limit = max(
        1,
        min(
            limit,
            100,
        ),
    )

    jobs = []

    for path in JOBS_ROOT.glob(
        "*/job.json"
    ):
        try:
            item = read_json(
                path
            )

            if item:
                if not _record_access_allowed(item, _user):
                    continue

                reclaim_timed_out(
                    item
                )

                update_stats(
                    item
                )

                write_json(
                    path,
                    item,
                )

                jobs.append(
                    item
                )

        except Exception:
            continue

    jobs.sort(
        key=lambda item: item.get(
            "created_at",
            "",
        ),
        reverse=True,
    )

    return {
        "items": jobs[:limit],
        "count": min(
            len(jobs),
            limit,
        ),
    }


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    path = job_file(
        job_id
    )

    job = read_json(
        path
    )

    if not job:
        raise HTTPException(
            404,
            "Job not found",
        )

    _require_record_access(job, _user)

    reclaim_timed_out(
        job
    )

    update_stats(
        job
    )

    write_json(
        path,
        job,
    )

    return job


@router.post("/worker/claim")
def worker_claim(
    worker_id: str,
    x_boris_worker_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:

    require_worker(
        x_boris_worker_token
    )

    worker_id = safe_token(
        worker_id
    )

    all_jobs = []

    for path in JOBS_ROOT.glob(
        "*/job.json"
    ):
        try:
            job = read_json(
                path
            )

            if job:
                all_jobs.append(
                    (
                        job.get(
                            "created_at",
                            "",
                        ),
                        path,
                        job,
                    )
                )
        except Exception:
            continue

    all_jobs.sort(
        key=lambda item: item[0]
    )

    for _, path, job in all_jobs:
        reclaim_timed_out(
            job
        )

        for scenario in job.get(
            "scenarios",
            [],
        ):
            scenario_status = scenario.get(
                "status"
            )

            if scenario_status not in {
                "queued",
                "parked_pre_real_gpu",
            }:
                continue

            attempts = int(
                scenario.get(
                    "attempts"
                )
                or 0
            )

            if attempts >= MAX_ATTEMPTS:
                scenario["status"] = "failed"
                scenario["last_error"] = "max_attempts"
                continue

            scenario["status"] = "processing"
            scenario["worker_id"] = worker_id
            scenario["claimed_at"] = utcnow()
            scenario["claimed_epoch"] = time.time()
            scenario["attempts"] = attempts + 1

            update_stats(
                job
            )

            write_json(
                path,
                job,
            )

            refs = []

            for ref in job.get(
                "references",
                [],
            ):
                refs.append(
                    {
                        **ref,
                        "download_url": (
                            f"/api/photo-factory/worker/reference/"
                            f"{job['object_id']}/"
                            f"{ref['filename']}"
                        ),
                    }
                )

            return {
                "ok": True,
                "claimed": True,
                "job_id": job["id"],
                "object_id": job["object_id"],
                "object_type": job["object_type"],
                "scenario": scenario,
                "references": refs,
            }

        update_stats(
            job
        )

        write_json(
            path,
            job,
        )

    return {
        "ok": True,
        "claimed": False,
    }


@router.get(
    "/worker/reference/{object_id}/{filename}"
)
def worker_reference(
    object_id: str,
    filename: str,
    x_boris_worker_token: Optional[str] = Header(default=None),
):
    require_worker(
        x_boris_worker_token
    )

    oid = safe_token(
        object_id
    )

    filename = Path(
        filename
    ).name

    path = (
        object_root(oid)
        / "references"
        / filename
    )

    if not path.exists():
        raise HTTPException(
            404,
            "Reference not found",
        )

    return FileResponse(
        path
    )


@router.post(
    "/worker/result/{job_id}/{scenario_index}"
)
async def worker_result(
    job_id: str,
    scenario_index: int,
    worker_id: str,
    identity_score: Optional[float] = None,
    quality_score: Optional[float] = None,
    file: UploadFile = File(...),
    x_boris_worker_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:

    require_worker(
        x_boris_worker_token
    )

    path = job_file(
        job_id
    )

    job = read_json(
        path
    )

    if not job:
        raise HTTPException(
            404,
            "Job not found",
        )

    scenario = next(
        (
            row
            for row in job.get(
                "scenarios",
                [],
            )
            if row.get(
                "index"
            ) == scenario_index
        ),
        None,
    )

    if not scenario:
        raise HTTPException(
            404,
            "Scenario not found",
        )

    if scenario.get(
        "worker_id"
    ) != safe_token(
        worker_id
    ):
        raise HTTPException(
            409,
            "Scenario owned by another worker",
        )

    suffix = (
        Path(
            file.filename
            or "result.jpg"
        )
        .suffix
        .lower()
    )

    if suffix not in ALLOWED_EXTENSIONS:
        suffix = ".jpg"

    out_dir = result_dir(
        job_id
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    target = (
        out_dir
        / f"{scenario_index:03d}{suffix}"
    )

    total = 0

    with target.open("wb") as out:
        while True:
            chunk = await file.read(
                1024 * 1024
            )

            if not chunk:
                break

            total += len(chunk)

            if total > MAX_RESULT_BYTES:
                target.unlink(
                    missing_ok=True
                )

                raise HTTPException(
                    413,
                    "Generated image too large",
                )

            out.write(
                chunk
            )

    digest = hashlib.sha256(
        target.read_bytes()
    ).hexdigest()

    scenario["status"] = "complete"
    scenario["output_url"] = (
        f"/images/photo_factory/"
        f"{safe_token(job_id)}/"
        f"{target.name}"
    )
    scenario["output_sha256"] = digest
    scenario["identity_score"] = identity_score
    scenario["quality_score"] = quality_score
    scenario["completed_at"] = utcnow()
    scenario["claimed_epoch"] = None

    update_stats(
        job
    )

    write_json(
        path,
        job,
    )

    return {
        "ok": True,
        "job_id": job_id,
        "scenario_index": scenario_index,
        "output_url": scenario["output_url"],
        "status": job["status"],
        "stats": job["stats"],
    }


@router.post("/worker/reject")
def worker_reject(
    request: RejectRequest,
    x_boris_worker_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:

    require_worker(
        x_boris_worker_token
    )

    path = job_file(
        request.job_id
    )

    job = read_json(
        path
    )

    if not job:
        raise HTTPException(
            404,
            "Job not found",
        )

    scenario = next(
        (
            row
            for row in job.get(
                "scenarios",
                [],
            )
            if row.get(
                "index"
            ) == request.scenario_index
        ),
        None,
    )

    if not scenario:
        raise HTTPException(
            404,
            "Scenario not found",
        )

    if scenario.get(
        "worker_id"
    ) != safe_token(
        request.worker_id
    ):
        raise HTTPException(
            409,
            "Scenario owned by another worker",
        )

    attempts = int(
        scenario.get(
            "attempts"
        )
        or 0
    )

    if attempts >= MAX_ATTEMPTS:
        scenario["status"] = "failed"
    else:
        scenario["status"] = "queued"

    scenario["last_error"] = request.reason
    scenario["worker_id"] = None
    scenario["claimed_at"] = None
    scenario["claimed_epoch"] = None

    update_stats(
        job
    )

    write_json(
        path,
        job,
    )

    return {
        "ok": True,
        "scenario_status": scenario["status"],
        "attempts": attempts,
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    path = job_file(
        job_id
    )

    job = read_json(
        path
    )

    if not job:
        raise HTTPException(
            404,
            "Job not found",
        )

    _require_record_access(job, _user)

    changed = 0

    for scenario in job.get(
        "scenarios",
        [],
    ):
        if scenario.get(
            "status"
        ) in {
            "rejected",
            "failed",
        }:
            scenario["status"] = "queued"
            scenario["attempts"] = 0
            scenario["last_error"] = None
            scenario["worker_id"] = None
            scenario["claimed_epoch"] = None

            changed += 1

    update_stats(
        job
    )

    write_json(
        path,
        job,
    )

    return {
        "ok": True,
        "requeued": changed,
        "job": job,
    }


@router.get("/jobs/{job_id}/gallery")
def gallery(
    job_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:

    job = read_json(
        job_file(
            job_id
        )
    )

    if not job:
        raise HTTPException(
            404,
            "Job not found",
        )

    _require_record_access(job, _user)

    images = []

    for scenario in job.get(
        "scenarios",
        [],
    ):
        if (
            scenario.get(
                "status"
            ) == "complete"
            and scenario.get(
                "output_url"
            )
        ):
            images.append(
                {
                    "index": scenario["index"],
                    "angle_label": scenario["angle_label"],
                    "scene_label": scenario["scene_label"],
                    "url": scenario["output_url"],
                    "identity_score": scenario.get(
                        "identity_score"
                    ),
                    "quality_score": scenario.get(
                        "quality_score"
                    ),
                }
            )

    return {
        "job_id": job_id,
        "status": job.get(
            "status"
        ),
        "count": len(
            images
        ),
        "images": images,
    }
