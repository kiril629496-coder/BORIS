# -*- coding: utf-8 -*-
"""
Карточки уведомлений системы мониторинга BORIS.

Отдельный бот («BORIS Monitor Bot») со своим токеном — существующий бот
уведомлений не трогается: два процесса на одном токене получают от Telegram
409 Conflict и глохнут оба.

Рендер карточки — чистая функция без сети, поэтому проверяется тестами.
"""
from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("monitor.alerts")

BUCKET_ICON = {"hot": "🔥", "warm": "🙂", "topic": "💬", "noise": "·"}
BUCKET_LABEL = {"hot": "Горячий лид", "warm": "Потенциальный клиент",
                "topic": "Тематическое обсуждение", "noise": "Шум"}


def _chat() -> str | None:
    return os.getenv("MONITOR_ALERT_CHAT_ID")


def _thread() -> int | None:
    v = os.getenv("MONITOR_ALERT_THREAD_ID")
    return int(v) if v and v.strip().isdigit() else None


def bucket_of(score: int) -> str:
    if score >= 90:
        return "hot"
    if score >= 70:
        return "warm"
    if score >= 40:
        return "topic"
    return "noise"


def human_age(posted_at: Any, now: datetime | None = None) -> str:
    if not posted_at:
        return "время неизвестно"
    if isinstance(posted_at, str):
        try:
            posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except ValueError:
            return "время неизвестно"
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    mins = int((now - posted_at).total_seconds() // 60)
    if mins < 1:
        return "только что"
    if mins < 60:
        return f"{mins} мин назад"
    hours = mins // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    return f"{days} дн назад"


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            v = json.loads(value)
            return v if isinstance(v, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            v = json.loads(value)
            return v if isinstance(v, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def render_card(row: dict[str, Any], now: datetime | None = None) -> tuple[str, dict]:
    """
    Возвращает (текст в HTML, клавиатура).
    row — строка из store.pending_alerts().
    """
    score = int(row.get("score_total") or 0)
    key = bucket_of(score)
    ai = _as_dict(row.get("ai"))
    reasons = _as_list(ai.get("reasons")) or _as_list(row.get("matched_terms"))

    e = html.escape
    lines = [f"{BUCKET_ICON[key]} <b>{BUCKET_LABEL[key]} · {score}/100</b>", ""]

    summary = row.get("summary") or (row.get("text") or "")[:200]
    lines.append(e(str(summary).strip()))
    lines.append("")

    src = row.get("source_title") or "источник не назван"
    if row.get("topic_title"):
        src = f"{src} · тема «{row['topic_title']}»"
    lines.append(f"Источник: {e(src)} (Telegram)")

    if ai.get("city"):
        lines.append(f"Регион: {e(str(ai['city']))}")
    if ai.get("service"):
        lines.append(f"Услуга: {e(str(ai['service']))}")

    author = row.get("author_username")
    author = f"@{author}" if author else (row.get("author_name") or "автор скрыт")
    lines.append(f"Автор: {e(str(author))}")
    lines.append(f"Найдено: {human_age(row.get('posted_at'), now)}")

    if reasons:
        lines.append("")
        lines.append("<b>Почему найден:</b>")
        for r in list(reasons)[:4]:
            lines.append(f"• {e(str(r))}")

    if ai.get("source") == "rules":
        lines.append("")
        lines.append("<i>Резюме собрано по правилам — модель была недоступна.</i>")

    mid = row.get("id")
    # формат РЯДОВ для send_telegram_message_with_buttons (обратно совместимой)
    buttons = [
        [{"text": "📂 Открыть", "url": row.get("url") or "https://t.me"},
         {"text": "✉️ Подготовить ответ", "callback_data": f"mreply:{mid}"}],
        [{"text": "✅ Создать лид", "callback_data": f"mlead:{mid}"},
         {"text": "❌ Не подходит", "callback_data": f"mskip:{mid}"}],
    ]
    return "\n".join(lines), buttons


def render_failure(reason: str, next_try: str = "через 30 минут") -> str:
    return ("⚠️ <b>Мониторинг не завершён</b>\n\n"
            f"Причина:\n{html.escape(reason)[:800]}\n\n"
            f"Следующая попытка: {html.escape(next_try)}")


def render_run_summary(stats: dict[str, int], errors: int = 0) -> str:
    return (
        "📊 <b>Прогон мониторинга</b>\n"
        f"источников: {stats.get('scanned_sources', 0)} · "
        f"сообщений: {stats.get('scanned_messages', 0)}\n"
        f"совпадений: {stats.get('matched_messages', 0)} · "
        f"новых: {stats.get('new_messages', 0)} · "
        f"дублей: {stats.get('duplicate_messages', 0)}\n"
        f"отправлено: {stats.get('sent_alerts', 0)} · ошибок: {errors}"
    )


# ------------------------------------------------------------------ отправка
#
# Только ПУБЛИЧНЫЕ функции app.telegram_bot: их токен, их прокси, их сессия.
# В приватные _telegram_post/API_BASE не лезем — обратно совместимая
# send_telegram_message_with_buttons принимает клавиатуру из нескольких рядов.


def _bot():
    """Ленивый импорт уведомителя проекта — рендер карточек работает и без него."""
    try:
        from app import telegram_bot
        return telegram_bot
    except Exception as e:  # noqa: BLE001
        log.warning("app.telegram_bot недоступен: %s", e)
        return None


def send_card(row: dict[str, Any]) -> bool:
    """Карточка с кнопками — через публичную send_telegram_message_with_buttons."""
    bot = _bot()
    if bot is None or not getattr(bot, "BOT_TOKEN", None):
        log.warning("уведомитель проекта не готов — карточка не отправлена")
        return False
    chat_id = _chat()
    if not chat_id:
        log.warning("MONITOR_ALERT_CHAT_ID не задан — карточка не отправлена")
        return False

    text, buttons = render_card(row)
    try:
        data = bot.send_telegram_message_with_buttons(
            chat_id, text, buttons, thread_id=_thread())
        return bool(data and data.get("ok"))
    except TypeError:
        # на случай, если функция ещё без thread_id (патч не применён) —
        # шлём без темы, чтобы карточка не потерялась
        data = bot.send_telegram_message_with_buttons(chat_id, text, buttons)
        return bool(data and data.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("отправка карточки не удалась: %s", e)
        return False


def send_text(text: str) -> bool:
    """Простое сообщение без кнопок — через публичную send_telegram_message."""
    bot = _bot()
    if bot is None or not getattr(bot, "BOT_TOKEN", None):
        return False
    chat_id = _chat()
    if not chat_id:
        return False
    try:
        data = bot.send_telegram_message(chat_id, text, thread_id=_thread())
        return bool(data and data.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("отправка текста не удалась: %s", e)
        return False


def edit_after_action(chat_id: Any, message_id: int, text: str) -> bool:
    """Гасит кнопки и показывает результат — через существующий edit_telegram_message."""
    bot = _bot()
    if bot is None:
        return False
    try:
        data = bot.edit_telegram_message(chat_id, message_id, text)
        return bool(data and data.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось обновить карточку: %s", e)
        return False


def notify_failure(reason: str, next_try: str = "через 30 минут") -> bool:
    return send_text(render_failure(reason, next_try))
