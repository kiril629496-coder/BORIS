import inspect
from unittest.mock import patch

from app.services import sales_ai_router
from app.api import messenger


PLANETA = "planetazayavki_65985"


def test_planeta_mop_is_openai_only_when_ready():
    with patch.object(sales_ai_router, "_openai_billing_blocked", return_value=False), \
         patch.object(sales_ai_router, "_circuit_blocks", return_value=False), \
         patch.dict("os.environ", {"BORIS_SALES_OPENAI_ENABLED": "1", "OPENAI_API_KEY": "test"}, clear=False):
        assert sales_ai_router.provider_order(PLANETA, module="mop") == ["openai"]


def test_planeta_mop_has_no_fallback_when_openai_blocked():
    with patch.object(sales_ai_router, "_openai_billing_blocked", return_value=True), \
         patch.dict("os.environ", {"BORIS_SALES_OPENAI_ENABLED": "1", "OPENAI_API_KEY": "test"}, clear=False):
        assert sales_ai_router.provider_order(PLANETA, module="mop") == []


def test_other_mop_accounts_keep_existing_policy():
    with patch.dict("os.environ", {
        "BORIS_SALES_DEEPSEEK_ENABLED": "0",
        "BORIS_MOP_GIGACHAT_ENABLED": "0",
        "BORIS_MOP_GEMINI_ENABLED": "0",
        "BORIS_SALES_LOCAL_ENABLED": "1",
    }, clear=False):
        assert sales_ai_router.provider_order("another_account", module="mop") == ["ollama"]


def test_planeta_fail_closed_guard_present():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    assert "PLANETA_OPENAI_ONLY_FAIL_CLOSED_V1" in src
    assert 'str(account_id or "") == "planetazayavki_65985"' in src


def test_planeta_snapshot_is_in_trusted_context_and_prompt():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    assert "planeta_knowledge_context" in src
    assert "_trusted_business_context" in src
    helper = inspect.getsource(messenger._planeta_mop_knowledge_snapshot_context)
    assert "mop_knowledge_snapshot_v1" in helper
    assert "reference_knowledge:furniture_catalog" in helper
    assert "reference_knowledge:furniture_hardware" in helper
    assert "reference_knowledge:ral_furniture" in helper
    assert "feed_direction_profile:%" in helper


def test_openai_recovery_probe_uses_explicit_proxy_pool():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/ext_api/aiprov.py").read_text(encoding="utf-8")
    assert "get_intl_requests_proxies" in src
    assert "proxies=_openai_proxies" in src


def test_reactivation_passes_stable_cycle_intent_to_canonical_mop():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/reactivation_draft.py").read_text(encoding="utf-8")
    assert "REACTIVATION_MOP_IDEMPOTENCY_V1" in src
    assert "cycle_no, attempts, last_analyzed_message_id" in src
    assert "idempotency_key=_reactivation_intent" in src
    assert "planeta_openai_required_unavailable" in src


def test_handoff_recovery_dedupes_generic_crm_followup():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/brain_recovery.py").read_text(encoding="utf-8")
    assert "CRM_HANDOFF_TASK_CROSS_SOURCE_DEDUPE_V1" in src
    assert "source='brain_auto_recovery' AND title='Связаться с клиентом'" in src
    assert "SET source='mop_handoff_recovery'" in src
    assert "brain_handoff_cross_source_dedupe:" in src
    assert "MOP_HUMAN_HANDOFF_MANAGER_TASK_V2" in src
