# -*- coding: utf-8 -*-
"""Сборка персонального анализа. Чистая функция: данные на входе, структура на выходе.

Никаких вызовов модели, никаких запросов в базу. Всё берётся из form_data
и из Базы знаний через публичные методы app/niches.py.
"""

SRC_PRODUCT = "product"      # факт о самом BORIS, не о рынке
SRC_USER = "user"            # то, что ввёл пользователь


def _cap_bucket(cap):
    """Куда отнести возможность: сейчас, после подключения Avito, за тариф."""
    needs = cap.get("needs") or {}
    if needs.get("license"):
        return "paid"
    if "avito" in (needs.get("all") or []) or "avito" in (needs.get("any") or []):
        return "after_connection"
    return "now"


def _cap_item(key, cap):
    return {
        "key": key,
        "title": cap.get("label"),
        "description": cap.get("description"),
        "status": cap.get("status"),
        "limits": (cap.get("limits") or {}).get("text"),
        "beta_limits": cap.get("beta_limits"),
        "soon_reason": cap.get("soon_reason"),
        "requirements": _requirements_text(cap.get("needs") or {}),
        "source": SRC_PRODUCT,
    }


def _requirements_text(needs):
    out = []
    for key in (needs.get("all") or []):
        out.append({"kind": "connection", "key": key})
    for key in (needs.get("any") or []):
        out.append({"kind": "connection_any", "key": key})
    for key in (needs.get("license") or []):
        out.append({"kind": "license", "key": key})
    for key in (needs.get("materials") or []):
        out.append({"kind": "material", "key": key})
    return out


def build(form_data, nz, resolution):
    """form_data — ответы мастера, nz — модуль niches, resolution — NicheResolution."""
    form = form_data or {}
    channels = list(form.get("channels") or [])

    profile = {
        "company_name": form.get("company_name"),
        "company_niche": form.get("company_niche"),
        "city": form.get("city"),
        "website": form.get("website"),
        "phone": form.get("phone"),
        "channels": [{"key": c, "title": (nz.common("channels", c) or {}).get("label", c),
                      "source": SRC_USER} for c in channels],
    }

    out = {"status": resolution.status, "profile": profile, "niche": None,
           "buyer_questions": [], "problems": [],
           "capabilities_now": [], "capabilities_after_connection": [], "capabilities_paid": [],
           "materials_missing": [], "plan": [], "automatic_after_avito": [],
           "channels_compare": {"have": [], "can_add_later": [], "not_required": []},
           "candidates": [], "fallback": None}

    if resolution.status == "ambiguous":
        for slug in resolution.candidates:
            n = nz.get(slug) or {}
            out["candidates"].append({"slug": slug, "name": n.get("name"),
                                      "summary": n.get("summary")})
        return out

    caps_source = None
    niche = None
    if resolution.status == "matched":
        niche = nz.for_module(resolution.slug, "onboarding") or {}
        caps_source = niche.get("capabilities") or []
    else:
        # ниша не определилась: показываем и базовые возможности, и то,
        # что появится после подключения Avito — этого требует ТЗ по фолбэку
        caps_source = [k for k, c in (nz.common("capabilities") or {}).items()
                       if _cap_bucket(c) in ("now", "after_connection")
                       and c.get("status") in ("works", "beta")]

    for key in caps_source:
        cap = nz.common("capabilities", key)
        if not cap:
            continue
        item = _cap_item(key, cap)
        bucket = _cap_bucket(cap)
        if bucket == "now" and cap.get("status") == "soon":
            continue
        out["capabilities_" + bucket].append(item)
        if bucket == "after_connection":
            out["automatic_after_avito"].append(item)

    if resolution.status == "not_found":
        out["fallback"] = {
            "text": ("По вашей нише у BORIS пока недостаточно проверенных данных. "
                     "После подключения сайта, объявлений или переписок я изучу ваш бизнес "
                     "и подготовлю точные рекомендации."),
            "source": SRC_PRODUCT,
        }
        out["materials_missing"] = _materials(nz, None)
        return out

    out["niche"] = {"slug": resolution.slug, "name": niche.get("name"),
                    "summary": niche.get("summary"), "source": "confirmed"}

    for q in (niche.get("buyer_questions") or []):
        conf = q.get("confidence")
        if conf == "confirmed" or conf == "cross_client":
            title = "Покупатели в этой нише часто уточняют: " + q.get("text", "")
        else:
            title = "По имеющимся данным покупатели могут уточнять: " + q.get("text", "")
        out["buyer_questions"].append({"title": title, "source": conf})

    for key in (niche.get("problems") or []):
        prob = nz.common("problems", key)
        if not prob:
            continue
        out["problems"].append({
            "key": key,
            "title": prob.get("label"),
            "question": "Что из этого похоже на вашу ситуацию?",
            "as_question": True,
            "source": "hypothesis",
        })

    out["channels_compare"] = _channels(nz, niche, channels)
    out["materials_missing"] = _materials(nz, niche)
    out["plan"] = _plan(nz, niche)
    return out


def _channels(nz, niche, chosen):
    have, later, not_required = [], [], []
    wanted = {c["key"]: c for c in (niche.get("connect") or [])} if niche else {}
    seen = set()
    for key, item in wanted.items():
        title = (nz.common("channels", key) or {}).get("label", key)
        row = {"key": key, "title": title, "required": bool(item.get("required")),
               "source": SRC_PRODUCT}
        seen.add(key)
        if key in chosen:
            have.append(row)
        else:
            later.append(row)
    for key in chosen:
        if key in seen:
            continue
        title = (nz.common("channels", key) or {}).get("label", key)
        have.append({"key": key, "title": title, "required": False, "source": SRC_USER})
    for key, ch in (nz.common("channels") or {}).items():
        if key in seen or key in chosen or key == "other":
            continue
        not_required.append({"key": key, "title": ch.get("label"), "required": False,
                             "source": SRC_PRODUCT})
    return {"have": have, "can_add_later": later, "not_required": not_required}


def _materials(nz, niche):
    keys = (niche or {}).get("materials_needed") or list((nz.common("materials") or {}).keys())
    out = []
    for key in keys:
        m = nz.common("materials", key)
        if not m:
            continue
        out.append({"key": key, "title": m.get("label"), "description": m.get("description"),
                    "asked": False,
                    "source": SRC_PRODUCT})
    return out


_ORDER = {"works": 0, "beta": 1, "soon": 2}


def _plan(nz, niche):
    """3-5 действий из целей и проблем ниши. Только существующие сценарии."""
    keys, why = [], {}
    for goal_key in (niche.get("goals") or []):
        goal = nz.common("goals", goal_key) or {}
        for s in goal.get("scenarios", []):
            if s not in keys:
                keys.append(s)
            why.setdefault(s, []).append({"kind": "goal", "key": goal_key,
                                          "title": goal.get("label")})
    for prob_key in (niche.get("problems") or []):
        prob = nz.common("problems", prob_key) or {}
        for s in prob.get("scenarios", []):
            if s not in keys:
                keys.append(s)
            why.setdefault(s, []).append({"kind": "problem", "key": prob_key,
                                          "title": prob.get("label")})

    rows = []
    for key in keys:
        sc = nz.common("scenarios", key)
        if not sc:
            continue
        needs = sc.get("needs") or {}
        blocked = bool(needs.get("all") or needs.get("license"))
        rows.append({
            "key": key,
            "title": sc.get("label"),
            "description": sc.get("description"),
            "status": sc.get("status"),
            "requirements": _requirements_text(needs),
            "result": _result_for(nz, key),
            "time": None,
            "why": why.get(key, []),
            "source": SRC_PRODUCT,
            "_sort": (_ORDER.get(sc.get("status"), 3), 1 if blocked else 0),
        })

    rows.sort(key=lambda r: r["_sort"])
    for i, row in enumerate(rows[:5], start=1):
        row["order"] = i
        row.pop("_sort", None)
    return rows[:5]


def _result_for(nz, scenario_key):
    for key, res in (nz.common("business_results") or {}).items():
        if scenario_key in (res.get("scenarios") or []):
            return {"key": key, "title": res.get("label")}
    return None
