from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="/api/mop-combat",
    tags=["mop-combat"],
)


MOP_TRAINING_RUNTIME_CONTRACT = "v4-ai-provider-readiness"


@router.get("/runtime-contract")
def mop_training_runtime_contract(account_id: str = Query(default="mbroker_27859")):
    """Read-only runtime fingerprint used by Guardian across both API replicas.

    The AI readiness probe never generates text and never calls a paid/remote
    model. It only evaluates provider circuits/quota fences and, when local is
    the sole candidate, checks production pressure + the local Ollama daemon.
    """
    try:
        from app.services.sales_ai_router import readiness as _sales_readiness
        ai_readiness = _sales_readiness(str(account_id or "mbroker_27859"))
    except Exception as exc:
        ai_readiness = {
            "ready": False,
            "order": [],
            "ready_providers": [],
            "reason": "readiness_probe_failed:" + type(exc).__name__,
        }
    return {
        "status": "ok",
        "contract": MOP_TRAINING_RUNTIME_CONTRACT,
        "price_followup_guard": True,
        "manager_turn_counter": True,
        "ai_readiness_probe": True,
        "ai_ready": bool(ai_readiness.get("ready")),
        "ai_readiness": ai_readiness,
    }


STORE_ROOT = Path(
    os.environ.get(
        "BORIS_MOP_COMBAT_STORE",
        "/root/BORIS/backend/data/mop_combat_training",
    )
)


class TrainingCreate(BaseModel):
    account_id: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    customer_message: str = Field(min_length=1)
    mop_answer: str = ""
    score: int = Field(default=0, ge=0, le=10)
    error: str = ""
    ideal_answer: str = ""
    comment: str = ""


class TrainingPatch(BaseModel):
    score: Optional[int] = Field(default=None, ge=0, le=10)
    error: Optional[str] = None
    ideal_answer: Optional[str] = None
    comment: Optional[str] = None
    mop_answer: Optional[str] = None


def _safe_account(account_id: str) -> str:
    value = str(account_id or "").strip()

    if not value:
        raise HTTPException(
            status_code=400,
            detail="account_id обязателен",
        )

    if len(value) > 160:
        raise HTTPException(
            status_code=400,
            detail="Некорректный account_id",
        )

    allowed = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "_-."
    )

    if any(ch not in allowed for ch in value):
        raise HTTPException(
            status_code=400,
            detail="Некорректный account_id",
        )

    return value


def _path(account_id: str) -> Path:
    account = _safe_account(account_id)
    return STORE_ROOT / f"{account}.json"


def _load(account_id: str) -> list[dict[str, Any]]:
    path = _path(account_id)

    if not path.exists():
        return []

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"MOP_TRAINING_STORE_READ_FAILED: {exc}",
        )

    if not isinstance(data, list):
        raise HTTPException(
            status_code=500,
            detail="MOP_TRAINING_STORE_INVALID",
        )

    return data


def _save(account_id: str, rows: list[dict[str, Any]]) -> None:
    path = _path(account_id)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, tmp = tempfile.mkstemp(
        prefix=".mop-training-",
        suffix=".json",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                rows,
                fh,
                ensure_ascii=False,
                indent=2,
            )
            fh.write("\n")

        os.replace(
            tmp,
            path,
        )
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


@router.get("/history")
def history(
    account_id: str = Query(...),
    limit: int = Query(
        50,
        ge=1,
        le=200,
    ),
):
    rows = _load(account_id)

    return {
        "status": "ok",
        "account_id": account_id,
        "total": len(rows),
        "items": rows[-limit:][::-1],
    }


@router.post("/training")
def create_training(
    body: TrainingCreate,
):
    account_id = _safe_account(
        body.account_id
    )

    rows = _load(account_id)

    item = {
        "id": (
            f"mop_{int(time.time() * 1000000)}"
        ),
        "account_id": account_id,
        "scenario": body.scenario.strip(),
        "customer_message": (
            body.customer_message.strip()
        ),
        "mop_answer": body.mop_answer.strip(),
        "score": body.score,
        "error": body.error.strip(),
        "ideal_answer": (
            body.ideal_answer.strip()
        ),
        "comment": body.comment.strip(),
        "created_at": int(time.time()),
        "source": "human_combat_training",
        "approved_by_human": True,
    }

    rows.append(item)
    _save(
        account_id,
        rows,
    )

    return {
        "status": "ok",
        "training": item,
    }


@router.patch("/training/{training_id}")
def patch_training(
    training_id: str,
    body: TrainingPatch,
    account_id: str = Query(...),
):
    rows = _load(account_id)

    for row in rows:
        if str(row.get("id")) != str(
            training_id
        ):
            continue

        if body.score is not None:
            row["score"] = body.score

        if body.error is not None:
            row["error"] = body.error.strip()

        if body.ideal_answer is not None:
            row["ideal_answer"] = (
                body.ideal_answer.strip()
            )

        if body.comment is not None:
            row["comment"] = (
                body.comment.strip()
            )

        if body.mop_answer is not None:
            row["mop_answer"] = (
                body.mop_answer.strip()
            )

        row["updated_at"] = int(
            time.time()
        )

        _save(
            account_id,
            rows,
        )

        return {
            "status": "ok",
            "training": row,
        }

    raise HTTPException(
        status_code=404,
        detail="Training example not found",
    )


@router.get("/summary")
def summary(
    account_id: str = Query(...),
):
    rows = _load(account_id)

    if not rows:
        return {
            "status": "ok",
            "total": 0,
            "average_score": 0,
            "approved": 0,
        }

    scores = [
        int(row.get("score", 0))
        for row in rows
    ]

    return {
        "status": "ok",
        "total": len(rows),
        "average_score": round(
            sum(scores) / len(scores),
            2,
        ),
        "approved": sum(
            1
            for row in rows
            if row.get(
                "approved_by_human"
            )
        ),
    }


# BORIS_MOP_REAL_SPARRING_V2 — existing combat-training store + production MOP generation.
class SparringStart(BaseModel):
    account_id: str = Field(min_length=1)
    business_goal: str = ""

class SparringTurn(BaseModel):
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)

class SparringFeedback(BaseModel):
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    score: int = Field(ge=1, le=10)
    comment: str = Field(min_length=8)
    flags: list[str] = []

class SparringLearning(BaseModel):
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    action: str = Field(pattern="^(remember|edit|reject)$")
    rule_text: str = ""

class SparringFinish(BaseModel):
    account_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


def _require_active_mop_training(account_id: str) -> None:
    """Universal rule: Combat/Training exists automatically for every active MOP account."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    db = SessionLocal()
    try:
        bound = bool(db.execute(sql_text("SELECT 1 FROM ai_bindings WHERE product='mop' AND account_id=:a LIMIT 1"), {"a": account_id}).scalar())
        if not bound:
            raise HTTPException(status_code=409, detail="МОП не подключён к этому аккаунту")
        try:
            from app.api.messenger import get_manager_balance
            package_active = bool((get_manager_balance(account_id) or {}).get("active"))
        except Exception:
            package_active = False
        raw = db.execute(sql_text("SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"), {"a": account_id}).scalar()
        try:
            cfg = json.loads(raw) if raw else {}
        except Exception:
            cfg = {}
        owner_enabled = bool(cfg.get("mop_enabled", cfg.get("enabled", True))) if isinstance(cfg, dict) else True
        if not package_active or not owner_enabled:
            raise HTTPException(status_code=409, detail="МОП подключён, но пакет или режим МОП сейчас выключен")
    finally:
        db.close()


def _heal_stale_sparring_sessions(account_id: str, rows: list[dict[str, Any]], now: int | None = None) -> int:
    """Close stale training sessions without deleting their history."""
    now = int(now or time.time())
    try:
        stale_after = max(
            900,
            int(os.environ.get("BORIS_MOP_SPARRING_STALE_SECONDS", "7200") or 7200),
        )
    except Exception:
        stale_after = 7200

    changed = 0
    for row in rows:
        if row.get("kind") != "sparring_session" or row.get("status") != "active":
            continue
        try:
            updated = int(row.get("updated_at") or row.get("created_at") or 0)
        except Exception:
            updated = 0
        if updated <= 0 or now - updated < stale_after:
            continue
        row["status"] = "abandoned"
        row["abandoned_reason"] = "stale_session_auto_heal"
        row["abandoned_at"] = now
        row["updated_at"] = now
        changed += 1

    if changed:
        _save(account_id, rows)
    return changed


def _spar(account_id: str, session_id: str):
    rows = _load(account_id)
    _heal_stale_sparring_sessions(account_id, rows)
    state = next((r for r in rows if r.get("kind") == "sparring_session" and r.get("session_id") == session_id), None)
    if not state: raise HTTPException(status_code=404, detail="sparring session not found")
    return rows, state


def _instruction_version(account_id: str) -> str:
    import hashlib
    from app.db.session import SessionLocal
    from app.models.messenger_prompt import MessengerPrompt
    db=SessionLocal()
    try:
        ps=db.query(MessengerPrompt).filter(MessengerPrompt.account_id==account_id,MessengerPrompt.is_active==True).order_by(MessengerPrompt.id).all()
        raw="\n".join(f"{p.id}:{p.updated_at}:{p.custom_instructions or ''}" for p in ps)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    finally: db.close()


def _proposal(comment: str, flags: list[str]) -> str:
    c=" ".join(comment.strip().split())
    if c[-1:] not in ".!?": c+="."
    quality=(" Учесть признаки: "+", ".join(flags[:4])+".") if flags else ""
    return ("Правило для МОПа: "+c+quality)[:1800]


def _sparring_context(account_id: str) -> dict[str, Any]:
    """Small persistent snapshot from existing account/CRM/memory stores; no new source of truth."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    db=SessionLocal()
    try:
        acc=db.execute(sql_text("SELECT client_goal,client_goal_text,company_niche,company_tone FROM accounts WHERE account_id=:a"),{"a":account_id}).mappings().first() or {}
        facts=db.execute(sql_text("SELECT count(*) FROM client_facts WHERE account_id=:a AND status='confirmed'"),{"a":account_id}).scalar() or 0
        leads=db.execute(sql_text("SELECT count(*) FROM messenger_leads WHERE account_id=:a"),{"a":account_id}).scalar() or 0
        return {"dialog_goal":acc.get("client_goal_text") or acc.get("client_goal") or "","company_niche":acc.get("company_niche") or "","company_tone":acc.get("company_tone") or "","confirmed_memory_facts":int(facts),"crm_leads":int(leads)}
    finally: db.close()


@router.post("/sparring/start")
def sparring_start(body: SparringStart):
    account_id=_safe_account(body.account_id); _require_active_mop_training(account_id); rows=_load(account_id); now=int(time.time())
    stale_sessions_healed=_heal_stale_sparring_sessions(account_id,rows,now)
    sid=f"spar_{int(time.time()*1000000)}"
    state={"kind":"sparring_session","session_id":sid,"account_id":account_id,"business_goal":body.business_goal.strip(),"status":"active","created_at":now,"updated_at":now,"instruction_version":_instruction_version(account_id),"crm_client_context":_sparring_context(account_id),"messages":[],"feedback":[],"manager_turns":0,"review":None}
    rows.append(state); _save(account_id,rows)
    return {"status":"ok","training":state,"training_mode":True,"avito_send":False,"stale_sessions_healed":stale_sessions_healed}


@router.get("/sparring/{session_id}")
def sparring_get(session_id: str, account_id: str=Query(...)):
    _,state=_spar(_safe_account(account_id),session_id); return {"status":"ok","training":state}

def _sparring_review(state: dict[str, Any]) -> dict[str, Any]:
    """Fast deterministic review for the live MOP combat session.

    Combat training already spends AI on each real MOP reply. Finishing must be
    instant/idempotent and must not trigger another paid generation merely to
    render the UI. The review therefore summarizes observable dialogue facts.
    """
    messages = list(state.get("messages") or [])
    mop_messages = [str(m.get("text") or "").strip() for m in messages if m.get("role") == "mop"]
    client_messages = [str(m.get("text") or "").strip() for m in messages if m.get("role") == "client"]
    low_mop = "\n".join(mop_messages).lower().replace("ё", "е")
    low_client = "\n".join(client_messages).lower().replace("ё", "е")

    duplicate_replies = sum(
        1 for i in range(1, len(mop_messages))
        if mop_messages[i].strip().lower() == mop_messages[i - 1].strip().lower()
    )
    from app.api.messenger import _mop_phone_known as _review_phone_known
    phone_received = _review_phone_known(low_client)
    next_step = bool(__import__("re").search(
        r"(?:созвон|звон|телефон|контакт|свяж|обсуд|уточн|замер)",
        low_mop,
    ))
    unsafe_promise = bool(__import__("re").search(
        r"(?:точно\s+одоб|гарант|свяж(?:усь|емся)\s+(?:сегодня|завтра|вечером|утром)|"
        r"заполн\w*.{0,50}(?:заявк\w*|форм\w*).{0,50}(?:сайт\w*|онлайн))",
        low_mop,
    ))

    score = 70
    if mop_messages:
        score += 8
    if len(mop_messages) >= 3:
        score += 5
    if next_step:
        score += 7
    if phone_received:
        score += 10
    score -= duplicate_replies * 18
    if unsafe_promise:
        score -= 20
    score = max(0, min(100, score))

    understood = []
    missed = []
    if mop_messages:
        understood.append("МОП поддержал диалог и отвечал на сообщения клиента.")
    if next_step:
        understood.append("МОП вёл разговор к следующему шагу.")
    if phone_received:
        understood.append("Контакт клиента получен — повторно запрашивать номер не нужно.")
    if duplicate_replies:
        missed.append("МОП повторил одинаковую реплику вместо продолжения уже уточнённого диалога.")
    if unsafe_promise:
        missed.append("В диалоге есть неподтверждённое обещание или действие, которое нельзя сообщать клиенту без факта.")
    if not missed:
        missed.append("Критичных повторов или неподтверждённых обещаний в этом диалоге не обнаружено.")

    main_error = (
        "Не повторять одинаковое уточнение после того, как клиент уже конкретизировал запрос."
        if duplicate_replies
        else ("Не обещать неподтверждённые условия или действия." if unsafe_promise else "")
    )
    summary = (
        f"Диалог завершён. Ответов МОП: {len(mop_messages)}. "
        + ("Контакт клиента получен. " if phone_received else "")
        + ("Есть повтор ответа — это нужно исправить в следующем диалоге." if duplicate_replies else "Повторяющихся подряд ответов не обнаружено.")
    )

    return {
        "score": score,
        "result": "progress" if mop_messages else "neutral",
        "manager_level": "strong" if score >= 85 else ("middle" if score >= 65 else "junior"),
        "summary": summary,
        "what_manager_understood": understood,
        "what_manager_missed": missed,
        "main_thinking_error": main_error,
        "better_thinking": "Отвечать на последний уточнённый запрос клиента и двигать разговор вперёд, используя уже полученную информацию.",
        "example_better_reply": "",
        "next_training_focus": "Не повторять уже заданные вопросы и не придумывать коммерческие условия.",
        "coach_message": "Разбор построен по фактическому диалогу этой тренировки.",
    }


@router.post("/sparring/finish")
def sparring_finish(body: SparringFinish):
    account_id=_safe_account(body.account_id); rows,state=_spar(account_id,body.session_id)
    state["manager_turns"] = sum(1 for m in state.get("messages", []) if m.get("role") == "mop")
    if not state.get("review"):
        state["review"] = _sparring_review(state)
    state["status"]="finished"
    state["updated_at"]=int(time.time())
    _save(account_id,rows)
    return {"status":"ok","training":state,"avito_send":False}


@router.post("/sparring/turn")
def sparring_turn(body: SparringTurn):
    account_id=_safe_account(body.account_id); rows,state=_spar(account_id,body.session_id)
    if state.get("status")!="active": raise HTTPException(status_code=409,detail="sparring session not active")
    now=int(time.time()); state["messages"].append({"id":f"h_{now}_{len(state['messages'])}","role":"client","text":body.message.strip(),"at":now})
    from app.api.messenger import (
        generate_ai_draft_reply,
        _mop_output_policy_violations,
        _mop_measurement_intent,
        _mop_measurement_repair_reply,
        _mop_text_has_phone,
        _mop_phone_known,
        _mop_phone_handoff_enabled_for_account,
        _mop_account_next_question,
        _mop_client_meta_nonanswer,
        _mop_meta_nonanswer_reply,
    )
    training_chat={
        "id":f"training:{state['session_id']}",
        "_training_messages":[{"role":m.get("role"),"text":m.get("text") or ""} for m in state["messages"]],
        "_training_goal":state.get("business_goal") or "",
        "_training_instruction":state.get("session_feedback_context") or "",
        "last_message":{"direction":"in","text":body.message.strip()},
        "context":{},
    }
    # MOP_FIRST_CONTACT_FAST_STAGE_V1:
    # The trainer knows exactly whether this is the first manager turn, so do
    # not spend AI time on a greeting/company-intro that is already present in
    # configured company truth and the account sales cycle.
    generated=None
    _has_previous_mop=any(
        m.get("role")=="mop"
        for m in state.get("messages", [])[:-1]
    )

    # MOP_TRAINING_PHONE_HANDOFF_PRIORITY_V1:
    # Training must obey the same account handoff policy as live Messenger.
    # A real phone on the current client turn has higher priority than the
    # trainer's first-contact / sales-cycle fast guards. Delegate to the
    # canonical generator so policy, wording and zero-AI accounting stay equal.
    _phone_handoff_turn=False
    try:
        _phone_handoff_turn=bool(
            _mop_phone_known(body.message.strip())
            and _mop_phone_handoff_enabled_for_account(account_id)
        )
    except Exception:
        _phone_handoff_turn=False
    if _phone_handoff_turn:
        generated=generate_ai_draft_reply(
            account_id,
            training_chat,
            return_meta=True,
            idempotency_key=f"mop-training-phone:{account_id}:{state['session_id']}:{len(state['messages'])}",
        )

    if generated is None and not _has_previous_mop:
        try:
            from app.db.session import SessionLocal as _FirstContactSession
            from app.api.client_memory import _first_contact_sales_reply
            _fcdb=_FirstContactSession()
            try:
                _fc=_first_contact_sales_reply(
                    _fcdb, account_id, body.message.strip(), force=True
                )
            finally:
                _fcdb.close()
            if _fc:
                generated={
                    "text":str(_fc.get("answer") or ""),
                    "usage":{
                        "provider":"deterministic_guard",
                        "model":"first_contact_sales_stage_v1",
                        "prompt_tokens":0,
                        "completion_tokens":0,
                        "total_tokens":0,
                        "cost_rub":0.0,
                        "memory_fast_path":True,
                        "policy_version":"MOP_FIRST_CONTACT_SALES_STAGE_V1",
                    },
                }
        except Exception as _fc_exc:
            print("MOP_FIRST_CONTACT_FAST_STAGE_DEFERRED %s: %s" % (
                account_id, str(_fc_exc)[:160]
            ), flush=True)

    if (
        generated is None
        and _has_previous_mop
        and _mop_client_meta_nonanswer(body.message.strip())
    ):
        _meta_history="\n".join(
            ("Клиент: " if m.get("role")=="client" else "Мы: ")
            + str(m.get("text") or "")
            for m in state.get("messages", [])
            if m.get("role") in {"client","mop"}
        )
        generated={
            "text":_mop_meta_nonanswer_reply(_meta_history),
            "usage":{
                "provider":"deterministic_guard",
                "model":"meta_nonanswer_repair_v1",
                "prompt_tokens":0,
                "completion_tokens":0,
                "total_tokens":0,
                "cost_rub":0.0,
                "memory_fast_path":True,
                "policy_version":"MOP_META_NONANSWER_CONTINUITY_V1",
            },
        }

    if generated is None and _has_previous_mop:
        # MOP_TRAINING_SALES_CYCLE_FAST_STAGE_V1:
        # A short answer to the previous qualification question is a completed
        # sales stage, not a reason to wait for AI. Move immediately to the next
        # still-missing required question. Substantive questions stay on the
        # normal memory/AI path and are answered before qualification continues.
        import re as _cycle_re
        _client_now=" ".join(body.message.strip().split())
        _client_low=_client_now.lower().replace("ё","е")
        _cycle_words=_cycle_re.findall(r"[а-яёa-z0-9]+",_client_low,_cycle_re.I)
        _looks_like_client_question=(
            "?" in _client_now
            or any(x in _client_low for x in (
                "как ", "почему", "сколько", "можно", "где ", "когда",
                "цена", "стоим", "адрес", "офис", "салон", "шоурум",
                "услов", "гарант", "достав",
            ))
        )
        if len(_cycle_words) <= 6 and not _looks_like_client_question:
            try:
                from app.db.session import SessionLocal as _CycleSession
                from app.api.client_memory import rules_for as _cycle_rules_for
                from app.api.messenger import (
                    _mop_confirmed_learning_relevant,
                    _mop_effective_learning_rules,
                    _mop_emergency_next_question,
                    _mop_sales_settings_cfg,
                )
                _cycledb=_CycleSession()
                try:
                    _cycle_rules=_mop_effective_learning_rules(
                        _cycle_rules_for(
                            _cycledb, account_id, shared=True, only_confirmed=True
                        ) or []
                    )
                    _cycle_cfg=_mop_sales_settings_cfg(_cycledb,account_id)
                finally:
                    _cycledb.close()
                _cycle_specific_rules=[
                    x for x in _cycle_rules
                    if str((x or {}).get("account_id") or "")!="__platform__"
                ]
                _cycle_learning_relevant=_mop_confirmed_learning_relevant(
                    _client_now,_cycle_specific_rules
                )
                if not _cycle_learning_relevant:
                    _cycle_history="\n".join(
                        ("Клиент: " if m.get("role")=="client" else "Мы: ")
                        + str(m.get("text") or "")
                        for m in state.get("messages", [])
                        if m.get("role") in {"client","mop"}
                    )
                    _last_mop=next((
                        str(m.get("text") or "").strip()
                        for m in reversed(state.get("messages", [])[:-1])
                        if m.get("role")=="mop" and str(m.get("text") or "").strip()
                    ),"")
                    _last_low=_last_mop.lower().replace("ё","е")
                    _answer_matches=True
                    if any(x in _last_low for x in ("размер","площад","габарит")):
                        _answer_matches=bool(
                            _cycle_re.search(
                                r"(?:нет|нету|не\s+зна|замер|замерщик|"
                                r"\d+(?:[.,]\d+)?\s*[xх×]\s*\d+(?:[.,]\d+)?|"
                                r"\d+\s*(?:м2|м²|метр))",
                                _client_low,
                            )
                        )
                    if not _answer_matches:
                        generated={
                            "text":"Уточню именно про размеры: есть примерные размеры помещения или зоны?",
                            "usage":{
                                "provider":"deterministic_guard",
                                "model":"sales_cycle_reask_v1",
                                "prompt_tokens":0,
                                "completion_tokens":0,
                                "total_tokens":0,
                                "cost_rub":0.0,
                                "memory_fast_path":True,
                                "policy_version":"MOP_TRAINING_SALES_CYCLE_REASK_V1",
                            },
                        }
                    else:
                        _cycle_next=_mop_account_next_question(
                            account_id,
                            _cycle_history,
                        )
                        if _cycle_next:
                            generated={
                                "text":_cycle_next,
                                "usage":{
                                    "provider":"deterministic_guard",
                                    "model":"sales_cycle_stage_v1",
                                    "prompt_tokens":0,
                                    "completion_tokens":0,
                                    "total_tokens":0,
                                    "cost_rub":0.0,
                                    "memory_fast_path":True,
                                    "policy_version":"MOP_TRAINING_SALES_CYCLE_FAST_STAGE_V1",
                                },
                            }
                        else:
                            _goal_now=str(
                                ((state.get("crm_client_context") or {}).get("dialog_goal"))
                                or state.get("business_goal")
                                or ""
                            ).lower().replace("ё","е")
                            _phone_known=_mop_phone_known(_cycle_history)
                            _target_text=""
                            if not _phone_known and ("созвон" in _goal_now or "телефон" in _goal_now or "контакт" in _goal_now):
                                if "замер" in _goal_now:
                                    _target_text=(
                                        "Основные параметры зафиксировал. "
                                        "Чтобы согласовать замер и следующий шаг, оставьте, пожалуйста, номер телефона для связи."
                                    )
                                elif "созвон" in _goal_now:
                                    _target_text=(
                                        "Основные параметры зафиксировал. "
                                        "Если удобно, оставьте номер телефона для связи, чтобы согласовать короткий созвон."
                                    )
                                else:
                                    _target_text=(
                                        "Основные параметры зафиксировал. "
                                        "Если удобно, оставьте номер телефона для связи."
                                    )
                            elif _phone_known:
                                _target_text=(
                                    "Основные параметры зафиксировал, контакт уже есть. "
                                    "Повторно ничего из пройденного уточнять не буду."
                                )
                            if _target_text:
                                generated={
                                    "text":_target_text,
                                    "usage":{
                                        "provider":"deterministic_guard",
                                        "model":"sales_cycle_target_v1",
                                        "prompt_tokens":0,
                                        "completion_tokens":0,
                                        "total_tokens":0,
                                        "cost_rub":0.0,
                                        "memory_fast_path":True,
                                        "policy_version":"MOP_TRAINING_SALES_CYCLE_TARGET_V1",
                                    },
                                }
            except Exception as _cycle_exc:
                print("MOP_TRAINING_SALES_CYCLE_FAST_STAGE_DEFERRED %s: %s" % (
                    account_id,str(_cycle_exc)[:160]
                ),flush=True)

    if generated is None:
        generated=generate_ai_draft_reply(
            account_id,
            training_chat,
            return_meta=True,
            # MOP_TRAINING_BOUNDED_WAIT_V2:
            # The training turn must leave enough shared budget for both the remote
            # provider and the local Ollama recovery layer. Remote attempts are
            # separately capped in sales_ai_router, so this larger total budget does
            # not force the UI to wait the full value when a provider answers fast.
            ai_timeout_sec=max(
                35,
                min(
                    70,
                    int(os.environ.get("BORIS_MOP_TRAINING_TURN_TIMEOUT_SEC", "60") or 60),
                ),
            ),
        )
    answer=str((generated or {}).get("text") or "").strip()
    if not answer or answer.startswith("[Ошибка генерации черновика:"): raise HTTPException(status_code=502,detail=answer or "MOP generation failed")

    # TRAINING_LIVE_CONTINUITY_PARITY_V1:
    # Training must show the same continuity behavior as the live MOP. In
    # particular, ordinary client typos like «замерик» must not make the trainer
    # repeat a dimensions question that the client has effectively closed.
    _training_history="\n".join(
        ("Клиент: " if m.get("role")=="client" else "Мы: ") + str(m.get("text") or "")
        for m in state.get("messages", [])
        if m.get("role") in {"client","mop"}
    )
    _previous_mop_turns=[
        str(m.get("text") or "") for m in state.get("messages", [])
        if m.get("role")=="mop"
    ]
    _measurement_context = bool(
        _mop_measurement_intent(body.message)
        and _previous_mop_turns
        and any(
            cue in _previous_mop_turns[-1].lower().replace("ё","е")
            for cue in ("размер","площад","габарит")
        )
    )
    _generated_usage=(generated or {}).get("usage") or {}
    _generated_model=str(_generated_usage.get("model") or "")
    _training_policy=_mop_output_policy_violations(answer,_training_history)
    _tpv=set(str(x) for x in _training_policy)

    # TRAINING_MEASUREMENT_QUESTION_SCOPE_V1:
    # The generic output guard intentionally errs on the safe side and can mark
    # an answer when "размеры" appear in a statement while a different sentence
    # contains the question mark. In training, repair only when an actual
    # question sentence asks dimensions/area/size again.
    import re as _training_measure_re
    _answer_question_sentences=_training_measure_re.findall(
        r"[^.!?]*\?", str(answer or "").lower().replace("ё","е")
    )
    _actually_reasks_dimensions=any(
        _training_measure_re.search(r"(?:размер|площад|габарит)", sentence)
        for sentence in _answer_question_sentences
    )
    if (
        _measurement_context
        or (
            "measurement_required_no_dimensions" in _tpv
            and _actually_reasks_dimensions
        )
        or (
            "repeated_answered_question:dimensions" in _tpv
            and _mop_measurement_intent(body.message)
            and _actually_reasks_dimensions
        )
    ):
        answer=_mop_measurement_repair_reply(_training_history)
    elif {"automation_disclosure", "internal_or_invented_identity"} & _tpv:
        answer=(
            "Понимаю. Давайте разберём ваш вопрос по существу — "
            "уже известные данные повторно спрашивать не буду."
        )
    elif "phone_already_received" in _tpv:
        answer="Контакт уже есть, повторно номер не нужен. Продолжим по текущему вопросу."
    elif "duplicate_previous_reply" in _tpv:
        answer=_mop_account_next_question(account_id, _training_history)
    elif (
        _generated_model!="sales_cycle_reask_v1"
        and any(x.startswith("repeated_answered_question:") for x in _tpv)
    ):
        answer=_mop_account_next_question(account_id, _training_history)

    _usage=_generated_usage
    _analysis=(generated or {}).get("analysis")
    if not isinstance(_analysis,dict):
        _analysis=None
    turn={
        "id":f"t_{now}_{len(state['messages'])}",
        "role":"mop",
        "text":answer,
        "at":int(time.time()),
        "instruction_version":_instruction_version(account_id),
        "business_goal":state.get("business_goal") or "",
        "provider":str(_usage.get("provider") or "local"),
        "model":str(_usage.get("model") or "local"),
        "usage":_usage,
        "analysis":_analysis,
        "human_handoff":bool((_analysis or {}).get("human_handoff") is True),
        "training":True,
        "delivery":"disabled",
    }
    state["messages"].append(turn)
    state["manager_turns"] = sum(1 for m in state.get("messages", []) if m.get("role") == "mop")
    state["updated_at"]=int(time.time()); _save(account_id,rows)
    return {"status":"ok","training":state,"turn":turn,"avito_send":False}


@router.post("/sparring/feedback")
def sparring_feedback(body: SparringFeedback):
    account_id=_safe_account(body.account_id); rows,state=_spar(account_id,body.session_id)
    if not any(m.get("id")==body.turn_id and m.get("role")=="mop" for m in state.get("messages",[])): raise HTTPException(status_code=404,detail="MOP turn not found")
    item={"turn_id":body.turn_id,"score":body.score,"comment":body.comment.strip(),"flags":body.flags,"proposal":_proposal(body.comment,body.flags),"proposal_status":"pending","at":int(time.time())}
    state.setdefault("feedback",[]).append(item)
    state["session_feedback_context"] = item["proposal"]
    state["updated_at"]=int(time.time()); _save(account_id,rows)
    return {"status":"ok","proposal":item,"training":state}


@router.post("/sparring/learning")
def sparring_learning(body: SparringLearning):
    import hashlib
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    account_id=_safe_account(body.account_id); rows,state=_spar(account_id,body.session_id)
    fb=next((x for x in reversed(state.get("feedback",[])) if x.get("turn_id")==body.turn_id),None)
    if not fb: raise HTTPException(status_code=404,detail="feedback not found")
    if body.action=="reject":
        fb["proposal_status"]="rejected"
        state.pop("session_feedback_context",None)
    else:
        rule=(body.rule_text.strip() if body.action=="edit" else str(fb.get("proposal") or "").strip())
        if not rule: raise HTTPException(status_code=400,detail="rule is empty")
        # MOP_TRAINING_POLICY_GATE_V1: training may refine behavior but may not
        # override non-negotiable client identity / false-promise constraints.
        # Keep the feedback for audit, but do not persist a live rule that the
        # MOP engine would immediately suppress.
        try:
            from app.api.messenger import _mop_effective_learning_rules
            _effective_candidate = _mop_effective_learning_rules([{"rule": rule}])
        except Exception:
            _effective_candidate = [{"rule": rule}]
        if not _effective_candidate:
            fb.update({
                "proposal_status":"blocked_by_policy",
                "approved_rule":None,
                "fact_id":None,
                "policy_blocked":True,
                "policy_message":"Правило конфликтует с обязательными ограничениями МОПа и не будет применено в реальных ответах.",
            })
            state.pop("session_feedback_context",None)
            state["updated_at"]=int(time.time()); _save(account_id,rows)
            return {
                "status":"ok",
                "feedback":fb,
                "training":state,
                "memory_store":None,
                "applies_next_generation":False,
                "policy_blocked":True,
                "policy_message":fb["policy_message"],
            }
        h=hashlib.md5(("rule|account|sparring:%s:%s"%(body.session_id,body.turn_id)).encode()).hexdigest(); db=SessionLocal()
        try:
            from app.api.client_memory import learning_rule_scope
            learning_scope=learning_rule_scope(db,account_id)
            row=db.execute(sql_text("SELECT id FROM client_facts WHERE account_id=:a AND fact_hash=:h"),{"a":account_id,"h":h}).first()
            if row:
                fact_id=int(row[0]); db.execute(sql_text("UPDATE client_facts SET value=:v,scope=:sc,source_ref=:sr,status='confirmed',confidence=100,updated_at=now() WHERE id=:i"),{"v":rule,"sc":learning_scope,"sr":"mop_sparring:"+body.session_id,"i":fact_id})
            else:
                fact_id=int(db.execute(sql_text("INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,confidence,status,fact_hash,snippet,confirmed_by) VALUES(:a,'rule','Правило из спарринга',:v,:sc,'client',:sr,now(),100,'confirmed',:h,:sn,'owner') RETURNING id"),{"a":account_id,"v":rule,"sc":learning_scope,"sr":"mop_sparring:"+body.session_id,"h":h,"sn":("Спарринг, оценка %s/10. "%fb.get('score')+rule)[:500]}).scalar_one()); db.commit()
            if row: db.commit()
        finally: db.close()
        fb.update({"proposal_status":"remembered","approved_rule":rule,"fact_id":fact_id})
        state["session_feedback_context"] = rule
    state["updated_at"]=int(time.time()); _save(account_id,rows)
    return {"status":"ok","feedback":fb,"training":state,"memory_store":"client_facts"}


class RealDialogFeedback(BaseModel):
    account_id: str = Field(min_length=1)
    avito_chat_id: str = Field(min_length=1)
    score: int = Field(ge=1, le=5)
    comment: str = Field(min_length=8)
    flags: list[str] = []

class RealDialogLearning(BaseModel):
    account_id: str = Field(min_length=1)
    avito_chat_id: str = Field(min_length=1)
    score: int = Field(ge=1, le=5)
    comment: str = Field(min_length=8)
    rule_text: str = Field(min_length=1)

class RealDialogFollowupDraft(BaseModel):
    account_id: str = Field(min_length=1)
    avito_chat_id: str = Field(min_length=1)
    instruction: str = Field(min_length=3)

@router.post("/dialog-feedback")
def real_dialog_feedback(body: RealDialogFeedback):
    """Save manager feedback AND immediately persist the derived MOP rule in canonical client_facts memory."""
    import hashlib
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    account_id=_safe_account(body.account_id); rows=_load(account_id); now=int(time.time())
    proposal=_proposal(body.comment,body.flags).strip()
    try:
        from app.api.messenger import _mop_effective_learning_rules
        _effective_candidate = _mop_effective_learning_rules([{"rule": proposal}])
    except Exception:
        _effective_candidate = [{"rule": proposal}]
    if not _effective_candidate:
        item={
            "kind":"real_dialog_feedback","id":f"dialogfb_{now}_{len(rows)}",
            "account_id":account_id,"avito_chat_id":body.avito_chat_id,
            "score":body.score,"comment":body.comment.strip(),"flags":body.flags,
            "proposal":proposal,"proposal_status":"blocked_by_policy","fact_id":None,
            "memory_store":None,"policy_blocked":True,
            "policy_message":"Правило конфликтует с обязательными ограничениями МОПа и не будет применено в реальных ответах.",
            "created_at":now,"updated_at":now,
        }
        rows.append(item); _save(account_id,rows)
        return {
            "status":"ok","account_id":account_id,"avito_chat_id":body.avito_chat_id,
            "score":body.score,"comment":body.comment.strip(),"proposal":proposal,
            "proposal_status":"blocked_by_policy","fact_id":None,"memory_store":None,
            "applies_next_generation":False,"feedback_id":item["id"],"saved":True,
            "policy_blocked":True,"policy_message":item["policy_message"],
        }
    h=hashlib.md5(("rule|account|dialog:%s"%body.avito_chat_id).encode()).hexdigest(); db=SessionLocal()
    try:
        from app.api.client_memory import learning_rule_scope
        learning_scope=learning_rule_scope(db,account_id)
        row=db.execute(sql_text("SELECT id FROM client_facts WHERE account_id=:a AND fact_hash=:h"),{"a":account_id,"h":h}).first()
        if row:
            fact_id=int(row[0]); db.execute(sql_text("UPDATE client_facts SET value=:v,scope=:sc,source_ref=:sr,status='confirmed',confidence=100,snippet=:sn,confirmed_by='owner',updated_at=now() WHERE id=:i"),{"v":proposal,"sc":learning_scope,"sr":"mop_dialog:"+body.avito_chat_id,"sn":("Оценка %s/5. "%body.score+body.comment.strip())[:500],"i":fact_id})
        else:
            fact_id=int(db.execute(sql_text("INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,confidence,status,fact_hash,snippet,confirmed_by,confirmed_at) VALUES(:a,'rule','Правило из реального диалога',:v,:sc,'client',:sr,now(),100,'confirmed',:h,:sn,'owner',now()) RETURNING id"),{"a":account_id,"v":proposal,"sc":learning_scope,"sr":"mop_dialog:"+body.avito_chat_id,"h":h,"sn":("Оценка %s/5. "%body.score+body.comment.strip())[:500]}).scalar_one())
        db.commit()
    finally: db.close()
    item={"kind":"real_dialog_feedback","id":f"dialogfb_{now}_{len(rows)}","account_id":account_id,"avito_chat_id":body.avito_chat_id,"score":body.score,"comment":body.comment.strip(),"flags":body.flags,"proposal":proposal,"proposal_status":"remembered","fact_id":fact_id,"memory_store":"client_facts","created_at":now,"updated_at":now}
    rows.append(item); _save(account_id,rows)
    return {"status":"ok","account_id":account_id,"avito_chat_id":body.avito_chat_id,"score":body.score,"comment":body.comment.strip(),"proposal":proposal,"proposal_status":"remembered","fact_id":fact_id,"memory_store":"client_facts","applies_next_generation":True,"feedback_id":item["id"],"saved":True}

@router.post("/followup-draft")
def real_dialog_followup_draft(body: RealDialogFollowupDraft):
    from app.api.messenger import generate_ai_draft_reply
    account_id = _safe_account(body.account_id)
    instruction = " ".join(body.instruction.strip().split())
    try:
        text = generate_ai_draft_reply(
            account_id,
            {"id": body.avito_chat_id, "_training_instruction": instruction},
            style_hint=("Сформулируй именно следующее сообщение клиенту по этой переписке. "
                        "Выполни указание руководителя естественно и по-деловому. "
                        "Не упоминай обучение, правило, руководителя или внутренние инструкции. "
                        "Не выдумывай цены, сроки и факты, которых нет в контексте."),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FOLLOWUP_DRAFT_FAILED: {str(exc)[:220]}")
    text = str(text or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="FOLLOWUP_DRAFT_EMPTY")
    return {"status": "ok", "text": text[:1000], "account_id": account_id, "avito_chat_id": body.avito_chat_id}

@router.post("/dialog-learning")
def real_dialog_learning(body: RealDialogLearning):
    import hashlib
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    account_id=_safe_account(body.account_id); rule=body.rule_text.strip(); h=hashlib.md5(("rule|account|dialog:%s"%body.avito_chat_id).encode()).hexdigest(); db=SessionLocal()
    try:
        from app.api.client_memory import learning_rule_scope
        learning_scope=learning_rule_scope(db,account_id)
        row=db.execute(sql_text("SELECT id FROM client_facts WHERE account_id=:a AND fact_hash=:h"),{"a":account_id,"h":h}).first()
        if row:
            fact_id=int(row[0]); db.execute(sql_text("UPDATE client_facts SET value=:v,scope=:sc,source_ref=:sr,status='confirmed',confidence=100,snippet=:sn,updated_at=now() WHERE id=:i"),{"v":rule,"sc":learning_scope,"sr":"mop_dialog:"+body.avito_chat_id,"sn":("Оценка %s/5. "%body.score+body.comment)[:500],"i":fact_id})
        else:
            fact_id=int(db.execute(sql_text("INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,confidence,status,fact_hash,snippet,confirmed_by) VALUES(:a,'rule','Правило из реального диалога',:v,:sc,'client',:sr,now(),100,'confirmed',:h,:sn,'owner') RETURNING id"),{"a":account_id,"v":rule,"sc":learning_scope,"sr":"mop_dialog:"+body.avito_chat_id,"h":h,"sn":("Оценка %s/5. "%body.score+body.comment)[:500]}).scalar_one())
        db.commit(); return {"status":"ok","fact_id":fact_id,"source":"real_dialog","applies_next_generation":True}
    finally: db.close()


@router.get("/real-dialogs")
def real_mop_dialogs(account_id: str = Query(...), limit: int = Query(40, ge=1, le=100)):
    """Real production dialogs where the virtual MOP actually authored a sent reply.
    Source of truth remains mop_drafts + messenger_messages + existing feedback store.
    No duplicate dialog store is created.
    """
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    account_id = _safe_account(account_id)
    _require_active_mop_training(account_id)
    db = SessionLocal()
    try:
        rows = db.execute(sql_text("""
            SELECT * FROM (
              SELECT DISTINCT ON (d.avito_chat_id)
                     d.avito_chat_id, d.client_name, d.item_title,
                     d.reply_text AS last_ai_reply,
                     COALESCE(d.sent_at,d.updated_at,d.created_at) AS last_ai_at,
                     d.id AS last_draft_id,
                     (SELECT count(*) FROM mop_drafts x
                       WHERE x.account_id=d.account_id
                         AND x.avito_chat_id=d.avito_chat_id
                         AND x.reply_author='ai' AND x.status='sent') AS ai_sent_count,
                     (SELECT count(*) FROM messenger_messages m
                       WHERE m.account_id=d.account_id
                         AND m.avito_chat_id=d.avito_chat_id) AS message_count
                FROM mop_drafts d
               WHERE d.account_id=:a AND d.reply_author='ai' AND d.status='sent'
               ORDER BY d.avito_chat_id, COALESCE(d.sent_at,d.updated_at,d.created_at) DESC
            ) q
            ORDER BY q.last_ai_at DESC
            LIMIT :lim
        """), {"a": account_id, "lim": int(limit)}).mappings().all()
        feedback_rows = [x for x in _load(account_id) if x.get("kind") == "real_dialog_feedback"]
        latest_feedback = {}
        for fb in feedback_rows:
            cid = str(fb.get("avito_chat_id") or "")
            if not cid: continue
            if cid not in latest_feedback or int(fb.get("updated_at") or fb.get("created_at") or 0) > int(latest_feedback[cid].get("updated_at") or latest_feedback[cid].get("created_at") or 0):
                latest_feedback[cid] = fb
        items=[]
        for r in rows:
            x=dict(r)
            fb=latest_feedback.get(str(x.get("avito_chat_id") or ""))
            x["feedback"] = ({"score":fb.get("score"),"comment":fb.get("comment"),"proposal":fb.get("proposal"),"proposal_status":fb.get("proposal_status")} if fb else None)
            items.append(x)
        return {"status":"ok","account_id":account_id,"total":len(items),"items":items}
    finally:
        db.close()


@router.get("/real-dialog")
def real_mop_dialog(account_id: str = Query(...), avito_chat_id: str = Query(...)):
    """Chronological real conversation with explicit provenance for each message."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as sql_text
    account_id=_safe_account(account_id); _require_active_mop_training(account_id)
    db=SessionLocal()
    try:
        lead=db.execute(sql_text("SELECT contact_name,item_title,item_id,stage,msg_count,last_msg_at FROM messenger_leads WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 1"),{"a":account_id,"c":avito_chat_id}).mappings().first() or {}
        msgs=db.execute(sql_text("""
            SELECT m.avito_message_id,m.direction,m.text,m.avito_created_at,m.msg_type,m.content_type,m.media_ref,m.is_read,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM mop_drafts d
                        WHERE d.account_id=m.account_id AND d.avito_chat_id=m.avito_chat_id
                          AND d.status='sent' AND d.reply_author='ai'
                          AND d.reply_text IS NOT NULL AND trim(d.reply_text)=trim(m.text)
                   ) THEN 'mop' WHEN lower(m.direction) LIKE 'out%' THEN 'human_or_company' ELSE 'client' END AS author_source
              FROM messenger_messages m
             WHERE m.account_id=:a AND m.avito_chat_id=:c
             ORDER BY m.avito_created_at ASC, m.id ASC
             LIMIT 160
        """),{"a":account_id,"c":avito_chat_id}).mappings().all()
        ai=db.execute(sql_text("SELECT id,reply_text,incoming_text,ai_summary,sent_at,created_at FROM mop_drafts WHERE account_id=:a AND avito_chat_id=:c AND reply_author='ai' AND status='sent' ORDER BY COALESCE(sent_at,updated_at,created_at) ASC"),{"a":account_id,"c":avito_chat_id}).mappings().all()
        fbs=[x for x in _load(account_id) if x.get("kind")=="real_dialog_feedback" and str(x.get("avito_chat_id"))==str(avito_chat_id)]
        return {"status":"ok","account_id":account_id,"avito_chat_id":avito_chat_id,"client_name":lead.get("contact_name") or "Клиент","item_title":lead.get("item_title") or "","item_id":lead.get("item_id"),"item_url":next((x.get("item_url") for x in [dict(m) for m in msgs] if x.get("item_url")), None) or (f"https://www.avito.ru/items/{lead.get('item_id')}" if lead.get("item_id") else None),"stage":lead.get("stage") or "","messages":[dict(x) for x in msgs],"mop_turns":[dict(x) for x in ai],"feedback":fbs[-1] if fbs else None,"source_explain":{"client":"Сообщение клиента из Avito","mop":"Ответ виртуального МОП BORIS","human_or_company":"Исходящее сообщение из аккаунта Avito; не доказано, что его отправил МОП"}}
    finally:
        db.close()
