# -*- coding: utf-8 -*-
import pytest
from unittest.mock import patch

from app.services import sales_ai_router as R


def _isolate(monkeypatch, *, gemini="0", local="0"):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")
    monkeypatch.setenv("BORIS_SALES_DEEPSEEK_ENABLED", "1")
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", gemini)
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", local)
    monkeypatch.setattr(R, "_deepseek_provider_blocked", lambda: False)
    monkeypatch.setattr(R, "_gemini_daily_quota_blocks", lambda: False)
    monkeypatch.setattr(R, "_gemini_rate_limited_blocked", lambda: False)
    monkeypatch.setattr(R, "_circuit_blocks", lambda *a, **k: False)


def test_deepseek_api_key_alone_does_not_enable_paid_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")
    monkeypatch.delenv("BORIS_SALES_DEEPSEEK_ENABLED", raising=False)
    monkeypatch.setenv("BORIS_SALES_GEMINI_ENABLED", "0")
    monkeypatch.setenv("BORIS_SALES_LOCAL_ENABLED", "0")
    monkeypatch.setattr(R, "_deepseek_provider_blocked", lambda: False)
    monkeypatch.setattr(R, "_circuit_blocks", lambda *a, **k: False)
    assert R.provider_order("a") == []


def test_provider_order_includes_deepseek_without_openai(monkeypatch):
    _isolate(monkeypatch)
    assert R.provider_order("a") == ["deepseek"]


def test_mop_uses_flash_and_rop_uses_pro(monkeypatch):
    _isolate(monkeypatch)
    seen = []

    def ds(**kw):
        seen.append((kw.get("model"), kw.get("module"), kw.get("expect_json")))
        return {"text": '{"ok":true}', "provider": "deepseek", "model": kw.get("model")}

    mop = R.generate_text(
        account_id="a", operation="mop_messenger_reply", prompt="json reply",
        module="mop", expect_json=True, deepseek_call=ds,
    )
    rop = R.generate_text(
        account_id="a", operation="rop_chat_analysis", prompt="json analysis",
        module="rop", expect_json=True, deepseek_call=ds,
    )
    assert mop["provider"] == "deepseek"
    assert rop["provider"] == "deepseek"
    assert seen[0] == ("deepseek-v4-flash", "mop", True)
    assert seen[1] == ("deepseek-v4-pro", "rop", True)


def test_deepseek_failure_falls_to_gemini(monkeypatch):
    _isolate(monkeypatch, gemini="1")

    def ds(**kw):
        raise R.SalesAIProviderUnavailable("deepseek rate_limit http=429")

    out = R.generate_text(
        account_id="a", operation="rop_chat_analysis", prompt="p", module="rop",
        deepseek_call=ds,
        gemini_call=lambda **kw: {"text": "ok", "provider": "gemini", "model": kw.get("model")},
    )
    assert out["provider"] == "gemini"
    assert out["fallback_chain"][0]["provider"] == "deepseek"


def test_deepseek_mop_request_disables_thinking_and_enables_json(monkeypatch):
    _isolate(monkeypatch)
    captured = {}

    class Resp:
        status_code = 200
        def json(self):
            return {
                "id": "ds-1",
                "choices": [{"message": {"content": '{"reply_text":"ok"}'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 40,
                    "prompt_cache_miss_tokens": 60,
                },
            }

    def post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, body=json, timeout=timeout)
        return Resp()

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(R, "dependency_call", lambda dep, fn, **kw: fn())
    monkeypatch.setattr("app.usage.log_usage", lambda **kw: 0.1234)
    from app.ext_api import aiprov
    monkeypatch.setattr(aiprov, "mark", lambda *a, **k: {})

    out = R._deepseek_default_call(
        account_id="a", operation="mop_messenger_reply", prompt="return json",
        model="deepseek-v4-flash", timeout=10, max_output_tokens=300,
        module="mop", expect_json=True,
    )
    assert out["provider"] == "deepseek"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == "deepseek-v4-flash"
    assert captured["headers"]["Authorization"].startswith("Bearer ")


def test_deepseek_rop_request_enables_high_reasoning(monkeypatch):
    _isolate(monkeypatch)
    captured = {}

    class Resp:
        status_code = 200
        def json(self):
            return {
                "id": "ds-2",
                "choices": [{"message": {"content": "analysis"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    def post(url, headers, json, timeout):
        captured["body"] = json
        return Resp()

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(R, "dependency_call", lambda dep, fn, **kw: fn())
    monkeypatch.setattr("app.usage.log_usage", lambda **kw: 0.1)
    from app.ext_api import aiprov
    monkeypatch.setattr(aiprov, "mark", lambda *a, **k: {})

    R._deepseek_default_call(
        account_id="a", operation="rop_call_analysis", prompt="analyze",
        model="deepseek-v4-pro", timeout=10, max_output_tokens=500,
        module="rop", expect_json=False,
    )
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["reasoning_effort"] == "high"
    assert "temperature" not in captured["body"]


def test_deepseek_402_sets_long_billing_fence(monkeypatch):
    _isolate(monkeypatch)
    marks = []

    class Resp:
        status_code = 402
        def json(self):
            return {"error": {"type": "insufficient_balance", "message": "balance"}}

    monkeypatch.setattr("requests.post", lambda *a, **k: Resp())
    monkeypatch.setattr(R, "dependency_call", lambda dep, fn, **kw: fn())
    from app.ext_api import aiprov
    monkeypatch.setattr(aiprov, "mark", lambda *a, **k: marks.append((a, k)) or {})

    with pytest.raises(R.SalesAIProviderUnavailable) as exc:
        R._deepseek_default_call(
            account_id="a", operation="mop_messenger_reply", prompt="p",
            model="deepseek-v4-flash", timeout=5, max_output_tokens=100,
            module="mop", expect_json=False,
        )
    assert "billing" in str(exc.value)
    assert marks
    args, kwargs = marks[-1]
    assert args[0] == "deepseek"
    assert args[1] == aiprov.UNAVAILABLE_BILLING
    assert int(kwargs["retry_after"]) >= 3600
