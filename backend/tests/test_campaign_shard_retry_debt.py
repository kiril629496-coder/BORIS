"""Offline regression for the durable parent/child media retry storm.

AST extraction executes the real planner/parent block with fake DB/enqueue only.
No campaign data, provider calls, billing, publication or real queue writes.
"""
import ast
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(os.environ.get('BORIS_TEST_JOBS_SOURCE', str(ROOT/'app/services/jobs.py')))
NOW=datetime(2026,10,8,12,0,tzinfo=timezone.utc)


def load_planners(path=SOURCE):
    tree=ast.parse(Path(path).read_text())
    names={'_campaign_media_shard_plan','_campaign_media_shard_fingerprint','_campaign_media_retry_window'}
    nodes=[copy.deepcopy(x) for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in names]
    scope={'datetime':datetime,'timedelta':timedelta,'timezone':timezone,'media_target':8}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(path),'exec'),scope)
    selected=None
    for node in ast.walk(tree):
        if not isinstance(node,ast.If):continue
        for i,stmt in enumerate(node.body):
            if isinstance(stmt,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_children' for t in stmt.targets):
                if any(isinstance(x,ast.Return) for x in node.body[i:]):selected=copy.deepcopy(node.body[i:]);break
        if selected:break
    if selected is None:raise AssertionError('canonical media parent block missing')
    fn=ast.FunctionDef(name='parent_block',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=x) for x in ('db','account_id','campaign','job','payload','media_pending','item_ids','_max_shards','_shard_size')],vararg=None,kwonlyargs=[],kw_defaults=[],kwarg=None,defaults=[]),body=selected,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(path),'exec'),scope)
    return scope


def child(i=1,ids=(1,),age=0,status='failed',code='NO_PROGRESS_LOOP'):
    return NS(id=i,status=status,finished_at=NOW-timedelta(seconds=age),payload_json={'item_ids':list(ids)},result_json={'diagnostic_code':code,'pending_item_ids':list(ids)})


def parent_once(scope, children, pending, now=NOW):
    calls=[]
    scope=dict(scope)
    # The function owns its original global namespace; configure only fake dependencies.
    global_scope=scope['parent_block'].__globals__
    global_scope['_campaign_media_shard_jobs']=lambda *args: list(children)
    helper=scope.get('_campaign_media_retry_window')
    if helper is not None:
        global_scope['_campaign_media_retry_window']=lambda ids,rows:helper(ids,rows,now=now)
    def enqueue(db,kind,payload,**kwargs):
        calls.append({'kind':kind,'payload':copy.deepcopy(payload),'fingerprint':kwargs['fingerprint']})
        return NS(id=9000+len(calls)),True
    global_scope['enqueue']=enqueue
    db=NS(commit=lambda:None)
    job=NS(id=100,payload_json={})
    result=global_scope['parent_block'](db,'test_account',NS(id=200,owner_user_id=1),job,{},list(pending),list(pending),4,10)
    return result,calls


class ShardRetryDebtTests(unittest.TestCase):
    def setUp(self):
        self.scope=load_planners()
        self.window=self.scope['_campaign_media_retry_window']

    def test_first_wave_has_no_debt(self):
        self.assertEqual(self.window([1,2],[],NOW)['held_item_ids'],[])

    def test_failed_child_is_held_for_five_minutes(self):
        result=self.window([1],[child()],NOW)
        self.assertEqual(result['held_item_ids'],[1])
        self.assertEqual(result['retry_after_seconds'],300)

    def test_expired_window_allows_retry(self):
        self.assertEqual(self.window([1],[child(age=300)],NOW)['held_item_ids'],[])

    def test_repeated_waves_have_bounded_exponential_backoff(self):
        for n,expected in [(1,300),(2,600),(3,1200),(4,1800),(20,1800)]:
            rows=[child(i=i) for i in range(n)]
            self.assertEqual(self.window([1],rows,NOW)['retry_after_seconds'],expected)

    def test_other_pending_items_are_not_held(self):
        result=self.window([2,3],[child(ids=(1,))],NOW)
        self.assertEqual(result['held_item_ids'],[])

    def test_progress_is_not_rolled_back_or_falsely_complete(self):
        result=self.window([2],[child(ids=(1,2))],NOW)
        self.assertEqual(result['held_item_ids'],[2])

    def test_business_rejection_does_not_become_no_progress(self):
        self.assertEqual(self.window([1],[child(code='validation')],NOW)['held_item_ids'],[])

    def test_running_child_is_not_failed_debt(self):
        self.assertEqual(self.window([1],[child(status='running')],NOW)['held_item_ids'],[])

    def test_old_day_history_is_not_permanent_block(self):
        self.assertEqual(self.window([1],[child(age=86401)],NOW)['held_item_ids'],[])

    def test_naive_database_timestamp_is_utc(self):
        row=child();row.finished_at=NOW.replace(tzinfo=None)
        self.assertEqual(self.window([1],[row],NOW)['retry_after_seconds'],300)

    def test_failed_parent_does_not_spawn_replacement_wave_immediately(self):
        (result,status),calls=parent_once(self.scope,[child(ids=(1,2))],[1,2])
        self.assertEqual(calls,[])
        self.assertEqual(status,'retry')
        self.assertEqual(result['stage'],'media_shard_backoff')
        self.assertEqual(result['pending_item_ids'],[1,2])
        self.assertEqual(result['done'],0)
        self.assertFalse(result['external_action'])

    def test_parent_resumes_automatically_after_backoff(self):
        (result,status),calls=parent_once(self.scope,[child(ids=(1,2))],[1,2],NOW+timedelta(seconds=301))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['payload']['item_ids'],[1,2])
        self.assertEqual(status,'retry')

    def test_unaffected_items_continue_while_failed_items_wait(self):
        (result,status),calls=parent_once(self.scope,[child(ids=(1,))],[1,2,3])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['payload']['item_ids'],[2,3])
        self.assertEqual(status,'retry')

    def test_active_children_are_not_duplicated(self):
        rows=[child(ids=(1,),status='running'),child(i=2,ids=(2,))]
        (result,status),calls=parent_once(self.scope,rows,[1,2,3])
        self.assertEqual([x['payload']['item_ids'] for x in calls],[[3]])

    def test_runner_honors_backoff_without_weakening_no_progress_guard(self):
        text=SOURCE.read_text()
        self.assertIn('job.kind=="campaign_prepare" and str((result or {}).get("stage") or "")=="media_shard_backoff"',text)
        self.assertIn('_chunk_delay=max(_chunk_delay,min(1800,max(1,int((result or {}).get("retry_after_seconds") or 300))))',text)
        self.assertIn('_no_progress_limit=3 if job.kind=="campaign_media_shard" else 24',text)
        self.assertIn('job.status = "failed"',text)

    def test_provider_error_detail_is_retained_for_diagnosis(self):
        text=SOURCE.read_text()
        self.assertIn('"provider_error_details"',text)
        self.assertIn('str(e.get("message") or "")[:240]',text)


if __name__=='__main__':unittest.main()
