from fastapi import APIRouter
from app.db.session import SessionLocal
from app.models.import_task import ImportTask

router = APIRouter(prefix="/api/import", tags=["import"])


@router.get("/{task_id}")
def get_import(task_id: int):
    db = SessionLocal()

    task = db.query(ImportTask).get(task_id)

    if not task:
        return {"error": "not found"}

    return {
        "id": task.id,
        "status": task.status,
        "progress": task.progress,
        "results": task.results
    }