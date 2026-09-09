from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from typing import Optional

from app.crm.db import SessionLocal
from app.api.auth import get_current_user

from .service import (
    user_id,
    ensure_default_pipeline,
    create_audit,
)

from .bridge import router as bridge_router
from .product import router as product_router
from .integration import router as integration_router
from .service import crm_owner_user_id
router = APIRouter(
    prefix="/api/crm",
    tags=["CRM"],
)


class DealCreate(BaseModel):
    title: str
    account_id: Optional[str] = None
    contact_id: Optional[int] = None
    stage_id: Optional[int] = None
    source: Optional[str] = "manual"
    source_ref: Optional[str] = None
    amount_kopeks: Optional[int] = None


class TaskCreate(BaseModel):
    deal_id: Optional[int] = None
    contact_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    due_at: Optional[str] = None
    assigned_user_id: Optional[int] = None


def uid(current_user) -> int:
    """Resolve CRM tenant owner for owner/employee."""
    return crm_owner_user_id(current_user)



@router.get("/health")
def crm_health(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        pipeline_id = ensure_default_pipeline(db, owner)

        return {
            "ok": True,
            "module": "crm",
            "version": "stage-a",
            "owner_user_id": owner,
            "default_pipeline_id": pipeline_id,
            "external_writes": False,
        }
    finally:
        db.close()


@router.get("/pipelines")
def boris_crm_pipelines(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        ensure_default_pipeline(db, owner)

        pipelines = db.execute(
            text("""
                SELECT id,name,code,is_default,is_active
                  FROM boris_crm_pipelines
                 WHERE owner_user_id=:uid
                 ORDER BY is_default DESC,id
            """),
            {"uid": owner, "account_id": account_id},
        ).mappings().all()

        result = []

        for p in pipelines:
            stages = db.execute(
                text("""
                    SELECT id,name,code,position,semantic_type
                      FROM boris_crm_stages
                     WHERE pipeline_id=:pid
                     ORDER BY position,id
                """),
                {"pid": p["id"]},
            ).mappings().all()

            obj = dict(p)
            obj["stages"] = [dict(x) for x in stages]
            result.append(obj)

        return result

    finally:
        db.close()


@router.get("/deals")
def boris_crm_deals(account_id: str = "", current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        ensure_default_pipeline(db, owner)

        rows = db.execute(
            text("""
                SELECT
                    d.*,
                    c.display_name AS contact_name,
                    c.primary_phone AS contact_phone,
                    s.name AS stage_name,
                    s.semantic_type AS stage_semantic
                FROM boris_crm_deals d
                LEFT JOIN boris_crm_contacts c ON c.id=d.contact_id
                LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
                WHERE d.owner_user_id=:uid
                  AND (:account_id='' OR d.avito_account_id=:account_id)
                ORDER BY
                    COALESCE(d.last_activity_at,d.created_at) DESC,
                    d.id DESC
                LIMIT 300
            """),
            {"uid": owner, "account_id": account_id},
        ).mappings().all()

        return [dict(r) for r in rows]

    finally:
        db.close()


@router.post("/deals")
def crm_create_deal(
    payload: DealCreate,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        account_id = str(payload.account_id or "").strip()
        if account_id:
            account_ok = db.execute(text("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:o LIMIT 1"), {"a": account_id, "o": owner}).first()
            if not account_ok:
                raise HTTPException(403, "ACCOUNT_ACCESS_DENIED")
        if payload.contact_id is not None:
            contact_ok = db.execute(text("""
                SELECT 1 FROM boris_crm_contacts c
                 WHERE c.id=:cid AND c.owner_user_id=:o
                   AND (:a='' OR EXISTS (SELECT 1 FROM boris_crm_conversations cv WHERE cv.owner_user_id=:o AND cv.contact_id=c.id AND cv.account_id=:a)
                        OR EXISTS (SELECT 1 FROM boris_crm_deals d WHERE d.owner_user_id=:o AND d.contact_id=c.id AND d.avito_account_id=:a))
            """), {"cid": payload.contact_id, "o": owner, "a": account_id}).first()
            if not contact_ok:
                raise HTTPException(400, "INVALID_CONTACT")
        pipeline_id = ensure_default_pipeline(db, owner)

        stage_id = payload.stage_id

        if stage_id is None:
            stage_id = db.execute(
                text("""
                    SELECT id
                      FROM boris_crm_stages
                     WHERE pipeline_id=:pid
                     ORDER BY position
                     LIMIT 1
                """),
                {"pid": pipeline_id},
            ).scalar_one()

        deal_id = db.execute(
            text("""
                INSERT INTO boris_crm_deals
                    (
                        owner_user_id,
                        contact_id,
                        pipeline_id,
                        stage_id,
                        title,
                        amount_kopeks,
                        source,
                        source_ref,
                        avito_account_id,
                        responsible_user_id
                    )
                VALUES
                    (
                        :uid,
                        :contact_id,
                        :pipeline_id,
                        :stage_id,
                        :title,
                        :amount,
                        :source,
                        :source_ref,
                        :account_id,
                        :uid
                    )
                RETURNING id
            """),
            {
                "uid": owner,
                "contact_id": payload.contact_id,
                "pipeline_id": pipeline_id,
                "stage_id": stage_id,
                "title": payload.title,
                "amount": payload.amount_kopeks,
                "source": payload.source,
                "source_ref": payload.source_ref,
                "account_id": account_id or None,
            },
        ).scalar_one()

        db.execute(text("""
            INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,actor_type,actor_id)
            VALUES(:o,:d,:c,'deal_created','crm','Создана сделка','Сделка создана вручную в BORIS CRM','boris_crm','user',:actor)
        """), {"o": owner, "d": deal_id, "c": payload.contact_id, "actor": str(owner)})

        create_audit(
            db,
            owner,
            action="deal_created",
            entity_type="deal",
            entity_id=deal_id,
            actor_id=owner,
            reason="CRM API",
        )

        db.commit()

        return {
            "ok": True,
            "id": deal_id,
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/contacts")
def boris_crm_contacts(account_id: str = "", current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT *
                  FROM boris_crm_contacts
                 WHERE owner_user_id=:uid
                   AND (:account_id='' OR EXISTS (SELECT 1 FROM boris_crm_deals dx WHERE dx.owner_user_id=:uid AND dx.contact_id=boris_crm_contacts.id AND dx.avito_account_id=:account_id))
                 ORDER BY updated_at DESC,id DESC
                 LIMIT 500
            """),
            {"uid": owner, "account_id": account_id},
        ).mappings().all()

        return [dict(r) for r in rows]
    finally:
        db.close()


@router.get("/conversations")
def boris_crm_conversations(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT
                    cv.*,
                    c.display_name AS contact_name,
                    c.primary_phone AS contact_phone,
                    d.title AS deal_title,
                    s.name AS deal_stage
                FROM boris_crm_conversations cv
                LEFT JOIN boris_crm_contacts c ON c.id=cv.contact_id
                LEFT JOIN boris_crm_deals d ON d.id=cv.deal_id
                LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
                WHERE cv.owner_user_id=:uid
                ORDER BY
                    cv.last_message_at DESC NULLS LAST,
                    cv.id DESC
                LIMIT 300
            """),
            {"uid": owner},
        ).mappings().all()

        return [dict(r) for r in rows]

    finally:
        db.close()


@router.get("/conversations/{conversation_id}/messages")
def boris_crm_messages(
    conversation_id: int,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        allowed = db.execute(
            text("""
                SELECT id
                  FROM boris_crm_conversations
                 WHERE id=:cid
                   AND owner_user_id=:uid
            """),
            {
                "cid": conversation_id,
                "uid": owner,
            },
        ).first()

        if not allowed:
            raise HTTPException(404, "Conversation not found")

        rows = db.execute(
            text("""
                SELECT *
                  FROM boris_crm_messages
                 WHERE conversation_id=:cid
                 ORDER BY sent_at,id
                 LIMIT 1000
            """),
            {"cid": conversation_id},
        ).mappings().all()

        return [dict(r) for r in rows]

    finally:
        db.close()


@router.get("/deals/{deal_id}/timeline")
def crm_timeline(
    deal_id: int,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT *
                  FROM boris_crm_activities
                 WHERE owner_user_id=:uid
                   AND deal_id=:deal_id
                 ORDER BY created_at DESC,id DESC
                 LIMIT 1000
            """),
            {
                "uid": owner,
                "deal_id": deal_id,
            },
        ).mappings().all()

        return [dict(r) for r in rows]

    finally:
        db.close()


@router.get("/sales-tasks")
def boris_crm_tasks(account_id: str = "", current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT t.*
                  FROM boris_crm_tasks t
                 WHERE t.owner_user_id=:uid
                   AND (:account_id='' OR EXISTS (SELECT 1 FROM boris_crm_deals dx WHERE dx.owner_user_id=:uid AND dx.avito_account_id=:account_id AND (dx.id=t.deal_id OR dx.contact_id=t.contact_id)))
                 ORDER BY
                    CASE WHEN status='open' THEN 0 ELSE 1 END,
                    due_at NULLS LAST,
                    id DESC
                 LIMIT 500
            """),
            {"uid": owner, "account_id": account_id},
        ).mappings().all()

        return [dict(r) for r in rows]

    finally:
        db.close()


@router.post("/sales-tasks")
def crm_create_task(
    payload: TaskCreate,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        task_id = db.execute(
            text("""
                INSERT INTO boris_crm_tasks
                    (
                        owner_user_id,
                        deal_id,
                        contact_id,
                        title,
                        description,
                        due_at,
                        assigned_user_id
                    )
                VALUES
                    (
                        :uid,
                        :deal_id,
                        :contact_id,
                        :title,
                        :description,
                        CAST(:due_at AS timestamptz),
                        COALESCE(:assigned_user_id,:uid)
                    )
                RETURNING id
            """),
            {
                "uid": owner,
                "deal_id": payload.deal_id,
                "contact_id": payload.contact_id,
                "title": payload.title,
                "description": payload.description,
                "due_at": payload.due_at,
                "assigned_user_id": payload.assigned_user_id,
            },
        ).scalar_one()

        create_audit(
            db,
            owner,
            action="task_created",
            entity_type="task",
            entity_id=task_id,
            actor_id=owner,
            reason="CRM API",
        )

        db.commit()

        return {
            "ok": True,
            "id": task_id,
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/connections")
def boris_crm_connections(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    db = SessionLocal()
    try:
        providers = {
            r["provider"]: dict(r)
            for r in db.execute(
                text("""
                    SELECT *
                      FROM boris_crm_connections
                     WHERE owner_user_id=:uid
                """),
                {"uid": owner},
            ).mappings().all()
        }

        result = []

        for provider, title in [
            ("boris", "BORIS CRM"),
            ("amocrm", "amoCRM"),
            ("bitrix24", "Битрикс24"),
        ]:
            saved = providers.get(provider)

            result.append({
                "provider": provider,
                "title": title,
                "status": (
                    saved["status"]
                    if saved
                    else ("active" if provider == "boris" else "disabled")
                ),
                "mode": (
                    saved["mode"]
                    if saved
                    else ("boris_master" if provider == "boris" else None)
                ),
            })

        return result

    finally:
        db.close()


@router.get("/workspace")
def crm_workspace(current_user=Depends(get_current_user)):
    """
    Contract for unified BORIS workspace.

    Next stages will populate conversations from the existing
    Unified Message Center without changing its current behavior.
    """
    owner = uid(current_user)

    db = SessionLocal()
    try:
        ensure_default_pipeline(db, owner)

        counts = db.execute(
            text("""
                SELECT
                    (SELECT COUNT(*)
                       FROM boris_crm_deals
                      WHERE owner_user_id=:uid
                        AND status='open') AS open_deals,

                    (SELECT COUNT(*)
                       FROM boris_crm_tasks
                      WHERE owner_user_id=:uid
                        AND status='open') AS open_tasks,

                    (SELECT COUNT(*)
                       FROM boris_crm_conversations
                      WHERE owner_user_id=:uid) AS conversations,

                    (SELECT COALESCE(SUM(unread_count),0)
                       FROM boris_crm_conversations
                      WHERE owner_user_id=:uid) AS unread
            """),
            {"uid": owner},
        ).mappings().one()

        return {
            "ok": True,
            "workspace": "sales",
            "layout": [
                "conversations",
                "messages",
                "deal_context",
            ],
            "counts": dict(counts),
            "channels_ready": [
                "avito",
                "telegram",
                "vk",
                "phone",
                "site",
            ],
            "external_crm_ready": [
                "amocrm",
                "bitrix24",
            ],
            "external_writes_enabled": False,
        }

    finally:
        db.close()

# BORIS CRM Stage B bridge
router.include_router(bridge_router)

# BORIS CRM Stage C product router
router.include_router(product_router)

# BORIS CRM Stage D integration

# BORIS CRM Stage D integration
router.include_router(integration_router)
