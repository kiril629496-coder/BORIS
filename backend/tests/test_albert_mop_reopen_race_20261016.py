"""Offline Albert MOP regression: exact buyer-turn time and missing-card route."""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"


def _isolated_function(path, name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    code = compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec")
    exec(code, namespace)
    return namespace[name]


def test_provider_v3_incoming_has_exact_epoch_and_ignores_system_nudge():
    from copy import deepcopy
    raw = {"status": "ok", "messages": [
        {"direction": "in", "id": "buyer-52", "created": 110, "type": "text",
         "content": {"text": "Рассчитайте стеклопакет, размеры уже отправлял"}},
    ]}
    ns = {
        "fetch_chat_messages": lambda *_: deepcopy(raw),
        "_AVITO_STUB_MARKERS": (),
        "_SYSTEM_NUDGE_PREFIX": "[Системное сообщение]",
    }
    fn = _isolated_function(ROOT / "api" / "messenger.py", "_real_last_incoming", ns)
    assert fn("albert", "chat") == {
        "id": "buyer-52", "text": "Рассчитайте стеклопакет, размеры уже отправлял", "created_at": 110,
    }
    raw["messages"][0]["content"]["text"] = "[Системное сообщение] Клиент открыл чат"
    assert fn("albert", "chat") is None


class _Query:
    def fetchone(self):
        return ("new", "")


class _DB:
    def execute(self, *_args, **_kwargs):
        return _Query()


def _run_buyer_turn(owner_reason, buyer_created_at, seller_created_at):
    transitions = []
    ns = {
        "human_takeover_state": lambda *_: {
            "active": True, "reason": owner_reason, "at_epoch": seller_created_at,
        },
        "create_draft": lambda *_: (12345, True),
        "set_status": lambda _db, _did, to, *_a, **_kw: (transitions.append(to) or object(), None),
        "deterministic_handoff_reason": lambda _: None,
        "deterministic_no_reply_reason": lambda _: None,
        "text": lambda q: q,
    }
    fn = _isolated_function(ROOT / "mop_core.py", "begin_incoming", ns)
    result = fn(_DB(), "albert", "chat", "buyer-52", "Можно рассчитать?", ai_enabled=True,
                incoming_created_at=buyer_created_at)
    return result, transitions


def test_later_buyers_reopen_mop_after_unattributed_seller_turn():
    result, events = _run_buyer_turn("external_human_outgoing", 110, 100)
    assert result["need_generate"] is True
    assert result["status"] == "analyzing"
    assert events == ["analyzing"]


def test_old_turn_does_not_override_human_reply():
    result, events = _run_buyer_turn("external_human_outgoing", 90, 100)
    assert result["need_generate"] is False
    assert result["status"] == "human_required"
    assert events == ["human_required"]


def test_explicit_employee_take_remains_sticky():
    result, events = _run_buyer_turn("employee_take", 110, 100)
    assert result["need_generate"] is False
    assert result["status"] == "human_required"
    assert events == ["human_required"]


def test_call_chain_propagates_provider_epoch_to_mop():
    tree = ast.parse((ROOT / "api" / "messenger.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_mop_incoming")
    assert any(isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "begin_incoming"
               and any(k.arg == "incoming_created_at" for k in n.keywords)
               for n in ast.walk(fn))
    source = (ROOT / "api" / "messenger.py").read_text(encoding="utf-8")
    assert 'incoming_created_at=_real.get("created_at")' in source
    assert "_exact_inbound_epoch = _provider_inbound_epoch or _current_inbound_epoch" in source


def test_missing_telegram_route_logs_once_even_when_repolled():
    fake_app = types.ModuleType("app")
    fake_tg = types.ModuleType("app.telegram_bot")
    fake_tg._telegram_post = lambda *_a, **_kw: None
    fake_tg.API_BASE = "https://invalid.example"
    orig_app = sys.modules.get("app")
    orig_tg = sys.modules.get("app.telegram_bot")
    sys.modules["app"] = fake_app
    sys.modules["app.telegram_bot"] = fake_tg
    try:
        state = {"events": [], "commits": 0}
        class Result:
            def first(self):
                return (1,) if state["events"] else None
        class Db:
            def execute(self, *_args, **_kwargs):
                return Result()
            def commit(self):
                state["commits"] += 1
        ns = {
            "_draft": lambda *_: {"account_id": "albert"},
            "target_for": lambda *_: None,
            "text": lambda s: s,
            "ROUTE_MISSING": "route_missing",
            "log_event": lambda *_a, **_kw: state["events"].append(_kw.get("payload")),
        }
        fn = _isolated_function(ROOT / "mop_core.py", "push_card", ns)
        assert fn(Db(), 12345) == "route_missing"
        assert fn(Db(), 12345) == "route_missing"
        assert state["events"] == ["route_missing"]
        assert state["commits"] == 1
    finally:
        if orig_app is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = orig_app
        if orig_tg is None:
            sys.modules.pop("app.telegram_bot", None)
        else:
            sys.modules["app.telegram_bot"] = orig_tg
