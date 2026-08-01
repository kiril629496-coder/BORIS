# -*- coding: utf-8 -*-
"""
Базовые контракты системы мониторинга источников BORIS.

Ядро работает ТОЛЬКО с RawMessage и ничего не знает о платформе.
Любой новый источник (VK, Reddit, форум, OLX) реализует SourceScanner
и приводит свой формат к RawMessage в normalize().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Protocol, runtime_checkable

# Платформы. Значение попадает в monitor_sources.platform / monitor_messages.platform.
PLATFORMS = (
    "telegram", "vk", "reddit", "youtube", "forum",
    "avito", "olx", "listam", "linkedin", "website",
)

# Вид источника — отдельно от платформы, чтобы фильтры и маршрутизация не разбирали строку.
SOURCE_KINDS = ("channel", "group", "topic", "forum", "user", "page", "board")


@dataclass
class RawMessage:
    """
    Единый объект на выходе любого сканера.
    Всё, что специфично для платформы, кладётся в payload — колонок под это не заводим.
    """
    platform: str
    external_message_id: str
    text: str
    posted_at: datetime | None = None
    url: str | None = None

    external_author_id: str | None = None
    author_name: str | None = None
    author_username: str | None = None

    topic_id: int | None = None
    topic_title: str | None = None

    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d


@dataclass
class SourceRef:
    """
    Описание источника в том виде, в каком его получает сканер.
    Соответствует строке monitor_sources, но не тянет за собой SQLAlchemy —
    сканер остаётся тестируемым без БД.
    """
    id: str
    platform: str
    source_kind: str
    external_id: str | None = None
    username: str | None = None
    title: str | None = None
    topic_id: int | None = None
    topic_title: str | None = None
    last_seen_external_id: int | None = None
    is_active: bool = True

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SourceRef":
        return cls(
            id=str(row.get("id")),
            platform=row.get("platform"),
            source_kind=row.get("source_kind"),
            external_id=row.get("external_id"),
            username=row.get("username"),
            title=row.get("title"),
            topic_id=row.get("topic_id"),
            topic_title=row.get("topic_title"),
            last_seen_external_id=row.get("last_seen_external_id"),
            is_active=bool(row.get("is_active", True)),
        )


@runtime_checkable
class SourceScanner(Protocol):
    """
    Интерфейс сканера источника. Три метода — намеренно узкий контракт.

    fetch_new()  — ходит в сеть, отдаёт СЫРЫЕ ответы платформы как есть.
    normalize()  — чистая функция, превращает сырой ответ в RawMessage.
                   Разделены специально: сырой ответ сохраняется в фикстуру
                   и normalize() тестируется без сети и без ключей доступа.
    build_url()  — ссылка на конкретное сообщение.
    """

    platform: str

    def fetch_new(self, source: SourceRef, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def normalize(self, raw: dict[str, Any], source: SourceRef) -> RawMessage | None:
        ...

    def build_url(self, raw_id: str, source: SourceRef) -> str | None:
        ...


class ScannerError(Exception):
    """Ошибка сканера, не останавливающая прогон: пишется в monitor_runs.errors."""

    def __init__(self, message: str, source_id: str | None = None, retry_after: int = 0):
        super().__init__(message)
        self.source_id = source_id
        self.retry_after = retry_after


_REGISTRY: dict[str, Any] = {}


def register_scanner(scanner: Any) -> None:
    _REGISTRY[scanner.platform] = scanner


def get_scanner(platform: str) -> Any:
    if platform not in _REGISTRY:
        raise ScannerError(f"Сканер для платформы '{platform}' не зарегистрирован")
    return _REGISTRY[platform]


def registered_platforms() -> Iterable[str]:
    return tuple(_REGISTRY.keys())
