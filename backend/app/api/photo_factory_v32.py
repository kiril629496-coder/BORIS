from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter(
    prefix="/api/photo-factory-v32",
    tags=["photo-factory-v32"],
)


NOTEBOOK = Path(
    '/root/BORIS/frontend/public/downloads/boris_photo_factory_t4_lowram.ipynb'
)


@router.get("/health")
def health():
    return {
        "ok": True,
        "service": "boris-photo-factory-v32",
        "version": "3.2",
        "profile": "free_t4_lowram",
        "engine": "sd15_ipadapter",
        "paid_calls": False,
        "gpu_rental": False,
        "default_max_tasks": 1,
        "width": 640,
        "height": 448,
        "steps": 18,
    }


@router.get("/free-notebook-t4")
def free_notebook_t4():

    if not NOTEBOOK.is_file():
        raise HTTPException(
            404,
            "T4 notebook unavailable",
        )

    return FileResponse(
        path=str(NOTEBOOK),
        media_type="application/x-ipynb+json",
        filename="boris_photo_factory_t4_lowram.ipynb",
    )
