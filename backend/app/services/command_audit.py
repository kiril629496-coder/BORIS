# -*- coding: utf-8 -*-
"""Журнал Command Center и retention текстов.

Единственный писатель boris_command_events. Сырой payload сюда попасть не
может: redact_payload применяется внутри, обойти его нельзя.
"""
import datetime
import json

from sqlalchemy import text

from app.services.command_redact import redact_payload
from app.services.exception_observability import observe_suppressed

TEXT_RETENTION_DAYS = 90


def log_event(db, command_id, event, actor=None, payload=None):
    """Пишет запись журнала. Никогда не роняет вызывающий код."""
    try:
        clean = redact_payload(payload) if payload is not None else None
        db.execute(text(
            "insert into boris_command_events"
            " (command_id, ts, event, actor, payload_redacted)"
            " values (cast(:c as uuid), :ts, :e, :a, cast(:p as jsonb))"),
            {"c": str(command_id), "ts": datetime.datetime.utcnow(),
             "e": str(event)[:80], "a": (str(actor)[:80] if actor else None),
             "p": json.dumps(clean, ensure_ascii=False) if clean is not None else None})
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=33)
        return False


def events_of(db, command_id, limit=50):
    rows = db.execute(text(
        "select ts, event, actor, payload_redacted from boris_command_events"
        " where command_id = cast(:c as uuid) order by ts, id limit :l"),
        {"c": str(command_id), "l": limit}).fetchall()
    return [{"ts": r[0].isoformat() if r[0] else None,
             "event": r[1], "actor": r[2], "payload": r[3]} for r in rows]


def purge_texts(db, days=TEXT_RETENTION_DAYS, dry=True):
    """Чистит raw_text и transcript у старых команд. Сама запись остаётся:
    история команд не теряется, уходит только содержимое реплики."""
    row = db.execute(text(
        "select count(*) from boris_commands"
        " where text_purged_at is null"
        "   and created_at < now() - make_interval(days => :d)"
        "   and (raw_text is not null or transcript is not null)"),
        {"d": int(days)}).fetchone()
    n = int(row[0]) if row else 0
    if dry or not n:
        return {"found": n, "purged": 0, "dry": bool(dry)}
    db.execute(text(
        "update boris_commands set raw_text = null, transcript = null,"
        " text_purged_at = now()"
        " where text_purged_at is null"
        "   and created_at < now() - make_interval(days => :d)"
        "   and (raw_text is not null or transcript is not null)"),
        {"d": int(days)})
    db.commit()
    return {"found": n, "purged": n, "dry": False}


def reconcile_lifecycle(db, confirm_ttl_minutes=15, executing_timeout_minutes=10, parsed_timeout_minutes=10):
    """Fail-closed lifecycle recovery for Command Center.

    Never replays a business side effect. Stale awaiting-confirm rows expire; a
    stale executing row is financially/operationally ambiguous and becomes a
    terminal failure that requires a fresh command. Stale parsed/confirmed rows
    likewise become explicit failures instead of invisible zombies.
    """
    # COMMAND_CENTER_ZOMBIE_RECOVERY_V1
    confirm_ttl = max(1, int(confirm_ttl_minutes or 15))
    exec_ttl = max(1, int(executing_timeout_minutes or 10))
    parsed_ttl = max(1, int(parsed_timeout_minutes or 10))
    out = {"expired_confirm": 0, "uncertain_executing": 0,
           "stale_preexecution": 0, "ids": []}

    expired = db.execute(text(
        "update boris_commands set status='expired', error_code='expired'"
        " where status='awaiting_confirm'"
        "   and coalesce(expires_at, created_at + make_interval(mins => :m)) <= now()"
        " returning command_id"), {"m": confirm_ttl}).fetchall()
    db.commit()
    out["expired_confirm"] = len(expired)
    out["ids"].extend(str(r[0]) for r in expired)
    for r in expired:
        log_event(db, r[0], "expired_by_maintenance", payload={"reason": "confirmation_timeout"})

    # Once execution was claimed, a process death may have happened before OR
    # after the real side effect. Never auto-retry this state.
    uncertain = db.execute(text(
        "update boris_commands set status='failed', error_code='executor_outcome_uncertain'"
        " where status='executing'"
        "   and coalesce(confirmed_at, created_at) < now() - make_interval(mins => :m)"
        " returning command_id"), {"m": exec_ttl}).fetchall()
    db.commit()
    out["uncertain_executing"] = len(uncertain)
    out["ids"].extend(str(r[0]) for r in uncertain)
    for r in uncertain:
        log_event(db, r[0], "failed_by_maintenance",
                  payload={"reason": "executor_outcome_uncertain", "auto_replay": False})

    # parsed/confirmed are before the executor claim; they should last seconds,
    # not hours. Mark them failed rather than silently running old intent later.
    stale = db.execute(text(
        "update boris_commands set status='failed', error_code='stale_preexecution'"
        " where status in ('parsed','confirmed')"
        "   and created_at < now() - make_interval(mins => :m)"
        " returning command_id"), {"m": parsed_ttl}).fetchall()
    db.commit()
    out["stale_preexecution"] = len(stale)
    out["ids"].extend(str(r[0]) for r in stale)
    for r in stale:
        log_event(db, r[0], "failed_by_maintenance",
                  payload={"reason": "stale_preexecution", "auto_replay": False})
    return out
