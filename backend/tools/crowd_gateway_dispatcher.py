#!/usr/bin/env python3
from __future__ import annotations

import json
import signal
import time

import crowd_gateway_flow as flow

STOP = False


def _stop(*_args):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

last_fingerprint = None
print(json.dumps({"event":"crowd_dispatcher_started","at":flow.now_iso()}, ensure_ascii=False), flush=True)

while not STOP:
    try:
        out = flow.reconcile()
        fingerprint = json.dumps(out, ensure_ascii=False, sort_keys=True, default=str)
        # Log only state transitions/actual work, not identical busy polls.
        if fingerprint != last_fingerprint:
            print(json.dumps({"event":"crowd_dispatch","at":flow.now_iso(),"result":out}, ensure_ascii=False, default=str), flush=True)
            last_fingerprint = fingerprint
        time.sleep(3)
    except Exception as exc:
        print(json.dumps({"event":"crowd_dispatch_error","at":flow.now_iso(),"error":f"{type(exc).__name__}:{exc}"}, ensure_ascii=False), flush=True)
        time.sleep(5)

print(json.dumps({"event":"crowd_dispatcher_stopped","at":flow.now_iso()}, ensure_ascii=False), flush=True)
