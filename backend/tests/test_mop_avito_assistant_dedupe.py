import inspect

from app.api import messenger


def test_avito_assistant_guard_runs_before_generation():
    src = inspect.getsource(messenger._mop_incoming)
    guard = src.index("_mop_close_if_avito_assistant_already_replied(")
    generation = src.index("generate_ai_draft_reply(")
    assert guard < generation
    assert "avito_assistant_already_replied" in src


def test_avito_assistant_guard_rechecks_before_external_send():
    src = inspect.getsource(messenger._mop_incoming)
    generation = src.index("generate_ai_draft_reply(")
    second_guard = src.index("_mop_close_if_avito_assistant_already_replied(", generation)
    send = src.index("_mc.do_send(", second_guard)
    assert generation < second_guard < send
    assert "MOP_AVITO_ASSISTANT_RACE_BLOCK" in src
