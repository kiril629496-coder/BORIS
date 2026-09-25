# -*- coding: utf-8 -*-
"""API экрана «Возврат клиентов»: три очереди, карточка, действия.
Отправки здесь нет физически — модуль reactivation_core её не содержит."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
import app.reactivation_core as rc
from app.reactivation_runtime import ready_queue_truth, _within_hours
from app.reactivation_value import dialog_money

try:
    from app.api.auth import get_current_user
except ImportError:  # на случай другой раскладки
    from app.auth import get_current_user

router = APIRouter(prefix="/api/reactivation", tags=["reactivation"])
HOT_DAYS = 7
DEBT = "seller_action_missing"


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _my_accounts(db, user):
    """Клиент видит только свои аккаунты. Владелец платформы ведёт клиентов вручную,
    их аккаунты числятся за другими owner_user_id, поэтому ему отдаём все."""
    uid = getattr(user, "id", None)
    role = str(getattr(user, "role", "") or "").lower()
    if role == "owner":
        rows = db.execute(text(
            "SELECT account_id FROM accounts ORDER BY account_id")).fetchall()
    else:
        rows = db.execute(text(
            "SELECT account_id FROM accounts WHERE owner_user_id = :u"
            # BORIS_EMP_REACT_SCOPE: сотрудник аккаунтами не владеет, доступ у него
            # через таблицу связей - без этого Спящие у него пустые
            " UNION SELECT account_id FROM user_account_access"
            " WHERE user_id = :u AND can_view = TRUE"), {"u": uid}).fetchall()
    return [r[0] for r in rows]


def _scope_accounts(db, user, account_id: str = ""):
    accounts = _my_accounts(db, user)
    if account_id and account_id != "all":
        return [a for a in accounts if a == account_id]
    return accounts


def _not_disabled(db, accounts):
    """Явно выключенные аккаунты на экран не попадают. Отсутствие настроек
    выключением не считается — это «ещё не настраивали»."""
    if not accounts:
        return []
    off = {r[0] for r in db.execute(text(
        "SELECT account_id FROM reactivation_settings WHERE enabled = false"))}
    return [a for a in accounts if a not in off]


def _mop_accounts(db, accounts):
    """Реактивация работает поверх AI-менеджера: без подключённого МОПа
    аккаунт в очереди не попадает вообще."""
    out = []
    try:
        from app.api.messenger import get_manager_balance
    except Exception:
        return accounts
    for acc in accounts:
        try:
            if (get_manager_balance(acc) or {}).get("active"):
                out.append(acc)
        except Exception:
            continue
    return out


def _age(ts):
    if not ts:
        return 999
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).days


def _queue_of(row):
    if row["primary_reason"] == DEBT:
        return "hot" if _age(row["watch_since"]) <= HOT_DAYS else "late"
    return "reactivation" if row["status"] in ("candidate", "needs_review", "approved", "scheduled", "cooldown") else None


def _rows(db, accounts):
    accounts = _mop_accounts(db, _not_disabled(db, accounts))
    if not accounts:
        return []
    sql = text(
        "SELECT c.id, c.account_id, c.avito_chat_id, c.primary_reason, c.status, c.score,"
        " c.confidence, c.summary, c.watch_since, c.evidence_message_ids, c.phone_received, c.lead_temperature,"
        " m.id AS msg_id, m.final_text, m.status AS msg_status, m.error_code AS msg_error_code, m.error_message AS msg_error_message, m.attempt_number,"
        " (SELECT max(to_timestamp(mm.avito_created_at)) FROM messenger_messages mm WHERE mm.account_id=c.account_id AND mm.avito_chat_id=c.avito_chat_id) AS last_contact_at,"
        " (SELECT item_title FROM messenger_messages mm WHERE mm.account_id=c.account_id"
        "    AND mm.avito_chat_id=c.avito_chat_id AND mm.item_title IS NOT NULL LIMIT 1) AS title,"
        " (SELECT s.value FROM storage s WHERE s.account_id=c.account_id AND s.key=('mop_qualification:' || c.avito_chat_id) ORDER BY s.id DESC LIMIT 1) AS qualification_json,"
        " (SELECT bool_or(lower(coalesce(mm.text,'')) ~ '(расч[её]т|рассчит|смет)') FROM messenger_messages mm WHERE mm.account_id=c.account_id AND mm.avito_chat_id=c.avito_chat_id) AS had_calculation"
        " FROM reactivation_candidates c"
        " LEFT JOIN LATERAL (SELECT rm.id,rm.final_text,rm.status,rm.error_code,rm.error_message,rm.attempt_number FROM reactivation_messages rm WHERE rm.candidate_id=c.id AND rm.status <> 'cancelled' ORDER BY rm.attempt_number DESC,rm.id DESC LIMIT 1) m ON true"
        " WHERE c.account_id = ANY(:a)"
        "   AND c.status IN ('candidate','needs_review','approved','scheduled','cooldown')"
        " ORDER BY c.score DESC, c.id")
    out = []
    for r in db.execute(sql, {"a": accounts}).mappings():
        d = dict(r)
        d["age"] = _age(d["watch_since"])
        d["queue"] = _queue_of(d)
        if d["queue"]:
            out.append(d)
    return out


def _money(db, d):
    m = dialog_money(db, d["account_id"], d["avito_chat_id"])
    return {k: (m[k]["amount"] if m.get(k) else None)
            for k in ("price", "deposit", "delivery", "rent")}


def _card(db, d, with_money=True):
    import json as _json_card
    try: qual = _json_card.loads(d.get("qualification_json") or "{}")
    except Exception: qual = {}
    if not isinstance(qual, dict): qual = {}
    return {"id": d["id"], "account_id": d["account_id"], "chat": d["avito_chat_id"],
            "title": d["title"], "reason": d["primary_reason"], "status": d["status"],
            "age_days": d["age"], "score": d["score"],
            "confidence": float(d["confidence"]) if d["confidence"] is not None else None,
            "summary": d["summary"], "phone_received": bool(d["phone_received"] or qual.get("phone_received")), "lead_temperature": d.get("lead_temperature") or qual.get("lead_temperature"),
            "last_contact_at": str(d.get("last_contact_at")) if d.get("last_contact_at") else None,
            "intent": qual.get("intent"), "qualification_stage": qual.get("qualification_stage"), "target_action": qual.get("target_action"),
            "next_action": qual.get("next_action"), "had_calculation": bool(d.get("had_calculation")),
            "recommendation": (qual.get("next_action") or "Вернуть клиента в диалог с учётом сохранённой причины и предыдущего контекста") if d["queue"] == "reactivation" else "Обработать текущую причину внимания",
            "queue": d["queue"], "message_id": d["msg_id"], "draft": d["final_text"],
            "message_status": d.get("msg_status"), "message_error_code": d.get("msg_error_code"),
            "message_error": d.get("msg_error_message"), "attempt_number": d.get("attempt_number"),
            "money": _money(db, d) if with_money else None}


@router.get("/queues")
def queues(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    scoped = _scope_accounts(db, user, account_id)
    rows = _rows(db, scoped)
    res = {}
    for q, title in (("hot", "Клиенты ждут ответа"),
                     ("late", "Просроченные обязательства"),
                     ("reactivation", "Вернуть потерянных клиентов")):
        sel = [x for x in rows if x["queue"] == q]
        total = 0
        for x in sel:
            m = _money(db, x)
            total += m.get("price") or m.get("rent") or 0
        res[q] = {"title": title, "count": len(sel), "amount": total,
                  "overdue_14": len([x for x in sel if x["age"] >= 14]),
                  "overdue_30": len([x for x in sel if x["age"] >= 30])}
    modes = []
    for acc in scoped:
        s = rc.settings_for(db, acc)
        modes.append((s or {}).get("mode", "recommend"))
    mop_connected = bool(_mop_accounts(db, scoped))
    res["sending_enabled"] = mop_connected
    res["mode"] = modes[0] if len(set(modes)) == 1 and modes else ("mixed" if modes else "recommend")
    res["mop_connected"] = mop_connected
    # OWNER_WORKBENCH_CAPACITY_V1: show the real safe capacity for today's
    # reactivation work. This is read-only and never sends/generates anything.
    capacity = []
    for acc in scoped:
        st = rc.settings_for(db, acc) or {}
        limit = int(st.get("daily_limit") or 10)
        used = int(rc.used_today(db, acc))
        capacity.append({"account_id": acc, "daily_limit": limit, "sent_today": used,
                         "remaining_today": max(0, limit - used), "mode": st.get("mode") or "recommend",
                         "enabled": st.get("enabled") is not False})
    res["capacity"] = capacity
    res["remaining_today"] = sum(x["remaining_today"] for x in capacity)
    return res


@router.get("/account-status")
def account_status(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    """Owner-facing availability for every accessible account. Read-only.

    A missing queue must never look like a broken button: this endpoint explains
    whether reactivation is ready, intentionally disabled, expired, exhausted,
    or simply has no candidates yet.
    """
    from app.api.messenger import get_manager_balance
    scoped = _scope_accounts(db, user, account_id)
    # Match the global account selector: internal QA accounts are not owner work.
    if not account_id or account_id == "all":
        scoped = [a for a in scoped if not str(a or "").lower().startswith("qa")]
    names = dict(db.execute(text("SELECT account_id,coalesce(name,account_id) FROM accounts WHERE account_id = ANY(:a)"), {"a": scoped}).all()) if scoped else {}
    out = []
    for acc in scoped:
        st = rc.settings_for(db, acc) or {}
        enabled = st.get("enabled") is not False
        try:
            mop = get_manager_balance(acc) or {}
        except Exception:
            mop = {"active": False}
        mop_active = bool(mop.get("active"))
        active_candidates = int(db.execute(text("SELECT count(*) FROM reactivation_candidates WHERE account_id=:a AND status IN ('candidate','needs_review','approved','scheduled','cooldown')"), {"a": acc}).scalar() or 0)
        queue_truth = ready_queue_truth(db, acc)
        ready = int(queue_truth.get("ready_messages") or 0)
        auto_sendable = int(queue_truth.get("auto_sendable") or 0)
        confirmation_required = int(queue_truth.get("confirmation_required") or 0)
        hold_total = int(queue_truth.get("hold_total") or confirmation_required)
        awaiting_revalidation = int(queue_truth.get("awaiting_revalidation") or 0)
        send_window_open = bool(_within_hours(st))
        limit = int(st.get("daily_limit") or 10)
        used = int(rc.used_today(db, acc))
        try:
            from app.reactivation_avito_health import effective_capability
            cap = effective_capability(db, acc)
        except Exception:
            cap = {}
        delivery_unknown = int(db.execute(text("SELECT count(*) FROM reactivation_messages WHERE account_id=:a AND status='delivery_unknown'"), {"a":acc}).scalar() or 0)
        blocked_transport = int(db.execute(text("SELECT count(*) FROM reactivation_messages WHERE account_id=:a AND status IN ('blocked','failed') AND error_code='transport_unavailable'"), {"a":acc}).scalar() or 0)
        restored = {}
        try:
            import json as _json
            raw_restored = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='reactivation_avito_capability_restored_v1' ORDER BY id DESC LIMIT 1"), {"a":acc}).scalar()
            restored = _json.loads(raw_restored) if raw_restored else {}
        except Exception:
            restored = {}
        recovery = db.execute(text("""
            SELECT count(*) FILTER (WHERE e.event='transport_restored') AS restored_total,
                   max(e.at) FILTER (WHERE e.event='transport_restored') AS last_restored_at
              FROM reactivation_events e
              JOIN reactivation_candidates c ON c.id=e.candidate_id
             WHERE c.account_id=:a
        """), {"a":acc}).mappings().first() or {}
        if not enabled:
            state, reason = "disabled", (st.get("disabled_reason") or "Реактивация выключена для этого аккаунта.")
        elif not mop_active and bool(mop.get("expired")):
            state, reason = "expired", "Оплаченный период ИИ-менеджера завершён. Реактивация остановлена до продления."
        elif not mop_active and int(mop.get("left") or 0) <= 0:
            state, reason = "limit_exhausted", "Лимит ИИ-менеджера исчерпан. Реактивация безопасно остановлена."
        elif not mop_active:
            state, reason = "mop_inactive", "ИИ-менеджер сейчас не активен для этого аккаунта."
        elif delivery_unknown > 0 and str(cap.get("classification") or "") == "avito_messenger_subscription_required":
            state, reason = "waiting_external", f"Есть {delivery_unknown} отправка с неопределённой доставкой; Avito сейчас не даёт прочитать историю чата (API Messenger 402). BORIS не повторяет отправку и сам перепроверит receipt после восстановления доступа."
        elif delivery_unknown > 0:
            state, reason = "needs_attention", f"Есть {delivery_unknown} отправка с неопределённой доставкой. BORIS не повторяет её автоматически, чтобы не создать дубль."
        elif str(cap.get("classification") or "") == "avito_messenger_subscription_required":
            state, reason = "waiting_external", "Avito не даёт читать историю проверенных чатов без доступа к API Messenger. Реактивация безопасно остановлена для этих чатов."
        elif str(cap.get("classification") or "") == "avito_messenger_history_partial_402":
            state, reason = "partial_external", "Часть Avito-диалогов недоступна через API Messenger, но доступные диалоги продолжают работать. BORIS проверяет каждый чат отдельно перед отправкой и не дублирует сообщения."
        elif str(cap.get("state") or "") == "degraded":
            state, reason = "degraded", "Avito Messenger сейчас отвечает с ошибкой. BORIS продолжит безопасные проверки без повторной отправки."
        elif active_candidates == 0:
            state, reason = "ready_empty", "Реактивация работает. Подходящих клиентов сейчас не найдено."
        elif ready == 0:
            state, reason = "preparing", f"Есть {active_candidates} клиентов для реактивации, но готовых сообщений пока нет. BORIS продолжает подготовку и безопасные проверки."
        elif (st.get("mode") or "recommend") == "auto" and auto_sendable > 0:
            state = "ready"
            reason = f"Автопилот: {auto_sendable} сообщений разрешены к автоматической отправке в рабочее окно."
            if hold_total:
                reason += f" Ещё {hold_total} удерживаются безопасностью и не попадут в автоотправку."
            if awaiting_revalidation:
                reason += f" Из них {awaiting_revalidation} BORIS перепроверяет сам перед автозапуском."
            if not send_window_open:
                reason += " Сейчас отправка закрыта по расписанию; очередь возобновится автоматически в разрешённые часы."
        elif (st.get("mode") or "recommend") == "auto" and awaiting_revalidation > 0:
            state, reason = "self_healing", f"BORIS сам перепроверяет {awaiting_revalidation} старых безопасных сообщений перед автозапуском; слепой отправки не будет."
        elif (st.get("mode") or "recommend") == "auto" and hold_total > 0:
            state, reason = "needs_safe_refresh", f"{hold_total} готовых черновиков нельзя безопасно отправить автопилотом. BORIS удерживает их по текущим данным диалога, квалификации и антидублю."
        else:
            state, reason = "ready", f"Можно работать: {active_candidates} клиентов в очереди, {ready} сообщений подготовлено; режим подтверждения включён."
        outcome = db.execute(text("""
            WITH sent AS (
                SELECT m.id,m.avito_chat_id,m.sent_at,m.avito_message_id
                  FROM reactivation_messages m
                 WHERE m.account_id=:a AND m.status='sent' AND m.sent_at IS NOT NULL
            )
            SELECT count(*) sent,
                   count(*) FILTER (WHERE avito_message_id IS NOT NULL) receipted,
                   count(*) FILTER (WHERE EXISTS(
                        SELECT 1 FROM messenger_messages mm
                         WHERE mm.account_id=:a AND mm.avito_chat_id=sent.avito_chat_id
                           AND lower(coalesce(mm.direction,'')) LIKE 'in%'
                           AND to_timestamp(mm.avito_created_at)>sent.sent_at
                   )) replied
              FROM sent
        """), {"a":acc}).mappings().first() or {}
        sent_total=int(outcome.get("sent") or 0); replied_total=int(outcome.get("replied") or 0)
        out.append({"account_id": acc, "name": names.get(acc) or acc, "state": state, "reason": reason,
                    "enabled": enabled, "mode": st.get("mode") or "recommend", "mop_active": mop_active,
                    "mop_expired": bool(mop.get("expired")), "mop_left": mop.get("left"),
                    "active_candidates": active_candidates, **queue_truth,
                    "send_window_open": send_window_open,
                    "sent_today": used, "daily_limit": limit, "remaining_today": max(0, limit-used),
                    "sent_total": sent_total, "receipted_total": int(outcome.get("receipted") or 0),
                    "replied_total": replied_total,
                    "reply_rate_pct": round(100.0*replied_total/sent_total,1) if sent_total else None,
                    "delivery_unknown": delivery_unknown, "blocked_transport": blocked_transport,
                    "transport_restored_total": int(recovery.get("restored_total") or 0),
                    "last_transport_restored_at": recovery.get("last_restored_at"),
                    "avito_capability": cap, "last_transport_restoration": restored})
    counts = {}
    for x in out: counts[x["state"]] = counts.get(x["state"], 0) + 1
    return {"accounts": out, "counts": counts, "total": len(out)}


@router.get("/operational-health")
def operational_health(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    """Factual runtime health of reactivation, separate from sales-health score.

    Recommend-mode queues waiting for owner selection are healthy by design;
    scheduler/errors/transport uncertainty are surfaced separately. Read-only.
    """
    from datetime import datetime as _dt, timezone as _tz
    from app.models.reliability import ReliabilityHeartbeat
    from app.api.messenger import get_manager_balance
    scoped = _scope_accounts(db, user, account_id)
    hb = db.query(ReliabilityHeartbeat).filter(
        ReliabilityHeartbeat.module == "runtime",
        ReliabilityHeartbeat.worker_id == "reactivation_scheduler",
    ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    hb_age = None
    if hb and hb.last_seen_at:
        seen = hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=_tz.utc)
        hb_age = max(0, int((_dt.now(_tz.utc) - seen).total_seconds()))
    accounts = []
    for acc in scoped:
        st = rc.settings_for(db, acc) or {}
        enabled = st.get("enabled") is not False
        try: mop_active = bool((get_manager_balance(acc) or {}).get("active"))
        except Exception: mop_active = False
        counts = dict(db.execute(text("SELECT status,count(*) FROM reactivation_candidates WHERE account_id=:a GROUP BY status"), {"a":acc}).all())
        mcounts = dict(db.execute(text("SELECT status,count(*) FROM reactivation_messages WHERE account_id=:a GROUP BY status"), {"a":acc}).all())
        errors = dict(db.execute(text("SELECT coalesce(error_code,'unknown'),count(*) FROM reactivation_messages WHERE account_id=:a AND status IN ('failed','delivery_unknown','blocked') GROUP BY 1"), {"a":acc}).all())
        try:
            from app.reactivation_avito_health import effective_capability
            cap = effective_capability(db, acc)
        except Exception:
            cap = {}
        used = int(rc.used_today(db, acc)); limit = int(st.get("daily_limit") or 10)
        queue_truth = ready_queue_truth(db, acc)
        ready = int(queue_truth.get("ready_messages") or 0)
        outcome = db.execute(text("""
            WITH sent AS (
                SELECT m.id,m.avito_chat_id,m.sent_at,m.avito_message_id
                  FROM reactivation_messages m
                 WHERE m.account_id=:a AND m.status='sent' AND m.sent_at IS NOT NULL
            ), facts AS (
                SELECT s.*,
                       EXISTS(SELECT 1 FROM messenger_messages mm
                               WHERE mm.account_id=:a AND mm.avito_chat_id=s.avito_chat_id
                                 AND lower(coalesce(mm.direction,'')) LIKE 'in%'
                                 AND to_timestamp(mm.avito_created_at)>s.sent_at) AS replied,
                       EXISTS(SELECT 1 FROM boris_crm_deals d
                               WHERE d.avito_account_id=:a AND d.avito_chat_id=s.avito_chat_id) AS crm_linked
                  FROM sent s
            )
            SELECT count(*) sent,
                   count(*) FILTER (WHERE avito_message_id IS NOT NULL) receipted,
                   count(*) FILTER (WHERE replied) replied,
                   count(*) FILTER (WHERE replied AND crm_linked) replied_crm,
                   max(sent_at) last_sent_at
              FROM facts
        """), {"a":acc}).mappings().first() or {}
        sent_total=int(outcome.get("sent") or 0); replied_total=int(outcome.get("replied") or 0)
        reply_rate=round((100.0*replied_total/sent_total),1) if sent_total else None
        if not enabled:
            state, classification = "stopped_expected", "intentionally_disabled"
        elif not mop_active:
            state, classification = "stopped_expected", "mop_or_entitlement_inactive"
        elif hb_age is None or hb_age > 1800:
            state, classification = "degraded", "scheduler_stale"
        elif str(getattr(hb, "state", "") or "") != "ok":
            state, classification = "degraded", "scheduler_degraded"
        elif int(mcounts.get("delivery_unknown") or 0) and str(cap.get("classification") or "") == "avito_messenger_subscription_required":
            state, classification = "waiting_external", "delivery_unknown_waiting_avito_read_access"
        elif int(mcounts.get("delivery_unknown") or 0):
            state, classification = "needs_attention", "delivery_unknown_no_auto_retry"
        elif str(cap.get("classification") or "") == "avito_messenger_subscription_required":
            state, classification = "waiting_external", "avito_messenger_subscription_required"
        elif str(cap.get("classification") or "") == "avito_messenger_history_partial_402":
            state, classification = "partial_external", "avito_messenger_partial_chat_access"
        elif str(cap.get("state") or "") == "degraded":
            state, classification = "degraded", "avito_transport_degraded"
        elif not cap and int(errors.get("transport_unavailable") or 0):
            state, classification = "waiting_external", "avito_transport_unavailable_unverified"
        elif (st.get("mode") or "recommend") == "auto" and ready and int(queue_truth.get("auto_sendable") or 0) == 0 and int(queue_truth.get("awaiting_revalidation") or 0) > 0:
            state, classification = "self_healing", "auto_queue_revalidation_pending"
        elif (st.get("mode") or "recommend") == "auto" and ready and int(queue_truth.get("auto_sendable") or 0) == 0 and int(queue_truth.get("hold_total") or 0) > 0:
            state, classification = "needs_attention", "auto_queue_requires_safe_refresh_or_confirmation"
        elif (st.get("mode") or "recommend") != "auto" and ready:
            state, classification = "healthy_waiting_confirmation", "waiting_owner_selection"
        else:
            state, classification = "healthy", "flow_ok"
        accounts.append({"account_id":acc,"enabled":enabled,"mode":st.get("mode") or "recommend","mop_active":mop_active,
            "state":state,"classification":classification,"candidates":counts,"messages":mcounts,"message_errors":errors,
            "queue_truth":queue_truth,"avito_capability":cap,
            "sent_today":used,"daily_limit":limit,"remaining_today":max(0,limit-used),
            "outcome":{"sent":sent_total,"receipted":int(outcome.get("receipted") or 0),
                       "replied":replied_total,"replied_crm":int(outcome.get("replied_crm") or 0),
                       "reply_rate_pct":reply_rate,"last_sent_at":outcome.get("last_sent_at")}})
    bad = sum(1 for x in accounts if x["state"] in {"degraded","needs_attention"})
    waiting_external = sum(1 for x in accounts if x["state"] == "waiting_external")
    partial_external = sum(1 for x in accounts if x["state"] == "partial_external")
    persistent_external = sum(1 for x in accounts if bool(((x.get("avito_capability") or {}).get("persistence") or {}).get("persistent")))
    overall = "degraded" if bad else ("partial_external" if partial_external else ("waiting_external" if waiting_external else "ok"))
    return {"status":overall,"scheduler":{"state":getattr(hb,"state",None),"age_sec":hb_age,"details":getattr(hb,"details_json",{}) or {}},
            "accounts":accounts,"degraded_accounts":bad,"waiting_external_accounts":waiting_external,
            "partial_external_accounts":partial_external,"persistent_external_accounts":persistent_external,
            "checked_at":_dt.now(_tz.utc).isoformat()}


@router.get("/avito-capability")
def avito_capability(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    """Фактическая capability-карта Avito Messenger для реактивации. Только чтение."""
    from app.reactivation_avito_health import effective_capability
    accs = _scope_accounts(db, user, account_id)
    out = []
    for acc in accs:
        try: data = effective_capability(db, acc)
        except Exception: data = {}
        out.append({"account_id": acc, **data})
    return {"status": "ok", "accounts": out, "total": len(out)}


@router.get("/health")
def health_index(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    """Индекс здоровья отдела продаж по аккаунтам этого пользователя."""
    from app.reactivation_health import health
    accs = _mop_accounts(db, _not_disabled(db, _scope_accounts(db, user, account_id)))
    return health(db, accs)


@router.get("/marks")
def marks(account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    """Метки для списка диалогов в едином окне: по каждому чату — что с ним не так.
    Одним запросом на весь экран, чтобы не дёргать API на каждую строку."""
    accs = _my_accounts(db, user)
    if account_id and account_id != "all":
        accs = [a for a in accs if a == account_id]
    out = {}
    for x in _rows(db, accs):
        out[x["avito_chat_id"]] = {
            "queue": x["queue"], "reason": x["primary_reason"], "age_days": x["age"],
            "has_draft": bool(x["final_text"]), "candidate_id": x["id"],
        }
    return {"status": "ok", "marks": out}


@router.get("/candidates")
def candidates(queue: str = "reactivation", account_id: str = "", db=Depends(_db), user=Depends(get_current_user)):
    rows = [x for x in _rows(db, _scope_accounts(db, user, account_id)) if x["queue"] == queue]
    if queue in ("hot", "late"):
        rows.sort(key=lambda z: -z["age"])
    return {"queue": queue, "items": [_card(db, x) for x in rows]}


class HandoffIn(BaseModel):
    account_id: str
    avito_chat_id: str
    reason: str = "manager_followup"
    summary: str = ""


@router.post("/handoff")
def handoff_from_dialog(body: HandoffIn, db=Depends(_db), user=Depends(get_current_user)):
    """Put the current real dialog into the existing reactivation engine.

    No send and no AI generation: preserve manager-provided reason/context and
    reuse reactivation_core idempotency (one active candidate per dialog).
    """
    if body.account_id not in _my_accounts(db, user):
        raise HTTPException(404, "аккаунт не найден")
    last = db.execute(text("""
        SELECT avito_message_id,avito_created_at,text FROM messenger_messages
         WHERE account_id=:a AND avito_chat_id=:c
           AND coalesce(msg_type,'') <> 'system'
         ORDER BY avito_created_at DESC,id DESC LIMIT 1
    """), {"a": body.account_id, "c": body.avito_chat_id}).fetchone()
    if not last:
        raise HTTPException(404, "диалог не найден")
    reason = (body.reason or "manager_followup").strip()[:120]
    summary = (body.summary or "").strip()[:1000] or "Менеджер передал диалог на реактивацию из рабочего окна."
    evidence = [str(last[0])] if last[0] else []
    cid, state = rc.create_candidate(db, body.account_id, body.avito_chat_id, reason, [reason],
                                     datetime.now(timezone.utc), summary=summary, evidence=evidence)
    db.commit()
    return {"ok": True, "candidate_id": cid, "result": state, "reason": reason, "summary": summary}


@router.get("/candidate/{cid}")
def candidate(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    rows = [x for x in _rows(db, _my_accounts(db, user)) if x["id"] == cid]
    if not rows:
        raise HTTPException(404, "кандидат не найден")
    d = rows[0]
    card = _card(db, d)
    card["history"] = [
        {"id": r[0], "from": "клиент" if str(r[1]).lower().startswith("in") else "мы",
         "at": str(r[2]), "text": r[3]}
        for r in db.execute(text(
            "SELECT id, direction, to_timestamp(avito_created_at), left(coalesce(text,''),400)"
            " FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c"
            "   AND coalesce(msg_type,'') <> 'system' AND btrim(coalesce(text,'')) <> ''"
            " ORDER BY avito_created_at DESC, id DESC LIMIT 12"),
            {"a": d["account_id"], "c": d["avito_chat_id"]}).fetchall()][::-1]
    card["evidence"] = d["evidence_message_ids"]
    card["events"] = [
        {"event": r[0], "at": str(r[1])}
        for r in db.execute(text(
            "SELECT event, at FROM reactivation_events WHERE candidate_id=:i ORDER BY id"),
            {"i": cid}).fetchall()]
    return card


def _act(db, user, cid, to_status, event):
    rows = [x for x in _rows(db, _my_accounts(db, user)) if x["id"] == cid]
    if not rows:
        raise HTTPException(404, "кандидат не найден")
    ok = rc.set_candidate_status(db, cid, to_status, (rows[0]["status"],), event,
                                 actor_type="user", actor_id=getattr(user, "id", None))
    db.commit()
    if not ok:
        raise HTTPException(409, "статус изменился, обновите страницу")
    return {"ok": True, "status": to_status}


@router.post("/candidate/{cid}/take")
def take(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    """«Взять в работу»: дальше отвечает человек, автоматика не вмешивается."""
    return _act(db, user, cid, "manager_taken_over", "manager_takeover")


@router.post("/candidate/{cid}/exclude")
def exclude(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    return _act(db, user, cid, "excluded", "excluded")


@router.post("/candidate/{cid}/dnc")
def dnc(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    res = _act(db, user, cid, "do_not_contact", "do_not_contact")
    db.execute(text("UPDATE reactivation_candidates SET do_not_contact = true WHERE id=:i"),
               {"i": cid})
    db.commit()
    return res


class DraftIn(BaseModel):
    text: str


@router.post("/message/{mid}/text")
def edit_draft(mid: int, body: DraftIn, db=Depends(_db), user=Depends(get_current_user)):
    row = db.execute(text(
        "SELECT account_id FROM reactivation_messages WHERE id=:i"), {"i": mid}).fetchone()
    if not row or row[0] not in _my_accounts(db, user):
        raise HTTPException(404, "черновик не найден")
    if not rc.set_final_text(db, mid, body.text, actor_type="user",
                             actor_id=getattr(user, "id", None)):
        raise HTTPException(409, "черновик уже нельзя менять")
    db.commit()
    return {"ok": True}


# === BORIS_REACTIVATION_RUNTIME_V2 ===
class ReactivationSettingsIn(BaseModel):
    enabled: bool = True
    mode: str = "recommend"  # recommend | auto
    daily_limit: int = 10
    max_attempts_per_dialog: int = 2
    allowed_hours_from: int = 10
    allowed_hours_to: int = 20
    timezone: str = "Europe/Moscow"


@router.get("/settings")
def get_settings(account_id: str, db=Depends(_db), user=Depends(get_current_user)):
    if account_id not in _my_accounts(db, user):
        raise HTTPException(404, "аккаунт не найден")
    s = rc.settings_for(db, account_id)
    if not s:
        s = {"enabled": True, "mode": "recommend", "daily_limit": 10,
             "max_attempts_per_dialog": 2, "allowed_hours_from": 10,
             "allowed_hours_to": 20, "timezone": "Europe/Moscow"}
    from app.api.messenger import get_manager_balance
    return {**s, "sending_enabled": True, "mop": get_manager_balance(account_id)}


@router.put("/settings")
def save_settings(body: ReactivationSettingsIn, account_id: str, db=Depends(_db), user=Depends(get_current_user)):
    if account_id not in _my_accounts(db, user):
        raise HTTPException(404, "аккаунт не найден")
    if body.mode not in ("recommend", "auto"):
        raise HTTPException(400, "режим должен быть recommend или auto")
    if not 1 <= body.daily_limit <= 100:
        raise HTTPException(400, "дневной лимит должен быть от 1 до 100")
    if not 1 <= body.max_attempts_per_dialog <= 5:
        raise HTTPException(400, "число попыток должно быть от 1 до 5")
    if not 0 <= body.allowed_hours_from <= 23 or not 1 <= body.allowed_hours_to <= 24:
        raise HTTPException(400, "неверные часы отправки")
    db.execute(text("""
        INSERT INTO reactivation_settings
            (account_id,enabled,mode,daily_limit,max_attempts_per_dialog,
             allowed_hours_from,allowed_hours_to,timezone,updated_at)
        VALUES (:a,:e,:m,:d,:x,:f,:t,:z,now())
        ON CONFLICT (account_id) DO UPDATE SET
            enabled=excluded.enabled,mode=excluded.mode,daily_limit=excluded.daily_limit,
            max_attempts_per_dialog=excluded.max_attempts_per_dialog,
            allowed_hours_from=excluded.allowed_hours_from,allowed_hours_to=excluded.allowed_hours_to,
            timezone=excluded.timezone,
            -- REACTIVATION_AUTO_AUTHORITY_EPOCH_V1:
            -- updated_at is the auto-authority boundary, not a generic save time.
            -- A no-op save or a daily-limit/hours edit must not strand every
            -- already validated ready message. Only authority changes move it.
            updated_at=CASE
                WHEN reactivation_settings.mode IS DISTINCT FROM excluded.mode
                  OR reactivation_settings.enabled IS DISTINCT FROM excluded.enabled
                THEN now()
                ELSE reactivation_settings.updated_at
            END
    """), {"a": account_id, "e": body.enabled, "m": body.mode, "d": body.daily_limit,
             "x": body.max_attempts_per_dialog, "f": body.allowed_hours_from,
             "t": body.allowed_hours_to, "z": body.timezone})
    db.commit()
    return {"ok": True, "account_id": account_id, "mode": body.mode}


@router.post("/candidate/{cid}/generate")
def generate_candidate_message(cid: int, db=Depends(_db), user=Depends(get_current_user)):
    row = db.execute(text("SELECT account_id FROM reactivation_candidates WHERE id=:i"), {"i": cid}).fetchone()
    if not row or row[0] not in _my_accounts(db, user):
        raise HTTPException(404, "кандидат не найден")
    from app.reactivation_runtime import generate_for_candidate
    result = generate_for_candidate(db, cid)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason") or "не удалось подготовить сообщение")
    return result


class BulkCandidatesIn(BaseModel):
    candidate_ids: list[int]

class BulkPreflightIn(BaseModel):
    candidate_ids: list[int]


@router.post("/bulk/prepare")
def bulk_prepare(body: BulkCandidatesIn, db=Depends(_db), user=Depends(get_current_user)):
    ids = list(dict.fromkeys(int(x) for x in body.candidate_ids if int(x) > 0))[:100]
    if not ids:
        raise HTTPException(400, "не выбраны клиенты")
    allowed = set(_my_accounts(db, user))
    rows = db.execute(text("SELECT id,account_id FROM reactivation_candidates WHERE id = ANY(:ids)"), {"ids": ids}).fetchall()
    found = {int(r[0]): r[1] for r in rows if r[1] in allowed}
    from app.reactivation_runtime import generate_for_candidate
    out = []
    for cid in ids:
        if cid not in found:
            out.append({"candidate_id": cid, "ok": False, "reason": "not_found_or_forbidden"}); continue
        try:
            r = generate_for_candidate(db, cid)
            out.append({"candidate_id": cid, **(r if isinstance(r, dict) else {"ok": False, "reason": "prepare_failed"})})
        except Exception as exc:
            db.rollback()
            out.append({"candidate_id": cid, "ok": False, "reason": str(exc)[:180]})
    return {"ok": True, "requested": len(ids), "prepared": sum(1 for x in out if x.get("ok")), "results": out}


@router.post("/bulk/preflight")
def bulk_preflight(body: BulkPreflightIn, db=Depends(_db), user=Depends(get_current_user)):
    ids = list(dict.fromkeys(int(x) for x in body.candidate_ids if int(x) > 0))[:100]
    if not ids:
        raise HTTPException(400, "не выбраны клиенты")
    allowed = set(_my_accounts(db, user))
    rows = db.execute(text("""
        SELECT c.id,c.account_id,m.id AS message_id
          FROM reactivation_candidates c
          LEFT JOIN LATERAL (
            SELECT id FROM reactivation_messages
             WHERE candidate_id=c.id AND status <> 'cancelled'
             ORDER BY attempt_number DESC,id DESC LIMIT 1
          ) m ON true
         WHERE c.id = ANY(:ids)
    """), {"ids": ids}).fetchall()
    found = {int(r[0]): (r[1], r[2]) for r in rows if r[1] in allowed}
    from app.reactivation_runtime import preflight_send
    out = []
    for cid in ids:
        row = found.get(cid)
        if not row:
            out.append({"candidate_id": cid, "ok": False, "reason": "not_found_or_forbidden"}); continue
        mid = row[1]
        if not mid:
            out.append({"candidate_id": cid, "ok": False, "reason": "draft_missing"}); continue
        try:
            ok, reason, _ = preflight_send(db, int(mid), explicit=True)
            out.append({"candidate_id": cid, "message_id": int(mid), "ok": bool(ok), "reason": reason})
        except Exception as exc:
            db.rollback()
            out.append({"candidate_id": cid, "message_id": int(mid), "ok": False, "reason": str(exc)[:180]})
    return {"ok": True, "requested": len(ids), "sendable": sum(1 for x in out if x.get("ok")), "blocked": sum(1 for x in out if not x.get("ok")), "results": out}


@router.post("/bulk/send")
def bulk_send(body: BulkCandidatesIn, db=Depends(_db), user=Depends(get_current_user)):
    ids = list(dict.fromkeys(int(x) for x in body.candidate_ids if int(x) > 0))[:100]
    if not ids:
        raise HTTPException(400, "не выбраны клиенты")
    allowed = set(_my_accounts(db, user))
    rows = db.execute(text("""
        SELECT c.id,c.account_id,m.id AS message_id
          FROM reactivation_candidates c
          LEFT JOIN LATERAL (
            SELECT id FROM reactivation_messages
             WHERE candidate_id=c.id AND status <> 'cancelled'
             ORDER BY attempt_number DESC,id DESC LIMIT 1
          ) m ON true
         WHERE c.id = ANY(:ids)
    """), {"ids": ids}).fetchall()
    found = {int(r[0]): (r[1], r[2]) for r in rows if r[1] in allowed}
    from app.reactivation_runtime import send_message
    out = []
    for cid in ids:
        row = found.get(cid)
        if not row:
            out.append({"candidate_id": cid, "ok": False, "reason": "not_found_or_forbidden"}); continue
        mid = row[1]
        if not mid:
            out.append({"candidate_id": cid, "ok": False, "reason": "draft_missing"}); continue
        try:
            r = send_message(db, int(mid), explicit=True, actor="user:%s" % getattr(user, "id", "unknown"))
            out.append({"candidate_id": cid, "message_id": int(mid), **(r if isinstance(r, dict) else {"ok": False, "reason": "send_failed"})})
        except Exception as exc:
            db.rollback()
            out.append({"candidate_id": cid, "message_id": int(mid), "ok": False, "reason": str(exc)[:180]})
    return {"ok": True, "requested": len(ids), "sent": sum(1 for x in out if x.get("ok")), "failed": sum(1 for x in out if not x.get("ok")), "results": out}


@router.post("/message/{mid}/send")
def send_reactivation_message(mid: int, db=Depends(_db), user=Depends(get_current_user)):
    row = db.execute(text("SELECT account_id FROM reactivation_messages WHERE id=:i"), {"i": mid}).fetchone()
    if not row or row[0] not in _my_accounts(db, user):
        raise HTTPException(404, "сообщение не найдено")
    from app.reactivation_runtime import send_message
    result = send_message(db, mid, explicit=True, actor="user:%s" % getattr(user, "id", "unknown"))
    if not result.get("ok"):
        reason = result.get("reason") or "send_failed"
        human = {
            "outside_allowed_hours": "Сейчас вне разрешённых часов отправки",
            "daily_limit": "Дневной лимит реактивации исчерпан",
            "attempt_limit": "Лимит попыток по этому клиенту исчерпан",
            "client_replied": "Клиент уже ответил — реактивация больше не нужна",
            "dialog_changed_after_draft": "После подготовки сообщения диалог изменился — сначала обновите текст",
            "mop_inactive": "МОП не активен или пакет сообщений закончился",
            "customer_declined": "Клиент явно отказался или попросил не писать — BORIS не будет его догонять",
            "candidate_blocked": "Этот клиент исключён из реактивации",
            "ai_fallback_requires_confirmation": "Текст слишком общий для автопилота — проверьте его вручную",
            "duplicate_outgoing_text": "Такой текст уже отправлялся этому клиенту — BORIS не будет дублировать сообщение",
            "transport_unavailable": "Avito не разрешает отправку через API для этого аккаунта — проверьте тариф/доступ",
            "delivery_unknown": "Avito мог принять сообщение, поэтому BORIS не повторяет отправку автоматически",
        }.get(reason, reason)
        raise HTTPException(409, human)
    return result
