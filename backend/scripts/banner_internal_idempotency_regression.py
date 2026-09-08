#!/usr/bin/env python3
"""Zero-paid-call regression for internal Avito banner intents and quota replay."""
from __future__ import annotations
import json

import app.api.avito as avito
import app.api.banners as banners
import app.api.billing as billing


def main() -> int:
    originals={
        'load': avito._load_drafts,
        'save': avito._save_drafts,
        'mutate': avito._mutate_drafts,
        'images': avito.images_list,
        'safe': banners.create_avito_safe_banner,
        'replay_available': banners.avito_safe_banner_replay_available,
        'status': billing.get_status,
        'consume': billing.check_and_consume,
    }
    seen=set(); billing_seen=set(); consumed=[]; saved=[]
    draft_state=[{
        'id':'qa-draft-1','batch_label':'QA batch','title':'Кухня на заказ',
        'description':'Кухня по размерам клиента','images':['/images/qa/original.png'],
    }]
    try:
        avito._load_drafts=lambda account_id:[dict(x) for x in draft_state]
        avito._save_drafts=lambda account_id, rows:saved.append((account_id,[dict(x) for x in rows]))
        def fake_mutate(account_id, mutator):
            nonlocal draft_state
            draft_state=[dict(x) for x in mutator([dict(x) for x in draft_state])]
            saved.append((account_id,[dict(x) for x in draft_state]))
            return [dict(x) for x in draft_state]
        avito._mutate_drafts=fake_mutate
        avito.images_list=lambda account_id:{'status':'ok','folders':{}}
        quota={'remaining':2}
        billing.get_status=lambda account_id:{'unlimited':False,'usage':{'banners':{'remaining':quota['remaining']}}}
        def fake_consume(account_id,unit,amount=1,idempotency_key=''):
            key=(account_id,unit,idempotency_key)
            if idempotency_key and key in billing_seen:
                return {'allowed':True,'idempotency_replay':True,'idempotency_key':idempotency_key}
            if quota['remaining'] < amount:
                return {'allowed':False,'blocked_reason':'billing_limit'}
            quota['remaining']-=amount
            billing_seen.add(key)
            consumed.append((account_id,unit,amount,idempotency_key))
            return {'allowed':True,'idempotency_replay':False,'idempotency_key':idempotency_key}
        billing.check_and_consume=fake_consume

        def fake_safe(*,account_id,description,direction='architecture',accent_color='#2F6FED',idempotency_key='',**kwargs):
            if not idempotency_key:
                raise AssertionError('internal batch banner lost idempotency key')
            replay=idempotency_key in seen
            seen.add(idempotency_key)
            return {'status':'ok','url':'/images/qa/'+idempotency_key.replace(':','_')+'.png','idempotency_replay':replay}
        banners.create_avito_safe_banner=fake_safe
        banners.avito_safe_banner_replay_available=lambda account_id,key,direction='architecture': key in seen

        kwargs=dict(account_id='qa_account',batch_label='QA batch',banner_count=2,photos_per_ad=1,folder='',idempotency_key='qa-batch-intent')
        first=avito.apply_banner_to_batch_impl(**kwargs)
        quota['remaining']=0
        second=avito.apply_banner_to_batch_impl(**kwargs)
        checks={
            'first_new_two': first.get('banners_new')==2 and first.get('banners_replayed')==0,
            'second_replay_two': second.get('banners_new')==0 and second.get('banners_replayed')==2,
            'quota_consumed_once': consumed==[
                ('qa_account','banners',1,'qa-batch-intent:billing:banner:0'),
                ('qa_account','banners',1,'qa-batch-intent:billing:banner:1'),
            ],
            'stable_two_billing_child_intents': billing_seen=={
                ('qa_account','banners','qa-batch-intent:billing:banner:0'),
                ('qa_account','banners','qa-batch-intent:billing:banner:1'),
            },
            'stable_two_child_intents': seen=={'qa-batch-intent:banner:0','qa-batch-intent:banner:1'},
            'drafts_saved_both_runs': len(saved)==2,
            'replay_works_after_quota_zero': second.get('status')=='ok' and second.get('banners_replayed')==2 and quota['remaining']==0,
        }
        failed=[k for k,v in checks.items() if not v]
        print(json.dumps({'status':'PASS' if not failed else 'FAIL','checks':checks,'consumed':consumed,'first':first,'second':second,'failures':failed},ensure_ascii=False,indent=2))
        return 0 if not failed else 2
    finally:
        avito._load_drafts=originals['load']; avito._save_drafts=originals['save']; avito._mutate_drafts=originals['mutate']; avito.images_list=originals['images']
        banners.create_avito_safe_banner=originals['safe']; banners.avito_safe_banner_replay_available=originals['replay_available']; billing.get_status=originals['status']; billing.check_and_consume=originals['consume']

if __name__=='__main__':
    raise SystemExit(main())
