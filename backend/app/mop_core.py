"""Ядро AI-МОПа: единственная точка смены состояния карточки ответа.

Каналы (Telegram, кабинет, позже MAX/WhatsApp) — только экраны. Они вызывают
функции этого модуля и никогда не пишут в Avito сами. Источник истины — БД.
На этом этапе модуль ниоткуда не импортируется рабочим кодом.
"""
import hashlib
import json
import re

from sqlalchemy import bindparam, text

# ---------------------------------------------------------------- справочники

STATUSES = (
    "new", "analyzing", "draft_ready", "in_progress", "editing", "custom_waiting",
    "waiting_confirm", "sending", "sent", "send_failed",
    "deleted", "human_required", "waiting_external", "no_reply_required",
)

TERMINAL = ("sent", "no_reply_required")

# из какого статуса в какой можно перейти
TRANSITIONS = {
    "new": ("analyzing", "draft_ready", "deleted",
            "human_required", "no_reply_required"),
    "analyzing": ("new", "draft_ready", "waiting_external", "deleted", "human_required", "sent"),
    "draft_ready": ("editing", "custom_waiting", "analyzing", "sending",
                    "deleted", "human_required", "no_reply_required",
                    "in_progress", "sent"),
    "in_progress": ("editing", "custom_waiting", "waiting_confirm", "sending",
                    "analyzing", "deleted", "human_required",
                    "no_reply_required", "sent"),
    "editing": ("waiting_confirm", "draft_ready", "deleted", "human_required"),
    "custom_waiting": ("waiting_confirm", "draft_ready", "deleted", "human_required"),
    "waiting_confirm": ("sending", "editing", "deleted", "human_required"),
    "sending": ("sent", "send_failed"),
    "send_failed": ("sending", "editing", "custom_waiting", "human_required", "analyzing", "waiting_external", "no_reply_required"),
    "deleted": ("analyzing", "custom_waiting", "human_required"),
    "human_required": ("new", "draft_ready", "waiting_external", "no_reply_required"),
    "waiting_external": ("new", "draft_ready", "human_required", "no_reply_required"),
    # A sent acknowledgement may still require a manager follow-up. This is
    # delivery-complete but not business-work-complete.
    "sent": ("human_required",),
    "no_reply_required": (),
}

EVENTS = (
    "draft_created", "ai_generated", "ai_failed", "regenerated",
    "edit_requested", "edited", "custom_requested", "custom_written",
    "awaiting_expired", "awaiting_cancelled", "approved", "send_started",
    "sent", "send_failed", "send_recovered", "draft_deleted",
    "handed_to_human", "returned_to_ai", "lead_created", "closed_no_reply",
    "card_posted", "card_no_ai", "taken", "answered_externally",
    "provider_access_restored",
)

REPLY_AUTHORS = ("ai", "ai_regenerated", "manager", "supervisor", "imported", "api")

CHANNELS = ("telegram", "web", "max", "whatsapp", "vk")


class MopError(Exception):
    pass


# ------------------------------------------------------------------ утилиты

def normalize_text(s):
    """Нормализация ПЕРЕД хэшем: иначе 'Привет' и 'Привет ' дадут разные хэши."""
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def deterministic_no_reply_reason(text_value):
    """MOP_DETERMINISTIC_NO_REPLY_V1: zero-AI fail-safe for obvious non-dialogue.

    Return a reason only for high-precision full-message cases. Any question or
    ambiguous customer text stays in the normal MOP pipeline.
    """
    raw=normalize_text(text_value or "")
    if not raw:
        return None
    low=raw.lower().replace("ё","е").strip()
    soft=re.sub(r"[^a-zа-я0-9]+"," ",low).strip()

    # Strong vendor solicitation is allowed to contain a question ("созвониться?").
    # Require both explicit Avito-service provenance and a lead-transfer/result offer
    # plus at least three independent signals, so normal customer questions stay safe.
    avito_vendor=("работаю с авито" in soft or "работаем с авито" in soft)
    lead_offer=("передать заявки" in soft or "передаем заявки" in soft or
                ("сотрудничаем" in soft and "за результат" in soft))
    signals=0
    if avito_vendor: signals+=1
    if "передать заявки" in soft or "передаем заявки" in soft: signals+=1
    if "сотрудничаем" in soft and "за результат" in soft: signals+=1
    if "больше клиентов принимать" in soft: signals+=1
    if "ищем кому" in soft and "заявк" in soft: signals+=1
    if "регулярный приход" in soft and "обращен" in soft: signals+=1
    if avito_vendor and lead_offer and signals>=3:
        return "vendor_solicitation"

    if "?" in low:
        return None
    gratitude_patterns=(
        r"(?:отлично |хорошо |понял |поняла |ясно |ок |окей )?(?:большое )?спасибо",
        r"(?:большое )?спасибо(?: вам)?",
        r"благодарю(?: вас)?",
    )
    if any(re.fullmatch(p,soft) for p in gratitude_patterns):
        return "gratitude_ack"
    simple_ack_patterns=(
        r"хорошо", r"ок", r"окей", r"понял", r"поняла", r"ясно",
        r"договорились", r"ладно", r"принято", r"отлично", r"супер",
    )
    if any(re.fullmatch(p,soft) for p in simple_ack_patterns):
        return "simple_ack"
    return None


def deterministic_handoff_reason(text_value):
    """High-confidence explicit request to continue with a human manager."""
    raw = normalize_text(text_value or "")
    if not raw:
        return None
    low = raw.lower().replace("ё", "е")
    phrases = (
        "позовите менеджера", "позовите человека", "нужен менеджер",
        "нужен человек", "хочу менеджера", "хочу человека",
        "соедините с менеджером", "соедините с человеком",
        "переключите на менеджера", "переключите на человека",
        "дайте менеджера", "дайте человека", "оператор", "живой человек",
        "живого человека", "с живым человеком",
        "хочу поговорить с менеджером", "хочу поговорить с человеком",
        "пусть менеджер", "пусть человек",
    )
    if any(p in low for p in phrases):
        return "client_requested_human"
    partnership_phrases = (
        "предлагаю сотрудничество", "предложение о сотрудничестве",
        "партнерская программа", "партнёрская программа",
        "интересует ли вас доп заработок", "интересует ли вас дополнительный заработок",
        "доп заработок по продуктам", "дополнительный заработок по продуктам",
        "агентское вознаграждение", "стать нашим партнером", "стать нашим партнёром",
    )
    if any(p in low for p in partnership_phrases):
        return "business_partnership_request"
    return None


def text_hash(s):
    return hashlib.sha256(normalize_text(s).encode("utf-8")).hexdigest()


def can_go(from_status, to_status):
    return to_status in TRANSITIONS.get(from_status, ())


# ------------------------------------------------------------------ история

def log_event(db, draft_id, event, from_status=None, to_status=None,
              channel=None, actor_type=None, actor_id=None, payload=None, meta=None):
    """Пишет строку в append-only журнал. Коммит делает вызывающий."""
    import json
    db.execute(text(
        "INSERT INTO mop_draft_events (draft_id, event, from_status, to_status, channel,"
        " actor_type, actor_id, payload, metadata)"
        " VALUES (:d, :e, :f, :t, :c, :at, :ai, :p, CAST(:m AS JSONB))"
    ), {"d": draft_id, "e": event, "f": from_status, "t": to_status, "c": channel,
        "at": actor_type, "ai": actor_id, "p": payload,
        "m": json.dumps(meta, ensure_ascii=False) if meta else None})


# ------------------------------------------------------------- смена статуса

def set_status(db, draft_id, to_status, allowed_from, event,
               channel=None, actor_type=None, actor_id=None,
               payload=None, meta=None, extra_sql="", extra_params=None):
    """Атомарный захват: условный UPDATE ... RETURNING.

    Кто получил строку — тот и работает. Кто получил None — опоздал,
    вызывающий показывает актуальный статус и НИЧЕГО не отправляет.
    Это единственный механизм захвата в системе.
    """
    if to_status not in STATUSES:
        raise MopError("неизвестный статус: %s" % to_status)
    for s in allowed_from:
        if not can_go(s, to_status):
            raise MopError("переход %s -> %s не разрешён" % (s, to_status))

    q = text(
        "UPDATE mop_drafts SET status = :to, updated_at = now()" + extra_sql +
        " WHERE id = :id AND status IN :froms"
        " RETURNING id, status, reply_text, account_id, avito_chat_id"
    ).bindparams(bindparam("froms", expanding=True))
    prev = db.execute(text("SELECT status FROM mop_drafts WHERE id = :id"),
                      {"id": draft_id}).scalar()
    params = {"to": to_status, "id": draft_id, "froms": list(allowed_from)}
    params.update(extra_params or {})
    row = db.execute(q, params).fetchone()
    if row is None:
        db.commit()
        return None, prev
    log_event(db, draft_id, event, from_status=prev, to_status=to_status,
              channel=channel, actor_type=actor_type, actor_id=actor_id,
              payload=payload, meta=meta)
    db.commit()
    return dict(row._mapping), to_status


# ------------------------------------------------------------------ карточка

def create_draft(db, account_id, avito_chat_id, avito_message_id, incoming_text,
                 item_id=None, item_title=None, client_name=None):
    """Одно входящее = одна карточка. Повторный/конкурентный вызов возвращает существующую.

    `uq_mop_drafts_incoming(account_id,avito_message_id)` is the database truth.
    Use it atomically instead of SELECT -> INSERT, otherwise two poll paths can
    both observe absence and one loses with UniqueViolation, delaying the MOP.
    """
    params = {"a": account_id, "c": avito_chat_id, "m": avito_message_id, "t": incoming_text,
              "i": item_id, "ti": item_title, "cn": client_name}
    new_id = db.execute(text(
        "INSERT INTO mop_drafts (account_id, avito_chat_id, avito_message_id,"
        " incoming_text, item_id, item_title, client_name, status)"
        " VALUES (:a, :c, :m, :t, :i, :ti, :cn, 'new')"
        " ON CONFLICT (account_id, avito_message_id) DO NOTHING RETURNING id"
    ), params).scalar()
    if new_id is None:
        existing_id = db.execute(text(
            "SELECT id FROM mop_drafts WHERE account_id=:a AND avito_message_id=:m"
        ), params).scalar()
        db.commit()
        if existing_id is None:
            raise MopError("incoming draft conflict resolved without visible row")
        return existing_id, False
    log_event(db, new_id, "draft_created", to_status="new", actor_type="system")
    db.commit()
    return new_id, True


def claim_for_send(db, draft_id, actor_type, actor_id, channel):
    """Захват под отправку. Второй нажавший получает (None, текущий_статус)."""
    return set_status(
        db, draft_id, "sending",
        allowed_from=("draft_ready", "in_progress", "waiting_confirm", "send_failed"),
        event="send_started", channel=channel,
        actor_type=actor_type, actor_id=actor_id,
        extra_sql=", locked_at = now(), locked_by = :lb",
        extra_params={"lb": str(actor_id)},
    )


def _initiator(actor_id, actor_type="telegram_user"):
    """Инициатор попытки живёт в metadata: событие результата — системное."""
    if not actor_id:
        return None
    return {"initiated_by_type": actor_type, "initiated_by_id": str(actor_id)}


def mark_sent(db, draft_id, outgoing_text, channel=None, actor_id=None):
    return set_status(
        db, draft_id, "sent", allowed_from=("sending",), event="sent",
        channel=channel, actor_type="system", meta=_initiator(actor_id),
        extra_sql=", sent_at = now(), outgoing_text_hash = :h, send_error = NULL, locked_at = NULL, locked_by = NULL",
        extra_params={"h": text_hash(outgoing_text)},
    )


def mark_send_failed(db, draft_id, reason, channel=None, actor_id=None):
    return set_status(
        db, draft_id, "send_failed", allowed_from=("sending",), event="send_failed",
        channel=channel, actor_type="system", meta=_initiator(actor_id),
        payload=str(reason)[:500],
        extra_sql=", send_error = :er, locked_at = NULL, locked_by = NULL",
        extra_params={"er": str(reason)[:500]},
    )


# --------------------------------------------------- карточки каналов и reply

def take_to_work(db, draft_id, tg_user_id, actor_name=None):
    """Взять карточку в работу. Захватывает только свободную."""
    lb = str(tg_user_id)
    row = db.execute(text(
        "UPDATE mop_drafts SET status = 'in_progress', locked_by = :lb,"
        " locked_at = now(), updated_at = now()"
        " WHERE id = :i AND status = 'draft_ready' AND locked_by IS NULL"
        " RETURNING id, status, locked_by, account_id, avito_chat_id"
    ), {"i": draft_id, "lb": lb}).fetchone()
    if row is not None:
        log_event(db, draft_id, "taken", from_status="draft_ready",
                  to_status="in_progress", channel="telegram",
                  actor_type="telegram_user", actor_id=lb,
                  meta={"actor_name": actor_name} if actor_name else None)
        db.commit()
        return "taken", dict(row._mapping)
    cur = db.execute(text(
        "SELECT id, status, locked_by FROM mop_drafts WHERE id = :i"
    ), {"i": draft_id}).fetchone()
    db.commit()
    if cur is None:
        return "bad_status", None
    d = dict(cur._mapping)
    if str(d.get("locked_by") or "") == lb:
        return "already_mine", d
    if d.get("locked_by"):
        return "busy", d
    return "bad_status", d


def upsert_card(db, draft_id, channel, chat_id="", thread_id=0,
                external_message_id=None, rendered_status=None, render_hash=None):
    db.execute(text(
        "INSERT INTO mop_draft_cards (draft_id, channel, chat_id, thread_id,"
        " external_message_id, rendered_status, last_render_hash, updated_at)"
        " VALUES (:d, :ch, :c, :t, :e, :rs, :rh, now())"
        " ON CONFLICT (draft_id, channel, chat_id, thread_id) DO UPDATE SET"
        " external_message_id = COALESCE(EXCLUDED.external_message_id,"
        " mop_draft_cards.external_message_id),"
        " rendered_status = EXCLUDED.rendered_status,"
        " last_render_hash = EXCLUDED.last_render_hash, updated_at = now()"
    ), {"d": draft_id, "ch": channel, "c": chat_id or "", "t": thread_id or 0,
        "e": external_message_id, "rs": rendered_status, "rh": render_hash})
    db.commit()


def draft_by_reply(db, channel, chat_id, external_message_id):
    """Reply валиден ТОЛЬКО если сообщение реально найдено среди карточек.

    Служебное сообщение Telegram о создании темы сюда не попадёт и карточкой
    считаться не будет — вызывающий уходит на запасной путь через mop_awaiting.
    """
    if not external_message_id:
        return None
    return db.execute(text(
        "SELECT draft_id FROM mop_draft_cards WHERE channel = :ch AND chat_id = :c"
        " AND external_message_id = :e"
    ), {"ch": channel, "c": str(chat_id), "e": str(external_message_id)}).scalar()


def needs_render(db, draft_id, channel, chat_id, thread_id, render_hash):
    """False, если карточка визуально не изменилась — не дёргаем editMessage."""
    cur = db.execute(text(
        "SELECT last_render_hash FROM mop_draft_cards WHERE draft_id = :d"
        " AND channel = :ch AND chat_id = :c AND thread_id = :t"
    ), {"d": draft_id, "ch": channel, "c": chat_id or "", "t": thread_id or 0}).scalar()
    return cur != render_hash


def cards_of(db, draft_id):
    """Все места публикации карточки — для фан-аута по каналам."""
    return [dict(r._mapping) for r in db.execute(text(
        "SELECT channel, chat_id, thread_id, external_message_id, rendered_status"
        " FROM mop_draft_cards WHERE draft_id = :d ORDER BY id"
    ), {"d": draft_id}).all()]


def route_for(db, account_id, thread_key="clients"):
    """Маршрут темы по account_id. Город из текста не выводится никогда."""
    row = db.execute(text(
        "SELECT chat_id, thread_id FROM tg_routes WHERE account_id = :a AND thread_key = :k"
    ), {"a": account_id, "k": thread_key}).fetchone()
    return dict(row._mapping) if row else None


# ================================================================= TG-СЛОЙ
# Рендер карточки, восемь кнопок, приём текста через reply и запасной FSM.
# Импорты telegram_bot и messenger — ЛЕНИВЫЕ, чтобы не было кольца импортов
# и чтобы этот модуль оставался пригодным для автономных прогонов.

import datetime as _dt

AWAIT_TTL_MIN = 15

STATUS_LABEL = {
    "new": "🟡 Новое сообщение",
    "analyzing": "⏳ Готовлю ответ",
    "draft_ready": "🟡 Ожидает решения менеджера",
    "editing": "✏️ Жду текст правки",
    "custom_waiting": "📝 Жду ваш вариант ответа",
    "waiting_confirm": "🟠 Готово к отправке, подтвердите",
    "sending": "📤 Отправляю",
    "sent": "✅ Ответ отправлен клиенту",
    "send_failed": "🔴 Avito не принял ответ",
    "deleted": "🗑 Черновик удалён, клиент без ответа",
    "human_required": "👤 Передано менеджеру",
    "no_reply_required": "❌ Закрыто без ответа",
}

REGEN_REASONS = {
    "1": "короче", "2": "подробнее", "3": "более продающий",
    "4": "задать уточняющий вопрос", "5": "без скидки", "6": "мягче",
    "7": "деловой стиль", "8": "только по базе знаний",
}

NS = "mp"


def conversation_key(channel, chat_id, thread_id):
    """Детерминированный ключ разговора, одинаковый для всех мессенджеров."""
    return "%s:%s:%s" % (channel, chat_id, thread_id or 0)


# ------------------------------------------------------------- ошибки Avito

ROUTE_MISSING = "route_missing"


def _err_message(result, exc=None):
    """Нормализованная строка ошибки без обёртки словарём."""
    if isinstance(result, dict):
        msg = result.get("message") or result.get("error") or ""
    else:
        msg = str(result or "")
    if not msg and exc:
        msg = str(exc)
    return normalize_text(msg)[:160] or "без описания"


def classify_send_error(result, exc=None):
    """Человеческая причина вместо «не удалось». Понимает и русский ответ Avito."""
    blob = ("%s %s" % (result, exc)).lower()
    if "401" in blob or "unauthorized" in blob or "авториз" in blob:
        return "Avito 401 — ключи аккаунта не приняты"
    if "429" in blob or "too many" in blob or "лимит" in blob or "превышен" in blob:
        return "Avito 429 — превышен лимит запросов"
    if "403" in blob or "forbidden" in blob or "доступ" in blob or "запрещ" in blob:
        return "Avito 403 — нет доступа к этому чату"
    if "timeout" in blob or "timed out" in blob or "время ожидания" in blob:
        return "timeout — Avito не ответил вовремя"
    if "connection" in blob or "network" in blob or "dns" in blob or "соединен" in blob:
        return "network — не достучались до Avito"
    return "другое: %s" % _err_message(result, exc)


# ------------------------------------------------------------------ рендер

def _has_event(db, draft_id, event):
    """Было ли событие у карточки. Причина берётся из журнала, не из пустоты полей."""
    return bool(db.execute(text(
        "SELECT 1 FROM mop_draft_events WHERE draft_id = :i AND event = :e LIMIT 1"
    ), {"i": draft_id, "e": event}).scalar())


def _draft(db, draft_id):
    row = db.execute(text("SELECT * FROM mop_drafts WHERE id = :i"), {"i": draft_id}).fetchone()
    if not row:
        return None
    d = dict(row._mapping)
    # ссылка на объявление: приоритет — тот же item_id, иначе свежайшая непустая
    d["item_url"] = db.execute(text(
        "SELECT item_url FROM messenger_messages"
        " WHERE account_id = :a AND avito_chat_id = :c"
        "   AND item_url IS NOT NULL AND item_url <> ''"
        " ORDER BY (item_id IS NOT DISTINCT FROM :it) DESC, id DESC LIMIT 1"
    ), {"a": d["account_id"], "c": d["avito_chat_id"],
        "it": d.get("item_id")}).scalar()
    return d


def _last_meta(db, draft_id):
    """model/tokens/cost/fact_ids последней генерации. Нет данных — пустой словарь."""
    import json
    row = db.execute(text(
        "SELECT metadata FROM mop_draft_events WHERE draft_id = :i"
        " AND event IN ('ai_generated', 'regenerated') AND metadata IS NOT NULL"
        " ORDER BY id DESC LIMIT 1"), {"i": draft_id}).scalar()
    if not row:
        return {}
    return row if isinstance(row, dict) else json.loads(row)


def card_text(db, draft, city=None, account_name=None):
    """Карточка. Строки про факты и стоимость печатаются ТОЛЬКО при данных."""
    d = draft
    head = "🟦 <b>%s · НОВОЕ СООБЩЕНИЕ</b>" % (
        city or account_name or d["account_id"]).upper()
    lines = [head, ""]
    lines.append("Аккаунт: %s" % (account_name or d["account_id"]))
    lines.append("ID: %s" % d["account_id"])
    if d.get("client_name"):
        lines.append("Клиент: %s" % d["client_name"])
    if d.get("item_title"):
        lines.append("Объявление: %s" % d["item_title"])
    when = d.get("created_at")
    chat_url = "https://www.avito.ru/profile/messenger/channel/%s" % d["avito_chat_id"]
    _links = ["<a href=\"%s\">Диалог в Avito</a>" % chat_url]
    if d.get("item_url"):
        _links.insert(0, "<a href=\"%s\">Объявление</a>" % d["item_url"])
    lines.append("Время: %s · %s" % (
        when.strftime("%H:%M") if when else "—", " · ".join(_links)))
    lines.append("")
    lines.append("<b>Клиент написал:</b>")
    lines.append("«%s»" % (d.get("incoming_text") or "").strip())
    if d.get("ai_summary"):
        lines.append("")
        lines.append("<b>Что понял AI-МОП:</b>")
        lines.append(d["ai_summary"])
    if d.get("reply_text"):
        lines.append("")
        who = "AI-МОП хочет ответить" if d.get("reply_author", "").startswith("ai") \
            else "Ваш вариант ответа"
        lines.append("<b>%s:</b>" % who)
        lines.append("«%s»" % d["reply_text"].strip())
    elif _has_event(db, d["id"], "card_no_ai"):
        lines.append("")
        lines.append("<i>AI-черновик недоступен — пакет МОП не подключён</i>")
    if d.get("status") == "send_failed" and d.get("send_error"):
        lines.append("")
        lines.append("<b>Причина:</b> %s" % d["send_error"])
    meta = _last_meta(db, d["id"])
    if meta.get("model") == "memory":
        lines.append("")
        lines.append("Источник ответа: подтверждённая база знаний"
                     if meta.get("fact_ids") else "Подтверждённых данных недостаточно")
    if meta.get("fact_ids"):
        lines.append("Использовано фактов: %d" % len(meta["fact_ids"]))
    if meta.get("cost_rub") is not None:
        lines.append("Стоимость генерации: %.2f ₽" % float(meta["cost_rub"]))
    if d.get("lead_id"):
        lines.append("📌 Лид создан: #%s" % d["lead_id"])
    lines.append("")
    lines.append("Статус: %s · #%s" % (STATUS_LABEL.get(d["status"], d["status"]), d["id"]))
    return "\n".join(lines)


def card_buttons(draft):
    """Три ряда. Набор зависит от статуса: мёртвые действия не показываем."""
    i = draft["id"]
    st = draft["status"]
    has_reply = bool((draft.get("reply_text") or "").strip())
    b = lambda t, c: {"text": t, "callback_data": "%s:%s:%s" % (NS, c, i)}
    if st in ("sent", "no_reply_required", "sending"):
        return []
    if st == "human_required":
        return [[b("↩️ Вернуть в работу", "b")]]
    if st == "draft_ready":
        return [[b("🙋 Взять в работу", "t")],
                [b("📌 Создать лид", "l"), b("❌ Закрыть", "x")]]
    if st == "in_progress":
        if has_reply:
            return [[b("✅ Отправить", "a"), b("✏️ Редактировать", "e"),
                     b("📝 Свой ответ", "c")],
                    [b("🔄 Другой ответ", "r"), b("👤 Менеджеру", "h"),
                     b("❌ Закрыть", "x")]]
        return [[b("📝 Свой ответ", "c")],
                [b("👤 Менеджеру", "h"), b("❌ Закрыть", "x")]]
    if st == "deleted":
        return [[b("🔄 Новый AI-ответ", "r"), b("📝 Написать свой", "c")],
                [b("👤 Менеджеру", "h")]]
    if st in ("editing", "custom_waiting"):
        return [[b("🗑 Удалить черновик", "d"), b("👤 Менеджеру", "h")],
                [b("❌ Закрыть без ответа", "x")]]
    rows = [[b("✅ Отправить", "a"), b("✏️ Редактировать", "e"), b("📝 Свой ответ", "c")],
            [b("🔄 Другой ответ", "r"), b("🗑 Удалить", "d"), b("👤 Менеджеру", "h")],
            [b("📌 Создать лид", "l"), b("❌ Закрыть", "x")]]
    return rows


def regen_buttons(draft_id):
    rows, cur = [], []
    for code, label in sorted(REGEN_REASONS.items()):
        cur.append({"text": label, "callback_data": "%s:r:%s:%s" % (NS, draft_id, code)})
        if len(cur) == 2:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    rows.append([{"text": "← Назад", "callback_data": "%s:n:%s" % (NS, draft_id)}])
    return rows


def render_hash(body, buttons):
    return text_hash(body + "|" + str(buttons))


def target_for(db, account_id, thread_key="clients"):
    """Куда слать карточку. Нет маршрута — НЕ угадываем и не шлём в чужую тему."""
    r = route_for(db, account_id, thread_key)
    if not r:
        return None
    return str(r["chat_id"]), int(r["thread_id"] or 0)


def account_title(db, account_id):
    """Имя аккаунта из accounts. Города в tg_routes пока нет — не выдумываем."""
    name = db.execute(text("SELECT name FROM accounts WHERE account_id = :a"),
                      {"a": account_id}).scalar()
    return name or account_id, None


def push_card(db, draft_id, chat_id=None, thread_id=0, city=None, account_name=None,
              buttons=None):
    """Отправляет или перерисовывает карточку. editMessageText — только по хэшу.

    chat_id не задан -> берём из tg_routes. Маршрута нет -> route_missing,
    ничего не отправляем: карточка не должна попасть в чужую тему.
    """
    from app.telegram_bot import _telegram_post, API_BASE
    d = _draft(db, draft_id)
    if not d:
        return None
    if chat_id is None:
        tgt = target_for(db, d["account_id"])
        if not tgt:
            log_event(db, draft_id, "card_posted", channel="telegram",
                      actor_type="system", payload=ROUTE_MISSING)
            db.commit()
            return ROUTE_MISSING
        chat_id, thread_id = tgt
    if account_name is None or city is None:
        auto_name, auto_city = account_title(db, d["account_id"])
        account_name = account_name or auto_name
        city = city or auto_city
    body = card_text(db, d, city=city, account_name=account_name)
    btns = card_buttons(d) if buttons is None else buttons
    h = render_hash(body, btns)
    row = db.execute(text(
        "SELECT external_message_id, last_render_hash FROM mop_draft_cards"
        " WHERE draft_id = :d AND channel = 'telegram' AND chat_id = :c AND thread_id = :t"
    ), {"d": draft_id, "c": str(chat_id), "t": thread_id or 0}).fetchone()

    if row and row[0]:
        if row[1] == h:
            return row[0]
        _telegram_post(API_BASE + "/editMessageText", {
            "chat_id": chat_id, "message_id": int(row[0]), "text": body,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": _kb(btns)},
        })
        mid = row[0]
    else:
        payload = {"chat_id": str(chat_id), "text": body, "parse_mode": "HTML",
                   "disable_web_page_preview": True,
                   "reply_markup": {"inline_keyboard": _kb(btns)}}
        if thread_id:
            payload["message_thread_id"] = int(thread_id)
        res = _telegram_post(API_BASE + "/sendMessage", payload).json()
        mid = str(((res or {}).get("result") or {}).get("message_id") or "")
        if not mid:
            return None
        log_event(db, draft_id, "card_posted", channel="telegram", actor_type="system")
    upsert_card(db, draft_id, "telegram", str(chat_id), thread_id or 0,
                external_message_id=mid, rendered_status=d["status"], render_hash=h)
    return mid


def _kb(buttons):
    from app.telegram_bot import _normalize_keyboard
    return _normalize_keyboard(buttons)


# -------------------------------------------------------------- ожидание текста

def set_awaiting(db, channel, user_key, chat_id, thread_id, draft_id, action):
    """Одна активная операция ввода на человека в теме. Вторая — честный отказ."""
    ck = conversation_key(channel, chat_id, thread_id)
    db.execute(text("DELETE FROM mop_awaiting WHERE expires_at < now()"))
    cur = db.execute(text(
        "SELECT draft_id FROM mop_awaiting WHERE channel = :ch AND user_key = :u"
        " AND conversation_key = :ck"), {"ch": channel, "u": str(user_key), "ck": ck}).scalar()
    if cur and int(cur) != int(draft_id):
        db.commit()
        return False, int(cur)
    exp = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=AWAIT_TTL_MIN)
    db.execute(text(
        "INSERT INTO mop_awaiting (channel, user_key, conversation_key, thread_key,"
        " draft_id, action, expires_at) VALUES (:ch, :u, :ck, :tk, :d, :a, :e)"
        " ON CONFLICT (channel, user_key, conversation_key) DO UPDATE SET"
        " draft_id = EXCLUDED.draft_id, action = EXCLUDED.action,"
        " expires_at = EXCLUDED.expires_at"
    ), {"ch": channel, "u": str(user_key), "ck": ck, "tk": str(thread_id or ""),
        "d": draft_id, "a": action, "e": exp})
    db.commit()
    return True, draft_id


def pop_awaiting(db, channel, user_key, chat_id, thread_id):
    ck = conversation_key(channel, chat_id, thread_id)
    row = db.execute(text(
        "SELECT id, draft_id, action, expires_at FROM mop_awaiting"
        " WHERE channel = :ch AND user_key = :u AND conversation_key = :ck"
    ), {"ch": channel, "u": str(user_key), "ck": ck}).fetchone()
    if not row:
        return None
    rid, did, action, exp = row[0], row[1], row[2], row[3]
    db.execute(text("DELETE FROM mop_awaiting WHERE id = :i"), {"i": rid})
    if exp and exp < _dt.datetime.now(_dt.timezone.utc):
        log_event(db, did, "awaiting_expired", channel=channel, actor_type="system")
        set_status(db, did, "draft_ready", ("editing", "custom_waiting"),
                   "awaiting_expired", channel=channel, actor_type="system")
        return None
    db.commit()
    return {"draft_id": did, "action": action}


def apply_manual_text(db, draft_id, body, channel, user_key):
    """Текст человека НИКОГДА не уходит в Avito сразу — только в waiting_confirm.

    Из in_progress текст принимается только от того, кто взял карточку:
    иначе двое ответят одновременно и клиент получит два разных ответа.
    """
    cur = db.execute(text(
        "SELECT status, COALESCE(locked_by, '') FROM mop_drafts WHERE id = :i"
    ), {"i": draft_id}).fetchone()
    if cur is not None and cur[0] == "in_progress":
        if cur[1] and str(cur[1]) != str(user_key):
            return None, "занята сотрудником %s" % cur[1]
    db.execute(text(
        "UPDATE mop_drafts SET reply_text = :t, reply_author = 'manager', updated_at = now()"
        " WHERE id = :i"), {"t": body, "i": draft_id})
    db.commit()
    return set_status(db, draft_id, "waiting_confirm",
                      ("editing", "custom_waiting", "in_progress"),
                      "edited", channel=channel, actor_type="telegram_user",
                      actor_id=str(user_key), payload=body[:500])


# ------------------------------------------------------------- точки расширения
# Подключаются на этапе интеграции. Пока не заданы — кнопка честно говорит,
# что действие ещё не подключено, вместо тихого ничегонеделания.
HOOKS = {"generate": None, "create_lead": None, "route": None}


ASK_TEXT = ("Ответьте на эту карточку новым текстом.\n"
            "Для отмены отправьте /cancel.")


def _answer(cb_id, note=None, alert=False):
    from app.telegram_bot import _telegram_post, API_BASE
    body = {"callback_query_id": cb_id}
    if note:
        body["text"] = note[:190]
    if alert:
        body["show_alert"] = True
    try:
        _telegram_post(API_BASE + "/answerCallbackQuery", body)
    except Exception:
        pass



def retry_send_readback_decision(draft_message_id: str, reply_text: str, provider_result: dict) -> str:
    """Decide whether a failed-send retry may safely POST again.

    MOP_SEND_RETRY_READBACK_DEDUPE_V1:
    - already_sent: the exact reply is already visible after the original inbound;
    - answered_externally: some other outbound already answered that inbound;
    - superseded: a newer inbound arrived, so the old draft is stale;
    - safe_to_send: provider proves the original inbound is still the latest meaningful edge;
    - unknown: provider evidence is incomplete/unavailable -> fail closed, never blind retry.
    """
    if not isinstance(provider_result, dict) or provider_result.get("status") != "ok":
        return "unknown"
    raw = provider_result.get("messages")
    messages = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    meaningful = [
        m for m in messages
        if isinstance(m, dict) and str(m.get("type") or "").lower() != "system"
    ]
    original = next(
        (m for m in meaningful if str(m.get("id") or "") == str(draft_message_id or "")),
        None,
    )
    if not original:
        return "unknown"

    try:
        original_created = int(original.get("created") or 0)
    except Exception:
        original_created = 0
    wanted = normalize_text(reply_text or "")

    later = []
    for m in meaningful:
        if str(m.get("id") or "") == str(draft_message_id or ""):
            continue
        try:
            created = int(m.get("created") or 0)
        except Exception:
            created = 0
        if original_created and created < original_created:
            continue
        later.append(m)

    for m in later:
        direction = str(m.get("direction") or "").lower()
        if not direction.startswith("out"):
            continue
        content = m.get("content")
        if isinstance(content, dict):
            body = content.get("text")
        else:
            body = content
        if wanted and normalize_text(str(body or "")) == wanted:
            return "already_sent"

    if any(str(m.get("direction") or "").lower().startswith("out") for m in later):
        return "answered_externally"
    if any(str(m.get("direction") or "").lower().startswith("in") for m in later):
        return "superseded"

    if str(original.get("direction") or "").lower().startswith("in"):
        return "safe_to_send"
    return "unknown"


def do_send(db, draft_id, actor_id, channel="telegram"):
    """Единственный путь отправки. Захват атомарный, второй нажавший опоздал.

    ACCOUNT_MASTER_OFF_MOP_SEND_V1: tenant-wide reliability switch is checked
    before the draft is claimed. This makes every MOP sender path (autopilot,
    recovery and manual MOP card) fail closed for suspended/unpaid accounts.
    """
    current = _draft(db, draft_id)
    if not current:
        return False, "карточка не найдена"
    try:
        from app.services.reliability import module_blocked
        blocked, blocked_reason = module_blocked(
            db, "mop", str(current.get("account_id") or "")
        )
    except Exception as exc:
        return False, "не удалось проверить доступ аккаунта: %s" % type(exc).__name__
    if blocked:
        return False, "account_disabled: %s" % (
            blocked_reason or "действия BORIS для аккаунта отключены"
        )

    pre_status = str(current.get("status") or "")
    incoming_message_id = str(current.get("avito_message_id") or "")

    row, st = claim_for_send(db, draft_id, "telegram_user", actor_id, channel)
    if row is None:
        return False, "уже в работе или отправлено (%s)" % st
    body = row.get("reply_text") or ""
    if not body.strip():
        mark_send_failed(db, draft_id, "пустой текст ответа", channel=channel, actor_id=actor_id)
        return False, "текст ответа пуст"

    # Lost-response protection. If Avito accepted the previous POST but BORIS
    # saw a timeout/network error, the draft is send_failed even though the
    # customer already received the text. Never POST the retry until Avito
    # readback proves that the original inbound is still unanswered.
    if pre_status == "send_failed":
        try:
            from app.api.messenger import fetch_chat_messages as _retry_fetch
            provider = _retry_fetch(row["account_id"], row["avito_chat_id"])
            retry_decision = retry_send_readback_decision(
                incoming_message_id, body, provider
            )
        except Exception:
            retry_decision = "unknown"

        if retry_decision == "already_sent":
            mark_sent(db, draft_id, body, channel=channel, actor_id=actor_id)
            try:
                log_event(
                    db, draft_id, "send_recovered",
                    from_status="sent", to_status="sent",
                    channel=channel, actor_type="system", actor_id="avito_readback",
                    payload="Точный ответ уже найден в Avito; повторный POST не выполнялся.",
                    meta={"policy_version": "MOP_SEND_RETRY_READBACK_DEDUPE_V1"},
                )
                db.commit()
            except Exception:
                db.rollback()
            return True, "доставка подтверждена по истории Avito; повтор не отправлен"

        if retry_decision in {"answered_externally", "superseded"}:
            mark_send_failed(
                db, draft_id, "retry suppressed: %s" % retry_decision,
                channel=channel, actor_id=actor_id,
            )
            set_status(
                db, draft_id, "no_reply_required", ("send_failed",),
                "answered_externally" if retry_decision == "answered_externally" else "closed_no_reply",
                channel=channel, actor_type="system", actor_id="avito_readback",
                payload="Повторный POST не выполнялся: %s" % retry_decision,
                meta={"policy_version": "MOP_SEND_RETRY_READBACK_DEDUPE_V1"},
            )
            return False, "повтор не нужен: диалог в Avito уже изменился"

        if retry_decision != "safe_to_send":
            mark_send_failed(
                db, draft_id,
                "retry readback inconclusive; blind resend blocked",
                channel=channel, actor_id=actor_id,
            )
            return False, "повтор отложен: Avito не подтвердил отсутствие доставки"

    try:
        from app.api.messenger import send_message as avito_send
        res = avito_send(row["account_id"], row["avito_chat_id"], body)
        ok = (res or {}).get("status") == "ok"
    except Exception as e:
        res, ok = {"status": "error", "message": str(e)}, False
    if ok:
        mark_sent(db, draft_id, body, channel=channel, actor_id=actor_id)
        return True, "отправлено"
    reason = classify_send_error(res)
    mark_send_failed(db, draft_id, reason, channel=channel, actor_id=actor_id)
    return False, reason


def handle_mop_callback(data, callback_query):
    """Ветка mp:*. Возвращает True, если нажатие обработано здесь."""
    from app.db.session import SessionLocal
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != NS:
        return False
    code, raw_id = parts[1], parts[2]
    reason = parts[3] if len(parts) > 3 else None
    cb_id = callback_query.get("id")
    msg = callback_query.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    thread_id = msg.get("message_thread_id") or 0
    user = (callback_query.get("from") or {}).get("id")
    try:
        draft_id = int(raw_id)
    except ValueError:
        _answer(cb_id, "битый идентификатор карточки")
        return True

    db = SessionLocal()
    try:
        d = _draft(db, draft_id)
        if not d:
            _answer(cb_id, "карточка не найдена")
            return True
        note = None
        buttons = None
        alert = False

        if code == "a":
            ok, note = do_send(db, draft_id, user)
        elif code == "e":
            r, st = set_status(db, draft_id, "editing",
                               ("draft_ready", "in_progress", "waiting_confirm",
                                "send_failed"),
                               "edit_requested", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            if r is None:
                note = "сейчас статус %s" % st
            else:
                free, busy_id = set_awaiting(db, "telegram", user, chat_id, thread_id,
                                             draft_id, "edit_ai_reply")
                note = ("Сейчас вы правите карточку #%s. Завершите её или /cancel" % busy_id
                        if not free else ASK_TEXT)
                alert = True
        elif code == "c":
            r, st = set_status(db, draft_id, "custom_waiting",
                               ("draft_ready", "in_progress", "waiting_confirm",
                                "send_failed"),
                               "custom_requested", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            if r is None:
                note = "сейчас статус %s" % st
            else:
                free, busy_id = set_awaiting(db, "telegram", user, chat_id, thread_id,
                                             draft_id, "write_custom_reply")
                note = ("Сейчас вы правите карточку #%s. Завершите её или /cancel" % busy_id
                        if not free else ASK_TEXT)
                alert = True
        elif code == "r" and reason is None:
            buttons = regen_buttons(draft_id)
            note = "Выберите, каким сделать ответ"
        elif code == "r":
            if not HOOKS["generate"]:
                note = "генератор ответов подключим на этапе интеграции"
            else:
                ok_gen, note = HOOKS["generate"](db, draft_id, reason, str(user))
                if not ok_gen:
                    note = note or "не удалось подготовить другой вариант"
        elif code == "n":
            note = "назад"
        elif code == "t":
            _res, _dd = take_to_work(db, draft_id, user,
                                     actor_name=(callback_query.get("from") or {}).get("first_name"))
            if _res == "taken":
                note = "взяли в работу"
            elif _res == "already_mine":
                note = "карточка уже у вас"
            elif _res == "busy":
                note = "уже взял: %s" % (_dd or {}).get("locked_by")
            else:
                note = "нельзя взять в статусе %s" % (_dd or {}).get("status")
        elif code == "d":
            r, st = set_status(db, draft_id, "deleted",
                               ("draft_ready", "in_progress", "editing",
                                "custom_waiting", "waiting_confirm"),
                               "draft_deleted", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            note = "черновик удалён" if r else "сейчас статус %s" % st
        elif code == "h":
            r, st = set_status(db, draft_id, "human_required",
                               ("draft_ready", "in_progress", "editing", "custom_waiting",
                                "waiting_confirm", "deleted", "send_failed", "new"),
                               "handed_to_human", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            note = "передано менеджеру" if r else "сейчас статус %s" % st
        elif code == "b":
            r, st = set_status(db, draft_id, "draft_ready", ("human_required",),
                               "returned_to_ai", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            note = "вернули в работу" if r else "сейчас статус %s" % st
        elif code == "l":
            if d.get("lead_id"):
                note = "лид уже создан: #%s" % d["lead_id"]
            elif not HOOKS["create_lead"]:
                note = "создание лида подключим на этапе интеграции"
            else:
                lead_id = HOOKS["create_lead"](db, d)
                db.execute(text("UPDATE mop_drafts SET lead_id = :l WHERE id = :i"
                                " AND lead_id IS NULL"), {"l": lead_id, "i": draft_id})
                log_event(db, draft_id, "lead_created", channel="telegram",
                          actor_type="telegram_user", actor_id=str(user))
                db.commit()
                note = "лид создан"
        elif code == "x":
            r, st = set_status(db, draft_id, "no_reply_required",
                               ("draft_ready", "in_progress", "new", "human_required"),
                               "closed_no_reply", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            note = "закрыто без ответа" if r else "сейчас статус %s" % st
        else:
            note = "неизвестное действие"

        _answer(cb_id, note, alert=alert)
        push_card(db, draft_id, chat_id, thread_id, buttons=buttons)
        return True
    finally:
        db.close()


def handle_mop_message(message):
    """Свободный текст. True — приняли как ввод для карточки, дальше не идём."""
    from app.db.session import SessionLocal
    body = (message.get("text") or "").strip()
    if not body:
        return False
    chat_id = (message.get("chat") or {}).get("id")
    thread_id = message.get("message_thread_id") or 0
    user = (message.get("from") or {}).get("id")
    reply_to = (message.get("reply_to_message") or {}).get("message_id")

    db = SessionLocal()
    try:
        if body.lower() in ("/отмена", "/cancel", "отмена"):
            st = pop_awaiting(db, "telegram", user, chat_id, thread_id)
            if not st:
                return False
            log_event(db, st["draft_id"], "awaiting_cancelled", channel="telegram",
                      actor_type="telegram_user", actor_id=str(user))
            set_status(db, st["draft_id"], "draft_ready", ("editing", "custom_waiting"),
                       "awaiting_cancelled", channel="telegram",
                       actor_type="telegram_user", actor_id=str(user))
            push_card(db, st["draft_id"], chat_id, thread_id)
            return True

        draft_id = draft_by_reply(db, "telegram", chat_id, reply_to) if reply_to else None
        if draft_id is None:
            st = pop_awaiting(db, "telegram", user, chat_id, thread_id)
            draft_id = st["draft_id"] if st else None
        else:
            pop_awaiting(db, "telegram", user, chat_id, thread_id)
        if draft_id is None:
            return False

        r, sts = apply_manual_text(db, draft_id, body, "telegram", user)
        if r is None:
            from app.telegram_bot import send_telegram_message
            send_telegram_message(chat_id, "Карточка #%s уже в статусе %s — текст не применён."
                                  % (draft_id, sts), thread_id=thread_id or None)
            return True
        push_card(db, draft_id, chat_id, thread_id)
        return True
    finally:
        db.close()


# ---------------------------------------------------------- переключатель контура
# Флаг на аккаунт: у кого 'new' — работает новый контур карточек, у остальных
# старая цепочка. По умолчанию 'legacy': нет записи — старое поведение.
# Гейт врезается в поллер отдельным шагом, здесь только чтение и запись флага.

CONTOURS = ("legacy", "new")


def contour_of(db, account_id):
    """Какой контур у аккаунта. Нет записи -> legacy (безопасное умолчание)."""
    v = db.execute(text("SELECT contour FROM mop_modes WHERE account_id = :a"),
                   {"a": account_id}).scalar()
    return v if v in CONTOURS else "legacy"


def set_contour(db, account_id, contour):
    """Явное переключение. Чужое значение -> MopError, молча не глотаем."""
    if contour not in CONTOURS:
        raise MopError("недопустимый контур: %r (можно %s)" % (contour, ", ".join(CONTOURS)))
    db.execute(text(
        "INSERT INTO mop_modes (account_id, contour, updated_at)"
        " VALUES (:a, :c, now())"
        " ON CONFLICT (account_id) DO UPDATE SET"
        " contour = EXCLUDED.contour, updated_at = now()"
    ), {"a": account_id, "c": contour})
    db.commit()
    return contour


def recover_stale_analyzing(db, stale_minutes=30, limit=100):
    """Закрывает зависшие analyzing без внешних действий и без AI-вызовов.

    MOP_STALE_ANALYZING_RETRYABLE_V1: если после входящего уже есть outbound,
    фиксируем sent. Иначе НЕ маскируем внутренний сбой как human_required:
    первые две зависшие попытки возвращаем в ``new``. Обычный poller увидит
    тот же exact-once draft и повторит генерацию с тем же idempotency key.
    Только после двух уже зафиксированных generation_failed/stale-retry событий
    эскалируем человеку. Это bounded self-heal, без AI/API вызова внутри recovery.
    """
    rows = db.execute(text(
        "SELECT d.id, d.account_id, d.avito_chat_id, d.avito_message_id,"
        "       (SELECT m.avito_created_at FROM messenger_messages m"
        "         WHERE m.account_id=d.account_id"
        "           AND m.avito_message_id=d.avito_message_id LIMIT 1) AS in_ts"
        "  FROM mop_drafts d"
        " WHERE d.status='analyzing'"
        "   AND COALESCE(d.updated_at,d.created_at)"
        "       < now() - (:mins * interval '1 minute')"
        " ORDER BY COALESCE(d.updated_at,d.created_at), d.id"
        " LIMIT :lim"
    ), {"mins": int(stale_minutes), "lim": int(limit)}).fetchall()
    out = {"checked": len(rows), "answered_externally": 0, "retryable": 0, "human_required": 0}
    for row in rows:
        d = dict(row._mapping)
        has_out = False
        if d.get("in_ts") is not None:
            has_out = bool(db.execute(text(
                "SELECT 1 FROM messenger_messages"
                " WHERE account_id=:a AND avito_chat_id=:c"
                "   AND LOWER(direction) LIKE 'out%'"
                "   AND COALESCE(msg_type,'') <> 'system'"
                "   AND avito_created_at > :ts LIMIT 1"
            ), {"a": d["account_id"], "c": d["avito_chat_id"],
                "ts": d["in_ts"]}).first())
        if has_out:
            got, _prev = set_status(
                db, d["id"], "sent", ("analyzing",), "answered_externally",
                channel="avito", actor_type="human", actor_id="avito",
                payload="stale analyzing reconciled from local outbound",
                extra_sql=", sent_at=now(), reply_author='manager',"
                          " locked_at=NULL, locked_by=NULL")
            if got is not None:
                out["answered_externally"] += 1
        else:
            # MOP_STALE_RETRY_BUDGET_SEPARATION_V2: a transient provider
            # generation failure and a stale-recovery attempt are different
            # signals. The old combined count could escalate after one provider
            # defer + one stale pass, without ever giving the poller its intended
            # second recovery opportunity. Count actual stale retries here; the
            # generation path keeps its own bounded failure budget.
            stale_retries = int(db.execute(text(
                "SELECT count(*) FROM mop_draft_events WHERE draft_id=:i "
                "AND event='stale_analysis_retry'"
            ), {"i": d["id"]}).scalar() or 0)
            if stale_retries < 2:
                got, _prev = set_status(
                    db, d["id"], "new", ("analyzing",), "stale_analysis_retry",
                    channel="web", actor_type="system",
                    payload="bounded stale analyzing retry after %s minutes" % int(stale_minutes))
                if got is not None:
                    out["retryable"] += 1
            else:
                got, _prev = set_status(
                    db, d["id"], "human_required", ("analyzing",), "handed_to_human",
                    channel="web", actor_type="system",
                    payload="stale analyzing exhausted bounded retries")
                if got is not None:
                    out["human_required"] += 1
    return out


# --------------------------------------------------------------- генерация
# Коды кнопок -> ключи белого списка в messenger.py. Восьмая причина в
# _STYLE_HINTS отсутствует намеренно: она идёт мимо модели, через память.

REASON_STYLE = {
    "1": "shorter", "2": "detailed", "3": "sales", "4": "clarifying",
    "5": "no_discount", "6": "softer", "7": "business", "8": "knowledge_only",
}


def _fact_ids_of(mem):
    """Идентификаторы фактов из ответа памяти, какой бы ключ там ни был."""
    ids = mem.get("fact_ids")
    if isinstance(ids, (list, tuple)):
        return [str(x) for x in ids if x]
    one = mem.get("fact_id") or mem.get("id")
    return [str(one)] if one else []


def _save_reply(db, draft_id, body, author, event_meta, actor_id):
    db.execute(text(
        "UPDATE mop_drafts SET reply_text = :t, reply_author = :a,"
        " regen_count = COALESCE(regen_count, 0) + 1, updated_at = now() WHERE id = :i"
    ), {"t": body, "a": author, "i": draft_id})
    log_event(db, draft_id, "regenerated", channel="telegram",
              actor_type="telegram_user", actor_id=str(actor_id),
              payload=body[:500], meta=event_meta)
    db.commit()


def _generate_by_memory(db, d, actor_id):
    """Причина №8. Модель НЕ вызывается ни при каком исходе."""
    from app.api.client_memory import answer_for_ai
    mem = answer_for_ai(db, d["account_id"], d["incoming_text"] or "") or {}
    meta = {"model": "memory", "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "cost_rub": 0.0,
            "regeneration_reason": "knowledge_only"}
    from app.api.messenger import _memory_reply
    found = bool(mem.get("found")) and bool(str(mem.get("answer") or "").strip())
    # answer — сырое значение факта ("3000"). Человеческую фразу собирает
    # _memory_reply: "Книга жизни — 3000 руб.". Клиенту нельзя слать сырьё.
    body = str(_memory_reply(mem) or "").strip() if found else ""
    if not mem.get("use_memory", True):
        body, found = "", False
        meta["memory_off"] = True
    if found and body:
        meta["fact_ids"] = _fact_ids_of(mem)
    else:
        meta["fact_ids"] = []
        meta["memory_answer_found"] = False
        body = "Уточню эту информацию и вернусь к вам с точным ответом."
    _save_reply(db, d["id"], body, "ai_regenerated", meta, actor_id)
    return True, ("ответ из базы знаний" if meta["fact_ids"]
                  else "подтверждённых данных нет, поставил честное уточнение")


def _generate(db, draft_id, reason_code, actor_id="system"):
    """Хук HOOKS["generate"]: 1-7 через модель, 8 через память."""
    d = _draft(db, draft_id)
    if not d:
        return False, "карточка не найдена"
    style = REASON_STYLE.get(str(reason_code or ""))
    if style == "knowledge_only":
        return _generate_by_memory(db, d, actor_id)
    from app.api.messenger import generate_ai_draft_reply
    res = generate_ai_draft_reply(d["account_id"], {"id": d["avito_chat_id"]},
                                  style_hint=style, return_meta=True) or {}
    body = str(res.get("text") or "").strip()
    if not body or body.startswith("[Ошибка генерации"):
        log_event(db, draft_id, "ai_failed", channel="telegram", actor_type="system",
                  payload=body[:500] or "пустой ответ модели")
        db.commit()
        return False, "модель не ответила"
    meta = dict(res.get("usage") or {})
    meta["regeneration_reason"] = style or "без причины"
    _save_reply(db, draft_id, body, "ai_regenerated", meta, actor_id)
    return True, "готов другой вариант"


HOOKS["generate"] = _generate


# ------------------------------------------------- п.4: приём входящего (линия Б)

def begin_incoming(db, account_id, avito_chat_id, avito_message_id, incoming_text,
                   item_id=None, item_title=None, client_name=None,
                   ai_enabled=True):
    """Шаг 1. Создаёт/находит карточку и захватывает право на генерацию.

    Сессию вызывающий закрывает СРАЗУ после этого вызова — сеть идёт без неё.
    """
    draft_id, created = create_draft(db, account_id, avito_chat_id, avito_message_id,
                                     incoming_text, item_id, item_title, client_name)
    row = db.execute(text(
        "SELECT status, COALESCE(reply_text, '') FROM mop_drafts WHERE id = :i"
    ), {"i": draft_id}).fetchone()
    status = row[0] if row else "new"
    body = (row[1] if row else "") or ""

    need_generate = False
    no_reply_reason = None
    if not body.strip():
        if status == "new":
            handoff_reason=deterministic_handoff_reason(incoming_text)
            no_reply_reason=deterministic_no_reply_reason(incoming_text)
            if handoff_reason:
                got, _prev = set_status(
                    db, draft_id, "human_required", ("new",), "handed_to_human",
                    channel="web", actor_type="system",
                    payload="customer explicitly requested a human",
                    meta={"reason":handoff_reason,"policy_version":"MOP_HUMAN_REQUEST_V1"},
                )
                if got is not None:
                    status="human_required"
                    try:
                        from app.services.lead_notifications import queue_mop_events
                        queue_mop_events(db, int(draft_id), {"human_handoff": True, "handoff_reason": handoff_reason})
                    except Exception as _notify_exc:
                        print("LEAD_NOTIFICATION_QUEUE_HANDOFF_ERROR %s: %s" % (draft_id, type(_notify_exc).__name__), flush=True)
            elif no_reply_reason:
                got, _prev = set_status(
                    db, draft_id, "no_reply_required", ("new",), "closed_no_reply",
                    channel="web", actor_type="system",
                    payload="deterministic no-reply classification",
                    meta={"reason":no_reply_reason,"policy_version":"MOP_DETERMINISTIC_NO_REPLY_V1"},
                )
                if got is not None:
                    status="no_reply_required"
            elif ai_enabled:
                got, _prev = set_status(db, draft_id, "analyzing", ("new",),
                                        "analyzing_started", channel="telegram",
                                        actor_type="system")
                need_generate = got is not None
                if got is not None:
                    status = "analyzing"
            else:
                got, _prev = set_status(db, draft_id, "draft_ready", ("new",),
                                        "card_no_ai", channel="telegram",
                                        actor_type="system")
                if got is not None:
                    status = "draft_ready"
        elif status in ("draft_ready", "send_failed") and ai_enabled:
            # SELF_HEAL_EMPTY_REPLY_V1: a card can exist without generated text
            # after an interrupted/legacy migration or an attempted send of an
            # empty body. Reclaim the SAME exactly-once draft for generation;
            # never create a second card/message for the incoming message.
            got, _prev = set_status(db, draft_id, "analyzing", (status,),
                                    "regenerated", channel="web",
                                    actor_type="system", meta={"reason":"empty_reply_recovery"})
            need_generate = got is not None
            if got is not None:
                status = "analyzing"
        elif status == "analyzing":
            stale = db.execute(text(
                "SELECT COALESCE(updated_at, created_at)"
                " < now() - interval '10 minutes' FROM mop_drafts WHERE id = :i"
            ), {"i": draft_id}).scalar()
            need_generate = bool(stale)

    return {"draft_id": draft_id, "created": created,
            "status": status, "need_generate": need_generate,
            "no_reply_reason": no_reply_reason}


def reconcile_trivial_human_required(db, limit=100):
    """Close high-confidence legacy/answered human_required rows without external action.

    If MOP itself never sent the draft (sent_at is NULL) but Avito history now
    contains a real outgoing message after the original incoming, the human work
    was already completed externally and the stale red task must disappear.
    Otherwise the exact incoming must still be latest before deterministic legacy
    reconciliation can touch it.
    """
    answered_rows=db.execute(text("""
      SELECT d.id,d.account_id,d.avito_chat_id,d.avito_message_id
        FROM mop_drafts d
        JOIN messenger_messages mi
          ON mi.account_id=d.account_id AND mi.avito_message_id=d.avito_message_id
       WHERE d.status='human_required' AND d.sent_at IS NULL
         AND EXISTS (
           SELECT 1 FROM messenger_messages mo
            WHERE mo.account_id=d.account_id AND mo.avito_chat_id=d.avito_chat_id
              AND lower(mo.direction) LIKE 'out%'
              AND coalesce(mo.msg_type,'') <> 'system'
              AND mo.avito_created_at > mi.avito_created_at
         )
       ORDER BY d.updated_at,d.id
       LIMIT :lim
    """),{"lim":int(limit)}).mappings().all()
    answered_externally=0
    for d in answered_rows:
        got,_prev=set_status(
            db,int(d["id"]),"no_reply_required",("human_required",),"answered_externally",
            channel="avito",actor_type="human",actor_id="avito",
            payload="manager/outside-MOP reply exists after the original incoming",
            meta={"reason":"answered_externally","policy_version":"MOP_HUMAN_REQUIRED_EXTERNAL_CLOSE_V1","external_action":False},
            extra_sql=", send_error=NULL, locked_at=NULL, locked_by=NULL",
        )
        if got is not None:
            answered_externally+=1

    # MOP_HUMAN_REQUIRED_TOPIC_RESOLVED_V1: if the acknowledgement was already
    # sent by MOP and a later real outgoing explicitly answers that same handoff
    # topic, the red manager task is no longer work. Keep this intentionally
    # narrow: availability/price/delivery only; callbacks and channel handoffs stay.
    topic_rows=db.execute(text("""
      SELECT d.id,d.ai_summary,d.sent_at,
             (SELECT string_agg(coalesce(m.text,''),'\n' ORDER BY m.avito_created_at)
                FROM messenger_messages m
               WHERE m.account_id=d.account_id AND m.avito_chat_id=d.avito_chat_id
                 AND lower(m.direction) LIKE 'out%'
                 AND coalesce(m.msg_type,'') <> 'system'
                 AND d.sent_at IS NOT NULL
                 AND to_timestamp(m.avito_created_at) > d.sent_at) AS later_outgoing
        FROM mop_drafts d
       WHERE d.status='human_required' AND d.sent_at IS NOT NULL
       ORDER BY d.updated_at,d.id
       LIMIT :lim
    """),{"lim":int(limit)}).mappings().all()
    topic_resolved=0
    for d in topic_rows:
        later=str(d.get("later_outgoing") or "").lower().replace("ё","е")
        if not later:
            continue
        summary={}
        try:
            summary=json.loads(str(d.get("ai_summary") or "{}"))
            if not isinstance(summary,dict): summary={}
        except Exception:
            summary={}
        reason=str(summary.get("handoff_reason") or "")
        if not _handoff_topic_resolved(reason, later):
            continue
        got,_=set_status(
            db,int(d["id"]),"no_reply_required",("human_required",),"answered_externally",
            channel="mop_recovery",actor_type="system",actor_id="human_topic_resolved",
            payload="later outgoing message explicitly resolved the handoff topic",
            meta={"reason":"handoff_topic_resolved","policy_version":"MOP_HUMAN_REQUIRED_TOPIC_RESOLVED_V1","external_action":False},
            extra_sql=", send_error=NULL, locked_at=NULL, locked_by=NULL",
        )
        if got is not None:
            topic_resolved+=1

    rows=db.execute(text("""
      SELECT d.id,d.account_id,d.avito_chat_id,d.avito_message_id,d.incoming_text,d.reply_text,d.sent_at
        FROM mop_drafts d
       WHERE d.status='human_required'
         AND EXISTS (
           SELECT 1 FROM messenger_messages m
            WHERE m.account_id=d.account_id
              AND m.avito_chat_id=d.avito_chat_id
              AND m.avito_message_id=d.avito_message_id
              AND m.avito_created_at=(
                SELECT max(m2.avito_created_at) FROM messenger_messages m2
                 WHERE m2.account_id=d.account_id AND m2.avito_chat_id=d.avito_chat_id
              )
              AND lower(m.direction) LIKE 'in%'
         )
       ORDER BY d.updated_at,d.id
       LIMIT :lim
    """),{"lim":int(limit)}).mappings().all()
    out={"checked":len(rows)+len(answered_rows),"closed":0,"answered_externally":answered_externally,"technical_requeued":0,"reasons":{}}
    for d in rows:
        # MOP_TECHNICAL_HANDOFF_SELFHEAL_V1: a provider/model failure is not
        # legitimate manager work. If no reply was sent and this exact incoming
        # is still latest, move it back to the bounded provider-retry lane.
        last_handoff=db.execute(text("""
          SELECT event,payload FROM mop_draft_events
           WHERE draft_id=:i AND to_status='human_required'
           ORDER BY at DESC,id DESC LIMIT 1
        """),{"i":int(d["id"])}).mappings().first()
        _hp=str((last_handoff or {}).get("payload") or "").lower()
        _he=str((last_handoff or {}).get("event") or "")
        technical=(_he=="generation_failed" and any(x in _hp for x in ("local ollama","local_mop_busy","лимит ai","provider temporarily deferred","rate limit","quota")))
        if technical and not d.get("sent_at") and not str(d.get("reply_text") or "").strip():
            got,_=set_status(
                db,int(d["id"]),"waiting_external",("human_required",),"provider_deferred",
                channel="mop_recovery",actor_type="system",actor_id="technical_handoff_selfheal",
                payload="technical generation handoff returned to bounded automatic retry",
                meta={"reason":"local_provider_busy_timeout","policy_version":"MOP_TECHNICAL_HANDOFF_SELFHEAL_V1","external_action":False},
            )
            if got is not None:
                out["technical_requeued"]+=1
            continue
        reason=deterministic_no_reply_reason(d.get("incoming_text") or "")
        if not reason:
            continue
        got,_prev=set_status(
            db,int(d["id"]),"no_reply_required",("human_required",),"closed_no_reply",
            channel="web",actor_type="system",
            payload="legacy human escalation deterministically reconciled",
            meta={"reason":reason,"policy_version":"MOP_DETERMINISTIC_NO_REPLY_V1","external_action":False},
            extra_sql=", send_error=NULL, locked_at=NULL, locked_by=NULL",
        )
        if got is not None:
            out["closed"]+=1
            out["reasons"][reason]=int(out["reasons"].get(reason) or 0)+1
    return out


def waiting_external_local_decision(draft_mid, latest_mid, latest_direction):
    """Pure local-history decision used before any external provider retry."""
    draft_mid=str(draft_mid or "")
    latest_mid=str(latest_mid or "")
    direction=str(latest_direction or "").lower()
    if not latest_mid or latest_mid==draft_mid:
        return "unchanged"
    if direction.startswith("out"):
        return "answered_externally"
    if direction.startswith("in"):
        return "superseded"
    return "unchanged"


def _handoff_topic_resolved(reason, later_outgoing):
    """High-precision business-topic completion check for an existing handoff."""
    reason=str(reason or "").lower().replace("ё","е")
    later=str(later_outgoing or "").lower().replace("ё","е")
    if not later:
        return False
    if ("availability" in reason or "налич" in reason):
        return bool(re.search(r"(?:есть|в наличии|доступн|вариант)",later))
    if ("price" in reason or "цен" in reason or "стоим" in reason):
        return bool(re.search(r"(?:\d[\d\s]{2,}\s*(?:руб|₽)|стоим|цен)",later))
    if ("delivery" in reason or "достав" in reason or "вывоз" in reason):
        return bool(re.search(r"(?:достав|вывоз).{0,100}(?:\d[\d\s]{2,}|руб|₽|расчет|рассчит)",later))
    return False


def reconcile_waiting_external_local_activity(db, limit=200):
    """MOP_WAITING_EXTERNAL_LOCAL_FIRST_V1: close stale external waits from local truth.

    Before any provider reprobe/retry, inspect already-synced non-system chat
    history. A newer outgoing means the customer was answered elsewhere; a newer
    incoming supersedes the old draft. This removes false work and avoids API use.
    """
    rows=db.execute(text("""
      SELECT d.id,d.account_id,d.avito_chat_id,d.avito_message_id,
             latest.avito_message_id AS latest_mid,latest.direction AS latest_direction
        FROM mop_drafts d
        LEFT JOIN LATERAL (
          SELECT m.avito_message_id,m.direction
            FROM messenger_messages m
           WHERE m.account_id=d.account_id AND m.avito_chat_id=d.avito_chat_id
             AND coalesce(m.msg_type,'') <> 'system'
           ORDER BY m.avito_created_at DESC NULLS LAST,m.id DESC
           LIMIT 1
        ) latest ON true
       WHERE d.status='waiting_external'
       ORDER BY d.updated_at,d.id
       LIMIT :lim
    """),{"lim":max(1,min(int(limit or 200),500))}).mappings().all()
    out={"checked":len(rows),"answered_externally":0,"superseded":0,"unchanged":0}
    for d in rows:
        latest_mid=str(d.get("latest_mid") or "")
        draft_mid=str(d.get("avito_message_id") or "")
        decision=waiting_external_local_decision(draft_mid, latest_mid, d.get("latest_direction"))
        if decision=="unchanged":
            out["unchanged"]+=1
            continue
        if decision=="answered_externally":
            got,_=set_status(
                db,int(d["id"]),"no_reply_required",("waiting_external",),"answered_externally",
                channel="mop_recovery",actor_type="system",actor_id="waiting_external_local_first",
                payload="local chat history already contains a newer outgoing message",
                meta={"policy_version":"MOP_WAITING_EXTERNAL_LOCAL_FIRST_V1","external_action":False},
                extra_sql=", send_error=NULL, locked_at=NULL, locked_by=NULL",
            )
            if got is not None:
                out["answered_externally"]+=1
        elif decision=="superseded":
            got,_=set_status(
                db,int(d["id"]),"no_reply_required",("waiting_external",),"closed_no_reply",
                channel="mop_recovery",actor_type="system",actor_id="waiting_external_local_first",
                payload="local chat history contains a newer incoming message; old draft superseded",
                meta={"policy_version":"MOP_WAITING_EXTERNAL_LOCAL_FIRST_V1","external_action":False},
                extra_sql=", send_error=NULL, locked_at=NULL, locked_by=NULL",
            )
            if got is not None:
                out["superseded"]+=1
        else:
            out["unchanged"]+=1
    return out


def recover_waiting_external_provider(db, active_accounts=None, limit=50, fresh_hours=24):
    """MOP_PROVIDER_WAIT_AUTORETRY_V1: safely requeue AI-provider waits.

    The recovery itself makes NO provider/AI call. It only returns an exact
    still-latest incoming message to new after an exponential cooldown; the
    normal messenger poller performs the single canonical generation attempt.
    Newer activity closes the stale draft, and very old exact inquiries are
    handed to a manager instead of sending a surprise late reply.
    """
    active={str(x) for x in (active_accounts or []) if str(x or "").strip()}
    rows=db.execute(text("""
      SELECT d.id,d.account_id,d.avito_chat_id,d.avito_message_id,d.created_at,d.updated_at,
             extract(epoch from (now()-d.created_at))::bigint AS age_sec,
             (SELECT count(*) FROM mop_draft_events e
               WHERE e.draft_id=d.id AND e.event='provider_deferred'
                 AND coalesce(e.metadata->>'reason','')='local_provider_busy_timeout') AS local_defer_count,
             (SELECT count(*) FROM mop_draft_events e
               WHERE e.draft_id=d.id AND e.event='provider_deferred'
                 AND coalesce(e.metadata->>'reason','')='ai_provider_or_budget_deferred') AS external_defer_count,
             (SELECT coalesce(e.metadata->>'reason','') FROM mop_draft_events e
               WHERE e.draft_id=d.id AND e.event='provider_deferred'
               ORDER BY e.at DESC,e.id DESC LIMIT 1) AS last_deferred_reason,
             (SELECT max(e.at) FROM mop_draft_events e
               WHERE e.draft_id=d.id AND e.event='provider_deferred'
                 AND coalesce(e.metadata->>'reason','') IN ('ai_provider_or_budget_deferred','local_provider_busy_timeout')) AS last_deferred_at
        FROM mop_drafts d
       WHERE d.status='waiting_external'
         AND coalesce(d.reply_text,'')=''
         AND EXISTS (
             SELECT 1 FROM mop_draft_events e
              WHERE e.draft_id=d.id AND e.event='provider_deferred'
                AND (
                    coalesce(e.metadata->>'reason','') IN ('ai_provider_or_budget_deferred','local_provider_busy_timeout')
                    OR lower(coalesce(e.payload,'')) LIKE '%лимит ai%'
                    OR lower(coalesce(e.payload,'')) LIKE '%provider temporarily deferred%'
                    OR lower(coalesce(e.payload,'')) LIKE '%mop_provider_deferred_wait_v1%'
                )
         )
       ORDER BY d.updated_at,d.id
       LIMIT :lim
    """),{"lim":max(1,min(int(limit or 50),200))}).mappings().all()
    out={"checked":0,"inactive_skipped":0,"cooldown":0,"requeued":0,
         "answered_externally":0,"superseded":0,"stale_handoff":0,"no_message_evidence":0,"accounts":{}}
    def _bump(aid, key, n=1):
        out[key]=int(out.get(key) or 0)+int(n)
        per=out["accounts"].setdefault(str(aid),{"checked":0,"cooldown":0,"requeued":0,"answered_externally":0,"superseded":0,"stale_handoff":0,"no_message_evidence":0})
        if key in per:
            per[key]=int(per.get(key) or 0)+int(n)
    for d in rows:
        aid=str(d.get("account_id") or "")
        if active and aid not in active:
            out["inactive_skipped"]+=1
            continue
        _bump(aid,"checked")
        last_reason=str(d.get("last_deferred_reason") or "")
        if last_reason=="local_provider_busy_timeout":
            defer_count=max(1,int(d.get("local_defer_count") or 1))
            # MOP_LOCAL_PROVIDER_RETRY_BACKOFF_V1:
            # Local contention is expected to clear quickly after another
            # on-box inference finishes: 2m, 5m, 10m, 20m, then 30m.
            # Recovery only requeues the exact latest incoming message; it does
            # not call the provider itself and therefore cannot create a retry storm.
            _local_backoff=(2*60,5*60,10*60,20*60,30*60)
        else:
            defer_count=max(1,int(d.get("external_defer_count") or 1))
            _local_backoff=(5*60,15*60,30*60,60*60,120*60)
        backoff_sec=_local_backoff[min(max(0,defer_count-1),len(_local_backoff)-1)]
        last_deferred=d.get("last_deferred_at") or d.get("updated_at")
        due=bool(db.execute(text(
            "SELECT :t IS NULL OR :t <= now() - (:s * interval '1 second')"
        ),{"t":last_deferred,"s":int(backoff_sec)}).scalar())
        if not due:
            _bump(aid,"cooldown")
            continue
        latest=db.execute(text("""
          SELECT avito_message_id,direction,avito_created_at
            FROM messenger_messages
           WHERE account_id=:a AND avito_chat_id=:c
           ORDER BY avito_created_at DESC NULLS LAST,id DESC
           LIMIT 1
        """),{"a":aid,"c":str(d.get("avito_chat_id") or "")}).mappings().first()
        if not latest:
            _bump(aid,"no_message_evidence")
            continue
        latest_id=str(latest.get("avito_message_id") or "")
        draft_mid=str(d.get("avito_message_id") or "")
        direction=str(latest.get("direction") or "").lower()
        did=int(d["id"])
        if latest_id != draft_mid:
            if direction.startswith("out"):
                got,_=set_status(
                    db,did,"no_reply_required",("waiting_external",),"answered_externally",
                    channel="mop_recovery",actor_type="system",actor_id="provider_wait_retry",
                    payload="newer outgoing message already answered the chat",
                    meta={"policy_version":"MOP_PROVIDER_WAIT_AUTORETRY_V1","external_action":False},
                )
                if got is not None: _bump(aid,"answered_externally")
            else:
                got,_=set_status(
                    db,did,"no_reply_required",("waiting_external",),"closed_no_reply",
                    channel="mop_recovery",actor_type="system",actor_id="provider_wait_retry",
                    payload="newer incoming message superseded provider-deferred draft",
                    meta={"policy_version":"MOP_PROVIDER_WAIT_AUTORETRY_V1","external_action":False},
                )
                if got is not None: _bump(aid,"superseded")
            continue
        age_sec=int(d.get("age_sec") or 0)
        if age_sec > max(3600,int(fresh_hours or 24)*3600):
            got,_=set_status(
                db,did,"human_required",("waiting_external",),"handed_to_human",
                channel="mop_recovery",actor_type="system",actor_id="provider_wait_retry",
                payload="AI provider wait exceeded safe late-auto-reply window",
                meta={"policy_version":"MOP_PROVIDER_WAIT_AUTORETRY_V1","stale_age_sec":age_sec,
                      "external_action":False,"reason":"provider_wait_stale"},
            )
            if got is not None: _bump(aid,"stale_handoff")
            continue
        got,_=set_status(
            db,did,"new",("waiting_external",),"returned_to_ai",
            channel="mop_recovery",actor_type="system",actor_id="provider_wait_retry",
            payload="bounded provider retry due; canonical messenger poller will retry exact latest incoming once",
            meta={"policy_version":"MOP_PROVIDER_WAIT_AUTORETRY_V1","defer_count":defer_count,
                  "backoff_sec":backoff_sec,"external_action":False},
        )
        if got is not None: _bump(aid,"requeued")
    return out


def generation_failed(db, draft_id, note=""):
    """MOP_PROVIDER_DEFERRED_WAIT_V2: transient provider failures never sit in analyzing."""
    note_s=str(note or "")[:500]; low=note_s.lower()
    local_defer=("local_mop_busy" in low or "local ollama fast mop failed: timed out" in low or "local ollama returned invalid json" in low or "ollama returned empty response" in low or ("local ollama" in low and "retry=timed out" in low))
    external_defer=any(x in low for x in ("лимит ai","provider temporarily deferred","rate limit","insufficient_quota","quota exceeded","billing hard limit"))
    failed_before=db.execute(text("SELECT count(*) FROM mop_draft_events WHERE draft_id=:i AND event='generation_failed'"),{"i":draft_id}).scalar() or 0
    if local_defer or external_defer:
        reason="local_provider_busy_timeout" if local_defer else "ai_provider_or_budget_deferred"
        row,_=set_status(db,draft_id,"waiting_external",("analyzing",),"provider_deferred",channel="telegram",actor_type="system",payload=note_s,meta={"reason":reason,"policy_version":"MOP_PROVIDER_DEFERRED_WAIT_V2"})
        return "waiting_external" if row is not None else "gen_failed"
    if int(failed_before)>=2:
        row,_=set_status(db,draft_id,"human_required",("analyzing",),"generation_failed",channel="telegram",actor_type="system",payload=note_s)
        return "human_required" if row is not None else "gen_failed"
    log_event(db,draft_id,"generation_failed",channel="telegram",actor_type="system",payload=note_s)
    db.execute(text("UPDATE mop_drafts SET updated_at=now() WHERE id=:i"),{"i":draft_id}); db.commit(); return "gen_failed"


def finish_incoming(db, draft_id, reply_text, usage=None, analysis=None, notify_manager=True):
    """Шаг 2. Reply + same-call qualification are persisted atomically; no second classifier.

    When the same AI call says a human is required, manual mode enters
    human_required immediately. Autopilot keeps draft_ready only long enough to
    send the safe acknowledgement, then the caller promotes sent -> human_required.
    """
    import json as _json
    summary = _json.dumps(analysis, ensure_ascii=False) if analysis else None
    handoff = bool(isinstance(analysis, dict) and analysis.get("human_handoff") is True)
    target_status = "human_required" if (handoff and notify_manager) else "draft_ready"
    event = "handed_to_human" if target_status == "human_required" else "ai_generated"
    event_meta = dict(usage or {})
    if handoff:
        event_meta["handoff_reason"] = str((analysis or {}).get("handoff_reason") or "AI policy handoff")[:240]
        event_meta["policy_version"] = "MOP_STRUCTURED_HANDOFF_V1"
    row, prev = set_status(
        db, draft_id, target_status, ("analyzing",), event,
        channel="telegram", actor_type="system", meta=(event_meta or None),
        extra_sql=", reply_text = :rt, reply_author = 'ai', ai_summary = :summary",
        extra_params={"rt": reply_text, "summary": summary})
    if row is None:
        return "dup"
    try:
        from app.services.lead_notifications import queue_mop_events
        queue_mop_events(db, int(draft_id), analysis if isinstance(analysis, dict) else {})
    except Exception as _notify_exc:
        print("LEAD_NOTIFICATION_QUEUE_MOP_ERROR %s: %s" % (draft_id, type(_notify_exc).__name__), flush=True)
    if not notify_manager:
        return target_status
    res = push_card(db, draft_id)
    if res == ROUTE_MISSING:
        return "route_missing"
    return "human_required" if target_status == "human_required" else ("card_sent" if res else "card_failed")
