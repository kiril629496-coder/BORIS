"""Read-only Campaign runtime health derived from truthful execution clocks."""
from __future__ import annotations


def classify_campaign_runtime_health(*, timing: dict, status: str, item_total: int,
                                     item_progress: bool, last_progress_age_seconds: float | None,
                                     requires_owner_action: bool = False,
                                     current_mode: str = "") -> dict:
    total=max(0.0,float((timing or {}).get("duration_total_seconds") or 0))
    active=max(0.0,float((timing or {}).get("duration_active_seconds") or 0))
    waiting=max(0.0,float((timing or {}).get("duration_waiting_seconds") or 0))
    provider=max(0.0,float((timing or {}).get("duration_provider_seconds") or 0))
    waiting_ratio=round(waiting/total,4) if total>0 else 0.0
    no_progress=max(0.0,float(last_progress_age_seconds or 0))
    state="OK"
    reasons=[]

    if requires_owner_action or str(status or "")=="awaiting_confirmation":
        return {
            "health_state":"WAITING_OWNER",
            "health_reasons":["Нужно действие владельца"],
            "waiting_ratio":waiting_ratio,
            "no_progress_seconds":round(no_progress,1),
            "sla_state":"PAUSED_FOR_OWNER",
        }

    if total>=120 and waiting_ratio>0.50:
        state="WARNING"
        reasons.append("Больше половины времени уходит на системное ожидание")
    if total>=600 and waiting_ratio>0.75:
        state="CRITICAL"
        reasons.append("Системное ожидание превышает 75% времени")

    provider_inflight=str(current_mode or "").strip().lower()=="provider"
    if not provider_inflight and no_progress>=600:
        if state=="OK":
            state="WARNING"
        reasons.append("Нет подтверждённого прогресса более 10 минут")
    if not provider_inflight and no_progress>=1800:
        state="CRITICAL"
        reasons.append("Нет подтверждённого прогресса более 30 минут")

    sla_state="OK"
    if item_progress and int(item_total or 0)>=100:
        if total>2700:
            sla_state="CRITICAL"
            state="CRITICAL"
            reasons.append("100+ объявлений выполняются дольше 45 минут")
        elif total>1200:
            sla_state="WARNING"
            if state=="OK":
                state="WARNING"
            reasons.append("100+ объявлений выполняются дольше 20 минут")

    return {
        "health_state":state,
        "health_reasons":reasons,
        "waiting_ratio":waiting_ratio,
        "no_progress_seconds":round(no_progress,1),
        "sla_state":sla_state,
        "timing_sum_seconds":round(active+waiting+provider,3),
    }
