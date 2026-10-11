#!/usr/bin/env bash
set -Eeuo pipefail

[[ "$(hostname)" == 8509713-oh794426* ]] || exit 101
[[ "$(id -un)" == "root" ]] || exit 102
[[ "$(pwd -P)" == "/root/BORIS/backend" ]] || exit 103

set -a
. ./.env
set +a

exec venv/bin/python3 \
    send_hot_lead_once.py \
    --lead 2 \
    --confirm SEND_LEAD_2_ONCE
