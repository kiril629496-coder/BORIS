# -*- coding: utf-8 -*-
import datetime
from unittest.mock import patch

import pytest

from app.services import sales_ai_router as R

_REAL_GEMINI_DAILY_QUOTA_BLOCKS = R._gemini_daily_quota_blocks


@pytest.fixture(autouse=True)
def _isolate_live_gemini_daily_quota(monkeypatch):
    # Unit tests must not inherit today's production provider credentials/quota.
    # Runtime provider behavior is verified separately against real services.
    monkeypatch.setattr(R, "_gemini_daily_quota_blocks", lambda: False)
    monkeypatch.setattr(R, "_openai_billing_blocked", lambda: False)
    monkeypatch.setenv("BORIS_SALES_GIGACHAT_ENABLED", "0")


def _ok(provider):
    return lambda **kw: {
        "text": '{"reply":"ok"}',
        "provider": provider,
        "model": kw.get("model"),
        "request_id": provider + "-1",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "cost_rub": 0 if provider != "openai" else 1.23,
    }


def test_openai_first_when_usable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("BORIS_SALES_OPENAI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    with patch.object(R, "_circuit_blocks", return_value=False):
        calls = []
        def oa(**kw):
            calls.append("openai")
            return _ok("openai")(**kw)
        out = R.generate_text(
            account_id="a", operation="t", prompt="p",
            openai_call=oa,
            gemini_call=lambda **kw: (_ for _ in ()).throw(AssertionError("gemini called")),
            local_call=lambda **kw: (_ for _ in ()).throw(AssertionError("local called")),
        )
    assert out["provider"] == "openai"
    assert calls == ["openai"]


def test_openai_open_skips_to_gemini(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    def blocked(dep, account):
        return dep == "openai.text"
    with patch.object(R, "_circuit_blocks", side_effect=blocked):
        out = R.generate_text(
            account_id="a", operation="t", prompt="p",
            openai_call=lambda **kw: (_ for _ in ()).throw(AssertionError("openai called")),
            gemini_call=_ok("gemini"),
            local_call=_ok("ollama"),
        )
    assert out["provider"] == "gemini"


def test_openai_quota_falls_to_gemini(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with patch.object(R, "_circuit_blocks", return_value=False):
        def oa(**kw):
            raise R.SalesAIProviderUnavailable("openai http 429 quota")
        out = R.generate_text(
            account_id="a", operation="t", prompt="p",
            openai_call=oa, gemini_call=_ok("gemini"), local_call=_ok("ollama"),
        )
    assert out["provider"] == "gemini"
    assert out["fallback_chain"][0]["provider"] == "openai"


def test_openai_budget_block_falls_to_gemini(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with patch.object(R, "_circuit_blocks", return_value=False):
        def oa(**kw):
            raise R.SalesAIBudgetBlocked("budget")
        out = R.generate_text(
            account_id="a", operation="t", prompt="p",
            openai_call=oa, gemini_call=_ok("gemini"), local_call=_ok("ollama"),
        )
    assert out["provider"] == "gemini"


def test_gemini_failure_falls_to_local(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.object(R, "_circuit_blocks", return_value=False):
        def g(**kw):
            raise R.SalesAIProviderUnavailable("gemini quota")
        out = R.generate_text(
            account_id="a", operation="t", prompt="p",
            gemini_call=g, local_call=_ok("ollama"),
        )
    assert out["provider"] == "ollama"
    assert out["fallback_chain"][0]["provider"] == "gemini"


def test_all_provider_outage_preserves_diagnostic_chain(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    with patch.object(R, "_circuit_blocks", return_value=False):
        def g(**kw):
            raise R.SalesAIProviderUnavailable("gemini quota/circuit")
        def local(**kw):
            raise R.SalesAIProviderUnavailable("local MOP inference busy; retry via provider recovery")
        with pytest.raises(R.SalesAIUnavailable) as exc:
            R.generate_text(
                account_id="a", operation="mop_messenger_reply", prompt="p",
                gemini_call=g, local_call=local,
            )
    assert [x["provider"] for x in exc.value.chain] == ["gemini", "ollama"]
    assert exc.value.chain[0]["reason"] == "SalesAIProviderUnavailable"
    assert "quota/circuit" in exc.value.chain[0]["detail"]
    assert "inference busy" in exc.value.chain[1]["detail"]


def test_gigachat_credentials_never_change_sales_fallback_policy(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GIGACHAT_ENABLED", "1")
    monkeypatch.setenv("GIGACHAT_KEY", "test-key")
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    with patch.object(R, "_circuit_blocks", return_value=False):
        assert R.provider_order("a") == ["gemini", "ollama"]


def test_status_exposes_exact_three_provider_policy(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    with patch.object(R, "_circuit_blocks", return_value=False),          patch.object(R, "_circuit_snapshot", return_value={}),          patch.object(R, "readiness", return_value={"ready": True, "order": ["gemini", "ollama"]}):
        out = R.status("a")
    assert out["policy"] == "openai_if_usable_then_free_gemini_then_local"
    assert "gigachat_circuit" not in out
    assert "gigachat" not in out["models"]
    assert out["order"] == ["gemini", "ollama"]


def test_expect_json_extracts(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.object(R, "_circuit_blocks", return_value=False):
        out = R.generate_text(
            account_id="a", operation="t", prompt="p", expect_json=True,
            gemini_call=lambda **kw: {"text": '{"x": 7}'},
            local_call=_ok("ollama"),
        )
    assert out["json"] == {"x": 7}


def test_gigachat_credentials_never_receive_live_mop_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "0")
    monkeypatch.setenv("BORIS_SALES_GIGACHAT_ENABLED", "1")
    monkeypatch.setenv("GIGACHAT_KEY", "test-key")
    with patch.object(R, "_circuit_blocks", return_value=False):
        out = R.generate_text(
            account_id="a",
            operation="mop_messenger_reply",
            prompt="p",
            expect_json=True,
            gigachat_call=lambda **kw: (_ for _ in ()).throw(
                AssertionError("gigachat must not receive sales traffic")
            ),
            local_call=lambda **kw: {
                "text": '{"reply_text":"Локальный ответ","human_handoff":false,"handoff_reason":null}',
                "provider": "ollama",
                "model": "qwen3:4b",
            },
        )
    assert out["provider"] == "ollama"
    assert out["json"]["reply_text"] == "Локальный ответ"
    assert out["json"]["human_handoff"] is False


def test_provider_order_expired_openai_circuit_allows_probe(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    expired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    with patch.object(R, "_circuit_snapshot", return_value={
        "openai.text": {"state": "open", "opened_until": expired, "failures": 5}
    }):
        assert R.provider_order("a")[0] == "openai"


def test_provider_order_future_openai_circuit_skips(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
    def snap(dep, account):
        if dep == "openai.text":
            return {"openai.text": {"state": "open", "opened_until": future, "failures": 5}}
        return {}
    with patch.object(R, "_circuit_snapshot", side_effect=snap):
        assert R.provider_order("a")[0] == "gemini"


def test_openai_credit_balance_429_is_billing_not_short_quota():
    assert R._provider_error_kind(429, "credit_balance_exhausted") == "billing"


def test_provider_order_skips_openai_while_billing_latch_cools(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    with patch.object(R, "_openai_billing_blocked", return_value=True),          patch.object(R, "_circuit_blocks", return_value=False):
        assert R.provider_order("a") == ["gemini", "ollama"]


def test_openai_credit_balance_failure_sets_long_billing_latch(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("BORIS_SALES_OPENAI_BILLING_RETRY_SEC", "21600")

    class Resp:
        status_code = 429
        headers = {}
        def json(self):
            return {"error": {"code": "credit_balance_exhausted"}}

    monkeypatch.setattr("requests.post", lambda *a, **k: Resp())
    monkeypatch.setattr(R, "dependency_call", lambda dep, fn, **kw: fn())

    from app.services import ai_budget
    monkeypatch.setattr(ai_budget, "reserve", lambda **kw: {"status": "reserved"})
    monkeypatch.setattr(ai_budget, "allow_next", lambda run_id: {"allowed": True})
    monkeypatch.setattr(ai_budget, "release", lambda *a, **k: None)
    monkeypatch.setattr(ai_budget, "stop", lambda *a, **k: None)

    from app.ext_api import aiprov
    marks = []
    monkeypatch.setattr(
        aiprov,
        "mark",
        lambda provider, state, note=None, retry_after=None: marks.append(
            (provider, state, note, retry_after)
        ) or {"provider": provider, "state": state},
    )

    with pytest.raises(R.SalesAIProviderUnavailable) as exc:
        R._openai_default_call(
            account_id="a",
            operation="mop_messenger_reply",
            prompt="p",
            model="gpt-test",
            max_output_tokens=32,
            idempotency_key="billing-test",
            module="mop",
            timeout=5,
        )

    assert "billing" in str(exc.value)
    assert marks
    assert marks[-1][0] == "openai"
    assert marks[-1][1] == aiprov.UNAVAILABLE_BILLING
    assert "credit_balance_exhausted" in str(marks[-1][2])
    assert int(marks[-1][3]) >= 3600


def test_one_paid_call_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with patch.object(R, "_circuit_blocks", return_value=False):
        count = {"openai": 0}
        def oa(**kw):
            count["openai"] += 1
            raise R.SalesAIProviderUnavailable("quota")
        out = R.generate_text(
            account_id="a", operation="t", prompt="p",
            openai_call=oa, gemini_call=_ok("gemini"), local_call=_ok("ollama"),
        )
    assert count["openai"] == 1
    assert out["provider"] == "gemini"


def test_openai_validation_error_does_not_mask_caller_bug(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with patch.object(R, "_circuit_blocks", return_value=False):
        def oa(**kw):
            raise R.SalesAIValidationError("bad request")
        with pytest.raises(R.SalesAIValidationError):
            R.generate_text(
                account_id="a", operation="t", prompt="p",
                openai_call=oa, gemini_call=_ok("gemini"), local_call=_ok("ollama"),
            )


def test_invalid_openai_json_falls_to_free_gemini(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with patch.object(R, "_circuit_blocks", return_value=False):
        out = R.generate_text(
            account_id="a", operation="t", prompt="p", expect_json=True,
            openai_call=lambda **kw: {"text": "not-json", "provider": "openai"},
            gemini_call=lambda **kw: {"text": '{"x": 9}', "provider": "gemini"},
            local_call=_ok("ollama"),
        )
    assert out["provider"] == "gemini"
    assert out["json"] == {"x": 9}
    assert out["fallback_chain"][0]["provider"] == "openai"
    assert out["fallback_chain"][0]["reason"] == "invalid_output"


def test_gemini_cli_quota_error_is_safe_shared_marker(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "HTTP 429 quota exceeded"

    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: Proc())
    monkeypatch.setattr(R, "dependency_call", lambda dep, fn, **kw: fn())

    with pytest.raises(R.SalesAIProviderUnavailable) as exc:
        R._gemini_default_call(
            account_id="a",
            operation="mop_messenger_reply",
            prompt="SECRET CUSTOMER PROMPT",
            model="gemini-test",
            timeout=5,
        )
    assert str(exc.value) == "gemini shared_quota_429"
    assert "SECRET CUSTOMER PROMPT" not in str(exc.value)


def test_gemini_cli_uses_deny_all_admin_policy(monkeypatch, tmp_path):
    captured = {}

    class Proc:
        returncode = 0
        stdout = '{"response":"ok"}'
        stderr = ""

    def fake_run(argv, cwd, capture_output, text, timeout, env):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        policy = argv[argv.index("--admin-policy") + 1]
        captured["policy"] = open(policy, encoding="utf-8").read()
        return Proc()

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    monkeypatch.setattr(R, "dependency_call", lambda dep, fn, **kw: fn())
    monkeypatch.setattr("app.usage.log_usage", lambda *a, **k: 0.0)

    out = R._gemini_default_call(
        account_id="__qa_sales_router",
        operation="qa",
        prompt="не используй инструменты",
        model="gemini-test",
        timeout=5,
    )
    assert out["provider"] == "gemini"
    assert "--admin-policy" in captured["argv"]
    assert "--approval-mode" in captured["argv"]
    assert "toolName = \"*\"" in captured["policy"]
    assert 'decision = "deny"' in captured["policy"]
    assert "priority = 999" in captured["policy"]


def test_mop_local_inference_guard_is_cross_process_safe(tmp_path, monkeypatch):
    lock_path = tmp_path / "mop-local.lock"
    monkeypatch.setenv("BORIS_SALES_LOCAL_LOCK_PATH", str(lock_path))

    with R._local_inference_guard("mop_messenger_reply"):
        with pytest.raises(R.SalesAIProviderUnavailable):
            with R._local_inference_guard("mop_training_client_turn"):
                pass

    # The lock must be released after the first inference finishes.
    with R._local_inference_guard("mop_training_client_turn"):
        pass

    # Non-MOP local work is not unnecessarily serialized by this guard.
    with R._local_inference_guard("rop_chat_analysis"):
        with R._local_inference_guard("rop_chat_analysis"):
            pass


def test_daily_quota_stays_fenced_after_clock_reset_until_canonical_probe(monkeypatch):
    class _Rows:
        def mappings(self):
            return self
        def first(self):
            return {
                "state": "RATE_LIMITED",
                "note": (
                    "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED "
                    "reset_epoch=1788826200 preserved_from=provider_state"
                ),
            }

    class _DB:
        def execute(self, *args, **kwargs):
            return _Rows()
        def close(self):
            pass

    from app.ext_api import aiprov

    monkeypatch.setattr(R, "SessionLocal", lambda: _DB())
    # Simulate wall clock already at/after reset: parser no longer returns a
    # future reset epoch, but RATE_LIMITED + durable daily marker must stay shut
    # until canonical health probe writes AVAILABLE.
    monkeypatch.setattr(aiprov, "gemini_daily_quota_reset_epoch", lambda note: None)
    monkeypatch.setattr(R, "_gemini_daily_quota_blocks", _REAL_GEMINI_DAILY_QUOTA_BLOCKS)
    monkeypatch.setenv("BORIS_SALES_OPENAI_ENABLED", "0")
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")

    assert R._gemini_daily_quota_blocks() is True
    assert R.provider_order("a") == ["ollama"]


def test_local_structured_contract_is_forwarded(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "0")
    seen = {}
    schema = {
        "type": "object",
        "properties": {"reply_text": {"type": "string"}},
        "required": ["reply_text"],
    }

    def local(**kw):
        seen.update(kw)
        return {"text": '{"reply_text":"ok"}', "provider": "ollama"}

    with patch.object(R, "_circuit_blocks", return_value=False):
        out = R.generate_text(
            account_id="a", operation="mop_messenger_reply", prompt="p",
            expect_json=True,
            local_format=schema,
            local_temperature=0.1,
            local_num_ctx=2048,
            local_num_predict=180,
            local_call=local,
        )
    assert out["provider"] == "ollama"
    assert out["json"]["reply_text"] == "ok"
    assert seen["format_schema"] == schema
    assert seen["temperature"] == 0.1
    assert seen["num_ctx"] == 2048
    assert seen["num_predict"] == 180


def test_local_historical_load_only_uses_recovery_floor(monkeypatch, tmp_path):
    monkeypatch.setenv("BORIS_SALES_LOCAL_LOCK_PATH", str(tmp_path / "mop-local.lock"))
    monkeypatch.setattr(
        "app.ext_api.sched.pressure",
        lambda: {
            "under_pressure": True,
            "reasons": ["load average выше безопасного порога"],
            "instant_headroom": True,
            "historical_load_only": True,
            "facts": {},
        },
    )
    monkeypatch.setattr("app.usage.log_usage", lambda *a, **k: 0.0)

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"response":"ok"}'

    monkeypatch.setattr(R.urllib.request, "urlopen", lambda *a, **k: Response())
    out = R._ollama_default_call(
        account_id="a",
        operation="mop_messenger_reply",
        prompt="p",
        model="qwen3:1.7b",
        timeout=10,
        num_ctx=1024,
        num_predict=32,
    )
    assert out["provider"] == "ollama"
    assert out["model"] == "qwen3:1.7b"


def test_local_real_iowait_pressure_stays_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "app.ext_api.sched.pressure",
        lambda: {
            "under_pressure": True,
            "reasons": [
                "load average выше безопасного порога",
                "слишком высокий CPU iowait",
            ],
            "instant_headroom": True,
            "historical_load_only": False,
            "facts": {},
        },
    )
    with pytest.raises(R.SalesAIProviderUnavailable):
        R._ollama_default_call(
            account_id="a",
            operation="mop_messenger_reply",
            prompt="p",
            model="qwen3:1.7b",
            timeout=10,
            num_ctx=1024,
            num_predict=32,
        )


def test_local_default_model_is_lightweight_emergency_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "0")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    monkeypatch.delenv("BORIS_SALES_LOCAL_MODEL", raising=False)
    seen = {}

    def local(**kw):
        seen.update(kw)
        return {"text": "ok", "provider": "ollama", "model": kw.get("model")}

    with patch.object(R, "_circuit_blocks", return_value=False):
        out = R.generate_text(
            account_id="a", operation="mop_messenger_reply", prompt="p",
            local_call=local,
        )
    assert out["provider"] == "ollama"
    assert seen["model"] == "qwen3:1.7b"


def test_local_model_override_is_preserved(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "0")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_LOCAL_MODEL", "qwen3:4b")
    seen = {}

    def local(**kw):
        seen.update(kw)
        return {"text": "ok", "provider": "ollama", "model": kw.get("model")}

    with patch.object(R, "_circuit_blocks", return_value=False):
        R.generate_text(
            account_id="a", operation="mop_training_client_turn", prompt="p",
            local_call=local,
        )
    assert seen["model"] == "qwen3:4b"


def test_local_ollama_refuses_real_production_pressure(monkeypatch):
    monkeypatch.setattr(
        "app.ext_api.sched.pressure",
        lambda: {"under_pressure": True, "reasons": ["свободной памяти меньше резерва production"]},
    )
    with pytest.raises(R.SalesAIProviderUnavailable) as exc:
        R._ollama_default_call(
            account_id="a",
            operation="mop_messenger_reply",
            prompt="p",
            model="qwen3:4b",
            timeout=120,
        )
    assert "server under pressure" in str(exc.value)


def test_local_ollama_rechecks_transient_pressure_before_deferring(monkeypatch, tmp_path):
    samples = iter([
        {
            "under_pressure": True,
            "reasons": ["CPU занят выше безопасного порога"],
        },
        {
            "under_pressure": False,
            "reasons": [],
        },
    ])
    monkeypatch.setattr("app.ext_api.sched.pressure", lambda: next(samples))
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)
    monkeypatch.setenv("BORIS_SALES_LOCAL_LOCK_PATH", str(tmp_path / "local.lock"))
    monkeypatch.setattr("app.usage.log_usage", lambda *a, **k: 0.0)

    class Resp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"response":"ok"}'

    monkeypatch.setattr(R.urllib.request, "urlopen", lambda req, timeout: Resp())
    out = R._ollama_default_call(
        account_id="a",
        operation="mop_messenger_reply",
        prompt="p",
        model="qwen3:4b",
        timeout=20,
    )
    assert out["provider"] == "ollama"
    assert out["text"] == "ok"


def test_local_ollama_refuses_persistent_transient_pressure(monkeypatch):
    calls = {"n": 0}

    def pressure():
        calls["n"] += 1
        return {
            "under_pressure": True,
            "reasons": ["гипервизор отбирает слишком много CPU"],
        }

    monkeypatch.setattr("app.ext_api.sched.pressure", pressure)
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)
    with pytest.raises(R.SalesAIProviderUnavailable) as exc:
        R._ollama_default_call(
            account_id="a",
            operation="mop_messenger_reply",
            prompt="p",
            model="qwen3:4b",
            timeout=20,
        )
    assert calls["n"] == 2
    assert "server under pressure" in str(exc.value)


def test_local_ollama_caps_timeout_before_urlopen(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.ext_api.sched.pressure",
        lambda: {"under_pressure": False, "reasons": []},
    )
    monkeypatch.setenv("BORIS_SALES_LOCAL_TIMEOUT_MAX", "45")
    monkeypatch.setenv("BORIS_SALES_LOCAL_LOCK_PATH", str(tmp_path / "local.lock"))
    seen = {}

    class Resp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"response":"ok"}'

    def fake_urlopen(req, timeout):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(R.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("app.usage.log_usage", lambda *a, **k: 0.0)
    out = R._ollama_default_call(
        account_id="a",
        operation="mop_messenger_reply",
        prompt="p",
        model="qwen3:4b",
        timeout=120,
    )
    assert seen["timeout"] == 45
    assert out["provider"] == "ollama"
