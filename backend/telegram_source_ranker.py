#!/usr/bin/env python3
from __future__ import annotations
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)

def main()->int:
    with engine.begin() as db:
        rows=db.execute(text("""
          SELECT s.id,s.title,s.weight,
            count(DISTINCT l.id) FILTER (WHERE l.monitor_intent='buy_service' AND l.boris_fit>=75 AND l.status NOT IN ('skipped','declined','lost','ignored')) buyer_hits,
            count(DISTINCT m.id) FILTER (WHERE COALESCE(m.posted_at,m.collected_at)>=NOW()-interval '14 days') recent_messages
          FROM monitor_sources s
          LEFT JOIN monitor_messages m ON m.source_id=s.id
          LEFT JOIN boris_sales_hot_leads l ON l.monitor_message_id=m.id::text
          WHERE s.platform='telegram' AND s.is_active=true
          GROUP BY s.id,s.title,s.weight
        """)).mappings().all()
        changed=0; proven=0; avito=0
        for r in rows:
            title=str(r.get('title') or '').lower()
            old=int(r.get('weight') or 10)
            buyers=int(r.get('buyer_hits') or 0)
            recent=int(r.get('recent_messages') or 0)
            target=old
            is_avito=any(x in title for x in ('авито','avito','авитолог'))
            if is_avito:
                avito+=1
                # Within the Avito-first lane, proven buyer sources must win over
                # generic professional/vendor chats. This matters if Telegram
                # throttles before all 135 sources are reached.
                if buyers>=2: target=20
                elif buyers==1: target=19
                elif recent>0: target=18
                else: target=17
            elif buyers>=2:
                target=max(target,20)
            elif buyers==1:
                target=max(target,18)
            elif recent>0:
                target=max(target,12)
            if buyers>0:
                proven+=1
            # Vendor/spam-shaped Avito communities remain readable but go to the
            # bottom of the Avito lane until they prove they contain buyer posts.
            if buyers==0 and any(x in title for x in ('поведенческий фактор','накрут','авито отзывы','отзывы яндекс')):
                target=8
            # Known job boards are still scanned, but do not outrank commercial
            # communities unless they actually produced a qualified buyer.
            if any(x in title for x in ('ваканс','работа для','фриланс ваканс','биржа специалистов')):
                # Job boards can contain genuine buyer requests, but publisher
                # reposts are noisy. One confirmed buyer is useful evidence yet
                # must not permanently outrank cleaner commercial sources.
                if buyers==0: target=min(target,8)
                elif buyers==1: target=min(target,18)
            target=max(5,min(20,target))
            if target!=old:
                db.execute(text("UPDATE monitor_sources SET weight=:w,updated_at=NOW() WHERE id=:i"),{'w':target,'i':r['id']})
                changed+=1
        top=db.execute(text("""SELECT title,weight FROM monitor_sources WHERE platform='telegram' AND is_active=true
          ORDER BY CASE WHEN lower(coalesce(title,'')) SIMILAR TO '%(авито|avito|авитолог)%' THEN 0 ELSE 1 END,weight DESC,title LIMIT 12""")).all()
    print(f'TG_SOURCE_RANKER=PASS active={len(rows)} changed={changed} proven_sources={proven} avito_sources={avito}')
    for title,weight in top:
        print(f'SOURCE_PRIORITY weight={weight} title={title}')
    return 0
if __name__=='__main__':
    raise SystemExit(main())
