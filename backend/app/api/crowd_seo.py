from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.auth import require_owner
from app.services import crowd_seo as svc
from app.services import seo_checkpoint_center

router = APIRouter(prefix="/api/prospecting/crowd-seo", tags=["crowd-seo"])

class ProjectCreate(BaseModel):
    site: str = Field(..., min_length=3)
    target_count: int = Field(..., ge=1, le=500)
    bonus_count: int = Field(0, ge=0, le=200)

    class Config:
        extra = "forbid"

class PublicationBody(BaseModel):
    publication_url: str = Field(..., min_length=5)

class DecisionBody(BaseModel):
    decision: str


class CheckpointCompleteBody(BaseModel):
    entry_id: str = Field(..., min_length=2, max_length=300)
    platform: str = Field(..., min_length=2, max_length=160)
    target_site: str | None = Field(None, max_length=200)
    page_url: str | None = Field(None, max_length=2000)

    class Config:
        extra = "forbid"


@router.get("/checkpoint-center")
def checkpoint_center(response: Response, user=Depends(require_owner)):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return seo_checkpoint_center.build_center_payload(include_secrets=False)


@router.get("/checkpoint-center/extension-config")
def checkpoint_center_extension_config(response: Response, user=Depends(require_owner)):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return seo_checkpoint_center.build_center_payload(include_secrets=True)


@router.post("/checkpoint-center/complete")
def checkpoint_complete(
    body: CheckpointCompleteBody,
    response: Response,
    user=Depends(require_owner),
):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    try:
        return seo_checkpoint_center.human_completed(
            entry_id=body.entry_id,
            platform=body.platform,
            target_site=body.target_site,
            page_url=body.page_url,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="checkpoint entry not found")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"checkpoint completion failed: {type(exc).__name__}: {exc}",
        )


@router.get("/projects")
def projects():
    return {"items": svc.list_projects()}

@router.post("/projects")
def create_project(body: ProjectCreate):
    try:
        return svc.create_project(body.site, body.target_count, body.bonus_count)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"site analysis failed: {type(exc).__name__}: {exc}")

@router.get("/projects/{project_id}")
def project(project_id: str):
    try:
        return svc.get_project(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")

@router.post("/projects/{project_id}/prepare")
def prepare(project_id: str):
    try:
        return svc.prepare_placement_plan(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")

@router.get("/projects/{project_id}/report")
def report(project_id: str):
    try:
        return svc.report(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")

@router.post("/projects/{project_id}/decision")
def decision(project_id: str, body: DecisionBody):
    try:
        return svc.decide_project(project_id, body.decision)
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/projects/{project_id}/sync")
def sync(project_id: str):
    try:
        a = svc.sync_project_drafts(project_id)
        b = svc.sync_publications(project_id)
        c = svc.schedule_replacements(project_id)
        return {"drafts": a, "publications": b, "replacements": c}
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found")

@router.post("/projects/{project_id}/placements/{placement_id}/publication")
def publication(project_id: str, placement_id: str, body: PublicationBody):
    try:
        return svc.mark_publication(project_id, placement_id, body.publication_url)
    except KeyError:
        raise HTTPException(status_code=404, detail="project or placement not found")

@router.post("/projects/{project_id}/placements/{placement_id}/verify")
def verify(project_id: str, placement_id: str):
    try:
        return svc.verify_placement(project_id, placement_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="project or placement not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
