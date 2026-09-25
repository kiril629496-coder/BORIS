#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/root/BORIS"
BACKEND="$ROOT/backend"
PY="$BACKEND/venv/bin/python"

INSPECT="$ROOT/deploy/photo_factory_free_notebook/inspect_first_real.py"

echo
echo "======================================================================"
echo " BORIS PHOTO FACTORY — LIVE FIRST REAL MONITOR"
echo "======================================================================"
echo
echo "Ctrl+C only stops this monitor."
echo "It does NOT stop BORIS or notebook worker."
echo

LAST=""

while true; do

    STATUS="$(
        curl -sS \
            --max-time 5 \
            http://127.0.0.1:8000/api/photo-factory-v31/worker-status \
            2>/dev/null || echo '{}'
    )"

    SUMMARY="$(
        "$PY" - "$STATUS" <<'PY'
import json
import sys

try:
    d = json.loads(
        sys.argv[1]
    )
except Exception:
    d = {}

worker = d.get("worker") or {}

print(
    "ready=%s provider=%s reason=%s worker=%s gpu=%s state=%s"
    % (
        d.get("generation_ready"),
        d.get("provider"),
        d.get("reason"),
        worker.get("worker_id"),
        worker.get("gpu_name"),
        worker.get("state"),
    )
)
PY
    )"

    IMAGE_COUNT="$(
        find \
            "$BACKEND/images/photo_factory" \
            -type f \
            \( \
                -iname '*.png' \
                -o -iname '*.jpg' \
                -o -iname '*.jpeg' \
                -o -iname '*.webp' \
            \) \
            2>/dev/null |
            wc -l |
            tr -d ' '
    )"

    NOW="$(date '+%H:%M:%S')"

    LINE="$NOW $SUMMARY images=$IMAGE_COUNT"

    if [[ "$LINE" != "$LAST" ]]; then
        echo "$LINE"
        LAST="$LINE"
    fi

    sleep 5

done
