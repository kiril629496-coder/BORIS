"""
BORIS Monitoring — Stage 2 Telegram preprocessing.

Задачи:
1. Склеить элементы Telegram media-group/grouped_id в одну логическую запись.
2. Не терять подпись альбома.
3. Передавать максимальный message id дальше, чтобы cursor не отставал.
4. Извлекать публично указанные в тексте @username и телефоны.
5. Ничего не отправлять и никуда не вступать.

Этот слой работает ДО rules/LLM/dedupe.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from app.services.exception_observability import observe_suppressed


_USERNAME_RE = re.compile(
    r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})"
)

_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+7|8)"
    r"[\s\-().]*\d{3}"
    r"[\s\-().]*\d{3}"
    r"[\s\-().]*\d{2}"
    r"[\s\-().]*\d{2}(?!\d)"
)


def _message_id(raw: dict[str, Any]) -> int:
    for key in ("id", "message_id"):
        try:
            return int(raw.get(key) or 0)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=38)
    return 0


def _grouped_id(raw: dict[str, Any]) -> str | None:
    value = raw.get("grouped_id")
    if value not in (None, "", 0):
        return str(value)

    msg = raw.get("message")
    if isinstance(msg, dict):
        value = msg.get("grouped_id")
        if value not in (None, "", 0):
            return str(value)

    return None


def _text(raw: dict[str, Any]) -> str:
    for key in ("message", "text", "caption"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    msg = raw.get("message")
    if isinstance(msg, dict):
        for key in ("message", "text", "caption"):
            value = msg.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def extract_contacts(text: str | None) -> dict[str, list[str]]:
    text = text or ""

    usernames: list[str] = []
    phones: list[str] = []

    seen_u = set()
    for m in _USERNAME_RE.finditer(text):
        username = m.group(1)
        key = username.lower()
        if key not in seen_u:
            seen_u.add(key)
            usernames.append("@" + username)

    seen_p = set()
    for m in _PHONE_RE.finditer(text):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)

        if digits.startswith("8") and len(digits) == 11:
            normalized = "+7" + digits[1:]
        elif digits.startswith("7") and len(digits) == 11:
            normalized = "+" + digits
        else:
            normalized = raw.strip()

        if normalized not in seen_p:
            seen_p.add(normalized)
            phones.append(normalized)

    return {
        "usernames": usernames[:10],
        "phones": phones[:10],
    }


def _set_text(raw: dict[str, Any], text: str) -> None:
    if isinstance(raw.get("message"), str):
        raw["message"] = text
        return

    if "text" in raw:
        raw["text"] = text
        return

    if isinstance(raw.get("message"), dict):
        raw["message"]["message"] = text
        return

    raw["message"] = text


def prepare_raw_items(
    raw_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Один grouped_id -> одна логическая запись.

    Не сгруппированные сообщения возвращаются 1:1.
    Для альбома representative получает максимальный id,
    поэтому last_seen_external_id сможет продвинуться до конца альбома.
    """
    singles: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[tuple[str, str | int]] = []

    for pos, original in enumerate(raw_items):
        if not isinstance(original, dict):
            continue

        raw = copy.deepcopy(original)
        gid = _grouped_id(raw)

        if not gid:
            raw["_boris_extracted_contacts"] = extract_contacts(_text(raw))
            singles.append(raw)
            order.append(("single", len(singles) - 1))
            continue

        if gid not in groups:
            groups[gid] = []
            order.append(("group", gid))

        groups[gid].append(raw)

    merged_groups: dict[str, dict[str, Any]] = {}

    for gid, items in groups.items():
        # Берём элемент с максимальным id как representative,
        # но текст собираем со всего альбома.
        representative = copy.deepcopy(
            max(items, key=_message_id)
        )

        texts: list[str] = []
        seen = set()

        for item in items:
            t = _text(item)
            if t and t not in seen:
                seen.add(t)
                texts.append(t)

        merged_text = "\n\n".join(texts).strip()

        if merged_text:
            _set_text(representative, merged_text)

        representative["_boris_album"] = {
            "grouped_id": gid,
            "message_ids": [
                _message_id(x)
                for x in items
                if _message_id(x)
            ],
            "items_count": len(items),
        }

        representative["_boris_extracted_contacts"] = (
            extract_contacts(merged_text)
        )

        merged_groups[gid] = representative

    result: list[dict[str, Any]] = []

    for kind, key in order:
        if kind == "single":
            result.append(singles[int(key)])
        else:
            result.append(merged_groups[str(key)])

    return result


def enrich_normalized_message(
    msg: Any,
    raw: dict[str, Any],
) -> Any:
    """
    Обогащает NormalizedMessage без изменения его идентичности.

    ВАЖНО:
    external_author_id здесь НЕ подменяется извлечённым телефоном/@username.
    Иначе постер-бот и настоящий автор могут быть ошибочно смешаны.
    """
    extracted = raw.get("_boris_extracted_contacts") or {}

    usernames = extracted.get("usernames") or []

    # Улучшаем отображение автора, но не identity key контакта.
    if usernames and not getattr(msg, "author_username", None):
        try:
            setattr(
                msg,
                "author_username",
                usernames[0].lstrip("@"),
            )
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=231)

    # Если payload уже есть и изменяемый — сохраняем доказательства туда.
    payload = getattr(msg, "payload", None)

    if isinstance(payload, dict):
        payload.setdefault(
            "boris_extracted_contacts",
            extracted,
        )

        if raw.get("_boris_album"):
            payload.setdefault(
                "boris_album",
                raw["_boris_album"],
            )

    return msg
