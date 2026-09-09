from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.auth import get_current_user

from .db import SessionLocal
from .service import (
    user_id,
    ensure_default_pipeline,
    create_audit,
    crm_owner_user_id,
)


router = APIRouter(
    prefix="/bridge",
    tags=["CRM Bridge"],
)


###############################################################################
# INPUT MODELS
###############################################################################


class DialogRef(BaseModel):
    account_id: str
    avito_chat_id: str


class StageChange(BaseModel):
    stage_id: int
    lost_reason: Optional[str] = None


class DealUpdate(BaseModel):
    amount_kopeks: Optional[int] = None
    title: Optional[str] = None


###############################################################################
# AUTHORIZATION
###############################################################################


def _uid(user) -> int:
    try:
        return user_id(user)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="CRM user context unavailable",
        ) from exc


def _columns(db, table: str) -> set[str]:
    return set(
        db.execute(
            text("""
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name=:t
            """),
            {"t": table},
        ).scalars()
    )


def _assert_account_access(
    db,
    owner_user_id: int,
    account_id: str,
):
    """
    CRM_BRIDGE_EXPLICIT_EMPLOYEE_ACCESS_V1

    Fail-closed BORIS CRM account authorization.

    Access is granted only when:

    1. the current user directly owns the requested account; OR
    2. the current user has an explicit row in user_account_access
       for this exact account_id with can_view=TRUE.

    IMPORTANT:
    An employee is NOT converted to the owner's user_id here.
    This prevents an employee who can access one account from
    automatically gaining every account belonging to that owner.
    """

    try:
        actor_user_id = int(owner_user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="CRM_USER_CONTEXT_INVALID",
        ) from exc

    account_id = str(
        account_id or ""
    ).strip()

    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="ACCOUNT_ID_REQUIRED",
        )

    # Platform owner is intentionally cross-account in BORIS. Keep the
    # employee/client fail-closed checks below unchanged for everyone else.
    platform_role = db.execute(
        text("SELECT role FROM users WHERE id=:actor LIMIT 1"),
        {"actor": actor_user_id},
    ).scalar()
    if str(platform_role or "").strip().lower() == "owner":
        return

    # ------------------------------------------------------------
    # 1. Detect the live ownership schema.
    # ------------------------------------------------------------

    cols = _columns(
        db,
        "accounts",
    )

    candidates = [
        "organization_id",
        "user_id",
        "owner_user_id",
        "owner_id",
    ]

    ownership = next(
        (
            col
            for col in candidates
            if col in cols
        ),
        None,
    )

    if ownership is None:
        raise HTTPException(
            status_code=503,
            detail="ACCOUNT_OWNERSHIP_SCHEMA_UNSUPPORTED",
        )

    # ------------------------------------------------------------
    # 2. Direct ownership.
    # ------------------------------------------------------------

    owned = db.execute(
        text(f"""
            SELECT 1
              FROM accounts
             WHERE account_id=:account_id
               AND {ownership}=:actor
             LIMIT 1
        """),
        {
            "account_id": account_id,
            "actor": actor_user_id,
        },
    ).first()

    if owned:
        return

    # ------------------------------------------------------------
    # 3. Explicit employee grant.
    #
    # Fail closed:
    # - exact actor
    # - exact account
    # - can_view must be TRUE
    # ------------------------------------------------------------

    uaa_cols = _columns(
        db,
        "user_account_access",
    )

    required = {
        "user_id",
        "account_id",
        "can_view",
    }

    if not required.issubset(uaa_cols):
        raise HTTPException(
            status_code=503,
            detail="USER_ACCOUNT_ACCESS_SCHEMA_UNSUPPORTED",
        )

    granted = db.execute(
        text("""
            SELECT 1
              FROM user_account_access
             WHERE user_id=:actor
               AND account_id=:account_id
               AND can_view IS TRUE
             LIMIT 1
        """),
        {
            "actor": actor_user_id,
            "account_id": account_id,
        },
    ).first()

    if granted:
        return

    # ------------------------------------------------------------
    # 4. Deny everything else.
    # ------------------------------------------------------------

    raise HTTPException(
        status_code=403,
        detail="ACCOUNT_ACCESS_DENIED",
    )


def _canonical_account_owner(
    db,
    actor_user_id: int,
    account_id: str,
) -> int:
    """Authorize the actor for one account, then resolve the CRM tenant owner.

    Employee access is checked against the exact account, but CRM entities are
    always stored/read under accounts.owner_user_id. This prevents the same
    Avito dialog from being materialized once per employee.
    """
    _assert_account_access(
        db,
        actor_user_id,
        account_id,
    )

    row = db.execute(
        text("""
            SELECT owner_user_id
              FROM accounts
             WHERE account_id=:account_id
             LIMIT 1
        """),
        {"account_id": str(account_id)},
    ).first()

    if not row or row[0] is None:
        raise HTTPException(
            status_code=503,
            detail="CRM_ACCOUNT_OWNER_MISSING",
        )

    return int(row[0])


###############################################################################
# MESSAGE STORAGE ADAPTER
###############################################################################


def _message_columns(db) -> set[str]:
    return _columns(
        db,
        "messenger_messages",
    )


def _expr(
    cols: set[str],
    preferred: list[str],
    fallback: str,
) -> str:
    for name in preferred:
        if name in cols:
            return f'"{name}"'

    return fallback


def _load_dialog(
    db,
    account_id: str,
    chat_id: str,
):
    cols = _message_columns(db)

    required = {
        "account_id",
        "avito_chat_id",
    }

    if not required.issubset(cols):
        raise HTTPException(
            status_code=503,
            detail="MESSENGER_SCHEMA_UNSUPPORTED",
        )

    message_id = _expr(
        cols,
        [
            "avito_message_id",
            "message_id",
        ],
        "NULL::text",
    )

    direction = _expr(
        cols,
        ["direction"],
        "'incoming'::text",
    )

    body = _expr(
        cols,
        [
            "text",
            "message_text",
            "body",
        ],
        "''::text",
    )

    if "avito_created_at" in cols:
        created = (
            "CASE "
            "WHEN avito_created_at IS NULL THEN stored_at "
            "WHEN avito_created_at > 100000000000 "
            "THEN to_timestamp(avito_created_at / 1000.0) "
            "ELSE to_timestamp(avito_created_at) "
            "END"
        )
    else:
        created = _expr(
            cols,
            [
                "created_at",
                "sent_at",
                "stored_at",
            ],
            "NOW()",
        )

    title = _expr(
        cols,
        ["item_title"],
        "NULL::text",
    )

    item_id = _expr(
        cols,
        [
            "item_id",
            "avito_item_id",
        ],
        "NULL::text",
    )

    client_name = _expr(
        cols,
        [
            "client_name",
            "sender_name",
        ],
        "NULL::text",
    )

    rows = db.execute(
        text(f"""
            SELECT
                {message_id}::text AS external_message_id,
                {direction}::text AS direction,
                {body}::text AS body,
                {created} AS sent_at,
                {title}::text AS item_title,
                {item_id}::text AS item_id,
                {client_name}::text AS client_name
            FROM messenger_messages
            WHERE account_id=:account_id
              AND avito_chat_id=:chat_id
            ORDER BY {created}, id
        """),
        {
            "account_id": account_id,
            "chat_id": chat_id,
        },
    ).mappings().all()

    return [
        dict(row)
        for row in rows
    ]


###############################################################################
# IDENTITY RESOLUTION
###############################################################################


def _contact_for_dialog(
    db,
    owner: int,
    chat_id: str,
    client_name: Optional[str],
):
    row = db.execute(
        text("""
            SELECT *
              FROM boris_crm_contacts
             WHERE owner_user_id=:owner
               AND avito_chat_id=:chat
             ORDER BY id
             LIMIT 1
        """),
        {
            "owner": owner,
            "chat": chat_id,
        },
    ).mappings().first()

    if row:
        if (
            client_name
            and str(client_name).strip()
            and str(client_name).strip().lower() not in {"клиент avito", "покупатель"}
            and str(row["display_name"] or "").strip().lower() in {"", "клиент avito", "покупатель"}
        ):
            db.execute(
                text("""
                    UPDATE boris_crm_contacts
                       SET display_name=:name,
                           updated_at=NOW()
                     WHERE id=:id
                """),
                {
                    "name": client_name,
                    "id": row["id"],
                },
            )

        return int(row["id"]), False

    display_name = (
        client_name
        or "Клиент Avito"
    )

    contact_id = db.execute(
        text("""
            INSERT INTO boris_crm_contacts
                (
                    owner_user_id,
                    display_name,
                    source,
                    source_ref,
                    avito_chat_id
                )
            VALUES
                (
                    :owner,
                    :name,
                    'avito',
                    :chat,
                    :chat
                )
            RETURNING id
        """),
        {
            "owner": owner,
            "name": display_name,
            "chat": chat_id,
        },
    ).scalar_one()

    create_audit(
        db,
        owner,
        action="contact_created",
        entity_type="contact",
        entity_id=contact_id,
        actor_type="system",
        reason="Avito dialog identity resolution",
    )

    return int(contact_id), True


###############################################################################
# DEAL RESOLUTION / MATURITY GATE
###############################################################################

_DEAL_TRIGGERS = {
    "first_inquiry",
    "after_engaged",
    "after_qualification",
    "after_contact",
    "after_target_action",
}


def _account_sales_settings(db, account_id: str) -> dict:
    """Account-scoped product settings without a parallel settings engine."""
    row = db.execute(text("""
        SELECT value FROM storage
         WHERE account_id=:a AND key='mop_crm_sales_settings'
         ORDER BY id DESC LIMIT 1
    """), {"a": account_id}).scalar()
    data = {}
    if row:
        try:
            data = json.loads(row) or {}
        except Exception:
            data = {}
    trigger = str(data.get("deal_trigger") or "first_inquiry")
    if trigger not in _DEAL_TRIGGERS:
        trigger = "first_inquiry"
    return {"deal_trigger": trigger}


def _qualification_to_lead_fields(q: dict) -> dict:
    """Map same-call MOP qualification into existing messenger_leads columns.

    No second AI extraction: values come only from qualification_fields already
    produced from the client dialog in the MOP call. Unknown values never erase
    previously known lead data.
    """
    fields = (q or {}).get("qualification_fields") or {}
    if not isinstance(fields, dict):
        return {}
    object_place = []
    rent_term = []
    need_type = []
    for raw_key, raw_value in fields.items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        key = str(raw_key or "").lower().replace("ё", "е")
        if any(x in key for x in ("адрес", "объект", "доставк", "куда")):
            object_place.append(value)
        elif any(x in key for x in ("срок", "период", "до какого", "на сколько")):
            rent_term.append(value)
        elif any(x in key for x in ("размер", "тип", "новая", "б/у", "аренда или", "покупка", "комплект", "состояние", "что нужно", "оформлен", "юрлиц")):
            need_type.append(value)
    def joined(vals, limit=240):
        out=[]
        for v in vals:
            if v not in out:
                out.append(v)
        return "; ".join(out)[:limit] or None
    return {
        "object_place": joined(object_place),
        "rent_term": joined(rent_term),
        "need_type": joined(need_type),
    }


def _persist_qualification_to_lead(db, account_id: str, chat_id: str, q: dict) -> None:
    vals = _qualification_to_lead_fields(q)
    if not any(vals.values()):
        return
    db.execute(text("""
        UPDATE messenger_leads
           SET object_place=COALESCE(:object_place,object_place),
               rent_term=COALESCE(:rent_term,rent_term),
               need_type=COALESCE(:need_type,need_type),
               updated_at=now()
         WHERE account_id=:a AND avito_chat_id=:c
    """), {"a": account_id, "c": chat_id, **vals})


def _latest_mop_qualification(db, account_id: str, chat_id: str) -> dict:
    raw = db.execute(text("""
        SELECT ai_summary FROM mop_drafts
         WHERE account_id=:a AND avito_chat_id=:c
           AND NULLIF(TRIM(ai_summary),'') IS NOT NULL
         ORDER BY id DESC LIMIT 1
    """), {"a": account_id, "c": chat_id}).scalar()
    if not raw:
        raw = db.execute(text("""
            SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1
        """), {"a": account_id, "k": "mop_qualification:" + str(chat_id)}).scalar()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _deal_gate(db, account_id: str, chat_id: str, rows: list[dict]) -> tuple[bool, str, dict]:
    settings = _account_sales_settings(db, account_id)
    trigger = settings["deal_trigger"]
    incoming_count = sum(
        1 for r in rows
        if _normal_direction(r.get("direction")) == "in"
        and not str(r.get("body") or "").lstrip().startswith("[Системное сообщение]")
    )
    lead = db.execute(text("""
        SELECT has_phone,stage,msg_count FROM messenger_leads
         WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 1
    """), {"a": account_id, "c": chat_id}).mappings().first() or {}
    q = _latest_mop_qualification(db, account_id, chat_id)
    _persist_qualification_to_lead(db, account_id, chat_id, q)
    stage = str(q.get("qualification_stage") or "").upper()
    phone = bool(lead.get("has_phone") or q.get("phone_received"))
    target = stage == "TARGET_ACTION" or str(q.get("crm_action") or "").lower() == "create_deal"
    qualified = stage in {"QUALIFIED", "TARGET_ACTION"}
    engaged = incoming_count >= 2
    # CRM capture invariant: every real inbound Avito inquiry creates a deal.
    # Qualification still controls stage/next action, but must never suppress the CRM card.
    allow = incoming_count > 0
    return allow, trigger, {
        "incoming_count": incoming_count,
        "engaged": engaged,
        "qualified": qualified,
        "phone_received": phone,
        "target_action": target,
        "qualification": q,
    }


def _manager_next_action(db, account_id: str, chat_id: str, maturity: dict, lead: dict | None, mop: dict | None, rop: dict | None, tasks: list, deal: dict | None) -> dict:
    """Deterministic product recommendation from existing dialog/CRM state. No new engine/storage."""
    q = maturity.get("qualification") or {}
    from datetime import datetime as _manager_dt, timezone as _manager_tz
    now = _manager_dt.now(_manager_tz.utc)
    open_tasks = [dict(x) for x in (tasks or []) if str(x.get("status") or "") == "open"]
    if open_tasks:
        t = open_tasks[0]
        due = t.get("due_at")
        overdue = bool(due and due < now)
        return {"kind":"complete_task" if overdue else "task","title":t.get("title") or "Выполнить задачу","reason":"Есть просроченная задача" if overdue else "Следующий шаг уже запланирован","task_id":t.get("id"),"due_at":str(due) if due else None,"requires_confirmation":True}
    stage = str(q.get("qualification_stage") or "").upper()
    nxt = str(q.get("next_action") or "").strip()
    target = str(q.get("target_action") or "").strip()
    phone = bool(maturity.get("phone_received") or (lead or {}).get("has_phone"))
    mop_status = str((mop or {}).get("status") or "").lower()
    if mop_status == "human_required":
        return {"kind":"take_human","title":"Взять диалог человеку","reason":"МОП запросил участие менеджера","requires_confirmation":True}
    low = (nxt+" "+target+" "+str(q.get("intent") or "")).lower()
    if phone and any(x in low for x in ("замер","встреч")):
        return {"kind":"task","title":nxt or target or "Записать клиента на замер","reason":"Контакт получен и клиент готов к замеру/встрече","suggested_due_hours":1,"requires_confirmation":True}
    if phone and any(x in low for x in ("расч","смет","кп","коммерческ")):
        return {"kind":"task","title":nxt or "Подготовить расчёт клиенту","reason":"Контакт получен; следующий обещанный результат — расчёт","suggested_due_hours":4,"requires_confirmation":True}
    if phone:
        return {"kind":"call","title":"Позвонить клиенту","reason":"Телефон уже получен — контакт готов к следующему шагу","requires_confirmation":False}
    if any(x in low for x in ("замер","встреч")):
        return {"kind":"task","title":nxt or target or "Записать клиента на замер","reason":"Клиент готов к целевому действию","suggested_due_hours":1,"requires_confirmation":True}
    if any(x in low for x in ("расч","смет","кп","коммерческ")):
        return {"kind":"task","title":nxt or "Подготовить расчёт клиенту","reason":"Для продолжения продажи нужен расчёт","suggested_due_hours":4,"requires_confirmation":True}
    # Existing reactivation evidence is authoritative for lost/refused dialogs.
    rc = db.execute(text("SELECT id,primary_reason,status FROM reactivation_candidates WHERE account_id=:a AND avito_chat_id=:c AND status IN ('candidate','approved','drafted') ORDER BY id DESC LIMIT 1"), {"a":account_id,"c":chat_id}).mappings().first()
    if q.get("reactivation_candidate") or rc:
        return {"kind":"reactivation","title":"Передать в реактивацию","reason":str((rc or {}).get("primary_reason") or "Клиент требует возврата в диалог"),"requires_confirmation":True}
    rows = _load_dialog(db, account_id, chat_id)
    last_in = next((r for r in reversed(rows) if _normal_direction(r.get("direction")) == "in"), None)
    last_out = next((r for r in reversed(rows) if _normal_direction(r.get("direction")) == "out"), None)
    joined = " ".join(str(r.get("body") or "") for r in rows[-8:]).lower()
    if last_out and (not last_in or str(last_out.get("created_at") or "") >= str(last_in.get("created_at") or "")) and any(x in joined for x in ("расчёт","расчет","смет","коммерческ")):
        return {"kind":"reminder","title":"Проверить клиента после расчёта","reason":"Расчёт/предложение уже обсуждались — нужен контроль","suggested_due_hours":36,"requires_confirmation":True}
    if maturity.get("engaged") or stage in {"ENGAGED","QUALIFIED"}:
        return {"kind":"task","title":nxt or "Продолжить квалификацию клиента","reason":"Диалог продолжается; следующий шаг ещё не зафиксирован","suggested_due_hours":4,"requires_confirmation":True}
    if rows and str(rows[-1].get("direction") or "").lower() in {"out","outgoing"}:
        return {"kind":"reminder","title":"Вернуться к клиенту через 24 часа","reason":"После ответа клиент пока не продолжил диалог; сделку форсировать не нужно","suggested_due_hours":24,"requires_confirmation":True}
    return {"kind":"reply","title":nxt or "Ответить клиенту","reason":"Есть обращение без зафиксированного следующего действия","requires_confirmation":True}


def _sync_goal_side_effects(db, owner: int, account_id: str, chat_id: str,
                            deal_id: int | None, contact_id: int, maturity: dict,
                            dialog_goal: str | None = None):
    """Close the MOP goal loop using existing CRM activities/tasks only; idempotent by source_ref."""
    q = maturity.get("qualification") or {}
    achieved_kind = None
    if maturity.get("phone_received"):
        achieved_kind = "phone_received"
    elif maturity.get("target_action"):
        achieved_kind = "target_action"
    if achieved_kind:
        source_ref = f"mop_goal:{account_id}:{chat_id}:{achieved_kind}"
        exists = db.execute(text("SELECT 1 FROM boris_crm_activities WHERE owner_user_id=:o AND source_ref=:r LIMIT 1"), {"o": owner, "r": source_ref}).first()
        if not exists:
            body = str(q.get("target_action") or dialog_goal or achieved_kind).strip()
            db.execute(text("""
                INSERT INTO boris_crm_activities
                    (owner_user_id,deal_id,contact_id,activity_type,channel,direction,title,body,source,source_ref,actor_type,created_at)
                VALUES (:o,:d,:c,'goal','avito','in','Цель МОП достигнута',:b,'mop_goal',:r,'system',NOW())
            """), {"o": owner, "d": deal_id, "c": contact_id, "b": body[:1000], "r": source_ref})

    # Existing CRM task engine: deterministic support for explicit "tomorrow after HH:MM" follow-up.
    next_action = str(q.get("next_action") or "").strip()
    if not next_action or deal_id is None:
        return
    low = next_action.lower()
    import re as _re_goal
    m = _re_goal.search(r"(?:завтра).*?(?:после\s*)?(\d{1,2})[:.](\d{2})", low)
    if not m:
        return
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from zoneinfo import ZoneInfo as _ZI
        raw = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"), {"a": account_id}).scalar()
        try: cfg = json.loads(raw) if raw else {}
        except Exception: cfg = {}
        tz_name = str(((cfg.get("work_schedule") or {}).get("timezone")) or "Europe/Moscow")
        tz = _ZI(tz_name)
        local = _dt.now(_tz.utc).astimezone(tz) + _td(days=1)
        due = local.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0).astimezone(_tz.utc)
    except Exception:
        return
    source_ref = f"mop_followup:{account_id}:{chat_id}:{due.isoformat()}"
    exists = db.execute(text("SELECT 1 FROM boris_crm_tasks WHERE owner_user_id=:o AND deal_id=:d AND description=:sr LIMIT 1"), {"o": owner, "d": deal_id, "sr": source_ref}).first()
    if exists:
        return
    db.execute(text("""
        INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,due_at,assigned_user_id,status,source)
        VALUES(:o,:d,:c,:t,:sr,:due,:o,'open','mop_goal')
    """), {"o": owner, "d": deal_id, "c": contact_id, "t": next_action[:500], "sr": source_ref, "due": due})



def _sync_deal_stage_from_maturity(db, owner: int, deal_id: int | None, maturity: dict):
    if deal_id is None:
        return None
    current = db.execute(text("SELECT d.stage_id,d.status,s.position,s.code,s.name,d.pipeline_id FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id WHERE d.id=:d AND d.owner_user_id=:o"), {"d": deal_id, "o": owner}).mappings().first()
    if not current or current.get("status") != "open":
        return None
    target_code = "qualified" if (maturity.get("qualified") or maturity.get("target_action")) else ("contacted" if (maturity.get("phone_received") or maturity.get("engaged")) else None)
    if not target_code:
        return None
    target = db.execute(text("SELECT id,position,code,name FROM boris_crm_stages WHERE pipeline_id=:p AND code=:c AND semantic_type='open' ORDER BY position,id LIMIT 1"), {"p": current["pipeline_id"], "c": target_code}).mappings().first()
    if not target or int(target.get("position") or 0) <= int(current.get("position") or 0):
        return None
    source_ref = f"crm_maturity_stage:{deal_id}:{target_code}"
    exists = db.execute(text("SELECT 1 FROM boris_crm_activities WHERE owner_user_id=:o AND source_ref=:r LIMIT 1"), {"o": owner, "r": source_ref}).first()
    db.execute(text("UPDATE boris_crm_deals SET stage_id=:s,last_activity_at=NOW(),updated_at=NOW() WHERE id=:d AND owner_user_id=:o AND status='open'"), {"s": target["id"], "d": deal_id, "o": owner})
    if not exists:
        db.execute(text("INSERT INTO boris_crm_activities(owner_user_id,deal_id,activity_type,channel,title,body,source,source_ref,actor_type) VALUES(:o,:d,'stage_change','crm','Этап обновлён автоматически',:b,'crm_maturity',:r,'system')"), {"o": owner, "d": deal_id, "b": f"{current.get('name') or '—'} → {target.get('name') or target_code}", "r": source_ref})
    return int(target["id"])

def _deal_for_dialog(
    db,
    owner: int,
    contact_id: int,
    account_id: str,
    chat_id: str,
    item_title: Optional[str],
    item_id: Optional[str],
):
    row = db.execute(
        text("""
            SELECT *
              FROM boris_crm_deals
             WHERE owner_user_id=:owner
               AND avito_account_id=:account_id
               AND avito_chat_id=:chat
               AND status='open'
             ORDER BY id DESC
             LIMIT 1
        """),
        {
            "owner": owner,
            "account_id": account_id,
            "chat": chat_id,
        },
    ).mappings().first()

    if row:
        return int(row["id"]), False

    pipeline_id = ensure_default_pipeline(
        db,
        owner,
    )

    stage_id = db.execute(
        text("""
            SELECT id
              FROM boris_crm_stages
             WHERE pipeline_id=:pid
             ORDER BY position,id
             LIMIT 1
        """),
        {"pid": pipeline_id},
    ).scalar_one()

    title = (
        item_title
        or "Обращение с Avito"
    )

    deal_id = db.execute(
        text("""
            INSERT INTO boris_crm_deals
                (
                    owner_user_id,
                    contact_id,
                    pipeline_id,
                    stage_id,
                    title,
                    source,
                    source_ref,
                    avito_account_id,
                    avito_item_id,
                    avito_chat_id,
                    status,
                    last_activity_at
                )
            VALUES
                (
                    :owner,
                    :contact_id,
                    :pipeline_id,
                    :stage_id,
                    :title,
                    'avito',
                    :source_ref,
                    :account_id,
                    :item_id,
                    :chat,
                    'open',
                    NOW()
                )
            RETURNING id
        """),
        {
            "owner": owner,
            "contact_id": contact_id,
            "pipeline_id": pipeline_id,
            "stage_id": stage_id,
            "title": title,
            "source_ref": (
                f"{account_id}:{chat_id}"
            ),
            "account_id": account_id,
            "item_id": item_id,
            "chat": chat_id,
        },
    ).scalar_one()

    create_audit(
        db,
        owner,
        action="deal_created",
        entity_type="deal",
        entity_id=deal_id,
        actor_type="system",
        reason="New Avito dialog",
    )

    return int(deal_id), True


###############################################################################
# CONVERSATION
###############################################################################


def _conversation_for_dialog(
    db,
    owner: int,
    contact_id: int,
    deal_id: Optional[int],
    account_id: str,
    chat_id: str,
    item_title: Optional[str],
):
    row = db.execute(
        text("""
            SELECT id
              FROM boris_crm_conversations
             WHERE owner_user_id=:owner
               AND channel='avito'
               AND account_id=:account_id
               AND external_chat_id=:chat
             LIMIT 1
        """),
        {
            "owner": owner,
            "account_id": account_id,
            "chat": chat_id,
        },
    ).first()

    if row:
        conversation_id = int(row[0])

        db.execute(
            text("""
                UPDATE boris_crm_conversations
                   SET contact_id=:contact_id,
                       deal_id=:deal_id,
                       title=COALESCE(:title,title),
                       updated_at=NOW()
                 WHERE id=:id
            """),
            {
                "contact_id": contact_id,
                "deal_id": deal_id,
                "title": item_title,
                "id": conversation_id,
            },
        )

        return conversation_id, False

    conversation_id = db.execute(
        text("""
            INSERT INTO boris_crm_conversations
                (
                    owner_user_id,
                    contact_id,
                    deal_id,
                    channel,
                    account_id,
                    external_chat_id,
                    title
                )
            VALUES
                (
                    :owner,
                    :contact_id,
                    :deal_id,
                    'avito',
                    :account_id,
                    :chat,
                    :title
                )
            RETURNING id
        """),
        {
            "owner": owner,
            "contact_id": contact_id,
            "deal_id": deal_id,
            "account_id": account_id,
            "chat": chat_id,
            "title": item_title,
        },
    ).scalar_one()

    return int(conversation_id), True


###############################################################################
# MESSAGE MIRROR
###############################################################################


def _normal_direction(value: Optional[str]) -> str:
    v = (value or "").lower()

    if v in {
        "out",
        "outgoing",
        "sent",
        "manager",
        "assistant",
    }:
        return "out"

    return "in"


def _synthetic_message_id(
    account_id: str,
    chat_id: str,
    row: dict,
) -> str:
    raw = "|".join(
        [
            account_id,
            chat_id,
            str(row.get("sent_at") or ""),
            str(row.get("direction") or ""),
            str(row.get("body") or ""),
        ]
    )

    return (
        "synthetic:"
        + hashlib.sha256(
            raw.encode(
                "utf-8",
                errors="ignore",
            )
        ).hexdigest()
    )


def _sync_messages(
    db,
    owner: int,
    conversation_id: int,
    contact_id: int,
    deal_id: Optional[int],
    account_id: str,
    chat_id: str,
    rows: list[dict],
):
    inserted = 0

    last_at = None

    for row in rows:
        external_id = (
            row.get("external_message_id")
            or _synthetic_message_id(
                account_id,
                chat_id,
                row,
            )
        )

        direction = _normal_direction(
            row.get("direction")
        )

        sent_at = (
            row.get("sent_at")
            or datetime.utcnow()
        )

        result = db.execute(
            text("""
                INSERT INTO boris_crm_messages
                    (
                        owner_user_id,
                        conversation_id,
                        external_message_id,
                        direction,
                        sender_type,
                        body,
                        sent_at,
                        metadata_json
                    )
                VALUES
                    (
                        :owner,
                        :conversation_id,
                        :external_id,
                        :direction,
                        :sender_type,
                        :body,
                        :sent_at,
                        CAST(:metadata AS jsonb)
                    )
                ON CONFLICT
                    (conversation_id, external_message_id)
                DO NOTHING
                RETURNING id
            """),
            {
                "owner": owner,
                "conversation_id": conversation_id,
                "external_id": external_id,
                "direction": direction,
                "sender_type": (
                    "manager"
                    if direction == "out"
                    else "client"
                ),
                "body": row.get("body") or "",
                "sent_at": sent_at,
                "metadata": "{}",
            },
        ).first()

        last_at = sent_at

        if not result:
            continue

        inserted += 1

        db.execute(
            text("""
                INSERT INTO boris_crm_activities
                    (
                        owner_user_id,
                        deal_id,
                        contact_id,
                        activity_type,
                        channel,
                        direction,
                        title,
                        body,
                        source,
                        source_ref,
                        actor_type,
                        created_at
                    )
                VALUES
                    (
                        :owner,
                        :deal_id,
                        :contact_id,
                        'message',
                        'avito',
                        :direction,
                        :title,
                        :body,
                        'messenger_messages',
                        :source_ref,
                        :actor_type,
                        :created_at
                    )
            """),
            {
                "owner": owner,
                "deal_id": deal_id,
                "contact_id": contact_id,
                "direction": direction,
                "title": (
                    "Исходящее сообщение"
                    if direction == "out"
                    else "Входящее сообщение"
                ),
                "body": row.get("body") or "",
                "source_ref": external_id,
                "actor_type": (
                    "manager"
                    if direction == "out"
                    else "client"
                ),
                "created_at": sent_at,
            },
        )

    if last_at is not None:
        db.execute(
            text("""
                UPDATE boris_crm_conversations
                   SET last_message_at=:last_at,
                       updated_at=NOW()
                 WHERE id=:id
            """),
            {
                "last_at": last_at,
                "id": conversation_id,
            },
        )

        if deal_id is not None:
            db.execute(
                text("""
                    UPDATE boris_crm_deals
                       SET last_activity_at=:last_at,
                           updated_at=NOW()
                     WHERE id=:id
                """),
                {
                    "last_at": last_at,
                    "id": deal_id,
                },
            )

    return inserted


###############################################################################
# CENTRAL SYNC
###############################################################################


def _sync_dialog(
    db,
    owner: int,
    account_id: str,
    chat_id: str,
    force_deal: bool = False,
):
    _assert_account_access(
        db,
        owner,
        account_id,
    )

    rows = _load_dialog(
        db,
        account_id,
        chat_id,
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="DIALOG_NOT_FOUND",
        )

    latest = rows[-1]

    client_name = next(
        (
            r.get("client_name")
            for r in reversed(rows)
            if r.get("client_name")
        ),
        None,
    )

    if not client_name:
        client_name = db.execute(
            text("""
                SELECT contact_name
                  FROM messenger_leads
                 WHERE account_id=:account_id
                   AND avito_chat_id=:chat
                   AND NULLIF(TRIM(contact_name),'') IS NOT NULL
                 ORDER BY id DESC
                 LIMIT 1
            """),
            {
                "account_id": account_id,
                "chat": chat_id,
            },
        ).scalar()

    item_title = next(
        (
            r.get("item_title")
            for r in reversed(rows)
            if r.get("item_title")
        ),
        None,
    )

    item_id = next(
        (
            r.get("item_id")
            for r in reversed(rows)
            if r.get("item_id")
        ),
        None,
    )

    contact_id, contact_created = (
        _contact_for_dialog(db, owner, chat_id, client_name)
    )
    # Phone extraction is deterministic DB/runtime work, not an AI call.
    phone = None
    for _r in reversed(rows):
        if _normal_direction(_r.get("direction")) != "in":
            continue
        body = str(_r.get("body") or "")
        match = re.search(r"(?<!\d)(?:\+?7|8)?[\s\-()]*(9\d{2})[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)", body)
        if match:
            digits = re.sub(r"\D", "", match.group(0))
            phone = ("+7" + digits[-10:]) if len(digits) >= 10 else None
            if phone:
                break
    if phone:
        db.execute(text("""UPDATE boris_crm_contacts SET primary_phone=COALESCE(primary_phone,:phone),updated_at=NOW() WHERE id=:id"""), {"phone": phone, "id": contact_id})

    existing_deal = db.execute(text("""
        SELECT id FROM boris_crm_deals
         WHERE owner_user_id=:owner AND avito_account_id=:account_id
           AND avito_chat_id=:chat AND status='open'
         ORDER BY id DESC LIMIT 1
    """), {"owner": owner, "account_id": account_id, "chat": chat_id}).scalar()
    deal_allowed, deal_trigger, maturity = _deal_gate(db, account_id, chat_id, rows)
    if existing_deal is not None:
        deal_id, deal_created = int(existing_deal), False
    elif deal_allowed or force_deal:
        deal_id, deal_created = _deal_for_dialog(
            db, owner, contact_id, account_id, chat_id, item_title, item_id
        )
    else:
        deal_id, deal_created = None, False

    if deal_id is not None:
        # When an inquiry matures later, attach its already preserved pre-deal history.
        db.execute(text("""UPDATE boris_crm_activities SET deal_id=:deal_id
            WHERE owner_user_id=:owner AND contact_id=:contact_id AND deal_id IS NULL
              AND source='messenger_messages'"""), {"deal_id": deal_id, "owner": owner, "contact_id": contact_id})
        assigned = db.execute(text("""SELECT assigned_user_id FROM messenger_leads WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 1"""), {"a": account_id, "c": chat_id}).scalar()
        if assigned is not None:
            db.execute(text("UPDATE boris_crm_deals SET responsible_user_id=:u WHERE id=:id AND responsible_user_id IS DISTINCT FROM :u"), {"u": int(assigned), "id": deal_id})
        else:
            # Every active deal needs an owner. Fallback only fills an empty field,
            # so a manager's explicit/manual assignment is never overwritten.
            db.execute(text("UPDATE boris_crm_deals SET responsible_user_id=:u WHERE id=:id AND responsible_user_id IS NULL"), {"u": int(owner), "id": deal_id})

    if deal_id is not None:
        _sync_deal_stage_from_maturity(db, owner, deal_id, maturity)

    conversation_id, conversation_created = (
        _conversation_for_dialog(
            db,
            owner,
            contact_id,
            deal_id,
            account_id,
            chat_id,
            item_title,
        )
    )

    inserted_messages = _sync_messages(
        db,
        owner,
        conversation_id,
        contact_id,
        deal_id,
        account_id,
        chat_id,
        rows,
    )
    _goal_text = db.execute(text("SELECT COALESCE(client_goal_text,client_goal,'') FROM accounts WHERE account_id=:a"), {"a": account_id}).scalar() or ""
    _sync_goal_side_effects(db, owner, account_id, chat_id, deal_id, contact_id, maturity, str(_goal_text))

    db.commit()

    return {
        "ok": True,
        "contact_id": contact_id,
        "deal_id": deal_id,
        "conversation_id": conversation_id,
        "contact_created": contact_created,
        "deal_created": deal_created,
        "deal_trigger": deal_trigger,
        "deal_eligible": deal_allowed,
        "deal_forced": bool(force_deal and existing_deal is None and not deal_allowed and deal_id is not None),
        "maturity": maturity,
        "conversation_created": conversation_created,
        "messages_added": inserted_messages,
        "source_messages": len(rows),
        "last_message_at": latest.get("sent_at"),
    }


###############################################################################
# CONTEXT RESPONSE
###############################################################################


def _context(
    db,
    owner: int,
    account_id: str,
    chat_id: str,
):
    goal_row = db.execute(text("""
        SELECT client_goal,client_goal_text FROM accounts WHERE account_id=:account_id LIMIT 1
    """), {"account_id": account_id}).mappings().first() or {}
    dialog_goal = str(goal_row.get("client_goal_text") or goal_row.get("client_goal") or "").strip() or None

    deal = db.execute(
        text("""
            SELECT
                d.*,
                s.name AS stage_name,
                s.semantic_type AS stage_semantic,
                c.display_name AS contact_name,
                c.primary_phone AS contact_phone,
                c.primary_email AS contact_email
            FROM boris_crm_deals d
            LEFT JOIN boris_crm_stages s
              ON s.id=d.stage_id
            LEFT JOIN boris_crm_contacts c
              ON c.id=d.contact_id
            WHERE d.owner_user_id=:owner
              AND d.avito_account_id=:account_id
              AND d.avito_chat_id=:chat
              AND d.status='open'
            ORDER BY d.id DESC
            LIMIT 1
        """),
        {
            "owner": owner,
            "account_id": account_id,
            "chat": chat_id,
        },
    ).mappings().first()

    if not deal:
        lead = db.execute(text("""
            SELECT id,stage,stage_source,has_phone,msg_count,last_msg_at,last_direction,
                   assigned_user_id,assigned_at,note,contact_name,updated_at
              FROM messenger_leads WHERE account_id=:account_id AND avito_chat_id=:chat
             ORDER BY id DESC LIMIT 1
        """), {"account_id": account_id, "chat": chat_id}).mappings().first()
        conversation = db.execute(text("""
            SELECT cv.*,c.display_name AS contact_name,c.primary_phone AS contact_phone
              FROM boris_crm_conversations cv
              LEFT JOIN boris_crm_contacts c ON c.id=cv.contact_id
             WHERE cv.owner_user_id=:owner AND cv.channel='avito'
               AND cv.account_id=:account_id AND cv.external_chat_id=:chat
             ORDER BY cv.id DESC LIMIT 1
        """), {"owner": owner, "account_id": account_id, "chat": chat_id}).mappings().first()
        mop = db.execute(text("""
            SELECT id,status,ai_summary,reply_text,reply_author,lead_id,created_at,updated_at,sent_at,send_error
              FROM mop_drafts WHERE account_id=:account_id AND avito_chat_id=:chat
             ORDER BY COALESCE(updated_at,created_at) DESC,id DESC LIMIT 1
        """), {"account_id": account_id, "chat": chat_id}).mappings().first()
        # Legacy MOP compatibility must also apply before a CRM deal exists.
        # Without this, fresh legacy-contour chats had ECS draft_ready but CRM mop=None
        # until deal creation, breaking the unified Messages/CRM context contract.
        if not mop:
            import json as _json
            for _key, _value in db.execute(text(
                "SELECT key,value FROM storage"
                " WHERE account_id='_telegram_drafts'"
                "   AND key LIKE 'messenger_draft:%'"
            )).fetchall():
                try:
                    _legacy = _json.loads(_value)
                except Exception:
                    continue
                _body = str(_legacy.get("text") or "").strip()
                _legacy_status = _legacy.get("status")
                if (_legacy.get("account_id") == account_id
                        and _legacy.get("avito_chat_id") == chat_id
                        and _legacy_status in ("pending", "send_failed")
                        and _body
                        and not _body.startswith("[Ошибка генерации")):
                    mop = {
                        "id": str(_key).split(":", 1)[-1],
                        "status": ("draft_ready" if _legacy_status == "pending" else "send_failed"),
                        "ai_summary": None,
                        "reply_text": _body,
                        "reply_author": "ai",
                        "lead_id": (lead.get("id") if lead else None),
                        "created_at": None,
                        "updated_at": None,
                        "sent_at": None,
                        "send_error": (_legacy.get("send_error") or None),
                        "source": "legacy",
                    }
                    break
        # Match ECS freshness semantics for legacy drafts: once a manager has
        # replied in Avito, an older pending AI draft is no longer active.
        if mop and mop.get("source") == "legacy" and mop.get("status") == "draft_ready":
            _last_real_direction = db.execute(text(
                "SELECT direction FROM messenger_messages"
                " WHERE account_id=:a AND avito_chat_id=:c"
                "   AND COALESCE(msg_type,'') <> 'system'"
                " ORDER BY avito_created_at DESC,id DESC LIMIT 1"),
                {"a": account_id, "c": chat_id}).scalar()
            if str(_last_real_direction or "").lower().startswith("out"):
                mop = None
        rop = db.execute(text("""
            SELECT id,analysis,created_at FROM chat_analysis
             WHERE account_id=:account_id AND chat_id=:chat ORDER BY created_at DESC,id DESC LIMIT 1
        """), {"account_id": account_id, "chat": chat_id}).mappings().first()
        history = []
        tasks = []
        if conversation and conversation.get("contact_id"):
            history = db.execute(text("""
                SELECT id,activity_type,channel,direction,title,body,source,actor_type,created_at
                  FROM boris_crm_activities
                 WHERE owner_user_id=:owner AND contact_id=:contact_id
                 ORDER BY created_at DESC,id DESC LIMIT 30
            """), {"owner": owner, "contact_id": conversation["contact_id"]}).mappings().all()
            tasks = db.execute(text("""
                SELECT id,title,description,due_at,status,source,created_at
                  FROM boris_crm_tasks
                 WHERE owner_user_id=:owner AND deal_id IS NULL AND contact_id=:contact_id
                 ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END,due_at NULLS LAST,id DESC
                 LIMIT 30
            """), {"owner": owner, "contact_id": conversation["contact_id"]}).mappings().all()
        rows = _load_dialog(db, account_id, chat_id)
        eligible, trigger, maturity = _deal_gate(db, account_id, chat_id, rows) if rows else (False, _account_sales_settings(db, account_id)["deal_trigger"], {})
        manager_next_action = _manager_next_action(db, account_id, chat_id, maturity, dict(lead) if lead else None, dict(mop) if mop else None, dict(rop) if rop else None, [dict(x) for x in tasks], None)
        return {"ok": True, "deal": None, "conversation": dict(conversation) if conversation else None,
                "manager_next_action": manager_next_action,
                "stages": [], "tasks": [dict(x) for x in tasks], "lead": dict(lead) if lead else None,
                "mop": dict(mop) if mop else None, "rop": dict(rop) if rop else None,
                "history": [dict(x) for x in history], "deal_trigger": trigger,
                "deal_eligible": eligible, "maturity": maturity,
                "qualification": maturity.get("qualification") or {},
                "dialog_goal": dialog_goal}

    stages = db.execute(
        text("""
            SELECT
                id,
                name,
                code,
                position,
                semantic_type
            FROM boris_crm_stages
            WHERE pipeline_id=:pipeline_id
            ORDER BY position,id
        """),
        {
            "pipeline_id": deal["pipeline_id"],
        },
    ).mappings().all()

    tasks = db.execute(
        text("""
            SELECT
                id,
                title,
                description,
                due_at,
                status,
                source,
                created_at
            FROM boris_crm_tasks
            WHERE owner_user_id=:owner
              AND deal_id=:deal_id
            ORDER BY
                CASE WHEN status='open'
                     THEN 0 ELSE 1 END,
                due_at NULLS LAST,
                id DESC
            LIMIT 30
        """),
        {
            "owner": owner,
            "deal_id": deal["id"],
        },
    ).mappings().all()

    lead = db.execute(
        text("""
            SELECT
                id,
                stage,
                stage_source,
                has_phone,
                msg_count,
                last_msg_at,
                last_direction,
                assigned_user_id,
                assigned_at,
                note,
                updated_at
            FROM messenger_leads
            WHERE account_id=:account_id
              AND avito_chat_id=:chat
            ORDER BY id DESC
            LIMIT 1
        """),
        {
            "account_id": account_id,
            "chat": chat_id,
        },
    ).mappings().first()

    mop = db.execute(
        text("""
            SELECT
                id,
                status,
                ai_summary,
                reply_text,
                reply_author,
                lead_id,
                created_at,
                updated_at,
                sent_at,
                send_error
            FROM mop_drafts
            WHERE account_id=:account_id
              AND avito_chat_id=:chat
            ORDER BY COALESCE(updated_at,created_at) DESC,id DESC
            LIMIT 1
        """),
        {
            "account_id": account_id,
            "chat": chat_id,
        },
    ).mappings().first()

    # Legacy MOP is still the active production contour for some accounts.
    # ECS already exposes its pending draft from storage; CRM context must show
    # the same send-ready state instead of falsely returning mop=None.
    if not mop:
        import json as _json
        for _key, _value in db.execute(text(
            "SELECT key,value FROM storage"
            " WHERE account_id='_telegram_drafts'"
            "   AND key LIKE 'messenger_draft:%'"
        )).fetchall():
            try:
                _legacy = _json.loads(_value)
            except Exception:
                continue
            _body = str(_legacy.get("text") or "").strip()
            _legacy_status = _legacy.get("status")
            if (_legacy.get("account_id") == account_id
                    and _legacy.get("avito_chat_id") == chat_id
                    and _legacy_status in ("pending", "send_failed")
                    and _body
                    and not _body.startswith("[Ошибка генерации")):
                mop = {
                    "id": str(_key).split(":", 1)[-1],
                    "status": ("draft_ready" if _legacy_status == "pending" else "send_failed"),
                    "ai_summary": None,
                    "reply_text": _body,
                    "reply_author": "ai",
                    "lead_id": (lead.get("id") if lead else None),
                    "created_at": None,
                    "updated_at": None,
                    "sent_at": None,
                    "send_error": None,
                    "source": "legacy",
                }
                break

    # Keep legacy CRM draft visibility aligned with ECS after a manager answers
    # directly in Avito; send_failed remains visible for explicit human retry.
    if mop and mop.get("source") == "legacy" and mop.get("status") == "draft_ready":
        _last_real_direction = db.execute(text(
            "SELECT direction FROM messenger_messages"
            " WHERE account_id=:a AND avito_chat_id=:c"
            "   AND COALESCE(msg_type,'') <> 'system'"
            " ORDER BY avito_created_at DESC,id DESC LIMIT 1"),
            {"a": account_id, "c": chat_id}).scalar()
        if str(_last_real_direction or "").lower().startswith("out"):
            mop = None

    rop = db.execute(
        text("""
            SELECT
                id,
                analysis,
                created_at
            FROM chat_analysis
            WHERE account_id=:account_id
              AND chat_id=:chat
            ORDER BY created_at DESC,id DESC
            LIMIT 1
        """),
        {
            "account_id": account_id,
            "chat": chat_id,
        },
    ).mappings().first()

    history = db.execute(
        text("""
            SELECT
                id,
                activity_type,
                channel,
                direction,
                title,
                body,
                source,
                actor_type,
                created_at
            FROM boris_crm_activities
            WHERE owner_user_id=:owner
              AND deal_id=:deal_id
            ORDER BY created_at DESC,id DESC
            LIMIT 30
        """),
        {
            "owner": owner,
            "deal_id": deal["id"],
        },
    ).mappings().all()

    rows = _load_dialog(db, account_id, chat_id)
    eligible, trigger, maturity = _deal_gate(db, account_id, chat_id, rows) if rows else (True, _account_sales_settings(db, account_id)["deal_trigger"], {})
    # Product-facing goal state reuses the existing MOP qualification/CRM maturity facts.
    # No parallel goal engine: TARGET_ACTION/contact is achieved; qualified/engaged is progress.
    _mop_status = str((dict(mop) if mop else {}).get("status") or "")
    _qstate = maturity.get("qualification") or {}
    _decline_text = " ".join(str(_qstate.get(k) or "").lower() for k in ("intent", "next_action", "target_action"))
    _declined = any(mark in _decline_text for mark in ("отказ", "не интересно", "неинтерес", "не готов", "не актуаль", "declin"))
    if _mop_status == "human_required":
        goal_progress = "HUMAN_HANDOFF"
    elif maturity.get("target_action") or maturity.get("phone_received"):
        goal_progress = "GOAL_ACHIEVED"
    elif _declined:
        goal_progress = "GOAL_DECLINED"
    elif maturity.get("qualified") or maturity.get("engaged"):
        goal_progress = "GOAL_IN_PROGRESS"
    else:
        goal_progress = "GOAL_NOT_STARTED"
    manager_next_action = _manager_next_action(db, account_id, chat_id, maturity, dict(lead) if lead else None, dict(mop) if mop else None, dict(rop) if rop else None, [dict(x) for x in tasks], dict(deal))
    return {
        "ok": True,
        "deal": dict(deal),
        "manager_next_action": manager_next_action,
        "deal_trigger": trigger,
        "deal_eligible": eligible,
        "maturity": maturity,
        "qualification": maturity.get("qualification") or {},
        "dialog_goal": dialog_goal,
        "goal_progress": goal_progress,
        "stages": [
            dict(x)
            for x in stages
        ],
        "tasks": [
            dict(x)
            for x in tasks
        ],
        "lead": dict(lead) if lead else None,
        "mop": dict(mop) if mop else None,
        "rop": dict(rop) if rop else None,
        "history": [dict(x) for x in history],
    }


###############################################################################
# INTERNAL PRODUCTION BRIDGE
###############################################################################


def sync_dialog_for_account(account_id: str, chat_id: str):
    """Link a persisted Avito dialog into BORIS CRM without user/API context.

    This is for trusted in-process ingestion only. Tenant ownership is resolved
    from the live accounts table, then the normal fail-closed bridge path is
    reused. No external CRM writes are performed here.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT owner_user_id
                  FROM accounts
                 WHERE account_id=:account_id
                 LIMIT 1
            """),
            {"account_id": str(account_id)},
        ).first()

        if not row or row[0] is None:
            raise RuntimeError("CRM_ACCOUNT_OWNER_MISSING")

        return _sync_dialog(
            db,
            int(row[0]),
            str(account_id),
            str(chat_id),
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


###############################################################################
# API
###############################################################################


@router.post("/sync-dialog")
def sync_dialog(
    body: DialogRef,
    current_user=Depends(
        get_current_user
    ),
):
    actor = _uid(current_user)

    db = SessionLocal()

    try:
        owner = _canonical_account_owner(
            db,
            actor,
            body.account_id,
        )
        return _sync_dialog(
            db,
            owner,
            body.account_id,
            body.avito_chat_id,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/promote-inquiry")
def promote_inquiry(
    body: DialogRef,
    current_user=Depends(get_current_user),
):
    """Manual lead→deal conversion through the same canonical bridge.

    No external send or AI call. Contact, conversation and pre-deal history are reused.
    """
    actor = _uid(current_user)
    db = SessionLocal()
    try:
        owner = _canonical_account_owner(db, actor, body.account_id)
        return _sync_dialog(db, owner, body.account_id, body.avito_chat_id, force_deal=True)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/context")
def dialog_context(
    account_id: str,
    avito_chat_id: str,
    current_user=Depends(
        get_current_user
    ),
):
    actor = _uid(current_user)

    db = SessionLocal()

    try:
        owner = _canonical_account_owner(
            db,
            actor,
            account_id,
        )

        return _context(
            db,
            owner,
            account_id,
            avito_chat_id,
        )
    finally:
        db.close()


@router.post("/context/sync")
def context_sync(
    body: DialogRef,
    current_user=Depends(
        get_current_user
    ),
):
    actor = _uid(current_user)

    db = SessionLocal()

    try:
        owner = _canonical_account_owner(
            db,
            actor,
            body.account_id,
        )
        sync = _sync_dialog(
            db,
            owner,
            body.account_id,
            body.avito_chat_id,
        )

        result = _context(
            db,
            owner,
            body.account_id,
            body.avito_chat_id,
        )

        result["sync"] = sync

        return result

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/deal/{deal_id}/stage")
def change_stage(
    deal_id: int,
    body: StageChange,
    current_user=Depends(
        get_current_user
    ),
):
    actor = _uid(current_user)
    owner = crm_owner_user_id(current_user)

    db = SessionLocal()

    try:
        row = db.execute(
            text("""
                SELECT
                    d.stage_id,
                    d.pipeline_id,
                    d.avito_account_id,
                    s.name AS old_stage_name
                FROM boris_crm_deals d
                LEFT JOIN boris_crm_stages s
                  ON s.id=d.stage_id
                WHERE d.id=:deal_id
                  AND d.owner_user_id=:owner
            """),
            {
                "deal_id": deal_id,
                "owner": owner,
            },
        ).mappings().first()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="DEAL_NOT_FOUND",
            )

        if row["avito_account_id"]:
            _assert_account_access(
                db,
                actor,
                str(row["avito_account_id"]),
            )

        new_stage = db.execute(
            text("""
                SELECT
                    id,
                    name,
                    semantic_type
                FROM boris_crm_stages
                WHERE id=:stage_id
                  AND pipeline_id=:pipeline_id
            """),
            {
                "stage_id": body.stage_id,
                "pipeline_id": row[
                    "pipeline_id"
                ],
            },
        ).mappings().first()

        if not new_stage:
            raise HTTPException(
                status_code=400,
                detail="INVALID_STAGE",
            )

        status = "open"
        lost_reason = None

        if new_stage["semantic_type"] == "won":
            status = "won"

        if new_stage["semantic_type"] == "lost":
            status = "lost"
            lost_reason = str(body.lost_reason or "").strip()
            if not lost_reason:
                raise HTTPException(status_code=400, detail="LOST_REASON_REQUIRED")
            lost_reason = lost_reason[:1000]

        db.execute(
            text("""
                UPDATE boris_crm_deals
                   SET stage_id=:stage_id,
                       status=:status,
                       lost_reason=:lost_reason,
                       updated_at=NOW(),
                       last_activity_at=NOW()
                 WHERE id=:deal_id
                   AND owner_user_id=:owner
            """),
            {
                "stage_id": body.stage_id,
                "status": status,
                "lost_reason": lost_reason,
                "deal_id": deal_id,
                "owner": owner,
            },
        )

        db.execute(
            text("""
                INSERT INTO boris_crm_activities
                    (
                        owner_user_id,
                        deal_id,
                        activity_type,
                        channel,
                        title,
                        body,
                        source,
                        actor_type
                    )
                VALUES
                    (
                        :owner,
                        :deal_id,
                        'stage_change',
                        'crm',
                        'Изменён этап сделки',
                        :body,
                        'boris_crm',
                        'user'
                    )
            """),
            {
                "owner": owner,
                "deal_id": deal_id,
                "body": (
                    f"{row['old_stage_name'] or '—'} → {new_stage['name']}"
                    + (f" · Причина: {lost_reason}" if lost_reason else "")
                ),
            },
        )

        create_audit(
            db,
            owner,
            action="deal_stage_changed",
            entity_type="deal",
            entity_id=deal_id,
            actor_type="user",
            actor_id=actor,
            reason=(
                f"{row['old_stage_name'] or '—'}"
                f" -> "
                f"{new_stage['name']}"
            ),
        )

        db.commit()

        try:
            from app.services.lead_notifications import queue_stage_event
            queue_stage_event(
                db,
                int(deal_id),
                str(row.get("avito_account_id") or ""),
                str(row.get("old_stage_name") or ""),
                str(new_stage.get("name") or ""),
                str(status or "open"),
                lost_reason,
            )
        except Exception as _notify_exc:
            print("LEAD_NOTIFICATION_QUEUE_STAGE_ERROR %s: %s" % (deal_id, type(_notify_exc).__name__), flush=True)

        return {
            "ok": True,
            "deal_id": deal_id,
            "stage_id": new_stage["id"],
            "stage_name": new_stage[
                "name"
            ],
            "status": status,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.patch("/deal/{deal_id}")
def update_deal(
    deal_id: int,
    body: DealUpdate,
    current_user=Depends(
        get_current_user
    ),
):
    actor = _uid(current_user)
    owner = crm_owner_user_id(current_user)

    if (
        body.amount_kopeks is None
        and body.title is None
    ):
        raise HTTPException(
            status_code=400,
            detail="NOTHING_TO_UPDATE",
        )

    db = SessionLocal()

    try:
        exists = db.execute(
            text("""
                SELECT avito_account_id
                FROM boris_crm_deals
                WHERE id=:id
                  AND owner_user_id=:owner
            """),
            {
                "id": deal_id,
                "owner": owner,
            },
        ).first()

        if not exists:
            raise HTTPException(
                status_code=404,
                detail="DEAL_NOT_FOUND",
            )

        if exists[0]:
            _assert_account_access(
                db,
                actor,
                str(exists[0]),
            )

        db.execute(
            text("""
                UPDATE boris_crm_deals
                   SET amount_kopeks=
                       COALESCE(
                           :amount,
                           amount_kopeks
                       ),
                       title=
                       COALESCE(
                           :title,
                           title
                       ),
                       updated_at=NOW()
                 WHERE id=:id
                   AND owner_user_id=:owner
            """),
            {
                "amount": body.amount_kopeks,
                "title": body.title,
                "id": deal_id,
                "owner": owner,
            },
        )

        create_audit(
            db,
            owner,
            action="deal_updated",
            entity_type="deal",
            entity_id=deal_id,
            actor_type="user",
            actor_id=actor,
            reason="CRM context panel",
        )

        db.commit()

        return {
            "ok": True,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/sync-existing")
def sync_existing(
    limit: int = 100,
    current_user=Depends(
        get_current_user
    ),
):
    """
    Safe lazy backfill for already-existing Avito dialogs.

    Only accounts owned by current BORIS user are considered.
    """

    owner = _uid(current_user)

    limit = max(
        1,
        min(
            int(limit or 100),
            300,
        ),
    )

    db = SessionLocal()

    try:
        account_cols = _columns(
            db,
            "accounts",
        )

        ownership = next(
            (
                col
                for col in (
                    "organization_id",
                    "user_id",
                    "owner_user_id",
                    "owner_id",
                )
                if col in account_cols
            ),
            None,
        )

        if not ownership:
            raise HTTPException(
                status_code=503,
                detail="ACCOUNT_OWNERSHIP_SCHEMA_UNSUPPORTED",
            )

        message_cols = _message_columns(
            db
        )

        if "avito_created_at" in message_cols:
            created_expr = (
                "CASE "
                "WHEN avito_created_at IS NULL THEN stored_at "
                "WHEN avito_created_at > 100000000000 "
                "THEN to_timestamp(avito_created_at / 1000.0) "
                "ELSE to_timestamp(avito_created_at) "
                "END"
            )
        else:
            created_expr = _expr(
                message_cols,
                [
                    "created_at",
                    "sent_at",
                    "stored_at",
                ],
                "NOW()",
            )

        dialogs = db.execute(
            text(f"""
                SELECT
                    m.account_id,
                    m.avito_chat_id,
                    MAX({created_expr}) AS last_at
                FROM messenger_messages m
                JOIN accounts a
                  ON a.account_id=m.account_id
                WHERE a.{ownership}=:owner
                GROUP BY
                    m.account_id,
                    m.avito_chat_id
                ORDER BY
                    MAX({created_expr}) DESC
                LIMIT :limit
            """),
            {
                "owner": owner,
                "limit": limit,
            },
        ).mappings().all()

        ok = 0
        failed = []

        for dialog in dialogs:
            try:
                _sync_dialog(
                    db,
                    owner,
                    str(
                        dialog[
                            "account_id"
                        ]
                    ),
                    str(
                        dialog[
                            "avito_chat_id"
                        ]
                    ),
                )

                ok += 1

            except Exception as exc:
                db.rollback()

                failed.append(
                    {
                        "account_id":
                            dialog[
                                "account_id"
                            ],
                        "avito_chat_id":
                            dialog[
                                "avito_chat_id"
                            ],
                        "error":
                            str(exc)[:300],
                    }
                )

        return {
            "ok": True,
            "dialogs_found": len(
                dialogs
            ),
            "synced": ok,
            "failed": failed[:20],
        }

    finally:
        db.close()
