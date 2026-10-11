"""Pilot 13-file ordinary release acceptance: real staged imports, no production IO.

Copy this file to backend/tests/test_clients510_pilot_service_only_runtime.py.
Run with the staged backend first on PYTHONPATH. It intentionally rejects imports
from /outputs or another release for every affected module.
"""
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import datetime as dt
import pytest

STAGED_BACKEND = Path(__file__).resolve().parents[1]
AFFECTED_MODULES = ('app.api.admin_clients', 'app.api.banners', 'app.api.billing', 'app.api.calltracking', 'app.api.campaigns', 'app.api.inbox_slots', 'app.api.messenger', 'app.api.payments', 'app.client_config.entitlements', 'app.product_limits', 'app.services.control_plane_adapters_ext', 'app.services.subscription_gate', 'app.services.client_pilot_lifecycle')

@pytest.mark.parametrize("module_name", AFFECTED_MODULES)
def test_affected_module_imported_from_this_staged_backend(module_name):
    module = importlib.import_module(module_name)
    source = Path(inspect.getsourcefile(module)).resolve()
    assert source.is_relative_to(STAGED_BACKEND), (module_name, source, STAGED_BACKEND)

def test_service_only_activation_is_closed_by_actual_protected_auth(monkeypatch):
    from app.api import billing, auth
    assert not hasattr(auth, "owner_pilot_route_contract"), "Ordinary release must not silently stage protected auth"
    contract = billing.owner_pilot_activation_contract(services=["mop", "ecs", "campaign"])
    assert not contract["ready"] and not contract["route_adapter_ready"]
    assert contract["notification_adapter_ready"]
    assert contract["blocked_dependency"] == "app.api.auth.owner_pilot_route_contract"
    assert not any(contract["checks"].values())
    factory = Mock(side_effect=AssertionError("closed activation must not open DB"))
    monkeypatch.setattr(billing, "SessionLocal", factory)
    with pytest.raises(RuntimeError, match="pilot_route_adapter_not_installed"):
        billing.activate_owner_pilot("qa-never-created", services=["mop"])
    factory.assert_not_called()

def test_service_only_reactivation_has_actual_missing_adapter_contracts(monkeypatch):
    from app.api import billing, auth
    import app.reactivation_core as core
    import app.reactivation_runtime as runtime
    assert not hasattr(core, "owner_pilot_reactivation_core_contract")
    assert not hasattr(runtime, "owner_pilot_reactivation_runtime_contract")
    # Simulate future auth-only activation: the two real old adapters must keep
    # selected reactivation closed. This patch is in-memory only for this test.
    monkeypatch.setattr(auth, "owner_pilot_route_contract", lambda: {
        "contract": "boris.owner_pilot.route_access", "version": 2,
        "product_scoped": True, "tenant_checks_preserved": True}, raising=False)
    contract = billing.owner_pilot_activation_contract(services=["mop", "reactivation"])
    assert contract["route_adapter_ready"] and not contract["reactivation_adapter_ready"]
    assert not contract["ready"]
    factory = Mock(side_effect=AssertionError("closed activation must not open DB"))
    monkeypatch.setattr(billing, "SessionLocal", factory)
    with pytest.raises(RuntimeError, match="pilot_route_adapter_not_installed"):
        billing.activate_owner_pilot("qa-never-created", services=["mop", "reactivation"],
                                     reactivation_messages=3)
    factory.assert_not_called()

class FakeQuery:
    def __init__(self, value): self.value = value
    def filter(self, *args): return self
    def first(self): return SimpleNamespace(value=json.dumps(self.value))

class FakeDB:
    def __init__(self, value): self.value = value; self.closed = False
    def query(self, model): return FakeQuery(self.value)
    def close(self): self.closed = True

@pytest.mark.parametrize("legacy_trial", [False, True])
def test_real_paid_and_legacy_mop_balance_response_contract(monkeypatch, legacy_trial):
    from app.api import messenger, calltracking
    from app.services import subscription_gate
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=40)).isoformat()
    db = FakeDB({"manager_msgs_purchased": 3, "manager_used_base": 0, "manager_paid_until": future})
    monkeypatch.setattr(calltracking, "_is_owner_account", lambda a: False)
    monkeypatch.setattr(messenger, "_manager_total_calls", lambda *a, **k: 5)
    base = {"allowed": not legacy_trial, "state": "active" if not legacy_trial else "expired", "paid_until": future}
    trial = {"allowed": legacy_trial, "state": "active" if legacy_trial else "missing", "until": future if legacy_trial else None}
    monkeypatch.setattr(subscription_gate, "base_subscription_access", lambda *a, **k: base)
    monkeypatch.setattr(messenger, "_legacy_mop_trial_access", lambda *a: trial)
    result = messenger.get_manager_balance("isolated-account", db=db)
    assert result["left"] == -2 and not result["active"]
    assert result["billing_period_invalid"] is False
    assert result["base_subscription_active"] is (not legacy_trial)
    assert result["base_paid_until"] == future
    assert result["mop_trial_state"] == trial["state"]
    assert result["mop_trial_until"] == trial["until"]
    assert not db.closed

def test_real_owner_balance_uses_canonical_authority(monkeypatch):
    from app.api import messenger, calltracking
    checker = Mock(return_value=True)
    monkeypatch.setattr(calltracking, "_is_owner_account", checker)
    factory = Mock(side_effect=AssertionError("owner balance must not open DB"))
    monkeypatch.setattr(messenger, "SessionLocal", factory)
    result = messenger.get_manager_balance("isolated-owner")
    assert result["unlimited"] and result["active"] and result["left"] == 999999
    checker.assert_called_once_with("isolated-owner")
    factory.assert_not_called()

def test_real_ecs_sender_does_not_depend_on_mop_balance(monkeypatch):
    from app.api import messenger
    from app.services import subscription_gate
    monkeypatch.setattr(messenger, "SessionLocal", lambda: FakeDB({}))
    monkeypatch.setattr(subscription_gate, "base_subscription_access", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(messenger, "_mop_client_visible_text", lambda s: (s, False))
    monkeypatch.setattr(messenger, "_mop_confirmed_send_context", lambda *a: "")
    monkeypatch.setattr(messenger, "_mop_output_policy_violations", lambda *a: [])
    balance = Mock(side_effect=AssertionError("ECS must not read MOP"))
    reserve = Mock(side_effect=AssertionError("ECS must not reserve MOP"))
    token = Mock(return_value=(None, None))
    monkeypatch.setattr(messenger, "get_manager_balance", balance)
    monkeypatch.setattr(messenger, "_mop_owner_pilot_reserve", reserve)
    monkeypatch.setattr(messenger, "_get_user_id_and_token", token)
    result = messenger.send_message("isolated-account", "chat", "Здравствуйте", product="ecs")
    assert result["status"] == "error" and "Avito" in result["message"]
    balance.assert_not_called(); reserve.assert_not_called()
    token.assert_called_once_with("isolated-account")

def test_real_paid_entitlement_survives_expired_pilot(monkeypatch):
    from app.client_config import entitlements
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=40)).isoformat()
    # Invalid/legacy pilot metadata must not erase independent current paid MOP.
    billing = {"manager_msgs_purchased": 1700, "manager_paid_until": future,
               "owner_pilot": {"enabled": True, "expires_at": "2020-01-01"}}
    result = entitlements.decide("mop", billing)
    assert result["allowed"] and result["source"] == "storage:billing"
    assert result["evidence"]["product_period_active"] is True

def test_real_lifecycle_adapter_retains_existing_scheduler_contract():
    from app.services import client_pilot_lifecycle
    contract = client_pilot_lifecycle.owner_pilot_notification_contract()
    assert contract["version"] == 2 and contract["existing_scheduler"]
    assert contract["durable_event_idempotency"]
    assert set(contract["events"]) == {"ENDING_SOON", "EXPIRED", "CONVERTED_TO_PAID"}

def test_real_exhausted_paid_mop_charges_finite_pilot_under_lock(monkeypatch):
    from app.api import billing, messenger, calltracking
    from app.services import subscription_gate
    now = dt.datetime.now(dt.timezone.utc)
    data = {"tier":"tariff_1", "paid_until":(now+dt.timedelta(days=30)).isoformat(),
            "manager_paid_until":(now+dt.timedelta(days=30)).isoformat(),
            "manager_msgs_purchased":1, "manager_used_base":0,
            "owner_pilot":{"contract_version":2,"pilot_id":"staged-isolated",
                          "enabled":True,"status":"ACTIVE","services":["mop"],
                          "starts_at":(now-dt.timedelta(days=1)).isoformat(),
                          "expires_at":(now+dt.timedelta(days=1)).isoformat(),
                          "limits":{"mop_requests":1,"mop_sends":1},"usage":{}}}
    class Result:
        def __init__(self,value):self.value=value
        def first(self):return self.value
        def fetchone(self):return self.value
    class Query(FakeQuery):
        def populate_existing(self):return self
        def with_for_update(self):return self
        def all(self):return [self.first()]
    class DB(FakeDB):
        def __init__(self,value):super().__init__(value);self.intents={};self.locks=0
        def query(self,model):return Query(self.value)
        def execute(self,sql,params=None):
            q=str(sql).lower();p=params or {}
            if "from accounts" in q:self.locks+=1;return Result(("isolated",))
            if "select amount,result_json" in q:return Result(self.intents.get((p["a"],p["u"],p["k"])))
            if "insert into boris_billing_usage_intents" in q:
                self.intents[(p["a"],p["u"],p["k"])]=(p["n"],json.loads(p["r"]))
                return Result(None)
            raise AssertionError("unexpected SQL boundary: "+q)
        def commit(self):return None
        def rollback(self):raise AssertionError("unexpected rollback")
    db=DB(data)
    monkeypatch.setattr(billing,"_load_billing",lambda *a,**k:data)
    monkeypatch.setattr(billing,"_save_billing",lambda a,change,**k:data.update(change))
    monkeypatch.setattr(messenger,"_manager_total_calls",lambda *a,**k:1)
    monkeypatch.setattr(messenger,"_legacy_mop_trial_access",lambda *a:{"allowed":False,"state":"missing"})
    monkeypatch.setattr(calltracking,"_is_owner_account",lambda *a:False)
    monkeypatch.setattr(subscription_gate,"base_subscription_access",lambda *a,**k:{"allowed":True,"state":"active"})
    first=billing.consume_owner_pilot_unit("isolated","mop_requests",idempotency_key="one",db=db)
    repeat=billing.consume_owner_pilot_unit("isolated","mop_requests",idempotency_key="one",db=db)
    second=billing.consume_owner_pilot_unit("isolated","mop_requests",idempotency_key="two",db=db)
    assert first["allowed"] and first["charged"] and first["owner_pilot"]
    assert repeat["idempotency_replay"] and repeat["used"]==1
    assert not second["allowed"] and second["reason"]=="owner_pilot_limit_exhausted"
    assert data["owner_pilot"]["usage"]["mop_requests"]==1 and db.locks==3
