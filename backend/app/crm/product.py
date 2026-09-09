from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.auth import get_current_user

from .db import SessionLocal
from .service import user_id, create_audit
from .service import crm_owner_user_id


router = APIRouter(
    prefix="/product",
    tags=["CRM Product"],
)


def uid(current_user) -> int:
    """Resolve CRM tenant owner for owner/employee."""
    return crm_owner_user_id(current_user)



class ContactUpdate(BaseModel):
    display_name: Optional[str] = None
    primary_phone: Optional[str] = None
    primary_email: Optional[str] = None
    company_name: Optional[str] = None
    visual_type: Optional[str] = None
    business_activity: Optional[str] = None


class DealPatch(BaseModel):
    title: Optional[str] = None
    amount_kopeks: Optional[int] = None
    responsible_user_id: Optional[int] = None
    lost_reason: Optional[str] = None
    next_action_at: Optional[str] = None


class TaskPatch(BaseModel):
    status: str

class TaskCreateProduct(BaseModel):
    deal_id: Optional[int] = None
    contact_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    due_at: Optional[str] = None
    assigned_user_id: Optional[int] = None


@router.get("/overview")
def overview(account_id: str = "", current_user=Depends(get_current_user)):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    db = SessionLocal()

    try:
        params = {"owner": owner, "account_id": account_id}
        row = db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE status='open') AS open_deals,
                    COUNT(*) FILTER (WHERE status='won') AS won_deals,
                    COUNT(*) FILTER (WHERE status='lost') AS lost_deals,
                    COALESCE(SUM(amount_kopeks) FILTER (WHERE status='won'),0) AS won_amount_kopeks,
                    COUNT(*) FILTER (
                        WHERE status='open'
                          AND last_activity_at < NOW() - INTERVAL '3 days'
                    ) AS stale_deals
                FROM boris_crm_deals
                WHERE owner_user_id=:owner
                  AND (:account_id='' OR avito_account_id=:account_id)
            """), params,
        ).mappings().one()

        task = db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE t.status='open') AS open_tasks,
                    COUNT(*) FILTER (
                        WHERE t.status='open' AND t.due_at IS NOT NULL AND t.due_at < NOW()
                    ) AS overdue_tasks
                FROM boris_crm_tasks t
                WHERE t.owner_user_id=:owner
                  AND (
                    :account_id=''
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_deals d
                        WHERE d.id=t.deal_id AND d.owner_user_id=:owner AND d.avito_account_id=:account_id
                    )
                    OR (t.deal_id IS NULL AND (
                        EXISTS (SELECT 1 FROM boris_crm_conversations cv
                          WHERE cv.contact_id=t.contact_id AND cv.owner_user_id=:owner AND cv.account_id=:account_id)
                        OR (t.source='avito_calltracking' AND t.description LIKE ('avito_call_task:' || :account_id || ':%'))
                    ))
                  )
            """), params,
        ).mappings().one()

        contacts = db.execute(
            text("""
                SELECT COUNT(*)
                FROM boris_crm_contacts c
                WHERE c.owner_user_id=:owner
                  AND (
                    :account_id=''
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_deals d
                        WHERE d.contact_id=c.id AND d.owner_user_id=:owner AND d.avito_account_id=:account_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_conversations cv
                        WHERE cv.contact_id=c.id AND cv.owner_user_id=:owner AND cv.account_id=:account_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_activities ca
                         WHERE ca.contact_id=c.id AND ca.owner_user_id=:owner
                           AND ca.source IN ('avito_calltracking','boris_telephony') AND COALESCE(ca.metadata_json->>'account_id','')=:account_id
                    )
                  )
            """), params,
        ).scalar_one()

        conversations = db.execute(
            text("""
                SELECT COUNT(*)
                FROM boris_crm_conversations
                WHERE owner_user_id=:owner
                  AND (:account_id='' OR account_id=:account_id)
            """), params,
        ).scalar_one()

        return {"ok": True, "account_id": account_id or None, **dict(row), **dict(task),
                "contacts": contacts, "conversations": conversations}
    finally:
        db.close()


@router.get("/kanban")
def kanban(account_id: str = "", current_user=Depends(get_current_user)):
    owner = uid(current_user)
    db = SessionLocal()

    try:
        pipeline = db.execute(
            text("""
                SELECT id,name
                FROM boris_crm_pipelines
                WHERE owner_user_id=:owner
                  AND is_active=TRUE
                ORDER BY is_default DESC,id
                LIMIT 1
            """),
            {"owner": owner},
        ).mappings().first()

        if not pipeline:
            return {
                "pipeline": None,
                "columns": [],
            }

        stages = db.execute(
            text("""
                SELECT id,name,code,position,semantic_type
                FROM boris_crm_stages
                WHERE pipeline_id=:pid
                ORDER BY position,id
            """),
            {"pid": pipeline["id"]},
        ).mappings().all()

        deals = db.execute(
            text("""
                SELECT
                    d.id,
                    d.title,
                    d.amount_kopeks,
                    d.stage_id,
                    d.status,
                    d.source,
                    d.avito_account_id,
                    d.avito_chat_id,
                    d.last_activity_at,
                    d.next_action_at,
                    d.responsible_user_id,
                    c.id AS contact_id,
                    c.display_name AS contact_name,
                    c.primary_phone AS contact_phone,
                    u.email AS responsible_email
                FROM boris_crm_deals d
                LEFT JOIN boris_crm_contacts c
                  ON c.id=d.contact_id
                LEFT JOIN users u
                  ON u.id=d.responsible_user_id
                WHERE d.owner_user_id=:owner
                  AND d.pipeline_id=:pid
                  AND (:account_id='' OR d.avito_account_id=:account_id)
                ORDER BY
                    CASE
                      WHEN d.status='open' AND d.next_action_at IS NOT NULL AND d.next_action_at < NOW() THEN 0
                      WHEN d.status='open' AND d.next_action_at IS NOT NULL AND d.next_action_at::date = CURRENT_DATE THEN 1
                      WHEN d.status='open' AND d.next_action_at IS NULL THEN 2
                      WHEN d.status='open' THEN 3
                      ELSE 4
                    END,
                    d.next_action_at NULLS LAST,
                    COALESCE(d.last_activity_at,d.created_at) DESC,
                    d.id DESC
            """),
            {
                "owner": owner,
                "pid": pipeline["id"],
                "account_id": account_id,
            },
        ).mappings().all()

        columns = []

        for stage in stages:
            cards = [
                dict(d)
                for d in deals
                if d["stage_id"] == stage["id"]
            ]

            columns.append({
                **dict(stage),
                "deals": cards,
                "count": len(cards),
                "amount_kopeks": sum(
                    int(x["amount_kopeks"] or 0)
                    for x in cards
                ),
            })

        return {
            "pipeline": dict(pipeline),
            "columns": columns,
        }

    finally:
        db.close()


@router.get("/deals/{deal_id}")
def deal_detail(
    deal_id: int,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    db = SessionLocal()

    try:
        deal = db.execute(
            text("""
                SELECT
                    d.*,
                    s.name AS stage_name,
                    s.semantic_type AS stage_semantic,
                    p.name AS pipeline_name,
                    c.display_name AS contact_name,
                    c.primary_phone AS contact_phone,
                    c.primary_email AS contact_email,
                    c.company_name AS contact_company
                FROM boris_crm_deals d
                LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
                LEFT JOIN boris_crm_pipelines p ON p.id=d.pipeline_id
                LEFT JOIN boris_crm_contacts c ON c.id=d.contact_id
                WHERE d.id=:deal_id
                  AND d.owner_user_id=:owner
                  AND (:account_id='' OR d.avito_account_id=:account_id)
            """),
            {
                "deal_id": deal_id,
                "owner": owner,
                "account_id": account_id,
            },
        ).mappings().first()

        if not deal:
            raise HTTPException(404, "DEAL_NOT_FOUND")

        stages = db.execute(
            text("""
                SELECT id,name,position,semantic_type
                FROM boris_crm_stages
                WHERE pipeline_id=:pid
                ORDER BY position,id
            """),
            {"pid": deal["pipeline_id"]},
        ).mappings().all()

        activities = db.execute(
            text("""
                SELECT *
                FROM boris_crm_activities
                WHERE owner_user_id=:owner
                  AND deal_id=:deal_id
                ORDER BY created_at DESC,id DESC
                LIMIT 300
            """),
            {
                "owner": owner,
                "deal_id": deal_id,
            },
        ).mappings().all()

        tasks = db.execute(
            text("""
                SELECT *
                FROM boris_crm_tasks
                WHERE owner_user_id=:owner
                  AND deal_id=:deal_id
                ORDER BY
                    CASE WHEN status='open' THEN 0 ELSE 1 END,
                    due_at NULLS LAST,
                    id DESC
                LIMIT 100
            """),
            {
                "owner": owner,
                "deal_id": deal_id,
            },
        ).mappings().all()

        conversation = db.execute(
            text("""
                SELECT id,channel,account_id,external_chat_id,last_message_at
                FROM boris_crm_conversations
                WHERE owner_user_id=:owner
                  AND deal_id=:deal_id
                ORDER BY id DESC
                LIMIT 1
            """),
            {
                "owner": owner,
                "deal_id": deal_id,
            },
        ).mappings().first()

        team = db.execute(text("""
            SELECT u.id,u.email,u.role
              FROM users u
             WHERE u.id=:owner
                OR EXISTS (
                    SELECT 1 FROM user_account_access x
                     WHERE x.user_id=u.id AND x.account_id=:account_id AND x.can_view=TRUE
                )
             ORDER BY CASE WHEN u.id=:owner THEN 0 ELSE 1 END,u.email
        """), {"owner": owner, "account_id": deal["avito_account_id"] or account_id}).mappings().all()

        return {
            "deal": dict(deal),
            "stages": [dict(x) for x in stages],
            "activities": [dict(x) for x in activities],
            "tasks": [dict(x) for x in tasks],
            "conversation": dict(conversation) if conversation else None,
            "team": [dict(x) for x in team],
        }

    finally:
        db.close()


@router.patch("/deals/{deal_id}")
def patch_deal(
    deal_id: int,
    body: DealPatch,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    db = SessionLocal()

    try:
        exists = db.execute(
            text("""
                SELECT 1
                FROM boris_crm_deals
                WHERE id=:deal_id
                  AND owner_user_id=:owner
                  AND (:account_id='' OR avito_account_id=:account_id)
            """),
            {
                "deal_id": deal_id,
                "owner": owner,
                "account_id": account_id,
            },
        ).first()

        if not exists:
            raise HTTPException(404, "DEAL_NOT_FOUND")

        db.execute(
            text("""
                UPDATE boris_crm_deals
                SET
                    title=COALESCE(:title,title),
                    amount_kopeks=COALESCE(:amount,amount_kopeks),
                    responsible_user_id=COALESCE(:responsible,responsible_user_id),
                    lost_reason=COALESCE(:lost_reason,lost_reason),
                    next_action_at=COALESCE(
                        CAST(:next_action AS timestamptz),
                        next_action_at
                    ),
                    updated_at=NOW()
                WHERE id=:deal_id
                  AND owner_user_id=:owner
                  AND (:account_id='' OR avito_account_id=:account_id)
            """),
            {
                "title": body.title,
                "amount": body.amount_kopeks,
                "responsible": body.responsible_user_id,
                "lost_reason": body.lost_reason,
                "next_action": body.next_action_at,
                "deal_id": deal_id,
                "owner": owner,
                "account_id": account_id,
            },
        )

        create_audit(
            db,
            owner,
            action="deal_updated",
            entity_type="deal",
            entity_id=deal_id,
            actor_type="user",
            actor_id=owner,
            reason="Full CRM deal card",
        )

        db.commit()

        return {"ok": True}

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/contacts")
def contacts(
    q: str = "",
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    db = SessionLocal()

    try:
        pattern = f"%{q.strip()}%"
        rows = db.execute(
            text("""
                SELECT
                    c.*,
                    COUNT(DISTINCT d.id) FILTER (
                        WHERE :account_id='' OR d.avito_account_id=:account_id
                    ) AS deal_count,
                    COUNT(DISTINCT d.id) FILTER (
                        WHERE d.status='open' AND (:account_id='' OR d.avito_account_id=:account_id)
                    ) AS open_deals,
                    (SELECT t.title FROM boris_crm_tasks t
                      WHERE t.owner_user_id=:owner AND t.contact_id=c.id AND t.status='open'
                        AND (:account_id='' OR
                          EXISTS (SELECT 1 FROM boris_crm_deals td WHERE td.id=t.deal_id AND td.owner_user_id=:owner AND td.avito_account_id=:account_id)
                          OR (t.deal_id IS NULL AND (
                            EXISTS (SELECT 1 FROM boris_crm_conversations tcv WHERE tcv.owner_user_id=:owner AND tcv.contact_id=c.id AND tcv.account_id=:account_id)
                            OR (t.source='avito_calltracking' AND t.description LIKE ('avito_call_task:' || :account_id || ':%'))
                          )))
                      ORDER BY t.due_at NULLS LAST,t.id DESC LIMIT 1) AS next_action_title,
                    (SELECT t.due_at FROM boris_crm_tasks t
                      WHERE t.owner_user_id=:owner AND t.contact_id=c.id AND t.status='open'
                        AND (:account_id='' OR
                          EXISTS (SELECT 1 FROM boris_crm_deals td WHERE td.id=t.deal_id AND td.owner_user_id=:owner AND td.avito_account_id=:account_id)
                          OR (t.deal_id IS NULL AND (
                            EXISTS (SELECT 1 FROM boris_crm_conversations tcv WHERE tcv.owner_user_id=:owner AND tcv.contact_id=c.id AND tcv.account_id=:account_id)
                            OR (t.source='avito_calltracking' AND t.description LIKE ('avito_call_task:' || :account_id || ':%'))
                          )))
                      ORDER BY t.due_at NULLS LAST,t.id DESC LIMIT 1) AS next_action_at
                FROM boris_crm_contacts c
                LEFT JOIN boris_crm_deals d
                  ON d.contact_id=c.id AND d.owner_user_id=:owner
                WHERE c.owner_user_id=:owner
                  AND (
                    :account_id=''
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_deals dx
                        WHERE dx.contact_id=c.id AND dx.owner_user_id=:owner AND dx.avito_account_id=:account_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_conversations cv
                        WHERE cv.contact_id=c.id AND cv.owner_user_id=:owner AND cv.account_id=:account_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM boris_crm_activities ca
                         WHERE ca.contact_id=c.id AND ca.owner_user_id=:owner
                           AND ca.source IN ('avito_calltracking','boris_telephony') AND COALESCE(ca.metadata_json->>'account_id','')=:account_id
                    )
                  )
                  AND (
                    :empty=TRUE
                    OR COALESCE(c.display_name,'') ILIKE :pattern
                    OR COALESCE(c.primary_phone,'') ILIKE :pattern
                    OR COALESCE(c.primary_email,'') ILIKE :pattern
                    OR COALESCE(c.company_name,'') ILIKE :pattern
                  )
                GROUP BY c.id
                ORDER BY c.updated_at DESC,c.id DESC
                LIMIT 500
            """),
            {"owner": owner, "account_id": account_id, "pattern": pattern, "empty": not bool(q.strip())},
        ).mappings().all()
        profiles = {}
        if account_id:
            for vr in db.execute(text("""
                SELECT DISTINCT ON (key) key,value
                  FROM storage
                 WHERE account_id=:account_id AND key LIKE 'crm_contact_visual:%'
                 ORDER BY key,id DESC
            """), {"account_id": account_id}).mappings().all():
                try:
                    cid = int(str(vr["key"]).rsplit(":",1)[-1]); data=json.loads(vr["value"] or "{}")
                    if isinstance(data,dict): profiles[cid]=data
                except Exception:
                    continue
        result=[]
        for x in rows:
            item=dict(x); profile=profiles.get(int(item["id"]),{})
            item["visual_type"]=profile.get("visual_type")
            item["business_activity"]=profile.get("business_activity")
            result.append(item)
        return result
    finally:
        db.close()


@router.get("/contacts/{contact_id}")
def contact_detail(
    contact_id: int,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    db = SessionLocal()

    try:
        contact = db.execute(
            text("""
                SELECT *
                FROM boris_crm_contacts
                WHERE id=:id
                  AND owner_user_id=:owner
                  AND (
                    :account_id=''
                    OR EXISTS (SELECT 1 FROM boris_crm_deals d WHERE d.contact_id=boris_crm_contacts.id AND d.owner_user_id=:owner AND d.avito_account_id=:account_id)
                    OR EXISTS (SELECT 1 FROM boris_crm_conversations cv WHERE cv.contact_id=boris_crm_contacts.id AND cv.owner_user_id=:owner AND cv.account_id=:account_id)
                    OR EXISTS (SELECT 1 FROM boris_crm_activities ca WHERE ca.contact_id=boris_crm_contacts.id AND ca.owner_user_id=:owner AND ca.source IN ('avito_calltracking','boris_telephony') AND COALESCE(ca.metadata_json->>'account_id','')=:account_id)
                  )
            """),
            {
                "id": contact_id,
                "owner": owner,
                "account_id": account_id,
            },
        ).mappings().first()

        if not contact:
            raise HTTPException(404, "CONTACT_NOT_FOUND")

        deals = db.execute(
            text("""
                SELECT
                    d.*,
                    s.name AS stage_name
                FROM boris_crm_deals d
                LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
                WHERE d.owner_user_id=:owner
                  AND d.contact_id=:contact_id
                  AND (:account_id='' OR d.avito_account_id=:account_id)
                ORDER BY d.created_at DESC
            """),
            {
                "owner": owner,
                "contact_id": contact_id,
                "account_id": account_id,
            },
        ).mappings().all()

        conversations = db.execute(text("""
            SELECT id,channel,account_id,external_chat_id,title,unread_count,last_message_at,deal_id
              FROM boris_crm_conversations
             WHERE owner_user_id=:owner AND contact_id=:contact_id
               AND (:account_id='' OR account_id=:account_id)
             ORDER BY COALESCE(last_message_at,updated_at) DESC,id DESC
        """), {"owner": owner, "contact_id": contact_id, "account_id": account_id}).mappings().all()

        tasks = db.execute(text("""
            SELECT id,deal_id,contact_id,title,description,assigned_user_id,due_at,status,source,created_at,completed_at
              FROM boris_crm_tasks
             WHERE owner_user_id=:owner AND contact_id=:contact_id
               AND (:account_id='' OR deal_id IN (SELECT id FROM boris_crm_deals WHERE owner_user_id=:owner AND avito_account_id=:account_id)
                    OR (deal_id IS NULL AND (source IS DISTINCT FROM 'avito_calltracking' OR description LIKE ('avito_call_task:' || :account_id || ':%'))))
             ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END,due_at NULLS LAST,id DESC
             LIMIT 100
        """), {"owner": owner, "contact_id": contact_id, "account_id": account_id}).mappings().all()

        activities = db.execute(text("""
            SELECT id,deal_id,activity_type,channel,direction,title,body,source,source_ref,metadata_json,actor_type,actor_id,created_at
              FROM boris_crm_activities
             WHERE owner_user_id=:owner AND contact_id=:contact_id
               AND (:account_id='' OR deal_id IN (SELECT id FROM boris_crm_deals WHERE owner_user_id=:owner AND avito_account_id=:account_id)
                    OR (deal_id IS NULL AND (source IS DISTINCT FROM 'avito_calltracking' OR COALESCE(metadata_json->>'account_id','')=:account_id)))
             ORDER BY created_at DESC,id DESC LIMIT 300
        """), {"owner": owner, "contact_id": contact_id, "account_id": account_id}).mappings().all()

        visual_profile = {}
        if account_id:
            raw_profile = db.execute(text("""SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"""), {"a":account_id,"k":f"crm_contact_visual:{contact_id}"}).scalar()
            if raw_profile:
                try:
                    parsed=json.loads(raw_profile); visual_profile=parsed if isinstance(parsed,dict) else {}
                except Exception:
                    visual_profile={}
        return {
            "contact": {**dict(contact), "visual_type": visual_profile.get("visual_type"), "business_activity": visual_profile.get("business_activity")},
            "deals": [dict(x) for x in deals],
            "conversations": [dict(x) for x in conversations],
            "tasks": [dict(x) for x in tasks],
            "activities": [dict(x) for x in activities],
        }

    finally:
        db.close()


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    db = SessionLocal()

    try:
        row = db.execute(
            text("""
                SELECT 1
                FROM boris_crm_contacts
                WHERE id=:id
                  AND owner_user_id=:owner
                  AND (
                    :account_id=''
                    OR EXISTS (SELECT 1 FROM boris_crm_deals d WHERE d.contact_id=boris_crm_contacts.id AND d.owner_user_id=:owner AND d.avito_account_id=:account_id)
                    OR EXISTS (SELECT 1 FROM boris_crm_conversations cv WHERE cv.contact_id=boris_crm_contacts.id AND cv.owner_user_id=:owner AND cv.account_id=:account_id)
                    OR EXISTS (SELECT 1 FROM boris_crm_activities ca WHERE ca.contact_id=boris_crm_contacts.id AND ca.owner_user_id=:owner AND ca.source IN ('avito_calltracking','boris_telephony') AND COALESCE(ca.metadata_json->>'account_id','')=:account_id)
                  )
            """),
            {
                "id": contact_id,
                "owner": owner,
                "account_id": account_id,
            },
        ).first()

        if not row:
            raise HTTPException(404, "CONTACT_NOT_FOUND")

        db.execute(
            text("""
                UPDATE boris_crm_contacts
                SET
                    display_name=COALESCE(:name,display_name),
                    primary_phone=COALESCE(:phone,primary_phone),
                    primary_email=COALESCE(:email,primary_email),
                    company_name=COALESCE(:company,company_name),
                    updated_at=NOW()
                WHERE id=:id
                  AND owner_user_id=:owner
            """),
            {
                "name": body.display_name,
                "phone": body.primary_phone,
                "email": body.primary_email,
                "company": body.company_name,
                "id": contact_id,
                "owner": owner,
            },
        )

        if body.visual_type is not None or body.business_activity is not None:
            if not account_id:
                raise HTTPException(400, "ACCOUNT_REQUIRED_FOR_VISUAL_PROFILE")
            allowed_types={"auto","neutral","male","female","company"}
            allowed_activity={"","production","trade","services","transport","marketing","other"}
            visual_type=(body.visual_type or "auto").strip().lower()
            business_activity=(body.business_activity or "").strip().lower()
            if visual_type not in allowed_types or business_activity not in allowed_activity:
                raise HTTPException(400, "INVALID_VISUAL_PROFILE")
            key=f"crm_contact_visual:{contact_id}"
            payload=json.dumps({"visual_type":visual_type,"business_activity":business_activity},ensure_ascii=False)
            existing=db.execute(text("SELECT id FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),{"a":account_id,"k":key}).scalar()
            if existing:
                db.execute(text("UPDATE storage SET value=:v WHERE id=:i"),{"v":payload,"i":existing})
            else:
                db.execute(text("INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"),{"a":account_id,"k":key,"v":payload})

        create_audit(
            db,
            owner,
            action="contact_updated",
            entity_type="contact",
            entity_id=contact_id,
            actor_type="user",
            actor_id=owner,
            reason="CRM contact card",
        )

        db.commit()

        return {"ok": True}

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/tasks")
def create_product_task(
    body: TaskCreateProduct,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    title = str(body.title or "").strip()
    if not title:
        raise HTTPException(400, "TASK_TITLE_REQUIRED")
    db = SessionLocal()
    try:
        deal = None
        contact_id = body.contact_id
        task_account_id = account_id
        if body.deal_id is not None:
            deal = db.execute(text("""
                SELECT id,contact_id,avito_account_id,responsible_user_id
                  FROM boris_crm_deals
                 WHERE id=:deal_id AND owner_user_id=:owner
                   AND (:account_id='' OR avito_account_id=:account_id)
            """), {"deal_id": body.deal_id, "owner": owner, "account_id": account_id}).mappings().first()
            if not deal:
                raise HTTPException(404, "DEAL_NOT_FOUND")
            contact_id = deal["contact_id"]
            task_account_id = deal["avito_account_id"] or account_id
        elif contact_id is not None:
            contact_ok = db.execute(text("""
                SELECT 1 FROM boris_crm_contacts c
                 WHERE c.id=:contact_id AND c.owner_user_id=:owner
                   AND (:account_id='' OR EXISTS (SELECT 1 FROM boris_crm_conversations cv WHERE cv.owner_user_id=:owner AND cv.contact_id=c.id AND cv.account_id=:account_id)
                        OR EXISTS (SELECT 1 FROM boris_crm_deals d WHERE d.owner_user_id=:owner AND d.contact_id=c.id AND d.avito_account_id=:account_id))
            """), {"contact_id": contact_id, "owner": owner, "account_id": account_id}).first()
            if not contact_ok:
                raise HTTPException(404, "CONTACT_NOT_FOUND")
        else:
            raise HTTPException(400, "TASK_TARGET_REQUIRED")
        assigned = body.assigned_user_id if body.assigned_user_id is not None else (deal["responsible_user_id"] if deal else None)
        if assigned is None:
            assigned = owner
        if int(assigned) != int(owner):
            allowed = db.execute(text("SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE LIMIT 1"), {"u": int(assigned), "a": task_account_id}).first()
            if not allowed:
                raise HTTPException(400, "INVALID_ASSIGNEE")
        task_id = db.execute(text("""
            INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,assigned_user_id,due_at,status,source)
            VALUES(:owner,:deal_id,:contact_id,:title,:description,:assigned,CAST(:due_at AS timestamptz),'open','crm_workcenter')
            RETURNING id
        """), {"owner": owner, "deal_id": body.deal_id, "contact_id": contact_id, "title": title[:500],
              "description": (str(body.description).strip()[:2000] if body.description else None), "assigned": int(assigned), "due_at": body.due_at}).scalar_one()
        if body.deal_id is not None:
            db.execute(text("""
                UPDATE boris_crm_deals d SET next_action_at=(
                    SELECT MIN(t.due_at) FROM boris_crm_tasks t
                     WHERE t.deal_id=d.id AND t.owner_user_id=:owner AND t.status='open' AND t.due_at IS NOT NULL
                ),updated_at=NOW() WHERE d.id=:id AND d.owner_user_id=:owner
            """), {"owner": owner, "id": body.deal_id})
        db.execute(text("""
            INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,actor_type,actor_id)
            VALUES(:owner,:deal_id,:contact_id,'task_created','crm','Создан следующий шаг',:body,'boris_crm','user',:actor)
        """), {"owner": owner, "deal_id": body.deal_id, "contact_id": contact_id, "body": title[:500], "actor": str(owner)})
        db.commit()
        return {"ok": True, "id": int(task_id)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: int,
    body: TaskPatch,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()

    if body.status not in {"open", "done", "cancelled"}:
        raise HTTPException(400, "INVALID_TASK_STATUS")

    db = SessionLocal()

    try:
        result = db.execute(
            text("""
                UPDATE boris_crm_tasks
                SET
                    status=:status,
                    completed_at=
                        CASE
                            WHEN :status='done' THEN NOW()
                            ELSE completed_at
                        END
                WHERE id=:id
                  AND owner_user_id=:owner
                  AND (
                    :account_id=''
                    OR EXISTS (SELECT 1 FROM boris_crm_deals d WHERE d.id=boris_crm_tasks.deal_id AND d.owner_user_id=:owner AND d.avito_account_id=:account_id)
                    OR (deal_id IS NULL AND EXISTS (SELECT 1 FROM boris_crm_conversations cv WHERE cv.contact_id=boris_crm_tasks.contact_id AND cv.owner_user_id=:owner AND cv.account_id=:account_id))
                  )
                RETURNING id,deal_id,contact_id,title
            """),
            {
                "status": body.status,
                "id": task_id,
                "owner": owner,
                "account_id": account_id,
            },
        ).first()

        if not result:
            raise HTTPException(404, "TASK_NOT_FOUND")

        if result.deal_id is not None:
            db.execute(text("""
                UPDATE boris_crm_deals d SET next_action_at=(
                    SELECT MIN(t.due_at) FROM boris_crm_tasks t
                     WHERE t.deal_id=d.id AND t.owner_user_id=:owner AND t.status='open' AND t.due_at IS NOT NULL
                ),updated_at=NOW() WHERE d.id=:deal_id AND d.owner_user_id=:owner
            """), {"owner": owner, "deal_id": result.deal_id})
        if body.status == "done":
            db.execute(text("""
                INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,title,body,source,actor_type,actor_id)
                VALUES(:owner,:deal_id,:contact_id,'task_completed','crm','Задача выполнена',:body,'boris_crm','user',:actor)
            """), {"owner": owner, "deal_id": result.deal_id, "contact_id": result.contact_id, "body": result.title, "actor": str(owner)})

        db.commit()

        return {"ok": True}

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/analytics")
def analytics(
    days: int = 30,
    account_id: str = "",
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)
    account_id = (account_id or "").strip()
    days = max(1, min(int(days or 30), 365))
    db = SessionLocal()

    try:
        params = {"owner": owner, "days": days, "account_id": account_id}
        totals = db.execute(
            text("""
                SELECT COUNT(*) AS deals,
                    COUNT(*) FILTER (WHERE status='won') AS won,
                    COUNT(*) FILTER (WHERE status='lost') AS lost,
                    COUNT(*) FILTER (WHERE status='open') AS open,
                    COALESCE(SUM(amount_kopeks) FILTER (WHERE status='won'),0) AS won_amount_kopeks
                FROM boris_crm_deals
                WHERE owner_user_id=:owner
                  AND (:account_id='' OR avito_account_id=:account_id)
                  AND created_at >= NOW() - (:days * INTERVAL '1 day')
            """), params,
        ).mappings().one()

        stages = db.execute(
            text("""
                SELECT s.id,s.name,s.position,
                    COUNT(d.id) AS deals,
                    COALESCE(SUM(d.amount_kopeks),0) AS amount_kopeks
                FROM boris_crm_stages s
                JOIN boris_crm_pipelines p ON p.id=s.pipeline_id
                LEFT JOIN boris_crm_deals d
                  ON d.stage_id=s.id
                 AND d.owner_user_id=:owner
                 AND (:account_id='' OR d.avito_account_id=:account_id)
                 AND d.created_at >= NOW() - (:days * INTERVAL '1 day')
                WHERE p.owner_user_id=:owner AND p.is_active=TRUE
                GROUP BY s.id,s.name,s.position
                ORDER BY s.position,s.id
            """), params,
        ).mappings().all()

        sources = db.execute(
            text("""
                SELECT COALESCE(source,'unknown') AS source,
                    COUNT(*) AS deals,
                    COUNT(*) FILTER (WHERE status='won') AS won,
                    COALESCE(SUM(amount_kopeks) FILTER (WHERE status='won'),0) AS won_amount_kopeks
                FROM boris_crm_deals
                WHERE owner_user_id=:owner
                  AND (:account_id='' OR avito_account_id=:account_id)
                  AND created_at >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY COALESCE(source,'unknown')
                ORDER BY deals DESC
            """), params,
        ).mappings().all()

        funnel = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM messenger_messages mm
                     WHERE mm.account_id=cv.account_id AND mm.avito_chat_id=cv.external_chat_id
                       AND lower(coalesce(mm.direction,'')) IN ('in','incoming','client','user')
                       AND coalesce(mm.text,'') NOT LIKE '[Системное сообщение]%'
                )) AS inquiries,
                COUNT(*) FILTER (WHERE cv.deal_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM messenger_messages mm
                     WHERE mm.account_id=cv.account_id AND mm.avito_chat_id=cv.external_chat_id
                       AND lower(coalesce(mm.direction,'')) IN ('in','incoming','client','user')
                       AND coalesce(mm.text,'') NOT LIKE '[Системное сообщение]%'
                )) AS deals,
                COUNT(*) FILTER (WHERE c.primary_phone IS NOT NULL AND TRIM(c.primary_phone)<>'' AND EXISTS (
                    SELECT 1 FROM messenger_messages mm
                     WHERE mm.account_id=cv.account_id AND mm.avito_chat_id=cv.external_chat_id
                       AND lower(coalesce(mm.direction,'')) IN ('in','incoming','client','user')
                       AND coalesce(mm.text,'') NOT LIKE '[Системное сообщение]%'
                )) AS contacts_received,
                COUNT(*) FILTER (WHERE d.status='won' AND EXISTS (
                    SELECT 1 FROM messenger_messages mm
                     WHERE mm.account_id=cv.account_id AND mm.avito_chat_id=cv.external_chat_id
                       AND lower(coalesce(mm.direction,'')) IN ('in','incoming','client','user')
                       AND coalesce(mm.text,'') NOT LIKE '[Системное сообщение]%'
                )) AS won,
                COUNT(*) FILTER (WHERE d.status='lost' AND EXISTS (
                    SELECT 1 FROM messenger_messages mm
                     WHERE mm.account_id=cv.account_id AND mm.avito_chat_id=cv.external_chat_id
                       AND lower(coalesce(mm.direction,'')) IN ('in','incoming','client','user')
                       AND coalesce(mm.text,'') NOT LIKE '[Системное сообщение]%'
                )) AS lost
            FROM boris_crm_conversations cv
            LEFT JOIN boris_crm_contacts c ON c.id=cv.contact_id AND c.owner_user_id=:owner
            LEFT JOIN boris_crm_deals d ON d.id=cv.deal_id AND d.owner_user_id=:owner
            WHERE cv.owner_user_id=:owner
              AND (:account_id='' OR cv.account_id=:account_id)
              AND cv.created_at >= NOW() - (:days * INTERVAL '1 day')
        """), params).mappings().one()

        lost_reasons = db.execute(text("""
            SELECT COALESCE(NULLIF(TRIM(lost_reason),''),'Причина не указана') AS reason,COUNT(*) AS deals
              FROM boris_crm_deals
             WHERE owner_user_id=:owner
               AND (:account_id='' OR avito_account_id=:account_id)
               AND status='lost'
               AND created_at >= NOW() - (:days * INTERVAL '1 day')
             GROUP BY COALESCE(NULLIF(TRIM(lost_reason),''),'Причина не указана')
             ORDER BY deals DESC,reason
        """), params).mappings().all()

        quality = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status='open' AND next_action_at IS NULL) AS no_next_action,
                COUNT(*) FILTER (WHERE status='open' AND responsible_user_id IS NULL) AS no_responsible,
                COUNT(*) FILTER (WHERE status='open' AND COALESCE(amount_kopeks,0)=0) AS no_amount,
                COUNT(*) FILTER (WHERE status='open' AND NOT EXISTS (
                    SELECT 1 FROM boris_crm_contacts c WHERE c.id=boris_crm_deals.contact_id
                      AND NULLIF(TRIM(c.primary_phone),'') IS NOT NULL
                )) AS no_phone
            FROM boris_crm_deals
            WHERE owner_user_id=:owner
              AND (:account_id='' OR avito_account_id=:account_id)
        """), params).mappings().one()

        quality_deals = db.execute(text("""
            SELECT d.id,d.title,d.stage_id,s.name AS stage_name,d.contact_id,c.display_name AS contact_name,
                   d.amount_kopeks,d.responsible_user_id,d.next_action_at,c.primary_phone,
                   ARRAY_REMOVE(ARRAY[
                     CASE WHEN d.next_action_at IS NULL THEN 'Без следующего шага' END,
                     CASE WHEN d.responsible_user_id IS NULL THEN 'Не назначен ответственный' END,
                     CASE WHEN COALESCE(d.amount_kopeks,0)=0 THEN 'Не указана сумма' END,
                     CASE WHEN NULLIF(TRIM(c.primary_phone),'') IS NULL THEN 'Нет телефона' END
                   ],NULL) AS reasons
              FROM boris_crm_deals d
              LEFT JOIN boris_crm_contacts c ON c.id=d.contact_id AND c.owner_user_id=:owner
              LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
             WHERE d.owner_user_id=:owner
               AND (:account_id='' OR d.avito_account_id=:account_id)
               AND d.status='open'
               AND (d.next_action_at IS NULL OR d.responsible_user_id IS NULL OR COALESCE(d.amount_kopeks,0)=0 OR NULLIF(TRIM(c.primary_phone),'') IS NULL)
             ORDER BY
               CASE WHEN d.next_action_at IS NULL THEN 0 ELSE 1 END,
               d.updated_at DESC,d.id DESC
             LIMIT 50
        """), params).mappings().all()

        pipeline_value = db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE status='open') AS open_deals,
                   COUNT(*) FILTER (WHERE status='open' AND COALESCE(amount_kopeks,0)>0) AS deals_with_amount,
                   COALESCE(SUM(amount_kopeks) FILTER (WHERE status='open'),0) AS open_amount_kopeks
              FROM boris_crm_deals
             WHERE owner_user_id=:owner
               AND (:account_id='' OR avito_account_id=:account_id)
        """), params).mappings().one()

        managers = db.execute(text("""
            SELECT d.responsible_user_id,
                   COALESCE(u.email, CASE WHEN d.responsible_user_id IS NULL THEN 'Не назначен' ELSE 'Сотрудник #' || d.responsible_user_id::text END) AS responsible_name,
                   COUNT(*) FILTER (WHERE d.status='open') AS open_deals,
                   COUNT(*) FILTER (WHERE d.status='won' AND d.updated_at >= NOW() - (:days * INTERVAL '1 day')) AS won,
                   COUNT(*) FILTER (WHERE d.status='lost' AND d.updated_at >= NOW() - (:days * INTERVAL '1 day')) AS lost,
                   COALESCE(SUM(d.amount_kopeks) FILTER (WHERE d.status='won' AND d.updated_at >= NOW() - (:days * INTERVAL '1 day')),0) AS won_amount_kopeks,
                   COUNT(*) FILTER (WHERE d.status='open' AND d.next_action_at IS NULL) AS no_next_action,
                   COALESCE((SELECT COUNT(*) FROM boris_crm_tasks t JOIN boris_crm_deals td ON td.id=t.deal_id
                     WHERE td.owner_user_id=:owner AND (:account_id='' OR td.avito_account_id=:account_id)
                       AND t.status='open' AND t.due_at<NOW()
                       AND t.assigned_user_id IS NOT DISTINCT FROM d.responsible_user_id),0) AS overdue_tasks
              FROM boris_crm_deals d
              LEFT JOIN users u ON u.id=d.responsible_user_id
             WHERE d.owner_user_id=:owner
               AND (:account_id='' OR d.avito_account_id=:account_id)
             GROUP BY d.responsible_user_id,u.email
             ORDER BY open_deals DESC,won DESC,responsible_name
        """), params).mappings().all()

        calls = db.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE COALESCE((metadata_json->>'missed')::boolean,FALSE)=FALSE) AS answered,
                COUNT(*) FILTER (WHERE COALESCE((metadata_json->>'missed')::boolean,FALSE)=TRUE) AS missed,
                COUNT(*) FILTER (WHERE deal_id IS NOT NULL) AS linked_to_deal,
                COUNT(*) FILTER (WHERE COALESCE((metadata_json->>'analysis_available')::boolean,FALSE)=TRUE) AS analyzed
              FROM boris_crm_activities
             WHERE owner_user_id=:owner
               AND source='avito_calltracking'
               AND (:account_id='' OR COALESCE(metadata_json->>'account_id','')=:account_id)
               AND created_at >= NOW() - (:days * INTERVAL '1 day')
        """), params).mappings().one()
        callback_overdue = db.execute(text("""
            SELECT COUNT(*) FROM boris_crm_tasks t
             WHERE t.owner_user_id=:owner AND t.source='avito_calltracking'
               AND t.status='open' AND t.due_at IS NOT NULL AND t.due_at<NOW()
               AND (:account_id='' OR t.description LIKE ('avito_call_task:' || :account_id || ':%'))
        """), params).scalar_one()

        f = dict(funnel)
        inquiries_n = int(f.get("inquiries") or 0)
        deals_n = int(f.get("deals") or 0)
        won_n = int(f.get("won") or 0)
        f["inquiry_to_deal_pct"] = round(deals_n * 100.0 / inquiries_n, 1) if inquiries_n else None
        f["deal_to_won_pct"] = round(won_n * 100.0 / deals_n, 1) if deals_n else None

        return {"days": days, "account_id": account_id or None, "totals": dict(totals),
                "funnel": f, "quality": dict(quality), "quality_deals": [dict(x) for x in quality_deals],
                "lost_reasons": [dict(x) for x in lost_reasons],
                "pipeline_value": {**dict(pipeline_value), "weighted_forecast_kopeks": None, "forecast_reason": "Вероятности этапов не настроены; BORIS не выдумывает прогноз."},
                "managers": [dict(x) for x in managers],
                "calls": {**dict(calls), "callback_overdue": int(callback_overdue or 0)},
                "stages": [dict(x) for x in stages], "sources": [dict(x) for x in sources]}
    finally:
        db.close()


@router.get("/integrations")
def integrations(current_user=Depends(get_current_user)):
    owner = uid(current_user)
    db = SessionLocal()

    try:
        rows = {
            x["provider"]: dict(x)
            for x in db.execute(
                text("""
                    SELECT *
                    FROM boris_crm_connections
                    WHERE owner_user_id=:owner
                """),
                {"owner": owner},
            ).mappings().all()
        }

        result = []

        for provider, name in (
            ("boris", "BORIS CRM"),
            ("amocrm", "amoCRM"),
            ("bitrix24", "Битрикс24"),
        ):
            row = rows.get(provider)

            result.append({
                "provider": provider,
                "name": name,
                "status": (
                    row["status"]
                    if row
                    else (
                        "active"
                        if provider == "boris"
                        else "disabled"
                    )
                ),
                "mode": (
                    row["mode"]
                    if row
                    else (
                        "boris_master"
                        if provider == "boris"
                        else None
                    )
                ),
                "external_domain": (
                    row["external_domain"]
                    if row
                    else None
                ),
                "external_writes": False,
            })

        return result

    finally:
        db.close()

@router.get("/inquiries")
def inquiries(account_id: str = "", current_user=Depends(get_current_user)):
    """Pre-deal inbox: real conversations/leads preserved before they qualify into a deal."""
    owner = uid(current_user); account_id=(account_id or "").strip(); db=SessionLocal()
    try:
        rows=db.execute(text("""
          SELECT cv.id,cv.account_id,cv.external_chat_id,cv.title,cv.unread_count,cv.last_message_at,
                 cv.contact_id,c.display_name AS contact_name,c.primary_phone AS contact_phone,
                 ml.stage AS lead_stage,ml.has_phone,ml.msg_count,ml.item_title,ml.reminder_at,
                 ml.note,ml.assigned_user_id,
                 (SELECT COUNT(*) FROM messenger_messages mm
                   WHERE mm.account_id=cv.account_id AND mm.avito_chat_id=cv.external_chat_id
                     AND lower(coalesce(mm.direction,'')) IN ('in','incoming','client','user')
                     AND coalesce(mm.text,'') NOT LIKE '[Системное сообщение]%') AS incoming_count
          FROM boris_crm_conversations cv
          LEFT JOIN boris_crm_contacts c ON c.id=cv.contact_id AND c.owner_user_id=:owner
          LEFT JOIN messenger_leads ml ON ml.account_id=cv.account_id AND ml.avito_chat_id=cv.external_chat_id
          WHERE cv.owner_user_id=:owner AND cv.deal_id IS NULL
            AND (:account_id='' OR cv.account_id=:account_id)
            AND EXISTS (
              SELECT 1 FROM messenger_messages real_in
               WHERE real_in.account_id=cv.account_id
                 AND real_in.avito_chat_id=cv.external_chat_id
                 AND lower(coalesce(real_in.direction,'')) IN ('in','incoming','client','user')
                 AND coalesce(real_in.text,'') NOT LIKE '[Системное сообщение]%'
            )
          ORDER BY COALESCE(cv.last_message_at,cv.updated_at) DESC,cv.id DESC
          LIMIT 300
        """),{"owner":owner,"account_id":account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally: db.close()

@router.get("/tasks")
def tasks(account_id: str = "", current_user=Depends(get_current_user)):
    """One operational work list backed by canonical boris_crm_tasks."""
    owner=uid(current_user); account_id=(account_id or "").strip(); db=SessionLocal()
    try:
        rows=db.execute(text("""
          SELECT t.id,t.title,t.description,t.due_at,t.status,t.source,t.deal_id,t.contact_id,
                 t.assigned_user_id,d.responsible_user_id,
                 d.title AS deal_title,d.avito_account_id,c.display_name AS contact_name,
                 CASE WHEN t.status='open' AND t.due_at<now()
                      THEN GREATEST(0,EXTRACT(EPOCH FROM (now()-t.due_at))::bigint)
                      ELSE 0 END AS overdue_seconds,
                 COALESCE(rr.reminder_count,0)::int AS reminder_count,
                 rr.last_reminded_at,
                 CASE
                   WHEN t.status<>'open' OR t.due_at IS NULL OR t.due_at>=now() THEN 'none'
                   WHEN now()-t.due_at>=interval '72 hours' THEN 'manager_critical'
                   WHEN now()-t.due_at>=interval '24 hours' THEN 'manager_urgent'
                   WHEN now()-t.due_at>=interval '4 hours' THEN 'manager_repeat'
                   ELSE 'manager_reminder'
                 END AS escalation_level,
                 false AS owner_action_required
          FROM boris_crm_tasks t
          LEFT JOIN boris_crm_deals d ON d.id=t.deal_id AND d.owner_user_id=:owner
          LEFT JOIN boris_crm_contacts c ON c.id=t.contact_id AND c.owner_user_id=:owner
          LEFT JOIN LATERAL (
            SELECT count(*) AS reminder_count,max(a.created_at) AS last_reminded_at
              FROM boris_crm_activities a
             WHERE a.deal_id=t.deal_id
               AND a.source='brain_crm_overdue_reminder'
               AND COALESCE(a.metadata_json->>'task_id','')=t.id::text
          ) rr ON true
          WHERE t.owner_user_id=:owner AND (:account_id='' OR d.avito_account_id=:account_id OR
            (t.deal_id IS NULL AND (
              EXISTS (SELECT 1 FROM boris_crm_conversations cv WHERE cv.owner_user_id=:owner AND cv.contact_id=t.contact_id AND cv.account_id=:account_id)
              OR (t.source='avito_calltracking' AND t.description LIKE ('avito_call_task:' || :account_id || ':%'))
            )))
          ORDER BY CASE WHEN t.status='open' AND t.due_at<now() THEN 0 WHEN t.status='open' THEN 1 ELSE 2 END,
                   t.due_at NULLS LAST,t.id DESC LIMIT 300
        """),{"owner":owner,"account_id":account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally: db.close()
