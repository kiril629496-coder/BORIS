# -*- coding: utf-8 -*-

"""
BORIS — human-like MOP training.

This module NEVER sends anything to a real client.

The manager talks to a synthetic AI buyer.
The buyer has hidden state:
- motive
- fears
- urgency
- budget behaviour
- personality
- decision process
- trust
- purchase intent

The model reacts semantically to the manager's actions.

Final review is made by a separate ROP model from the whole dialogue.
"""

import json
import os
import random
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from app.api.auth import get_current_user
from app.client_config.entitlements import has_entitlement
from app.db.session import SessionLocal
from app.usage import log_usage


router = APIRouter(
    prefix="/api/mop-training",
    tags=["mop-training"],
)


CLIENT_MODEL = os.environ.get(
    "MOP_TRAINING_CLIENT_MODEL",
    "gpt-5.4-mini",
)

ROP_MODEL = os.environ.get(
    "MOP_TRAINING_ROP_MODEL",
    "gpt-5.4",
)

MAX_MANAGER_TURNS = 20


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------

def _storage_key(session_id: str):
    return "mop_training:" + str(session_id)


def _gate(db, account_id: str):
    """
    Universal read-only gate for MOP TRAINING.

    TRAINING POLICY:
        Any existing BORIS account may use the internal MOP training.

    IMPORTANT:
        This does NOT grant a commercial MOP entitlement.
        Billing / product limits remain unchanged.

    The training engine itself is intentionally available to every
    existing account so the owner can train the MOP independently
    from the paid production-MOP entitlement.

    No database writes.
    """

    aid = str(account_id or "").strip()

    if not aid:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_account_id",
                "reason": "account_id_required",
            },
        )

    return {
        "allowed": True,
        "reason": "training_open_for_account",
        "source": "mop_training",
        "account_id": aid,
        "commercial_entitlement": "unchanged",
    }



def _load_state(db, account_id, session_id):
    raw = db.execute(
        text(
            """
            SELECT value
            FROM storage
            WHERE account_id=:a
              AND key=:k
            LIMIT 1
            """
        ),
        {
            "a": account_id,
            "k": _storage_key(session_id),
        },
    ).scalar()

    if not raw:
        raise HTTPException(
            status_code=404,
            detail="training session not found",
        )

    try:
        return json.loads(raw)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="training state corrupted",
        )


def _save_state(db, state):
    account_id = state["account_id"]
    key = _storage_key(state["session_id"])

    raw = json.dumps(
        state,
        ensure_ascii=False,
    )

    exists = db.execute(
        text(
            """
            SELECT id
            FROM storage
            WHERE account_id=:a
              AND key=:k
            LIMIT 1
            """
        ),
        {
            "a": account_id,
            "k": key,
        },
    ).scalar()

    if exists:
        db.execute(
            text(
                """
                UPDATE storage
                SET value=:v
                WHERE account_id=:a
                  AND key=:k
                """
            ),
            {
                "a": account_id,
                "k": key,
                "v": raw,
            },
        )
    else:
        db.execute(
            text(
                """
                INSERT INTO storage
                    (account_id, key, value)
                VALUES
                    (:a, :k, :v)
                """
            ),
            {
                "a": account_id,
                "k": key,
                "v": raw,
            },
        )

    db.commit()


def _save_state_fresh(state):
    """Persist training state in a fresh short DB session.

    Local model inference can take tens of seconds under CPU load.  Never keep a
    PostgreSQL session/transaction alive across that inference boundary.
    """
    db = SessionLocal()
    try:
        _save_state(db, state)
    finally:
        db.close()


# ----------------------------------------------------------------------
# Optional context from this account
# ----------------------------------------------------------------------

def _account_context(db, account_id):
    """
    Universal read-only company context.

    Source:
        accounts

    Isolation:
        account_id == requested account_id

    SQLAlchemy Session compatible.
    No writes.
    No migrations.
    No client hardcode.
    """

    aid = str(account_id or "").strip()

    if not aid:
        return {
            "account_id": "",
            "company_context_status": "invalid_account_id",
        }

    row = db.execute(
        text(
            """
            SELECT
                account_id,
                name,
                company_website,
                company_niche,
                company_description,
                company_advantages,
                company_client_description,
                company_tone,
                client_goal,
                client_goal_text
            FROM accounts
            WHERE account_id = :account_id
            LIMIT 1
            """
        ),
        {"account_id": aid},
    ).mappings().first()

    if row is None:
        return {
            "account_id": aid,
            "company_context_status": "account_not_found",
        }

    def _clean(value):
        if value is None:
            return ""
        return str(value).strip()[:3000]

    return {
        "account_id": aid,
        "company_context_status": "found",
        "company_name": _clean(row.get("name")),
        "company_website": _clean(row.get("company_website")),
        "company_niche": _clean(row.get("company_niche")),
        "company_description": _clean(row.get("company_description")),
        "company_advantages": _clean(row.get("company_advantages")),
        "company_client_description": _clean(row.get("company_client_description")),
        "company_tone": _clean(row.get("company_tone")),
        "client_goal": _clean(row.get("client_goal")),
        "client_goal_text": _clean(row.get("client_goal_text")),
    }


# ----------------------------------------------------------------------
# AI
# ----------------------------------------------------------------------

def _clean_json(text):
    """
    Extract one JSON object from AI output.

    Local Ollama models may wrap the answer in:
      - <think>...</think>
      - ```json fences
      - explanatory text before/after JSON

    Training requires a JSON object, so decode the first
    syntactically valid object instead of requiring the whole
    model response to be pure JSON.
    """
    import json
    import re

    raw = str(text or "").strip()

    if not raw:
        raise HTTPException(
            status_code=502,
            detail="AI returned empty training JSON",
        )

    # qwen / reasoning-model wrapper.
    raw = re.sub(
        r"<think>.*?</think>",
        "",
        raw,
        flags=re.I | re.S,
    ).strip()

    # Markdown fences.
    raw = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        raw,
        flags=re.I,
    )

    raw = re.sub(
        r"\s*```\s*$",
        "",
        raw,
    ).strip()

    # Fast path: response already is JSON.
    try:
        value = json.loads(raw)

        if isinstance(value, dict):
            return value
    except Exception:
        pass

    # Robust path: find the first decodable JSON object.
    decoder = json.JSONDecoder()

    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(
                raw[match.start():]
            )
        except Exception:
            continue

        if isinstance(value, dict):
            return value

    raise HTTPException(
        status_code=502,
        detail="AI returned invalid training JSON",
    )



def _usage_details(usage):
    cached = 0

    details = getattr(
        usage,
        "input_tokens_details",
        None,
    )

    if details is not None:
        cached = int(
            getattr(
                details,
                "cached_tokens",
                0,
            )
            or 0
        )

    return {
        "input_tokens_details": {
            "cached_tokens": cached,
        }
    }



def _boris_r286_compact_company_context(context):
    """
    Compact account/company context before sending it to local Qwen.

    The complete company context remains available to BORIS.
    Only the high-value fields are sent into the small local model.
    """

    context = context or {}

    def _cut(value, limit):
        value = str(value or "").strip()
        return value[:limit]

    return {
        "account_id": _cut(
            context.get("account_id"),
            120,
        ),
        "company_name": _cut(
            context.get("company_name"),
            160,
        ),
        "company_niche": _cut(
            context.get("company_niche"),
            300,
        ),
        "company_description": _cut(
            context.get("company_description"),
            1800,
        ),
        "company_advantages": _cut(
            context.get("company_advantages"),
            1200,
        ),
        "company_client_description": _cut(
            context.get("company_client_description"),
            1200,
        ),
        "company_tone": _cut(
            context.get("company_tone"),
            120,
        ),
        "client_goal": _cut(
            context.get("client_goal"),
            300,
        ),
        "client_goal_text": _cut(
            context.get("client_goal_text"),
            800,
        ),
    }




# BORIS_MOP_TRAINING_STRUCTURED_OUTPUT
def _mop_training_output_schema(operation):
    """
    Ollama structured-output contract.

    The local model must return the fields actually consumed
    by each training operation. Property values remain flexible;
    the existing training prompt defines their semantic types.
    """
    contracts = {'mop_training_client_start': ['hidden_profile', 'initial_intent', 'initial_trust', 'opening_message', 'scenario_public'], 'mop_training_client_turn': ['intent_delta', 'message', 'outcome', 'trust_delta'], 'mop_training_rop_review': ['result'], 'mop_messenger_reply': ['reply_text', 'intent', 'qualification_stage', 'lead_temperature', 'target_action', 'phone_received', 'crm_action', 'next_action', 'reactivation_candidate', 'human_handoff', 'handoff_reason', 'qualification_fields']}

    keys = list(
        contracts.get(str(operation or ""), [])
    )

    if not keys:
        return {
            "type": "object",
            "additionalProperties": True,
        }

    if str(operation or "") == "mop_messenger_reply":
        # Fast production contract: the local model writes the client-facing
        # reply and only the handoff decision. Lead stage/temperature/CRM action
        # are derived deterministically by messenger.py from the real dialogue.
        return {
            "type": "object",
            "properties": {
                "reply_text": {"type": "string"},
                "human_handoff": {"type": "boolean"},
                "handoff_reason": {},
            },
            "required": ["reply_text", "human_handoff", "handoff_reason"],
            "additionalProperties": False,
        }

    # BORIS_R35_STRICT_CLIENT_SCHEMA
    if str(operation or "") == "mop_training_client_turn":
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                },
                "trust_delta": {
                    "type": "integer",
                },
                "intent_delta": {
                    "type": "integer",
                },
                "outcome": {
                    "type": "string",
                    "enum": [
                        "continue",
                        "next_step",
                        "sale",
                        "lost",
                        "human_required",
                    ],
                },
            },
            "required": [
                "message",
                "trust_delta",
                "intent_delta",
                "outcome",
            ],
            "additionalProperties": True,
        }

    return {
        "type": "object",
        "properties": {
            key: {}
            for key in keys
        },
        "required": keys,
        "additionalProperties": True,
    }

def _ai_json(
    account_id: str,
    model: str,
    operation: str,
    instructions: str,
    payload: dict,
    role_instruction: str = "Отвечай естественно как клиент.",
    timeout_sec: int | None = None,
    allow_json_retry: bool = True,
):
    """
    BORIS R22.0 — FAST LOCAL MOP.

    Одна общая локальная модель.
    account_id и Company Context приходят от реального MOP.
    """

    # Account context is fetched in its own short session and closed BEFORE
    # Ollama inference.  The old code referenced an undefined `db` here and
    # silently skipped this enrichment; worse, callers kept their outer session
    # open during a potentially long local-model request.
    try:
        _r28_account_id = str(
            ((payload or {}).get("account_id") if isinstance(payload, dict) else "")
            or account_id
            or ""
        ).strip()
        if _r28_account_id:
            _ctx_db = SessionLocal()
            try:
                _r28_context = _account_context(_ctx_db, _r28_account_id)
            finally:
                _ctx_db.close()
            if isinstance(payload, dict):
                payload = dict(payload)
                payload["company_context"] = _boris_r286_compact_company_context(_r28_context)
                payload["boris_account_id"] = _r28_account_id
    except Exception:
        # Context enrichment must never make the training engine unavailable.
        pass

    import json as _json
    import time as _time
    import urllib.request as _urllib_request
    import fcntl as _fcntl

    started = _time.time()
    _default_timeout = 180 if operation == "mop_messenger_reply" else 120
    _timeout_sec = max(5, min(int(timeout_sec or _default_timeout), 180))

    context = _json.dumps(
        payload or {},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    prompt = (
        str(instructions or "").strip()
        + "\n\nКОНТЕКСТ:\n"
        + context
        + "\n\n"
        + str(role_instruction or "").strip()
        + "\nНе объясняй решение."
        + "\nВерни только JSON."
    )

    # MOP_TRAINING_SALES_AI_ROUTER_V1:
    # Training uses the same autonomous provider policy as live MOP/ROP:
    # OpenAI when usable -> free Gemini CLI -> local Ollama. This keeps
    # sparring alive when paid OpenAI is unavailable and automatically
    # returns to OpenAI after its reliability circuit recovers.
    try:
        import hashlib as _router_hashlib
        from app.services import sales_ai_router as _sales_ai
        _router_predict = (
            512 if operation in {"mop_training_client_start", "mop_training_rop_review"}
            else 160
        )
        _router_intent = "mop-training:" + _router_hashlib.sha256(
            (
                str(account_id or "") + "\n" + str(operation or "") + "\n"
                + str(instructions or "") + "\n" + context
            ).encode("utf-8")
        ).hexdigest()
        _routed = _sales_ai.generate_text(
            account_id=str(account_id or ""),
            operation=str(operation or "mop_training"),
            prompt=prompt,
            module="mop_training",
            idempotency_key=_router_intent,
            openai_model=str(model or "gpt-5.4-mini"),
            max_output_tokens=_router_predict,
            timeout=_timeout_sec,
            expect_json=True,
            local_format=_mop_training_output_schema(operation),
            local_temperature=0.10,
            local_num_ctx=4096,
            local_num_predict=_router_predict,
        )
        _result = _routed.get("json")
        if not isinstance(_result, dict):
            raise _sales_ai.SalesAIOutputInvalid("training result is not a JSON object")
        _ru = dict(_routed.get("usage") or {})
        _usage = {
            "provider": str(_routed.get("provider") or ""),
            "model": str(_routed.get("model") or ""),
            "input_tokens": int(_ru.get("prompt_tokens") or 0),
            "output_tokens": int(_ru.get("completion_tokens") or 0),
            "prompt_tokens": int(_ru.get("prompt_tokens") or 0),
            "completion_tokens": int(_ru.get("completion_tokens") or 0),
            "cost_rub": float(_routed.get("cost_rub") or 0.0),
            "elapsed_sec": round(_time.time() - started, 3),
            "fallback_chain": list(_routed.get("fallback_chain") or []),
            "router_policy": "openai_if_usable_then_free_gemini_then_local",
        }
        return _result, _usage
    except Exception as _router_exc:
        raise RuntimeError(
            "MOP training AI router failed: %s" % (_router_exc,)
        ) from _router_exc



def _add_usage(state, usage):
    u = state.setdefault(
        "usage",
        {
            "calls": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "cost_rub": 0,
        },
    )

    u["calls"] += 1

    u["input_tokens"] += int(
        usage.get("input_tokens")
        or 0
    )

    u["cached_input_tokens"] += int(
        usage.get("cached_input_tokens")
        or 0
    )

    u["output_tokens"] += int(
        usage.get("output_tokens")
        or 0
    )

    u["cost_rub"] = round(
        float(u.get("cost_rub") or 0)
        + float(usage.get("cost_rub") or 0),
        4,
    )


# ----------------------------------------------------------------------
# Hide all buyer internals from manager
# ----------------------------------------------------------------------

def _public(state):
    return {
        "session_id": state["session_id"],
        "account_id": state["account_id"],
        "status": state["status"],
        "mode": state.get("mode", "manager_training"),
        "scenario": state["scenario_public"],
        "messages": state["messages"],
        "manager_turns": state["manager_turns"],
        "max_manager_turns": MAX_MANAGER_TURNS,
        "outcome": state.get("outcome"),
        "review": state.get("review"),
        "human_feedback": state.get("human_feedback"),
        "usage": state.get("usage"),
    }


# ----------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------

@router.get("/status")
def status(
    account_id: str,
    user=Depends(get_current_user),
):
    db = SessionLocal()

    try:
        ent = has_entitlement(
            db,
            account_id,
            "mop",
        )

        return {
            "status": "ok",
            "entitled": True,
            "reason": ent.get("reason"),
            "client_model": CLIENT_MODEL,
            "rop_model": ROP_MODEL,
            "max_manager_turns": MAX_MANAGER_TURNS,
        }

    finally:
        db.close()


# ----------------------------------------------------------------------
# Start
# ----------------------------------------------------------------------

@router.post("/start")
def start(
    body: dict = Body(...),
    user=Depends(get_current_user),
):
    account_id = str(
        body.get("account_id")
        or ""
    ).strip()

    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="account_id required",
        )

    db = SessionLocal()

    try:
        _gate(
            db,
            account_id,
        )

        account_context = _account_context(
            db,
            account_id,
        )
        # Local model inference may be slow; release PostgreSQL before it starts.
        db.close()

        difficulty = random.choice(
            [
                "обычный",
                "выше среднего",
                "сложный",
            ]
        )

        buyer, usage = _ai_json(
            account_id=account_id,
            model=CLIENT_MODEL,
            operation="mop_training_client_start",
            instructions="""
Ты играешь настоящего потенциального клиента компании.

Это НЕ тест с правильными ключевыми словами.

Твоя задача — создать реалистичного человека с внутренней психологией.

У клиента должны быть скрыты:
- настоящий мотив покупки;
- уровень интереса;
- бюджетное поведение;
- страх;
- прошлый негативный опыт, если уместно;
- способ принятия решения;
- отношение к продавцам;
- срочность;
- одно или несколько возражений;
- признаки, по которым доверие растёт или падает.

Менеджеру эти данные НЕ показываются.

Сформируй естественную ситуацию и первое сообщение клиента.

Клиент должен вести себя как обычный человек в Avito/мессенджере:
короткие фразы, иногда неполные ответы, нормальные сомнения.

Не создавай ситуацию, которую невозможно продать вообще.

Верни ТОЛЬКО JSON:

{
  "scenario_public": "...",
  "opening_message": "...",
  "hidden_profile": {
    "personality": "...",
    "real_motive": "...",
    "main_fear": "...",
    "budget_behaviour": "...",
    "decision_process": "...",
    "urgency": "...",
    "objections": ["..."],
    "trust_up": ["..."],
    "trust_down": ["..."]
  },
  "initial_trust": 40,
  "initial_intent": 65
}
""",
            payload={
                "difficulty": difficulty,
                "account_context": account_context,
            },
        )

        try:
            trust = int(
                buyer.get(
                    "initial_trust",
                    40,
                )
            )
        except Exception:
            trust = 40

        try:
            intent = int(
                buyer.get(
                    "initial_intent",
                    60,
                )
            )
        except Exception:
            intent = 60

        trust = max(
            0,
            min(100, trust),
        )

        intent = max(
            0,
            min(100, intent),
        )

        state = {
            "version": 3,
            "session_id": uuid.uuid4().hex,
            "account_id": account_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "active",
            "account_context": account_context,

            "scenario_public": str(
                buyer.get(
                    "scenario_public",
                    "Потенциальный клиент обратился по объявлению.",
                )
            ),

            "hidden_profile": buyer.get(
                "hidden_profile"
            ) or {},

            "trust": trust,
            "intent": intent,

            "manager_turns": 0,

            "messages": [
                {
                    "role": "client",
                    "text": str(
                        buyer.get(
                            "opening_message"
                        )
                        or
                        "Здравствуйте. Подскажите по вашему предложению."
                    ),
                }
            ],

            "usage": {
                "calls": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "cost_rub": 0,
            },

            "outcome": None,
            "review": None,
        }

        _add_usage(
            state,
            usage,
        )

        _save_state_fresh(state)

        return {
            "status": "ok",
            "training": _public(state),
        }

    finally:
        db.close()


# ----------------------------------------------------------------------
# MOP sparring: human plays the client, production MOP answers in this same
# training engine. No Avito send and no second memory/training storage.
# ----------------------------------------------------------------------

@router.post("/sparring/start")
def sparring_start(body: dict = Body(...), user=Depends(get_current_user)):
    account_id = str(body.get("account_id") or "").strip()
    if not account_id: raise HTTPException(status_code=400, detail="account_id required")
    db=SessionLocal()
    try:
        _gate(db,account_id)
        state={"version":4,"mode":"mop_sparring","session_id":uuid.uuid4().hex,"account_id":account_id,"created_at":datetime.utcnow().isoformat(),"status":"active","scenario_public":"Вы играете клиента. МОП отвечает по текущим настройкам, цели и накопленному обучению аккаунта.","hidden_profile":{},"trust":0,"intent":0,"manager_turns":0,"messages":[],"usage":{"calls":0,"input_tokens":0,"cached_input_tokens":0,"output_tokens":0,"cost_rub":0},"outcome":None,"review":None}
        _save_state(db,state); return {"status":"ok","training":_public(state)}
    finally: db.close()

@router.post("/sparring/reply")
def sparring_reply(body: dict = Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); session_id=str(body.get("session_id") or "").strip(); msg=str(body.get("text") or "").strip()
    if not account_id or not session_id or not msg: raise HTTPException(status_code=400,detail="account_id/session_id/text required")
    db=SessionLocal()
    try:
        _gate(db,account_id); state=_load_state(db,account_id,session_id)
        if state.get("mode")!="mop_sparring" or state.get("status")!="active": raise HTTPException(status_code=409,detail="sparring session is not active")
        state.setdefault("messages",[]).append({"role":"client","text":msg})
        from app.api.messenger import generate_ai_draft_reply
        result=generate_ai_draft_reply(account_id,{"id":"training:"+session_id,"context":{},"_training_messages":state["messages"]},return_meta=True)
        answer=str((result or {}).get("text") or "").strip(); usage=(result or {}).get("usage")
        if not answer or answer.startswith("[Ошибка"): raise HTTPException(status_code=502,detail=answer or "МОП не ответил")
        state["messages"].append({"role":"manager","text":answer}); state["review"] = {"summary": "Оцените ответ МОП и сохраните подробный комментарий — он войдёт в накопленное обучение."}; state["manager_turns"]=int(state.get("manager_turns") or 0)+1
        if usage:
            state.setdefault("usage",{})["calls"]=int(state.get("usage",{}).get("calls") or 0)+1
            state["usage"]["input_tokens"]=int(state["usage"].get("input_tokens") or 0)+int(usage.get("prompt_tokens") or 0)
            state["usage"]["output_tokens"]=int(state["usage"].get("output_tokens") or 0)+int(usage.get("completion_tokens") or 0)
            state["usage"]["cost_rub"]=round(float(state["usage"].get("cost_rub") or 0)+float(usage.get("cost_rub") or 0),4)
        _save_state(db,state); return {"status":"ok","training":_public(state)}
    finally: db.close()

# ----------------------------------------------------------------------
# Manager reply
# ----------------------------------------------------------------------

@router.post("/reply")
def reply(
    body: dict = Body(...),
    user=Depends(get_current_user),
):
    account_id = str(
        body.get("account_id")
        or ""
    ).strip()

    session_id = str(
        body.get("session_id")
        or ""
    ).strip()

    manager_text = str(
        body.get("text")
        or ""
    ).strip()

    if (
        not account_id
        or not session_id
    ):
        raise HTTPException(
            status_code=400,
            detail="account_id/session_id required",
        )

    if not manager_text:
        raise HTTPException(
            status_code=400,
            detail="empty message",
        )

    db = SessionLocal()

    try:
        _gate(
            db,
            account_id,
        )

        state = _load_state(
            db,
            account_id,
            session_id,
        )

        if state.get("status") != "active":
            raise HTTPException(
                status_code=409,
                detail="training already finished",
            )

        training_account_context = (
            state.get("account_context")
            or _account_context(db, account_id)
        )
        state["account_context"] = training_account_context
        # Do not hold the DB connection while local inference is running.
        db.close()

        state["messages"].append(
            {
                "role": "manager",
                "text": manager_text,
            }
        )

        state["manager_turns"] += 1

        ai, usage = _ai_json(
            account_id=account_id,
            model=CLIENT_MODEL,
            operation="mop_training_client_turn",
            instructions="""
Ты продолжаешь играть ТОГО ЖЕ потенциального клиента.

У тебя есть скрытый психологический профиль,
контекст РЕАЛЬНОГО аккаунта и история разговора.

КРИТИЧЕСКИ ВАЖНО:
- предмет разговора определяется ТОЛЬКО account_context;
- никогда не меняй нишу аккаунта;
- не придумывай товары и услуги, которых нет в account_context;
- не придумывай приложения, SaaS, подписки и платёжные сервисы;
- не выдумывай цены, характеристики, гарантии или условия;
- если данных нет — задай естественный вопрос;
- сохраняй один предмет разговора всю тренировку.

Ты реальный потенциальный клиент в Avito/мессенджере.
Отвечай естественно и коротко: обычно 1-3 предложения.
Не пиши длинные монологи.

Реагируй на СМЫСЛ и поведение менеджера, а не на ключевые слова.

Оцени внутренне:
- понял ли он, что человеку реально нужно;
- отвечает ли прямо на вопросы;
- задаёт ли уместные вопросы;
- слышит ли сомнение;
- слишком ли давит;
- создаёт ли доверие;
- предлагает ли следующий шаг вовремя;
- врёт или обещает то, чего не знает;
- чувствует ли момент покупки.

Если менеджер сильный:
- клиент постепенно раскрывается;
- отвечает подробнее;
- задаёт конкретные вопросы;
- может согласиться на следующий шаг.

Если менеджер слабый:
- клиент становится холоднее;
- отвечает короче;
- сомневается;
- может уйти.

НЕ ОБУЧАЙ менеджера во время разговора.
НЕ говори, что он ошибся.
НЕ раскрывай скрытый профиль.
НЕ говори, что ты AI.

Не продолжай разговор искусственно.
Если ситуация естественно завершена — заверши её.

Верни ТОЛЬКО JSON:

{
  "message": "...",
  "trust_delta": 0,
  "intent_delta": 0,
  "outcome": "continue|next_step|sale|lost|human_required",
  "hidden_reason": "..."
}
""",
            payload={
                # BORIS_R33_CONTEXT_EVERY_TURN
                "account_id":
                    account_id,

                "account_context":
                    training_account_context,

                "hidden_profile":
                    state.get(
                        "hidden_profile"
                    ),

                "trust":
                    state.get(
                        "trust"
                    ),

                "intent":
                    state.get(
                        "intent"
                    ),

                "dialogue":
                    state.get(
                        "messages",
                        [],
                    ),
            },
        )

        try:
            trust_delta = int(
                ai.get(
                    "trust_delta",
                    0,
                )
            )
        except Exception:
            trust_delta = 0

        try:
            intent_delta = int(
                ai.get(
                    "intent_delta",
                    0,
                )
            )
        except Exception:
            intent_delta = 0

        state["trust"] = max(
            0,
            min(
                100,
                int(
                    state.get(
                        "trust",
                        50,
                    )
                )
                + trust_delta,
            ),
        )

        state["intent"] = max(
            0,
            min(
                100,
                int(
                    state.get(
                        "intent",
                        50,
                    )
                )
                + intent_delta,
            ),
        )

        outcome = str(
            ai.get(
                "outcome",
                "continue",
            )
        )

        if (
            state["manager_turns"]
            >= MAX_MANAGER_TURNS
        ):
            outcome = "max_turns"

        # BORIS_R34_CLIENT_MESSAGE_CONTRACT
        #
        # mop_training_client_turn MUST produce an actual client
        # message. Never silently accept an empty/malformed result,
        # otherwise the manager message is saved without a client reply.
        _raw_client_message = ai.get(
            "message"
        )

        if isinstance(
            _raw_client_message,
            dict,
        ):
            _raw_client_message = (
                _raw_client_message.get("text")
                or _raw_client_message.get("message")
                or ""
            )

        # BORIS_R35_CLIENT_REPLY
        raw_client_message = ai.get(
            "message"
        )

        if isinstance(
            raw_client_message,
            dict,
        ):
            raw_client_message = (
                raw_client_message.get("text")
                or raw_client_message.get("message")
                or ""
            )

        elif isinstance(
            raw_client_message,
            list,
        ):
            raw_client_message = " ".join(
                str(x)
                for x in raw_client_message
                if x is not None
            )

        client_text = str(
            raw_client_message
            or ""
        ).strip()

        if client_text in (
            "",
            "{}",
            "[]",
            "null",
            "None",
        ):
            raise RuntimeError(
                "mop_training_client_turn returned empty message"
            )

        state["messages"].append(
            {
                "role": "client",
                "text": client_text,
            }
        )

        _add_usage(
            state,
            usage,
        )

        if outcome != "continue":
            state["outcome"] = outcome

        _save_state_fresh(state)

        return {
            "status": "ok",
            "training": _public(state),
            "conversation_finished":
                outcome != "continue",
        }

    finally:
        db.close()


# ----------------------------------------------------------------------
# Human score/comment -> existing confirmed memory rule
# ----------------------------------------------------------------------

@router.post("/feedback")
def human_feedback(body: dict = Body(...), user=Depends(get_current_user)):
    account_id = str(body.get("account_id") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    comment = str(body.get("comment") or "").strip()
    try:
        score = int(body.get("score"))
    except Exception:
        score = -1
    if not account_id or not session_id:
        raise HTTPException(status_code=400, detail="account_id/session_id required")
    if score < 1 or score > 10:
        raise HTTPException(status_code=400, detail="Оценка должна быть от 1 до 10")
    if len(comment) < 8:
        raise HTTPException(status_code=400, detail="Добавьте подробный комментарий")
    db = SessionLocal()
    try:
        _gate(db, account_id)
        state = _load_state(db, account_id, session_id)
        # Idempotent per session: editing feedback updates the same canonical fact.
        import hashlib
        fact_hash = hashlib.md5(("rule|account|training:%s" % session_id).encode()).hexdigest()
        name = "Обратная связь по тренировке"
        rule = comment[:2000]
        row = db.execute(text("SELECT id FROM client_facts WHERE account_id=:a AND fact_hash=:h"), {"a": account_id, "h": fact_hash}).first()
        if row:
            fact_id = int(row[0])
            db.execute(text("UPDATE client_facts SET name=:n,value=:v,source_type='client',source_ref=:sr,confidence=100,status='confirmed',confirmed_by=:by,updated_at=now() WHERE id=:i"), {"n": name, "v": rule, "sr": "mop_training:"+session_id, "by": str(getattr(user,'email',None) or getattr(user,'id','owner'))[:60], "i": fact_id})
        else:
            fact_id = int(db.execute(text("INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,confidence,status,fact_hash,snippet,confirmed_by) VALUES(:a,'rule',:n,:v,'account','client',:sr,now(),100,'confirmed',:h,:sn,:by) RETURNING id"), {"a": account_id,"n":name,"v":rule,"sr":"mop_training:"+session_id,"h":fact_hash,"sn":("Оценка %s/10. " % score + comment)[:500],"by":str(getattr(user,'email',None) or getattr(user,'id','owner'))[:60]}).scalar_one())
        state["human_feedback"] = {"score": score, "comment": comment, "fact_id": fact_id, "saved_at": datetime.utcnow().isoformat()}
        _save_state(db, state)
        return {"status":"ok","training":_public(state),"learning":{"fact_id":fact_id,"source":"human_training","applies_next_generation":True}}
    finally:
        db.close()


@router.post("/rule/update")
def training_rule_update(body: dict = Body(...), user=Depends(get_current_user)):
    account_id = str(body.get("account_id") or "").strip()
    value = str(body.get("value") or "").strip()
    try: fact_id = int(body.get("fact_id"))
    except Exception: fact_id = 0
    if not account_id or not fact_id or len(value) < 3:
        raise HTTPException(status_code=400, detail="account_id/fact_id/value required")
    db = SessionLocal()
    try:
        _gate(db, account_id)
        row = db.execute(text("""UPDATE client_facts SET value=:v,status='confirmed',confidence=100,
            confirmed_by=:by,updated_at=now() WHERE id=:i AND account_id=:a AND category='rule'
            RETURNING id"""), {"v":value[:2000],"by":str(getattr(user,'email',None) or getattr(user,'id','owner'))[:60],"i":fact_id,"a":account_id}).first()
        if not row: raise HTTPException(status_code=404, detail="training rule not found")
        db.commit()
        return {"status":"ok","fact_id":fact_id,"value":value[:2000],"applies_next_generation":True}
    finally:
        db.close()


@router.post("/rule/delete")
def training_rule_delete(body: dict = Body(...), user=Depends(get_current_user)):
    account_id = str(body.get("account_id") or "").strip()
    try: fact_id = int(body.get("fact_id"))
    except Exception: fact_id = 0
    if not account_id or not fact_id:
        raise HTTPException(status_code=400, detail="account_id/fact_id required")
    db = SessionLocal()
    try:
        _gate(db, account_id)
        # Existing memory semantics: rejected facts are excluded from confirmed learning.
        row = db.execute(text("""UPDATE client_facts SET status='rejected',updated_at=now()
            WHERE id=:i AND account_id=:a AND category='rule' RETURNING id"""), {"i":fact_id,"a":account_id}).first()
        if not row: raise HTTPException(status_code=404, detail="training rule not found")
        db.commit()
        return {"status":"ok","fact_id":fact_id,"applies_next_generation":False}
    finally:
        db.close()


# ----------------------------------------------------------------------
# Finish + ROP analysis
# ----------------------------------------------------------------------

@router.post("/finish")
def finish(
    body: dict = Body(...),
    user=Depends(get_current_user),
):
    account_id = str(
        body.get("account_id")
        or ""
    ).strip()

    session_id = str(
        body.get("session_id")
        or ""
    ).strip()

    if (
        not account_id
        or not session_id
    ):
        raise HTTPException(
            status_code=400,
            detail="account_id/session_id required",
        )

    db = SessionLocal()

    try:
        _gate(
            db,
            account_id,
        )

        state = _load_state(
            db,
            account_id,
            session_id,
        )

        if state.get("review"):
            return {
                "status": "ok",
                "training": _public(state),
            }

        # ROP review may take a long time on the local model. Release the DB
        # connection before inference; persist the result in a fresh session.
        db.close()

        review, usage = _ai_json(
            account_id=account_id,
            model=ROP_MODEL,
            operation="mop_training_rop_review",
            instructions="""
Ты — очень сильный РОП и тренер продаж.

Перед тобой ПОЛНЫЙ разговор менеджера с учебным клиентом
и скрытая реальная психология покупателя.

Разбирай НЕ по ключевым словам и НЕ по механическому чек-листу.

Тебя интересует мышление менеджера.

Ответь:

1. Понял ли менеджер настоящий мотив клиента?
2. Где он понял клиента правильно?
3. Что клиент пытался показать, но менеджер не заметил?
4. Как менялось доверие?
5. Как менялось намерение купить?
6. В какой момент сделка приблизилась?
7. В какой момент сделка стала слабее?
8. Был ли следующий шаг предложен в правильный момент?
9. Какая ОДНА главная ошибка мышления?
10. Что менеджеру надо научиться замечать в следующих разговорах?
11. Как выглядел бы сильный ответ в ключевой точке?

Не ставь высокий балл ради вежливости.
Не придумывай ошибок, если менеджер действительно работал сильно.

Score 0..100 — твоя цельная экспертная оценка разговора.
Это НЕ сумма пунктов.

Верни ТОЛЬКО JSON:

{
  "score": 0,
  "result": "won|progress|neutral|lost",
  "manager_level": "junior|middle|strong",
  "summary": "...",

  "what_manager_understood": [
    "..."
  ],

  "what_manager_missed": [
    "..."
  ],

  "turning_point": {
    "manager_message": "...",
    "client_state": "...",
    "why_it_mattered": "..."
  },

  "main_thinking_error": "...",
  "better_thinking": "...",

  "example_better_reply": "...",

  "next_training_focus": "...",

  "coach_message": "..."
}
""",
            payload={
                "hidden_profile":
                    state.get(
                        "hidden_profile"
                    ),

                "initial_scenario":
                    state.get(
                        "scenario_public"
                    ),

                "final_trust":
                    state.get(
                        "trust"
                    ),

                "final_intent":
                    state.get(
                        "intent"
                    ),

                "outcome":
                    state.get(
                        "outcome"
                    ),

                "dialogue":
                    state.get(
                        "messages",
                        [],
                    ),
            },
        )

        state["review"] = review
        state["status"] = "finished"

        if not state.get("outcome"):
            state["outcome"] = str(
                review.get(
                    "result",
                    "neutral",
                )
            )

        _add_usage(
            state,
            usage,
        )

        _save_state_fresh(state)

        return {
            "status": "ok",
            "training": _public(state),
        }

    finally:
        db.close()
