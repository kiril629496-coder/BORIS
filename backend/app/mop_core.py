"""Ядро AI-МОПа: единственная точка смены состояния карточки ответа.

Каналы (Telegram, кабинет, позже MAX/WhatsApp) — только экраны. Они вызывают
функции этого модуля и никогда не пишут в Avito сами. Источник истины — БД.
На этом этапе модуль ниоткуда не импортируется рабочим кодом.
"""
import hashlib
import re

from sqlalchemy import bindparam, text

# ---------------------------------------------------------------- справочники

STATUSES = (
    "new", "analyzing", "draft_ready", "editing", "custom_waiting",
    "waiting_confirm", "sending", "sent", "send_failed",
    "deleted", "human_required", "no_reply_required",
)

TERMINAL = ("sent", "no_reply_required")

# из какого статуса в какой можно перейти
TRANSITIONS = {
    "new": ("analyzing", "deleted", "human_required", "no_reply_required"),
    "analyzing": ("draft_ready", "deleted", "human_required"),
    "draft_ready": ("editing", "custom_waiting", "analyzing", "sending",
                    "deleted", "human_required", "no_reply_required"),
    "editing": ("waiting_confirm", "draft_ready", "deleted", "human_required"),
    "custom_waiting": ("waiting_confirm", "draft_ready", "deleted", "human_required"),
    "waiting_confirm": ("sending", "editing", "deleted", "human_required"),
    "sending": ("sent", "send_failed"),
    "send_failed": ("sending", "editing", "custom_waiting", "human_required"),
    "deleted": ("analyzing", "custom_waiting", "human_required"),
    "human_required": ("draft_ready", "no_reply_required"),
    "sent": (),
    "no_reply_required": (),
}

EVENTS = (
    "draft_created", "ai_generated", "ai_failed", "regenerated",
    "edit_requested", "edited", "custom_requested", "custom_written",
    "awaiting_expired", "awaiting_cancelled", "approved", "send_started",
    "sent", "send_failed", "send_recovered", "draft_deleted",
    "handed_to_human", "returned_to_ai", "lead_created", "closed_no_reply",
    "card_posted",
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
    """Одно входящее = одна карточка. Повторный вызов вернёт существующую."""
    row = db.execute(text(
        "SELECT id FROM mop_drafts WHERE account_id = :a AND avito_message_id = :m"
    ), {"a": account_id, "m": avito_message_id}).fetchone()
    if row:
        return row[0], False
    new_id = db.execute(text(
        "INSERT INTO mop_drafts (account_id, avito_chat_id, avito_message_id,"
        " incoming_text, item_id, item_title, client_name, status)"
        " VALUES (:a, :c, :m, :t, :i, :ti, :cn, 'new') RETURNING id"
    ), {"a": account_id, "c": avito_chat_id, "m": avito_message_id, "t": incoming_text,
        "i": item_id, "ti": item_title, "cn": client_name}).scalar()
    log_event(db, new_id, "draft_created", to_status="new", actor_type="system")
    db.commit()
    return new_id, True


def claim_for_send(db, draft_id, actor_type, actor_id, channel):
    """Захват под отправку. Второй нажавший получает (None, текущий_статус)."""
    return set_status(
        db, draft_id, "sending",
        allowed_from=("draft_ready", "waiting_confirm", "send_failed"),
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
        extra_sql=", sent_at = now(), outgoing_text_hash = :h, locked_at = NULL, locked_by = NULL",
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

def _draft(db, draft_id):
    row = db.execute(text("SELECT * FROM mop_drafts WHERE id = :i"), {"i": draft_id}).fetchone()
    return dict(row._mapping) if row else None


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
    lines.append("Время: %s · <a href=\"%s\">Диалог</a>" % (
        when.strftime("%H:%M") if when else "—", chat_url))
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
    b = lambda t, c: {"text": t, "callback_data": "%s:%s:%s" % (NS, c, i)}
    if st in ("sent", "no_reply_required", "sending"):
        return []
    if st == "human_required":
        return [[b("↩️ Вернуть в работу", "b")]]
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
    """Текст человека НИКОГДА не уходит в Avito сразу — только в waiting_confirm."""
    db.execute(text(
        "UPDATE mop_drafts SET reply_text = :t, reply_author = 'manager', updated_at = now()"
        " WHERE id = :i"), {"t": body, "i": draft_id})
    db.commit()
    return set_status(db, draft_id, "waiting_confirm", ("editing", "custom_waiting"),
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


def do_send(db, draft_id, actor_id, channel="telegram"):
    """Единственный путь отправки. Захват атомарный, второй нажавший опоздал."""
    row, st = claim_for_send(db, draft_id, "telegram_user", actor_id, channel)
    if row is None:
        return False, "уже в работе или отправлено (%s)" % st
    body = row.get("reply_text") or ""
    if not body.strip():
        mark_send_failed(db, draft_id, "пустой текст ответа", channel=channel, actor_id=actor_id)
        return False, "текст ответа пуст"
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
                               ("draft_ready", "waiting_confirm", "send_failed"),
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
                               ("draft_ready", "waiting_confirm", "send_failed"),
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
        elif code == "d":
            r, st = set_status(db, draft_id, "deleted",
                               ("draft_ready", "editing", "custom_waiting", "waiting_confirm"),
                               "draft_deleted", channel="telegram",
                               actor_type="telegram_user", actor_id=str(user))
            note = "черновик удалён" if r else "сейчас статус %s" % st
        elif code == "h":
            r, st = set_status(db, draft_id, "human_required",
                               ("draft_ready", "editing", "custom_waiting",
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
                               ("draft_ready", "new", "human_required"),
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
                   item_id=None, item_title=None, client_name=None):
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
    if not body.strip():
        if status == "new":
            got, _prev = set_status(db, draft_id, "analyzing", ("new",),
                                    "analyzing_started", channel="telegram",
                                    actor_type="system")
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
            "status": status, "need_generate": need_generate}


def generation_failed(db, draft_id, note=""):
    """Генерация не удалась. Статус НЕ двигаем — следующий цикл повторит."""
    log_event(db, draft_id, "generation_failed", channel="telegram",
              actor_type="system", payload=(note or "")[:500])
    db.execute(text("UPDATE mop_drafts SET updated_at = now() - interval '1 hour'"
                    " WHERE id = :i"), {"i": draft_id})
    db.commit()
    return "gen_failed"


def finish_incoming(db, draft_id, reply_text, usage=None):
    """Шаг 2. Текст, автор и статус — одним атомарным UPDATE, затем карточка."""
    row, prev = set_status(
        db, draft_id, "draft_ready", ("analyzing",), "ai_generated",
        channel="telegram", actor_type="system", meta=(usage or None),
        extra_sql=", reply_text = :rt, reply_author = 'ai'",
        extra_params={"rt": reply_text})
    if row is None:
        return "dup"
    res = push_card(db, draft_id)
    if res == ROUTE_MISSING:
        return "route_missing"
    return "card_sent" if res else "card_failed"
