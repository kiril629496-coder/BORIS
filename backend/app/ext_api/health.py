# -*- coding: utf-8 -*-
"""Состояние production. Только чтение: ни платных, ни изменяющих действий."""
import os, time
from . import db, registry, preflight, discovery

INTEGRATIONS = {
    "avito": ["app.services.avito_client:AvitoClient", "app.api.avito:router",
              "app.services.avito:get_token"],
    "media_provider": ["app.services.media:generate_images", "app.services.images:generate_for_item",
                       "app.api.banners:router"],
    "telegram": ["app.telegram_bot:send_telegram_message", "app.services.telegram:send_report", "app.notifications:send_telegram"],
    "crm": ["app.api.manager:router", "app.services.crm:upsert_lead"],
    "orchestrator": ["app.api.plan_items:execute_plan_item", "app.services.plan_executor:execute_plan_item",
                     "app.services.boris_plan:execute_plan_item"],
}


def _table(name):
    try:
        n = db.one("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name=:t",
                   t=name)["n"]
        if not n:
            return {"ok": False, "detail": "таблицы нет"}
        return {"ok": True, "rows": db.one(f"SELECT COUNT(*) AS n FROM {name}")["n"]}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


def production():
    t0 = time.time()
    out = {"status": "ok", "checks": {}}

    try:
        db.one("SELECT 1 AS ok")
        out["checks"]["database"] = {"ok": True,
                                     "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        out["checks"]["database"] = {"ok": False, "detail": str(e)[:200]}
        out["status"] = "down"
        return out

    out["checks"]["backend"] = {"ok": True, "pid": os.getpid()}

    tables = {t: _table(t) for t in ("accounts", "campaigns", "campaign_items",
                                     "ext_jobs", "ext_audit", "ext_handoffs")}
    out["checks"]["tables"] = tables

    jobs = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) AS n FROM ext_jobs GROUP BY status")}
    stuck = db.one("""SELECT COUNT(*) AS n FROM ext_jobs
                      WHERE status='running' AND started_at < NOW() - INTERVAL '30 minutes'""")["n"]
    fail24 = db.one("""SELECT COUNT(*) AS n FROM ext_jobs
                       WHERE status IN ('failed','blocked')
                         AND created_at > NOW() - INTERVAL '24 hours'""")["n"]
    out["checks"]["job_system"] = {"ok": stuck == 0, "by_status": jobs,
                                   "stuck_over_30min": stuck, "failed_or_blocked_24h": fail24}

    calls24 = db.one("""SELECT COUNT(*) AS n FROM ext_audit
                        WHERE created_at > NOW() - INTERVAL '24 hours'""")["n"]
    keys = db.one("SELECT COUNT(*) AS n FROM ext_api_keys WHERE active=TRUE")["n"]
    out["checks"]["gateway"] = {"ok": True, "calls_24h": calls24, "active_keys": keys}

    ex = {}
    for name, cands in INTEGRATIONS.items():
        fn, target, _rep = discovery.resolve(cands)
        ex[name] = {"ok": fn is not None,
                    "target": target,
                    "detail": None if fn is not None else "штатный модуль не найден в этой сборке"}
    out["checks"]["integrations"] = ex

    ops = capabilities()["operations"]
    out["checks"]["executors"] = {"ok": any(o["available"] for o in ops),
                                  "available": sum(1 for o in ops if o["available"]),
                                  "total": len(ops)}

    if not out["checks"]["job_system"]["ok"]:
        out["status"] = "degraded"
    out["duration_ms"] = int((time.time() - t0) * 1000)
    return out


def capabilities():
    ops = []
    for name in registry.names():
        spec = registry.get(name)
        available, target, report = preflight.executor_state(name)
        gated = spec["cls"] in registry.PHASE1_BLOCKED_CLASSES
        ops.append({
            "name": name,
            "available": bool(available) and not gated,
            "requires_confirmation": bool(spec.get("confirm")),
            "class": spec["cls"],
            "scope": spec["scope"],
            "mode": spec["mode"],
            "summary": spec.get("summary"),
            "executor": target,
            "reason": (None if available and not gated else
                       ("класс операции выключен в этой версии шлюза" if gated
                        else "штатный исполнитель не найден")),
            "candidates": [r["target"] for r in report] if not available else None,
        })
    return {"version": "1.0.0", "phase": "v1 · read/prepare",
            "blocked_classes": sorted(registry.PHASE1_BLOCKED_CLASSES),
            "operations": ops}
