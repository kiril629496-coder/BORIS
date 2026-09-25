# -*- coding: utf-8 -*-
"""Боевой runtime реактивации поверх существующего МОП/Avito transport.

Не создаёт второй мессенджер: генерация использует reactivation_draft,
отправка — app.api.messenger.send_message. Все переходы/идемпотентность —
reactivation_core.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

import app.reactivation_core as rc
from app.services.exception_observability import observe_suppressed


def _settings(db, account_id):
    s = rc.settings_for(db, account_id)
    if s:
        return s
    return {
        "enabled": True, "mode": "recommend", "reasons_enabled": ["price_requested", "no_reply"],
        "daily_limit": 10, "min_interval_minutes": 5, "max_interval_minutes": 15,
        "max_attempts_per_dialog": 2, "attempts_window_days": 90, "cooldown_days": 7,
        "allowed_hours_from": 10, "allowed_hours_to": 20, "timezone": "Europe/Moscow",
        "disabled_reason": None, "updated_at": None,
    }


def _mop_active(account_id):
    try:
        from app.api.messenger import get_manager_balance
        return bool((get_manager_balance(account_id) or {}).get("active"))
    except Exception:
        return False


def _within_hours(settings):
    try:
        now = datetime.now(ZoneInfo(settings.get("timezone") or "Europe/Moscow"))
    except Exception:
        now = datetime.now(timezone.utc)
    start = int(settings.get("allowed_hours_from") or 10)
    end = int(settings.get("allowed_hours_to") or 20)
    return start <= now.hour < end


def _latest_dialog_state(db, account_id, chat_id):
    return db.execute(text("""
        SELECT id, lower(coalesce(direction,'')), to_timestamp(avito_created_at), text
          FROM messenger_messages
         WHERE account_id=:a AND avito_chat_id=:c
           AND coalesce(msg_type,'') <> 'system'
           AND btrim(coalesce(text,'')) <> ''
         ORDER BY avito_created_at DESC, id DESC LIMIT 1
    """), {"a": account_id, "c": chat_id}).fetchone()


def _customer_declined(db, account_id, chat_id):
    """Жёсткий стоп для явного отказа клиента — даже при включённом автопилоте."""
    row = db.execute(text("""
        SELECT lower(coalesce(text,''))
          FROM messenger_messages
         WHERE account_id=:a AND avito_chat_id=:c
           AND lower(coalesce(direction,'')) IN ('in','incoming')
           AND coalesce(msg_type,'') <> 'system'
           AND btrim(coalesce(text,'')) <> ''
         ORDER BY avito_created_at DESC,id DESC LIMIT 1
    """), {"a": account_id, "c": chat_id}).fetchone()
    t = str(row[0] or "").lower() if row else ""
    hard_stop = (
        "не актуал", "неактуал", "не интерес", "неинтерес", "не нужно", "не надо",
        "уже куп", "уже заказ", "уже наш", "не пиш", "не звон", "удалите мой",
    )
    return any(x in t for x in hard_stop)


def _candidate_message(db, candidate_id):
    return db.execute(text("""
        SELECT m.id,m.account_id,m.avito_chat_id,m.attempt_number,m.final_text,m.status,
               m.created_at,c.status,c.attempts,c.primary_reason,c.summary
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
         WHERE m.candidate_id=:c AND m.status <> 'cancelled'
         ORDER BY m.attempt_number DESC,m.id DESC LIMIT 1
    """), {"c": candidate_id}).mappings().first()


def generate_for_candidate(db, candidate_id):
    """Идемпотентно создаёт персональный ready-черновик для кандидата."""
    c = db.execute(text("""
        SELECT id,account_id,avito_chat_id,status,attempts,primary_reason,summary
          FROM reactivation_candidates WHERE id=:i
    """), {"i": candidate_id}).mappings().first()
    if not c or c["status"] not in ("candidate", "needs_review", "approved", "cooldown"):
        return {"ok": False, "reason": "candidate_not_active"}
    existing = _candidate_message(db, candidate_id)
    if existing and existing["status"] in ("draft", "ready", "scheduled", "sending"):
        return {"ok": True, "message_id": existing["id"], "created": False, "status": existing["status"]}
    # После первой успешной попытки новый текст разрешён только после перехода
    # кандидата в cooldown. Так max_attempts_per_dialog действительно работает,
    # но повторное сообщение никогда не создаётся сразу после первого.
    if existing and existing["status"] == "sent" and c["status"] != "cooldown":
        return {"ok": True, "message_id": existing["id"], "created": False, "status": existing["status"]}
    if not _mop_active(c["account_id"]):
        return {"ok": False, "reason": "mop_inactive"}
    settings = _settings(db, c["account_id"])
    if not settings.get("enabled", True):
        return {"ok": False, "reason": "reactivation_disabled"}
    # Do not spend an AI generation on a contour that BORIS already knows
    # cannot read Avito Messenger history. The capability probe is read-only
    # and refreshed by the scheduler; once Avito access is restored the next
    # normal tick may generate the draft. Existing ready drafts are preserved.
    try:
        from app.reactivation_avito_health import effective_capability
        cap = effective_capability(db, c["account_id"])
    except Exception:
        cap = {}
    if str(cap.get("classification") or "") == "avito_messenger_subscription_required":
        return {"ok": False, "reason": "transport_unavailable"}
    # CHAT_SCOPED_PAID_GENERATION_GUARD_V2: use the fresh minute inbox sweep
    # first. It already checked up to 50 chats and records exact 402 chat ids, so
    # known-blocked candidates spend neither AI money nor another Avito request.
    # Unknown chats on a partial account still get one exact read-only preflight.
    if str(cap.get("classification") or "") == "avito_messenger_history_partial_402":
        try:
            from app.reactivation_avito_health import chat_transport_blocked, _classify_error
            if chat_transport_blocked(db, c["account_id"], c["avito_chat_id"]):
                return {"ok": False, "reason": "transport_unavailable", "scope": "chat", "evidence": "minute_inbox_sync"}
            from app.api.messenger import sync_chat_messages
            chat_probe = sync_chat_messages(c["account_id"], c["avito_chat_id"])
            if (chat_probe or {}).get("status") != "ok":
                cls = _classify_error((chat_probe or {}).get("message"))
                if cls == "avito_messenger_subscription_required":
                    return {"ok": False, "reason": "transport_unavailable", "scope": "chat"}
                return {"ok": False, "reason": "transport_preflight_failed", "scope": "chat"}
        except Exception:
            return {"ok": False, "reason": "transport_preflight_failed", "scope": "chat"}
    attempt = int(c["attempts"] or 0) + 1
    if attempt > int(settings.get("max_attempts_per_dialog") or 2):
        return {"ok": False, "reason": "attempt_limit"}
    from app.reactivation_draft import generate
    out = generate(db, c["account_id"], c["avito_chat_id"], reason=c["primary_reason"],
                   summary=c["summary"])
    if not out.get("ok"):
        return {"ok": False, "reason": out.get("error") or "generation_failed"}
    draft = out.get("draft") or {}
    if not draft.get("ok_to_show") or not (draft.get("message") or "").strip():
        return {"ok": False, "reason": "draft_safety_failed", "problems": draft.get("problems") or []}
    mid, created = rc.ensure_message(db, candidate_id, c["account_id"], c["avito_chat_id"],
                                     attempt, draft["message"])
    if created and mid:
        provider_fallback = bool(out.get("provider_fallback"))
        rc.set_message_status(
            db, mid, "ready", ("draft",), "message_ready",
            meta={"provider_fallback": provider_fallback, "auto_eligible": not provider_fallback},
        )
    db.commit()
    return {"ok": True, "message_id": mid, "created": bool(created), "status": "ready"}


def _auto_revalidated_after_boundary(db, message_id, boundary):
    """Durable proof that an old draft was freshly rechecked after auto activation."""
    if not boundary:
        return True
    return bool(db.execute(text("""
        SELECT 1 FROM reactivation_events
         WHERE message_id=:m AND event='message_ready' AND at>=:b
           AND coalesce(metadata->>'auto_revalidated','false')='true'
           AND coalesce(metadata->>'auto_eligible','false')='true'
         ORDER BY id DESC LIMIT 1
    """), {"m": int(message_id), "b": boundary}).fetchone())


def ready_queue_truth(db, account_id):
    """Read-only truth for ready drafts.

    The ready status is only storage state. It must not be presented as
    equivalent to "autopilot may send it". This partitions the queue into:
    auto_sendable, confirmation_required, awaiting_revalidation and
    safety_blocked. Time-of-day, daily capacity, MOP entitlement and live
    Avito transport are reported separately by callers.
    """
    row = db.execute(text("""
        WITH ready AS (
            SELECT
                m.id,
                m.created_at,
                c.status AS candidate_status,
                coalesce(c.do_not_contact,false) AS do_not_contact,
                coalesce(c.attempts,0) AS attempts,
                coalesce(s.enabled,true) AS enabled,
                coalesce(s.mode,'recommend') AS mode,
                s.updated_at AS auto_boundary,
                coalesce(s.max_attempts_per_dialog,2) AS max_attempts,
                EXISTS (
                    SELECT 1 FROM reactivation_events e
                     WHERE e.message_id=m.id
                       AND e.event='message_ready'
                       AND coalesce(e.metadata->>'auto_eligible','false')='true'
                ) AS auto_eligible,
                EXISTS (
                    SELECT 1 FROM reactivation_events e
                     WHERE e.message_id=m.id
                       AND e.event='message_edited'
                ) AS edited,
                EXISTS (
                    SELECT 1 FROM reactivation_events e
                     WHERE e.message_id=m.id
                       AND e.event='message_ready'
                       AND coalesce(e.metadata->>'provider_fallback','false')='true'
                ) AS provider_fallback,
                (
                    s.updated_at IS NULL
                    OR m.created_at >= s.updated_at
                    OR EXISTS (
                        SELECT 1 FROM reactivation_events e
                         WHERE e.message_id=m.id
                           AND e.event='message_ready'
                           AND e.at>=s.updated_at
                           AND coalesce(e.metadata->>'auto_revalidated','false')='true'
                           AND coalesce(e.metadata->>'auto_eligible','false')='true'
                    )
                ) AS authority_ok
            FROM reactivation_messages m
            JOIN reactivation_candidates c ON c.id=m.candidate_id
            LEFT JOIN reactivation_settings s ON s.account_id=m.account_id
            WHERE m.account_id=:a AND m.status='ready'
        ),
        classified AS (
            SELECT *,
                (
                    enabled
                    AND candidate_status IN ('candidate','needs_review','approved','scheduled','cooldown')
                    AND NOT do_not_contact
                    AND attempts < max_attempts
                ) AS active_safe,
                (
                    enabled
                    AND candidate_status IN ('candidate','approved','scheduled','cooldown')
                    AND NOT do_not_contact
                    AND attempts < max_attempts
                ) AS scheduler_safe
            FROM ready
        )
        SELECT
            count(*) AS ready_messages,
            count(*) FILTER (
                WHERE scheduler_safe
                  AND mode='auto'
                  AND auto_eligible
                  AND NOT edited
                  AND authority_ok
            ) AS auto_sendable,
            count(*) FILTER (
                WHERE active_safe
                  AND (
                      candidate_status='needs_review'
                      OR mode<>'auto'
                      OR NOT auto_eligible
                      OR edited
                  )
            ) AS confirmation_required,
            count(*) FILTER (
                WHERE scheduler_safe
                  AND mode='auto'
                  AND auto_eligible
                  AND NOT edited
                  AND NOT authority_ok
            ) AS awaiting_revalidation,
            count(*) FILTER (WHERE NOT active_safe) AS safety_blocked,
            count(*) FILTER (
                WHERE active_safe
                  AND candidate_status<>'needs_review'
                  AND NOT edited
                  AND NOT auto_eligible
                  AND provider_fallback
            ) AS ai_fallback_requires_confirmation,
            count(*) FILTER (
                WHERE active_safe
                  AND candidate_status<>'needs_review'
                  AND NOT edited
                  AND NOT auto_eligible
                  AND NOT provider_fallback
            ) AS legacy_requires_refresh,
            count(*) FILTER (
                WHERE active_safe
                  AND candidate_status<>'needs_review'
                  AND edited
            ) AS edited_requires_confirmation,
            count(*) FILTER (
                WHERE active_safe
                  AND candidate_status='needs_review'
            ) AS needs_review_candidates,
            count(*) FILTER (
                WHERE active_safe
                  AND candidate_status<>'needs_review'
                  AND NOT edited
                  AND auto_eligible
                  AND mode<>'auto'
            ) AS recommend_mode_requires_confirmation
        FROM classified
    """), {"a": str(account_id)}).mappings().first() or {}

    # Runtime safety guards must be reflected in owner-facing truth too. The
    # aggregate SQL above intentionally classifies durable authority first;
    # current MOP qualification and exact historical duplicates are checked
    # against the small auto-authorized subset here.
    import json as _json_truth
    guarded = db.execute(text("""
        SELECT m.avito_chat_id,m.final_text,
               (SELECT s2.value FROM storage s2
                 WHERE s2.account_id=m.account_id
                   AND s2.key=('mop_qualification:' || m.avito_chat_id)
                 ORDER BY s2.id DESC LIMIT 1) AS qualification_json
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
          LEFT JOIN reactivation_settings s ON s.account_id=m.account_id
         WHERE m.account_id=:a AND m.status='ready'
           AND c.status IN ('candidate','approved','scheduled','cooldown')
           AND coalesce(c.do_not_contact,false)=false
           AND coalesce(c.attempts,0) < coalesce(s.max_attempts_per_dialog,2)
           AND coalesce(s.enabled,true)=true
           AND coalesce(s.mode,'recommend')='auto'
           AND EXISTS (
                SELECT 1 FROM reactivation_events e
                 WHERE e.message_id=m.id AND e.event='message_ready'
                   AND coalesce(e.metadata->>'auto_eligible','false')='true'
           )
           AND NOT EXISTS (
                SELECT 1 FROM reactivation_events e
                 WHERE e.message_id=m.id AND e.event='message_edited'
           )
           AND (
                s.updated_at IS NULL OR m.created_at>=s.updated_at OR EXISTS (
                    SELECT 1 FROM reactivation_events e
                     WHERE e.message_id=m.id AND e.event='message_ready'
                       AND e.at>=s.updated_at
                       AND coalesce(e.metadata->>'auto_revalidated','false')='true'
                       AND coalesce(e.metadata->>'auto_eligible','false')='true'
                )
           )
    """), {"a": str(account_id)}).fetchall()
    qualification_blocked = duplicate_blocked = 0
    customer_recontact_blocked = factual_safety_blocked = 0
    for chat_id, final_text, raw_q in guarded:
        try:
            q = _json_truth.loads(raw_q) if raw_q else {}
        except Exception:
            q = {}
        if _qualification_block_from_dict(q):
            qualification_blocked += 1
        elif _customer_promised_to_recontact(db, account_id, chat_id):
            customer_recontact_blocked += 1
        elif _draft_factual_safety_problems(db, account_id, chat_id, final_text):
            factual_safety_blocked += 1
        elif _duplicate_outgoing_blocked(db, account_id, chat_id, final_text):
            duplicate_blocked += 1
    raw_auto = int(row.get("auto_sendable") or 0)
    dynamic_holds = qualification_blocked + customer_recontact_blocked + factual_safety_blocked + duplicate_blocked
    auto_sendable = max(0, raw_auto - dynamic_holds)
    confirmation_required = int(row.get("confirmation_required") or 0)
    awaiting_revalidation = int(row.get("awaiting_revalidation") or 0)
    safety_blocked = int(row.get("safety_blocked") or 0)
    hold_total = confirmation_required + awaiting_revalidation + safety_blocked + dynamic_holds
    return {
        "ready_messages": int(row.get("ready_messages") or 0),
        "auto_sendable": auto_sendable,
        "auto_sendable_raw": raw_auto,
        "hold_total": hold_total,
        "qualification_blocked": qualification_blocked,
        "customer_recontact_blocked": customer_recontact_blocked,
        "factual_safety_blocked": factual_safety_blocked,
        "duplicate_blocked": duplicate_blocked,
        "confirmation_required": confirmation_required,
        "awaiting_revalidation": awaiting_revalidation,
        "safety_blocked": safety_blocked,
        "ai_fallback_requires_confirmation": int(row.get("ai_fallback_requires_confirmation") or 0),
        "legacy_requires_refresh": int(row.get("legacy_requires_refresh") or 0),
        "edited_requires_confirmation": int(row.get("edited_requires_confirmation") or 0),
        "needs_review_candidates": int(row.get("needs_review_candidates") or 0),
        "recommend_mode_requires_confirmation": int(row.get("recommend_mode_requires_confirmation") or 0),
    }


def _qualification_block_from_dict(q):
    if not isinstance(q, dict):
        return None
    if q.get("reactivation_candidate") is False:
        return "qualification_reactivation_disabled"
    if q.get("human_handoff") is True:
        return "human_handoff_pending"
    phone = bool(q.get("phone_received"))
    stage = str(q.get("qualification_stage") or "").upper()
    action = " ".join(str(q.get(k) or "") for k in ("target_action", "next_action", "handoff_reason")).lower()
    if phone and stage == "TARGET_ACTION" and any(x in action for x in (
        "связ", "позвон", "звон", "специалист", "менеджер", "коллег"
    )):
        return "target_action_pending"
    return None


def _auto_qualification_block_reason(db, account_id, chat_id):
    """Return a fail-closed reason when current MOP memory forbids auto-reactivation."""
    import json as _json
    try:
        raw = db.execute(text("""
            SELECT value FROM storage
             WHERE account_id=:a AND key=:k
             ORDER BY id DESC LIMIT 1
        """), {"a": str(account_id), "k": "mop_qualification:" + str(chat_id)}).scalar()
        q = _json.loads(raw) if raw else {}
    except Exception:
        return None
    return _qualification_block_from_dict(q)


def _duplicate_outgoing_blocked(db, account_id, chat_id, body):
    try:
        from app.reactivation_draft import is_duplicate_outgoing
        return bool(is_duplicate_outgoing(db, account_id, chat_id, body))
    except Exception:
        # Duplicate guard is safety-critical only when the check itself works;
        # do not turn an unrelated read error into a false claim of duplication.
        return False


def _auto_min_interval_blocked(db, account_id, settings):
    """True when autopilot must wait before another send for this account."""
    mins = max(0, int((settings or {}).get("min_interval_minutes") or 0))
    if mins <= 0:
        return False
    row = db.execute(text("""
        SELECT 1 FROM reactivation_messages
         WHERE account_id=:a AND sent_at IS NOT NULL
           AND sent_at > now() - (:mins * interval '1 minute')
         ORDER BY sent_at DESC LIMIT 1
    """), {"a": str(account_id), "mins": mins}).fetchone()
    return bool(row)


_CLIENT_RECONTACT_RE = re.compile(
    r"(?:\b(?:сам(?:а)?\s+)?(?:напишу|отпишусь|свяжусь)\b.{0,40}\b(?:позже|потом|как|когда|после)\b|"
    r"\b(?:как|когда|после).{0,55}\b(?:напишу|отпишусь|свяжусь)\b|"
    r"\b(?:узнаю|уточню|посмотрю|подумаю|решу|приедет|приеду|вернется|вернётся).{0,55}\b(?:напишу|отпишусь|свяжусь)\b)",
    re.I | re.S,
)


def _customer_promised_to_recontact(db, account_id, chat_id):
    """Customer explicitly took the next turn; autopilot must not chase them."""
    row = db.execute(text("""
        SELECT coalesce(text,'')
          FROM messenger_messages
         WHERE account_id=:a AND avito_chat_id=:c
           AND lower(coalesce(direction,'')) IN ('in','incoming')
           AND coalesce(msg_type,'') <> 'system'
           AND btrim(coalesce(text,'')) <> ''
         ORDER BY avito_created_at DESC,id DESC LIMIT 1
    """), {"a": str(account_id), "c": str(chat_id)}).fetchone()
    body = str(row[0] or "") if row else ""
    return bool(_CLIENT_RECONTACT_RE.search(body))


def _draft_factual_safety_problems(db, account_id, chat_id, body):
    """Re-run deterministic factual checks against the freshest stored dialog."""
    try:
        from app.reactivation_draft import verify
        rows = db.execute(text("""
            SELECT coalesce(text,'')
              FROM messenger_messages
             WHERE account_id=:a AND avito_chat_id=:c
               AND coalesce(msg_type,'') <> 'system'
               AND btrim(coalesce(text,'')) <> ''
             ORDER BY avito_created_at,id
        """), {"a": str(account_id), "c": str(chat_id)}).fetchall()
        dialog = "\n".join(str(r[0] or "") for r in rows)
        return list(verify(body, dialog) or [])
    except Exception:
        return ["fact_guard_unavailable"]


def preflight_send(db, message_id, explicit=False):
    m = db.execute(text("""
        SELECT m.id,m.candidate_id,m.account_id,m.avito_chat_id,m.status,m.final_text,
               m.created_at,c.status AS candidate_status,c.attempts
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
         WHERE m.id=:i
    """), {"i": message_id}).mappings().first()
    if not m:
        return False, "message_not_found", None
    if m["status"] not in ("ready", "scheduled", "failed"):
        return False, "wrong_message_status", m
    if not (m["final_text"] or "").strip():
        return False, "empty_text", m
    settings = _settings(db, m["account_id"])
    if not settings.get("enabled", True):
        return False, "reactivation_disabled", m
    if not _mop_active(m["account_id"]):
        return False, "mop_inactive", m
    if not explicit and settings.get("mode") != "auto":
        return False, "confirmation_required", m
    if not explicit:
        # REACTIVATION_AUTO_ACTIVATION_BOUNDARY_V1:
        # Switching an account from recommend -> auto must never flush the
        # accumulated historical ready queue. Only drafts created at/after the
        # current auto settings activation may be sent automatically.
        _auto_boundary = settings.get("updated_at")
        if (
            _auto_boundary
            and m.get("created_at")
            and m["created_at"] < _auto_boundary
            and not _auto_revalidated_after_boundary(db, message_id, _auto_boundary)
        ):
            return False, "auto_activation_boundary", m
        auto_ok = db.execute(text("""
            SELECT 1 FROM reactivation_events
             WHERE message_id=:m AND event='message_ready'
               AND coalesce(metadata->>'auto_eligible','false')='true'
             ORDER BY id DESC LIMIT 1
        """), {"m": message_id}).fetchone()
        edited = db.execute(text("""
            SELECT 1 FROM reactivation_events
             WHERE message_id=:m AND event='message_edited' LIMIT 1
        """), {"m": message_id}).fetchone()
        if not auto_ok or edited:
            return False, "ai_fallback_requires_confirmation", m
        qualification_block = _auto_qualification_block_reason(
            db, m["account_id"], m["avito_chat_id"]
        )
        if qualification_block:
            return False, qualification_block, m
        if _customer_promised_to_recontact(db, m["account_id"], m["avito_chat_id"]):
            return False, "customer_promised_to_recontact", m
        if _draft_factual_safety_problems(db, m["account_id"], m["avito_chat_id"], m["final_text"]):
            return False, "draft_factual_safety", m
    if _duplicate_outgoing_blocked(
        db, m["account_id"], m["avito_chat_id"], m["final_text"]
    ):
        return False, "duplicate_outgoing_text", m
    if not _within_hours(settings):
        return False, "outside_allowed_hours", m
    if rc.used_today(db, m["account_id"]) >= int(settings.get("daily_limit") or 10):
        return False, "daily_limit", m
    if not explicit and _auto_min_interval_blocked(db, m["account_id"], settings):
        return False, "min_interval", m
    if int(m["attempts"] or 0) >= int(settings.get("max_attempts_per_dialog") or 2):
        return False, "attempt_limit", m
    blocked = db.execute(text("""
        SELECT 1 FROM reactivation_candidates
         WHERE id=:c AND (do_not_contact=true OR status IN ('do_not_contact','excluded','manager_taken_over','replied','converted'))
    """), {"c": m["candidate_id"]}).fetchone()
    if blocked:
        return False, "candidate_blocked", m
    if _customer_declined(db, m["account_id"], m["avito_chat_id"]):
        # Явный отказ клиента сильнее режима автопилота и ручной кнопки отправки.
        return False, "customer_declined", m
    latest = _latest_dialog_state(db, m["account_id"], m["avito_chat_id"])
    if latest and latest[2] and m["created_at"] and latest[2] > m["created_at"]:
        # Любое новое реальное сообщение после генерации делает черновик устаревшим.
        if str(latest[1] or "").startswith("in"):
            try:
                rc.set_candidate_status(db, m["candidate_id"], "replied", (m["candidate_status"],),
                                        "client_replied", meta={"blocked_send": True, "last_message_id": latest[0]})
                db.commit()
            except Exception:
                db.rollback()
            return False, "client_replied", m
        return False, "dialog_changed_after_draft", m
    return True, "ok", m


def send_message(db, message_id, explicit=False, actor="reactivation_worker"):
    ok, reason, m = preflight_send(db, message_id, explicit=explicit)
    if not ok:
        return {"ok": False, "reason": reason}

    # REACTIVATION_AVITO_RECEIVE_PREFLIGHT_V1
    # Never write a reactivation message into a chat whose reply path cannot be
    # read through canonical Avito Messenger. Refresh exactly this chat first,
    # then re-run preflight against the freshly persisted dialog state. This
    # prevents writing over a client/manager reply that arrived after draft time
    # and fail-closes accounts/chats gated by Avito Messenger subscription (402).
    try:
        from app.api.messenger import sync_chat_messages
        receive_probe = sync_chat_messages(m["account_id"], m["avito_chat_id"])
    except Exception as exc:
        receive_probe = {"status": "error", "message": f"receive_preflight_exception:{type(exc).__name__}"}
    if (receive_probe or {}).get("status") != "ok":
        msg = str((receive_probe or {}).get("message") or "Avito message history unavailable")[:500]
        permanent_transport = any(x in msg.lower() for x in (
            "402", "подписку с api мессенджера", "messenger subscription",
        ))
        if permanent_transport:
            # No POST has happened yet: this is a proven receive-capability block,
            # not delivery_unknown. Keep the draft durable and recoverable.
            rc.set_message_status(
                db, message_id, "blocked", (m["status"],), "transport_blocked",
                meta={"error_code": "transport_unavailable", "phase": "receive_preflight"},
            )
            db.execute(text("""
                UPDATE reactivation_messages
                   SET error_code='transport_unavailable',error_message=:e,updated_at=now()
                 WHERE id=:i AND status='blocked'
            """), {"i": message_id, "e": msg})
            db.commit()
            return {"ok": False, "reason": "transport_unavailable", "detail": msg}
        return {"ok": False, "reason": "receive_unavailable", "detail": msg}

    # Fresh Avito history may invalidate the old draft. Re-evaluate before any
    # candidate approval/schedule/send state transition.
    ok, reason, m = preflight_send(db, message_id, explicit=explicit)
    if not ok:
        return {"ok": False, "reason": reason}

    if m["candidate_status"] in ("candidate", "needs_review", "cooldown"):
        if not rc.set_candidate_status(db, m["candidate_id"], "approved", (m["candidate_status"],),
                                       "approved", actor_type="user" if explicit else "system",
                                       actor_id=actor):
            db.rollback()
            return {"ok": False, "reason": "approval_conflict"}
        db.commit()
    if m["status"] in ("ready", "failed"):
        if not rc.schedule_message(db, message_id, datetime.now(timezone.utc)):
            db.rollback()
            return {"ok": False, "reason": "schedule_conflict"}
        db.commit()
    claimed = rc.claim_for_send(db, message_id, actor)
    if not claimed:
        db.rollback()
        return {"ok": False, "reason": "claim_conflict"}
    db.commit()
    try:
        from app.api.messenger import send_message as avito_send
        result = avito_send(claimed[2], claimed[3], claimed[1])
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    if (result or {}).get("status") == "ok":
        provider_message = (result or {}).get("avito_message") if isinstance(result, dict) else None
        provider_message_id = None
        if isinstance(provider_message, dict):
            provider_message_id = provider_message.get("id") or provider_message.get("message_id")
        if not provider_message_id:
            rc.mark_delivery_unknown(db, message_id, "Avito accepted request but returned no message id")
            db.commit()
            return {"ok": False, "reason": "delivery_unknown"}
        rc.mark_sent(db, message_id, avito_message_id=provider_message_id)
        db.commit()
        return {"ok": True, "status": "sent", "avito_message_id": str(provider_message_id)}
    msg = str((result or {}).get("message") or "send_failed")[:500]
    unclear = any(x in msg.lower() for x in ("timeout", "timed out", "connection", "reset"))
    permanent_transport = any(x in msg.lower() for x in ("402", "подписку с api мессенджера", "messenger subscription"))
    if unclear:
        rc.mark_delivery_unknown(db, message_id, msg)
    elif permanent_transport:
        rc.mark_send_failed(db, message_id, "transport_unavailable", msg)
    else:
        rc.mark_send_failed(db, message_id, "avito_error", msg)
    db.commit()
    reason = "delivery_unknown" if unclear else ("transport_unavailable" if permanent_transport else "avito_error")
    return {"ok": False, "reason": reason, "detail": msg}

def recover_stale_sending(db, minutes=10):
    """Не повторяет зависшую отправку: переводит её в delivery_unknown.

    После claim транспорт мог успеть принять сообщение до падения worker, поэтому
    автоматический retry опасен дублем. Владелец увидит состояние и решит его явно.
    """
    rows = db.execute(text("""
        SELECT id FROM reactivation_messages
         WHERE status='sending' AND locked_at IS NOT NULL
           AND locked_at < now() - (:mins * interval '1 minute')
         ORDER BY id
    """), {"mins": int(minutes)}).fetchall()
    recovered = 0
    for (mid,) in rows:
        if rc.mark_delivery_unknown(db, mid, "stale sending recovered without retry"):
            recovered += 1
    if recovered:
        db.commit()
    return recovered


def start_due_cooldowns(db, limit=200):
    """Запускает следующую попытку только после паузы и только если лимит не исчерпан."""
    rows = db.execute(text("""
        SELECT c.id,c.attempts,coalesce(s.max_attempts_per_dialog,2)
          FROM reactivation_candidates c
          LEFT JOIN reactivation_settings s ON s.account_id=c.account_id
         WHERE c.status='sent'
           AND coalesce(s.enabled,true)=true
           AND c.watch_since IS NOT NULL
           AND c.watch_since < now() - (coalesce(s.cooldown_days,7) * interval '1 day')
           AND c.attempts < coalesce(s.max_attempts_per_dialog,2)
         ORDER BY c.watch_since,c.id LIMIT :n
    """), {"n": int(limit)}).fetchall()
    moved = 0
    for cid, _attempts, _max_attempts in rows:
        try:
            if rc.set_candidate_status(db, cid, "cooldown", ("sent",), "cooldown_started",
                                       meta={"source": "reactivation_runtime"}):
                moved += 1
                db.commit()
        except Exception:
            db.rollback()
    return moved


def reconcile_sent_receipts(db, limit=100):
    """Самовосстановление старых sent-записей без Avito receipt.

    Если канонический messenger poller уже сохранил исходящее сообщение,
    связываем его только при однозначном совпадении аккаунта, чата, текста и
    времени. Не угадываем при нескольких совпадениях.
    """
    rows = db.execute(text("""
        SELECT id,account_id,avito_chat_id,final_text,sent_at
          FROM reactivation_messages
         WHERE status='sent' AND avito_message_id IS NULL AND sent_at IS NOT NULL
         ORDER BY id LIMIT :n
    """), {"n": int(limit)}).mappings().all()
    fixed = 0
    for r in rows:
        ts = r["sent_at"].timestamp()
        hits = db.execute(text("""
            SELECT avito_message_id
              FROM messenger_messages
             WHERE account_id=:a AND avito_chat_id=:c
               AND direction ILIKE 'out%'
               AND text=:t
               AND avito_created_at BETWEEN :lo AND :hi
             ORDER BY abs(avito_created_at-:ts)
             LIMIT 2
        """), {"a": r["account_id"], "c": r["avito_chat_id"], "t": r["final_text"],
                "lo": ts-600, "hi": ts+600, "ts": ts}).fetchall()
        if len(hits) == 1 and hits[0][0]:
            db.execute(text("""
                UPDATE reactivation_messages SET avito_message_id=:m,updated_at=now()
                 WHERE id=:i AND status='sent' AND avito_message_id IS NULL
            """), {"m": str(hits[0][0]), "i": r["id"]})
            fixed += 1
    if fixed:
        db.commit()
    return fixed


def reconcile_sent_event_receipts(db, limit=200):
    """Append missing provider receipts to the immutable reactivation event trail.

    Older successful sends stored the Avito provider id in
    ``reactivation_messages`` only.  Do not rewrite those historical ``sent``
    events; append one idempotent ``delivery_resolved`` evidence event instead.
    """
    rows = db.execute(text("""
        SELECT m.id,m.candidate_id,m.avito_message_id,m.attempt_number
          FROM reactivation_messages m
         WHERE m.status='sent' AND m.avito_message_id IS NOT NULL
           AND NOT EXISTS (
                SELECT 1 FROM reactivation_events e
                 WHERE e.message_id=m.id
                   AND e.event IN ('sent','delivery_resolved')
                   AND coalesce(e.metadata->>'avito_message_id','')=m.avito_message_id
           )
         ORDER BY m.id LIMIT :n
    """), {"n": int(limit)}).fetchall()
    fixed = 0
    for mid, cid, provider_id, attempt in rows:
        rc.log_event(
            db, cid, "delivery_resolved", message_id=mid,
            from_status="sent", to_status="sent",
            meta={"attempt": int(attempt or 1), "avito_message_id": str(provider_id),
                  "source": "receipt_journal_backfill"},
        )
        fixed += 1
    if fixed:
        db.commit()
    return fixed


def reconcile_delivery_unknown(db, limit=50, sync_active=True):
    """Self-heal ambiguous Avito sends without ever posting a duplicate.

    A worker can die after Avito accepted POST but before BORIS persisted the
    provider id. The safe recovery is READ -> exact match -> mark_sent. We match
    account + chat + exact text and a bounded time window; ambiguous matches are
    never promoted. For active accounts we refresh the exact Avito chat first so
    the local projection is current. No SMS/email fallback and no second POST.
    """
    rows = db.execute(text("""
        SELECT id,account_id,avito_chat_id,final_text,sent_at,status,updated_at
          FROM reactivation_messages
         WHERE status IN ('sending','delivery_unknown')
           AND final_text IS NOT NULL AND btrim(final_text) <> ''
         ORDER BY updated_at,id LIMIT :n
    """), {"n": int(limit)}).mappings().all()
    fixed = 0
    unresolved = 0
    for r in rows:
        account_id, chat_id = r["account_id"], r["avito_chat_id"]
        if sync_active:
            try:
                from app.api.messenger import get_manager_balance, sync_chat_messages
                if (get_manager_balance(account_id) or {}).get("active"):
                    sync_chat_messages(account_id, chat_id)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=806)
        anchor = r["sent_at"] or r["updated_at"]
        ts = anchor.timestamp() if anchor else None
        if ts is None:
            unresolved += 1
            continue
        hits = db.execute(text("""
            SELECT avito_message_id,avito_created_at
              FROM messenger_messages
             WHERE account_id=:a AND avito_chat_id=:c
               AND direction ILIKE 'out%' AND text=:t
               AND avito_message_id IS NOT NULL
               AND avito_created_at BETWEEN :lo AND :hi
             ORDER BY abs(avito_created_at-:ts)
             LIMIT 2
        """), {"a":account_id,"c":chat_id,"t":r["final_text"],
                "lo":ts-1800,"hi":ts+1800,"ts":ts}).fetchall()
        if len(hits) != 1 or not hits[0][0]:
            unresolved += 1
            continue
        # Preserve provider delivery time for causal attribution/cooldown. Using
        # recovery time would shift the experiment window and could delay or
        # misattribute a client's reply.
        from datetime import datetime, timezone
        provider_sent_at = datetime.fromtimestamp(float(hits[0][1]), tz=timezone.utc) if hits[0][1] is not None else anchor
        if rc.mark_sent(db, r["id"], avito_message_id=str(hits[0][0]), sent_at=provider_sent_at):
            db.commit()
            fixed += 1
        else:
            db.rollback()
    return {"fixed": fixed, "unresolved": unresolved, "scanned": len(rows)}


def reconcile_ready_transport_blocks(db, limit=500):
    """Fail closed ready drafts whose exact chat is known blocked by fresh inbox evidence.

    No provider call, no send and no paid AI call. The reversible blocked state
    is later restored only by the existing double-read transport recovery.
    """
    from app.reactivation_avito_health import chat_transport_blocked
    rows=db.execute(text("""
        SELECT m.id,m.account_id,m.avito_chat_id
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
         WHERE m.status='ready'
           AND c.status IN ('candidate','needs_review','approved','scheduled','cooldown')
         ORDER BY m.updated_at,m.id LIMIT :n
    """), {"n":int(limit)}).fetchall()
    blocked=[]; checked=0; skipped_inactive=0; active_cache={}
    for mid,account_id,chat_id in rows:
        checked += 1
        try:
            aid=str(account_id)
            if aid not in active_cache:
                active_cache[aid]=_mop_active(aid)
            if not active_cache[aid]:
                skipped_inactive += 1
                continue
            if not chat_transport_blocked(db,aid,str(chat_id),max_age_sec=180):
                continue
            if rc.set_message_status(db,mid,'blocked',('ready',),'transport_blocked',
                meta={'error_code':'transport_unavailable','phase':'minute_inbox_preblock',
                      'verification':'fresh_exact_chat_blocked','provider_call':False}):
                db.execute(text("""
                    UPDATE reactivation_messages
                       SET error_code='transport_unavailable',
                           error_message='Avito chat transport unavailable (fresh inbox evidence)',
                           updated_at=now()
                     WHERE id=:i AND status='blocked'
                """), {'i':mid})
                db.commit(); blocked.append(int(mid))
        except Exception:
            db.rollback()
    return {'checked':checked,'blocked':len(blocked),'skipped_inactive':skipped_inactive,'message_ids':blocked}


def reconcile_transport_unavailable(db, limit=100):
    """Self-heal Avito transport blocks without sending anything.

    Recovery is read-only. A successful history GET is not sufficient to re-arm
    an old draft: after the fresh sync the dialog must still be unchanged. If a
    customer or manager spoke meanwhile, stale-dialog reconciliation owns the
    terminal transition instead of resurrecting outreach.
    """
    rows = db.execute(text("""
        SELECT m.id,m.account_id,m.avito_chat_id
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
         WHERE m.status IN ('blocked','failed') AND coalesce(m.error_code,'')='transport_unavailable'
           AND c.status IN ('candidate','approved','scheduled','cooldown')
           -- REACTIVATION_402_EXACT_CHAT_COOLDOWN_V1: minute inbox sync already
           -- provides frequent account/chat evidence. Exact history double-checks
           -- are expensive provider reads, so retry a blocked chat at most once
           -- per 30 minutes until access returns.
           AND m.updated_at <= now() - interval '30 minutes'
         ORDER BY m.updated_at,m.id LIMIT :n
    """), {"n": int(limit)}).fetchall()
    restored = 0
    checked = 0
    still_blocked = 0
    verification_failed = 0
    stale_changed = 0
    restored_by_account = {}
    # A transport block is account/chat scoped. Do not let one account with many
    # 402 chats monopolize every scheduler tick: inspect the whole bounded backlog
    # and keep each message fail-closed until its own history is readable twice.
    active_cache = {}
    skipped_inactive = 0
    for mid, account_id, chat_id in rows:
        checked += 1
        try:
            aid=str(account_id)
            if aid not in active_cache:
                active_cache[aid]=_mop_active(aid)
            if not active_cache[aid]:
                skipped_inactive += 1
                continue
            # Normalize historical pre-guard rows: receive-preflight 402 happened
            # before POST and belongs to the reversible external-block state.
            current_status = db.execute(text("SELECT status FROM reactivation_messages WHERE id=:i"), {"i": mid}).scalar()
            if current_status == "failed":
                if rc.set_message_status(db, mid, "blocked", ("failed",), "transport_blocked",
                    meta={"error_code": "transport_unavailable", "phase": "legacy_normalize"}):
                    db.commit()
            from app.api.messenger import fetch_chat_messages, sync_chat_messages
            # Require two consecutive readable histories: some Avito chats can
            # alternate 200/402. Never re-arm on a single transient success.
            probe = sync_chat_messages(account_id, chat_id)
            if (probe or {}).get("status") != "ok":
                still_blocked += 1
                db.execute(text("UPDATE reactivation_messages SET updated_at=now() WHERE id=:i AND status='blocked'"), {"i": mid})
                db.commit()
                continue
            verify = fetch_chat_messages(account_id, chat_id)
            if (verify or {}).get("status") != "ok":
                verification_failed += 1
                db.execute(text("UPDATE reactivation_messages SET updated_at=now() WHERE id=:i AND status='blocked'"), {"i": mid})
                db.commit()
                continue
            # Fresh inbox is authoritative: access recovery must not resurrect
            # a draft after the customer or manager has spoken meanwhile.
            stale = reconcile_stale_ready_messages(db, account_id=account_id)
            current = db.execute(text("SELECT status FROM reactivation_messages WHERE id=:i"), {"i": mid}).fetchone()
            if not current or current[0] not in ("blocked", "failed"):
                stale_changed += 1
                db.commit()
                continue
            if rc.set_message_status(
                db, mid, "ready", ("blocked","failed"), "transport_restored",
                meta={"channel": "avito", "verification": "fresh_history_and_dialog_unchanged",
                      "stale_reconciled": int(stale or 0)},
            ):
                db.execute(text("""
                    UPDATE reactivation_messages
                       SET error_code=NULL,error_message=NULL,updated_at=now()
                     WHERE id=:i AND status='ready'
                """), {"i": mid})
                db.commit()
                restored += 1
                restored_by_account[account_id] = int(restored_by_account.get(account_id) or 0) + 1
        except Exception:
            db.rollback()
            verification_failed += 1
    return {"checked": checked, "restored": restored, "still_blocked": still_blocked,
            "verification_failed": verification_failed, "stale_changed": stale_changed,
            "skipped_inactive": skipped_inactive, "restored_by_account": restored_by_account}

def process(db, generate_limit=5, send_limit=10):
    """Один безопасный проход. recommend готовит черновики, auto ещё и отправляет."""
    stats = {
        "generated": 0, "sent": 0, "waiting_confirmation": 0, "blocked": 0, "errors": 0,
        "generation_waiting_mop": 0, "generation_waiting_transport": 0,
        "generation_safety_blocked": 0, "generation_blocked_reasons": {},
        "client_replied": 0, "stale_recovered": 0, "cooldown_started": 0, "receipts_backfilled": 0,
        "event_receipts_backfilled": 0, "delivery_reconciled": 0, "delivery_unresolved": 0,
        "stale_ready_cancelled": 0, "stale_ready_replied": 0, "stale_ready_manager_taken_over": 0,
        "transport_preblocked": 0, "transport_restored": 0, "transport_checked": 0, "transport_still_blocked": 0,
        "transport_verification_failed": 0, "transport_stale_changed": 0,
        "auto_revalidation_checked": 0, "auto_revalidated": 0,
        "auto_revalidation_invalidated": 0, "auto_revalidation_sync_failed": 0,
        "auto_revalidation_safety_blocked": 0,
        "send_waiting_schedule": 0, "send_waiting_daily_limit": 0,
        "send_waiting_interval": 0,
        "send_duplicate_blocked": 0, "send_qualification_blocked": 0,
        "send_customer_recontact_blocked": 0, "send_factual_safety_blocked": 0,
    }
    try:
        stats["receipts_backfilled"] = reconcile_sent_receipts(db)
        stats["event_receipts_backfilled"] = reconcile_sent_event_receipts(db)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        delivery_reconcile = reconcile_delivery_unknown(db)
        stats["delivery_reconciled"] = int(delivery_reconcile.get("fixed") or 0)
        stats["delivery_unresolved"] = int(delivery_reconcile.get("unresolved") or 0)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        stats["stale_recovered"] = recover_stale_sending(db)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        delivery_reconcile = reconcile_delivery_unknown(db)
        stats["delivery_reconciled"] += int(delivery_reconcile.get("fixed") or 0)
        stats["delivery_unresolved"] = int(delivery_reconcile.get("unresolved") or 0)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        transport_preblock = reconcile_ready_transport_blocks(db)
        stats["transport_preblocked"] = int((transport_preblock or {}).get("blocked") or 0)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        transport_recovery = reconcile_transport_unavailable(db)
        if isinstance(transport_recovery, dict):
            stats["transport_restored"] = int(transport_recovery.get("restored") or 0)
            stats["transport_checked"] = int(transport_recovery.get("checked") or 0)
            stats["transport_still_blocked"] = int(transport_recovery.get("still_blocked") or 0)
            stats["transport_verification_failed"] = int(transport_recovery.get("verification_failed") or 0)
            stats["transport_stale_changed"] = int(transport_recovery.get("stale_changed") or 0)
        else:
            stats["transport_restored"] = int(transport_recovery or 0)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        stale_ready = reconcile_stale_ready_messages(db)
        stats["stale_ready_cancelled"] = int(stale_ready.get("cancelled") or 0)
        stats["stale_ready_replied"] = int(stale_ready.get("replied") or 0)
        stats["stale_ready_manager_taken_over"] = int(stale_ready.get("manager_taken_over") or 0)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        stats["client_replied"] = reconcile_client_replies(db)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        # REACTIVATION_AUTO_BACKLOG_REVALIDATE_V1:
        # Switching recommend -> auto is fail-closed, but the old safe backlog
        # must not remain dead forever. Recheck a bounded batch against the
        # current Avito dialog and append durable authority evidence. Sending
        # still goes through normal hours/daily-limit/receipt preflight.
        _rearm = reconcile_auto_activation_backlog(db, limit=max(10, min(int(send_limit), 25)))
        stats["auto_revalidation_checked"] = int(_rearm.get("checked") or 0)
        stats["auto_revalidated"] = int(_rearm.get("revalidated") or 0)
        stats["auto_revalidation_invalidated"] = int(_rearm.get("invalidated") or 0)
        stats["auto_revalidation_sync_failed"] = int(_rearm.get("sync_failed") or 0)
        stats["auto_revalidation_safety_blocked"] = int(_rearm.get("safety_blocked") or 0)
    except Exception:
        db.rollback(); stats["errors"] += 1
    try:
        stats["cooldown_started"] = start_due_cooldowns(db)
    except Exception:
        db.rollback(); stats["errors"] += 1
    candidates = db.execute(text("""
        SELECT c.id,c.account_id
          FROM reactivation_candidates c
          LEFT JOIN reactivation_messages m ON m.candidate_id=c.id AND m.status <> 'cancelled'
          LEFT JOIN reactivation_settings s ON s.account_id=c.account_id
         WHERE c.status IN ('candidate','needs_review','approved','cooldown')
           AND coalesce(s.enabled,true)=true
           AND m.id IS NULL
         ORDER BY c.score DESC,c.id
         LIMIT :n
    """), {"n": int(generate_limit)}).fetchall()
    for cid, _acc in candidates:
        try:
            r = generate_for_candidate(db, cid)
            if r.get("ok") and r.get("created"):
                stats["generated"] += 1
            elif not r.get("ok"):
                generation_reason = str(r.get("reason") or "")
                if generation_reason == "mop_inactive":
                    stats["generation_waiting_mop"] += 1
                elif generation_reason in ("transport_unavailable", "transport_preflight_failed"):
                    stats["generation_waiting_transport"] += 1
                elif generation_reason == "draft_safety_failed":
                    stats["generation_safety_blocked"] += 1
                else:
                    stats["blocked"] += 1
                    key = generation_reason or "unknown"
                    stats["generation_blocked_reasons"][key] = int(
                        stats["generation_blocked_reasons"].get(key) or 0
                    ) + 1
        except Exception:
            db.rollback(); stats["errors"] += 1

    # Human-confirmation queues are measured separately and must never consume
    # the bounded scheduler send window. Otherwise 25 old recommend-mode rows
    # can permanently starve every real auto account behind them.
    if int(send_limit) > 0:
        stats["waiting_confirmation"] = int(db.execute(text("""
            SELECT count(*)
              FROM reactivation_messages m
              JOIN reactivation_candidates c ON c.id=m.candidate_id
              LEFT JOIN reactivation_settings s ON s.account_id=m.account_id
             WHERE m.status IN ('ready','scheduled','failed')
               AND c.status IN ('candidate','needs_review','approved','scheduled','cooldown')
               AND coalesce(s.enabled,true)=true
               AND coalesce(s.mode,'recommend') <> 'auto'
        """)).scalar() or 0)

    ready = db.execute(text("""
        SELECT m.id,m.account_id,coalesce(s.mode,'recommend'),m.avito_chat_id,m.final_text
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
          LEFT JOIN reactivation_settings s ON s.account_id=m.account_id
         WHERE m.status IN ('ready','scheduled','failed')
           AND c.status IN ('candidate','approved','scheduled','cooldown')
           AND coalesce(s.enabled,true)=true
           AND coalesce(s.mode,'recommend')='auto'
           AND NOT (m.status='failed' AND coalesce(m.error_code,'')='transport_unavailable')
           AND EXISTS (
                SELECT 1 FROM reactivation_events ae
                 WHERE ae.message_id=m.id
                   AND ae.event='message_ready'
                   AND coalesce(ae.metadata->>'auto_eligible','false')='true'
           )
           AND NOT EXISTS (
                SELECT 1 FROM reactivation_events ee
                 WHERE ee.message_id=m.id AND ee.event='message_edited'
           )
           AND (
                s.updated_at IS NULL
                OR m.created_at >= s.updated_at
                OR EXISTS (
                    SELECT 1 FROM reactivation_events re
                     WHERE re.message_id=m.id
                       AND re.event='message_ready'
                       AND re.at>=s.updated_at
                       AND coalesce(re.metadata->>'auto_revalidated','false')='true'
                       AND coalesce(re.metadata->>'auto_eligible','false')='true'
                )
           )
         -- Round-robin by account so one large queue cannot monopolize a tick.
         ORDER BY row_number() OVER (PARTITION BY m.account_id ORDER BY m.id), m.account_id, m.id
         LIMIT :n
    """), {"n": max(200, min(2000, int(send_limit) * 50))}).fetchall()
    attempted = 0
    settings_cache = {}
    used_cache = {}
    for mid, _acc, mode, chat_id, final_text in ready:
        if mode != "auto":
            # Defense in depth: SQL above makes this unreachable.
            stats["waiting_confirmation"] += 1
            continue
        aid = str(_acc)
        qualification_block = _auto_qualification_block_reason(db, aid, chat_id)
        if qualification_block:
            stats["send_qualification_blocked"] += 1
            continue
        if _customer_promised_to_recontact(db, aid, chat_id):
            stats["send_customer_recontact_blocked"] += 1
            continue
        if _draft_factual_safety_problems(db, aid, chat_id, final_text):
            stats["send_factual_safety_blocked"] += 1
            continue
        if _duplicate_outgoing_blocked(db, aid, chat_id, final_text):
            stats["send_duplicate_blocked"] += 1
            continue
        st = settings_cache.setdefault(aid, _settings(db, aid))
        if not _within_hours(st):
            stats["send_waiting_schedule"] += 1
            continue
        if aid not in used_cache:
            used_cache[aid] = int(rc.used_today(db, aid))
        daily_limit = int(st.get("daily_limit") or 10)
        if used_cache[aid] >= daily_limit:
            stats["send_waiting_daily_limit"] += 1
            continue
        if _auto_min_interval_blocked(db, aid, st):
            stats["send_waiting_interval"] += 1
            continue
        if attempted >= int(send_limit):
            break
        attempted += 1
        try:
            r = send_message(db, mid, explicit=False)
            if r.get("ok"):
                stats["sent"] += 1
                used_cache[aid] = int(used_cache.get(aid) or 0) + 1
            elif r.get("reason") == "outside_allowed_hours":
                stats["send_waiting_schedule"] += 1
            elif r.get("reason") == "daily_limit":
                stats["send_waiting_daily_limit"] += 1
                used_cache[aid] = daily_limit
            elif r.get("reason") == "min_interval":
                stats["send_waiting_interval"] += 1
            else:
                stats["blocked"] += 1
        except Exception:
            db.rollback(); stats["errors"] += 1
    return stats


def reconcile_stale_ready_messages(db, limit=500, account_id=None):
    """Cancel unsent reactivation drafts after the real dialog has changed.

    A ready/scheduled/failed draft is only valid against the conversation state
    that existed when it was generated. If any newer non-system message exists,
    never keep offering or retrying that old text. Incoming replies also close
    the candidate; newer outgoing messages simply invalidate the draft so a
    future scan may build a fresh hypothesis.
    """
    params={"n":int(limit), "account_id":str(account_id or "").strip()}
    rows=db.execute(text("""
        SELECT m.id,m.candidate_id,m.status,m.account_id,m.avito_chat_id,m.created_at,
               c.status AS candidate_status,
               latest.id AS latest_id,latest.direction AS latest_dir,latest.created_at AS latest_at
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
          JOIN LATERAL (
                SELECT mm.id,lower(coalesce(mm.direction,'')) AS direction,
                       to_timestamp(mm.avito_created_at) AS created_at
                  FROM messenger_messages mm
                 WHERE mm.account_id=m.account_id AND mm.avito_chat_id=m.avito_chat_id
                   AND coalesce(mm.msg_type,'') <> 'system'
                   AND btrim(coalesce(mm.text,'')) <> ''
                 ORDER BY mm.avito_created_at DESC,mm.id DESC LIMIT 1
          ) latest ON true
         WHERE m.status IN ('ready','scheduled','failed')
           AND latest.created_at > m.created_at
           AND (:account_id='' OR m.account_id=:account_id)
         ORDER BY m.id LIMIT :n
    """),params).mappings().all()
    cancelled=replied=manager_taken_over=0
    for r in rows:
        try:
            incoming=str(r["latest_dir"] or "").startswith("in")
            active_status=str(r["candidate_status"] or "")
            if incoming and active_status in (
                "candidate","needs_review","approved","scheduled","cooldown"
            ):
                if rc.set_candidate_status(
                    db,r["candidate_id"],"replied",(active_status,),
                    "client_replied",
                    meta={"last_message_id":int(r["latest_id"]),"source":"stale_ready_reconcile"},
                ):
                    replied += 1
                    db.execute(text(
                        "UPDATE reactivation_candidates SET last_analyzed_message_id=:m WHERE id=:i"
                    ),{"m":int(r["latest_id"]),"i":r["candidate_id"]})
            elif (not incoming) and active_status in (
                "candidate","needs_review","approved","scheduled","cooldown"
            ):
                # A newer real outgoing message means the normal MOP / manager
                # resumed the dialog. Reactivation must terminate this cycle,
                # not regenerate another follow-up on the next scheduler tick.
                if rc.set_candidate_status(
                    db,r["candidate_id"],"manager_taken_over",(active_status,),
                    "manager_takeover",
                    meta={"last_message_id":int(r["latest_id"]),"source":"stale_ready_reconcile"},
                ):
                    manager_taken_over += 1
            if rc.set_message_status(
                db,r["id"],"cancelled",(r["status"],),"cancelled",
                meta={
                    "reason":"client_replied" if incoming else "dialog_changed_manager_takeover",
                    "last_message_id":int(r["latest_id"]),
                    "last_direction":r["latest_dir"],
                },
            ):
                cancelled += 1
            db.commit()
        except Exception:
            db.rollback()
    return {
        "cancelled":cancelled,
        "replied":replied,
        "manager_taken_over":manager_taken_over,
        "scanned":len(rows),
    }


def reconcile_auto_activation_backlog(db, limit=10, account_id=None):
    """Freshly re-authorize old auto-safe drafts without bypassing any send gate.

    The recommend->auto boundary deliberately blocks historical drafts. This
    reconciler handles the missing continuation step: only a draft whose
    original message_ready evidence says auto_eligible=true, that was never
    edited by a human, and whose exact Avito chat can still be read is eligible
    for revalidation. A newer customer/manager message is reconciled/cancelled
    before any authority evidence is written.

    Revalidation is NOT sending. Normal allowed-hours, daily-limit, attempts,
    decline/DNC and exact receive-preflight checks remain in send_message().
    """
    params={"n":max(0,int(limit)), "account_id":str(account_id or "").strip()}
    if params["n"] <= 0:
        return {"checked":0,"revalidated":0,"invalidated":0,"sync_failed":0,"safety_blocked":0}
    rows=db.execute(text("""
        SELECT m.id,m.candidate_id,m.account_id,m.avito_chat_id,m.created_at,
               c.status AS candidate_status,c.attempts,
               s.updated_at AS auto_boundary
          FROM reactivation_messages m
          JOIN reactivation_candidates c ON c.id=m.candidate_id
          JOIN reactivation_settings s ON s.account_id=m.account_id
         WHERE m.status='ready'
           -- needs_review is deliberately human-only and can never enter the
           -- scheduler send set. Do not spend Avito reads revalidating it.
           AND c.status IN ('candidate','approved','scheduled','cooldown')
           AND coalesce(s.enabled,true)=true
           AND coalesce(s.mode,'recommend')='auto'
           AND s.updated_at IS NOT NULL
           AND m.created_at < s.updated_at
           AND (:account_id='' OR m.account_id=:account_id)
           AND EXISTS (
                SELECT 1 FROM reactivation_events e
                 WHERE e.message_id=m.id AND e.event='message_ready'
                   AND coalesce(e.metadata->>'auto_eligible','false')='true'
           )
           AND NOT EXISTS (
                SELECT 1 FROM reactivation_events e
                 WHERE e.message_id=m.id AND e.event='message_edited'
           )
           AND NOT EXISTS (
                SELECT 1 FROM reactivation_events e
                 WHERE e.message_id=m.id AND e.event='message_ready'
                   AND e.at>=s.updated_at
                   AND coalesce(e.metadata->>'auto_revalidated','false')='true'
                   AND coalesce(e.metadata->>'auto_eligible','false')='true'
           )
         ORDER BY m.created_at,m.id
         LIMIT :n
    """),params).mappings().all()
    checked=revalidated=invalidated=sync_failed=safety_blocked=0
    from app.api.messenger import sync_chat_messages
    for r in rows:
        checked += 1
        mid=int(r["id"]); cid=int(r["candidate_id"])
        aid=str(r["account_id"]); chat_id=str(r["avito_chat_id"])
        try:
            settings=_settings(db,aid)
            boundary=settings.get("updated_at")
            if (
                not settings.get("enabled",True)
                or settings.get("mode")!="auto"
                or not boundary
                or not r.get("created_at")
                or r["created_at"]>=boundary
            ):
                safety_blocked += 1
                continue
            if not _mop_active(aid):
                safety_blocked += 1
                continue
            fresh=sync_chat_messages(aid,chat_id) or {}
            if fresh.get("status")!="ok":
                sync_failed += 1
                db.rollback()
                continue

            # Fresh provider truth can invalidate multiple old drafts for this
            # account. This is intentional and fail-closed.
            reconcile_stale_ready_messages(db,account_id=aid)
            current=db.execute(text("""
                SELECT m.status,c.status AS candidate_status,c.attempts,m.created_at
                  FROM reactivation_messages m
                  JOIN reactivation_candidates c ON c.id=m.candidate_id
                 WHERE m.id=:m
            """),{"m":mid}).mappings().first()
            if (
                not current
                or current.get("status")!="ready"
                or current.get("candidate_status") not in ("candidate","needs_review","approved","scheduled","cooldown")
            ):
                invalidated += 1
                db.commit()
                continue
            if int(current.get("attempts") or 0) >= int(settings.get("max_attempts_per_dialog") or 2):
                safety_blocked += 1
                continue
            if _customer_declined(db,aid,chat_id):
                safety_blocked += 1
                continue
            if _auto_revalidated_after_boundary(db,mid,boundary):
                continue

            latest=_latest_dialog_state(db,aid,chat_id)
            if latest and latest[2] and current.get("created_at") and latest[2] > current["created_at"]:
                # Should normally have been cancelled by stale reconciliation;
                # never re-authorize if provider truth still proves drift.
                invalidated += 1
                continue

            rc.log_event(
                db,cid,"message_ready",message_id=mid,from_status="ready",to_status="ready",
                meta={
                    "auto_eligible":True,
                    "auto_revalidated":True,
                    "source":"auto_activation_backlog_reconcile",
                    "settings_boundary":boundary.isoformat() if hasattr(boundary,"isoformat") else str(boundary),
                    "fresh_history_messages":int(fresh.get("messages") or 0),
                },
            )
            db.commit()
            revalidated += 1
        except Exception:
            db.rollback()
            sync_failed += 1
    return {
        "checked":checked,
        "revalidated":revalidated,
        "invalidated":invalidated,
        "sync_failed":sync_failed,
        "safety_blocked":safety_blocked,
    }


def reconcile_client_replies(db, limit=500, account_id=None):
    """Закрывает реактивацию, если после последнего учтённого сообщения уже ответил клиент.

    ``account_id`` allows the low-latency Avito inbox poller to reconcile only
    the account it has just refreshed, so a client reply does not wait for the
    15-minute reactivation scheduler tick.
    """
    rows = db.execute(text("""
        SELECT c.id,c.status,c.last_analyzed_message_id,
               (SELECT mm.id FROM messenger_messages mm
                 WHERE mm.account_id=c.account_id AND mm.avito_chat_id=c.avito_chat_id
                   AND coalesce(mm.msg_type,'') <> 'system' AND btrim(coalesce(mm.text,'')) <> ''
                 ORDER BY mm.avito_created_at DESC,mm.id DESC LIMIT 1) AS last_id,
               (SELECT lower(coalesce(mm.direction,'')) FROM messenger_messages mm
                 WHERE mm.account_id=c.account_id AND mm.avito_chat_id=c.avito_chat_id
                   AND coalesce(mm.msg_type,'') <> 'system' AND btrim(coalesce(mm.text,'')) <> ''
                 ORDER BY mm.avito_created_at DESC,mm.id DESC LIMIT 1) AS last_dir
          FROM reactivation_candidates c
         WHERE c.status IN ('candidate','needs_review','approved','scheduled','sent','cooldown')
           AND c.last_analyzed_message_id IS NOT NULL
           AND (:account_id='' OR c.account_id=:account_id)
         ORDER BY c.id LIMIT :n
    """), {"n": int(limit), "account_id": str(account_id or "").strip()}).fetchall()
    changed = 0
    for cid, status, seen, last_id, last_dir in rows:
        if last_id and int(last_id) > int(seen or 0) and str(last_dir or "").startswith("in"):
            try:
                if rc.set_candidate_status(db, cid, "replied", (status,), "client_replied",
                                           meta={"last_message_id": int(last_id), "source": "runtime_reconcile"}):
                    changed += 1
                    db.execute(text("UPDATE reactivation_candidates SET last_analyzed_message_id=:m WHERE id=:i"),
                               {"m": int(last_id), "i": cid})
                    db.commit()
            except Exception:
                db.rollback()
    return changed
