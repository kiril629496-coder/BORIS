#!/usr/bin/env bash
# COORDINATED_CONVERGENCE_READ_ONLY_V2
# Compatibility entrypoint. Certification must never compete with canonical deploy.
# All convergence/restarts belong to boris-deploy.service; this script only observes.
set -euo pipefail
ROOT=/root/BORIS/backend
export BORIS_POLICY_SELF_HEAL=0
export BORIS_CERT_MAX_CYCLES="${BORIS_CERT_MAX_CYCLES:-13}"
export BORIS_CERT_INTERVAL="${BORIS_CERT_INTERVAL:-300}"
export BORIS_CERT_REQUIRED_STREAK="${BORIS_CERT_REQUIRED_STREAK:-3}"
exec "$ROOT/venv/bin/python" "$ROOT/scripts/production_convergence_certifier_v2.py"
