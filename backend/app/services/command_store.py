# -*- coding: utf-8 -*-
"""Чтение и запись boris_commands. Тексты маскируются ПЕРЕД записью."""
import datetime
import json

from sqlalchemy import text

from app.services.command_redact import redact_text

FIELDS = ("command_id", "user_id", "billing_owner_kind", "billing_owner_id",
          "account_id", "source", "raw_text", "transcript", "page_context",
          "status", "intent", "confidence",
          "entities", "operation", "risk_level", "permission", "units",
          "needs_confirmation", "human_summary", "clarify_question",
          "error_code", "expires_at", "created_at", "executed_at",
          "duration_ms", "units_charged_at", "result", "entity_type", "entity_id")


def create(db, command_id, user_id, owner_kind, owner_id, account_id, source,
           raw_text=None, transcript=None, page_context=None,
           entity_type=None, entity_id=None, locale="ru", device=None,
           audio_seconds=None, audio_format=None, asr_model=None):
    # command_id is the economic/business idempotency key for Command Center.
    # A browser retry after an ambiguous network response must never create or
    # execute the same command twice.  Do not overwrite the existing row: the
    # caller reloads it and returns the already-known state/result.
    row = db.execute(text(
        "insert into boris_commands (command_id, user_id, billing_owner_kind,"
        " billing_owner_id, account_id, source, raw_text, transcript,"
        " page_context, entity_type, entity_id, locale, device,"
        " audio_seconds, audio_format, asr_model, status, units, created_at)"
        " values (cast(:cid as uuid), :u, :k, :o, :a, :s, :rt, :tr,"
        " cast(:pc as jsonb), :et, :ei, :lo, :dv, :as_, :af, :am, 'parsed', 0, :ts)"
        " on conflict (command_id) do nothing returning command_id"),
        {"cid": str(command_id), "u": user_id, "k": owner_kind, "o": owner_id,
         "a": account_id, "s": source,
         "rt": redact_text(raw_text, 4000), "tr": redact_text(transcript, 4000),
         "pc": json.dumps(page_context, ensure_ascii=False) if page_context else None,
         "et": entity_type, "ei": entity_id, "lo": locale, "dv": device,
         "as_": audio_seconds, "af": audio_format, "am": asr_model,
         "ts": datetime.datetime.utcnow()}).fetchone()
    db.commit()
    return bool(row)


def save_parse(db, command_id, intent=None, confidence=None, entities=None,
               summary=None, clarify=None, snapshot=None,
               needs_confirmation=False, status="parsed",
               error_code=None, expires_at=None):
    snap = snapshot or {}
    db.execute(text(
        "update boris_commands set intent=:i, confidence=:cf,"
        " entities=cast(:en as jsonb), human_summary=:hs, clarify_question=:cq,"
        " operation=:op, risk_level=:rl, permission=:pm, units=:un,"
        " needs_confirmation=:nc, status=:st, error_code=:ec, expires_at=:ex"
        " where command_id=cast(:cid as uuid)"),
        {"i": intent, "cf": confidence,
         "en": json.dumps(entities or {}, ensure_ascii=False),
         "hs": redact_text(summary, 500), "cq": redact_text(clarify, 500),
         "op": snap.get("operation"), "rl": snap.get("risk_level"),
         "pm": snap.get("permission"), "un": int(snap.get("units") or 0),
         "nc": bool(needs_confirmation), "st": status, "ec": error_code,
         "ex": expires_at, "cid": str(command_id)})
    db.commit()


def set_status(db, command_id, status, error_code=None):
    db.execute(text(
        "update boris_commands set status=:st, error_code=coalesce(:ec, error_code)"
        " where command_id=cast(:cid as uuid)"),
        {"st": status, "ec": error_code, "cid": str(command_id)})
    db.commit()


def load(db, command_id):
    row = db.execute(text(
        "select " + ", ".join(FIELDS) + " from boris_commands"
        " where command_id=cast(:cid as uuid)"),
        {"cid": str(command_id)}).fetchone()
    if not row:
        return None
    return dict(zip(FIELDS, row))


def history(db, user_id, limit=20):
    rows = db.execute(text(
        "select command_id, created_at, source, intent, operation, status,"
        " units, human_summary, error_code, account_id from boris_commands"
        " where user_id=:u order by created_at desc limit :l"),
        {"u": user_id, "l": int(limit)}).fetchall()
    return [{"command_id": str(r[0]),
             "created_at": r[1].isoformat() if r[1] else None,
             "source": r[2], "intent": r[3], "operation": r[4],
             "status": r[5], "units": r[6], "summary": r[7],
             "error_code": r[8], "account_id": r[9]} for r in rows]
