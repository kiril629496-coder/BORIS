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


def test_resume_with_terminal_block_never_becomes_full_completed():
    ids = [1, 2, 3]
    payload = _base(ids)
    payload["banner_terminal_blocked"] = {
        "1": {
            "id": 1,
            "code": "premium_batch_qa_not_passed",
            "reason": "Awkward crop",
            "artifact_identity": "banner1:v1:" + "a" * 64,
        }
    }
    plan = plan_banner_slice(
        job_id=77,
        payload=payload,
        item_ids=ids,
        passed_ids=[2, 3],
        legacy_attempted=True,
    )
    assert plan["final"] == "partial"
    assert plan["result"]["stage"] == "banners_partial"
    assert plan["result"]["blocked_count"] == 1
    assert plan["result"]["pending_item_ids"] == []


def test_jobs_continue_only_pass_subset_after_banner_partial():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert 'str(final_result.get("stage") or "") == "banners_partial"' in src
    assert '_passed_scope=passed_ids()' in src
    assert '_enqueue_scoped_prepare_after_banners(db,job,C,campaign,user,account_id,_passed_scope)' in src
    assert 'final_result["next_stage"]="prepare_ready_subset"' in src
