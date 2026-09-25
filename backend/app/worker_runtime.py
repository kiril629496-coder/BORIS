"""Dedicated BORIS background runtime. No HTTP listener.

Reuses the authoritative poll loops and background_jobs engine from app.main;
it does not create a second scheduler or queue.
"""
import os
import threading
import time

os.environ["BORIS_RUNTIME_ROLE"] = "worker"

from app.db.base import Base
from app.db.session import engine
from app.services.jobs import recover_interrupted_campaign_jobs
from app.services.reliability import heartbeat
from app.telegram_bot import _telegram_poll_loop
from app.api.messenger import _messenger_poll_loop, _reminder_loop
from app.main import _background_jobs_loop


def main():
    # BORIS_RUNTIME_SCHEMA_MIGRATION_OWNED_V1: startup must never mutate schema.
    recovered = recover_interrupted_campaign_jobs()
    threads = []
    specs = [("telegram_poll", _telegram_poll_loop, ()),
             ("messenger_poll", _messenger_poll_loop, ()),
             ("reminder_poll", _reminder_loop, ())]
    try:
        workers = max(1, min(int(os.environ.get("BORIS_JOB_WORKERS", "8")), 16))
    except Exception:
        workers = 8
    specs += [(f"background_jobs_{slot}", _background_jobs_loop, (slot,)) for slot in range(workers)]
    for name, target, args in specs:
        t = threading.Thread(target=target, args=args, daemon=True, name=name)
        t.start(); threads.append(t)
    print(f"WORKER_RUNTIME: started threads={len(threads)} jobs={workers} recovered={recovered}", flush=True)
    while True:
        alive = sum(1 for t in threads if t.is_alive())
        state = "ok" if alive == len(threads) else "degraded"
        heartbeat("runtime", "background_worker", state=state, details={"alive": alive, "expected": len(threads), "job_workers": workers})
        if alive != len(threads):
            dead = [t.name for t in threads if not t.is_alive()]
            raise RuntimeError("background runtime thread died: " + ",".join(dead))
        time.sleep(15)


if __name__ == "__main__":
    main()
