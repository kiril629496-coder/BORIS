# -*- coding: utf-8 -*-
"""
Смысловая дедупликация системы мониторинга BORIS.

Технические дубли (одно и то же сообщение, прочитанное дважды) ловят курсор
источника и уникальный индекс. Здесь — про другое: один и тот же ТЕКСТ,
опубликованный под разными external_message_id.

Что это ловит:
    репост в другой канал · повторная публикация через день ·
    тот же текст с изменённым хвостом или эмодзи

Правило v1: НИЧЕГО не удаляем и не запрещаем. Только проставляем
duplicates_group, а в выдаче показываем основное сообщение группы,
остальные считаем повторами. Уникальным индексом группа не защищена
сознательно — ложное срабатывание не должно терять находку.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from .rules import NORMALIZER, normalize_text

# Окно склейки: тот же текст, опубликованный в пределах окна, — одна группа.
# Через неделю тот же вопрос от того же человека — уже новая история.
WINDOW_HOURS = 72

# Слова, которые не несут смысла и мешают отпечатку сойтись при мелких правках
_STOP = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя",
    "их", "чем", "была", "сам", "чтоб", "без", "будто", "человек", "чего",
    "раз", "тоже", "себе", "под", "жизнь", "будет", "ж", "тогда", "кто",
    "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь",
    "этом", "один", "почти", "мой", "тем", "чтобы", "нее", "были", "куда",
    "всем", "всего", "при", "об", "the", "a", "an", "and", "or", "to", "of",
    "in", "on", "for", "is", "are", "we", "you", "it", "this", "that",
}

_URL_RE = re.compile(r"https?://\S+|t\.me/\S+")
_EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002700-\U000027BF"
    "\U0001F000-\U0001F0FF" "\U00002600-\U000026FF" "\uFE0F" "]+"
)


def clean_text(text: str | None) -> str:
    """Убирает ссылки, эмодзи и повторные пробелы — то, что чаще всего правят при перепосте."""
    s = _URL_RE.sub(" ", text or "")
    s = _EMOJI_RE.sub(" ", s)
    return normalize_text(s)


def strict_fingerprint(text: str | None) -> str:
    """Отпечаток дословного совпадения: порядок слов сохраняется."""
    tokens = NORMALIZER.tokens(clean_text(text))
    keys = NORMALIZER.keys(tokens)
    return hashlib.sha1(" ".join(keys).encode("utf-8")).hexdigest()[:16]


def soft_fingerprint(text: str | None, top_n: int = 14) -> str:
    """
    Отпечаток, устойчивый к мелким правкам: значимые основы, без повторов,
    отсортированные. Переставленные слова и дописанный хвост его не меняют.
    Короткие тексты (меньше 4 значимых слов) отпечатка не получают —
    иначе «Кто подскажет?» склеит десяток разных сообщений.
    """
    tokens = NORMALIZER.tokens(clean_text(text))
    keys = [k for k in NORMALIZER.keys(tokens) if len(k) >= 3 and k not in _STOP]
    uniq = sorted(set(keys))
    if len(uniq) < 4:
        return ""
    return hashlib.sha1(" ".join(uniq[:top_n]).encode("utf-8")).hexdigest()[:16]


def window_key(posted_at: datetime | None, hours: int = WINDOW_HOURS) -> str:
    """Номер временного окна. Соседние окна не склеиваются — это принятое упрощение v1."""
    dt = posted_at or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return str(int((dt - epoch) / timedelta(hours=hours)))


def duplicates_group(platform: str, text: str | None,
                     posted_at: datetime | None = None) -> str | None:
    """
    Ключ группы: платформа + окно + мягкий отпечаток.
    None означает «сообщение слишком короткое, группировать не с чем».
    """
    soft = soft_fingerprint(text)
    if not soft:
        return None
    return f"{platform}:{window_key(posted_at)}:{soft}"
