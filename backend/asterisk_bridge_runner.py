#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import sys
import time
from urllib.parse import urlencode

import psycopg2
import websockets

sys.path.insert(0, "/root/BORIS/backend")

from app.db.session import DATABASE_URL
from app.services.asterisk_gateway import (
    ARI_APP,
    ensure_schema,
    process_ari_event,
    runtime_mark,
)

_LOCK_KEY = 0x424F52415354  # BORAST
_STOP = asyncio.Event()


def _credentials() -> tuple[str, str]:
    user = str(os.getenv("BORIS_ASTERISK_ARI_USER") or "").strip()
    password = str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or "").strip()
    if not user or not password:
        raise RuntimeError("asterisk_ari_credentials_missing")
    return user, password


def _ws_url() -> str:
    base = str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").strip().rstrip("/")
    if base.startswith("https://"):
        ws = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws = "ws://" + base[len("http://"):]
    else:
        raise RuntimeError("asterisk_ari_url_invalid")
    return ws + "/events?" + urlencode({"app": ARI_APP, "subscribeAll": "true"})


def _lock_connection():
    conn = psycopg2.connect(
        DATABASE_URL,
        application_name="boris-asterisk-bridge-lock",
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,))
    if not bool(cur.fetchone()[0]):
        cur.close()
        conn.close()
        return None, None
    return conn, cur


async def _run() -> None:
    ensure_schema()
    lock_conn, lock_cur = _lock_connection()
    if not lock_conn:
        print("BORIS_ASTERISK_BRIDGE=STANDBY singleton_lock_busy", flush=True)
        return
    print(f"BORIS_ASTERISK_BRIDGE_LOCK=ACQUIRED pid={os.getpid()}", flush=True)
    backoff = 1.0
    try:
        while not _STOP.is_set():
            try:
                user, password = _credentials()
                token = base64.b64encode(f"{user}:{password}".encode()).decode()
                url = _ws_url()
                async with websockets.connect(
                    url,
                    additional_headers={"Authorization": f"Basic {token}", "User-Agent": "BORIS-Asterisk-Bridge/1.0"},
                    open_timeout=5,
                    close_timeout=3,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2 * 1024 * 1024,
                ) as ws:
                    runtime_mark(state="connected", websocket_connected=True, error=None,
                                 metadata={"app": ARI_APP})
                    print("BORIS_ASTERISK_BRIDGE=CONNECTED", flush=True)
                    backoff = 1.0
                    while not _STOP.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            # websockets protocol ping/pong checks liveness. Mark the
                            # control plane alive without creating fake call evidence.
                            runtime_mark(state="connected", websocket_connected=True, error=None,
                                         metadata={"idle_heartbeat": True})
                            continue
                        if raw is None:
                            raise ConnectionError("ari_websocket_closed")
                        try:
                            event = json.loads(raw)
                        except Exception:
                            runtime_mark(state="degraded", websocket_connected=True,
                                         error="ari_event_json_invalid")
                            continue
                        if not isinstance(event, dict):
                            continue
                        result = await asyncio.to_thread(process_ari_event, event)
                        if result.get("status") == "error":
                            print("asterisk_event_error", {
                                "type": str(event.get("type") or "")[:80],
                                "error_code": result.get("error_code"),
                            }, flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime_mark(state="disconnected", websocket_connected=False,
                             error=type(exc).__name__, metadata={"retry_in_sec": backoff})
                print("BORIS_ASTERISK_BRIDGE=RECONNECT", type(exc).__name__, f"in={backoff:.1f}s", flush=True)
                try:
                    await asyncio.wait_for(_STOP.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2.0, 30.0)
    finally:
        runtime_mark(state="stopped", websocket_connected=False, error=None)
        try:
            lock_cur.close()
            lock_conn.close()
        except Exception:
            pass


def _signal(*_):
    _STOP.set()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
