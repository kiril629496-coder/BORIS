# -*- coding: utf-8 -*-
"""Ядро реактивации: статусы, переходы, журнал, нормализация, лимиты.
Ничего не отправляет в Avito. Транспорт подключается отдельным этапом."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, text

# ---------------------------------------------------------------- справочники

CAND_ACTIVE = ("candidate", "needs_review", "approved", "scheduled",
               "sending", "sent", "cooldown", "delivery_unknown")

CAND_TRANSITIONS = {
    "candidate":        ("needs_review", "approved", "not_eligible", "excluded",
                         "do_not_contact", "expired", "cancelled"),
    "needs_review":     ("approved", "not_eligible", "excluded", "do_not_contact", "cancelled"),
    "approved":         ("scheduled", "cancelled", "excluded", "do_not_contact"),
    "scheduled":        ("sending", "cancelled", "replied", "manager_taken_over"),
    "sending":          ("sent", "delivery_unknown", "failed_final", "cancelled"),
    "sent":             ("replied", "converted", "cooldown", "manager_taken_over",
                         "do_not_contact", "expired"),
    "cooldown":         ("approved", "replied", "converted", "manager_taken_over",
                         "expired", "do_not_contact"),
    "delivery_unknown": ("sent", "cancelled", "failed_final"),
    "replied":          ("converted",),
    "not_eligible":     (),
    "converted":        (),
    "manager_taken_over": (),
    "excluded":         (),
    "do_not_contact":   (),
    "cancelled":        (),
    "expired":          (),
    "failed_final":     (),
}

MSG_TRANSITIONS = {
    "draft":            ("ready", "cancelled"),
    "ready":            ("scheduled", "cancelled"),
    "scheduled":        ("sending", "cancelled"),
    "sending":          ("sent", "failed", "delivery_unknown"),
    "failed":           ("scheduled", "cancelled"),
    "delivery_unknown": ("sent", "cancelled"),
    "sent":             (),
    "cancelled":        (),
}

EVENTS = (
    "candidate_created", "analyzed", "analysis_failed", "not_eligible", "needs_review",
    "approved", "message_generated", "message_edited", "scheduled", "dry_run_pass",
    "message_ready", "send_started", "sent", "send_failed", "delivery_unclear",
    "delivery_resolved",
    "client_replied", "manager_takeover", "converted", "cancelled", "excluded",
    "do_not_contact", "cooldown_started", "expired", "sync_failed", "limit_blocked",
)

REASONS = ("no_phone", "no_reply", "price_requested", "estimate_sent",
           "asked_to_contact_later", "checkout_abandoned", "old_customer", "manual", "other")

REASON_PRIORITY = ("price_requested", "no_phone", "no_reply")


def can_go(kind, frm, to):
    table = CAND_TRANSITIONS if kind == "candidate" else MSG_TRANSITIONS
    return to in table.get(frm, ())


# ------------------------------------------------------------- нормализация

def norm_text(s):
    """Полная нормализация перед хэшем. Только для сверки, не для показа."""
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(re.sub(r"[ \t]+", " ", ln).rstrip() for ln in s.split("\n"))
    return s.strip()


def text_hash(s):
    return hashlib.sha256(norm_text(s).encode("utf-8")).hexdigest()


def idem_key(candidate_id, attempt_number):
    return "reactivation:%s:%s" % (int(candidate_id), int(attempt_number))


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------- журнал

def log_event(db, candidate_id, event, message_id=None, from_status=None,
              to_status=None, actor_type="system", actor_id=None, channel=None,
              payload=None, meta=None):
    """Пишет строку в append-only журнал. Коммит делает вызывающий."""
    db.execute(text(
        "INSERT INTO reactivation_events (candidate_id, message_id, event, from_status,"
        " to_status, channel, actor_type, actor_id, payload, metadata)"
        " VALUES (:c, :m, :e, :f, :t, :ch, :at, :ai, :p, CAST(:meta AS JSONB))"),
        {"c": candidate_id, "m": message_id, "e": event, "f": from_status, "t": to_status,
         "ch": channel, "at": actor_type, "ai": (str(actor_id) if actor_id is not None else None),
         "p": payload, "meta": (json.dumps(meta, ensure_ascii=False) if meta else None)})


# ------------------------------------------------------------ смена статусов

def _set_status(db, table, row_id, to_status, allowed_from, kind):
    for frm in allowed_from:
        if not can_go(kind, frm, to_status):
            raise ValueError("переход %s -> %s запрещён картой" % (frm, to_status))
    prev = db.execute(text("SELECT status FROM %s WHERE id=:i" % table), {"i": row_id}).fetchone()
    if not prev:
        return None
    stmt = text(
        "UPDATE %s SET status=:s, updated_at=now() WHERE id=:i AND status IN :froms"
        " RETURNING id" % table).bindparams(bindparam("froms", expanding=True))
    row = db.execute(stmt, {"s": to_status, "i": row_id,
                            "froms": list(allowed_from)}).fetchone()
    return (prev[0], to_status) if row else None


def set_candidate_status(db, candidate_id, to_status, allowed_from, event,
                         actor_type="system", actor_id=None, meta=None):
    moved = _set_status(db, "reactivation_candidates", candidate_id, to_status,
                        allowed_from, "candidate")
    if not moved:
        return False
    log_event(db, candidate_id, event, from_status=moved[0], to_status=to_status,
              actor_type=actor_type, actor_id=actor_id, meta=meta)
    return True


def set_message_status(db, message_id, to_status, allowed_from, event,
                       actor_type="system", actor_id=None, meta=None):
    row = db.execute(text("SELECT candidate_id FROM reactivation_messages WHERE id=:i"),
                     {"i": message_id}).fetchone()
    if not row:
        return False
    moved = _set_status(db, "reactivation_messages", message_id, to_status,
                        allowed_from, "message")
    if not moved:
        return False
    log_event(db, row[0], event, message_id=message_id, from_status=moved[0],
              to_status=to_status, actor_type=actor_type, actor_id=actor_id, meta=meta)
    return True


# --------------------------------------------------------------- кандидаты

def active_candidate(db, account_id, avito_chat_id):
    stmt = text(
        "SELECT id, status, cycle_no, watch_since FROM reactivation_candidates"
        " WHERE account_id=:a AND avito_chat_id=:c AND status IN :act"
        " ORDER BY id DESC LIMIT 1").bindparams(bindparam("act", expanding=True))
    return db.execute(stmt, {"a": account_id, "c": avito_chat_id,
                             "act": list(CAND_ACTIVE)}).fetchone()


def is_blocked_forever(db, account_id, avito_chat_id):
    """do_not_contact и excluded действуют на все будущие циклы диалога."""
    row = db.execute(text(
        "SELECT 1 FROM reactivation_candidates WHERE account_id=:a AND avito_chat_id=:c"
        " AND (do_not_contact = true OR status IN ('do_not_contact','excluded')) LIMIT 1"),
        {"a": account_id, "c": avito_chat_id}).fetchone()
    return bool(row)


def create_candidate(db, account_id, avito_chat_id, primary_reason, matched_reasons,
                     watch_since, summary=None, evidence=None):
    """Идемпотентно. Активный кандидат по диалогу может быть только один —
    это держит частичный уникальный индекс uq_react_cand_active."""
    if is_blocked_forever(db, account_id, avito_chat_id):
        return None, "blocked"
    exists = active_candidate(db, account_id, avito_chat_id)
    if exists:
        return exists[0], "exists"
    cyc = db.execute(text(
        "SELECT coalesce(max(cycle_no), 0) + 1 FROM reactivation_candidates"
        " WHERE account_id=:a AND avito_chat_id=:c"), {"a": account_id, "c": avito_chat_id}).scalar()
    row = db.execute(text(
        "INSERT INTO reactivation_candidates (account_id, avito_chat_id, cycle_no,"
        " primary_reason, matched_reasons, status, watch_since, summary, evidence_message_ids)"
        " VALUES (:a, :c, :cy, :r, CAST(:mr AS JSONB), 'candidate', :ws, :su, CAST(:ev AS JSONB))"
        " RETURNING id"),
        {"a": account_id, "c": avito_chat_id, "cy": cyc, "r": primary_reason,
         "mr": json.dumps(matched_reasons or [], ensure_ascii=False), "ws": watch_since,
         "su": summary, "ev": json.dumps(evidence or [], ensure_ascii=False)}).fetchone()
    log_event(db, row[0], "candidate_created", to_status="candidate",
              meta={"primary_reason": primary_reason, "matched_reasons": matched_reasons,
                    "cycle_no": cyc})
    return row[0], "created"


# ---------------------------------------------------------------- сообщения

def ensure_message(db, candidate_id, account_id, avito_chat_id, attempt_number, generated_text):
    """Один кандидат + номер попытки = одна строка. Повторный вызов ничего не создаёт."""
    key = idem_key(candidate_id, attempt_number)
    row = db.execute(text(
        "INSERT INTO reactivation_messages (candidate_id, account_id, avito_chat_id,"
        " attempt_number, idempotency_key, generated_text, final_text, text_hash, status)"
        " VALUES (:c, :a, :ch, :n, :k, :g, :g, :h, 'draft')"
        " ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"),
        {"c": candidate_id, "a": account_id, "ch": avito_chat_id, "n": attempt_number,
         "k": key, "g": generated_text, "h": text_hash(generated_text)}).fetchone()
    if row:
        log_event(db, candidate_id, "message_generated", message_id=row[0], to_status="draft")
        return row[0], True
    old = db.execute(text("SELECT id FROM reactivation_messages WHERE idempotency_key=:k"),
                     {"k": key}).fetchone()
    return (old[0] if old else None), False


def set_final_text(db, message_id, new_text, actor_type="user", actor_id=None):
    row = db.execute(text(
        "UPDATE reactivation_messages SET edited_text=:t, final_text=:t, text_hash=:h,"
        " updated_at=now() WHERE id=:i AND status IN ('draft','ready') RETURNING candidate_id"),
        {"t": new_text, "h": text_hash(new_text), "i": message_id}).fetchone()
    if not row:
        return False
    log_event(db, row[0], "message_edited", message_id=message_id,
              actor_type=actor_type, actor_id=actor_id)
    return True


def schedule_message(db, message_id, when):
    row = db.execute(text(
        "WITH old AS (SELECT id, status FROM reactivation_messages WHERE id=:i)"
        " UPDATE reactivation_messages m SET status='scheduled', scheduled_at=:w,"
        " updated_at=now() FROM old WHERE m.id = old.id AND m.status IN ('ready','failed')"
        " RETURNING m.candidate_id, old.status"),
        {"w": when, "i": message_id}).fetchone()
    if not row:
        return False
    db.execute(text(
        "UPDATE reactivation_candidates SET status='scheduled', updated_at=now()"
        " WHERE id=:c AND status IN ('approved','cooldown')"), {"c": row[0]})
    log_event(db, row[0], "scheduled", message_id=message_id, from_status=row[1],
              to_status="scheduled", meta={"scheduled_at": str(when)})
    return True


def claim_for_send(db, message_id, worker):
    """Атомарный захват. Пустой результат = уже взято другим процессом."""
    row = db.execute(text(
        "UPDATE reactivation_messages SET status='sending', locked_at=now(), locked_by=:w,"
        " updated_at=now() WHERE id=:i AND status='scheduled' AND locked_at IS NULL"
        " RETURNING candidate_id, final_text, account_id, avito_chat_id"),
        {"i": message_id, "w": str(worker)[:64]}).fetchone()
    if not row:
        return None
    db.execute(text(
        "UPDATE reactivation_candidates SET status='sending', updated_at=now()"
        " WHERE id=:c AND status='scheduled'"), {"c": row[0]})
    log_event(db, row[0], "send_started", message_id=message_id,
              from_status="scheduled", to_status="sending", actor_id=worker)
    return row


def mark_sent(db, message_id, avito_message_id=None, sent_at=None):
    """ЕДИНСТВЕННОЕ место, где поднимается watch_since кандидата."""
    ts = sent_at or _now()
    row = db.execute(text(
        "WITH old AS (SELECT id, status FROM reactivation_messages WHERE id=:i)"
        " UPDATE reactivation_messages m SET status='sent', sent_at=:ts, avito_message_id=:am,"
        " locked_at=NULL, locked_by=NULL, updated_at=now() FROM old"
        " WHERE m.id = old.id AND m.status IN ('sending','delivery_unknown')"
        " RETURNING m.candidate_id, m.attempt_number, old.status"),
        {"i": message_id, "ts": ts, "am": avito_message_id}).fetchone()
    if not row:
        return False
    db.execute(text(
        "UPDATE reactivation_candidates SET status='sent', attempts=attempts+1,"
        " watch_since=:ts, updated_at=now() WHERE id=:c AND status IN ('sending','delivery_unknown')"),
        {"ts": ts, "c": row[0]})
    log_event(db, row[0], "sent", message_id=message_id, from_status=row[2],
              to_status="sent", meta={"attempt": row[1], "avito_message_id": avito_message_id})
    return True


def mark_send_failed(db, message_id, error_code, error_message=None):
    row = db.execute(text(
        "UPDATE reactivation_messages SET status='failed', error_code=:ec, error_message=:em,"
        " locked_at=NULL, locked_by=NULL, updated_at=now()"
        " WHERE id=:i AND status='sending' RETURNING candidate_id"),
        {"i": message_id, "ec": error_code, "em": error_message}).fetchone()
    if not row:
        return False
    db.execute(text(
        "UPDATE reactivation_candidates SET status='scheduled', updated_at=now()"
        " WHERE id=:c AND status='sending'"), {"c": row[0]})
    log_event(db, row[0], "send_failed", message_id=message_id,
              from_status="sending", to_status="failed", meta={"error_code": error_code})
    return True


def mark_delivery_unknown(db, message_id, note=None):
    """Avito мог принять сообщение до падения процесса. Автоповтор запрещён."""
    row = db.execute(text(
        "UPDATE reactivation_messages SET status='delivery_unknown', locked_at=NULL,"
        " locked_by=NULL, updated_at=now() WHERE id=:i AND status='sending'"
        " RETURNING candidate_id"), {"i": message_id}).fetchone()
    if not row:
        return False
    db.execute(text(
        "UPDATE reactivation_candidates SET status='delivery_unknown', updated_at=now()"
        " WHERE id=:c AND status='sending'"), {"c": row[0]})
    log_event(db, row[0], "delivery_unclear", message_id=message_id,
              from_status="sending", to_status="delivery_unknown", payload=note)
    return True


# ------------------------------------------------------------------ лимиты

def _billing(db, account_id):
    row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='billing'"),
                     {"a": account_id}).fetchone()
    if not row or not row[0]:
        return {}
    val = row[0]
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return {}


def _is_own(db, account_id):
    row = db.execute(text("SELECT coalesce(is_own, false) FROM accounts WHERE account_id=:a"),
                     {"a": account_id}).fetchone()
    return bool(row and row[0])


def _parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def reactivation_balance(db, account_id):
    """Период берётся из пакета МОПа, расход — по sent_at, а не по текущему статусу."""
    b = _billing(db, account_id)
    start, until = _parse_dt(b.get("manager_period_start")), _parse_dt(b.get("manager_paid_until"))
    if not start or not until:
        return {"active": False, "block": "no_period", "purchased": 0, "used": 0, "left": 0}
    purchased = int(b.get("reactivation_purchased", 0) or 0)
    used = db.execute(text(
        "SELECT count(*) FROM reactivation_messages WHERE account_id=:a"
        " AND sent_at IS NOT NULL AND sent_at >= :s AND sent_at < :u"),
        {"a": account_id, "s": start, "u": until}).scalar() or 0
    left = purchased - used
    if _is_own(db, account_id):
        return {"active": True, "block": None, "purchased": purchased, "used": used,
                "left": 999999, "own": True, "period_start": start, "paid_until": until}
    if _now() >= until:
        return {"active": False, "block": "period_over", "purchased": purchased,
                "used": used, "left": left}
    return {"active": left > 0, "block": (None if left > 0 else "limit_reached"),
            "purchased": purchased, "used": used, "left": left,
            "period_start": start, "paid_until": until}


def used_today(db, account_id, tz_offset_hours=3):
    since = _now().astimezone(timezone(timedelta(hours=tz_offset_hours)))
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    return db.execute(text(
        "SELECT count(*) FROM reactivation_messages WHERE account_id=:a"
        " AND sent_at IS NOT NULL AND sent_at >= :s"), {"a": account_id, "s": since}).scalar() or 0


def settings_for(db, account_id):
    row = db.execute(text(
        "SELECT enabled, mode, reasons_enabled, daily_limit, min_interval_minutes,"
        " max_interval_minutes, max_attempts_per_dialog, attempts_window_days, cooldown_days,"
        " allowed_hours_from, allowed_hours_to, timezone, disabled_reason"
        " FROM reactivation_settings WHERE account_id=:a"), {"a": account_id}).fetchone()
    if not row:
        return None
    keys = ("enabled", "mode", "reasons_enabled", "daily_limit", "min_interval_minutes",
            "max_interval_minutes", "max_attempts_per_dialog", "attempts_window_days",
            "cooldown_days", "allowed_hours_from", "allowed_hours_to", "timezone",
            "disabled_reason")
    return dict(zip(keys, row))


def enabled_accounts(db):
    return [r[0] for r in db.execute(text(
        "SELECT account_id FROM reactivation_settings WHERE enabled = true ORDER BY account_id"))]
