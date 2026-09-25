#!/usr/bin/env python3
"""Supervised production entrypoint for the canonical paid ROP auto-runner.

This wrapper owns only runtime truth: systemd execution + ReliabilityHeartbeat.
Business selection, billing guards, dedupe and AI work stay in rop_auto.py.
"""
from __future__ import annotations

import subprocess
import sys
import time

from app.services.reliability import heartbeat

WORKER_ID = "rop_auto_scheduler"
SCRIPT = "/root/BORIS/backend/rop_auto.py"


def main() -> int:
    started=time.time()
    # Separate inflight liveness from terminal scheduler truth. Reliability
    # heartbeat writes are throttled; using the same key for a fast (<5s) run
    # can suppress the final "ok" update and leave a false perpetual "running".
    heartbeat("runtime",WORKER_ID+"_inflight",state="running",details={"phase":"start"})
    try:
        p=subprocess.run(
            [sys.executable,SCRIPT,"--apply"],
            cwd="/root/BORIS/backend",
            capture_output=True,
            text=True,
            timeout=1500,
        )
    except subprocess.TimeoutExpired as exc:
        duration=round(time.time()-started,3)
        heartbeat(
            "runtime",WORKER_ID,state="degraded",
            details={"phase":"timeout","duration_sec":duration,"timeout_sec":1500},
        )
        print("ROP_AUTO_TIMEOUT",duration,flush=True)
        return 124
    except Exception as exc:
        duration=round(time.time()-started,3)
        heartbeat(
            "runtime",WORKER_ID,state="degraded",
            details={"phase":"exception","duration_sec":duration,"error_type":type(exc).__name__},
        )
        print("ROP_AUTO_EXCEPTION",type(exc).__name__,str(exc)[:500],flush=True)
        return 1

    if p.stdout:
        print(p.stdout.rstrip(),flush=True)
    if p.stderr:
        print(p.stderr.rstrip(),file=sys.stderr,flush=True)
    duration=round(time.time()-started,3)
    state="ok" if p.returncode==0 else "degraded"
    heartbeat(
        "runtime",WORKER_ID,state=state,
        details={
            "phase":"completed" if p.returncode==0 else "failed",
            "rc":int(p.returncode),
            "duration_sec":duration,
            "stdout_tail":(p.stdout or "")[-1200:],
            "stderr_tail":(p.stderr or "")[-800:],
        },
    )
    return int(p.returncode)


if __name__=="__main__":
    raise SystemExit(main())
