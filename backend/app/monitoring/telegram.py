# -*- coding: utf-8 -*-
"""
TelegramScanner — первый драйвер системы мониторинга источников.

ВАЖНО: telethon импортируется ЛЕНИВО, внутри fetch_new/connect.
Благодаря этому normalize() и весь dry-run работают на машине,
где telethon не установлен и api_id/api_hash ещё не получены.

Формат сырого элемента (его строит fetch_new, его же читает normalize):
    {
      "message": {...Message.to_dict()...},
      "sender":  {...User.to_dict()...} | None,
      "chat":    {"id":..., "username":..., "title":...}
    }
Причина конверта: в Telethon у объекта Message нет имени автора,
отправителя приходится резолвить отдельно. Конверт делает normalize() чистым.
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timezone
from typing import Any

from .base import RawMessage, ScannerError, SourceRef

PLATFORM = "telegram"

# Пауза между источниками, чтобы не выглядеть роботом и не ловить FloodWait
MIN_DELAY_SEC = float(os.getenv("MONITOR_TG_MIN_DELAY", "2"))
MAX_DELAY_SEC = float(os.getenv("MONITOR_TG_MAX_DELAY", "6"))


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _peer_id(peer: Any) -> str | None:
    """Из PeerUser/PeerChannel/PeerChat достаёт числовой идентификатор."""
    if peer is None:
        return None
    if isinstance(peer, (int, str)):
        return str(peer)
    if isinstance(peer, dict):
        for k in ("user_id", "channel_id", "chat_id"):
            if peer.get(k) is not None:
                return str(peer[k])
    return None


def _topic_id_from(msg: dict[str, Any]) -> int | None:
    """
    В супергруппе с темами принадлежность сообщения теме лежит в reply_to:
    forum_topic=True, reply_to_top_id — id темы (у первого уровня — reply_to_msg_id).
    """
    rt = msg.get("reply_to") or {}
    if not isinstance(rt, dict):
        return None
    if not rt.get("forum_topic"):
        return None
    return rt.get("reply_to_top_id") or rt.get("reply_to_msg_id")


class TelegramScanner:
    """Реализует протокол SourceScanner."""

    platform = PLATFORM

    def __init__(self, client: Any = None):
        # client — готовый TelegramClient; в dry-run не нужен
        self._client = client

    # ------------------------------------------------------------ ссылка

    def build_url(self, raw_id: str, source: SourceRef) -> str | None:
        if not raw_id:
            return None
        if source.username:
            u = source.username.lstrip("@")
            if source.topic_id:
                return f"https://t.me/{u}/{source.topic_id}/{raw_id}"
            return f"https://t.me/{u}/{raw_id}"
        if source.external_id:
            internal = str(source.external_id)
            if internal.startswith("-100"):
                internal = internal[4:]
            internal = internal.lstrip("-")
            if source.topic_id:
                return f"https://t.me/c/{internal}/{source.topic_id}/{raw_id}"
            return f"https://t.me/c/{internal}/{raw_id}"
        return None

    # --------------------------------------------------------- нормализация

    def normalize(self, raw: dict[str, Any], source: SourceRef) -> RawMessage | None:
        """Чистая функция. Сеть не трогает, telethon не импортирует."""
        if not isinstance(raw, dict):
            return None
        msg = raw.get("message") or {}
        if not isinstance(msg, dict):
            return None

        text = (msg.get("message") or "").strip()
        mid = msg.get("id")
        if mid is None:
            return None
        if not text:
            # медиа без подписи ядру правил бесполезно
            return None

        sender = raw.get("sender") or {}
        chat = raw.get("chat") or {}

        author_id = _peer_id(msg.get("from_id")) or (
            str(sender.get("id")) if sender.get("id") is not None else None
        )
        first = (sender.get("first_name") or "").strip()
        last = (sender.get("last_name") or "").strip()
        author_name = (" ".join(x for x in (first, last) if x)).strip() or None
        if not author_name:
            # пост от имени канала — автора-человека нет
            author_name = msg.get("post_author") or chat.get("title") or None

        username = sender.get("username") or None
        topic_id = _topic_id_from(msg) or source.topic_id

        eff_source = source
        if topic_id and not source.topic_id:
            eff_source = SourceRef(
                id=source.id, platform=source.platform, source_kind=source.source_kind,
                external_id=source.external_id, username=source.username,
                title=source.title, topic_id=topic_id,
                topic_title=source.topic_title,
                last_seen_external_id=source.last_seen_external_id,
            )

        payload = {
            "views": msg.get("views"),
            "forwards": msg.get("forwards"),
            "is_forward": bool(msg.get("fwd_from")),
            "has_media": bool(msg.get("media")),
            "chat_title": chat.get("title"),
            "chat_username": chat.get("username"),
        }
        payload = {k: v for k, v in payload.items() if v not in (None, False)}

        return RawMessage(
            platform=self.platform,
            external_message_id=str(mid),
            text=text,
            posted_at=_parse_dt(msg.get("date")),
            url=self.build_url(str(mid), eff_source),
            external_author_id=author_id,
            author_name=author_name,
            author_username=username,
            topic_id=topic_id,
            topic_title=source.topic_title,
            payload=payload,
        )

    # ------------------------------------------------------------- сбор

    def fetch_new(self, source: SourceRef, limit: int = 100) -> list[dict[str, Any]]:
        """
        Ходит в Telegram и отдаёт СЫРЫЕ элементы в конверте, описанном выше.
        Требует подключённого клиента (api_id/api_hash + авторизованная сессия).
        Ничего не отправляет и никуда не вступает — только чтение.
        """
        if self._client is None:
            raise ScannerError(
                "TelegramClient не подключён: нужны api_id/api_hash и авторизованная сессия",
                source_id=source.id,
            )

        try:
            from telethon.errors import FloodWaitError  # ленивый импорт
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"telethon не установлен: {e}", source_id=source.id)

        peer = source.username or source.external_id
        if not peer:
            raise ScannerError("У источника нет ни username, ни external_id", source_id=source.id)

        min_id = int(source.last_seen_external_id or 0)
        out: list[dict[str, Any]] = []

        try:
            entity = self._client.get_entity(peer)
            chat = {
                "id": getattr(entity, "id", None),
                "username": getattr(entity, "username", None),
                "title": getattr(entity, "title", None),
            }
            kwargs: dict[str, Any] = {"limit": limit}
            if min_id:
                kwargs["min_id"] = min_id
            if source.topic_id:
                kwargs["reply_to"] = int(source.topic_id)

            for m in self._client.iter_messages(entity, **kwargs):
                sender = None
                try:
                    s = m.sender
                    if s is not None:
                        sender = {
                            "id": getattr(s, "id", None),
                            "username": getattr(s, "username", None),
                            "first_name": getattr(s, "first_name", None),
                            "last_name": getattr(s, "last_name", None),
                        }
                except Exception:  # noqa: BLE001
                    sender = None
                out.append({"message": m.to_dict(), "sender": sender, "chat": chat})

        except FloodWaitError as e:
            raise ScannerError(
                f"FloodWait {e.seconds}s на источнике {source.username or source.external_id}",
                source_id=source.id, retry_after=int(e.seconds),
            )
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"{type(e).__name__}: {e}", source_id=source.id)

        time.sleep(random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC))
        return out


def build_client(session_string: str, api_id: int, api_hash: str) -> Any:
    """
    Собирает TelegramClient из расшифрованной строки сессии.
    Вызывается только из monitor_runner.py, когда ключи уже есть.
    Ни api_hash, ни session_string НЕ логируются.
    """
    from telethon.sync import TelegramClient          # ленивый импорт
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    client.connect()
    if not client.is_user_authorized():
        raise ScannerError("Сессия Telegram недействительна — нужна повторная авторизация")
    return client
