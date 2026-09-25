#!/usr/bin/env python3
from __future__ import annotations
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client
from app.monitoring.telegram_outbound import resolve_personal_entity,verify_personal_message
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)

def main()->int:
    with engine.begin() as db:
        baseline=db.execute(text("select nullif(value_text,'')::timestamptz from monitor_runtime_state where key='tg_delivery_verification_baseline'")).scalar()
        if not baseline:
            print('TG_DELIVERY_RECONCILE=SKIP no_baseline'); return 0
        initials=db.execute(text("""select id,contact_username,contact_peer_id,outbound_message_id,first_message_draft
          from boris_sales_hot_leads where contacted_at>=:b and outbound_message_id is not null and outbound_verified_at is null
          order by contacted_at limit 20"""),{'b':baseline}).mappings().all()
        follows=db.execute(text("""select id,contact_username,contact_peer_id,followup_message_id
          from boris_sales_hot_leads where followup_sent_at>=:b and followup_message_id is not null and followup_verified_at is null
          order by followup_sent_at limit 20"""),{'b':baseline}).mappings().all()
        replies=db.execute(text("""select r.id,r.hot_lead_id,r.auto_reply_message_id,r.auto_reply_text,l.contact_username,l.contact_peer_id
          from boris_sales_tg_replies r join boris_sales_hot_leads l on l.id=r.hot_lead_id
          where r.auto_replied_at>=:b and r.auto_reply_message_id is not null and r.auto_reply_verified_at is null
          order by r.auto_replied_at limit 20"""),{'b':baseline}).mappings().all()
    total=len(initials)+len(follows)+len(replies)
    if not total:
        print('TG_DELIVERY_RECONCILE=PASS pending=0 verified=0 errors=0'); return 0
    client=build_client(decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),int(os.environ['MONITOR_TG_API_ID']),os.environ['MONITOR_TG_API_HASH'])
    verified=errors=0
    try:
        def entity_for(row):
            u=str(row.get('contact_username') or '').strip().lstrip('@')
            p=row.get('contact_peer_id')
            return resolve_personal_entity(client,u if u else int(p))
        for row in initials:
            try:
                ent=entity_for(row); ok=verify_personal_message(client,ent,row['outbound_message_id'],row.get('first_message_draft'))
                if ok:
                    with engine.begin() as db: db.execute(text("update boris_sales_hot_leads set outbound_verified_at=now(),outbound_error=null where id=:i and outbound_verified_at is null"),{'i':row['id']})
                    verified+=1
            except Exception: errors+=1
        for row in follows:
            try:
                ent=entity_for(row); ok=verify_personal_message(client,ent,row['followup_message_id'])
                if ok:
                    with engine.begin() as db: db.execute(text("update boris_sales_hot_leads set followup_verified_at=now(),followup_error=null where id=:i and followup_verified_at is null"),{'i':row['id']})
                    verified+=1
            except Exception: errors+=1
        for row in replies:
            try:
                ent=entity_for(row); ok=verify_personal_message(client,ent,row['auto_reply_message_id'],row.get('auto_reply_text'))
                if ok:
                    with engine.begin() as db: db.execute(text("update boris_sales_tg_replies set auto_reply_verified_at=now() where id=:i and auto_reply_verified_at is null"),{'i':row['id']})
                    verified+=1
            except Exception: errors+=1
    finally:
        client.disconnect()
    print(f'TG_DELIVERY_RECONCILE=PASS pending={total} verified={verified} errors={errors}')
    return 0
if __name__=='__main__': raise SystemExit(main())
