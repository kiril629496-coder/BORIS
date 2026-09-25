#!/usr/bin/env python3
from __future__ import annotations
import os,time
from datetime import datetime,timezone,timedelta
import psycopg2
from dotenv import load_dotenv
load_dotenv('/root/BORIS/backend/.env')
from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client
LOCK_KEY=884422990
LIMIT=max(1,min(30,int(os.getenv('TG_AUTHOR_IDENTITY_REPAIR_LIMIT','12') or 12)))

def main()->int:
    conn=psycopg2.connect(os.environ['DATABASE_URL']); cur=conn.cursor()
    cur.execute('select pg_try_advisory_lock(%s)',(LOCK_KEY,))
    if not bool(cur.fetchone()[0]):
        print('TG_AUTHOR_IDENTITY_REPAIR=SKIP telegram_session_busy'); conn.close(); return 0
    conn.commit()
    cur.execute("""select l.id,m.id,m.external_message_id,m.external_author_id,s.external_id,
      s.payload->>'telegram_access_hash' source_access_hash
      from boris_sales_hot_leads l join monitor_messages m on m.id::text=l.monitor_message_id
      join monitor_sources s on s.id=m.source_id
      where l.status='new' and l.monitor_intent='buy_service' and coalesce(l.approval_required,false)=true
        and coalesce(l.contact_username,'')='' and l.contact_access_hash is null
        and coalesce(l.source_posted_at,l.discovered_at)>=now()-interval '5 days'
        and coalesce(m.external_author_id,'') ~ '^[0-9]+$'
        and s.external_id like '-100%%' and coalesce(s.payload->>'telegram_access_hash','') ~ '^[0-9-]+$'
      order by coalesce(l.source_posted_at,l.discovered_at) desc limit %s""",(LIMIT,))
    rows=cur.fetchall(); conn.commit()
    if not rows:
        cur.execute('select pg_advisory_unlock(%s)',(LOCK_KEY,)); conn.commit(); conn.close()
        print('TG_AUTHOR_IDENTITY_REPAIR=PASS candidates=0 repaired=0 skipped=0 errors=0'); return 0
    client=build_client(decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),int(os.environ['MONITOR_TG_API_ID']),os.environ['MONITOR_TG_API_HASH'])
    repaired=skipped=errors=0
    try:
        from telethon.tl.types import InputPeerChannel, InputPeerUserFromMessage
        from telethon.errors import FloodWaitError
        for lead_id,msg_uuid,external_message_id,external_author_id,source_external_id,source_access_hash in rows:
            try:
                peer=InputPeerChannel(int(str(source_external_id)[4:]),int(source_access_hash))
                msg=client.get_messages(peer,ids=int(external_message_id))
                sender=getattr(msg,'sender',None) if msg else None
                sid=getattr(sender,'id',None) if sender else None
                ah=getattr(sender,'access_hash',None) if sender else None
                is_bot=bool(getattr(sender,'bot',False)) if sender else False
                is_min=bool(getattr(sender,'min',False)) if sender else False
                if not sender or is_bot or not sid or str(sid)!=str(external_author_id):
                    skipped+=1; continue
                if is_min:
                    try:
                        sender=client.get_entity(InputPeerUserFromMessage(peer,int(external_message_id),int(sid)))
                        sid=getattr(sender,'id',None); ah=getattr(sender,'access_hash',None)
                        is_bot=bool(getattr(sender,'bot',False)); is_min=bool(getattr(sender,'min',False))
                    except Exception:
                        skipped+=1; continue
                if is_bot or is_min or not sid or not ah:
                    skipped+=1; continue
                resolved_username=str(getattr(sender,'username',None) or '')
                cur.execute("""update monitor_messages set
                  author_username=COALESCE(NULLIF(%s,''),author_username),
                  payload=coalesce(payload,'{}'::jsonb)||jsonb_build_object(
                  'author_access_hash',%s,'author_is_bot',false,'author_is_min',false,'author_identity_repaired_at',NOW()::text)
                  where id=%s""",(resolved_username,str(ah),msg_uuid))
                cur.execute("""update boris_sales_hot_leads set contact_peer_id=%s,contact_access_hash=%s,contact_username=COALESCE(NULLIF(%s,''),contact_username),
                  approval_required=false,approval_note=case when approval_note is null then 'telegram_author_identity_repaired' else approval_note end,
                  updated_at=now() where id=%s and status='new'""",(int(sid),int(ah),resolved_username,lead_id))
                conn.commit(); repaired+=1; time.sleep(0.25)
            except FloodWaitError as e:
                sec=max(300,int(getattr(e,'seconds',0) or 0)); until=datetime.now(timezone.utc)+timedelta(seconds=sec)
                cur.execute("""insert into monitor_runtime_state(key,value_text,updated_at) values('telegram_scan_cooldown_until',%s,NOW())
                  on conflict(key) do update set value_text=excluded.value_text,updated_at=now()""",(until.isoformat(),)); conn.commit()
                print(f'TG_AUTHOR_IDENTITY_REPAIR=COOLDOWN seconds={sec} repaired={repaired}'); return 0
            except Exception as e:
                errors+=1; conn.rollback(); print(f'TG_AUTHOR_IDENTITY_REPAIR_ITEM=DEGRADED lead={lead_id} type={type(e).__name__}')
    finally:
        try: client.disconnect()
        except Exception: pass
        try: cur.execute('select pg_advisory_unlock(%s)',(LOCK_KEY,)); conn.commit()
        except Exception: pass
        conn.close()
    print(f'TG_AUTHOR_IDENTITY_REPAIR=PASS candidates={len(rows)} repaired={repaired} skipped={skipped} errors={errors}')
    return 0 if errors==0 else 2
if __name__=='__main__': raise SystemExit(main())
