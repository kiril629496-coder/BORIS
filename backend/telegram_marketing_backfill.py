#!/usr/bin/env python3
from __future__ import annotations
import json, os, random, time, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv('/root/BORIS/backend/.env')
from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client
from app.monitoring.quality import classify as quality_classify
from app.monitoring.rules import SearchRule, evaluate

RULE_ID='aaaaaaaa-0000-0000-0000-000000000006'
LOCK_KEY=884422990
LOOKBACK_HOURS=max(24,min(168,int(os.getenv('HOT_LEAD_AUTO_MAX_AGE_HOURS','120') or 120)))
SOURCE_LIMIT=max(5,min(30,int(os.getenv('TG_MARKETING_BACKFILL_SOURCE_LIMIT','20') or 20)))
MESSAGE_LIMIT=max(40,min(200,int(os.getenv('TG_MARKETING_BACKFILL_MESSAGE_LIMIT','120') or 120)))


def main()->int:
    conn=psycopg2.connect(os.environ['DATABASE_URL'])
    cur=conn.cursor()
    cur.execute('SELECT pg_try_advisory_lock(%s)',(LOCK_KEY,))
    if not bool(cur.fetchone()[0]):
        print('TG_MARKETING_BACKFILL=SKIP telegram_session_busy')
        conn.close(); return 0
    conn.commit()
    cur.execute("""CREATE TABLE IF NOT EXISTS telegram_marketing_backfill_sources(
      source_id UUID PRIMARY KEY REFERENCES monitor_sources(id) ON DELETE CASCADE,
      processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL DEFAULT 'ok',
      scanned_messages INTEGER NOT NULL DEFAULT 0, matched INTEGER NOT NULL DEFAULT 0,
      inserted INTEGER NOT NULL DEFAULT 0, duplicates INTEGER NOT NULL DEFAULT 0
    )""")
    conn.commit()
    cur.execute("SELECT * FROM monitor_rules WHERE id=%s AND is_active=true",(RULE_ID,))
    cols=[d.name for d in cur.description]
    rr=cur.fetchone()
    if not rr:
        print('TG_MARKETING_BACKFILL=FAIL rule_missing'); conn.close(); return 2
    rule=SearchRule.from_row(dict(zip(cols,rr)))
    cur.execute("""
      SELECT s.id,s.external_id,s.username,s.title,s.weight,s.payload,s.topic_id,
        count(distinct l.id) filter(where l.monitor_intent='buy_service') buyer_hits
      FROM monitor_sources s
      LEFT JOIN monitor_messages m ON m.source_id=s.id
      LEFT JOIN boris_sales_hot_leads l ON l.monitor_message_id=m.id::text
      WHERE s.platform='telegram' AND s.is_active=true
        AND NOT EXISTS (SELECT 1 FROM telegram_marketing_backfill_sources b WHERE b.source_id=s.id AND b.status='ok')
        AND COALESCE(s.payload->>'telegram_access_hash','')<>''
        AND lower(coalesce(s.title,'')) ~ '(авито|avito|авитолог|маркет|бизнес|предприним|продаж|лид|трафик|эксперт)'
        AND lower(coalesce(s.title,'')) !~ '(ваканс|работа для|фриланс ваканс)'
      GROUP BY s.id
      ORDER BY buyer_hits DESC,s.weight DESC,s.title
      LIMIT %s
    """,(SOURCE_LIMIT,))
    sources=[dict(zip([d.name for d in cur.description],r)) for r in cur.fetchall()]
    conn.commit()
    client=build_client(decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),int(os.environ['MONITOR_TG_API_ID']),os.environ['MONITOR_TG_API_HASH'])
    cutoff=datetime.now(timezone.utc)-timedelta(hours=LOOKBACK_HOURS)
    scanned_sources=scanned_messages=matched=inserted=duplicates=errors=0
    try:
        from telethon.tl.types import InputPeerChannel
        from telethon.errors import FloodWaitError
        for s in sources:
            source_messages0=scanned_messages; source_matched0=matched; source_inserted0=inserted; source_duplicates0=duplicates
            try:
                ext=str(s.get('external_id') or '')
                ah=(s.get('payload') or {}).get('telegram_access_hash')
                if not ext.startswith('-100') or not ah:
                    continue
                peer=InputPeerChannel(int(ext[4:]),int(ah))
                scanned_sources+=1
                for msg in client.iter_messages(peer,limit=MESSAGE_LIMIT):
                    dt=getattr(msg,'date',None)
                    if not dt: continue
                    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff: break
                    body=(getattr(msg,'message',None) or '').strip()
                    if len(body)<10: continue
                    scanned_messages+=1
                    res=evaluate(body,[rule],platform='telegram',source_id=s['id'],source_kind='group',source_weight=int(s.get('weight') or 10),posted_at=dt)
                    if not res.matched: continue
                    q=quality_classify(body,None,res.score_total)
                    if q.get('reject') or not q.get('qualified'):
                        continue
                    matched+=1
                    sender=getattr(msg,'sender',None)
                    au=(getattr(sender,'username',None) or '').strip() if sender else ''
                    first=(getattr(sender,'first_name',None) or '').strip() if sender else ''
                    last=(getattr(sender,'last_name',None) or '').strip() if sender else ''
                    an=' '.join(x for x in (first,last) if x).strip()
                    aid=getattr(sender,'id',None) if sender else getattr(msg,'sender_id',None)
                    author_access_hash=getattr(sender,'access_hash',None) if sender else None
                    mid=str(getattr(msg,'id'))
                    url=(f"https://t.me/{s['username']}/{mid}" if s.get('username') else None)
                    terms=[]
                    for mr in (res.matched_rules or []): terms.extend(mr.get('terms') or [])
                    cur.execute("""
                      INSERT INTO monitor_messages(
                        id,platform,source_id,external_message_id,external_author_id,author_name,author_username,
                        text,posted_at,url,matched_rule_id,matched_terms,matched_rules,score_total,score_breakdown,
                        language,intent,priority,ai,payload,status,collected_at,rules_hash,normalizer
                      ) VALUES(
                        %s,'telegram',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,
                        'ru','buy_service','hot','{}'::jsonb,%s::jsonb,'new',NOW(),'marketing_backfill_v1','production_quality_v2'
                      ) ON CONFLICT(platform,source_id,external_message_id) DO NOTHING
                    """,(
                      str(uuid.uuid4()),s['id'],mid,str(aid) if aid is not None else None,an or None,au or None,
                      body,dt,url,RULE_ID,json.dumps(list(dict.fromkeys(terms)),ensure_ascii=False),
                      json.dumps(res.matched_rules or [],ensure_ascii=False),int(res.score_total or 0),
                      json.dumps(res.score_breakdown or {},ensure_ascii=False),
                      json.dumps({'marketing_backfill':True,'lookback_hours':LOOKBACK_HOURS,'author_access_hash':str(author_access_hash) if author_access_hash else None},ensure_ascii=False),
                    ))
                    if cur.rowcount: inserted+=1
                    else: duplicates+=1
                    conn.commit()
                cur.execute("""INSERT INTO telegram_marketing_backfill_sources(source_id,status,scanned_messages,matched,inserted,duplicates,processed_at)
                  VALUES(%s,'ok',%s,%s,%s,%s,NOW())
                  ON CONFLICT(source_id) DO UPDATE SET status='ok',scanned_messages=EXCLUDED.scanned_messages,
                    matched=EXCLUDED.matched,inserted=EXCLUDED.inserted,duplicates=EXCLUDED.duplicates,processed_at=NOW()""",(
                    s['id'],scanned_messages-source_messages0,matched-source_matched0,inserted-source_inserted0,duplicates-source_duplicates0))
                conn.commit()
                time.sleep(random.uniform(0.4,1.0))
            except FloodWaitError as e:
                seconds=max(300,int(getattr(e,'seconds',0) or 0))
                until=datetime.now(timezone.utc)+timedelta(seconds=seconds)
                cur.execute("""INSERT INTO monitor_runtime_state(key,value_text,updated_at) VALUES('telegram_scan_cooldown_until',%s,NOW())
                  ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()""",(until.isoformat(),))
                conn.commit()
                print(f'TG_MARKETING_BACKFILL=COOLDOWN seconds={seconds}')
                return 0
            except Exception as e:
                errors+=1
                print(f'BACKFILL_SOURCE_ERROR title={s.get("title")} type={type(e).__name__}')
                try: conn.rollback()
                except Exception: pass
    finally:
        try: client.disconnect()
        except Exception: pass
        try:
            cur.execute('SELECT pg_advisory_unlock(%s)',(LOCK_KEY,)); conn.commit()
        except Exception: pass
        conn.close()
    conn2=psycopg2.connect(os.environ['DATABASE_URL']); cur2=conn2.cursor()
    cur2.execute("""SELECT count(*) FROM monitor_sources s WHERE s.platform='telegram' AND s.is_active=true
      AND COALESCE(s.payload->>'telegram_access_hash','')<>''
      AND lower(coalesce(s.title,'')) ~ '(авито|avito|авитолог|маркет|бизнес|предприним|продаж|лид|трафик|эксперт)'
      AND lower(coalesce(s.title,'')) !~ '(ваканс|работа для|фриланс ваканс)'
      AND NOT EXISTS (SELECT 1 FROM telegram_marketing_backfill_sources b WHERE b.source_id=s.id AND b.status='ok')""")
    remaining=int(cur2.fetchone()[0] or 0); conn2.close()
    print(f'TG_MARKETING_BACKFILL=PASS sources={scanned_sources} messages={scanned_messages} matched={matched} inserted={inserted} duplicates={duplicates} errors={errors} remaining={remaining} lookback_h={LOOKBACK_HOURS}')
    return 0 if errors==0 else 2

if __name__=='__main__':
    raise SystemExit(main())
