"""Воркер фоновых задач. Cron: * * * * *.

Также страхует canonical email_queue_runner: при активной owner-outreach
кампании контролирует systemd timer/heartbeat без участия владельца.
Cron для email_queue_runner не создаётся и не восстанавливается.
"""
from app.services.jobs import run_once


def _email_worker_guard():
    try:
        from app.services.email_worker_guard import ensure
        result = ensure()
        if result.get("status") == "degraded" or result.get("timer_repaired") or result.get("service_triggered"):
            print("[jobs_runner] email_worker_guard:", result, flush=True)
    except Exception as exc:
        print("[jobs_runner] email_worker_guard error: %s" % type(exc).__name__, flush=True)


if __name__ == "__main__":
    _email_worker_guard()
    n = run_once()
    if n:
        print("[jobs_runner] обработано задач: %d" % n, flush=True)
