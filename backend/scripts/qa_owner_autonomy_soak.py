#!/root/BORIS/backend/venv/bin/python
# -*- coding: utf-8 -*-
"""Read-only production soak for owner reporting + autonomous development runtime.

No Telegram sends, no Avito mutations, no paid calls. The normal dispatcher is
responsible for healing; this script only proves that the healed state stays true.
"""
import argparse
import datetime as dt
import json
import subprocess
import time
import sys
from pathlib import Path

# Allow direct/systemd execution from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.inbox_daily import MSK, render_owner_daily_report
from app.ext_api import agents, aiprov, clients, factory, master, queuefix, report, sched, watchdog

BANNED = (
    "CLIENT BUSY NOW", "FALSE GREEN", "ACTIVE ORDERS PER TASK",
    "OWNER ACTION REQUIRED", "REGISTERED WORKERS", "safety gate FAIL",
    "Telegram sales cycle DEGRADED", "rc=", "результата пока нет около",
    "нового подтверждённого результата нет", "snapshot'ов", "лид(а)", "диалог(а)",
)
CORE_SERVICES = (
    "boris-dispatcher.service", "boris-watchdog.service", "boris-backend.service",
    "boris-backend-replica.service", "boris-background-worker.service",
    "boris-frontend.service",
)
TRACKED = {"CLIENT_ANTON", "CLIENT_VASILY", "CLIENT_PLANETA_MEBELI", "CLIENT_ANDREY_PENZA"}


def unit_active(unit):
    p = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                       text=True, timeout=8)
    return (p.stdout or "").strip() == "active"


def one_cycle(index):
    failures = []
    service_state = {u: unit_active(u) for u in CORE_SERVICES}
    failures.extend("service_inactive:" + u for u, ok in service_state.items() if not ok)

    caps = sched.limits()
    f = factory.workers()
    q = queuefix.plan()
    cov = master.coverage()["counts"]
    truth = report.business_truth(2, fresh=True)
    owner = watchdog.snapshot_report()
    # Production daily reporting is a completed-day report. Asking for
    # "today" before today's snapshot exists fabricates a QA failure.
    daily = render_owner_daily_report()

    if int(q.get("supersede") or 0) != 0:
        failures.append("queue_duplicates:%s" % q.get("supersede"))
    if not cov.get("pass"):
        failures.append("master_coverage:%s/%s" % (cov.get("mapped"), cov.get("total")))
    free_dev = aiprov.development_candidates()
    if free_dev and int(caps.get("MAX_EXECUTOR_WORKERS") or 0) > 0 and int(f.get("active") or 0) < 1:
        failures.append("no_active_executor_with_capacity")
    if not free_dev and int(f.get("active") or 0) != 0:
        failures.append("executor_running_without_free_provider")
    if int(f.get("active") or 0) != len(f.get("active_units") or []):
        failures.append("factory_active_count_mismatch")

    tracked = {r.get("key"): r for r in truth.get("clients") or [] if r.get("key") in TRACKED}
    for key in sorted(TRACKED):
        row = tracked.get(key)
        if not row:
            failures.append("missing_business_truth:" + key)
            continue
        if not row.get("measurable"):
            failures.append("not_measurable:" + key)
        if not row.get("overall_done"):
            failures.append("business_goal_not_done:%s:%s/%s" %
                            (key, row.get("current"), row.get("expected_count")))

    owner_text = owner.get("text") or ""
    daily_text = daily.get("text") or ""
    for bad in BANNED:
        if bad.lower() in owner_text.lower():
            failures.append("owner_jargon:" + bad)
        if bad.lower() in daily_text.lower():
            failures.append("daily_jargon:" + bad)
    if "Цель:" not in owner_text or "Факт сейчас:" not in owner_text:
        failures.append("owner_missing_goal_fact")
    if "Коротко:" not in daily_text or "Срез данных:" not in daily_text:
        failures.append("daily_missing_summary")

    payload = {
        "cycle": index,
        "at": dt.datetime.now(tz=MSK).isoformat(),
        "ok": not failures,
        "failures": failures,
        "services": service_state,
        "executors_active": f.get("active"),
        "executor_units": f.get("active_units"),
        "executor_cap": caps.get("MAX_EXECUTOR_WORKERS"),
        "queue_duplicates": q.get("supersede"),
        "coverage": "%s/%s" % (cov.get("mapped"), cov.get("total")),
        "watchdog": owner.get("system"),
        "tracked": {k: {"current": v.get("current"), "target": v.get("expected_count"),
                          "feed": v.get("feed_count"), "live": v.get("live_count"),
                          "done": v.get("overall_done")} for k, v in tracked.items()},
    }
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=45)
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    deadline = time.time() + max(1, args.minutes) * 60
    cycle = 0
    all_failures = []
    while True:
        cycle += 1
        try:
            failures = one_cycle(cycle)
        except Exception as exc:
            failures = ["cycle_exception:%s:%s" % (type(exc).__name__, str(exc)[:160])]
            print(json.dumps({"cycle": cycle, "ok": False, "failures": failures}, ensure_ascii=False), flush=True)
        all_failures.extend(failures)
        if time.time() >= deadline:
            break
        time.sleep(max(10, args.interval))
    summary = {"status": "PASS" if not all_failures else "FAIL",
               "cycles": cycle, "failure_count": len(all_failures),
               "failures": all_failures[-30:]}
    print("OWNER_AUTONOMY_SOAK=" + summary["status"], flush=True)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if not all_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
