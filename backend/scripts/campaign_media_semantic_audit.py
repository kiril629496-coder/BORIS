#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal title/description/category -> image semantic audit for Campaign/Feed Factory.

Usage:
  PYTHONPATH=/root/BORIS/backend venv/bin/python scripts/campaign_media_semantic_audit.py <campaign_id>
      Read-only validation of persisted proofs.
  ... <campaign_id> --classify-item-match
      Re-check every linked image against EACH item's current title/description/category/direction,
      persist exact content+media proofs, then validate. Never approves, uploads or publishes.

Legacy --classify remains available for old furniture-class tags only.
"""
import argparse, json, sys
from pathlib import Path

# Executable audit scripts must work from any cwd without an operator-supplied
# PYTHONPATH.  This changes import bootstrap only; read-only mode remains read-only.
_BACKEND_ROOT=Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0,str(_BACKEND_ROOT))

from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.media_asset import MediaAsset
from app.models.media_link import MediaLink
from app.services.media_semantic_validator import (
    classify_asset_vision, stamp_semantic_review, validate_linked_media_semantics,
    classify_item_media_vision, stamp_item_semantic_evidence, semantic_content_fingerprint,
)

ap=argparse.ArgumentParser(); ap.add_argument('campaign_id',type=int); ap.add_argument('--classify',action='store_true'); ap.add_argument('--classify-item-match',action='store_true'); ap.add_argument('--include-history',action='store_true',help='also audit superseded/archived/deleted historical rows'); args=ap.parse_args()
db=SessionLocal()
try:
 q=db.query(CampaignItem).filter(CampaignItem.campaign_id==args.campaign_id)
 if not args.include_history:
  q=q.filter(~CampaignItem.status.in_(['superseded','archived','deleted']))
 rows=q.order_by(CampaignItem.id).all()
 if not rows: raise SystemExit('campaign has no items')
 if args.classify:
  cache={}
  pairs=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
         .filter(MediaLink.entity_type=='campaign_item',MediaLink.entity_id.in_([str(r.id) for r in rows]),MediaAsset.deleted_at.is_(None)).all())
  for _,asset in pairs:
   tags=[str(t) for t in (asset.tags or [])]
   if 'semantic_reviewed' in tags: continue
   key=asset.source_ref or ('asset:'+str(asset.id)); verdict=cache.get(key)
   if verdict is None: verdict=cache[key]=classify_asset_vision(asset,asset.account_id)
   stamp_semantic_review(asset,verdict)
  db.commit()
 if args.classify_item_match:
  item_ids=[r.id for r in rows]
  for n,item_id in enumerate(item_ids,1):
   saved=False
   for attempt in range(1,4):
    db.expire_all(); row=db.query(CampaignItem).filter(CampaignItem.id==item_id,CampaignItem.campaign_id==args.campaign_id).first()
    if not row: raise RuntimeError(f'item {item_id}: disappeared during audit')
    payload=json.loads(row.payload_json or '{}'); fp_before=semantic_content_fingerprint(payload); updated_before=str(row.updated_at)
    linked=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
            .filter(MediaLink.entity_type=='campaign_item',MediaLink.entity_id==str(row.id),MediaAsset.deleted_at.is_(None))
            .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
    if not linked: raise RuntimeError(f'item {row.id}: no linked media')
    media_sig=[(int(l.position),int(a.id)) for l,a in linked]
    # First try the durable per-asset family cache. This is important for draft
    # cards whose text fingerprint changed after the images had already passed
    # paid Vision QA. Reusing an explicit positive family proof is zero-cost and
    # avoids calling the provider again merely to refresh the item fingerprint.
    verdicts=classify_item_media_vision(payload,linked)
    db.expire_all(); fresh=db.query(CampaignItem).filter(CampaignItem.id==item_id,CampaignItem.campaign_id==args.campaign_id).first(); fresh_payload=json.loads(fresh.payload_json or '{}')
    fresh_linked=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                  .filter(MediaLink.entity_type=='campaign_item',MediaLink.entity_id==str(item_id),MediaAsset.deleted_at.is_(None))
                  .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
    fresh_sig=[(int(l.position),int(a.id)) for l,a in fresh_linked]
    if semantic_content_fingerprint(fresh_payload)!=fp_before or fresh_sig!=media_sig or str(fresh.updated_at)!=updated_before:
     print(f'RETRY item={item_id} attempt={attempt}: concurrent change detected',flush=True); db.rollback(); continue
    stamp_item_semantic_evidence(fresh_payload,fresh_linked,verdicts); fresh.payload_json=json.dumps(fresh_payload,ensure_ascii=False); db.commit(); saved=True
    print(f'AUDITED {n}/{len(item_ids)} item={item_id} match={sum(1 for v in verdicts if v.get("match"))}/{len(verdicts)} min_conf={min(float(v.get("confidence") or 0) for v in verdicts):.2f}',flush=True); break
   if not saved: raise RuntimeError(f'item {item_id}: concurrent changes prevented safe semantic proof')
 errors=[]
 for row in rows:
  payload=json.loads(row.payload_json or '{}')
  for e in validate_linked_media_semantics(db,row,payload): errors.append({'item_id':row.id,**e})
 print(json.dumps({'campaign_id':args.campaign_id,'scope':'all_history' if args.include_history else 'current','items':len(rows),'semantic_errors':len(errors),'errors':errors},ensure_ascii=False,indent=2))
 raise SystemExit(2 if errors else 0)
finally: db.close()
