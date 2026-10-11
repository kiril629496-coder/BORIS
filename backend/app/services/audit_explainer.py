from __future__ import annotations

from typing import Any, Dict, List


def _rank_weakest(breakdown: Dict[str, Any]) -> list[tuple[float, str, int, int]]:
    ranked = []
    for key, row in (breakdown or {}).items():
        try:
            points = float(row.get("score") or 0)
            maximum = max(1.0, float(row.get("max") or 1))
            ranked.append((points / maximum, str(row.get("label") or key), int(points), int(maximum)))
        except Exception:
            continue
    ranked.sort(key=lambda item: item[0])
    return ranked[:3]


def build_answer(audit: Dict[str, Any], question: str, recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    q = str(question or "").strip().lower()
    score = audit.get("score") or {}
    breakdown = score.get("breakdown") or {}
    loss = audit.get("loss") or {}
    confirmed = loss.get("confirmed") or {}
    estimated = loss.get("estimated") or {}
    assumptions = loss.get("assumptions") or {}
    reasons = list(audit.get("reasons") or [])
    gaps = list(score.get("data_gaps") or [])

    total = int(score.get("total") or 0)
    low = int(estimated.get("lost_revenue_low") or 0)
    high = int(estimated.get("lost_revenue_high") or 0)
    weakest = _rank_weakest(breakdown)
    action = recommendations[0] if recommendations else {
        "title": "Открыть BORIS",
        "href": "/dashboard/home",
    }

    if "почему" in q and ("score" in q or "скор" in q or "бал" in q):
        if weakest:
            parts = [f"{label}: {points}/{maximum}" for _, label, points, maximum in weakest]
            answer = (
                f"Ваш BORIS Score — {total}/100. Больше всего итог снижают: "
                + "; ".join(parts)
                + ". Эти значения рассчитаны формулой Score, а не выбраны AI."
            )
        else:
            answer = f"Ваш BORIS Score — {total}/100. Для подробной расшифровки пока недостаточно компонентов."

    elif any(word in q for word in ("сумм", "деньг", "потер", "руб")):
        if score.get("mode") == "connected":
            risk = int(confirmed.get("currently_or_never_unanswered") or 0)
            conv = assumptions.get("sales_conversion_percent_declared")
            check = assumptions.get("average_check_rub_declared")
            answer = (
                f"Диапазон {low:,}–{high:,} ₽ — расчётный, не подтверждённая потеря. "
                f"В connected-аудите база расчёта — {risk} проблемных диалогов"
                + (f", указанная конверсия {conv}%" if conv is not None else "")
                + (f" и средний чек {float(check):,.0f} ₽" if check is not None else "")
                + ". BORIS показывает диапазон, а не гарантированную сумму."
            ).replace(",", " ")
        else:
            conv = assumptions.get("conversion_percent")
            check = assumptions.get("average_check_rub")
            answer = (
                f"Диапазон {low:,}–{high:,} ₽ рассчитан по введённым вами данным. "
                + (f"Использована конверсия {conv}% " if conv is not None else "")
                + (f"и средний чек {float(check):,.0f} ₽. " if check is not None else "")
                + "После подключения Avito фактические обращения заменят часть предположений."
            ).replace(",", " ")

    elif any(word in q for word in ("что исправ", "с чего", "перв", "делать")):
        if recommendations:
            answer = "Начните с: " + "; ".join(
                str(x.get("title") or "") for x in recommendations[:3]
            ) + "."
        else:
            answer = (
                "Критичных действий по текущей версии аудита не найдено. "
                "Откройте детали и проверьте пробелы данных."
            )

    elif any(word in q for word in ("клиент", "лид", "без ответ", "вернут", "реактив")):
        unanswered = int(
            confirmed.get("currently_unanswered")
            or confirmed.get("currently_or_never_unanswered")
            or 0
        )
        recoverable = int(estimated.get("recoverable_leads") or 0)
        if score.get("mode") == "connected":
            answer = (
                f"Сейчас аудит видит {unanswered} диалогов без ответа и "
                f"{recoverable} лидов как кандидатов на проверку для возврата в работу. "
                "Это preview: массовая реактивация не запускается без отдельного подтверждения."
            )
        else:
            answer = (
                f"Предварительная оценка показывает около {recoverable} лидов для проверки. "
                "Подключите Avito, чтобы заменить эту оценку фактическими диалогами."
            )

    elif any(word in q for word in ("полный boris", "полный борис", "что даст", "зачем boris", "зачем борис")):
        modules = []
        for item in recommendations[:3]:
            href = str(item.get("href") or "")
            if href == "/messages":
                modules.append("Сообщения и AI-менеджер")
            elif "campaign" in href:
                modules.append("Объявления")
            elif "crm" in href:
                modules.append("CRM")
            elif "reactivation" in href:
                modules.append("Реактивация")
            elif "analytics" in href:
                modules.append("Аналитика")
        modules = list(dict.fromkeys(modules))
        answer = (
            "Audit Gateway показывает, что не так; основной BORIS даёт инструменты, чтобы это исправлять."
            + (f" По вашему аудиту в первую очередь релевантны: {', '.join(modules)}." if modules else "")
        )

    else:
        main = [str(x.get("text") or "") for x in reasons[:3] if x.get("text")]
        answer = (
            f"Ваш текущий Score — {total}/100. "
            + (f"Главные сигналы: {'; '.join(main)}. " if main else "")
            + "Спросите, почему такой Score, откуда взялась сумма или что исправить первым."
        )

    facts = []
    if score.get("mode") == "connected":
        facts = [
            {"label": "Диалогов за период", "value": confirmed.get("dialogs_period")},
            {"label": "Без ответа сейчас", "value": confirmed.get("currently_unanswered")},
            {"label": "Средний ответ, мин", "value": confirmed.get("avg_response_minutes")},
            {"label": "Avito conversion", "value": confirmed.get("view_to_contact_conversion_pct")},
        ]

    return {
        "mode": "deterministic_audit_answer",
        "answer": answer,
        "facts": [x for x in facts if x.get("value") is not None],
        "data_gaps": gaps,
        "action": action,
    }
