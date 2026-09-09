from unittest.mock import patch
from app.api.cpx_advisor import negative_raise_rollback_candidates, _classify_negative_rollback_apply_result

def run(j):
 def load(_db, _account_id, key):
  if key == 'cpx_learning_journal': return j
  if key == 'cpx_advice':
   ids = [x.get('item_id') for x in j if isinstance(x, dict) and x.get('item_id')]
   return {'recommendations': {'hold': [
    {'id': iid, 'write_capability':'canonical', 'promotion_active':True, 'bid_rub':999}
    for iid in ids
   ]}}
  return None
 with patch('app.api.cpx_advisor._load_json',side_effect=load):return negative_raise_rollback_candidates(object(),'a')
def test_worsened_candidate():
 c=run([{'action':'raise','item_id':7,'old_bid_rub':100,'new_bid_rub':110,'effect':'worsened','finished_at':'2026-09-01'}]);assert len(c)==1 and c[0]['item_id']==7
def test_newer_improved_clears_old_loss():
 assert run([{'action':'raise','item_id':7,'old_bid_rub':100,'new_bid_rub':110,'effect':'worsened','finished_at':'2026-09-01'},{'action':'raise','item_id':7,'old_bid_rub':110,'new_bid_rub':120,'effect':'improved','finished_at':'2026-09-03'}])==[]
def test_lower_after_loss_prevents_repeat():
 assert run([{'action':'raise','item_id':7,'old_bid_rub':100,'new_bid_rub':110,'effect':'worsened','finished_at':'2026-09-01'},{'action':'lower','item_id':7,'old_bid_rub':110,'new_bid_rub':100,'effect':'neutral','finished_at':'2026-09-02'}])==[]


def test_idempotent_succeeded_receipt_is_healthy_noop():
    result = {'status':'ok','changed_avito':False,'idempotent':True,'execution_status':'succeeded'}
    assert _classify_negative_rollback_apply_result(result) == ('idempotent','ok',False)


def test_plain_ok_nochange_without_receipt_proof_stays_error():
    result = {'status':'ok','changed_avito':False}
    assert _classify_negative_rollback_apply_result(result) == ('error','ok',False)
