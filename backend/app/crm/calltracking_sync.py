from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.api.calltracking import _ct_token, _get_calls
from .db import SessionLocal
from .service import ensure_default_pipeline


def _phone(raw: object) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = digits[-10:]
    if len(digits) != 10 or not digits.startswith("9"):
        return None
    return "+7" + digits


def _dt(raw: object) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _contact(db, owner: int, account_id: str, phone: str) -> tuple[int, bool]:
    last10 = re.sub(r"\D", "", phone)[-10:]
    # Shared lock with BORIS Phone prevents cross-module duplicate contacts.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": f"crm-phone|{owner}|{last10}"})
    row = db.execute(text("""
        SELECT id,primary_phone FROM boris_crm_contacts
         WHERE owner_user_id=:o
           AND RIGHT(regexp_replace(COALESCE(primary_phone,''),'\\D','','g'),10)=:p
         ORDER BY id LIMIT 1
    """), {"o": owner, "p": last10}).mappings().first()
    if row:
        if not row.get("primary_phone"):
            db.execute(text("UPDATE boris_crm_contacts SET primary_phone=:p,updated_at=NOW() WHERE id=:id"), {"p": phone, "id": row["id"]})
        return int(row["id"]), False
    cid = db.execute(text("""
        INSERT INTO boris_crm_contacts(owner_user_id,display_name,primary_phone,source,source_ref,status)
        VALUES(:o,'Клиент Avito',:p,'avito_calltracking',:r,'active') RETURNING id
    """), {"o": owner, "p": phone, "r": f"avito_phone:{account_id}:{last10}"}).scalar_one()
    return int(cid), True


def _open_deal(db, owner: int, account_id: str, contact_id: int):
    return db.execute(text("""
        SELECT id FROM boris_crm_deals
         WHERE owner_user_id=:o AND contact_id=:c AND avito_account_id=:a AND status='open'
         ORDER BY id DESC LIMIT 1
    """), {"o": owner, "c": contact_id, "a": account_id}).scalar()


def _create_call_deal(db, owner: int, account_id: str, contact_id: int, call: dict, missed: bool = False) -> int:
    pipeline_id = ensure_default_pipeline(db, owner)
    preferred_code = "new" if missed else "contacted"
    stage = db.execute(text("""
        SELECT id FROM boris_crm_stages
         WHERE pipeline_id=:p AND code=:code AND semantic_type='open'
         ORDER BY position,id LIMIT 1
    """), {"p": pipeline_id, "code": preferred_code}).scalar()
    if stage is None:
        stage = db.execute(text("SELECT id FROM boris_crm_stages WHERE pipeline_id=:p AND semantic_type='open' ORDER BY position,id LIMIT 1"), {"p": pipeline_id}).scalar()
    if stage is None:
        stage = db.execute(text("SELECT id FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id LIMIT 1"), {"p": pipeline_id}).scalar_one()
    item_id = call.get("itemId")
    title = ("Пропущенный звонок Avito" if missed else "Звонок с Avito") + (f" · объявление #{item_id}" if item_id else "")
    deal_id = db.execute(text("""
        INSERT INTO boris_crm_deals(
            owner_user_id,contact_id,pipeline_id,stage_id,title,source,source_ref,
            avito_account_id,avito_item_id,responsible_user_id,status,last_activity_at,updated_at
        ) VALUES(:o,:c,:p,:s,:t,'avito_calltracking',:r,:a,:item,:o,'open',:at,NOW()) RETURNING id
    """), {"o": owner, "c": contact_id, "p": pipeline_id, "s": int(stage), "t": title,
            "r": f"avito_call:{account_id}:{call.get('callId')}", "a": account_id,
            "item": str(item_id) if item_id is not None else None, "at": _dt(call.get("callTime"))}).scalar_one()
    return int(deal_id)


def _reconcile_prior_call_tasks(db, owner: int, deal_id: int | None, contact_id: int,
                                account_id: str, call: dict, missed: bool) -> list[dict]:
    """CALLTRACKING_NEXT_ACTION_SUPERSESSION_V1.

    A later answered Avito call is exact evidence that an old callback happened.
    It can also supersede only the generic "Зафиксировать следующий шаг" task
    after that task's deadline. Specific promises (send docs/address/invoice/etc.)
    are never auto-closed by a call because a call does not prove their outcome.
    """
    if missed or deal_id is None:
        return []
    happened=_dt(call.get("callTime"))
    call_id=str(call.get("callId") or "")
    if not call_id:
        return []
    rows=db.execute(text("""
      SELECT id,title,due_at,created_at
        FROM boris_crm_tasks
       WHERE owner_user_id=:o AND deal_id=:d AND contact_id=:c
         AND status='open' AND source='avito_calltracking'
         AND created_at<:happened
         AND title IN ('Перезвонить по пропущенному звонку Avito',
                       'Зафиксировать следующий шаг после звонка')
       ORDER BY created_at,id
    """),{"o":owner,"d":deal_id,"c":contact_id,"happened":happened}).mappings().all()
    changed=[]
    for r in rows:
        title=str(r.get("title") or "")
        if title=="Перезвонить по пропущенному звонку Avito":
            new_status="done"; activity_type="task_completed"
            reason="answered_call_after_callback_obligation"
        elif title=="Зафиксировать следующий шаг после звонка" and r.get("due_at") and r["due_at"]<happened:
            new_status="cancelled"; activity_type="task_cancelled"
            reason="superseded_by_later_answered_call"
        else:
            continue
        ref=f"calltracking_task_reconcile:{int(r['id'])}:{call_id}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),{"k":ref})
        updated=db.execute(text("""
          UPDATE boris_crm_tasks
             SET status=:status,completed_at=COALESCE(completed_at,now())
           WHERE id=:id AND owner_user_id=:o AND status='open'
           RETURNING id
        """),{"status":new_status,"id":int(r["id"]),"o":owner}).scalar()
        if updated is None:
            continue
        if not db.execute(text("SELECT 1 FROM boris_crm_activities WHERE source_ref=:r LIMIT 1"),{"r":ref}).first():
            db.execute(text("""
              INSERT INTO boris_crm_activities(
                owner_user_id,deal_id,contact_id,activity_type,channel,title,body,
                source,source_ref,actor_type,actor_id,metadata_json,created_at
              ) VALUES(
                :o,:d,:c,:activity,'crm',:title,:body,
                'calltracking_task_reconcile',:ref,'system','boris',CAST(:meta AS jsonb),now()
              )
            """),{
              "o":owner,"d":deal_id,"c":contact_id,"activity":activity_type,
              "title":"BORIS обновил задачу по новому звонку",
              "body":reason,"ref":ref,
              "meta":json.dumps({
                "task_id":int(r["id"]),"reason":reason,"call_id":call_id,
                "account_id":account_id,"call_time":happened.isoformat(),
                "external_action":False,"owner_action_required":False,
              },ensure_ascii=False),
            })
        changed.append({"task_id":int(r["id"]),"status":new_status,"reason":reason})
    if changed:
        db.execute(text("""
          UPDATE boris_crm_deals d SET next_action_at=(
            SELECT min(t.due_at) FROM boris_crm_tasks t
             WHERE t.deal_id=d.id AND t.status='open' AND t.due_at IS NOT NULL
          ),updated_at=now()
          WHERE d.id=:d AND d.owner_user_id=:o
        """),{"d":deal_id,"o":owner})
    return changed


def _ensure_task(db, owner: int, deal_id: int | None, contact_id: int, account_id: str, call: dict, missed: bool):
    happened = _dt(call.get("callTime"))
    if datetime.now(timezone.utc) - happened > timedelta(days=2):
        return None
    ref = f"avito_call_task:{account_id}:{call.get('callId')}"
    existing_task=db.execute(text("""
      SELECT id,deal_id,contact_id,assigned_user_id
      FROM boris_crm_tasks
      WHERE owner_user_id=:o AND description=:r
      ORDER BY id LIMIT 1
    """),{"o":owner,"r":ref}).mappings().first()
    if existing_task:
        # CALLTRACKING_ORPHAN_TASK_EXACT_LINK_REPAIR_V1:
        # legacy imports could create the exact call task before the call activity
        # had a canonical deal. Once the exact activity/deal exists, backfill only
        # by this exact account+call-id task identity; never guess by phone/title.
        if deal_id is not None and existing_task.get("deal_id") is None:
            responsible=db.execute(text(
                "SELECT responsible_user_id FROM boris_crm_deals WHERE id=:d AND owner_user_id=:o"
            ),{"d":deal_id,"o":owner}).scalar()
            db.execute(text("""
              UPDATE boris_crm_tasks
                 SET deal_id=:d,
                     contact_id=COALESCE(contact_id,:c),
                     assigned_user_id=COALESCE(assigned_user_id,:assigned)
               WHERE id=:id AND owner_user_id=:o AND deal_id IS NULL
            """),{
              "d":deal_id,"c":contact_id,
              "assigned":int(responsible) if responsible is not None else owner,
              "id":int(existing_task["id"]),"o":owner,
            })
            db.execute(text("""
              UPDATE boris_crm_deals d SET next_action_at=(
                SELECT min(t.due_at) FROM boris_crm_tasks t
                 WHERE t.deal_id=d.id AND t.status='open' AND t.due_at IS NOT NULL
              ),updated_at=now()
              WHERE d.id=:d AND d.owner_user_id=:o
            """),{"d":deal_id,"o":owner})
        return None
    if missed:
        # MISSED_CALL_TASK_CONTACT_COALESCE_V1: a burst of missed attempts from
        # the same buyer is one callback obligation, not N separate manager tasks.
        # Keep call activities per call for analytics, but coalesce the open CRM
        # callback task on the same deal/contact. This prevents queue explosions
        # while preserving every call event.
        _existing_callback=db.execute(text("""SELECT id FROM boris_crm_tasks
          WHERE owner_user_id=:o AND deal_id=:d AND contact_id=:c AND status='open'
            AND source='avito_calltracking' AND title='Перезвонить по пропущенному звонку Avito'
          ORDER BY due_at NULLS LAST,id LIMIT 1"""),{'o':owner,'d':deal_id,'c':contact_id}).scalar()
        if _existing_callback is not None:
            return None
        title = "Перезвонить по пропущенному звонку Avito"
        due = max(datetime.now(timezone.utc) + timedelta(minutes=15), happened + timedelta(minutes=15))
    else:
        if db.execute(text("SELECT 1 FROM boris_crm_tasks WHERE owner_user_id=:o AND deal_id=:d AND status='open' LIMIT 1"), {"o": owner, "d": deal_id}).first():
            return None
        title = "Зафиксировать следующий шаг после звонка"
        due = datetime.now(timezone.utc) + timedelta(hours=2)
    assigned = owner
    if deal_id is not None:
        responsible = db.execute(text("SELECT responsible_user_id FROM boris_crm_deals WHERE id=:d AND owner_user_id=:o"), {"d": deal_id, "o": owner}).scalar()
        if responsible is not None:
            assigned = int(responsible)
    tid = db.execute(text("""
        INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,due_at,assigned_user_id,status,source)
        VALUES(:o,:d,:c,:t,:r,:due,:assigned,'open','avito_calltracking') RETURNING id
    """), {"o": owner, "d": deal_id, "c": contact_id, "t": title, "r": ref, "due": due, "assigned": assigned}).scalar_one()
    if deal_id is not None:
        db.execute(text("""
            UPDATE boris_crm_deals d SET next_action_at=(SELECT MIN(t.due_at) FROM boris_crm_tasks t
              WHERE t.deal_id=d.id AND t.owner_user_id=:o AND t.status='open' AND t.due_at IS NOT NULL),updated_at=NOW()
             WHERE d.id=:d
        """), {"o": owner, "d": deal_id})
    return int(tid)


def _call_analysis(db, account_id: str, call_id: object) -> dict:
    try:
        row = db.execute(text("SELECT transcript,analysis FROM call_analysis WHERE account_id=:a AND call_id=:c LIMIT 1"), {"a": account_id, "c": int(call_id)}).mappings().first()
    except Exception:
        return {}
    if not row:
        return {}
    analysis = row.get("analysis") or {}
    if isinstance(analysis, str):
        try: analysis = json.loads(analysis)
        except Exception: analysis = {}
    return {"transcript": str(row.get("transcript") or "").strip(), "analysis": analysis if isinstance(analysis, dict) else {}}


def _sync_analysis_into_crm(db, owner: int, account_id: str, call_id: object, activity_id: int, contact_id: int, deal_id: int | None):
    ca = _call_analysis(db, account_id, call_id)
    if not ca:
        return None
    transcript = ca.get("transcript") or ""
    analysis = ca.get("analysis") or {}
    summary = str(analysis.get("recommendation") or "").strip()
    client_name = str(analysis.get("client_name") or "").strip()
    agreement = analysis.get("agreement") if isinstance(analysis.get("agreement"), dict) else {}
    if client_name and client_name.lower() not in {"клиент", "неизвестно", "нет"}:
        db.execute(text("UPDATE boris_crm_contacts SET display_name=CASE WHEN display_name='Клиент Avito' OR display_name IS NULL OR TRIM(display_name)='' THEN :n ELSE display_name END,updated_at=NOW() WHERE id=:c AND owner_user_id=:o"), {"n": client_name[:200], "c": contact_id, "o": owner})
    meta = {"analysis_available": True, "transcript": transcript[:12000], "call_summary": summary[:2000]}
    if agreement and agreement.get("exists"):
        meta["agreement"] = agreement
    db.execute(text("""
        UPDATE boris_crm_activities
           SET metadata_json = COALESCE(metadata_json,'{}'::jsonb) || CAST(:m AS jsonb),
               body = CASE WHEN :summary='' OR body LIKE '%Итог:%' THEN body ELSE body || E'\\nИтог: ' || :summary END
         WHERE id=:id AND owner_user_id=:o
    """), {"id": activity_id, "o": owner, "summary": summary[:1000], "m": json.dumps(meta, ensure_ascii=False)})
    if deal_id is not None and agreement and agreement.get("exists") and float(agreement.get("confidence") or 0) >= 0.75:
        raw_dt = str(agreement.get("datetime_local") or "").strip()
        if raw_dt:
            try:
                from zoneinfo import ZoneInfo
                tz_name = str(agreement.get("timezone") or "Europe/Moscow")
                due = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                if due.tzinfo is None:
                    due = due.replace(tzinfo=ZoneInfo(tz_name))
                due = due.astimezone(timezone.utc)
                if due > datetime.now(timezone.utc) - timedelta(minutes=5):
                    ref = f"call_agreement:{account_id}:{call_id}:{due.isoformat()}"
                    exists = db.execute(text("SELECT 1 FROM boris_crm_tasks WHERE owner_user_id=:o AND description=:r LIMIT 1"), {"o": owner, "r": ref}).first()
                    if not exists:
                        assigned = db.execute(text("SELECT COALESCE(responsible_user_id,:o) FROM boris_crm_deals WHERE id=:d AND owner_user_id=:o"), {"d": deal_id, "o": owner}).scalar() or owner
                        title = str(agreement.get("text") or agreement.get("type") or "Договорённость по звонку")[:500]
                        db.execute(text("INSERT INTO boris_crm_tasks(owner_user_id,deal_id,contact_id,title,description,due_at,assigned_user_id,status,source) VALUES(:o,:d,:c,:t,:r,:due,:assigned,'open','call_agreement')"), {"o": owner, "d": deal_id, "c": contact_id, "t": title, "r": ref, "due": due, "assigned": int(assigned)})
                        db.execute(text("UPDATE boris_crm_deals SET next_action_at=(SELECT MIN(due_at) FROM boris_crm_tasks WHERE deal_id=:d AND owner_user_id=:o AND status='open' AND due_at IS NOT NULL),updated_at=NOW() WHERE id=:d AND owner_user_id=:o"), {"d": deal_id, "o": owner})
            except Exception:
                pass
    return ca

def ingest_call(db, owner: int, account_id: str, call: dict) -> dict:
    call_id = call.get("callId")
    phone = _phone(call.get("buyerPhone"))
    if not call_id or not phone:
        return {"status": "skipped", "reason": "missing_call_id_or_phone"}
    source_ref = f"avito_call:{account_id}:{call_id}"
    existing = db.execute(text("SELECT id,deal_id,contact_id FROM boris_crm_activities WHERE owner_user_id=:o AND source_ref=:r LIMIT 1"), {"o": owner, "r": source_ref}).mappings().first()
    talk = int(call.get("talkDuration") or 0)
    missed = talk <= 0
    if existing:
        # Keep the call snapshot fresh on every idempotent sync. The Avito API
        # may expose final talkDuration a little later than the first poll; an
        # early 0-second snapshot must not stay forever as a false missed call.
        fresh_metadata = {
            "account_id": account_id,
            "call_id": call_id,
            "buyer_phone": phone,
            "seller_phone": call.get("sellerPhone"),
            "virtual_phone": call.get("virtualPhone"),
            "item_id": call.get("itemId"),
            "call_time": call.get("callTime"),
            "talk_duration": talk,
            "waiting_duration": call.get("waitingDuration"),
            "missed": missed,
        }
        db.execute(text("""
            UPDATE boris_crm_activities
               SET title=:t,body=:b,metadata_json=CAST(:m AS jsonb),created_at=:at
             WHERE id=:id AND owner_user_id=:o
        """), {
            "t": "Пропущенный звонок Avito" if missed else "Входящий звонок Avito",
            "b": f"{phone} · " + ("пропущен" if missed else f"разговор {talk // 60}:{talk % 60:02d}"),
            "m": json.dumps(fresh_metadata, ensure_ascii=False), "at": _dt(call.get("callTime")),
            "id": int(existing["id"]), "o": owner,
        })
        deal_id = int(existing["deal_id"]) if existing["deal_id"] is not None else _open_deal(db, owner, account_id, int(existing["contact_id"]))
        deal_created = False
        if deal_id is None:
            deal_id = _create_call_deal(db, owner, account_id, int(existing["contact_id"]), call, missed=missed)
            deal_created = True
        if existing["deal_id"] is None:
            db.execute(text("UPDATE boris_crm_activities SET deal_id=:d WHERE id=:id AND owner_user_id=:o"), {"d": deal_id, "id": int(existing["id"]), "o": owner})
        # Refresh first, then merge canonical analysis into the refreshed call.
        # This preserves transcript/summary/agreement fields instead of wiping
        # them with the delayed Avito snapshot on every idempotent poll.
        _sync_analysis_into_crm(db, owner, account_id, call_id, int(existing["id"]), int(existing["contact_id"]), int(deal_id))
        _reconcile_prior_call_tasks(db, owner, int(deal_id), int(existing["contact_id"]), account_id, call, missed)
        _ensure_task(db, owner, int(deal_id), int(existing["contact_id"]), account_id, call, missed)
        return {"status": "existing", "activity_id": int(existing["id"]), "deal_id": int(deal_id), "contact_id": existing["contact_id"], "deal_created": deal_created}
    contact_id, contact_created = _contact(db, owner, account_id, phone)
    deal_id = _open_deal(db, owner, account_id, contact_id)
    deal_created = False
    if deal_id is None:
        deal_id = _create_call_deal(db, owner, account_id, contact_id, call, missed=missed)
        deal_created = True
    metadata = {
        "account_id": account_id,
        "call_id": call_id,
        "buyer_phone": phone,
        "seller_phone": call.get("sellerPhone"),
        "virtual_phone": call.get("virtualPhone"),
        "item_id": call.get("itemId"),
        "call_time": call.get("callTime"),
        "talk_duration": talk,
        "waiting_duration": call.get("waitingDuration"),
        "missed": missed,
    }
    analyzed = db.execute(text("SELECT 1 FROM call_analysis WHERE account_id=:a AND call_id=:c LIMIT 1"), {"a": account_id, "c": int(call_id)}).first()
    metadata["analysis_available"] = bool(analyzed)
    title = "Пропущенный звонок Avito" if missed else "Входящий звонок Avito"
    body = f"{phone} · " + ("пропущен" if missed else f"разговор {talk // 60}:{talk % 60:02d}")
    activity_id = db.execute(text("""
        INSERT INTO boris_crm_activities(
            owner_user_id,deal_id,contact_id,activity_type,channel,direction,title,body,
            source,source_ref,actor_type,metadata_json,created_at
        ) VALUES(:o,:d,:c,'call','avito_calltracking','in',:t,:b,'avito_calltracking',:r,'system',CAST(:m AS jsonb),:at)
        RETURNING id
    """), {"o": owner, "d": deal_id, "c": contact_id, "t": title, "b": body, "r": source_ref,
            "m": json.dumps(metadata, ensure_ascii=False), "at": _dt(call.get("callTime"))}).scalar_one()
    _sync_analysis_into_crm(db, owner, account_id, call_id, int(activity_id), contact_id, int(deal_id) if deal_id is not None else None)
    if deal_id is not None:
        db.execute(text("UPDATE boris_crm_deals SET last_activity_at=GREATEST(COALESCE(last_activity_at,:at),:at),updated_at=NOW() WHERE id=:d"), {"at": _dt(call.get("callTime")), "d": deal_id})
    _reconcile_prior_call_tasks(db, owner, int(deal_id) if deal_id is not None else None, contact_id, account_id, call, missed)
    task_id = _ensure_task(db, owner, int(deal_id) if deal_id is not None else None, contact_id, account_id, call, missed)
    return {"status": "created", "activity_id": int(activity_id), "contact_id": contact_id, "contact_created": contact_created,
            "deal_id": int(deal_id) if deal_id is not None else None, "deal_created": deal_created, "task_id": task_id, "missed": missed}



def _canonical_account_id(account_id: str) -> str:
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT owner_user_id,avito_user_id FROM accounts WHERE account_id=:a LIMIT 1"), {"a": account_id}).mappings().first()
        if not row or row.get("owner_user_id") is None or not row.get("avito_user_id"):
            return account_id
        canonical = db.execute(text("""
            SELECT account_id FROM accounts
             WHERE owner_user_id=:o AND avito_user_id=:u
               AND NULLIF(TRIM(avito_client_id),'') IS NOT NULL
               AND NULLIF(TRIM(avito_client_secret),'') IS NOT NULL
             ORDER BY created_at ASC,id ASC LIMIT 1
        """), {"o": int(row["owner_user_id"]), "u": str(row["avito_user_id"])}).scalar()
        return str(canonical or account_id)
    finally:
        db.close()

def sync_account(account_id: str, days: int = 7) -> dict:
    requested_account_id = account_id
    account_id = _canonical_account_id(account_id)
    token, err = _ct_token(account_id)
    if err:
        return {"status": "no_access", "message": err}
    now = datetime.now(timezone.utc)
    code, data = _get_calls(token, (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if code != 200:
        return {"status": "error", "code": code}
    db = SessionLocal()
    out = []
    try:
        owner = db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"), {"a": account_id}).scalar()
        if owner is None:
            return {"status": "no_owner"}
        for wrapper in (data.get("calls") or []):
            call = wrapper.get("call") or wrapper
            out.append(ingest_call(db, int(owner), account_id, call))
        db.commit()
    except Exception:
        db.rollback(); raise
    finally:
        db.close()
    return {"status": "ok", "account_id": account_id, "requested_account_id": requested_account_id, "canonicalized": requested_account_id != account_id, "calls": len(out), "created": sum(1 for x in out if x.get("status") == "created"), "items": out}


def sync_one_due_account() -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT a.account_id FROM accounts a
             WHERE a.owner_user_id IS NOT NULL
               AND NULLIF(TRIM(a.avito_client_id),'') IS NOT NULL
               AND NULLIF(TRIM(a.avito_client_secret),'') IS NOT NULL
               AND a.account_id NOT LIKE 'qa_%'
               AND NOT EXISTS (
                   SELECT 1 FROM accounts older
                    WHERE older.owner_user_id=a.owner_user_id
                      AND older.avito_user_id=a.avito_user_id
                      AND older.avito_user_id IS NOT NULL
                      AND (older.created_at<a.created_at OR (older.created_at=a.created_at AND older.id<a.id))
                      AND NULLIF(TRIM(older.avito_client_id),'') IS NOT NULL
                      AND NULLIF(TRIM(older.avito_client_secret),'') IS NOT NULL
               )
             ORDER BY a.account_id
        """)).scalars().all()
    finally:
        db.close()
    if not rows:
        return {"status": "no_accounts"}
    idx = int(time.time() // 5) % len(rows)
    account_id = str(rows[idx])
    result = sync_account(account_id, 7)
    result["account_id"] = account_id
    return result


if __name__ == "__main__":
    print(json.dumps(sync_one_due_account(), ensure_ascii=False, default=str), flush=True)


def send_due_client_agreement_reminders(now: datetime | None = None, sender=None) -> dict:
    """Send an Avito-chat reminder ~3h before a call agreement when the same contact has a real Avito conversation.
    No SMS/phone fallback is invented: without a chat, only the internal CRM task remains.
    """
    now = now or datetime.now(timezone.utc)
    db = SessionLocal(); sent=0; skipped_no_chat=0; candidates=0
    try:
        rows=db.execute(text("""
          SELECT t.id,t.owner_user_id,t.deal_id,t.contact_id,t.title,t.due_at,d.avito_account_id
            FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id AND d.owner_user_id=t.owner_user_id
           WHERE t.status='open' AND t.source='call_agreement'
             AND t.due_at > :lo AND t.due_at <= :hi
           ORDER BY t.due_at,t.id LIMIT 50
        """),{'lo':now+timedelta(hours=2,minutes=30),'hi':now+timedelta(hours=3,minutes=30)}).mappings().all()
        candidates=len(rows)
        for r in rows:
            ref=f"call_agreement_client_reminder:{r['id']}"
            if db.execute(text("select 1 from boris_crm_activities where owner_user_id=:o and source_ref=:r limit 1"),{'o':r['owner_user_id'],'r':ref}).first(): continue
            cv=db.execute(text("""select external_chat_id from boris_crm_conversations where owner_user_id=:o and contact_id=:c and account_id=:a and channel='avito' order by coalesce(last_message_at,updated_at) desc,id desc limit 1"""),{'o':r['owner_user_id'],'c':r['contact_id'],'a':r['avito_account_id']}).mappings().first()
            if not cv or not cv.get('external_chat_id'):
                skipped_no_chat+=1; continue
            local=r['due_at'].astimezone(timezone(timedelta(hours=3)))
            msg=f"Напоминаем: сегодня в {local.strftime('%H:%M')} — {str(r['title']).strip()}. Если планы изменились, напишите нам, пожалуйста."
            if sender is None:
                from app.api.messenger import send_message
                result=send_message(str(r['avito_account_id']),str(cv['external_chat_id']),msg)
                if isinstance(result,dict) and result.get('error'): continue
            else:
                sender(str(r['avito_account_id']),str(cv['external_chat_id']),msg)
            db.execute(text("""insert into boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,channel,direction,title,body,source,source_ref,actor_type,created_at) values(:o,:d,:c,'reminder','avito','out','Напоминание клиенту о договорённости',:b,'call_agreement_reminder',:r,'system',NOW())"""),{'o':r['owner_user_id'],'d':r['deal_id'],'c':r['contact_id'],'b':msg,'r':ref})
            db.commit(); sent+=1
        return {'status':'ok','candidates':candidates,'sent':sent,'skipped_no_chat':skipped_no_chat}
    except Exception:
        db.rollback(); raise
    finally: db.close()
