#!/usr/bin/env python3
"""Zero-paid deterministic repair for published campaign hero -> canonical feed drift.

Only repairs an already bound published identity when campaign payload has a selected
hero_media_id and the corresponding feed item already exists. It changes image[0]
only; it never creates/publishes an item and never calls Avito or paid AI.
"""
import json
from urllib.parse import unquote
from app.db.session import SessionLocal
from app.models.campaign_item import CampaignItem
from app.models.storage import Storage
from app.api.avito import _mutate_feed_items


def canon(v):
    """Canonical identity for a public image URL without changing stored spelling."""
    s=str(v or '').strip()
    if '/images/' not in s:
        return s
    return '/images/'+unquote(s.split('/images/',1)[1].split('?',1)[0])


def apply_hero_replacements(current, repl):
    """Move the selected hero to index 0 without duplicating or losing gallery media.

    If the hero already exists, move its canonical occurrence and retain every
    other image in relative order. If it is absent, preserve the historical
    replace-index-zero behavior. Canonical-equivalent URLs count as one image.
    """
    seen=set()
    for x in current:
        fid=str(getattr(x,'id','') or '')
        if fid not in repl:
            continue
        gallery=list(getattr(x,'images',None) or [])
        if not gallery:
            raise RuntimeError(f'empty feed gallery {fid}')
        hero=repl[fid]['hero']
        hero_key=canon(hero)
        hero_present=any(canon(value)==hero_key for value in gallery)
        tail=gallery if hero_present else gallery[1:]
        normalized=[]
        used={hero_key}
        for value in tail:
            key=canon(value)
            if not key or key in used:
                continue
            used.add(key)
            normalized.append(value)
        x.images=[hero]+normalized
        seen.add(fid)
    missing=set(repl)-seen
    if missing:
        raise RuntimeError('missing identities '+repr(sorted(missing)))
    return current


def main():
    db=SessionLocal(); repairs=[]; skipped=[]
    try:
        rows=db.query(CampaignItem).filter(
            CampaignItem.feed_identity.isnot(None),
            CampaignItem.identity_status=='published_identity_bound',
            CampaignItem.hero_media_id.isnot(None),
            CampaignItem.status=='published',
        ).all()
        by_acc={}
        for r in rows:
            try: p=json.loads(r.payload_json or '{}')
            except Exception:
                skipped.append({'item_id':r.id,'reason':'invalid_payload'}); continue
            imgs=[str(x).strip() for x in (p.get('images') or []) if str(x).strip()]
            if not imgs: continue
            sr=db.query(Storage).filter(Storage.account_id==r.account_id,Storage.key=='feed_items').first()
            if not sr: continue
            try: feed=json.loads(sr.value or '[]')
            except Exception: continue
            fi=next((x for x in feed if isinstance(x,dict) and str(x.get('id') or '')==str(r.feed_identity)),None)
            if not fi: continue
            live=[str(x).strip() for x in (fi.get('images') or []) if str(x).strip()]
            if live and canon(live[0])==canon(imgs[0]): continue
            if not live:
                skipped.append({'item_id':r.id,'reason':'empty_live_gallery'}); continue
            by_acc.setdefault(r.account_id,{})[str(r.feed_identity)]={'hero':imgs[0],'item_id':r.id}
        for acc,repl in by_acc.items():
            _mutate_feed_items(acc,lambda current: apply_hero_replacements(current,repl))
            for fid,v in repl.items(): repairs.append({'account_id':acc,'feed_identity':fid,'item_id':v['item_id'],'hero':v['hero']})
        print(json.dumps({'status':'PASS','repairs':len(repairs),'repaired':repairs,'skipped':skipped,'paid_calls':0,'external_publish_calls':0},ensure_ascii=False,indent=2))
        return 0
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
