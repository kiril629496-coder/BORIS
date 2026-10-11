"""Execute actual producer bodies with provider/ledger boundaries replaced locally."""
import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT=Path(os.environ.get('BORIS_FEED_PRODUCER_SOURCE_ROOT',Path(__file__).resolve().parents[1]))
if not (ROOT/'app/api/avito.py').exists():
    ROOT=ROOT/'candidate/backend'


def functions(path,names,env):
    tree=ast.parse((ROOT/path).read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(nodes)==len(names)
    for n in nodes:
        n.decorator_list=[]
        n.args.defaults=[ast.Constant(None) for _ in n.args.defaults]
        n.returns=None
        for arg in n.args.args+n.args.kwonlyargs:arg.annotation=None
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(path),'exec'),env)
    return env


class DB:
    def __init__(self,events,acc):self.events,self.acc=events,acc
    def query(self,*a):return self
    def filter(self,*a):return self
    def first(self):return self.acc
    def commit(self):
        self.events.append('commit')
        if hasattr(self.acc,'_expire'):self.acc._expire()
    def rollback(self):self.events.append('rollback')
    def close(self):self.events.append('close')
    def __enter__(self):return self
    def __exit__(self,*a):self.close()


def setup_publisher(monkeypatch,*,observe_fail=False,changed=False,disabled=False,xmlstate='ok',repetitions=1,expire_credentials=False):
    events=[];captured=[];observed=[]
    class ExpiringAccount:
        owner_user_id=2
        expired=False
        def _expire(self):self.expired=True
        @property
        def avito_client_id(self):
            assert not self.expired, 'Unexpected credentials ORM reload after observation commit'
            return 'encrypted-id'
        @property
        def avito_client_secret(self):
            assert not self.expired, 'Unexpected credentials ORM reload after observation commit'
            return 'encrypted-secret'
    acc=ExpiringAccount() if expire_credentials else NS(avito_client_id='encrypted-id',avito_client_secret='encrypted-secret',owner_user_id=2)
    monkeypatch.setitem(sys.modules,'app.db.session',NS(SessionLocal=lambda:DB(events,acc)))
    monkeypatch.setitem(sys.modules,'app.models.account',NS(Account=NS(account_id='acct')))
    monkeypatch.setitem(sys.modules,'app.services.avito_account_throttle',NS(account_throttle_remaining=lambda a:0,record_account_throttle=lambda *a,**k:1))
    monkeypatch.setitem(sys.modules,'app.services.reliability',NS(module_blocked=lambda *a:(disabled,'disabled'),get_trace_id=lambda:'trace'))
    monkeypatch.setitem(sys.modules,'app.crypto_utils',NS(decrypt_secret=lambda x:x))
    monkeypatch.setitem(sys.modules,'app.services.action_log',NS(log_action=lambda **k:events.append('old_audit'),ACTOR_USER='user',ACTOR_BORIS_AUTO='auto'))
    async def xmlcheck(url):events.append('xmlcheck');return {'status':xmlstate}
    monkeypatch.setitem(sys.modules,'app.api.parser',NS(validate_feed_xmlcheck=xmlcheck))
    def capture(db,**kw):
        events.append('capture');captured.append(kw)
        return {**kw,'source_event_id':kw['event_id'],'event_id':'internal'}
    def observe(db,context,facts):
        events.append('observe');observed.append(facts)
        if observe_fail:raise RuntimeError('ledger-only failure')
    monkeypatch.setitem(sys.modules,'app.services.control_rule_runtime',NS(safe_capture_feed_context=capture,observe_feed_result=observe,_observe_rule_failure=lambda exc:events.append("observable_error")))
    def post(url,**kw):
        if url.endswith('/token/'):events.append('token');body={'access_token':'token'}
        else:events.append('publish');body={'upload_id':123}
        return NS(status_code=200,content=b'{}',json=lambda:body,headers={},text='')
    def get(url,**kw):events.append('history');return NS(status_code=200,json=lambda:{'uploads':[]},headers={})
    monkeypatch.setitem(sys.modules,'httpx',NS(post=post,get=get))
    reads=[]
    def load(a):
        reads.append(a)
        return [{'id':'ff-1','title':'changed' if changed and len(reads)>1 else 'source'}]
    def attach(*a,**kw):events.append('attach');return {'status':'ok','attached':True}
    env={'_load_feed_items':load,'_ensure_autoload_feed_configured':attach,'observe_suppressed':lambda *a,**k:events.append('observable_error')}
    functions(Path('app/api/avito.py'),{'feed_send_to_avito'},env)
    for _ in range(repetitions):
        result=env['feed_send_to_avito']('acct-a',NS(id=2,role='owner'))
    return result,events,captured,observed


def test_actual_publish_preserves_result_and_one_provider_write(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch)
    assert result['upload_id']==123 and result['status']=='ok'
    assert events.count('publish')==1 and events.count('xmlcheck')==1
    assert events.index('capture')<events.index('commit')<events.index('token')
    assert events.index('observe')>events.index('publish')
    assert cap[0]['campaign_id'] is None and cap[0]['artifact_kind']=='canonical_feed_items_json'
    assert obs[0]['event_id']==cap[0]['event_id'] and obs[0]['source_input_unchanged'] is True
    assert obs[0]['XML_bytes_verified'] is False


def test_actual_ledger_failure_never_changes_provider_result(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch,observe_fail=True)
    assert result['upload_id']==123 and events.count('publish')==1 and 'observable_error' in events


def test_actual_source_drift_is_explicit(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch,changed=True)
    assert result['upload_id']==123 and obs[0]['source_input_unchanged'] is False


def test_disabled_has_no_capture_or_provider_calls(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch,disabled=True)
    assert result['status']=='blocked' and not cap and not obs and 'token' not in events


def test_xml_pending_no_publish_but_actual_observation(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch,xmlstate='pending')
    assert result['status']=='waiting' and 'publish' not in events and obs[0]['result']==result


def test_actual_two_new_invocations_same_source_are_distinct(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch,repetitions=2)
    assert events.count('publish')==2 and len(cap)==len(obs)==2
    assert cap[0]['artifact_sha256']==cap[1]['artifact_sha256']
    assert cap[0]['event_id']!=cap[1]['event_id']
    assert [o['event_id'] for o in obs]==[c['event_id'] for c in cap]


def test_actual_xmlcheck_same_artifact_pending_then_ok_new_context(monkeypatch):
    import io
    import importlib.util
    helper=ROOT/'app/services/control_feed_rule_facts.py'
    spec=importlib.util.spec_from_file_location('app.services.control_feed_rule_facts',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules,'app.services.control_feed_rule_facts',module)
    captured=[];observed=[];events=[]
    def capture(db,**kw):
        captured.append(kw);return {**kw,'source_event_id':kw['event_id'],'event_id':'frozen-'+kw['event_id']}
    monkeypatch.setitem(sys.modules,'app.services.control_rule_runtime',NS(safe_capture_feed_context=capture,
        observe_feed_result=lambda db,ctx,facts:observed.append(facts)))
    monkeypatch.setitem(sys.modules,'app.services.campaign_editor_jobs',NS(maybe_enqueue=lambda *a:None))
    states=iter(['pending','ok'])
    async def xmlcheck(url):return {'status':next(states),'issues':[]}
    monkeypatch.setitem(sys.modules,'app.api.parser',NS(validate_feed_xmlcheck=xmlcheck))
    import os as realos
    monkeypatch.setitem(sys.modules,'os',NS(path=realos.path,makedirs=lambda *a,**kw:None))
    row=NS(id=11,account_id='acct-a',updated_at='same')
    campaign=NS(id=517,default_account_id='acct-a');ff={};db=DB(events,None)
    env=dict(json=json,open=lambda *a,**kw:io.StringIO(),_allowed_accounts=lambda *a:['acct-a'],_campaign_or_404=lambda *a:campaign,
        _ff_activation_snapshot=lambda *a:([row],[row],[],['acct-a'],1,[]),_ff_state_load=lambda *a:({},ff),
        _ff_direction_intake=lambda *a:{'all_complete':True},_ff_payload=lambda r:{'title':'same'},
        _ff_validate_row=lambda *a:{'id':11,'account_id':'acct-a','valid':True,'errors':[]},
        _ff_delivery_projection=lambda *a:([{'id':'ff-11'}],'<Ads/>'),_campaign_provider_call=lambda fn,*args:fn(*args),
        _ff_state_save=lambda *a:db.commit(),_ff_delivery_snapshot=lambda *a,**kw:{'status':'ok'})
    functions(Path('app/api/campaigns.py'),{'campaign_feed_validate','_ff_rule_capture','_ff_rule_result','_ff_rule_item_input'},env)
    for _ in range(2):env['campaign_feed_validate'](517,NS(account_id='acct-a'),db,NS())
    caps=[c for c in captured if c['stage']=='xmlcheck'];obs=[o for o in observed if o['stage']=='xmlcheck']
    assert len(caps)==len(obs)==2 and caps[0]['artifact_sha256']==caps[1]['artifact_sha256']
    assert caps[0]['event_id']!=caps[1]['event_id']
    assert [o['xmlcheck_status'] for o in obs]==['pending','ok']


def test_get_functions_have_no_added_ledger_calls():
    for filename,names in [('campaigns.py',{'_ff_delivery_snapshot','_ff_delivery_projection'}),('avito.py',{'get_feed','_autoload_upload_snapshot'})]:
        tree=ast.parse((ROOT/'app/api'/filename).read_text())
        for n in tree.body:
            if isinstance(n,ast.FunctionDef) and n.name in names:
                assert not any(isinstance(c,ast.Name) and ('feed_rule' in c.id or c.id in {'observe_feed_result','safe_capture_feed_context'}) for c in ast.walk(n))


def test_campaign_capture_before_canonical_actions_and_observe_before_save():
    source=(ROOT/'app/api/campaigns.py').read_text();tree=ast.parse(source)
    def body(name):return ast.get_source_segment(source,next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name))
    materialize=body('ff_workflow_materialize')
    assert materialize.index('_feed_rule_context=')<materialize.index('defaults=')
    assert materialize.rindex('_ff_rule_result(')<materialize.rindex('_ff_state_save(')
    validate=body('campaign_feed_validate')
    assert validate.index('_feed_local_context=')<validate.index('semantic_reports=[]')
    assert validate.index('_feed_xml_context=')<validate.index('for _attempt in range(3)')
    assert validate.rindex('_ff_rule_result(')<validate.rindex('_ff_state_save(')
    activate=body('campaign_owner_activate')
    assert activate.index('_feed_activation_context=')<activate.index('delivery=_ff_delivery_snapshot(')
    assert activate.index('_feed_publish_context=')<activate.index('result=feed_send_to_avito(target)')
    assert activate.rindex('_ff_rule_result(')<activate.rindex('_ff_state_save(')


def test_actual_credentials_frozen_before_observational_commit(monkeypatch):
    result,events,cap,obs=setup_publisher(monkeypatch,expire_credentials=True)
    assert result['upload_id']==123 and events.count('publish')==1
    assert events.index('commit')<events.index('token')
