from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import yandex_webmaster as yw

router = APIRouter(prefix="/api/prospecting/yandex-webmaster", tags=["yandex-webmaster"])

class RecrawlRequest(BaseModel):
    user_id: int
    host_id: str
    url: str

class HostRequest(BaseModel):
    user_id: int

class SitemapRequest(BaseModel):
    user_id: int
    host_id: str

@router.get("/readiness")
def readiness():
    return yw.readiness()

@router.post("/bootstrap")
def bootstrap():
    try:
        return yw.bootstrap()
    except yw.YandexWebmasterError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post("/sitemap")
def add_sitemap(req: SitemapRequest):
    try:
        return yw.add_sitemap(req.user_id, req.host_id)
    except yw.YandexWebmasterError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post("/recrawl")
def recrawl(req: RecrawlRequest):
    try:
        return yw.recrawl(req.user_id, req.host_id, req.url)
    except yw.YandexWebmasterError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.get("/quota")
def quota(user_id: int, host_id: str):
    try:
        return yw.recrawl_quota(user_id, host_id)
    except yw.YandexWebmasterError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.get("/diagnostics")
def diagnostics(user_id: int, host_id: str):
    try:
        return yw.diagnostics(user_id, host_id)
    except yw.YandexWebmasterError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
