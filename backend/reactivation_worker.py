# -*- coding: utf-8 -*-
"""Canonical periodic reactivation tick for all accounts with an active MOP."""
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.messenger import get_manager_balance
from app.reactivation_scan import scan_account
from app.reactivation_runtime import process
from app.reactivation_avito_health import probe_accounts, effective_capability
from app.services.reliability import heartbeat


def main():
    db = SessionLocal()
    stats = {"accounts": 0, "scanned": 0, "created": 0, "scan_errors": 0, "avito_capability": {}}
    try:
        accounts = [r[0] for r in db.execute(text("""
            SELECT a.account_id
              FROM accounts a
              LEFT JOIN reactivation_settings s ON s.account_id=a.account_id
             WHERE a.avito_client_id IS NOT NULL
               AND coalesce(s.enabled,true)=true
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','background','messages','reactivation')
               )
             ORDER BY a.account_id
        """)).fetchall()]
        # Read-only capability probe: no synthetic sends, no AI, no business mutation.
        active_probe_accounts = []
        for _account_id in accounts:
            try:
                if (get_manager_balance(_account_id) or {}).get("active"):
                    active_probe_accounts.append(_account_id)
            except Exception:
                pass
        for _probe in probe_accounts(db, active_probe_accounts):
            # Prefer the effective capability projection: a fresh full-inbox sync
            # is stronger than a small sampled probe and prevents contradictory
            # owner/supervisor status for the same account.
            _effective = effective_capability(db, _probe["account_id"]) or _probe
            stats["avito_capability"][_probe["account_id"]] = {
                "state": _effective.get("state"), "classification": _effective.get("classification"),
                "chat_list": _effective.get("chat_list", _probe.get("chat_list")),
                "history": _effective.get("history", _probe.get("history")),
                "send": _effective.get("send", _probe.get("send")),
                "receipt": _effective.get("receipt", _probe.get("receipt")),
                "self_heal": _effective.get("self_heal", _probe.get("self_heal")),
                "source": _effective.get("source"),
            }

        for account_id in accounts:
            try:
                if not (get_manager_balance(account_id) or {}).get("active"):
                    continue
                stats["accounts"] += 1
                result = scan_account(db, account_id, dry=False)
                stats["scanned"] += int(result.get("found") or 0)
                stats["created"] += int(result.get("created") or 0)
            except Exception as exc:
                db.rollback()
                stats["scan_errors"] += 1
                print("REACTIVATION_SCAN_ERROR", account_id, repr(exc)[:300], flush=True)
        runtime = process(db, generate_limit=10, send_limit=25)
        details = {**stats, **runtime}
        cap_partial_external = sum(1 for x in stats["avito_capability"].values() if x.get("classification") == "avito_messenger_history_partial_402")
        cap_degraded = sum(1 for x in stats["avito_capability"].values() if x.get("state") == "degraded" and x.get("classification") != "avito_messenger_history_partial_402")
        cap_external = sum(1 for x in stats["avito_capability"].values() if x.get("state") == "waiting_external")
        details["capability_degraded_accounts"] = cap_degraded
        details["capability_partial_external_accounts"] = cap_partial_external
        details["capability_waiting_external_accounts"] = cap_external
        # Scheduler heartbeat describes BORIS worker execution, not provider health.
        # Avito capability degradation is already carried in details and diagnosed
        # separately by the control plane. Mixing it into scheduler state creates a
        # false internal P1 and can hide the precise chat/provider dependency.
        state = "degraded" if int(details.get("scan_errors") or 0) or int(details.get("errors") or 0) else "ok"
        heartbeat("runtime", "reactivation_scheduler", state=state, details=details)
        print("REACTIVATION_TICK", details, flush=True)
    except Exception as exc:
        # REACTIVATION_SUPERVISOR_EXCEPTION_HEARTBEAT_V1: a crashed tick must
        # become observable immediately. Keep the systemd failure semantics by
        # re-raising after the heartbeat; never swallow the business/runtime error.
        try:
            heartbeat("runtime", "reactivation_scheduler", state="degraded", details={**stats, "fatal_error_type": type(exc).__name__})
        finally:
            raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
