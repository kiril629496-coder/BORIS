# -*- coding: utf-8 -*-
"""Маскирование секретов.

Применяется ДВАЖДЫ: перед отправкой текста в модель и перед записью в
boris_commands / boris_command_events.
"""
import re

_MASK = "[скрыто]"

_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.I),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"\b(?:\d[ \-]?){13,19}\b"),
    re.compile(r"(?i)\b(пароль|password|passwd|token|секрет|secret|api[_ -]?key|"
               r"client[_ -]?secret)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b\w+://[^\s:@]+:[^\s@]+@\S+"),
)


def redact_text(value, limit=4000):
    """Возвращает текст без секретов. None и не-строки не ломают вызов."""
    if value is None:
        return None
    s = str(value)
    for rx in _PATTERNS:
        s = rx.sub(_MASK, s)
    return s[:limit]


def redact_payload(obj, depth=0):
    """Рекурсивная очистка словаря или списка для payload_redacted."""
    if depth > 6:
        return _MASK
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if re.search(r"(?i)(pass|token|secret|key|cred|auth)", str(k)):
                out[k] = _MASK
            else:
                out[k] = redact_payload(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_payload(v, depth + 1) for v in obj][:50]
    if isinstance(obj, str):
        return redact_text(obj, limit=1000)
    return obj
