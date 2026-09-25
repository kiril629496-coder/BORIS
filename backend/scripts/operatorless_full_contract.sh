#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/root/BORIS/backend${PYTHONPATH:+:$PYTHONPATH}
cd /root/BORIS/backend

echo "OPERATORLESS_FULL_CONTRACT_START=$(date -u +%FT%TZ)"

echo '=== GIT TRANSPORT MUST STAY OFF ==='
[ "$(systemctl is-enabled boris-gitdeploy.service 2>/dev/null || true)" = disabled ]
[ "$(systemctl is-active boris-gitdeploy.service 2>/dev/null || true)" = inactive ]
echo GIT_TRANSPORT=PASS

echo '=== CORE SERVICES ==='
for s in boris-backend boris-backend-replica boris-background-worker boris-deploy boris-crm-sync boris-telephony-runtime boris-frontend; do
  st="$(systemctl is-active "$s.service" 2>/dev/null || true)"
  echo "$s=$st"
  [ "$st" = active ]
done

echo '=== PRODUCTION CONTRACT ==='
contract="$(./venv/bin/python production_contract_runner.py)"
echo "$contract"
echo "$contract" | ./venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="pass" and d.get("passed_accounts")==d.get("accounts")==19 and not d.get("failures")'

echo '=== AVITO VISUAL CONTRACT ==='
visual="$(./venv/bin/python scripts/avito_visual_contract_regression.py)"
echo "$visual"
echo "$visual" | ./venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="PASS" and not d.get("failures")'

echo '=== CAMPAIGN LAUNCHER REGRESSION ==='
if [ -f scripts/campaign_launcher_regression.py ]; then
  ./venv/bin/python scripts/campaign_launcher_regression.py
fi

echo '=== CAMPAIGN MEDIA QUALITY REGRESSION ==='
if [ -f scripts/campaign_media_quality_regression.py ]; then
  ./venv/bin/python scripts/campaign_media_quality_regression.py
fi

echo '=== PYTHON SYNTAX CRITICAL ==='
./venv/bin/python -m py_compile app/api/avito.py app/api/cpx_advisor.py app/api/campaigns.py app/services/autonomy.py app/services/avito_premium_compositor.py app/api/reliability.py production_contract_runner.py

echo "OPERATORLESS_FULL_CONTRACT=PASS"
echo "OPERATORLESS_FULL_CONTRACT_END=$(date -u +%FT%TZ)"
