#!/usr/bin/env python3
"""BORIS Ops MCP stdio bridge.

Dependency-free MCP transport for a future Secure MCP Tunnel / local MCP client.
It proxies JSON-RPC messages to the loopback BORIS Ops HTTP MCP endpoint and
adds the private Bearer+HMAC authentication locally. Secrets never travel over
stdio and never need to be embedded in a ChatGPT connector configuration.
"""
import hashlib
import hmac
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path("/etc/boris-ops/agent.env")
ENDPOINT = "http://127.0.0.1:8741/mcp"


def load_env():
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value.strip().strip('"').strip("'")
    return env


def proxy(message, token, secret):
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = hmac.new(
        secret.encode(), (ts + "\n" + nonce + "\n").encode() + body, hashlib.sha256
    ).hexdigest()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "X-Boris-Timestamp": ts,
            "X-Boris-Nonce": nonce,
            "X-Boris-Signature": signature,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=1900) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except Exception:
            detail = {"error": "http_error", "status": exc.code}
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32001, "message": "BORIS Ops HTTP error", "data": detail},
        }
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32002, "message": "BORIS Ops unavailable", "data": str(exc)[:300]},
        }


def main():
    env = load_env()
    token = env.get("BORIS_OPS_TOKEN", "")
    secret = env.get("BORIS_OPS_HMAC_SECRET", "")
    if not token or not secret:
        print("BORIS Ops stdio bridge: missing credentials", file=sys.stderr, flush=True)
        return 78
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except Exception:
            print(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                    separators=(",", ":"),
                ),
                flush=True,
            )
            continue
        response = proxy(message, token, secret)
        # JSON-RPC notifications have no id and must not receive a response.
        if "id" not in message:
            continue
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
