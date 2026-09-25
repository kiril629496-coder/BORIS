#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# BORIS QWEN PHOTO WORKER INSTALLER
#
# RUN ONLY ON SEPARATE NVIDIA GPU MACHINE.
###############################################################################

echo
echo "======================================================================"
echo " BORIS — QWEN GPU WORKER INSTALL"
echo "======================================================================"

WHO="$(id -un)"

[[ "$WHO" == "root" ]] || {
    echo "FAIL CLOSED: run as root"
    exit 101
}

command -v nvidia-smi >/dev/null 2>&1 || {
    echo "FAIL CLOSED: nvidia-smi missing"
    exit 102
}

echo
echo "--- NVIDIA GPU ---"
nvidia-smi

GPU_MEM_MB="$(
nvidia-smi \
    --query-gpu=memory.total \
    --format=csv,noheader,nounits \
    | head -1 \
    | tr -d ' '
)"

echo "gpu_memory_mb=$GPU_MEM_MB"

[[ "$GPU_MEM_MB" =~ ^[0-9]+$ ]] || {
    echo "FAIL CLOSED: cannot determine GPU VRAM"
    exit 103
}

# Qwen-Image-Edit-2511 is large.
# Fail closed below 24 GB rather than pretending it will run well.
if (( GPU_MEM_MB < 24000 )); then
    echo "FAIL CLOSED:"
    echo "GPU has ${GPU_MEM_MB}MB VRAM."
    echo "Need >=24GB for this worker configuration."
    echo "For smaller GPU use an offload/quantized profile separately."
    exit 104
fi

command -v python3 >/dev/null 2>&1 || {
    echo "FAIL CLOSED: python3 missing"
    exit 105
}

FREE_GB="$(
df -Pk /opt \
    | awk 'NR==2 {printf "%.0f", $4/1024/1024}'
)"

echo "free_disk_gb=$FREE_GB"

if (( FREE_GB < 90 )); then
    echo "FAIL CLOSED:"
    echo "At least ~90GB free disk required."
    exit 106
fi

ROOT="/opt/boris-photo-worker"
VENV="$ROOT/venv"
APP="$ROOT/app"

mkdir -p "$APP"

python3 -m venv "$VENV"

"$VENV/bin/python" -m pip install \
    --upgrade \
    pip \
    setuptools \
    wheel

# Install PyTorch CUDA build.
# CUDA 12.8 wheels are used where supported.
"$VENV/bin/pip" install \
    torch \
    torchvision \
    --index-url https://download.pytorch.org/whl/cu128

"$VENV/bin/pip" install \
    --upgrade \
    "diffusers>=0.36.0" \
    transformers \
    accelerate \
    safetensors \
    huggingface_hub \
    pillow \
    requests \
    sentencepiece

echo
echo "======================================================================"
echo " CUDA PYTHON QA"
echo "======================================================================"

"$VENV/bin/python" - <<'PY'
import torch

print("torch=", torch.__version__)
print("cuda_available=", torch.cuda.is_available())

assert torch.cuda.is_available(), "CUDA unavailable"

print("gpu=", torch.cuda.get_device_name(0))

props = torch.cuda.get_device_properties(0)

print(
    "vram_gb=",
    round(
        props.total_memory
        / 1024**3,
        2,
    ),
)

from diffusers import QwenImageEditPlusPipeline

print("QWEN_PIPELINE_IMPORT=PASS")
PY

echo
echo "INSTALL=PASS"
echo
echo "Next:"
echo "  copy boris_qwen_worker.py to:"
echo "  /opt/boris-photo-worker/app/boris_qwen_worker.py"
echo
echo "  create /etc/boris-photo-worker.env"
echo "  then start:"
echo
echo "  source /etc/boris-photo-worker.env"
echo "  /opt/boris-photo-worker/venv/bin/python \\"
echo "    /opt/boris-photo-worker/app/boris_qwen_worker.py"
