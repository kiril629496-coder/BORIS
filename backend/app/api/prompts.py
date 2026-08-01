from fastapi import APIRouter
from pydantic import BaseModel
from app.db.session import SessionLocal
from app.models.saved_prompt import SavedPrompt

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class SavePromptRequest(BaseModel):
    account_id: str
    title: str
    text: str
    purpose: str = "banner"


@router.post("/save")
def save_prompt(req: SavePromptRequest):
    db = SessionLocal()
    try:
        p = SavedPrompt(
            account_id=req.account_id,
            title=req.title.strip()[:120],
            text=req.text,
            purpose=req.purpose or "banner",
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return {"status": "ok", "id": p.id, "title": p.title}
    finally:
        db.close()


@router.get("/list")
def list_prompts(account_id: str, purpose: str = ""):
    db = SessionLocal()
    try:
        q = db.query(SavedPrompt).filter(SavedPrompt.account_id == account_id)
        if purpose:
            q = q.filter(SavedPrompt.purpose == purpose)
        rows = q.order_by(SavedPrompt.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "text": r.text,
                "purpose": r.purpose,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int):
    db = SessionLocal()
    try:
        r = db.query(SavedPrompt).filter(SavedPrompt.id == prompt_id).first()
        if not r:
            return {"status": "error", "message": "не найден"}
        db.delete(r)
        db.commit()
        return {"status": "ok", "deleted": prompt_id}
    finally:
        db.close()
