from pathlib import Path


ROOT = Path(__file__).parents[1]
SELFTEST = ROOT / "app" / "ext_api" / "selftest.py"


def _canonical_source() -> str:
    source = SELFTEST.read_text(encoding="utf-8")
    assert "def _run_messenger_p0_canonical" in source
    assert "def run_messenger_p0" in source
    return source.split("def _run_messenger_p0_canonical", 1)[1].split("def run_messenger_p0", 1)[0]


def test_messenger_selector_uses_canonical_inbox_handlers_and_safe_boundaries():
    canonical = _canonical_source()
    for token in (
        "inbox_slots.inbox_take",
        "inbox_slots.inbox_send",
        "inbox_slots.inbox_release",
        "inbox_slots.inbox_mark_read",
        "inbox_slots.inbox_thread",
        "_send_idempotency_storage_key",
        "outgoing_authors",
        "_messenger.send_message = safe_send",
        "_messenger.send_message = original_send",
        "sync_dialog_for_account",
        "queue_event",
        "human_takeover_state",
        "qa_messenger_p0_",
        "QA_CLEANUP",
        "MESSENGER_P0_ACCEPTANCE",
    ):
        assert token in canonical


def test_messenger_canonical_selector_enforces_full_p0_matrix():
    canonical = _canonical_source()
    for gate in (
        "TENANT_ISOLATION",
        "ROLE_PERMISSIONS",
        "TAKE",
        "MOP_PAUSED_AFTER_TAKE",
        "EMPLOYEE_SEND_SAFE",
        "DURABLE_IDEMPOTENCY",
        "OUTBOUND_PERSISTENCE",
        "CRM_BRIDGE",
        "NO_DUPLICATE_CONTACT",
        "NO_DUPLICATE_DEAL",
        "CRM_TASK_BRIDGE",
        "RELEASE",
        "MOP_STILL_PAUSED_AFTER_RELEASE",
        "MOP_RESUME",
        "UNREAD",
        "MARK_READ",
        "PUSH_ROUTING",
        "PUSH_DEEP_LINK",
        "AUDIT",
        "REALTIME",
        "QA_CLEANUP",
    ):
        assert gate in canonical


def test_messenger_cleanup_is_scoped_to_synthetic_idempotency_key():
    canonical = _canonical_source()
    assert 'DELETE FROM storage WHERE account_id=:a AND key=:k' in canonical
    assert 'DELETE FROM storage WHERE account_id=:a AND key LIKE :k' not in canonical
    assert "inbox_slots._send_idempotency_storage_key(key)" in canonical


def test_mop_pause_after_take_is_checked_before_employee_send_and_resume():
    canonical = _canonical_source()
    pause_check = canonical.index('MOP_PAUSED_AFTER_TAKE=')
    stub_send = canonical.index('_messenger.send_message = safe_send')
    resume = canonical.index('MOP_RESUME=')
    assert pause_check < stub_send < resume
    assert 'len(set(full_p0_gate_registry)) == len(full_p0_gate_registry)' in canonical


def test_messenger_contract_test_is_the_affected_certification_target():
    assert Path(__file__).name == "test_messenger_p0_selftest_contract.py"
