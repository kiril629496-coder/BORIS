#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# BORIS PHOTO FACTORY — DEPLOY TO EXTERNAL NVIDIA HOST
#
# Required:
#
#   export GPU_SSH="root@1.2.3.4"
#   export BORIS_BASE_URL="https://your-boris-domain"
#
# Optional:
#
#   export GPU_PORT=22
#
###############################################################################

GPU_SSH="${GPU_SSH:-}"
BORIS_BASE_URL="${BORIS_BASE_URL:-}"
GPU_PORT="${GPU_PORT:-22}"

BORIS_ROOT="/root/BORIS"

TOKEN_FILE="$BORIS_ROOT/secrets/photo_factory_worker_token"
BUNDLE="$BORIS_ROOT/deploy/photo_factory_gpu_worker"

[[ -n "$GPU_SSH" ]] || {
    echo "FAIL CLOSED: GPU_SSH not set"
    exit 101
}

[[ -n "$BORIS_BASE_URL" ]] || {
    echo "FAIL CLOSED: BORIS_BASE_URL not set"
    exit 102
}

[[ -s "$TOKEN_FILE" ]] || {
    echo "FAIL CLOSED: worker token missing"
    exit 103
}

[[ -d "$BUNDLE" ]] || {
    echo "FAIL CLOSED: bundle missing"
    exit 104
}

TOKEN="$(cat "$TOKEN_FILE")"

echo
echo "======================================================================"
echo " BORIS -> GPU DEPLOY"
echo "======================================================================"
echo "gpu=$GPU_SSH"
echo "boris=$BORIS_BASE_URL"
echo "token=[HIDDEN]"

###############################################################################
# 1. GPU PROBE
###############################################################################

scp -P "$GPU_PORT" \
    "$BUNDLE/gpu_probe.sh" \
    "$GPU_SSH:/tmp/boris_gpu_probe.sh"

ssh -p "$GPU_PORT" "$GPU_SSH" \
    'chmod +x /tmp/boris_gpu_probe.sh && /tmp/boris_gpu_probe.sh'

echo "REMOTE_GPU_PROBE=PASS"

###############################################################################
# 2. COPY BUNDLE
###############################################################################

ssh -p "$GPU_PORT" "$GPU_SSH" \
    'mkdir -p /opt/boris-photo-worker/source'

scp -P "$GPU_PORT" \
    "$BUNDLE/boris_qwen_worker.py" \
    "$BUNDLE/install_gpu_worker.sh" \
    "$BUNDLE/boris-photo-worker.service.example" \
    "$GPU_SSH:/opt/boris-photo-worker/source/"

echo "BUNDLE_COPY=PASS"

###############################################################################
# 3. INSTALL DEPENDENCIES/MODEL RUNTIME
###############################################################################

ssh -p "$GPU_PORT" "$GPU_SSH" \
    'chmod +x /opt/boris-photo-worker/source/install_gpu_worker.sh && /opt/boris-photo-worker/source/install_gpu_worker.sh'

echo "GPU_RUNTIME_INSTALL=PASS"

###############################################################################
# 4. INSTALL WORKER CODE
###############################################################################

ssh -p "$GPU_PORT" "$GPU_SSH" \
    'mkdir -p /opt/boris-photo-worker/app &&
     cp /opt/boris-photo-worker/source/boris_qwen_worker.py /opt/boris-photo-worker/app/boris_qwen_worker.py &&
     chmod 755 /opt/boris-photo-worker/app/boris_qwen_worker.py'

###############################################################################
# 5. WRITE REMOTE ENV WITHOUT PRINTING TOKEN
###############################################################################

TMP_ENV="/tmp/boris_photo_worker_env.$$"

umask 077

cat >"$TMP_ENV" <<EOF
BORIS_BASE_URL=$BORIS_BASE_URL
BORIS_WORKER_TOKEN=$TOKEN
BORIS_WORKER_ID=qwen-gpu-1
BORIS_QWEN_MODEL=Qwen/Qwen-Image-Edit-2511
HF_HOME=/opt/boris-photo-worker/hf-cache
BORIS_WORKER_OUTPUT=/opt/boris-photo-worker/output
BORIS_WORKER_POLL_SECONDS=10
BORIS_MAX_REFERENCES=3
BORIS_INFERENCE_STEPS=40
BORIS_TRUE_CFG_SCALE=4.0
BORIS_GUIDANCE_SCALE=1.0
BORIS_HTTP_TIMEOUT=120
BORIS_WORKER_LOG_LEVEL=INFO
EOF

scp -P "$GPU_PORT" \
    "$TMP_ENV" \
    "$GPU_SSH:/etc/boris-photo-worker.env"

rm -f "$TMP_ENV"

ssh -p "$GPU_PORT" "$GPU_SSH" \
    'chmod 600 /etc/boris-photo-worker.env'

echo "REMOTE_ENV=PASS"

###############################################################################
# 6. CONNECTIVITY TEST FROM GPU
###############################################################################

ssh -p "$GPU_PORT" "$GPU_SSH" \
    "set -a;
     . /etc/boris-photo-worker.env;
     set +a;
     python3 - <<'PY'
import json
import os
import urllib.request

base = os.environ['BORIS_BASE_URL'].rstrip('/')

with urllib.request.urlopen(
    base + '/api/photo-factory/health',
    timeout=15,
) as response:
    data = json.load(response)

assert data['service'] == 'boris-photo-factory'
assert data['version'] == '2.0'

print('GPU_TO_BORIS_CONNECTIVITY=PASS')
PY"

###############################################################################
# 7. DO NOT AUTO-ENABLE SYSTEMD YET
#
# First run is foreground so Qwen/model errors are visible.
###############################################################################

echo
echo "======================================================================"
echo " DEPLOYMENT READY"
echo "======================================================================"
echo
echo "Run foreground worker:"
echo
echo "ssh -p $GPU_PORT $GPU_SSH"
echo
echo "set -a"
echo ". /etc/boris-photo-worker.env"
echo "set +a"
echo "/opt/boris-photo-worker/venv/bin/python \\"
echo "  /opt/boris-photo-worker/app/boris_qwen_worker.py"
echo
echo "SYSTEMD_NOT_ENABLED_YET=PASS"
