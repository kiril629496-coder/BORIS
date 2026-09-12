#!/usr/bin/env python3
import argparse
import json
from app.db.session import SessionLocal
from sqlalchemy import text
from app.services.development_lead_radar import run, OWNER_ID, CAMPAIGN_NAME


def _ready_state():
    db=SessionLocal()
    try:
        row=db.execute(text("""SELECT c.id,
          count(m.id) FILTER (WHERE m.status='ready')::int AS ready,
          count(m.id) FILTER (WHERE m.status='queued')::int AS queued,
          count(m.id) FILTER (WHERE m.status='sent')::int AS sent
          FROM prospect_campaigns c
          LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id
          WHERE c.owner_id=:o AND c.name=:n
          GROUP BY c.id ORDER BY c.id DESC LIMIT 1"""),{'o':OWNER_ID,'n':CAMPAIGN_NAME}).mappings().first()
        return dict(row) if row else {'id':None,'ready':0,'queued':0,'sent':0}
    finally:
        db.close()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--no-discovery',action='store_true')
    ap.add_argument('--target',type=int,default=50)
    ap.add_argument('--refill-only',action='store_true')
    ap.add_argument('--min-ready',type=int,default=20)
    args=ap.parse_args()
    before=_ready_state()
    if args.refill_only and int(before.get('ready') or 0)>=max(1,int(args.min_ready)):
        print(json.dumps({'status':'SKIP_READY_RESERVE_OK','before':before,'min_ready':int(args.min_ready)},ensure_ascii=False,default=str))
        return
    out=run(discover=not args.no_discovery,target=args.target)
    out['before']=before
    out['after']=_ready_state()
    out['mode']='refill_only' if args.refill_only else ('no_discovery' if args.no_discovery else 'full')
    print(json.dumps(out,ensure_ascii=False,default=str))

if __name__=='__main__':
    main()
