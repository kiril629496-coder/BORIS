#!/usr/bin/env python3
"""Read-only acceptance audit for one-card service + product campaign fixtures.

No provider calls, no publication, no paid generation. It proves that a current
one-card campaign has exactly one safe local-text banner followed by three real
photos and that persisted semantic/mobile QA still matches the canonical graph.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.media_link import MediaLink
from app.models.media_asset import MediaAsset
from app.services.media_semantic_validator import validate_linked_media_semantics

SAFE_BANNER_REFS={"campaign_stock_visual_local_text_v1","campaign_openai_visual_local_text_v1"}
SAFE_BANNER_PROVIDERS={"boris_local","openai"}


def kind(payload):
    c=str(payload.get('category_path') or payload.get('category') or '').lower()
    if 'услуг' in c or 'ремонт и отделка' in c: return 'service'
    return 'product'


def inspect_item(db,row):
    p=json.loads(row.payload_json or '{}')
    linked=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
        .filter(MediaLink.entity_type=='campaign_item',MediaLink.entity_id==str(row.id),MediaAsset.deleted_at.is_(None))
        .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
    errors=[]
    if len(linked)!=4: errors.append(f'linked_media_expected_4_got_{len(linked)}')
    if len(p.get('images') or [])!=4: errors.append(f'payload_images_expected_4_got_{len(p.get("images") or [])}')
    if not linked: errors.append('no_linked_media')
    else:
        l0,a0=linked[0]
        if int(l0.position or 0)!=0: errors.append('banner_not_position_0')
        if str(a0.source_ref or '') not in SAFE_BANNER_REFS: errors.append('unsafe_banner_source_ref')
        if str(a0.source_provider or '') not in SAFE_BANNER_PROVIDERS: errors.append('unsafe_banner_provider')
        if str(p.get('banner_text_renderer') or '')!='local_pillow': errors.append('banner_text_not_local_pillow')
        qa=p.get('banner_qa') or {}
        if not (qa.get('passed') and qa.get('mobile_ok') and qa.get('text_ok') and qa.get('composition_ok')):
            errors.append('banner_qa_not_mobile_safe')
        banner_path=Path('/root/BORIS/backend/images')/str(a0.storage_key)
        if not banner_path.is_file(): errors.append('banner_file_missing')
        else:
            try:
                with Image.open(banner_path) as im:
                    if im.width < 1024 or im.height < 1024: errors.append(f'banner_too_small_{im.width}x{im.height}')
                    if im.format!='PNG': errors.append(f'banner_format_{im.format}')
            except Exception as exc: errors.append('banner_unreadable_'+type(exc).__name__)
        for pos,(lnk,a) in enumerate(linked[1:],1):
            if int(lnk.position or 0)!=pos: errors.append(f'gallery_position_{pos}_mismatch')
            if str(a.source_ref or '') in SAFE_BANNER_REFS: errors.append(f'extra_banner_at_{pos}')
    sem=validate_linked_media_semantics(db,row,p)
    if sem: errors.append('semantic_errors:'+str(len(sem)))
    return {
        'campaign_id':row.campaign_id,'item_id':row.id,'account_id':row.account_id,
        'kind':kind(p),'title':p.get('title'),'category':p.get('category_path') or p.get('category'),
        'linked_media':len(linked),'payload_images':len(p.get('images') or []),
        'banner_provider':p.get('banner_provider'),'banner_engine':p.get('banner_engine'),
        'banner_text_renderer':p.get('banner_text_renderer'),'banner_qa':p.get('banner_qa'),
        'errors':errors,'status':'PASS' if not errors else 'FAIL'
    }


def main():
    db=SessionLocal()
    try:
        rows=(db.query(CampaignItem).join(Campaign,Campaign.id==CampaignItem.campaign_id)
            .filter(~CampaignItem.status.in_(['superseded','archived','deleted']))
            .order_by(CampaignItem.id.desc()).limit(120).all())
        representatives={}
        for row in rows:
            p=json.loads(row.payload_json or '{}')
            k=kind(p)
            if k not in representatives and len(p.get('images') or [])==4 and p.get('banner_url'):
                representatives[k]=row
            if len(representatives)==2: break
        results=[]
        for k in ('service','product'):
            row=representatives.get(k)
            if not row:
                results.append({'kind':k,'status':'BLOCKED','errors':['no_current_one_card_fixture']})
            else: results.append(inspect_item(db,row))
        ok=all(x['status']=='PASS' for x in results)
        print(json.dumps({'status':'PASS' if ok else 'FAIL','fixtures':results},ensure_ascii=False,indent=2))
        return 0 if ok else 2
    finally: db.close()

if __name__=='__main__': raise SystemExit(main())
