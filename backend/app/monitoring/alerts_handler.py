# -*- coding: utf-8 -*-
"""
Обработка нажатий на кнопки карточек мониторинга.

Вызывается из app.telegram_bot._handle_callback_query по префиксам
mlead: / mreply: / mskip: — ДО менеджерской логики msgr_*, чтобы наш uuid
не попал в _load_pending_draft.

Никаких сообщений найденным людям НЕ отправляет. «Подготовить ответ» лишь
присылает черновик в ту же тему, чтобы менеджер скопировал и написал сам.

Разделение с alerts.py:
    alerts.py       — рендер и ОТПРАВКА карточек (прогон мониторинга)
    alerts_handler  — РЕАКЦИЯ на нажатия (живёт в поллинге telegram_bot)
"""
from __future__ import annotations

import html
import logging

from app.monitoring import alerts, store

log = logging.getLogger("monitor.alerts_handler")

PREFIXES = ("mlead", "mreply", "mskip")


def owns(data: str) -> bool:
    """True, если callback принадлежит мониторингу. Используется в telegram_bot."""
    return isinstance(data, str) and data.split(":", 1)[0] in PREFIXES


def _answer(bot, callback_id: str, text: str = "") -> None:
    """
    Всплывающий ответ на нажатие (answerCallbackQuery). Публичной обёртки в
    telegram_bot нет, поэтому шлём сами — но через ЕГО же токен и ЕГО же
    транспорт (requests с прокси, если он настроен). Это служебный ack, не
    отправка сообщения; сообщения идут только через публичные функции.
    """
    token = getattr(bot, "BOT_TOKEN", None)
    if not token:
        return
    try:
        import requests
        proxies = bot._telegram_proxies() if hasattr(bot, "_telegram_proxies") else None
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text[:190]},
            proxies=proxies, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def handle(data: str, callback_query: dict) -> None:
    """
    data — строка вида "mlead:<uuid>".
    callback_query — сырой объект Telegram.
    """
    from app import telegram_bot as bot

    cb_id = callback_query.get("id")
    msg = callback_query.get("message", {}) or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")

    try:
        action, mid = data.split(":", 1)
    except ValueError:
        _answer(bot, cb_id, "Непонятная кнопка")
        return

    con = store.connect()
    try:
        with con, con.cursor() as cur:
            if action == "mlead":
                lead_id = store.create_lead(cur, mid)
                if lead_id:
                    _answer(bot, cb_id, "Лид создан")
                    alerts.edit_after_action(
                        chat_id, message_id,
                        _tail(msg) + "\n\n✅ <b>Лид создан.</b>")
                else:
                    _answer(bot, cb_id, "Сообщение не найдено")

            elif action == "mskip":
                store.set_status(cur, mid, "rejected")
                _answer(bot, cb_id, "Отмечено: не подходит")
                alerts.edit_after_action(
                    chat_id, message_id,
                    _tail(msg) + "\n\n❌ <b>Отмечено «не подходит».</b>")

            elif action == "mreply":
                text = store.prepare_reply(cur, mid)
                if not text:
                    _answer(bot, cb_id, "Не удалось подготовить ответ")
                else:
                    _answer(bot, cb_id, "Черновик ниже")
                    # отдельным сообщением в <code> — копируется одним касанием
                    alerts.send_text(
                        "✉️ <b>Черновик ответа</b> — проверьте и отправьте "
                        "со своего аккаунта:\n\n"
                        f"<code>{html.escape(text)}</code>")
            else:
                _answer(bot, cb_id, "Неизвестное действие")
    except Exception:  # noqa: BLE001
        log.exception("ошибка обработки кнопки мониторинга")
        _answer(bot, cb_id, "Ошибка, записана в лог")
    finally:
        con.close()


def _tail(msg: dict) -> str:
    """
    Текст карточки без клавиатуры для edit. Берём исходный HTML-текст сообщения,
    чтобы после действия карточка осталась читаемой, но уже без кнопок.
    Telegram в объекте callback отдаёт text без разметки — используем его как есть.
    """
    t = msg.get("text") or ""
    # обрезаем, чтобы уложиться в лимит editMessageText с запасом на приписку
    return html.escape(t[:3500])
