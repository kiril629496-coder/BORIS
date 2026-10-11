"""Pure regressions: changing date must not discard unconfirmed bid resets.

No database writes, money permits, HTTP calls, account settings or clock changes.
"""
import ast
from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(os.environ.get('BORIS_TEST_LATE_DAY_SOURCE',str(ROOT/'late_day_spend_controller.py')))
NOW=datetime(2026,10,8,8,0,tzinfo=ZoneInfo('Europe/Moscow'))


def extract(path=SOURCE):
    tree=ast.parse(Path(path).read_text())
    helper=next((n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_pacing_day_state'),None)
    boost=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='boost_evening')
    body=None
    for node in ast.walk(boost):
        # The account loop wraps its body in try/finally; select the actual
        # statement list containing the adjacent load/rollover operations.
        if not isinstance(getattr(node,'body',None),list):continue
        start=next((i for i,n in enumerate(node.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='state' for t in n.targets)),None)
        if start is None:continue
        end=next((i for i,n in enumerate(node.body) if i>start and isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_state_items' for t in n.targets)),None)
        if end is not None:body=deepcopy(node.body[start:end]);break
    if body is None:raise AssertionError('actual rollover block not found')
    template=ast.parse('def rollout(previous, now_msk):\n out=[]\n reached=False\n state=None\n for a in ["fixture_account"]:\n  reached=True\n return state,out,reached\n').body[0]
    loop=next(n for n in template.body if isinstance(n,ast.For))
    loop.body=body+loop.body
    scope={'datetime':datetime,'db':None,'STATE_KEY':'fixture_state','_load':None}
    nodes=([deepcopy(helper)] if helper else [])+[template]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(path),'exec'),scope)
    def invoke(previous,now=NOW):
        scope['_load']=lambda *args:deepcopy(previous)
        return scope['rollout'](previous,now)
    return scope.get('_pacing_day_state'),invoke


def previous(status='boosting',day='2026-10-07'):
    return {'day_msk':day,'status':'reset_pending','items':{'101':{'status':status,'baseline_bid_rub':22,'last_reset_block':'http_500'}},'custom_audit':'preserve'}


class LateDayStateRolloverTests(unittest.TestCase):
    def setUp(self):self.helper,self.rollout=extract()

    def test_unresolved_previous_day_is_not_erased(self):
        before=previous();state,out,reached=self.rollout(before)
        self.assertFalse(reached)
        self.assertEqual(state,before)
        self.assertEqual(out[0]['pending_item_ids'],['101'])
        self.assertFalse(out[0]['changed_avito'])
        self.assertEqual(out[0]['applied'],0)

    def test_original_state_not_modified_in_memory(self):
        before=previous();snapshot=deepcopy(before)
        result=self.helper(before,NOW)
        self.assertEqual(before,snapshot)
        result['state']['items']['101']['status']='fixture_mutation'
        self.assertEqual(before,snapshot)

    def test_reset_success_allows_new_day(self):
        state,out,reached=self.rollout(previous(status='reset'))
        self.assertTrue(reached)
        self.assertEqual(state,{'day_msk':'2026-10-08','status':'active','items':{}})
        self.assertEqual(out,[])

    def test_already_below_baseline_allows_new_day(self):
        state,out,reached=self.rollout(previous(status='skipped_below_baseline'))
        self.assertTrue(reached)
        self.assertEqual(state['items'],{})

    def test_mixed_terminal_and_pending_does_not_drop_pending(self):
        old=previous();old['items']['102']={'status':'reset'}
        state,out,reached=self.rollout(old)
        self.assertFalse(reached)
        self.assertEqual(state,old)
        self.assertEqual(out[0]['pending_item_ids'],['101'])

    def test_empty_initial_state_bootstraps(self):
        state,out,reached=self.rollout({})
        self.assertTrue(reached)
        self.assertEqual(state['day_msk'],'2026-10-08')

    def test_same_service_day_preserves_current_progress(self):
        old=previous(day='2026-10-08');old['status']='active'
        state,out,reached=self.rollout(old)
        self.assertTrue(reached)
        self.assertEqual(state,old)

    def test_multiple_missed_days_keep_original_debt(self):
        old=previous(day='2026-09-28')
        for day in [8,9,10]:
            state,out,reached=self.rollout(old,datetime(2026,10,day,8,tzinfo=ZoneInfo('Europe/Moscow')))
            self.assertFalse(reached)
            self.assertEqual(state['day_msk'],'2026-09-28')
            self.assertEqual(state['items'],old['items'])

    def test_missing_baseline_is_not_silently_discarded(self):
        old=previous();old['items']['101'].pop('baseline_bid_rub')
        state,out,reached=self.rollout(old)
        self.assertFalse(reached)
        self.assertEqual(state,old)

    def test_corrupt_items_fail_closed(self):
        old=previous();old['items']=['invalid']
        result=self.helper(old,NOW)
        self.assertFalse(result['allow'])
        self.assertEqual(result['reason'],'invalid_previous_day_items')

    def test_malformed_item_is_not_lost(self):
        old=previous();old['items']['101']='invalid'
        self.assertFalse(self.helper(old,NOW)['allow'])

    def test_future_or_invalid_date_does_not_reset_state(self):
        for day in ['2026-10-09','broken','2026-99-99']:
            old={'day_msk':day,'status':'active','items':{}}
            result=self.helper(old,NOW)
            self.assertFalse(result['allow'])
            self.assertEqual(result['state'],old)

    def test_both_main_modes_still_use_the_guarded_boost_path(self):
        text=SOURCE.read_text()
        self.assertIn('if not args.reset_only:boost=boost_evening(now)',text)
        self.assertIn('state=_day_state["state"]',text)
        self.assertIn('"changed_avito":False',text)


if __name__=='__main__':unittest.main()
