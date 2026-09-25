#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# RUN THIS ON MAIN BORIS SERVER
#
# Usage:
#
#   export GPU_SSH="root@1.2.3.4"
#   export GPU_PORT=22
#   /root/BORIS/deploy/photo_factory_gpu_worker/deploy_external_gpu_from_boris.sh
#
###############################################################################

ROOT="/root/BORIS"

GPU_SSH="${GPU_SSH:-}"
GPU_PORT="${GPU_PORT:-22}"

BORIS_BASE_URL="https://boris-ai.pro"

BUNDLE="$ROOT/deploy/photo_factory_gpu_worker"
TOKEN_FILE="$ROOT/secrets/photo_factory_worker_token"

HOST="$(hostname)"
WHO="$(id -un)"

echo
echo "======================================================================"
echo " BORIS -> EXTERNAL GPU DEPLOY"
echo "======================================================================"

[[ "$HOST" == 8509713-oh794426* ]] || {
    echo "FAIL CLOSED: must run from BORIS server"
    exit 101
}

[[ "$WHO" == "root" ]] || {
    echo "FAIL CLOSED: run as root"
    exit 102
}

[[ -n "$GPU_SSH" ]] || {
    echo "FAIL CLOSED:"
    echo "GPU_SSH is not set."
    echo
    echo "Example:"
    echo "export GPU_SSH='root@203.0.113.10'"
    exit 103
}

[[ -s "$TOKEN_FILE" ]] || {
    echo "FAIL CLOSED: worker token missing"
    exit 104
}

###############################################################################
# Never deploy to main BORIS itself.
###############################################################################

TARGET_HOST="${GPU_SSH#*@}"

case "$TARGET_HOST" in
    193.160.209.44|8509713-oh794426.twc1.net)
        echo "FAIL CLOSED:"
        echo "GPU target points to MAIN BORIS server."
        echo "Use a separate NVIDIA server."
        exit 105
        ;;
esac

echo "gpu_ssh=$GPU_SSH"
echo "gpu_port=$GPU_PORT"
echo "boris_base_url=$BORIS_BASE_URL"
echo "token=[HIDDEN]"

###############################################################################
# SSH connectivity
###############################################################################

ssh \
    -p "$GPU_PORT" \
    -o ConnectTimeout=15 \
    "$GPU_SSH" \
    'echo SSH_GPU=PASS; hostname; id -un'

###############################################################################
# GPU gate
###############################################################################

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    '
set -Eeuo pipefail

command -v nvidia-smi >/dev/null 2>&1 || {
    echo "FAIL CLOSED: target has no nvidia-smi"
    exit 201
}

nvidia-smi

VRAM=$(
  nvidia-smi \
    --query-gpu=memory.total \
    --format=csv,noheader,nounits \
    | head -1 \
    | tr -d " "
)

echo "vram_mb=$VRAM"

[[ "$VRAM" =~ ^[0-9]+$ ]]

if (( VRAM < 24000 )); then
    echo "FAIL CLOSED: GPU VRAM below 24GB"
    exit 202
fi

FREE=$(
  df -Pk / \
    | awk "NR==2 {printf \"%.0f\", \$4/1024/1024}"
)

echo "free_gb=$FREE"

if (( FREE < 90 )); then
    echo "FAIL CLOSED: target disk below 90GB free"
    exit 203
fi

echo "REMOTE_GPU_GATE=PASS"
'

###############################################################################
# Create remote workspace
###############################################################################

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    'mkdir -p /opt/boris-photo-worker/source'

###############################################################################
# Copy worker sources
###############################################################################

scp \
    -P "$GPU_PORT" \
    "$BUNDLE/boris_qwen_worker.py" \
    "$BUNDLE/install_gpu_worker.sh" \
    "$BUNDLE/gpu_probe.sh" \
    "$BUNDLE/boris-photo-worker.service.example" \
    "$GPU_SSH:/opt/boris-photo-worker/source/"

echo "REMOTE_BUNDLE_COPY=PASS"

###############################################################################
# Run probe
###############################################################################

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    '
chmod +x /opt/boris-photo-worker/source/gpu_probe.sh
/opt/boris-photo-worker/source/gpu_probe.sh
'

echo "GPU_PROBE=PASS"

###############################################################################
# Install runtime
###############################################################################

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    '
chmod +x /opt/boris-photo-worker/source/install_gpu_worker.sh
/opt/boris-photo-worker/source/install_gpu_worker.sh
'

echo "GPU_RUNTIME_INSTALL=PASS"

###############################################################################
# Install worker source
###############################################################################

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    '
mkdir -p /opt/boris-photo-worker/app
cp \
  /opt/boris-photo-worker/source/boris_qwen_worker.py \
  /opt/boris-photo-worker/app/boris_qwen_worker.py
chmod 755 \
  /opt/boris-photo-worker/app/boris_qwen_worker.py
'

###############################################################################
# Secure env
###############################################################################

TOKEN="$(cat "$TOKEN_FILE")"

TMP="/tmp/boris_gpu_env.$$"

umask 077

cat >"$TMP" <<EOF
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

scp \
    -P "$GPU_PORT" \
    "$TMP" \
    "$GPU_SSH:/etc/boris-photo-worker.env"

rm -f "$TMP"

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    'chmod 600 /etc/boris-photo-worker.env'

echo "REMOTE_ENV=PASS"

###############################################################################
# GPU -> BORIS health
###############################################################################

ssh \
    -p "$GPU_PORT" \
    "$GPU_SSH" \
    "
curl -sS \
  --max-time 15 \
  '$BORIS_BASE_URL/api/photo-factory/health'
"

echo
echo "GPU_TO_BORIS_HTTP=PASS"

###############################################################################
# Do NOT start worker automatically yet.
# Queue is intentionally empty.
###############################################################################

echo
echo "======================================================================"
echo " EXTERNAL GPU DEPLOY = PASS"
echo "======================================================================"
echo
echo "Worker installed but NOT started yet."
echo
echo "GPU target:"
echo "  $GPU_SSH"
echo
echo "Start after creating FIRST REAL JOB:"
echo
echo "ssh -p $GPU_PORT $GPU_SSH"
echo
echo "set -a"
echo ". /etc/boris-photo-worker.env"
echo "set +a"
echo "/opt/boris-photo-worker/venv/bin/python \\"
echo "  /opt/boris-photo-worker/app/boris_qwen_worker.py"
