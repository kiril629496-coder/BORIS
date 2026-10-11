from types import SimpleNamespace

from app.services.job_execution_clock import (
    CURRENT_CLOCK,
    _job_provenance,
)
from app.usage import _usage_provenance


def _job(**payload):
    return SimpleNamespace(
        id=77,
        account_id="qa_account",
        kind="campaign_prepare",
        payload_json=payload,
    )


def test_job_provenance_single_item_is_exact():
    out=_job_provenance(_job(campaign_id=517,stage="banners",item_ids=[6001]))
    assert out["attribution_contract"]==2
    assert out["account_id"]=="qa_account"
    assert out["job_id"]==77
    assert out["job_kind"]=="campaign_prepare"
    assert out["campaign_id"]==517
    assert out["campaign_item_id"]==6001
    assert out["stage"]=="banners"


def test_job_provenance_batch_is_not_falsely_assigned_to_one_item():
    out=_job_provenance(_job(campaign_id=517,stage="media",item_ids=[6001,6002,6003]))
    assert out["campaign_id"]==517
    assert out["item_scope_count"]==3
    assert "campaign_item_id" not in out


def test_usage_provenance_merges_job_context_and_explicit_item_override():
    token=CURRENT_CLOCK.set(SimpleNamespace(provenance={
        "attribution_contract":2,
        "account_id":"qa_account",
        "job_id":88,
        "job_kind":"campaign_prepare",
        "campaign_id":517,
        "stage":"banners",
        "item_scope_count":10,
    }))
    try:
        out=_usage_provenance({
            "boris_intent_key":"premium-banner:517:6010",
            "boris_provenance":{
                "item_id":6010,
                "banner_id":"abc123",
                "stage":"premium_banner",
            },
        })
    finally:
        CURRENT_CLOCK.reset(token)
    assert out["campaign_id"]==517
    assert out["job_id"]==88
    assert out["campaign_item_id"]==6010
    assert out["banner_id"]=="abc123"
    assert out["stage"]=="premium_banner"
    assert out["intent_key"]=="premium-banner:517:6010"
    assert "attribution_missing" not in out


def test_campaign_job_without_campaign_id_is_marked_missing():
    token=CURRENT_CLOCK.set(SimpleNamespace(provenance={
        "attribution_contract":2,
        "account_id":"qa_account",
        "job_id":99,
        "job_kind":"campaign_prepare",
        "stage":"texts",
    }))
    try:
        out=_usage_provenance({})
    finally:
        CURRENT_CLOCK.reset(token)
    assert out["attribution_missing"]=="campaign_id"


def test_secrets_are_never_copied_to_usage_provenance():
    token=CURRENT_CLOCK.set(SimpleNamespace(provenance={
        "job_kind":"campaign_prepare",
        "campaign_id":517,
        "token":"secret",
    }))
    try:
        out=_usage_provenance({"boris_provenance":{"api_key":"nope","campaign_item_id":6011}})
    finally:
        CURRENT_CLOCK.reset(token)
    assert "token" not in out
    assert "api_key" not in out
    assert out["campaign_item_id"]==6011


def test_current_job_provenance_tracks_checkpoint_stage_from_live_job():
    from types import SimpleNamespace
    from app.services.job_execution_clock import current_job_provenance

    job=SimpleNamespace(
        id=123,
        account_id="qa_account",
        kind="campaign_prepare",
        payload_json={"campaign_id":517,"stage":"queued","item_ids":[6010]},
    )
    token=CURRENT_CLOCK.set(SimpleNamespace(
        provenance=_job_provenance(job),
        job=job,
    ))
    try:
        job.payload_json={"campaign_id":517,"stage":"banners","item_ids":[6010]}
        out=current_job_provenance()
    finally:
        CURRENT_CLOCK.reset(token)
    assert out["campaign_id"]==517
    assert out["campaign_item_id"]==6010
    assert out["stage"]=="banners"


def test_specialized_campaign_job_kind_has_canonical_stage_before_first_checkpoint():
    from types import SimpleNamespace

    banner=_job_provenance(SimpleNamespace(
        id=201,account_id="qa",kind="campaign_banners",
        payload_json={"campaign_id":535,"stage":"queued","item_ids":[6765]},
    ))
    media=_job_provenance(SimpleNamespace(
        id=202,account_id="qa",kind="campaign_media_shard",
        payload_json={"campaign_id":535,"item_ids":[6765]},
    ))
    prepare=_job_provenance(SimpleNamespace(
        id=203,account_id="qa",kind="campaign_prepare",
        payload_json={"campaign_id":535,"stage":"queued","item_ids":[6765]},
    ))
    assert banner["stage"]=="banners"
    assert media["stage"]=="media"
    # campaign_prepare is multi-stage; its real checkpoint remains authoritative.
    assert prepare["stage"]=="queued"
