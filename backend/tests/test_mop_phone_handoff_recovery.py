import inspect

from app.api import messenger, mop_combat_training
from app.services import brain_recovery


def test_phone_detector_accepts_real_russian_phone_formats():
    assert messenger._mop_text_has_phone("+7 999 123-45-67")
    assert messenger._mop_text_has_phone("89991234567")
    assert messenger._mop_text_has_phone("9991234567")


def test_phone_detector_rejects_non_phone_numbers():
    assert not messenger._mop_text_has_phone("бюджет 15000000 рублей")
    assert not messenger._mop_text_has_phone("объект 123456789")
    assert not messenger._mop_text_has_phone("2026-09-09")


def test_phone_handoff_is_account_rule_driven():
    assert messenger._mop_handoff_rules_require_phone(["получен телефон"])
    assert messenger._mop_handoff_rules_require_phone(["клиент оставил номер"])
    assert messenger._mop_handoff_rules_require_phone(["номер получен"])
    assert not messenger._mop_handoff_rules_require_phone(["клиент просит человека"])
    assert not messenger._mop_handoff_rules_require_phone(["требуется нестандартное решение"])


def test_live_generation_has_zero_ai_phone_handoff_fast_path():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    assert "MOP_PHONE_HANDOFF_RULE_V1" in src
    assert "_mop_text_has_phone(_question)" in src
    assert "_mop_phone_handoff_enabled_for_account(account_id)" in src
    assert '"human_handoff": True' in src
    assert '"cost_rub": 0.0' in src


def test_crm_phone_recovery_is_db_only_and_idempotent_by_source():
    src = inspect.getsource(brain_recovery._safe_crm_assignment_phone_handoff_recovery)
    assert "CRM_ASSIGNMENT_PHONE_HANDOFF_RECOVERY_V1" in src
    assert "assigned_user_id IS NULL" in src
    assert "mop_phone_handoff_recovery" in src
    assert "client_supervisor_snapshot_v1" in src
    assert "SET LOCAL jit=off" in src
    assert "brain_phone_handoff_evidence" in src
    assert "human_followup" in src
    assert "send_message(" not in src
    assert "generate_ai" not in src
    assert "provider call" in src.lower()


def test_safe_recovery_runner_contains_assignment_phone_handoff():
    src = inspect.getsource(brain_recovery.run_safe_recovery)
    assert "crm_assignment_phone_handoff" in src
    assert "_safe_crm_assignment_phone_handoff_recovery" in src


def test_training_phone_handoff_precedes_first_contact_fast_stage():
    src = inspect.getsource(mop_combat_training.sparring_turn)
    phone_pos = src.index("MOP_TRAINING_PHONE_HANDOFF_PRIORITY_V1")
    first_contact_pos = src.index("if generated is None and not _has_previous_mop")
    assert phone_pos < first_contact_pos
    assert "_mop_text_has_phone(body.message.strip())" in src
    assert "_mop_phone_handoff_enabled_for_account(account_id)" in src
    assert "generate_ai_draft_reply(" in src
    assert '"analysis":_analysis' in src
    assert '"human_handoff":bool' in src
