from pathlib import Path
import ast
import copy
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
import pytest

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT
sys.path.insert(0,str(ROOT))
from app.services import control_owner_budget_rule_facts as H
assert Path(H.__file__).resolve() == ROOT/"app/services/control_owner_budget_rule_facts.py"

def facts():
    now=datetime.now(timezone.utc)-timedelta(seconds=1)
    auth={"policy_version":"MONEY_BUDGET_OWNER_PROVENANCE_V1","authorized_by_user_id":17,
          "authorized_at":now.isoformat(),"daily_budget_limit_rub":400,"previous_daily_budget_limit_rub":0,
          "source":"authenticated_set_kpi_settings"}
    return {"account_id":"a","actor_user_id":17,"account_owner_user_id":17,"is_platform_owner":False,
            "authorization":auth,"authorization_time":auth["authorized_at"],"requested_budget":400,
            "stored_budget":400,"storage_readback":True}

def test_two_actual_keys_local_not_full():
    d=H.evaluate_owner_budget_facts(facts())
    assert set(d)==set(H.KEYS)
    assert all(x["predicate_result"] is True and x["whole_rule_postcondition_proven"] is False for x in d.values())
    assert all(x["facts"]["provider_spend_verified"] is False for x in d.values())

@pytest.mark.parametrize("bad",[None,True,"NaN","Infinity",-1,"not_money"])
def test_invalid_budget_never_positive(bad):
    f=facts();f["requested_budget"]=bad
    assert all(x["predicate_result"] is False for x in H.evaluate_owner_budget_facts(f).values())

@pytest.mark.parametrize("field,value",[("authorized_by_user_id",18),("authorized_by_user_id",True),
    ("policy_version","fake"),("source","unknown"),("authorized_at","bad"),("authorized_at","2026-10-09T00:00:00"),
    ("daily_budget_limit_rub",500)])
def test_provenance_corruption(field,value):
    f=facts();f["authorization"][field]=value
    assert H.evaluate_owner_budget_facts(f)[H.KEYS[1]]["predicate_result"] is False

def test_unknown_readback_is_not_zero_success():
    f=facts();f["storage_readback"]=False
    assert all(x["predicate_result"] is None and x["evaluation_status"]=="NOT_PROVEN" for x in H.evaluate_owner_budget_facts(f).values())

def test_valid_zero_explicit_budget():
    f=facts();f.update(requested_budget=0,stored_budget=0);f["authorization"]["daily_budget_limit_rub"]=0
    assert all(x["predicate_result"] is True for x in H.evaluate_owner_budget_facts(f).values())

def test_foreign_user_and_platform_owner():
    f=facts();f["account_owner_user_id"]=99
    assert all(x["predicate_result"] is False for x in H.evaluate_owner_budget_facts(f).values())
    f["is_platform_owner"]=True
    assert H.evaluate_owner_budget_facts(f)[H.KEYS[1]]["predicate_result"] is True

class Expr:
    def __getattr__(self,n): return self
    def __getitem__(self,n): return self
    def __eq__(self,n): return ("eq",n)
    def __lt__(self,n): return ("lt",n)
    def __call__(self,*a): return self
class Model:
    account_id=key=module=plan_json=created_at=opened_at=updated_at=incident_type=state=details_json=Expr()
class CPExecution(Model): pass
class Incident(Model): pass
class Scalar:
    def __init__(self,v): self.v=v
    def scalar(self): return self.v
class Query:
    def __init__(self,first): self.value=first
    def filter(self,*a): return self
    def order_by(self,*a): return self
    def first(self): return self.value
    def limit(self,n): return self
    def all(self): return self.value or []
class Nested:
    def __enter__(self): return self
    def __exit__(self,*a): return False
class DB:
    def __init__(self,owner=17): self.owner=owner;self.prior=None;self.calls=[];self.executions={};self.gaps=None
    def execute(self,q,p=None):
        self.calls.append(("sql",str(q)))
        return Scalar(self.owner if "owner_user_id" in str(q) else None)
    def query(self,m): return Query(self.prior if m is CPExecution else self.gaps)
    def begin_nested(self): return Nested()

@pytest.fixture
def runtime(monkeypatch):
    import app.services
    calls=[]
    def capture(db,**kw):
        calls.append(("capture",kw))
        return dict(contract="control_rule_event_v1",account_id=kw["account_id"],module=kw["module"],
            event_source=kw["event_source"],event_id=kw["event_id"],observed_at=kw["observed_at"].isoformat(),
            bindings=[{"requirement_key":k,"requirement_id":"uuid"+str(i),"requirement_version":3,"module":"marketing",
                       "verification_due_at":(kw["observed_at"]+timedelta(seconds=900)).isoformat()}
                      for i,k in enumerate(H.KEYS)],errors=[])
    def execution(db,c,b):
        key=(c["account_id"],c["event_id"],b["requirement_key"])
        ex=db.executions.setdefault(key,types.SimpleNamespace(id=len(db.executions)+1,plan_json={},account_id=c["account_id"],evidence={}))
        return ex
    def evidence(db,ex,**kw):
        key=(kw["level"],kw["evidence_type"],kw["fact_id"])
        if key in ex.evidence and ex.evidence[key]!=kw: raise ValueError("RULE_EVENT_REPLAY_CONFLICT")
        ex.evidence[key]=copy.deepcopy(kw);calls.append(("evidence",kw));return types.SimpleNamespace(observed_at=None)
    def move(db,ex,status,actual,reason): ex.actual=actual;calls.append(("move",status,actual,reason))
    mod=types.ModuleType("app.services.control_rule_runtime")
    mod.capture_bindings=capture;mod._execution=execution;mod._evidence_once=evidence;mod._move=move
    mod._resolve_missing=lambda db,ex: calls.append(("resolved",ex.id))
    source=ROOT/"app/services/control_rule_runtime.py"
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=="_observe_rule_failure")
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),mod.__dict__)
    monkeypatch.setitem(sys.modules,mod.__name__,mod);monkeypatch.setattr(app.services,"control_rule_runtime",mod,raising=False)
    cp=types.ModuleType("app.models.control_plane");cp.ControlExecution=CPExecution;cp.ControlIncident=Incident
    monkeypatch.setitem(sys.modules,cp.__name__,cp)
    errors=[]
    obs=types.ModuleType("app.services.exception_observability");obs.observe_suppressed=lambda *a,**k:errors.append(a)
    monkeypatch.setitem(sys.modules,obs.__name__,obs)
    gaps=[]
    monkeypatch.setattr(H,"_gap",lambda db,c,r,facts=None: gaps.append((c,r,facts)) or types.SimpleNamespace(id=12))
    return calls,gaps,errors

def capture(db):
    f=facts()
    return H.capture_owner_budget_context(db,account_id=f["account_id"],actor_user_id=f["actor_user_id"],
        account_owner_user_id=f["account_owner_user_id"],is_platform_owner=f["is_platform_owner"],
        authorization=f["authorization"],requested_budget=f["requested_budget"])

def test_capture_exact_owner_versions_pending(runtime):
    db=DB();c=capture(db)
    assert c["producer_entrypoint"]==H.ENTRYPOINT and len(db.executions)==2
    assert all(ex.actual["evaluation_status"]=="PENDING" for ex in db.executions.values())
    assert all(ex.plan_json[H.CONTEXT_KEY]["bindings"][0]["requirement_version"]==3 for ex in db.executions.values())
    assert runtime[0][0][1]["entrypoints"]=={H.ENTRYPOINT}

@pytest.mark.parametrize("actual_owner",[None,18])
def test_capture_rejects_cross_account_owner(runtime,actual_owner):
    with pytest.raises(ValueError,match="OWNER_MISMATCH"): capture(DB(actual_owner))
    assert not runtime[0]

def test_result_replay_no_duplicate_and_conflicting_payload_denied(runtime):
    db=DB();c=capture(db);s={"daily_budget_limit_rub":400,"daily_budget_authorization":c["input_facts"]["authorization"]}
    H.record_owner_budget_result(db,c,stored_settings=s,observed_at=c["observed_at"])
    H.record_owner_budget_result(db,c,stored_settings=s,observed_at=c["observed_at"])
    assert len(db.executions)==2 and all(len(ex.evidence)==2 for ex in db.executions.values())
    with pytest.raises(ValueError,match="REPLAY_CONFLICT"):
        H.record_owner_budget_result(db,c,stored_settings=dict(s,daily_budget_limit_rub=401),observed_at=c["observed_at"])

def test_incomplete_binding_creates_gap_not_pass(runtime):
    db=DB();c=capture(db);c["bindings"]=c["bindings"][:1]
    r=H.observe_owner_budget_result(db,c,stored_settings={},observed_at=datetime.now(timezone.utc).isoformat())
    assert r["state"]=="NOT_PROVEN" and len(runtime[1])==1

def test_missing_prewriter_never_late_binds(runtime):
    db=DB()
    r=H.observe_owner_budget_result(db,{"capture_failed":True},stored_settings={},observed_at=datetime.now(timezone.utc).isoformat())
    assert r["state"]=="NOT_PROVEN" and not runtime[0]

class Storage(Model):
    def __init__(self,**kw): self.__dict__.update(kw)
class Account(Model): pass
class ApiDB(DB):
    def __init__(self,events):
        super().__init__();self.events=events
        self.row=Storage(account_id="a",key="kpi_settings",value=json.dumps({"daily_budget_limit_rub":10}))
        self.account=types.SimpleNamespace(account_id="a",owner_user_id=17)
        self.queries=0
    def query(self,model):
        if model is Account:return Query(self.account)
        self.queries+=1
        return Query(self.row if self.queries==1 else None)
    def add(self,row): self.events.append("add")
    def flush(self):self.events.append("flush")
    def refresh(self,row):self.events.append("refresh")
    def commit(self):self.events.append("commit")
    def rollback(self):self.events.append("rollback")
    def close(self):self.events.append("close")

def boundary(monkeypatch):
    events=[];db=ApiDB(events)
    def module(name,**kw):
        x=types.ModuleType(name);x.__dict__.update(kw);monkeypatch.setitem(sys.modules,name,x)
    module("app.db.session",SessionLocal=lambda:db)
    module("app.models.storage",Storage=Storage)
    module("app.models.account",Account=Account)
    module("app.services.platform_roles",is_platform_owner=lambda u:False)
    module("app.services.control_plane",sync_marketing_baseline_from_actual=lambda *a,**k:events.append("baseline"))
    def cap(session,**kw):
        assert json.loads(db.row.value)["daily_budget_limit_rub"]==10
        events.append("capture");return kw
    def result(session,c,**kw):
        assert events[-2:]==["flush","refresh"]
        assert kw["stored_settings"]["daily_budget_limit_rub"]==400
        events.append("result")
    module("app.services.control_owner_budget_rule_facts",safe_capture_owner_budget_context=cap,observe_owner_budget_result=result)
    class HTTPError(Exception):
        def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail
    source=ast.parse((ROOT/"app/api/avito.py").read_text())
    node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="set_kpi_settings")
    node.decorator_list=[];node.args.defaults=[]
    tree=ast.Module(body=[node],type_ignores=[])
    ns={"KpiSettingsRequest":object,"HTTPException":HTTPError,"_audit_log":lambda *a,**kw:events.append("audit")}
    exec(compile(tree,"actual_set_kpi_settings","exec"),ns)
    req=types.SimpleNamespace(account_id="a",target_leads_per_day=None,max_cost_per_lead_rub=None,
        daily_budget_limit_rub=400,bid_autopilot=None,lead_temperature=None,hard_max_bid_rub=None,goal_auto_enabled=None)
    return ns["set_kpi_settings"],db,req,events,HTTPError

def test_actual_boundary_capture_before_write_result_before_existing_commit(monkeypatch):
    fn,db,req,ev,err=boundary(monkeypatch)
    result=fn(req,types.SimpleNamespace(id=17))
    assert result["status"]=="ok"
    assert ev==["capture","baseline","flush","refresh","result","commit","audit","close"]
    assert json.loads(db.row.value)["daily_budget_authorization"]["authorized_by_user_id"]==17

def test_actual_unauthorized_has_no_write_or_telemetry(monkeypatch):
    fn,db,req,ev,err=boundary(monkeypatch)
    with pytest.raises(err) as e:fn(req,types.SimpleNamespace(id=18))
    assert e.value.status_code==403 and ev==["close"]
    assert json.loads(db.row.value)=={"daily_budget_limit_rub":10}

def test_actual_nonbudget_toggle_no_budget_obligation(monkeypatch):
    fn,db,req,ev,err=boundary(monkeypatch)
    req.daily_budget_limit_rub=None;req.bid_autopilot=True
    result=fn(req,types.SimpleNamespace(id=17))
    assert "capture" not in ev and "result" not in ev and ev.count("commit")==1
    assert result["settings"]["daily_budget_limit_rub"]==10


def test_capture_and_gap_sink_failure_are_observational(runtime,monkeypatch):
    def broken(*a,**k):raise RuntimeError("diagnostic unavailable")
    monkeypatch.setattr(H,"capture_owner_budget_context",broken)
    monkeypatch.setattr(H,"_gap",broken)
    result=H.safe_capture_owner_budget_context(DB(),account_id="a",authorization={})
    assert result["capture_failed"] is True and len(runtime[2])==2

def test_result_and_gap_sink_failure_preserve_existing_pending(runtime,monkeypatch):
    db=DB();c=capture(db)
    def broken(*a,**k):raise RuntimeError("diagnostic unavailable")
    monkeypatch.setattr(H,"_gap",broken)
    result=H.observe_owner_budget_result(db,dict(c,bindings=[]),stored_settings={},observed_at=datetime.now(timezone.utc).isoformat())
    assert result["state"]=="NOT_PROVEN" and result["diagnostic_stored"] is False
    assert len(db.executions)==2 and all(x.actual["evaluation_status"]=="PENDING" for x in db.executions.values())

def test_triple_diagnostic_failure_does_not_escape(runtime,monkeypatch):
    def broken(*a,**k):raise RuntimeError("unavailable")
    monkeypatch.setattr(H,"capture_owner_budget_context",broken);monkeypatch.setattr(H,"_gap",broken)
    monkeypatch.setattr(sys.modules["app.services.exception_observability"],"observe_suppressed",broken)
    import logging
    with monkeypatch.context() as local:
        local.setattr(logging,"getLogger",broken)
        result=H.safe_capture_owner_budget_context(DB(),account_id="a",authorization={})
    assert result["capture_failed"] is True

def test_past_readback_time_preserved_and_due_incident_resolved(runtime):
    db=DB();c=capture(db)
    settings={"daily_budget_limit_rub":400,"daily_budget_authorization":c["input_facts"]["authorization"]}
    H.record_owner_budget_result(db,c,stored_settings=settings,observed_at=c["observed_at"])
    values=[x[1] for x in runtime[0] if x[0]=="evidence" and x[1]["level"]=="L3"]
    assert all(x["payload"]["source_observed_at"]==c["observed_at"] for x in values)
    assert len([x for x in runtime[0] if x[0]=="resolved"])==2

def test_failed_capture_gap_does_not_rebind_after_publication(runtime):
    db=DB();f=facts()
    gapcontext={"account_id":"a","event_id":H._hash(["a",17,f["authorization"]]),"event_source":H.EVENT_SOURCE,"capture_failed":True}
    db.gaps=types.SimpleNamespace(details_json={"context":gapcontext})
    c=H.capture_owner_budget_context(db,account_id="a",actor_user_id=17,account_owner_user_id=17,
        is_platform_owner=False,authorization=f["authorization"],requested_budget=400)
    assert c==gapcontext and not runtime[0]

def test_bounded_recovery_uses_exact_context_and_frozen_time(runtime,monkeypatch):
    db=DB();c=capture(db)
    settings={"daily_budget_limit_rub":400,"daily_budget_authorization":c["input_facts"]["authorization"]}
    gap=types.SimpleNamespace(id=1,account_id="a",state="open",updated_at=datetime.now(timezone.utc),
        details_json={"context":c,"facts":{"stored_settings":settings,"observed_at":c["observed_at"]}})
    db.gaps=[gap]
    result=H.reconcile_owner_budget_rule_gaps(db,now=datetime.now(timezone.utc),limit=2)
    assert result["recovered"]==1 and gap.state=="resolved" and result["business_actions"]==0

def test_recovery_wrong_tenant_does_not_bind_or_record(runtime):
    db=DB();c=capture(db);before=len(runtime[0])
    db.gaps=[types.SimpleNamespace(id=1,account_id="other",updated_at=datetime.now(timezone.utc),
        details_json={"context":c,"facts":{"stored_settings":{},"observed_at":c["observed_at"]}})]
    result=H.reconcile_owner_budget_rule_gaps(db,now=datetime.now(timezone.utc),limit=2)
    assert result["recovered"]==0 and len(runtime[0])==before

def test_future_readback_is_rejected(runtime):
    db=DB();c=capture(db)
    with pytest.raises(ValueError,match="TIME_NOT_PROVEN"):
        H.record_owner_budget_result(db,c,stored_settings={},observed_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())


def test_actual_control_incident_columns_match_adapter():
    source=BACKEND/"app/models/control_plane.py"
    incident=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=="ControlIncident")
    columns={target.id for n in incident.body if isinstance(n,ast.Assign) for target in n.targets if isinstance(target,ast.Name)}
    tree=ast.parse((ROOT/"app/services/control_owner_budget_rule_facts.py").read_text())
    references={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=="ControlIncident"}
    assert references <= columns
