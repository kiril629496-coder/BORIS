"""Account-scoped V7 creative-family evidence and conservative allocation."""
from __future__ import annotations

import json
from collections import defaultdict

V7_FAMILIES=("glass_light","glass_dark","warm_accent","charcoal_center")


def _payload(row):
    raw=getattr(row,"payload_json",None)
    if isinstance(raw,dict):
        return dict(raw)
    try:
        value=json.loads(raw or "{}")
    except (TypeError,ValueError,json.JSONDecodeError):
        return {}
    return value if isinstance(value,dict) else {}


def aggregate_creative_family_evidence(rows) -> dict:
    buckets=defaultdict(lambda:{
        "views":0,
        "contacts":0,
        "items":set(),
        "days":set(),
        "observations":0,
    })
    for row in rows or []:
        payload=_payload(row)
        versions=payload.get("optimization_versions") or []
        if not isinstance(versions,list):
            continue
        for version in versions:
            if not isinstance(version,dict):
                continue
            observations=version.get("observations") or []
            if not isinstance(observations,list):
                continue
            for obs in observations:
                if not isinstance(obs,dict):
                    continue
                evidence=obs.get("creative_evidence") if isinstance(obs.get("creative_evidence"),dict) else {}
                family=str(evidence.get("banner_family") or "").strip()
                if family not in V7_FAMILIES:
                    continue
                try:
                    views=max(0,int(obs.get("views") or 0))
                except (TypeError,ValueError):
                    views=0
                try:
                    contacts=max(0,int(obs.get("contacts") or 0))
                except (TypeError,ValueError):
                    contacts=0
                bucket=buckets[family]
                bucket["views"]+=views
                bucket["contacts"]+=contacts
                bucket["observations"]+=1
                item_id=getattr(row,"id",None)
                if item_id is not None:
                    try: bucket["items"].add(int(item_id))
                    except (TypeError,ValueError): pass
                day=str(obs.get("snapshot_date") or "").strip()
                if day: bucket["days"].add(day)

    evidence={}
    for family in V7_FAMILIES:
        b=buckets[family]
        views=int(b["views"])
        contacts=int(b["contacts"])
        evidence[family]={
            "views":views,
            "contacts":contacts,
            "conversion":round(contacts/views*100.0,2) if views else 0.0,
            "items":len(b["items"]),
            "days":len(b["days"]),
            "observations":int(b["observations"]),
            "eligible":bool(views>=30 and contacts>=3),
        }
    return evidence


def recommend_creative_family_distribution(evidence: dict | None) -> dict:
    data={family:dict((evidence or {}).get(family) or {}) for family in V7_FAMILIES}
    eligible=[
        (family,data[family])
        for family in V7_FAMILIES
        if bool(data[family].get("eligible"))
    ]
    eligible.sort(
        key=lambda pair:(
            -float(pair[1].get("conversion") or 0),
            -int(pair[1].get("contacts") or 0),
            -int(pair[1].get("views") or 0),
            pair[0],
        )
    )

    if not eligible:
        weights={family:25 for family in V7_FAMILIES}
        return {
            "status":"collecting",
            "confidence":"low",
            "winner":None,
            "runner_up":None,
            "weights_pct":weights,
            "hard_cap_pct":60,
            "reason":"Недостаточно V7 evidence; сохранить равномерное исследование 4 creative family.",
        }

    if len(eligible)==1:
        winner=eligible[0][0]
        weights={family:(40 if family==winner else 20) for family in V7_FAMILIES}
        return {
            "status":"provisional_winner",
            "confidence":"medium",
            "winner":winner,
            "runner_up":None,
            "weights_pct":weights,
            "hard_cap_pct":60,
            "reason":"Есть один подтверждённый лидер; усилить его умеренно и сохранить 60% exploration.",
        }

    winner=eligible[0][0]
    runner=eligible[1][0]
    weights={family:15 for family in V7_FAMILIES}
    weights[winner]=45
    weights[runner]=25
    return {
        "status":"evidence_weighted",
        "confidence":"medium",
        "winner":winner,
        "runner_up":runner,
        "weights_pct":weights,
        "hard_cap_pct":60,
        "reason":"Два family имеют достаточную выборку; 70% exploit / 30% exploration без monoculture.",
    }


def creative_family_memory(rows) -> dict:
    evidence=aggregate_creative_family_evidence(rows)
    plan=recommend_creative_family_distribution(evidence)
    return {
        "creative_memory_contract":1,
        "families":evidence,
        "plan":plan,
        "total_views":sum(int(x.get("views") or 0) for x in evidence.values()),
        "total_contacts":sum(int(x.get("contacts") or 0) for x in evidence.values()),
        "families_with_evidence":sum(1 for x in evidence.values() if int(x.get("observations") or 0)>0),
    }
