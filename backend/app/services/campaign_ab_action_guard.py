"""Server-side evidence guard for destructive/scaling A/B Campaign actions."""
from __future__ import annotations


def evaluate_ab_action_evidence(action: str, evidence: dict | None) -> dict:
    action=str(action or "").strip().lower()
    item=dict(evidence or {})
    state=str(item.get("experiment_state") or "").strip().lower()
    try:
        views=max(0,int(item.get("views") or 0))
    except (TypeError,ValueError):
        views=0
    try:
        contacts=max(0,int(item.get("contacts") or 0))
    except (TypeError,ValueError):
        contacts=0
    try:
        conversion=max(0.0,float(item.get("conversion") or 0))
    except (TypeError,ValueError):
        conversion=0.0

    base={
        "action":action,
        "state":state or None,
        "views":views,
        "contacts":contacts,
        "conversion":conversion,
        "minimum_views":30,
        "minimum_contacts_for_winner":3,
    }

    if action not in {"finish","scale"}:
        return {**base,"allowed":True,"code":"not_evidence_gated"}

    if not item:
        return {
            **base,
            "allowed":False,
            "code":"ab_evidence_missing",
            "message":"BORIS не нашёл актуальную статистику этого объявления. Действие не выполнено.",
        }

    if action=="scale":
        if views<30 or contacts<3:
            return {
                **base,
                "allowed":False,
                "code":"ab_insufficient_evidence",
                "message":"Для масштабирования нужно минимум 30 просмотров и 3 обращения.",
            }
        if state!="leader":
            return {
                **base,
                "allowed":False,
                "code":"ab_not_proven_winner",
                "message":"Масштабировать можно только подтверждённого лидера текущего A/B теста.",
            }
        return {
            **base,
            "allowed":True,
            "code":"ab_winner_evidence_pass",
            "message":"Выборки достаточно, объявление подтверждено как лидер.",
        }

    # Finish: a true zero-contact loser may be stopped after enough views.
    # If there are 1-2 contacts, keep observing until the same >=3-contact
    # comparison window used by the UI can classify it reliably.
    if views<30 or (0<contacts<3):
        return {
            **base,
            "allowed":False,
            "code":"ab_insufficient_evidence",
            "message":"Данных пока мало: BORIS не завершает объявление на слабой выборке.",
        }
    if state!="weak":
        return {
            **base,
            "allowed":False,
            "code":"ab_not_proven_loser",
            "message":"Завершить автоматически можно только вариант, подтверждённый как слабый.",
        }
    return {
        **base,
        "allowed":True,
        "code":"ab_loser_evidence_pass",
        "message":"Выборки достаточно, вариант подтверждён как слабый.",
    }


def enforce_current_ab_action(db, user, account_id: str, item_id: int, action: str) -> dict:
    """Use the same read-only A/B projection as UI, then enforce evidence server-side."""
    from fastapi import HTTPException
    from app.api import campaigns as campaign_api

    projection=campaign_api.campaign_ab_tests(str(account_id),db,user)
    evidence=next(
        (
            row for row in (projection.get("items") or [])
            if int(row.get("item_id") or 0)==int(item_id)
        ),
        None,
    )
    result=evaluate_ab_action_evidence(action,evidence)
    if not bool(result.get("allowed")):
        raise HTTPException(
            status_code=409,
            detail={
                "code":result.get("code") or "ab_evidence_blocked",
                "message":result.get("message") or "Данных A/B теста пока недостаточно для этого действия.",
                "evidence":{
                    "state":result.get("state"),
                    "views":result.get("views"),
                    "contacts":result.get("contacts"),
                    "conversion":result.get("conversion"),
                    "minimum_views":result.get("minimum_views"),
                    "minimum_contacts_for_winner":result.get("minimum_contacts_for_winner"),
                },
            },
        )
    return result
