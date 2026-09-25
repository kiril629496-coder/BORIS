"""Shared, privacy-safe ROP brain.

The global layer contains only:
1) curated public sales principles;
2) anonymized cross-account aggregates.

Raw transcripts, names, phones, prices, company facts and offers never leave
an account scope and are never copied into this shared brain.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.exception_observability import observe_suppressed

GLOBAL_BRAIN_TABLE = "rop_global_brain"
ACCOUNT_BRAIN_TABLE = "rop_account_brain"
GLOBAL_BRAIN_VERSION = "rop_global_brain_v1"

PUBLIC_PLAYBOOK = [
    {
        "id": "discovery_before_pitch",
        "title": "Сначала диагностика, потом предложение",
        "guidance": "До презентации выясни цель клиента, текущую ситуацию, ограничения, сроки и критерии выбора. Не вываливай функции продукта раньше понимания задачи.",
        "source": "HubSpot / Salesforce",
        "source_urls": [
            "https://blog.hubspot.com/sales/discovery-call-questions",
            "https://www.salesforce.com/blog/sales/what-is-a-discovery-call/",
        ],
        "verified_at": "2026-09-10",
    },
    {
        "id": "targeted_questions_listen",
        "title": "Целевые вопросы и активное слушание",
        "guidance": "Задавай конкретные вопросы по ситуации клиента и оставляй клиенту достаточно пространства говорить. Уточняй смысл ответа, а не просто переходи к следующему пункту.",
        "source": "Gong / Salesforce",
        "source_urls": [
            "https://www.gong.io/blog/discovery-call",
            "https://trailhead.salesforce.com/content/learn/modules/maximize-the-impact-of-sales-calls/make-successful-sales-calls",
        ],
        "verified_at": "2026-09-10",
    },
    {
        "id": "objection_probe",
        "title": "Возражение сначала уточнить",
        "guidance": "На возражение сначала спокойно отреагируй и уточни настоящую причину, затем дай ответ под эту причину и проверь, снято ли сомнение.",
        "source": "HubSpot",
        "source_urls": [
            "https://blog.hubspot.com/sales/handling-common-sales-objections",
        ],
        "verified_at": "2026-09-10",
    },
    {
        "id": "clear_next_step",
        "title": "Каждый разговор заканчивать конкретным следующим шагом",
        "guidance": "Фиксируй конкретное действие, кто его делает и когда. Если следующий шаг — встреча или созвон, по возможности согласуй время сразу.",
        "source": "Salesforce",
        "source_urls": [
            "https://www.salesforce.com/blog/sales/what-is-a-discovery-call/",
        ],
        "verified_at": "2026-09-10",
    },
    {
        "id": "summarize_confirm",
        "title": "Резюмировать потребность перед закрытием",
        "guidance": "Коротко повтори, что понял про задачу клиента, и попроси подтвердить. После подтверждения связывай решение именно с этими потребностями.",
        "source": "HubSpot / Salesforce",
    },
]


def ensure_table(db) -> None:
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text(
        'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'
    ))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()


def latest_payload(db=None) -> dict[str, Any]:
    own = db is None
    db = db or SessionLocal()
    try:
        ensure_table(db)
        row = db.execute(text(
            f"SELECT payload FROM {GLOBAL_BRAIN_TABLE} "
            "ORDER BY generated_at DESC, id DESC LIMIT 1"
        )).first()
        if not row or not row[0]:
            return {
                "version": GLOBAL_BRAIN_VERSION,
                "public_playbook": PUBLIC_PLAYBOOK,
                "internal": {},
            }
        raw = row[0]
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=122)
        return {
            "version": GLOBAL_BRAIN_VERSION,
            "public_playbook": PUBLIC_PLAYBOOK,
            "internal": {},
        }
    finally:
        if own:
            db.close()





def _outcome_context(max_chars: int = 2600) -> str:
    """Cross-account CRM maturity associations; never described as causality."""
    db = SessionLocal()
    try:
        exists = db.execute(text("SELECT to_regclass('public.rop_outcome_brain')")).scalar()
        if not exists:
            return ""
        row = db.execute(text(
            "SELECT payload FROM rop_outcome_brain ORDER BY generated_at DESC,id DESC LIMIT 1"
        )).first()
        if not row or not row[0]:
            return ""
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        patterns = [
            x for x in (payload.get("patterns") or [])
            if isinstance(x, dict) and bool(x.get("reliable"))
        ]
        summary = payload.get("summary") or {}
        parts = [
            "СВЯЗЬ С ФАКТИЧЕСКИМИ СТАДИЯМИ CRM.",
            "Это наблюдаемая ассоциация, не доказанная причинность. Используй для приоритета обучения, а не как абсолютное правило.",
            f"Связано с CRM разборов: {int(summary.get('linked_total') or 0)}.",
        ]
        for x in patterns[:8]:
            lift = float(x.get("outcome_lift_points") or 0)
            direction = "выше" if lift > 0 else "ниже"
            parts.append(
                f"- {x.get('label')}: при наличии этапа текущий CRM maturity-score в среднем {direction} "
                f"примерно на {abs(lift):.1f} п.; сравнено аккаунтов: {int(x.get('accounts_with_comparison') or 0)}, "
                f"наблюдений: {int(x.get('samples') or 0)}."
            )
        return "\n".join(parts)[:max(800, int(max_chars))]
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=172)
        return ""
    finally:
        db.close()


def _niche_key(name: str, niche: str) -> str:
    s = (str(name or "") + " " + str(niche or "")).lower().replace("ё", "е")
    rules = [
        ("bytovki", ("бытов",)),
        ("plitka", ("тротуар", "брусчат", "плитк")),
        ("mebel", ("мебел", "кухн", "гардероб")),
        ("steklo", ("стекл", "перегород")),
        ("shtukaturka", ("штукатур",)),
        ("beton_stroitelstvo", ("бетон", "фундамент", "монолит", "жби")),
        ("almaznoe", ("алмазн",)),
        ("evakuatory", ("эвакуатор",)),
        ("marketing", ("маркетинг", "продвижение на авито")),
    ]
    for key, needles in rules:
        if any(n in s for n in needles):
            return key
    return ""


def _niche_context(db, account_id: str, max_chars: int = 2200) -> str:
    """Privacy-safe cross-account niche patterns, only if >=2 accounts built them."""
    try:
        exists = db.execute(text("SELECT to_regclass('public.rop_niche_brain')")).scalar()
        if not exists:
            return ""
        row = db.execute(text(
            "SELECT coalesce(name,''),coalesce(company_niche,'') FROM accounts WHERE account_id=:a"
        ), {"a": account_id}).first()
        if not row:
            return ""
        key = _niche_key(row[0], row[1])
        if not key:
            return ""
        brain = db.execute(text(
            "SELECT payload FROM rop_niche_brain WHERE niche_key=:k"
        ), {"k": key}).first()
        if not brain or not brain[0]:
            return ""
        payload = brain[0] if isinstance(brain[0], dict) else json.loads(brain[0])
        if int(payload.get("accounts") or 0) < 2:
            return ""
        labels = {
            "bytovki": "бытовки",
            "plitka": "тротуарная плитка",
            "mebel": "мебель",
            "steklo": "стекло и перегородки",
            "shtukaturka": "штукатурка",
            "beton_stroitelstvo": "бетон/строительство",
            "almaznoe": "алмазное бурение",
            "evakuatory": "эвакуаторы",
            "marketing": "маркетинг",
        }
        parts = [
            f"НИШЕВОЙ МОЗГ РОПА: {labels.get(key, key)}.",
            f"Только обезличенные агрегаты из {int(payload.get('accounts') or 0)} независимых аккаунтов; "
            "никаких цен, контактов и условий чужих компаний.",
        ]
        patterns = payload.get("outcome_patterns") or []
        if patterns:
            parts.append("Нишевые связи с продвижением сделки по CRM:")
            for x in patterns[:6]:
                lift = float(x.get("outcome_lift_points") or 0)
                parts.append(
                    f"- {x.get('label')}: ассоциация с CRM maturity-score {lift:+.1f} п.; "
                    f"наблюдений {int(x.get('samples') or 0)}."
                )
        else:
            parts.append("Нишевых outcome-паттернов пока недостаточно; не выдумывай их.")
        return "\n".join(parts)[:max(700, int(max_chars))]
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=251)
        return ""


def coaching_context(max_chars: int = 5000) -> str:
    """Compact prompt block safe to share across accounts."""
    payload = latest_payload()
    parts = [
        "ОБЩИЙ МОЗГ РОПА — ТОЛЬКО ОБЕЗЛИЧЕННЫЕ ПРАКТИКИ.",
        "Никогда не переноси между клиентами цены, имена, телефоны, адреса, "
        "условия, товары, акции или факты компании. Локальные данные текущего "
        "account_id всегда важнее общего мозга.",
        "",
        "БАЗОВЫЕ ПРАКТИКИ:",
    ]
    for item in payload.get("public_playbook") or PUBLIC_PLAYBOOK:
        title = str(item.get("title") or "").strip()
        guidance = str(item.get("guidance") or "").strip()
        if title and guidance:
            parts.append(f"- {title}: {guidance}")

    public_consensus = payload.get("public_consensus") or []
    if public_consensus:
        parts.extend([
            "",
            "ОБНОВЛЯЕМЫЕ ПРАКТИКИ ИЗ ИНТЕРНЕТА, ПОДТВЕРЖДЁННЫЕ 2+ ИСТОЧНИКАМИ:",
        ])
        for item in public_consensus[:8]:
            title = str(item.get("title") or item.get("tag") or "").strip()
            guidance = str(item.get("guidance") or "").strip()
            sources = int(item.get("source_count") or 0)
            if title and guidance and sources >= 2:
                parts.append(f"- {title}: {guidance} (источников: {sources})")

    internal = payload.get("internal") or {}
    dimensions = internal.get("dimensions") or []
    if dimensions:
        parts.extend([
            "",
            "НАШИ ОБЕЗЛИЧЕННЫЕ НАБЛЮДЕНИЯ ПО НЕСКОЛЬКИМ АККАУНТАМ:",
            "Считай их подсказками, а не причинно доказанными правилами.",
        ])
        for d in dimensions[:10]:
            label = str(d.get("label") or d.get("dimension") or "")
            accounts = int(d.get("accounts_seen") or 0)
            calls = int(d.get("calls_seen") or 0)
            done_rate = float(d.get("done_rate") or 0)
            if accounts >= 2 and calls >= 5:
                parts.append(
                    f"- {label}: выполнено примерно в {done_rate:.0f}% разборов; "
                    f"наблюдение основано на {calls} звонках из {accounts} аккаунтов."
                )

    summary = internal.get("summary") or {}
    if summary:
        parts.extend([
            "",
            "ОБЪЁМ ВНУТРЕННЕЙ БАЗЫ:",
            f"- разобрано звонков: {int(summary.get('calls') or 0)}",
            f"- аккаунтов с разборами: {int(summary.get('accounts') or 0)}",
            f"- звонков с зафиксированным следующим шагом: {int(summary.get('agreements') or 0)}",
        ])
    outcome = _outcome_context(max_chars=2600)
    if outcome:
        parts.extend(["", outcome])
    return "\n".join(parts)[:max(1000, int(max_chars))]


def account_coaching_context(account_id: str, max_chars: int = 3000) -> str:
    """Account-only ROP learning; never shared with another account."""
    account_id = str(account_id or "").strip()
    if not account_id:
        return ""
    db = SessionLocal()
    try:
        ensure_table(db)
        row = db.execute(text(
            f"SELECT payload FROM {ACCOUNT_BRAIN_TABLE} WHERE account_id=:a"
        ), {"a": account_id}).first()
        if not row or not row[0]:
            return ""
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        parts = [
            "ЛОКАЛЬНЫЙ РОП-ПРОФИЛЬ ЭТОГО АККАУНТА.",
            "Используй только для текущего account_id; наружу и другим клиентам не переносить.",
        ]
        niche_ctx = _niche_context(db, account_id, max_chars=2200)
        if niche_ctx:
            parts.extend(["", niche_ctx])
        summary = payload.get("summary") or {}
        parts.append(
            f"Разборов: {int(summary.get('analyses') or 0)} "
            f"(звонки {int(summary.get('calls') or 0)}, чаты {int(summary.get('chats') or 0)})."
        )
        gaps = payload.get("top_gaps") or []
        if gaps:
            parts.append("Главные повторяющиеся зоны роста:")
            for g in gaps[:5]:
                parts.append(
                    f"- {g.get('label')}: выполнено примерно в {float(g.get('done_rate') or 0):.0f}% разборов."
                )
        strengths = payload.get("top_strengths") or []
        if strengths:
            parts.append("Что уже получается стабильнее:")
            for g in strengths[:3]:
                parts.append(
                    f"- {g.get('label')}: выполнено примерно в {float(g.get('done_rate') or 0):.0f}% разборов."
                )
        return "\n".join(parts)[:max(800, int(max_chars))]
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=364)
        return ""
    finally:
        db.close()


def training_focus_context(account_id: str, max_chars: int = 1800) -> str:
    """Choose 1-3 coaching targets from observed CRM associations + local gaps."""
    account_id = str(account_id or "").strip()
    global_patterns = []
    local_gaps = []
    db = SessionLocal()
    try:
        exists = db.execute(text("SELECT to_regclass('public.rop_outcome_brain')")).scalar()
        if exists:
            row = db.execute(text(
                "SELECT payload FROM rop_outcome_brain ORDER BY generated_at DESC,id DESC LIMIT 1"
            )).first()
            if row and row[0]:
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                global_patterns = [
                    x for x in (payload.get("patterns") or [])
                    if isinstance(x, dict)
                    and bool(x.get("reliable"))
                    and float(x.get("outcome_lift_points") or 0) > 0
                ]
        row = db.execute(text(
            f"SELECT payload FROM {ACCOUNT_BRAIN_TABLE} WHERE account_id=:a"
        ), {"a": account_id}).first()
        if row and row[0]:
            payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            local_gaps = [x for x in (payload.get("top_gaps") or []) if isinstance(x, dict)]
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=400)
    finally:
        db.close()

    local_labels = {str(x.get("label") or "") for x in local_gaps if str(x.get("label") or "")}
    ranked = []
    for x in global_patterns:
        label = str(x.get("label") or "").strip()
        if not label:
            continue
        score = float(x.get("training_priority") or 0)
        if label in local_labels:
            score += 5.0
        ranked.append((score, label, x))
    ranked.sort(key=lambda t: (-t[0], t[1]))

    selected = ranked[:3]
    if not selected:
        selected = [(1.0, str(x.get("label") or ""), {}) for x in local_gaps[:3] if str(x.get("label") or "")]

    if not selected:
        return ""

    parts = [
        "СКРЫТЫЙ ФОКУС ТРЕНИРОВКИ РОПА. Клиент-симулятор не должен раскрывать этот список менеджеру.",
        "Создай естественную ситуацию, в которой менеджер получит возможность проявить эти навыки; не подсказывай ему правильный ответ.",
    ]
    for _, label, meta in selected:
        if not label:
            continue
        lift = float(meta.get("outcome_lift_points") or 0) if meta else 0
        done_rate = float(meta.get("done_rate") or 0) if meta else 0
        if lift > 0:
            parts.append(
                f"- {label}: высокий приоритет; навык пока выполняется примерно в {done_rate:.0f}% связанных разборов "
                f"и ассоциирован с более зрелой CRM-стадией."
            )
        else:
            parts.append(f"- {label}: локальная повторяющаяся зона роста этого аккаунта.")
    return "\n".join(parts)[:max(700, int(max_chars))]
