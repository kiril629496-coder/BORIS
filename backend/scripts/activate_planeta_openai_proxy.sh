#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/BORIS/backend"
ENV_FILE="$ROOT/.env"
PY="$ROOT/venv/bin/python"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
BACKUP="$ROOT/.env.bak.planeta_proxy_$STAMP"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0"
  exit 2
fi

cp -a "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP"

read -r -p "NEW PROXY LOGIN: " NEW_PROXY_USER
read -r -s -p "NEW PROXY PASSWORD: " NEW_PROXY_PASS
echo
if [ -z "$NEW_PROXY_USER" ] || [ -z "$NEW_PROXY_PASS" ]; then
  echo "Empty credentials; aborting."
  exit 2
fi
export NEW_PROXY_USER NEW_PROXY_PASS

"$PY" - <<'PY'
import os
from pathlib import Path

path = Path("/root/BORIS/backend/.env")
user = os.environ["NEW_PROXY_USER"]
password = os.environ["NEW_PROXY_PASS"]
updates = {
    "PROXY_INTL_HOSTS": "95.134.224.3:10000",
    "PROXY_INTL_USER": user,
    "PROXY_INTL_PASS": password,
    "BORIS_SALES_OPENAI_ENABLED": "1",
}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
tmp = path.with_name(".env.planeta_proxy.tmp")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
unset NEW_PROXY_USER NEW_PROXY_PASS

set +e
"$PY" - <<'PY'
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env", override=True)

import requests
from app.services.sales_ai_router import _openai_default_call
from app.db.session import SessionLocal
from app.services.reliability import circuit_success, _account_circuit_key
from app.ext_api import aiprov
from proxy_pool import get_intl_requests_proxies

ACCOUNT = "planetazayavki_65985"
px = get_intl_requests_proxies()
if not px:
    raise SystemExit("FAIL: proxy config unavailable")

r = requests.get("https://ipinfo.io/json", proxies=px, timeout=20)
if r.status_code != 200:
    raise SystemExit(f"FAIL: proxy geo HTTP {r.status_code}")
geo = r.json() or {}
print("PROXY_EXIT", geo.get("ip"), geo.get("city"), geo.get("country"))
if str(geo.get("country") or "") != "DE":
    raise SystemExit("FAIL: proxy exit is not DE")

import os
key = str(os.getenv("OPENAI_API_KEY") or "").strip()
if not key:
    raise SystemExit("FAIL: OPENAI_API_KEY missing")

probe = requests.post(
    "https://api.openai.com/v1/responses",
    headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Idempotency-Key": "planeta-openai-secure-recovery-v2",
    },
    json={
        "model": "gpt-5-mini",
        "input": "Ответь только словом PASS",
        "max_output_tokens": 256,
    },
    proxies=px,
    timeout=25,
)
print("OPENAI_HTTP", probe.status_code)
if probe.status_code < 200 or probe.status_code >= 300:
    try:
        err = (probe.json() or {}).get("error") or {}
        print("OPENAI_ERROR_TYPE", str(err.get("type") or "")[:100])
        print("OPENAI_ERROR_CODE", str(err.get("code") or "")[:100])
        print("OPENAI_ERROR_MSG", str(err.get("message") or "")[:240])
    except Exception:
        pass
    raise SystemExit("FAIL: OpenAI recovery probe non-2xx")

body = probe.json() or {}
chunks = []
for item in body.get("output") or []:
    if not isinstance(item, dict):
        continue
    for part in item.get("content") or []:
        if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
            val = part.get("text")
            if isinstance(val, dict):
                val = val.get("value")
            if val:
                chunks.append(str(val))
text_out = str(body.get("output_text") or "\n".join(chunks)).strip()
if not text_out:
    print("OPENAI_RESPONSE_STATUS", body.get("status"))
    print("OPENAI_INCOMPLETE_DETAILS", body.get("incomplete_details"))
    print("OPENAI_OUTPUT_TYPES", [
        str(x.get("type") or "") for x in (body.get("output") or []) if isinstance(x, dict)
    ])
    raise SystemExit("FAIL: OpenAI returned empty visible text")
print("OPENAI_PASS", text_out[:40], bool(probe.headers.get("x-request-id")))

# Only real provider success may clear durable provider/circuit state.
aiprov.mark("openai", aiprov.AVAILABLE, "Planeta secure German proxy OpenAI probe PASS")

db = SessionLocal()
try:
    circuit_success(db, "openai.text")
    circuit_success(db, _account_circuit_key("openai.text", ACCOUNT))
    db.commit()
finally:
    db.close()

# Verify the exact production sales/MOP transport after clearing stale circuits.
from app.services.sales_ai_router import _openai_default_call
prod = _openai_default_call(
    account_id=ACCOUNT,
    operation="planeta_openai_production_path_probe",
    prompt="Ответь только словом PASS",
    model="gpt-5-mini",
    max_output_tokens=256,
    idempotency_key="planeta-openai-production-path-v1",
    module="mop",
    timeout=25,
)
prod_text = str((prod or {}).get("text") or "").strip()
if not prod_text:
    raise SystemExit("FAIL: production sales_ai_router returned empty text")
print("PRODUCTION_OPENAI_PASS", prod_text[:40], bool((prod or {}).get("request_id")))
print("PROVIDER_STATE", (aiprov.state("openai") or {}).get("state"))
PY
RC=$?
set -e

if [ "$RC" -ne 0 ]; then
  echo "Validation failed. Restoring previous .env"
  cp -a "$BACKUP" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  exit "$RC"
fi

systemctl restart boris-background-worker.service
systemctl restart boris-dispatcher.service

echo "PLANETA_OPENAI_PROXY_ACTIVATED=PASS"
echo "Backup: $BACKUP"
