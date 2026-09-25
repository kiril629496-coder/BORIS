# -*- coding: utf-8 -*-
"""Предфильтр и поиск кандидатов на реактивацию.
Ни одного обращения к модели: только SQL. AI подключается отдельным шагом.
Режим dry=True ничего не пишет в базу."""
import logging
import re

from sqlalchemy import text

import app.reactivation_core as rc

log = logging.getLogger(__name__)

# Грубый предфильтр «обсуждали цену». Его задача — сузить выборку перед AI,
# а не выносить решение. Окончательный вердикт даёт структурированный анализ.
PRICE_RE = (r"(цена|цену|ценам|ценой|стоимост|стоит|стоить|стоил|"
            r"рассчит|расчит|расч[её]т|посчит|подсчит|смет|бюджет|"
            r"сколько будет|во сколько|почём|почем|прайс|"
            r"коммерческ|услови[яй] доставк|комплектац)")

# Последнее сообщение клиента, закрывающее разговор. Не приговор, а повод
# отправить кандидата человеку на проверку вместо автоматической отправки.
CLOSING_RE = (r"^\s*(договорились|спасибо|спасиб|благодар|хорошо|ок|окей|понял|поняла|"
              r"написал|написала|уже купил|уже заказал|не актуально|неактуально|"
              r"не нужно|не интересует|отказ|\W+)\s*$")

# Системные сообщения Avito приходят с этим префиксом, а msg_type у них 'user' —
# он вычисляется по направлению, а не по содержимому. Фильтруем по тексту.
SYSTEM_PREFIX_RE = r"^\[Системное сообщение\]"

# Если последнее сообщение продавца уже обещает передачу специалисту/звонок,
# клиент не должен получать автоматическую «реактивацию» поверх этого обещания.
# Такой диалог уходит только на проверку человеку.
HANDOFF_PENDING_RE = (
    r"(?:передал|передам|передаю).{0,100}(?:специалист|коллег|менеджер)|"
    r"(?:специалист|коллег|менеджер).{0,100}(?:свяж|позвон|набер)|"
    r"\bсвяжется\s+с\s+вами\b|\bпозвонит\s+вам\b|\bнаберет\s+вас\b|\bнаберёт\s+вас\b"
)

# If the customer explicitly says they will write/contact us after their own next
# event, silence is expected. Autopilot must not chase them before they return.
CLIENT_RECONTACT_RE = (
    r"(?:\b(?:сам(?:а)?\s+)?(?:напишу|отпишусь|свяжусь)\b.{0,40}\b(?:позже|потом|как|когда|после)\b|"
    r"\b(?:как|когда|после).{0,55}\b(?:напишу|отпишусь|свяжусь)\b|"
    r"\b(?:узнаю|уточню|посмотрю|подумаю|решу|приедет|приеду|вернется|вернётся).{0,55}\b(?:напишу|отпишусь|свяжусь)\b)"
)

# Верхняя граница давности: диалоги старше — отдельная история, часто вообще
# из прошлой жизни аккаунта. Пока константа, в настройках такого поля нет.
MAX_AGE_DAYS = 180

# Сколько дней молчания считаем поводом. Пока константа: в reactivation_settings
# такого поля НЕТ, добавление потребует отдельного согласованного ALTER.
DEFAULT_SILENCE_DAYS = 3

SQL = """
WITH msgs AS (
  SELECT id, avito_chat_id, direction, text, item_id, item_title,
         avito_created_at, to_timestamp(avito_created_at) AS ts
  FROM messenger_messages
  WHERE account_id = :acc AND coalesce(msg_type,'') <> 'system'
    AND coalesce(text,'') !~ :sys_re
),
last_msg AS (
  SELECT DISTINCT ON (avito_chat_id)
         avito_chat_id, id AS last_msg_id, direction, ts, item_id, item_title, text AS last_text
  FROM msgs ORDER BY avito_chat_id, avito_created_at DESC, id DESC
),
maxid AS (
  SELECT avito_chat_id, max(id) AS last_id, count(*) AS msg_count FROM msgs GROUP BY avito_chat_id
),
price_hit AS (
  SELECT avito_chat_id, min(id) AS ev_id
  FROM msgs WHERE lower(direction) IN ('in','incoming') AND text ~* :price_re
  GROUP BY avito_chat_id
),
last_in AS (
  SELECT DISTINCT ON (avito_chat_id) avito_chat_id, id AS in_id, text AS in_text
  FROM msgs WHERE lower(direction) IN ('in','incoming')
  ORDER BY avito_chat_id, avito_created_at DESC, id DESC
),
has_content AS (
  SELECT DISTINCT avito_chat_id FROM msgs
  WHERE lower(direction) IN ('in','incoming') AND btrim(coalesce(text,'')) <> ''
)
SELECT l.avito_chat_id, l.ts, l.item_id, l.item_title, l.last_msg_id, l.last_text,
       m.last_id, m.msg_count, p.ev_id,
       i.in_text, (i.in_text ~* :closing_re) AS closing,
       (hc.avito_chat_id IS NOT NULL) AS has_content
FROM last_msg l
JOIN maxid m ON m.avito_chat_id = l.avito_chat_id
LEFT JOIN price_hit p ON p.avito_chat_id = l.avito_chat_id
LEFT JOIN last_in i ON i.avito_chat_id = l.avito_chat_id
LEFT JOIN has_content hc ON hc.avito_chat_id = l.avito_chat_id
WHERE lower(l.direction) IN ('out','outgoing')
  AND l.ts < now() - make_interval(days => :silence)
  AND l.ts > now() - make_interval(days => :max_age)
  AND NOT EXISTS (
        SELECT 1 FROM reactivation_candidates c
        WHERE c.account_id = :acc AND c.avito_chat_id = l.avito_chat_id
          AND (c.do_not_contact = true
               OR c.status IN ('do_not_contact','excluded','candidate','needs_review',
                               'approved','scheduled','sending','sent','cooldown',
                               'delivery_unknown')))
  AND (SELECT count(*) FROM reactivation_messages rm
       WHERE rm.account_id = :acc AND rm.avito_chat_id = l.avito_chat_id
         AND rm.sent_at IS NOT NULL
         AND rm.sent_at > now() - make_interval(days => :window)) < :max_attempts
ORDER BY (p.ev_id IS NOT NULL) DESC, l.ts
"""


def prefilter(db, account_id, silence_days=DEFAULT_SILENCE_DAYS,
              window_days=90, max_attempts=2, max_age_days=MAX_AGE_DAYS,
              include_empty=False):
    """include_empty=False выбрасывает диалоги, где клиент не написал НИ ОДНОГО
    непустого текста: просмотры номера, звонки, картинки без подписи и монологи
    продавца. В модель такие отправлять незачем."""
    rows = db.execute(text(SQL), {
        "acc": account_id, "price_re": PRICE_RE, "closing_re": CLOSING_RE,
        "sys_re": SYSTEM_PREFIX_RE, "silence": silence_days, "max_age": max_age_days,
        "window": window_days, "max_attempts": max_attempts}).fetchall()
    out = []
    for r in rows:
        (chat, ts, item_id, item_title, last_msg_id, last_text, last_id, msg_count, ev_id,
         in_text, closing, has_content) = r
        handoff_pending = bool(re.search(HANDOFF_PENDING_RE, str(last_text or ""), re.I | re.S))
        customer_recontact = bool(re.search(CLIENT_RECONTACT_RE, str(in_text or ""), re.I | re.S))
        matched = ["no_reply"]
        if ev_id:
            matched.insert(0, "price_requested")
        out.append({
            "avito_chat_id": chat, "last_activity": ts, "item_id": item_id,
            "item_title": item_title, "last_analyzed_message_id": last_id,
            "msg_count": msg_count, "matched_reasons": matched,
            "primary_reason": matched[0],
            "evidence": [x for x in (ev_id, last_msg_id) if x],
            "last_incoming": in_text,
            "manager_handoff_pending": handoff_pending,
            "customer_promised_to_recontact": customer_recontact,
            "needs_review": bool(closing or handoff_pending or customer_recontact),
            "has_content": bool(has_content),
        })
    if not include_empty:
        out = [r for r in out if r["has_content"]]
    return out


def prefilter_stats(db, account_id, **kw):
    """Сколько нашли, сколько выбросили как пустые, сколько уйдёт в модель."""
    kw.pop("include_empty", None)
    every = prefilter(db, account_id, include_empty=True, **kw)
    live = [r for r in every if r["has_content"]]
    return {"account_id": account_id, "found": len(every),
            "excluded_empty": len(every) - len(live), "to_ai": len(live)}


def pick_primary(matched, reasons_enabled):
    """Приоритет из ядра, но только среди включённых причин аккаунта."""
    allowed = [r for r in matched if r in (reasons_enabled or [])]
    for r in rc.REASON_PRIORITY:
        if r in allowed:
            return r
    return allowed[0] if allowed else None


def _qualification_policy(db, account_id, chat_id):
    """Reuse current MOP qualification instead of treating every silence as no_reply."""
    import json
    try:
        raw = db.execute(text("""
            SELECT value FROM storage
             WHERE account_id=:a AND key=:k
             ORDER BY id DESC LIMIT 1
        """), {"a": account_id, "k": "mop_qualification:" + str(chat_id)}).scalar()
        q = json.loads(raw) if raw else {}
    except Exception:
        return {"skip": False, "needs_review": False, "reason": None}
    if not isinstance(q, dict):
        return {"skip": False, "needs_review": False, "reason": None}
    if q.get("reactivation_candidate") is False:
        return {"skip": True, "needs_review": False, "reason": "qualification_reactivation_disabled"}
    if q.get("human_handoff") is True:
        return {"skip": False, "needs_review": True, "reason": "human_handoff_pending"}
    phone = bool(q.get("phone_received"))
    stage = str(q.get("qualification_stage") or "").upper()
    action = " ".join(str(q.get(k) or "") for k in ("target_action", "next_action", "handoff_reason")).lower()
    if phone and stage == "TARGET_ACTION" and any(x in action for x in (
        "связ", "позвон", "звон", "специалист", "менеджер", "коллег"
    )):
        return {"skip": False, "needs_review": True, "reason": "target_action_pending"}
    return {"skip": False, "needs_review": False, "reason": None}


def scan_account(db, account_id, dry=True, silence_days=None, limit=None):
    """dry=True — ничего не пишет, только возвращает найденное."""
    st = rc.settings_for(db, account_id)
    # Default-on contract: every account with an active MOP participates even
    # before the owner has opened Reactivation settings.  Explicit disabled=false
    # remains authoritative.  Keep defaults in the existing core, no second engine.
    if st is None:
        st = {
            "enabled": True,
            "reasons_enabled": list(rc.REASON_PRIORITY),
            "attempts_window_days": 90,
            "max_attempts_per_dialog": 2,
        }
    if not st["enabled"]:
        return {"account_id": account_id, "skipped": "disabled", "found": 0, "items": [],
                "disabled_reason": st.get("disabled_reason")}
    reasons = st.get("reasons_enabled") or []
    found = prefilter(db, account_id,
                      silence_days=(silence_days or DEFAULT_SILENCE_DAYS),
                      window_days=st["attempts_window_days"],
                      max_attempts=st["max_attempts_per_dialog"])
    items, created, skipped = [], 0, 0
    for row in found:
        qualification = _qualification_policy(db, account_id, row["avito_chat_id"])
        if qualification.get("skip"):
            skipped += 1
            continue
        if qualification.get("needs_review"):
            row["needs_review"] = True
            row["qualification_hold_reason"] = qualification.get("reason")
        primary = pick_primary(row["matched_reasons"], reasons)
        if not primary:
            skipped += 1
            continue
        row["primary_reason"] = primary
        items.append(row)
        if limit and len(items) >= limit:
            break
    try:
        from app.reactivation_profile import apply_profile
        apply_profile(db, account_id, items)
    except Exception as e:
        log.warning("профильная проверка пропущена для %s: %s", account_id, e)
    if dry:
        return {"account_id": account_id, "found": len(items), "no_enabled_reason": skipped,
                "items": items, "dry": True}
    for row in items:
        cid, how = rc.create_candidate(
            db, account_id, row["avito_chat_id"], row["primary_reason"],
            row["matched_reasons"], row["last_activity"], evidence=row["evidence"])
        if how != "created":
            continue
        db.execute(text(
            "UPDATE reactivation_candidates SET last_analyzed_message_id=:m, updated_at=now()"
            " WHERE id=:i"), {"m": row["last_analyzed_message_id"], "i": cid})
        # Закрывающий ответ клиента (не актуально / уже купил / спасибо и т.п.)
        # никогда не попадает в автоотправку. BORIS может подготовить вариант,
        # но отправить его можно только после явной проверки человеком.
        if row.get("needs_review"):
            rc.set_candidate_status(
                db, cid, "needs_review", ("candidate",), "needs_review",
                meta={
                    "source": "reactivation_scan",
                    "reason": row.get("qualification_hold_reason") or (
                        "manager_handoff_pending" if row.get("manager_handoff_pending")
                        else "customer_promised_to_recontact" if row.get("customer_promised_to_recontact")
                        else "closing_reply"
                    ),
                },
            )
        row["candidate_id"] = cid
        created += 1
    db.commit()
    return {"account_id": account_id, "found": len(items), "created": created,
            "no_enabled_reason": skipped, "items": items, "dry": False}
