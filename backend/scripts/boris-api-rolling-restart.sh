#!/usr/bin/env bash
set -euo pipefail
exec /root/BORIS/backend/rolling_restart_backend.sh "$@"
