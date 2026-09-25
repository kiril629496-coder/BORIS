"""
BORIS Sale Score v1.

Это не match score правила.

Match score отвечает:
    "насколько сообщение совпало с мониторинговым правилом?"

Sale Score отвечает:
    "насколько это потенциально хороший клиент BORIS?"

0..100.
"""

from __future__ import annotations
from typing import Any


AVITO = (
    "авито",
    "avito",
    "авитолог",
)

PAIN = (
    "нет заявок",
    "мало заявок",
    "заявок почти нет",
    "результата нет",
    "толку ноль",
    "тольку ноль",
    "потратили много денег",
    "слили бюджет",
    "не работает",
    "не можем настроить",
)

LONG_TERM = (
    "на постоянку",
    "на постоянной основе",
    "долгосрочно",
    "постоянное сотрудничество",
    "стабильно",
)

AUTOMATION = (
    "автоматизация",
    "автоматизировать",
    "массовая загрузка",
    "фид",
    "api",
)

BUY = (
    "нужен",
    "нужна",
    "ищу",
    "ищем",
    "подрядчик",
    "исполнитель",
    "кто возьмёт",
    "кто возьмет",
    "кто ведёт",
    "кто ведет",
)


def calculate(item: dict[str, Any]) -> dict[str, Any]:

    text=(item.get("text") or "").lower()

    match_score=int(item.get("score_total") or 0)
    intent=(item.get("intent") or "").lower()

    payload=item.get("payload") or {}

    extracted=(
        payload.get("boris_extracted_contacts")
        or payload.get("_boris_extracted_contacts")
        or {}
    )

    author_username=item.get("author_username")

    score=0
    reasons=[]

    # До 40 — качество исходного match.
    score += min(40, round(match_score * 0.40))

    if intent == "buy_service":
        score += 20
        reasons.append("прямой спрос на услугу")

    if any(x in text for x in AVITO):
        score += 18
        reasons.append("Avito-контекст")

    if any(x in text for x in PAIN):
        score += 12
        reasons.append("есть явная боль")

    if any(x in text for x in LONG_TERM):
        score += 5
        reasons.append("долгосрочный запрос")

    if any(x in text for x in AUTOMATION):
        score += 5
        reasons.append("нужна автоматизация")

    if any(x in text for x in BUY):
        score += 5

    has_contact=bool(
        author_username
        or extracted.get("usernames")
        or extracted.get("phones")
    )

    if has_contact:
        score += 5
        reasons.append("есть контакт")

    # Не-Avito маркетинговый спрос оставляем,
    # но ниже в приоритете BORIS.
    if (
        not any(x in text for x in AVITO)
        and intent == "buy_service"
    ):
        score -= 12
        reasons.append("не Avito — ниже приоритет")

    score=max(0,min(100,int(score)))

    if score >= 80:
        tier="hot"
    elif score >= 60:
        tier="warm"
    elif score >= 40:
        tier="possible"
    else:
        tier="low"

    return {
        "version":1,
        "score":score,
        "tier":tier,
        "reasons":reasons[:6],
    }
