#!/usr/bin/env bash
set -euo pipefail
cd /root/BORIS/backend
mkdir -p .boris_logs
LOG=".boris_logs/feed_factory_soak_$(date -u +%Y%m%dT%H%M%SZ).log"
echo "SOAK_START $(date -u +%FT%TZ)" | tee -a "$LOG"
for cycle in $(seq 1 7); do
  echo "=== CYCLE $cycle $(date -u +%FT%TZ) ===" | tee -a "$LOG"
  ./venv/bin/python -m py_compile app/api/campaigns.py app/services/jobs.py app/services/category_resolver.py app/services/ai_budget.py app/usage.py
  curl -fsS https://boris-ai.pro/health | tee -a "$LOG"
  systemctl is-active boris-backend.service boris-background-worker.service | tee -a "$LOG"
  ./venv/bin/python - <<'PY' | tee -a "$LOG"
from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.background_job import BackgroundJob
from app.models.category_template import CategoryTemplate
from app.api import campaigns as C
import json
s=SessionLocal(); failures=[]
try:
 c=s.query(Campaign).filter(Campaign.id==225).first(); ff=(json.loads(c.settings_json or '{}').get('feed_factory') or {}); a=ff.get('owner_activation') or {}; snap=a.get('submitted_snapshot') or {}; retry=ff.get('safe_retry_failed_only') or {}
 if not C._ff_publication_locked(ff): failures.append('225_not_locked')
 if snap.get('sha256')!=snap.get('validated_sha256'): failures.append('225_snapshot_sha')
 if ((retry.get('official_xmlcheck') or {}).get('status')!='pass'): failures.append('225_retry_not_validated')
 c2=s.query(Campaign).filter(Campaign.id==144).first(); ff2=(json.loads(c2.settings_json or '{}').get('feed_factory') or {}); a2=ff2.get('owner_activation') or {}; snap2=a2.get('submitted_snapshot') or {}; chk2=ff2.get('official_xml_check') or {}
 if not C._ff_publication_locked(ff2): failures.append('144_not_locked')
 if snap2.get('sha256')!=chk2.get('sha256'): failures.append('144_snapshot_sha')
 tpl=s.query(CategoryTemplate).filter(CategoryTemplate.template_id=='133104').first(); tags={str(x.get('tag')) for x in json.loads(tpl.required_fields or '[]') if isinstance(x,dict)}
 if tags & {'Consultations','Specialty','WorkWithContract'}: failures.append('legacy_template_polluted')
 active=s.query(BackgroundJob).filter(BackgroundJob.status.in_(['queued','running','validating'])).all(); runaway=[]
 for j in active:
  p=j.payload_json or {}; same=int(p.get('_same_continuation_count') or 0); cont=int(p.get('_continuation_count') or 0)
  if same>=24 or cont>=500: runaway.append((j.id,j.kind,j.status,same,cont,p.get('stage')))
 if runaway: failures.append('active_runaway:'+repr(runaway))
 print(json.dumps({'campaign225_status':a.get('status'),'campaign225_published':a.get('published'),'campaign144_status':a2.get('status'),'active_jobs':len(active),'failures':failures},ensure_ascii=False))
 if failures: raise SystemExit(2)
finally:s.close()
PY
  if journalctl -u boris-background-worker.service --since '6 minutes ago' --no-pager | grep -E 'QueuePool limit|job_no_progress_guard|Traceback|WORKER_RUNTIME: dead_threads' >/dev/null; then
    echo "SOAK_RUNTIME_WARNING cycle=$cycle" | tee -a "$LOG"
    journalctl -u boris-background-worker.service --since '6 minutes ago' --no-pager | grep -E 'QueuePool limit|job_no_progress_guard|Traceback|WORKER_RUNTIME: dead_threads' | tail -40 | tee -a "$LOG"
    exit 3
  fi
  echo "CYCLE_PASS=$cycle" | tee -a "$LOG"
  if [ "$cycle" -lt 7 ]; then sleep 300; fi
done
echo "SOAK_PASS $(date -u +%FT%TZ) LOG=$LOG" | tee -a "$LOG"
