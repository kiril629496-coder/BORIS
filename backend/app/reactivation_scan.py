# -*- coding: utf-8 -*-
"""Предфильтр и поиск кандидатов на реактивацию.
Ни одного обращения к модели: только SQL. AI подключается отдельным шагом.
Режим dry=True ничего не пишет в базу."""
import logging

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
         avito_chat_id, id AS last_msg_id, direction, ts, item_id, item_title
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
SELECT l.avito_chat_id, l.ts, l.item_id, l.item_title, l.last_msg_id,
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
        (chat, ts, item_id, item_title, last_msg_id, last_id, msg_count, ev_id,
         in_text, closing, has_content) = r
        matched = ["no_reply"]
        if ev_id:
            matched.insert(0, "price_requested")
        out.append({
            "avito_chat_id": chat, "last_activity": ts, "item_id": item_id,
            "item_title": item_title, "last_analyzed_message_id": last_id,
            "msg_count": msg_count, "matched_reasons": matched,
            "primary_reason": matched[0],
            "evidence": [x for x in (ev_id, last_msg_id) if x],
            "last_incoming": in_text, "needs_review": bool(closing),
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


def scan_account(db, account_id, dry=True, silence_days=None, limit=None):
    """dry=True — ничего не пишет, только возвращает найденное."""
    st = rc.settings_for(db, account_id)
    if st is None:
        return {"account_id": account_id, "skipped": "no_settings", "found": 0, "items": []}
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
        row["candidate_id"] = cid
        created += 1
    db.commit()
    return {"account_id": account_id, "found": len(items), "created": created,
            "no_enabled_reason": skipped, "items": items, "dry": False}
