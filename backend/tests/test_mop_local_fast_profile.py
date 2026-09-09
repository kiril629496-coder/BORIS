import inspect

from app.api import messenger
from app.services import sales_ai_router


def test_mop_local_fallback_uses_bounded_fast_profile():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    assert "MOP_LOCAL_COMPACT_PROMPT_V3" in src
    assert 'BORIS_MOP_LOCAL_NUM_CTX' in src
    assert '"1280"' in src
    assert 'BORIS_MOP_LOCAL_NUM_PREDICT' in src
    assert '"56"' in src
    assert "history_text[-600:]" in src
    assert "learning_priority_block[:320]" in src
    assert "company_context[:250]" in src
    assert "confirmed_memory_context[:350]" in src
    assert "local_format=None" in src
    assert "Без JSON" in src


def test_full_remote_prompt_and_local_prompt_are_separate():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    assert "system_prompt = (" in src
    assert "_local_prompt_text = (" in src
    assert "_prompt_text = system_prompt + _style_block(style_hint)" in src
    assert "_local_router_prompt = _local_prompt_text + _style_block(style_hint)" in src
    assert "local_prompt=_local_router_prompt" in src


def test_router_uses_local_prompt_only_for_ollama():
    src = inspect.getsource(sales_ai_router.generate_text)
    assert 'local_prompt: str | None = None' in src
    assert 'local_prompt = str(local_prompt or prompt).strip() or prompt' in src
    assert '"prompt": local_prompt if provider == "ollama" else prompt' in src
