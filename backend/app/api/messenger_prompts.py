"""CRUD для кастомных промптов ИИ-менеджера продаж (на конкретный товар/услугу или общие).
Лимит: 3 бесплатных активных промпта на аккаунт, сверх лимита — платно (биллинг ещё не реализован,
пока просто считаем и блокируем создание 4-го без явного флага is_paid_slot)."""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import json as _json

from app.db.session import SessionLocal
from app.models.messenger_prompt import MessengerPrompt, MessengerPromptSource

router = APIRouter(prefix="/api/messenger_prompts", tags=["messenger_prompts"])

FREE_PROMPT_LIMIT = 3


class CreatePromptRequest(BaseModel):
    account_id: str
    label: str
    item_ids: List[str] = []
    custom_instructions: str = ""


class UpdatePromptRequest(BaseModel):
    label: Optional[str] = None
    item_ids: Optional[List[str]] = None
    custom_instructions: Optional[str] = None


@router.post("/create")
def create_prompt(req: CreatePromptRequest):
    db = SessionLocal()
    try:
        active_count = db.query(MessengerPrompt).filter(
            MessengerPrompt.account_id == req.account_id,
            MessengerPrompt.is_active == True,
        ).count()
        if active_count >= FREE_PROMPT_LIMIT:
            return {
                "status": "limit_reached",
                "message": f"Достигнут лимит бесплатных промптов ({FREE_PROMPT_LIMIT}). "
                           f"Дополнительные промпты оплачиваются отдельно.",
                "limit": FREE_PROMPT_LIMIT,
                "current_count": active_count,
            }
        prompt = MessengerPrompt(
            account_id=req.account_id,
            label=req.label,
            item_ids=_json.dumps(req.item_ids, ensure_ascii=False),
            custom_instructions=req.custom_instructions,
            is_active=True,
        )
        db.add(prompt)
        db.commit()
        db.refresh(prompt)
        return {"status": "ok", "prompt_id": prompt.id}
    finally:
        db.close()


@router.get("/list")
def list_prompts(account_id: str):
    db = SessionLocal()
    try:
        prompts = db.query(MessengerPrompt).filter(MessengerPrompt.account_id == account_id).all()
        result = []
        for p in prompts:
            sources = db.query(MessengerPromptSource).filter(MessengerPromptSource.prompt_id == p.id).all()
            result.append({
                "id": p.id,
                "label": p.label,
                "item_ids": _json.loads(p.item_ids) if p.item_ids else [],
                "custom_instructions": p.custom_instructions,
                "is_active": p.is_active,
                "sources_count": len(sources),
            })
        active_count = sum(1 for p in prompts if p.is_active)
        return {
            "status": "ok", "prompts": result,
            "free_limit": FREE_PROMPT_LIMIT, "active_count": active_count,
            "slots_remaining": max(0, FREE_PROMPT_LIMIT - active_count),
        }
    finally:
        db.close()


@router.post("/{prompt_id}/update")
def update_prompt(prompt_id: int, req: UpdatePromptRequest):
    db = SessionLocal()
    try:
        prompt = db.query(MessengerPrompt).filter(MessengerPrompt.id == prompt_id).first()
        if not prompt:
            return {"status": "error", "message": "Промпт не найден"}
        if req.label is not None:
            prompt.label = req.label
        if req.item_ids is not None:
            prompt.item_ids = _json.dumps(req.item_ids, ensure_ascii=False)
        if req.custom_instructions is not None:
            prompt.custom_instructions = req.custom_instructions
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/{prompt_id}/toggle")
def toggle_prompt(prompt_id: int):
    """Включить/выключить промпт временно (не удаляя) — если включаем, проверяем лимит."""
    db = SessionLocal()
    try:
        prompt = db.query(MessengerPrompt).filter(MessengerPrompt.id == prompt_id).first()
        if not prompt:
            return {"status": "error", "message": "Промпт не найден"}
        if not prompt.is_active:
            active_count = db.query(MessengerPrompt).filter(
                MessengerPrompt.account_id == prompt.account_id,
                MessengerPrompt.is_active == True,
            ).count()
            if active_count >= FREE_PROMPT_LIMIT:
                return {
                    "status": "limit_reached",
                    "message": f"Нельзя включить — уже {FREE_PROMPT_LIMIT} активных промпта. Отключите другой или оплатите доп. слот.",
                }
        prompt.is_active = not prompt.is_active
        db.commit()
        return {"status": "ok", "is_active": prompt.is_active}
    finally:
        db.close()


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int):
    db = SessionLocal()
    try:
        db.query(MessengerPromptSource).filter(MessengerPromptSource.prompt_id == prompt_id).delete()
        db.query(MessengerPrompt).filter(MessengerPrompt.id == prompt_id).delete()
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()
