#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# FIRST REAL PHOTO FACTORY TEST
#
# Usage on BORIS:
#
# /root/BORIS/deploy/photo_factory_gpu_worker/first_real_worker_test.sh \
#   car \
#   /path/front.jpg \
#   /path/side.jpg \
#   /path/rear.jpg
###############################################################################

TYPE="${1:-car}"
shift || true

[[ "$#" -ge 1 ]] || {
    echo "usage: $0 <object_type> <image1> [image2] [image3]"
    exit 101
}

[[ "$#" -le 3 ]] || {
    echo "FAIL CLOSED: first test uses maximum 3 references"
    exit 102
}

ROOT="/root/BORIS"
PY="$ROOT/backend/venv/bin/python"

STAMP="$(date +%Y%m%d_%H%M%S)"
OBJECT="real_photo_test_${STAMP}"

FORM_ARGS=()

for image in "$@"; do
    [[ -f "$image" ]] || {
        echo "FAIL CLOSED: missing image=$image"
        exit 103
    }

    FORM_ARGS+=(
        -F "files=@${image}"
    )
done

echo
echo "======================================================================"
echo " PHOTO FACTORY — FIRST REAL TEST"
echo "======================================================================"
echo "object=$OBJECT"
echo "references=$#"
echo "count=1"

UPLOAD="/tmp/pf_real_upload_${STAMP}.json"

CODE="$(
curl -sS \
    --max-time 60 \
    -o "$UPLOAD" \
    -w '%{http_code}' \
    "${FORM_ARGS[@]}" \
    "http://127.0.0.1:8000/api/photo-factory/objects/${OBJECT}/references"
)"

echo "upload_http=$CODE"

[[ "$CODE" == "200" ]] || {
    cat "$UPLOAD"
    exit 110
}

JOB="/tmp/pf_real_job_${STAMP}.json"

CODE="$(
curl -sS \
    --max-time 30 \
    -o "$JOB" \
    -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -d "{
      \"object_id\":\"$OBJECT\",
      \"object_type\":\"$TYPE\",
      \"count\":1,
      \"preserve_identity\":true,
      \"custom_instruction\":\"maximum photorealism; preserve exact object identity; commercial marketplace photography\"
    }" \
    http://127.0.0.1:8000/api/photo-factory/jobs
)"

echo "job_http=$CODE"

[[ "$CODE" == "200" ]] || {
    cat "$JOB"
    exit 111
}

JOB_ID="$(
"$PY" - "$JOB" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))

print(data["id"])
PY
)"

echo
echo "REAL_JOB_ID=$JOB_ID"
echo "STATUS=queued"
echo
echo "GPU worker will claim this job automatically."
echo
echo "Watch:"
echo "  watch -n 3 \"curl -s http://127.0.0.1:8000/api/photo-factory/jobs/$JOB_ID\""
echo
echo "Gallery:"
echo "  http://127.0.0.1:8000/api/photo-factory/jobs/$JOB_ID/gallery"
