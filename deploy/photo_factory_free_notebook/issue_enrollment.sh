#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/root/BORIS"
PY="$ROOT/backend/venv/bin/python"

ENROLL="$ROOT/data/photo_factory/runtime/enrollment"

mkdir -p "$ENROLL"
chmod 700 "$ENROLL"

"$PY" - "$ENROLL" <<'PY'
import hashlib
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])

code = secrets.token_urlsafe(32)

digest = hashlib.sha256(
    code.encode("utf-8")
).hexdigest()

now = time.time()

record = {
    "created_at": datetime.now(
        timezone.utc
    ).isoformat(),
    "created_epoch": now,
    "expires_epoch": now + 900,
    "used": False,
}

path = root / f"{digest}.json"

path.write_text(
    json.dumps(
        record,
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

print()
print("==============================================")
print(" BORIS FREE NOTEBOOK — ONE-TIME ENROLLMENT")
print("==============================================")
print()
print("Valid for: 15 minutes")
print("Single use: YES")
print()
print("BORIS_ENROLL_CODE=" + code)
print()
print("This is NOT the permanent worker token.")
print("After one successful enrollment this code dies.")
print()
PY
