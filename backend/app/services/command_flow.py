# -*- coding: utf-8 -*-
"""Оркестрация команды: parse -> policy -> executor -> audit -> usage.

Этим модулем пользуются ВСЕ каналы: web, PWA, позже Telegram. У каналов
не должно быть собственной бизнес-логики — только свой транспорт.

Порядок жёсткий: billing owner -> флаг тарифа -> доступ -> остаток пула,
и только потом обращение к модели. Списание units — строго после успеха.
"""
import datetime
import json
import re

from sqlalchemy import text

from app.services import command_audit as audit
from app.services import command_context as ctx
from app.services import command_executor as executor
from app.services import command_ops  # noqa: F401 — регистрация исполнителей
from app.services import command_limits as limits
from app.services import command_parser as parser
from app.services import command_policy as policy
from app.services import command_registry as registry
from app.services import command_store as store
from app.services.command_redact import redact_payload, redact_text
from app.services.command_contract import CONFIRM_TTL_MINUTES, ERRORS

# Терминальные статусы: перезаписывать их нельзя ни при каких условиях.
TERMINAL = ("done", "failed", "cancelled", "expired")


def _out(command_id, status, **kw):
    d = {"command_id": str(command_id), "status": status,
         "intent": None, "operation": None, "risk_level": None,
         "units": 0, "needs_confirmation": False, "human_summary": None,
         "clarify_question": None, "answer": None, "error_code": None,
         "message": None, "data": None}
    d.update(kw)
    if d.get("error_code") and not d.get("message"):
        d["message"] = ERRORS.get(d["error_code"])
    return d


# Ключи, которые у исполнителя служебные: они уже разложены по полям
# ответа, в data им делать нечего. Всё остальное, что вернул исполнитель,
# и есть его структурированный payload: route, clients, views, task_id.
_SERVICE_KEYS = ("status", "answer", "changes", "actions", "error_code", "detail")


def _payload(res):
    """Полезные данные ОТ ИСПОЛНИТЕЛЯ. Ничего из ответа модели сюда не попадает."""
    if not isinstance(res, dict):
        return None
    data = {k: v for k, v in res.items() if k not in _SERVICE_KEYS}
    return data or None


def _result_json(res):
    """Durable replay snapshot for boris_commands.result.

    Keep the human answer together with structured data so a browser retry or
    status refresh can return the same completed result without executing again.
    """
    if not isinstance(res, dict):
        return None
    snap = {"answer": res.get("answer"), "data": _payload(res)}
    if not snap.get("answer") and not snap.get("data"):
        return None
    try:
        return json.dumps(redact_payload(snap), ensure_ascii=False)[:8000]
    except Exception:
        return None


def _existing_out(row):
    """Return a durable command state without re-running parser/executor.

    Used for HTTP retries with the same command_id and for concurrent confirm
    calls.  Never turns a terminal row back into a live state.
    """
    if not row:
        return None
    st = str(row.get("status") or "parsed")
    result = row.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = None
    answer = None
    data = None
    if isinstance(result, dict):
        # New rows store {answer,data}; legacy rows stored data directly.
        if "answer" in result or "data" in result:
            answer = result.get("answer")
            data = result.get("data") if isinstance(result.get("data"), dict) else None
        else:
            data = result
    return _out(
        row.get("command_id"), st, intent=row.get("intent"),
        operation=row.get("operation"), risk_level=row.get("risk_level"),
        units=int(row.get("units") or 0),
        needs_confirmation=(st == "awaiting_confirm"),
        human_summary=row.get("human_summary"),
        clarify_question=row.get("clarify_question"),
        answer=answer, data=data,
        # A durable replay of a successful command is success, not an error.
        # Duplicate confirm has its own explicit already_charged response below.
        error_code=row.get("error_code"),
    )


def _claim_execution(db, command_id):
    """Exactly one caller may cross the provider/business side-effect boundary."""
    moved = db.execute(text(
        "update boris_commands set status='executing'"
        " where command_id=cast(:c as uuid)"
        "   and status in ('parsed','confirmed')"
        " returning command_id"), {"c": str(command_id)}).fetchone()
    db.commit()
    return bool(moved)


def _finish(db, user, row, command_id):
    """Выполнение операции и списание. Общая часть green и confirm."""
    # EXECUTION_CLAIM_EXACTLY_ONCE_V1: claim the durable row before ANY executor
    # side effect. Concurrent browser retries/confirm taps return the state of
    # the winning request instead of executing or charging twice.
    if not _claim_execution(db, command_id):
        current = store.load(db, command_id)
        return _existing_out(current) or _out(command_id, "failed", error_code="unknown_intent")
    row = store.load(db, command_id) or row
    op = row.get("operation")
    started = datetime.datetime.utcnow()
    res = executor.execute(db, user, op, entities=row.get("entities"),
                           account_id=row.get("account_id"),
                           command_id=command_id,
                           entity_type=row.get("entity_type"),
                           entity_id=row.get("entity_id"))
    ms = int((datetime.datetime.utcnow() - started).total_seconds() * 1000)

    if res.get("status") != "ok":
        store.set_status(db, command_id, "failed",
                         res.get("error_code") or "executor_failed")
        audit.log_event(db, command_id, "failed", actor=row.get("user_id"),
                        payload={"error": res.get("error_code"),
                                 "detail": res.get("detail")})
        return _out(command_id, "failed", intent=row.get("intent"), operation=op,
                    risk_level=row.get("risk_level"),
                    units=int(row.get("units") or 0),
                    human_summary=row.get("human_summary"),
                    error_code=res.get("error_code") or "executor_failed",
                    message=res.get("detail"))

    charged = limits.charge_units_once(
        db, command_id, int(row.get("units") or 0),
        row.get("billing_owner_kind"), row.get("billing_owner_id"),
        duration_ms=ms, result_json=_result_json(res))
    audit.log_event(db, command_id, "done", actor=row.get("user_id"),
                    payload={"charge": charged, "ms": ms})
    return _out(command_id, "done", intent=row.get("intent"), operation=op,
                risk_level=row.get("risk_level"),
                units=int(row.get("units") or 0),
                human_summary=row.get("human_summary"),
                answer=res.get("answer"), data=_payload(res),
                error_code=None if charged == "charged" else charged)


def start(db, user, inp):
    """inp — CommandInput. Возвращает словарь ответа."""
    uid = policy._uid(user)
    command_id = inp.command_id or ctx.new_command_id()
    body = inp.user_text()

    owner, err = policy.precheck(db, user, inp.account_id, inp.source)
    if err and not owner:
        # Отказ ДО разбора тоже должен быть в журнале: для вопроса «что
        # Борис делал сегодня» отказ не менее важен успеха. Секретов нет —
        # только нормализованный код, аккаунт, источник и стадия.
        audit.log_event(db, command_id, "rejected", actor=uid,
                        payload={"error_code": err, "stage": "precheck",
                                 "account_id": inp.account_id,
                                 "source": inp.source})
        return _out(command_id, "failed", error_code=err)
    kind, oid = owner
    if err:
        store.create(db, command_id, uid, kind, oid, inp.account_id, inp.source,
                     raw_text=inp.text, transcript=inp.transcript)
        store.set_status(db, command_id, "failed", err)
        audit.log_event(db, command_id, "rejected", actor=uid,
                        payload={"error_code": err, "stage": "precheck",
                                 "account_id": inp.account_id,
                                 "source": inp.source})
        return _out(command_id, "failed", error_code=err)

    created = store.create(db, command_id, uid, kind, oid, inp.account_id, inp.source,
                           raw_text=inp.text, transcript=inp.transcript,
                           page_context=inp.page_context, entity_type=inp.entity_type,
                           entity_id=inp.entity_id, locale=inp.locale, device=inp.device,
                           audio_seconds=inp.audio_duration, audio_format=inp.audio_format,
                           asr_model=inp.transcription_model)
    if not created:
        existing = store.load(db, command_id)
        # Same UUID may never be replayed across users/accounts/sources.  This is
        # both tenant isolation and an idempotency-conflict detector.
        same_identity = (existing and existing.get("user_id") == uid and
                         str(existing.get("account_id") or "") == str(inp.account_id or "") and
                         str(existing.get("source") or "") == str(inp.source or ""))
        same_payload = (same_identity and
                        str(existing.get("raw_text") or "") == str(redact_text(inp.text, 4000) or "") and
                        str(existing.get("transcript") or "") == str(redact_text(inp.transcript, 4000) or ""))
        if not same_payload:
            audit.log_event(db, command_id, "idempotency_conflict", actor=uid,
                            payload={"account_id": inp.account_id, "source": inp.source})
            return _out(command_id, "failed", error_code="idempotency_conflict")
        audit.log_event(db, command_id, "idempotency_replay", actor=uid,
                        payload={"state": existing.get("status")})
        return _existing_out(existing)

    token = ctx.set_command(command_id=command_id, user_id=uid,
                            account_id=inp.account_id,
                            billing_owner_kind=kind, billing_owner_id=oid)
    try:
        local_date = str((inp.page_context or {}).get("local_date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", local_date):
            local_date = datetime.date.today().isoformat()
        p = parser.parse_command(
            body, account_id=inp.account_id, page_context=inp.page_context,
            entity_type=inp.entity_type, entity_id=inp.entity_id,
            today=local_date)
    finally:
        ctx.reset(token)

    audit.log_event(db, command_id, "parsed", actor=uid,
                    payload={"intent": p.get("intent"),
                             "confidence": p.get("confidence"),
                             "error": p.get("error")})

    operation = registry.resolve_operation(p.get("intent"), p.get("entities"))
    if not operation:
        # Любая человеческая фраза должна закончиться полезным ответом, а не тупиком
        # "Не понял команду". Если внешний парсер не классифицировал запрос, переводим
        # его в безопасный диалоговый fallback без списания units и без внешних действий.
        p = dict(p or {})
        p["intent"] = "chit_chat"
        ent = dict(p.get("entities") or {})
        ent["topic"] = "clarify"
        p["entities"] = ent
        p["confidence"] = float(p.get("confidence") or 0.0)
        p["summary"] = p.get("summary") or "Уточнить запрос пользователя"
        p["clarify_question"] = None
        operation = "chat.smalltalk"

    snap, err = policy.authorize_operation(db, user, inp.account_id, operation)
    if err:
        store.save_parse(db, command_id, intent=p.get("intent"),
                         confidence=p.get("confidence"),
                         entities=p.get("entities"), summary=p.get("summary"),
                         clarify=p.get("clarify_question"), snapshot=snap,
                         status="failed", error_code=err)
        return _out(command_id, "failed", operation=operation,
                    intent=p.get("intent"), error_code=err)

    # Останавливает выполнение ТОЛЬКО clarify_question. confidence не участвует.
    stop = bool(p.get("clarify_question")) or registry.needs_confirmation(operation)
    exp = (datetime.datetime.utcnow()
           + datetime.timedelta(minutes=CONFIRM_TTL_MINUTES)) if stop else None
    store.save_parse(db, command_id, intent=p.get("intent"),
                     confidence=p.get("confidence"), entities=p.get("entities"),
                     summary=p.get("summary"), clarify=p.get("clarify_question"),
                     snapshot=snap, needs_confirmation=stop,
                     status="awaiting_confirm" if stop else "parsed",
                     expires_at=exp)

    if stop:
        pv = executor.preview(db, user, operation, entities=p.get("entities"),
                              account_id=inp.account_id, command_id=command_id,
                              entity_type=inp.entity_type,
                              entity_id=inp.entity_id)
        audit.log_event(db, command_id, "awaiting_confirm", actor=uid,
                        payload={"changes": pv.get("changes")})
        return _out(command_id, "awaiting_confirm", intent=p.get("intent"),
                    operation=operation, risk_level=snap.get("risk_level"),
                    units=snap.get("units"), needs_confirmation=True,
                    human_summary=pv.get("summary") or p.get("summary"),
                    proposed_changes=pv.get("changes") or [],
                    clarify_question=p.get("clarify_question"))

    row = store.load(db, command_id)
    return _finish(db, user, row, command_id)


def confirm(db, user, command_id):
    row = store.load(db, command_id)
    if not row:
        return _out(command_id, "failed", error_code="unknown_intent")
    if row.get("user_id") != policy._uid(user):
        return _out(command_id, "failed", error_code="forbidden_account")

    snap, err = policy.authorize_operation(db, user, row.get("account_id"),
                                           row.get("operation"))
    if err:
        store.set_status(db, command_id, "failed", err)
        return _out(command_id, "failed", error_code=err)

    moved = db.execute(text(
        "update boris_commands set status='confirmed', confirmed_at=now()"
        " where command_id=cast(:c as uuid) and status='awaiting_confirm'"
        "   and (expires_at is null or expires_at > now()) returning units"),
        {"c": str(command_id)}).fetchone()
    db.commit()
    if moved is None:
        cur = store.load(db, command_id) or {}
        st = cur.get("status")
        if st in TERMINAL:
            return _out(command_id, st,
                        error_code="already_charged" if st == "done"
                        else "already_final")
        if st in ("confirmed", "executing"):
            # Another confirm request already won the atomic transition. Never
            # expire or re-run it while the winning request is executing.
            return _existing_out(cur)
        expired = db.execute(text(
            "update boris_commands set status='expired', error_code='expired'"
            " where command_id=cast(:c as uuid) and status='awaiting_confirm'"
            "   and expires_at is not null and expires_at <= now() returning command_id"),
            {"c": str(command_id)}).fetchone()
        db.commit()
        if expired:
            return _out(command_id, "expired", error_code="expired")
        return _existing_out(store.load(db, command_id))

    audit.log_event(db, command_id, "confirmed", actor=policy._uid(user))
    return _finish(db, user, store.load(db, command_id), command_id)


def cancel(db, user, command_id):
    row = store.load(db, command_id)
    if not row or row.get("user_id") != policy._uid(user):
        return _out(command_id, "failed", error_code="forbidden_account")
    # TERMINAL_CANCEL_GUARD_V1: cancellation may win only before execution was
    # claimed. It must never rewrite done/failed/expired or an in-flight side
    # effect, otherwise history and exactly-once accounting become false.
    moved = db.execute(text(
        "update boris_commands set status='cancelled'"
        " where command_id=cast(:c as uuid)"
        "   and status in ('parsed','awaiting_confirm','confirmed')"
        " returning command_id"), {"c": str(command_id)}).fetchone()
    db.commit()
    if not moved:
        return _existing_out(store.load(db, command_id))
    audit.log_event(db, command_id, "cancelled", actor=policy._uid(user))
    return _out(command_id, "cancelled")
