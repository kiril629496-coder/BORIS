"""Canonical fleet coverage and singleton-lock tests without production DB."""
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace as NS
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'boris_guardian.py'
NAMES={'_module_watch_account_order','_module_watch_lock_key','active_client_module_watch'}


def extracted():
    tree=ast.parse(SOURCE.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
    scope={'dt':dt,'json':json,'text':lambda v:v,'os':NS(environ={})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(SOURCE),'exec'),scope)
    return scope


class Result:
    def __init__(self,value=None):self.value=value
    def scalar(self):return self.value
    def mappings(self):return self
    def first(self):return self.value


class DB:
    def __init__(self,blocked_keys=(),existing=None):
        self.blocked=set(blocked_keys);self.existing=existing or {};self.writes=[];self.rollbacks=0;self.commits=0
    def execute(self,sql,params=None):
        params=params or {}
        if 'pg_try_advisory_xact_lock' in sql:return Result(params['k'] not in self.blocked)
        if 'SELECT id,value FROM storage' in sql:return Result(self.existing.get(params['a']))
        if 'SELECT value FROM storage' in sql:return Result(None)
        if sql.lstrip().startswith(('INSERT INTO storage','UPDATE storage')):
            self.writes.append((sql,dict(params)));return Result()
        raise AssertionError('Unexpected SQL: '+sql)
    def commit(self):self.commits+=1
    def rollback(self):self.rollbacks+=1
    def close(self):pass


def watch(accounts,priority='',db=None):
    scope=extracted();db=db or DB();scope['SessionLocal']=lambda:db
    scope['os']=NS(environ={'OWNER_DAILY_REPORT_ACCOUNT_IDS':priority})
    supervisor=ModuleType('app.services.client_supervisor')
    supervisor.candidate_accounts=lambda session:list(accounts)
    supervisor.build_client_state=lambda session,aid:{'client_state':'active','overall':'working','expected_modules':[],'modules':[]}
    control=ModuleType('app.models.control_plane');control.ControlIncident=object
    with patch.dict(sys.modules,{'app.services.client_supervisor':supervisor,'app.models.control_plane':control}):
        result=scope['active_client_module_watch']()
    return result,db


class RecoveryFleetWatchTests(unittest.TestCase):
    def test_priority_four_does_not_hide_other_canonical_accounts(self):
        accounts=['maria','albert','artashes','garik','evgeniy','planeta']
        result,db=watch(accounts,'maria,albert,artashes,garik')
        self.assertEqual([s['account_id'] for s in result['snapshots']],accounts)
        self.assertEqual(result['discovery']['account_count'],6)
        self.assertEqual(len(db.writes),6)

    def test_unknown_retired_priority_does_not_create_a_client(self):
        result,db=watch(['active'],'retired,qa_test')
        self.assertEqual([s['account_id'] for s in result['snapshots']],['active'])
        self.assertEqual(len(db.writes),1)

    def test_empty_discovery_does_not_fallback_to_hardcoded_clients(self):
        result,db=watch([],'maria,albert')
        self.assertEqual(result['snapshots'],[])
        self.assertEqual(db.writes,[])

    def test_duplicate_accounts_are_checked_once(self):
        result,db=watch(['b','a','a'],'b,b,a')
        self.assertEqual([s['account_id'] for s in result['snapshots']],['b','a'])
        self.assertEqual(len(db.writes),2)

    def test_lock_matches_existing_singleton_reconciler(self):
        scope=extracted()
        for aid in ['mbroker_27859','evgeniy_peregorodki_12447','planetazayavki_65985']:
            raw=hashlib.blake2b((aid+'|active_module_watch_v1').encode(),digest_size=8).digest()
            n=int.from_bytes(raw,'big',signed=False)
            expected=n if n<(1<<63) else n-(1<<64)
            self.assertEqual(scope['_module_watch_lock_key'](aid),expected)

    def test_concurrent_writer_is_deferred_without_duplicate_insert(self):
        key=extracted()['_module_watch_lock_key']('busy')
        result,db=watch(['busy','free'],db=DB(blocked_keys=[key]))
        self.assertEqual(result['deferred_accounts'],['busy'])
        self.assertEqual([s['account_id'] for s in result['snapshots']],['free'])
        self.assertEqual(len(db.writes),1)

    def test_current_bucket_is_read_without_rewriting(self):
        bucket=int(dt.datetime.now(dt.timezone.utc).timestamp()//600)
        existing={'id':1,'value':json.dumps({'account_id':'active','bucket':bucket})}
        result,db=watch(['active'],db=DB(existing={'active':existing}))
        self.assertEqual(len(result['snapshots']),1)
        self.assertEqual(db.writes,[])

    def test_no_financial_or_configuration_mutation(self):
        result,db=watch(['one','two'])
        for sql,params in db.writes:
            self.assertTrue(sql.lstrip().startswith('INSERT INTO storage'))
            self.assertIn('active_module_watch_v1',sql)
            snapshot=json.loads(params['v'])
            self.assertEqual(snapshot['client_state'],'active')
        self.assertEqual(result['warnings'],[])


if __name__=='__main__':unittest.main()
