import inspect
from unittest.mock import patch

from app.services import sales_ai_router as R
from app.api import mop_combat_training


def test_mop_remote_attempt_preserves_local_recovery_budget(monkeypatch):
    seen = {}

    def gemini(**kw):
        seen["gemini_timeout"] = kw["timeout"]
        seen["gemini_prompt"] = kw["prompt"]
        raise R.SalesAIProviderUnavailable("gemini timeout")

    def local(**kw):
        seen["local_timeout"] = kw["timeout"]
        seen["local_prompt"] = kw["prompt"]
        return {
            "text": '{"reply_text":"Локальный ответ","human_handoff":false,"handoff_reason":null}',
            "provider": "ollama",
            "model": kw.get("model"),
            "request_id": "local-test",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "cost_rub": 0.0,
        }

    monkeypatch.setenv("BORIS_SALES_LOCAL_RESERVE_SEC", "30")
    monkeypatch.setenv("BORIS_SALES_REMOTE_ATTEMPT_CAP_SEC", "30")
    with patch.object(R, "provider_order", return_value=["gemini", "ollama"]):
        out = R.generate_text(
            account_id="qa",
            operation="mop_messenger_reply",
            prompt="FULL_REMOTE_PROMPT",
            local_prompt="SHORT_LOCAL_PROMPT",
            timeout=60,
            expect_json=True,
            gemini_call=gemini,
            local_call=local,
        )

    assert out["provider"] == "ollama"
    assert seen["gemini_prompt"] == "FULL_REMOTE_PROMPT"
    assert seen["local_prompt"] == "SHORT_LOCAL_PROMPT"
    assert 5 <= seen["gemini_timeout"] <= 30
    assert seen["local_timeout"] >= 25


def test_training_turn_has_budget_for_remote_and_local_layers():
    src = inspect.getsource(mop_combat_training.sparring_turn)
    assert "MOP_TRAINING_BOUNDED_WAIT_V2" in src
    assert 'BORIS_MOP_TRAINING_TURN_TIMEOUT_SEC", "60"' in src
    assert "max(" in src
    assert "35," in src
    assert "70," in src


def test_router_contains_local_recovery_reserve_contract():
    src = inspect.getsource(R.generate_text)
    assert "SALES_AI_LOCAL_RESERVE_V1" in src
    assert 'BORIS_SALES_LOCAL_RESERVE_SEC' in src
    assert 'BORIS_SALES_REMOTE_ATTEMPT_CAP_SEC' in src
    assert 'provider != "ollama"' in src
