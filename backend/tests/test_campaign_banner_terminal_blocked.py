from app.services.campaign_banner_job_policy import finish_banner_slice, plan_banner_slice


def _base(ids):
    return {
        "banner_item_intents": {str(i): f"image:{i}:r0" for i in ids},
        "banner_item_budget_intents": {str(i): f"budget:{i}" for i in ids},
        "quality_retry_counts": {},
    }


def _terminal_quality_error(item_id=1, retry_allowed=False):
    return {
        "id": item_id,
        "code": "premium_batch_qa_not_passed",
        "retry_action": "regenerate_quality",
        "retry_allowed": retry_allowed,
        "image_outcome": "succeeded",
        "qa_terminal": True,
        "qa_rejection_reason": "Awkward crop",
        "banner_id": f"banner{item_id}",
        "version": 1,
        "artifact_sha256": ("a" if item_id == 1 else "b") * 64,
    }


def test_terminal_quality_block_does_not_stall_siblings():
    ids = [1, 2, 3]
    out = finish_banner_slice(
        job_id=77,
        payload=_base(ids),
        item_ids=ids,
        called_ids=[1],
        result={"status": "error", "errors": [_terminal_quality_error()]},
        passed_ids=[],
        quality_retry_limit=4,
    )
    assert out["final"] == "retry"
    assert out["result"]["pending_item_ids"] == [2, 3]
    assert out["result"]["blocked_count"] == 1
    assert out["payload"]["banner_terminal_blocked"]["1"]["reason"] == "Awkward crop"

    plan = plan_banner_slice(
        job_id=77,
        payload=out["payload"],
        item_ids=ids,
        passed_ids=[],
        legacy_attempted=True,
    )
    assert plan["final"] == "invoke"
    assert plan["chunk"] == [2, 3]


def test_unknown_outcome_still_fails_closed_and_is_not_terminalized():
    ids = [1, 2]
    error = {
        "id": 1,
        "code": "provider_state_unknown",
        "retry_action": "reconcile_image",
        "retry_allowed": False,
        "image_outcome": "unknown",
        "qa_terminal": False,
        "qa_rejection_reason": "",
        "banner_id": None,
        "version": None,
        "artifact_sha256": None,
    }
    out = finish_banner_slice(
        job_id=77,
        payload=_base(ids),
        item_ids=ids,
        called_ids=[1],
        result={"status": "error", "errors": [error]},
        passed_ids=[],
        quality_retry_limit=4,
    )
    assert out["final"] == "partial"
    assert not out["payload"].get("banner_terminal_blocked")


def test_terminal_block_is_truthful_partial_when_no_siblings_remain():
    ids = [1]
    out = finish_banner_slice(
        job_id=77,
        payload=_base(ids),
        item_ids=ids,
        called_ids=[1],
        result={"status": "error", "errors": [_terminal_quality_error()]},
        passed_ids=[],
        quality_retry_limit=4,
    )
    assert out["final"] == "partial"
    assert out["result"]["stage"] == "banners_partial"
    assert out["result"]["blocked_count"] == 1
    assert out["result"]["done"] == 1
