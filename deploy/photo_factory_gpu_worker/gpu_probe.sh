#!/usr/bin/env bash
set -Eeuo pipefail

echo
echo "======================================================================"
echo " BORIS PHOTO FACTORY — GPU HOST PROBE"
echo "======================================================================"

WHO="$(id -un)"
HOST="$(hostname)"

echo "host=$HOST"
echo "user=$WHO"

command -v nvidia-smi >/dev/null 2>&1 || {
    echo "GPU_PROBE=FAIL"
    echo "reason=nvidia-smi_missing"
    exit 101
}

echo
echo "--- GPU ---"
nvidia-smi

GPU_COUNT="$(
nvidia-smi \
  --query-gpu=name \
  --format=csv,noheader \
  | wc -l \
  | tr -d ' '
)"

VRAM_MB="$(
nvidia-smi \
  --query-gpu=memory.total \
  --format=csv,noheader,nounits \
  | head -1 \
  | tr -d ' '
)"

DRIVER="$(
nvidia-smi \
  --query-gpu=driver_version \
  --format=csv,noheader \
  | head -1 \
  | tr -d ' '
)"

echo
echo "gpu_count=$GPU_COUNT"
echo "vram_mb=$VRAM_MB"
echo "driver=$DRIVER"

[[ "$VRAM_MB" =~ ^[0-9]+$ ]] || {
    echo "GPU_PROBE=FAIL"
    echo "reason=cannot_read_vram"
    exit 102
}

FREE_ROOT_GB="$(
df -Pk / \
  | awk 'NR==2 {printf "%.0f", $4/1024/1024}'
)"

echo "free_root_gb=$FREE_ROOT_GB"

command -v python3 >/dev/null 2>&1 || {
    echo "GPU_PROBE=FAIL"
    echo "reason=python3_missing"
    exit 103
}

python3 --version

echo
echo "======================================================================"
echo " CAPACITY CLASS"
echo "======================================================================"

if (( VRAM_MB >= 47000 )); then
    echo "GPU_PROFILE=FULL"
    echo "QWEN_STANDARD_PROFILE=LIKELY"
elif (( VRAM_MB >= 24000 )); then
    echo "GPU_PROFILE=24GB"
    echo "QWEN_STANDARD_PROFILE=CONDITIONAL"
    echo "NOTE=may require CPU offload / memory optimization"
else
    echo "GPU_PROFILE=LOW_VRAM"
    echo "QWEN_STANDARD_PROFILE=NO"
fi

if (( FREE_ROOT_GB >= 90 )); then
    echo "DISK_PROFILE=PASS"
else
    echo "DISK_PROFILE=FAIL"
fi

echo
echo "GPU_PROBE=PASS"
