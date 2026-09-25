"""Deterministic lead-purpose classification for KPI accounting.

This module intentionally uses no paid AI. It protects business KPI from
job-seeker messages that arrive on ordinary advertising listings while keeping
recruitment/vacancy accounts and vacancy listings countable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from app.services.exception_observability import observe_suppressed


_STRONG_JOB_SEEKER = (
    "по вакансии",
    "интересует вакансия",
    "насчет вакансии",
    "насчёт вакансии",
    "ищу работу",
    "ищу подработку",
    "хочу устроиться",
    "хочу работать",
    "устроиться на работу",
    "по поводу работы",
    "есть работа",
    "трудоустройство",
    "отправить резюме",
    "мое резюме",
    "моё резюме",
)

# MOP_DIRECT_JOB_SEEKER_ASK_V1:
# High-precision phrases where the sender is offering themselves as labour.
# Deliberately do not use a broad "работник" stem: it collides with normal
# customer requests such as "бытовка для работников".
_DIRECT_JOB_SEEKER_ASK = (
    "вам работник не требуется",
    "вам работники не требуются",
    "вам случайно работник не требуется",
    "вам случайно работники не требуются",
    "вам рабочий не требуется",
    "вам рабочие не требуются",
    "вам случайно рабочий не требуется",
    "вам случайно рабочие не требуются",
    "вам мастер не требуется",
    "вам мастера не требуются",
    "вам случайно мастер не требуется",
    "вам случайно мастера не требуются",
    "нужен работник",
    "нужны работники",
    "требуется работник",
    "требуются работники",
)

_SUPPORT_JOB_SEEKER = (
    "ваканси",
    "резюме",
    "зарплат",
    "оклад",
    "график работы",
    "сменный график",
    "трудоустрой",
    "стажиров",
    "собеседован",
    "кандидат",
    "соискател",
)

_RECRUITMENT_LISTING = (
    "ваканси",
    "работа ",
    "требуется",
    "ищем сотруд",
    "ищем менедж",
    "ищем продав",
    "ищем водител",
    "набор сотруд",
    "подбор персонал",
)

_RECRUITMENT_ACCOUNT = (
    "ваканси",
    "подбор персонал",
    "рекрут",
    "соискател",
    "трудоустрой",
    "кадров",
)

# Do not add broad stems such as "персонал" or "работник" here.
# They collide with normal business facts: "персональные данные",
# "бытовка для работников", "доставка работников" etc. Recruitment account
# classification must require an explicit recruiting/vacancy signal.

# Avito can inject recruitment system messages into vacancy chats.  When the
# lead row is missing or stale, these phrases are enough to prove that the
# conversation belongs to a vacancy and therefore must remain a target KPI
# contact rather than being filtered as an accidental job seeker.
_VACANCY_SYSTEM = (
    "вы откликнулись на вакансию",
    "работодатель получил ваше резюме",
    "кандидат откликнулся на вакансию",
    "его данные сохранились в разделе отклики",
    "вот его резюме",
)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _account_is_recruitment(db, account_id: str) -> bool:
    try:
        row = db.execute(
            text(
                "SELECT coalesce(name,''), coalesce(client_goal,''), "
                "coalesce(client_goal_text,'') FROM accounts WHERE account_id=:a"
            ),
            {"a": account_id},
        ).first()
        account_text = _norm(" ".join(str(x or "") for x in (row or ())))
        if any(token in account_text for token in _RECRUITMENT_ACCOUNT):
            return True
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=125)
    try:
        rows = db.execute(
            text(
                "SELECT value FROM client_facts "
                "WHERE account_id=:a AND status='confirmed' "
                "AND category IN ('business','goal','product','rule') "
                "ORDER BY id DESC LIMIT 50"
            ),
            {"a": account_id},
        ).fetchall()
        memory_text = _norm(" ".join(str(x[0] or "") for x in rows))
        if any(token in memory_text for token in _RECRUITMENT_ACCOUNT):
            return True
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=140)
    return False


def _day_start_epoch(db, account_id: str) -> int:
    now = datetime.now(timezone.utc)
    try:
        from app.factory.runtime_config import local_now

        local, _meta = local_now(db, account_id, now)
        if local.tzinfo is None:
            local = local.replace(tzinfo=timezone.utc)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp())
    except Exception:
        return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def classify_job_seeker_text(inbound_text: str, item_title: str = "") -> tuple[bool, str]:
    """Return (exclude_from_business_kpi, reason)."""
    msg = _norm(inbound_text)
    title = _norm(item_title)
    if not msg:
        return False, "no_inbound_text"
    if any(token in title for token in _RECRUITMENT_LISTING):
        return False, "vacancy_listing"
    if any(_norm(token) in msg for token in _VACANCY_SYSTEM):
        return False, "vacancy_system_message"
    if any(_norm(token) in msg for token in _STRONG_JOB_SEEKER):
        return True, "explicit_job_seeker"
    if any(_norm(token) in msg for token in _DIRECT_JOB_SEEKER_ASK):
        return True, "direct_job_seeker_ask"
    support_hits = sum(1 for token in _SUPPORT_JOB_SEEKER if _norm(token) in msg)
    if support_hits >= 2:
        return True, "job_seeker_signals"
    return False, "business_or_unknown"


def job_seekers_today(db, account_id: str) -> dict[str, Any]:
    """Classify today's inbound Avito dialogs and return excluded KPI count."""
    if _account_is_recruitment(db, account_id):
        return {
            "status": "ok",
            "account_mode": "recruitment",
            "excluded_count": 0,
            "checked_dialogs": 0,
            "items": [],
            "rule": "job_seekers_are_target_for_recruitment_account",
        }

    start_epoch = _day_start_epoch(db, account_id)
    try:
        rows = db.execute(
            text(
                """
                SELECT m.avito_chat_id,
                       coalesce(
                         max(nullif(l.item_title,'')),
                         max(nullif(m.item_title,'')),
                         ''
                       ) AS item_title,
                       string_agg(
                         coalesce(m.text,''),
                         ' ' ORDER BY m.avito_created_at
                       ) AS inbound_text
                  FROM messenger_messages m
                  LEFT JOIN messenger_leads l
                    ON l.avito_chat_id=m.avito_chat_id
                   AND l.account_id=m.account_id
                 WHERE m.account_id=:a
                   AND coalesce(m.avito_created_at,0) >= :s
                   AND lower(coalesce(m.direction,'')) IN ('in','incoming','client')
                 GROUP BY m.avito_chat_id
                """
            ),
            {"a": account_id, "s": start_epoch},
        ).mappings().all()
    except Exception as exc:
        return {
            "status": "degraded",
            "reason": f"classification_query_failed:{type(exc).__name__}",
            "excluded_count": 0,
            "checked_dialogs": 0,
            "items": [],
        }

    excluded: list[dict[str, Any]] = []
    for row in rows:
        is_job, reason = classify_job_seeker_text(
            str(row.get("inbound_text") or ""),
            str(row.get("item_title") or ""),
        )
        if is_job:
            excluded.append(
                {
                    "chat_id": str(row.get("avito_chat_id") or ""),
                    "item_title": str(row.get("item_title") or "")[:240],
                    "reason": reason,
                }
            )
    return {
        "status": "ok",
        "account_mode": "business",
        "excluded_count": len(excluded),
        "checked_dialogs": len(rows),
        "items": excluded,
        "rule": "exclude_job_seekers_from_business_kpi",
    }


def apply_business_lead_filter(db, account_id: str, raw_contacts: float | int) -> dict[str, Any]:
    """Convert raw Avito contacts into business KPI contacts conservatively."""
    try:
        raw = max(float(raw_contacts or 0), 0.0)
    except Exception:
        raw = 0.0
    result = job_seekers_today(db, account_id)
    excluded = min(raw, float(result.get("excluded_count") or 0))
    business = max(raw - excluded, 0.0)
    return {
        **result,
        "raw_contacts_today": raw,
        "excluded_job_seekers_today": excluded,
        "business_contacts_today": business,
    }
