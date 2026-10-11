import contextlib
import datetime as dt
import importlib.util
import pathlib
import sqlite3
import ast
import sys
from unittest import mock

import pytest

import os
from app.api import admin_clients as _runtime_admin_clients
TARGET = pathlib.Path(os.environ.get("BORIS_ADMIN_CLIENTS_UNDER_TEST", _runtime_admin_clients.__file__))
spec = importlib.util.spec_from_file_location("app.api.admin_clients", TARGET)
m = importlib.util.module_from_spec(spec)
sys.modules["app.api.admin_clients"] = m
spec.loader.exec_module(m)
import app.api
app.api.admin_clients = m
NOW = dt.datetime(2026, 10, 8, 12, tzinfo=dt.timezone.utc)


class Result:
    def __init__(self, value): self.value = value
    def fetchone(self): return self.value
    def first(self): return self.value
    def mappings(self): return self
    def scalar(self): return self.value
    def fetchall(self): return self.value
    def all(self): return self.value


class NoWriteDB:
    def __init__(self, pilot=None, heartbeat=None):
        self.pilot = pilot
        self.heartbeat = heartbeat
        self.queries = []
    def execute(self, stmt, params=None):
        sql = str(stmt); self.queries.append((sql,params))
        assert sql.lstrip().lower().startswith("select"), "Read projection attempted write"
        if "key=:k" in sql:
            assert params["a"] == "tenant_a" and params["k"] == "client_pilot_v1"
            return Result((self.pilot,) if self.pilot is not None else None)
        if "reliability_heartbeats" in sql:
            return Result(self.heartbeat)
        if "max(stored_at)" in sql:
            assert params == {"a": "tenant_a"}
            return Result(NOW)
        if "from account_slots" in sql:
            assert params == {"a": "tenant_a"}
            return Result({"status":"connected","sync_status":"ok","last_sync_at":NOW})
        raise AssertionError("Unexpected SQL: "+sql[:120])
    def close(self): pass
    def commit(self): raise AssertionError("Projection attempted commit")
    def begin_nested(self): return contextlib.nullcontext()


@pytest.mark.parametrize("key", ["starts_at", "started_at"])
def test_both_persisted_start_contracts_support_scheduled_state(key):
    assert m._pilot_state({key:"2026-10-09T12:00:00Z","expires_at":"2026-10-16T12:00:00Z"}, NOW) == "SCHEDULED"


@pytest.mark.parametrize("payload,expected",[
    ({}, "NOT_STARTED"),
    ({"starts_at":"2026-10-06T12:00:00Z","expires_at":"2026-10-13T12:00:00Z"}, "ACTIVE"),
    ({"starts_at":"2026-10-01T12:00:00Z","expires_at":"2026-10-08T12:00:00Z"}, "EXPIRED"),
    ({"starts_at":"2026-10-02T12:00:00Z","expires_at":"2026-10-09T12:00:00Z"}, "ENDING_SOON"),
    ({"starts_at":"2026-10-10T12:00:00Z","expires_at":"2026-10-09T12:00:00Z"}, "UNKNOWN"),
    ({"starts_at":"invalid","expires_at":"invalid"}, "UNKNOWN"),
    ({"status":"CONVERTED_TO_PAID","expires_at":"2026-10-01T00:00:00Z"}, "CONVERTED_TO_PAID"),
    ({"status":"PAUSED","expires_at":"2026-10-01T00:00:00Z"}, "PAUSED"),
    ({"status":"CANCELLED","expires_at":"2026-10-01T00:00:00Z"}, "CANCELLED"),
])
def test_pilot_boundary_states(payload,expected):
    assert m._pilot_state(payload,NOW) == expected


def test_existing_maria_projection_preserves_exact_clock_without_writes():
    payload={"status":"ACTIVE","starts_at":"2026-10-06T12:56:24.344040+00:00",
             "expires_at":"2026-10-13T12:56:24.344040+00:00",
             "services":["mop","ecs","crm","reactivation","campaign"],
             "source":"reconciled_existing_pilot"}
    db=NoWriteDB(payload)
    out=m._pilot_projection(db,"tenant_a",now=NOW)
    assert out["state"]=="ACTIVE" and out["active"]
    assert out["expires_at"]==payload["expires_at"] and out["source"]==payload["source"]
    assert payload["status"]=="ACTIVE" and len(db.queries)==1


@pytest.mark.parametrize("dry", [True,False])
def test_missing_billing_dependency_blocks_before_any_session_or_write(dry):
    cap={"available":False,"code":"PILOT_BILLING_RUNTIME_UNAVAILABLE","missing":["activate_owner_pilot"],"message":"dependency missing"}
    with mock.patch.object(m,"_pilot_mutation_capability",return_value=cap), \
         mock.patch.object(m,"SessionLocal",side_effect=AssertionError("No DB before unavailable gate")):
        with pytest.raises(m.HTTPException) as err:
            m.client_pilot(m.ClientPilotBody(user_id=1,reactivation_messages=17,dry_run=dry),user=mock.Mock(id=1))
    assert err.value.status_code==503
    assert err.value.detail["code"]=="PILOT_BILLING_RUNTIME_UNAVAILABLE"


@pytest.mark.parametrize("state",["ACTIVE","ENDING_SOON","EXPIRED","CANCELLED","PAUSED","CONVERTED_TO_PAID","UNKNOWN"])
@pytest.mark.parametrize("dry",[True,False])
def test_existing_pilot_is_never_granted_again_without_explicit_restart(state,dry):
    current={"configured":True,"active":state in {"ACTIVE","ENDING_SOON"},"state":state,"expires_at":"2026-10-01T12:00:00Z"}
    db=NoWriteDB()
    runtime={"services":{"mop":{"active":True},"ecs":{"active":True},"campaign":{"active":False}}}
    with mock.patch.object(m,"_pilot_mutation_capability",return_value={"available":True}), \
         mock.patch.object(m,"SessionLocal",return_value=db), \
         mock.patch.object(m,"_card",return_value={"user":{"id":1},"accounts":[{"account_id":"tenant_a"}]}), \
         mock.patch.object(m,"_module_provision_targets",return_value=([{"account_id":"tenant_a"}],[])), \
         mock.patch.object(m,"_pilot_projection",return_value=current), \
         mock.patch.object(m,"_account_runtime",return_value=runtime):
        result=m.client_pilot(m.ClientPilotBody(user_id=1,reactivation_messages=17,dry_run=dry),user=mock.Mock(id=1))
    assert db.queries==[]
    if dry:
        assert result["accounts"][0]["blocked_repeat"]
        assert not result["accounts"][0]["campaign_will_enable"]
    else:
        assert not result["errors"]
        assert "повторного начисления" in result["done"][0]


def test_frontend_payload_and_explicit_restart_alias_are_understood():
    body=m.ClientPilotBody(user_id=63,days=7,services=["mop","ecs","crm","reactivation","campaign"],mop_messages=2000,reactivation_messages=17,renew=False,dry_run=True)
    assert len(body.services)==5 and body.reactivation_messages==17 and not body.renew and not body.restart
    assert m.ClientPilotBody(user_id=63,restart=True).restart


@pytest.mark.parametrize("saved_mode",["auto","manual","recommend"])
def test_actual_pilot_reactivation_upsert_preserves_existing_mode(saved_mode):
    tree=ast.parse(TARGET.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="client_pilot")
    sql=next(n.value for n in ast.walk(node) if isinstance(n,ast.Constant) and isinstance(n.value,str)
             and "insert into reactivation_settings" in n.value)
    db=sqlite3.connect(":memory:")
    db.create_function("now",0,lambda:"2026-10-08T12:00:00")
    db.execute("create table reactivation_settings (account_id text primary key, enabled bool, mode text, daily_limit int, max_attempts_per_dialog int, allowed_hours_from int, allowed_hours_to int, timezone text, created_at text, updated_at text)")
    db.execute("insert into reactivation_settings(account_id,enabled,mode,updated_at) values(?,?,?,?)",("tenant_a",0,saved_mode,"before"))
    db.execute(sql,{"a":"tenant_a"})
    enabled,mode,updated=db.execute("select enabled,mode,updated_at from reactivation_settings where account_id='tenant_a'").fetchone()
    assert enabled==1 and mode==saved_mode and updated!="before"
    db.execute(sql,{"a":"tenant_b"})
    assert db.execute("select mode from reactivation_settings where account_id='tenant_b'").fetchone()[0]=="recommend"


def test_foreign_supervisor_snapshot_is_not_accepted():
    with mock.patch("app.services.client_supervisor.load_supervisor_snapshot",return_value={"account_id":"tenant_b","checked_at":NOW.isoformat()}), \
         mock.patch("app.services.client_supervisor.build_client_state",side_effect=AssertionError("No cross-tenant recovery")):
        with pytest.raises(ValueError,match="account_mismatch"):
            m._client_supervisor_state(NoWriteDB(),"tenant_a")


@pytest.mark.parametrize("health,owner_required,expected",[
    ("healthy",False,True),("degraded",True,False),("blocked",True,False),("NOT_PROVEN",False,False)
])
def test_global_poller_does_not_hide_canonical_provider_block(health,owner_required,expected):
    now=dt.datetime.now(dt.timezone.utc)
    db=NoWriteDB(heartbeat=("ok",now))
    state={"account_id":"tenant_a","modules":[{"module":"mop","health":health,"owner_action_required":owner_required,
             "classification":"MOP_PROVIDER_PARTIAL_402" if health=="degraded" else None}]}
    out=m._messenger_runtime_projection(db,"tenant_a",supervisor=state)
    assert out["messages_ok"] is expected and out["runtime_source"]=="client_supervisor"


def test_lifecycle_import_contract_is_available():
    import app.services.client_pilot_lifecycle
    from app.api.admin_clients import _pilot_projection
    assert _pilot_projection is m._pilot_projection


def test_runtime_service_gates_preserve_expiry_and_reactivation_mode():
    class SalesDB:
        def execute(self, stmt,params):
            assert params=={"a":"tenant_a"}
            return Result({"mop_enabled":True,"auto_send":False})
    state={"account_id":"tenant_a","modules":[{"module":"mop","health":"healthy"},{"module":"reactivation","health":"off"}]}
    with mock.patch("app.services.subscription_gate.base_subscription_access",return_value={"state":"expired","allowed":False}), \
         mock.patch("app.services.control_plane_adapters_ext.marketing_service_entitlement",return_value={"state":"expired"}), \
         mock.patch("app.client_config.entitlements.has_entitlement",return_value={"allowed":False,"state":"known"}), \
         mock.patch("app.reactivation_core.settings_for",return_value={"enabled":True,"mode":"manual"}), \
         mock.patch("app.reactivation_core.reactivation_balance",return_value={"active":False,"block":"period_over","left":100}):
        result=m._service_projection(SalesDB(),"tenant_a",supervisor=state)
    assert not result["mop"]["active"] and not result["mop"]["auto_send"]
    assert not result["reactivation"]["active"] and result["reactivation"]["mode"]=="manual"
    assert not result["campaign"]["active"] and result["campaign"]["blocked_reason"]=="product_access_not_active"
    assert result["campaign"]["e2e_verified"] is False


class AtomicStore:
    def __init__(self):
        self.committed={
            "billing":{"paid_until":"2027-01-01T00:00:00Z","rop_minutes":1500},
            "mop_crm_sales_settings":{"enabled":False,"mop_enabled":False,"auto_send":False},
            "reactivation":{"enabled":False,"mode":"manual"},
            "rights":{"mop":False,"ecs":False,"rop":True},
        }
        self.commits=0; self.rollbacks=0; self.calls=[]


class AtomicDB:
    def __init__(self,store):
        import copy
        self.store=store; self.working=copy.deepcopy(store.committed)
        self.ids={1:"mop_crm_sales_settings",2:"client_pilot_v1"}
    def execute(self,stmt,params=None):
        import json
        sql=str(stmt); params=params or {}
        if "from accounts" in sql and "for update" in sql:
            assert "owner_user_id=:u" in sql and params["u"]==1
            return Result(None if getattr(self.store,"failure",None)=="ownership" else (params["a"],))
        if "select value from storage" in sql:
            return Result(self.working.get("mop_crm_sales_settings"))
        if "select id from storage" in sql:
            key=params["k"]
            found=next((i for i,k in self.ids.items() if k==key),3)
            return Result((found,) if key in self.working else None)
        if "update storage" in sql:
            self.working[self.ids[params["i"]]]=json.loads(params["v"]); return Result(None)
        if "insert into storage" in sql:
            self.working[params["k"]]=json.loads(params["v"]); return Result(None)
        if "insert into reactivation_settings" in sql:
            self.working["reactivation"]["enabled"]=True; return Result(None)
        raise AssertionError("Unexpected mutation SQL "+sql[:150])
    def flush(self): pass
    def commit(self):
        import copy
        self.store.committed=copy.deepcopy(self.working); self.store.commits+=1
    def rollback(self):
        import copy
        self.working=copy.deepcopy(self.store.committed); self.store.rollbacks+=1
    def close(self): pass
    def begin_nested(self): return contextlib.nullcontext()


def atomic_pilot_run(failure=None, existing_paid_mop=False):
    import copy
    store=AtomicStore()
    store.failure=failure
    store.readbacks=[]
    if existing_paid_mop:
        store.committed["rights"]["mop"]=True
        store.committed["billing"]["manager_paid_until"]="2027-02-01T00:00:00Z"
        store.committed["billing"]["manager_msgs_purchased"]=8000
    before=copy.deepcopy(store.committed)
    def activate(aid,*,db,commit,**kwargs):
        assert commit is False
        assert kwargs["services"]==["mop","ecs","crm","reactivation","campaign"]
        assert kwargs["mop_messages"]==2000 and kwargs["reactivation_messages"]==17
        store.calls.append("activate")
        now=dt.datetime.now(dt.timezone.utc)
        starts=now+dt.timedelta(days=2) if failure=="future_clock" else now
        expires=now-dt.timedelta(days=1) if failure=="backward_clock" else now+dt.timedelta(days=7)
        pilot={"contract_version":2,"pilot_id":"isolated-admin-pilot","active":True,
               "services":list(kwargs["services"]),"starts_at":starts.isoformat(),
               "expires_at":expires.isoformat(),
               "limits":{"mop_requests":2000,"mop_sends":2000,"reactivation_messages":17,
                         "listings":100,"banners":100,"ai_templates":100,"images":1000},
               "usage":{}}
        db.working["billing"]["owner_pilot"]=pilot
        if failure=="activation":
            raise RuntimeError("activation failure after staged metadata write")
        return {"status":"activated","pilot":pilot}
    def forbidden_paid_grant(aid,*,db,commit,**kwargs):
        raise AssertionError("Pilot must not invoke paid MOP/ECS grants")
    def entitlement(db,aid,module):
        assert aid=="tenant_a"
        store.readbacks.append(module)
        if (failure=="unconfirmed_mop" and module=="mop") or (failure=="ecs" and module=="ecs"):
            return {"allowed":False}
        pilot=db.working["billing"].get("owner_pilot") or {}
        return {"allowed":db.working["rights"].get(module,False) or module in pilot.get("services",[])}
    functions={"activate_owner_pilot":activate,"add_manager_package":forbidden_paid_grant,
               "add_ecs_package":forbidden_paid_grant}
    runtime={"services":{}}
    actual_pilot_state=m._pilot_state
    clock_calls=0
    def clock_projection(pilot,now=None):
        nonlocal clock_calls
        clock_calls+=1
        if failure=="expired_before_commit" and clock_calls==2:
            now=m._utc_dt(pilot["expires_at"])+dt.timedelta(microseconds=1)
        return actual_pilot_state(pilot,now=now)
    with mock.patch.object(m,"_pilot_mutation_capability",return_value={"available":True}), \
         mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch.object(m,"SessionLocal",side_effect=lambda:AtomicDB(store)), \
         mock.patch.object(m,"_card",return_value={"user":{"id":1},"accounts":[{"account_id":"tenant_a"}]}), \
         mock.patch.object(m,"_module_provision_targets",return_value=([{"account_id":"tenant_a"}],[])), \
         mock.patch.object(m,"_pilot_projection",return_value={"configured":False,"state":"NOT_STARTED"}), \
         mock.patch.object(m,"_account_runtime",return_value=runtime), \
         mock.patch.object(m,"_pilot_state",side_effect=clock_projection), \
         mock.patch("app.client_config.entitlements.has_entitlement",side_effect=entitlement):
        result=m.client_pilot(m.ClientPilotBody(user_id=1,reactivation_messages=17,dry_run=False),user=mock.Mock(id=1))
    return result,store,before



@pytest.mark.parametrize("failure",["activation","ecs","unconfirmed_mop","ownership","future_clock","backward_clock","expired_before_commit"])
def test_failed_canonical_pilot_step_rolls_back_all_grants_and_settings(failure):
    result,store,before=atomic_pilot_run(failure)
    assert result["status"]=="partial" and result["errors"] and not result["done"]
    assert store.committed==before and store.commits==0 and store.rollbacks==1
    assert "client_pilot_v1" not in store.committed
    if failure in {"activation","future_clock","backward_clock"}:
        assert store.calls==["activate"]
    if failure=="ownership":
        assert store.calls==[]
    if failure in {"unconfirmed_mop","ecs","expired_before_commit"}:
        assert store.calls==["activate"]
    if failure=="unconfirmed_mop":
        assert store.readbacks==["mop"]
    if failure=="ecs":
        assert store.readbacks==["mop","ecs"]


def test_successful_pilot_commits_once_after_all_grants_and_preserves_paid_settings():
    result,store,before=atomic_pilot_run(existing_paid_mop=True)
    assert result["status"]=="ok" and not result["errors"]
    assert store.commits==1 and store.rollbacks==0
    assert store.calls==["activate"]
    assert store.readbacks==["mop","ecs","crm","reactivation","campaign"]
    assert store.committed["rights"]==before["rights"]
    assert {k:v for k,v in store.committed["billing"].items() if k!="owner_pilot"}==before["billing"]
    assert store.committed["billing"]["owner_pilot"]["limits"]["reactivation_messages"]==17
    assert store.committed["billing"]["owner_pilot"]["limits"]["mop_requests"]==2000
    assert store.committed["billing"]["paid_until"]==before["billing"]["paid_until"]
    assert store.committed["billing"]["manager_paid_until"]=="2027-02-01T00:00:00Z"
    assert store.committed["billing"]["manager_msgs_purchased"]==8000
    assert store.committed["reactivation"]["mode"]=="manual"
    assert store.committed["mop_crm_sales_settings"]["auto_send"] is False
    assert store.committed["client_pilot_v1"]["status"]=="ACTIVE"


def test_capability_rejects_self_committing_helper_even_when_present():
    def activate(aid,*,db,commit): pass
    def old_mop(aid,amount=2000,days=7): pass
    with mock.patch.object(m,"_pilot_provisioners",return_value={"activate_owner_pilot":activate,"add_manager_package":old_mop}), \
         mock.patch("app.api.billing.owner_pilot_state",new=lambda data: {},create=True):
        capability=m._pilot_mutation_capability(["mop"])
    assert capability["available"] is False
    assert capability["non_transactional"]==["add_manager_package"]
    assert capability["blocks_existing_services"] is False


def test_capability_checks_only_requested_modules():
    def activate(aid,*,db,commit): pass
    with mock.patch.object(m,"_pilot_provisioners",return_value={"activate_owner_pilot":activate}) as functions, \
         mock.patch("app.api.billing.owner_pilot_state",new=lambda data: {},create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",new=lambda **kw:_activation_contract_report(kw["services"]),create=True):
        capability=m._pilot_mutation_capability(["campaign"])
    functions.assert_called_once_with(["campaign"])
    assert capability["available"] is True


@pytest.mark.parametrize("services,configured",[
    (["campaign"],False),(["mop","ecs"],True),
])
def test_preview_never_promises_unrequested_or_blocked_module_grants(services,configured):
    db=NoWriteDB()
    with mock.patch.object(m,"_pilot_mutation_capability",return_value={"available":True}), \
         mock.patch.object(m,"SessionLocal",return_value=db), \
         mock.patch.object(m,"_card",return_value={"user":{"id":1},"accounts":[{"account_id":"tenant_a"}]}), \
         mock.patch.object(m,"_module_provision_targets",return_value=([{"account_id":"tenant_a"}],[])), \
         mock.patch.object(m,"_pilot_projection",return_value={"configured":configured,"state":"EXPIRED" if configured else "NOT_STARTED"}), \
         mock.patch.object(m,"_account_runtime",return_value={"services":{}}):
        result=m.client_pilot(m.ClientPilotBody(user_id=1,services=services,dry_run=True),user=mock.Mock(id=1))
    assert not result["accounts"][0]["mop_will_change"]
    assert not result["accounts"][0]["ecs_will_change"]


def _activation_contract_report(services):
    return {
        "contract":"boris.owner_pilot.activation","version":1,"ready":True,
        "services":list(services),
        "checks":{
            "account_atomic_grants":True,"product_scoped_trial_access":True,
            "bounded_quota_consumption":True,"expiry_preserves_paid_rights":True,
            "paid_conversion_scope_readback":True,
        },
    }


def _mechanical_pilot_providers():
    """Use exact operator-v2 functions offline, equivalent fail-if-called interfaces staged."""
    root=os.environ.get("BORIS_PILOT_MECHANICAL_FIXTURE_ROOT")
    if root:
        base=pathlib.Path(root)
        exports={}
        for rel,names in {
            "billing.py":{"activate_owner_pilot","add_ecs_package","owner_pilot_state"},
            "messenger.py":{"add_manager_package"},
            "calltracking.py":{"add_rop_package"},
        }.items():
            source=(base/"app/api"/rel).read_text()
            tree=ast.parse(source)
            assert not any(isinstance(n,ast.FunctionDef) and n.name=="owner_pilot_activation_contract" for n in tree.body)
            namespace={}
            selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
            exec(compile(ast.Module(body=selected,type_ignores=[]),"mechanical-v2:"+rel,"exec"),namespace)
            exports.update({name:namespace[name] for name in names})
        return {name:fn for name,fn in exports.items() if name!="owner_pilot_state"},exports["owner_pilot_state"]
    def activate(account_id: str,*,days: int=7,limits=None,source: str="client_pilot_v1",
                 actor_user_id=None,renew: bool=False,db=None,commit: bool=True):
        raise AssertionError("No activation before full canonical contract")
    def mop(account_id: str,amount: int,days: int=0,*,db=None,commit: bool=True):
        raise AssertionError("No MOP grant before full canonical contract")
    def ecs(account_id: str,days: int=30,*,db=None,commit: bool=True):
        raise AssertionError("No ECS grant before full canonical contract")
    def rop(account_id: str,minutes: int=0,chats: int=0,rep_calls: int=0,
            rep_chats: int=0,days: int=0,*,db=None,commit: bool=True):
        raise AssertionError("No ROP grant before full canonical contract")
    return {"activate_owner_pilot":activate,"add_manager_package":mop,
            "add_ecs_package":ecs,"add_rop_package":rop},lambda data: {}


@pytest.mark.parametrize("dry",[True,False])
def test_partial_billing_v2_interfaces_still_return_503_before_any_db(dry):
    functions,project=_mechanical_pilot_providers()
    with mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch("app.api.billing.owner_pilot_state",new=project,create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",new=None,create=True), \
         mock.patch.object(m,"SessionLocal",side_effect=AssertionError("No DB before full contract")):
        with pytest.raises(m.HTTPException) as err:
            m.client_pilot(m.ClientPilotBody(user_id=1,reactivation_messages=17,dry_run=dry),user=mock.Mock(id=1))
    assert err.value.status_code==503
    detail=err.value.detail
    assert detail["non_transactional"]==[]
    assert detail["missing"]==["owner_pilot_activation_contract"]
    assert detail["activation_contract"]["reason"]=="not_implemented"
    assert detail["scope"]=="pilot_start" and detail["severity"]=="warning"
    assert detail["blocks_existing_services"] is False


@pytest.mark.parametrize("field,value,reason",[
    ("contract","other.contract","contract_version_mismatch"),
    ("version",True,"contract_version_mismatch"),
    ("version","1","contract_version_mismatch"),
    ("version",2,"contract_version_mismatch"),
    ("ready",1,"canonical_contract_not_ready"),
    ("ready","true","canonical_contract_not_ready"),
    ("ready",False,"canonical_contract_not_ready"),
    ("services",["mop"],"incomplete_selected_contract"),
    ("services","campaign","incomplete_selected_contract"),
    ("checks",{},"incomplete_selected_contract"),
])
def test_incomplete_or_incompatible_activation_contract_does_not_enable_mutation(field,value,reason):
    functions,project=_mechanical_pilot_providers()
    report=_activation_contract_report(["campaign"]);report[field]=value
    with mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch("app.api.billing.owner_pilot_state",new=project,create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",new=lambda **kw:report,create=True), \
         mock.patch.object(m,"SessionLocal",side_effect=AssertionError("Capability probe must not open DB")):
        result=m._pilot_mutation_capability(["campaign"])
    assert not result["available"] and result["activation_contract"]["reason"]==reason
    assert result["blocks_existing_services"] is False


@pytest.mark.parametrize("report",[None,[],True])
def test_invalid_activation_contract_response_is_fail_closed(report):
    functions,project=_mechanical_pilot_providers()
    with mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch("app.api.billing.owner_pilot_state",new=project,create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",new=lambda **kw:report,create=True):
        out=m._pilot_mutation_capability(["campaign"])
    assert not out["available"] and out["activation_contract"]["reason"]=="invalid_response"


def test_failed_activation_contract_probe_is_visible_and_fail_closed():
    functions,project=_mechanical_pilot_providers()
    with mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch("app.api.billing.owner_pilot_state",new=project,create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",side_effect=RuntimeError("unavailable"),create=True):
        out=m._pilot_mutation_capability(["campaign"])
    assert not out["available"] and out["activation_contract"]["reason"]=="handshake_error:RuntimeError"


def test_truthy_check_does_not_replace_verified_true():
    functions,project=_mechanical_pilot_providers()
    report=_activation_contract_report(["campaign"])
    report["checks"]["bounded_quota_consumption"]=1
    with mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch("app.api.billing.owner_pilot_state",new=project,create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",new=lambda **kw:report,create=True):
        out=m._pilot_mutation_capability(["campaign"])
    assert not out["available"]
    assert out["activation_contract"]["missing_checks"]==["bounded_quota_consumption"]


def test_future_complete_contract_handshake_is_scoped_and_read_only():
    functions,project=_mechanical_pilot_providers()
    def handshake(*,services):
        assert services==["campaign","mop"]
        return _activation_contract_report(services)
    with mock.patch.object(m,"_pilot_provisioners",return_value=functions), \
         mock.patch("app.api.billing.owner_pilot_state",new=project,create=True), \
         mock.patch("app.api.billing.owner_pilot_activation_contract",new=handshake,create=True), \
         mock.patch.object(m,"SessionLocal",side_effect=AssertionError("Readiness opens no DB")):
        out=m._pilot_mutation_capability(["mop","campaign","mop"])
    assert out["available"] and out["activation_contract"]["ready"]
    assert out["activation_contract"]["missing_services"]==out["activation_contract"]["missing_checks"]==[]


@pytest.mark.parametrize("dry",[True,False])
def test_selected_reactivation_without_explicit_quota_is_422_before_any_session(dry):
    with mock.patch.object(m,"SessionLocal",side_effect=AssertionError("No DB for undefined quota")), \
         mock.patch.object(m,"_pilot_mutation_capability",side_effect=AssertionError("Validate quota before capability")):
        with pytest.raises(m.HTTPException) as err:
            m.client_pilot(m.ClientPilotBody(user_id=1,dry_run=dry),user=mock.Mock(id=1))
    assert err.value.status_code==422
    assert err.value.detail=="reactivation_quota_required_no_default"
