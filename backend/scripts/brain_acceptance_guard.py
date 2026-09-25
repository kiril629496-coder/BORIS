#!/root/BORIS/backend/venv/bin/python
"""BORIS_BRAIN_ACCEPTANCE_GUARD_V1 — periodic fail-closed acceptance snapshot.

Read-only against business data. Exit non-zero only when mandatory Brain
acceptance is not PASS; owner-policy WAITING dependencies are intentionally not
internal failures.
"""
import json
import sys
sys.path.insert(0,"/root/BORIS/backend")
from pathlib import Path
from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.services.brain_acceptance import build_acceptance_report

OUT=Path('/root/BORIS/backend/data/brain_acceptance_latest.json')
TMP=OUT.with_suffix('.tmp')

def main():
    db=SessionLocal()
    try:
        report=build_acceptance_report(db)
    finally:
        # BRAIN_ACCEPTANCE_READONLY_TX_RELEASE_V1
        try:
            db.rollback()
        finally:
            db.close()
    report['guard_checked_at']=datetime.now(timezone.utc).isoformat()
    TMP.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    TMP.replace(OUT)
    print(json.dumps({
        'status':report.get('BORIS_BRAIN_100_PERCENT'),
        'score_percent':report.get('score_percent'),
        'hard_gates':report.get('hard_gates'),
        'waiting_dependencies':report.get('waiting_dependencies'),
        'business_scenarios':(report.get('checks') or {}).get('BUSINESS_SCENARIOS_EXECUTABLE'),
        'chat_runtime_context':(report.get('checks') or {}).get('CHAT_RUNTIME_CONTEXT'),
    },ensure_ascii=False))
    return 0 if report.get('BORIS_BRAIN_100_PERCENT')=='PASS' else 2

if __name__=='__main__':
    raise SystemExit(main())
