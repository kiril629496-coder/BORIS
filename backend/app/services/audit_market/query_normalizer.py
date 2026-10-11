from __future__ import annotations

import re

_STOP = {
    "в","на","для","и","или","по","из","с","со","к","ко","от","до","под","над",
    "купить","заказать","цена","стоимость","недорого","дешево","москва","казань",
}

def normalize_text(value: str) -> str:
    text = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9\s-]+", " ", str(value or "").lower())
    return " ".join(text.split())

def query_tokens(value: str) -> list[str]:
    return [x for x in normalize_text(value).split() if len(x) >= 3 and x not in _STOP]

def normalize_query(query: str, region: str = "") -> dict:
    q = " ".join(str(query or "").split()).strip()
    r = " ".join(str(region or "").split()).strip()
    if len(q) < 2:
        raise ValueError("Нужно указать направление бизнеса")
    return {
        "query": q,
        "region": r,
        "normalized_query": normalize_text(q),
        "tokens": query_tokens(q),
        "cache_key": f"{normalize_text(q)}|{normalize_text(r)}",
    }

def relevance(title: str, query: str) -> str:
    qt = set(query_tokens(query))
    tt = set(query_tokens(title))
    if not qt or not tt:
        return "irrelevant"
    overlap = len(qt & tt)
    ratio = overlap / max(1, len(qt))
    if overlap >= 2 or ratio >= 0.60:
        return "direct"
    if overlap >= 1 or ratio >= 0.30:
        return "adjacent"
    return "irrelevant"
