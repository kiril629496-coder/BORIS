from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_posting_runner_persists_structured_delivery_failure_contract():
    source = (ROOT / "posting_runner.py").read_text(encoding="utf-8")
    block = source[source.index('delivery_failures = {}'):source.index('if not _persist_post_record', source.index('delivery_failures = {}'))]
    for field in (
        '"reason"', '"dependency_state"', '"retryable_external"',
        '"retry_after_seconds"', '"owner_action_required"',
    ):
        assert field in block
    assert 'rec["delivery_failures"] = delivery_failures' in block
    assert 'rec.pop("delivery_failures", None)' in source


def test_due_rescue_treats_vk_external_wait_and_pacing_as_non_internal():
    source = (ROOT / "run" / "social_due_rescue_all_active_v3.py").read_text(encoding="utf-8")
    assert 'failure_dependency=="waiting_external"' in source
    assert 'vk_failure.get("retryable_external") is True' in source
    assert 'failure_reason=="vk_delivery_paced"' in source
    assert '"VK cooldown после rate limit:" in legacy_error' in source
    assert '"Flood control" in legacy_error' in source
    assert '"VK live delivery paced: next rescue pass" in legacy_error' in source
    assert 'delivery_retry_scheduled.append(item)' in source
    unhealthy = source[source.index('unhealthy=bool('):source.index('result={', source.index('unhealthy=bool('))]
    assert 'delivery_waiting_external' not in unhealthy
    assert 'delivery_retry_scheduled' not in unhealthy
