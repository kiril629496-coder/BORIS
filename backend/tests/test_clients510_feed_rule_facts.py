import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

source = os.environ.get("BORIS_FEED_RULE_FACTS_SOURCE")
if source:
    spec = importlib.util.spec_from_file_location("feed_facts_test", source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
else:
    from app.services import control_feed_rule_facts as mod

SHA = "a" * 64


def facts(stage, **extra):
    return dict(account_id="acct-a", campaign_id=537, stage=stage, event_id="job:10859:watch:3",
                observed_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), **extra)


def get(f, key):
    result = mod.evaluate_feed_rule_facts(f)
    assert all(v["whole_rule_postcondition_proven"] is False and v["rule_pass"] is False for v in result.values())
    return result["rule.feed_factory." + key]


def materialize():
    return facts("materialize", order_id="ffo_exact", requested_total=2, materialized_ids=[11, 12], items_total=2,
                 created_count=1, updated_count=1, row_scope_verified=True)


def local():
    return facts("local_validation", expected_item_ids=[11, 12], source="campaigns._ff_validate_row",
                 reports=[dict(id=x, account_id="acct-a", valid=True, errors=[]) for x in [11, 12]])


def review():
    return facts("delivery_review", snapshot_source="app.api.avito._autoload_upload_snapshot", snapshot_account_id="acct-a",
                 expected_ad_ids=["ff-11", "ff-12"], snapshot_expected_ad_ids=["ff-11", "ff-12"], requested_upload_id=891,
                 snapshot=dict(upload_id=891, status="completed", terminal=True, full_success=True, partial=False,
                               total=2, successful=2, error=0, blocked=0, item_errors=[], scope={}), persisted_terminal_status="completed")


def test_registered_fourteen_unique_keys():
    assert len({k for keys in mod.STAGE_RULES.values() for k in keys}) == 14
    assert all(set(v) <= set(mod.STAGE_RULES[k]) for k, v in mod.MANDATORY_STAGE_RULES.items())


@pytest.mark.parametrize("field,value", [("account_id", ""), ("event_id", ""), ("campaign_id", True),
    ("campaign_id", -1), ("observed_at", "2026-10-09"), ("observed_at", "infinity"),
    ("observed_at", "2099-01-01T00:00:00+00:00"), ("artifact_sha256", "bad")])
def test_identity_unknown_never_pass(field, value):
    f=materialize(); f[field]=value
    d=get(f,"draft"); assert d["predicate_result"] is None and d["evaluation_due_applicable"] is False


def test_draft_complete_quantity():
    assert get(materialize(), "draft")["predicate_result"] is True


def test_draft_partial_quantity_false():
    f=materialize(); f["requested_total"]=3
    assert get(f,"draft")["predicate_result"] is False


@pytest.mark.parametrize("change", [{"materialized_ids":[11,11]}, {"items_total":3}, {"row_scope_verified":"true"}, {"created_count":True}])
def test_draft_requires_exact_scope(change):
    f=materialize();f.update(change);assert get(f,"draft")["predicate_result"] is None


def test_semantic_valid_not_full_gallery():
    d=get(local(),"media_quality")
    assert d["predicate_result"] is True and d["facts"]["full_order_gallery_count_proven"] is False


def test_media_negative_remains_negative():
    f=local();f["reports"][0].update(valid=False,errors=[{"code":"semantic_subject_mismatch","field":"images"}])
    assert get(f,"validation")["predicate_result"] is False
    assert get(f,"media_quality")["predicate_result"] is False


@pytest.mark.parametrize("change", ["tenant","missing","duplicate","string_bool","unknown_source"])
def test_local_validation_incomplete(change):
    f=local()
    if change=="tenant":f["reports"][0]["account_id"]="other"
    if change=="missing":f["reports"].pop()
    if change=="duplicate":f["reports"][1]["id"]=11
    if change=="string_bool":f["reports"][0]["valid"]="true"
    if change=="unknown_source":f["source"]="generated_by_ai"
    assert get(f,"validation")["predicate_result"] is None


@pytest.mark.parametrize("state,result,status", [("ok",True,"EVALUATED"),("errors",False,"EVALUATED"),("pending",None,"WAITING_EVENT"),("error",None,"NOT_PROVEN")])
def test_official_xml_statuses(state,result,status):
    f=facts("xmlcheck",artifact_sha256=SHA,xml_written=True,xml_items=2,xmlcheck_status=state)
    d=get(f,"validation");assert d["predicate_result"] is result and d["evaluation_status"]==status
    assert get(f,"xml_generation")["predicate_result"] is True


def test_xml_without_exact_artifact_cannot_pass():
    assert get(facts("xmlcheck",xml_written=True,xml_items=2,xmlcheck_status="ok"),"validation")["predicate_result"] is None


def activation():
    return facts("activation_preflight",delivery=dict(account_id="acct-a",campaign_id=537,current_sha=SHA,validated_sha=SHA,
        validation_state="READY",semantic_ready=True,semantic_blocked=0,image_unique=True,content_unique=True))


def test_activation_drift_rejects_validation():
    f=activation();f["delivery"]["validated_sha"]="b"*64
    assert get(f,"validation")["predicate_result"] is False


def test_activation_cross_campaign_not_proof():
    f=activation();f["delivery"]["campaign_id"]=536
    assert get(f,"validation")["predicate_result"] is None
    assert get(f,"duplicate")["predicate_result"] is None


def test_missing_conditional_branch_no_execution():
    assert "rule.feed_factory.replacement" not in mod.evaluate_feed_rule_facts(activation())
    assert "rule.feed_factory.retry" not in mod.evaluate_feed_rule_facts(review())


def test_explicit_replacement_requires_distinct_actual_identities():
    f=activation();f.update(replacement_requested=True,replacement_evidence=dict(verified=True,old_feed_identity="a",new_feed_identity="b",
        provider_rejection_upload_id=5,verified_at=f["observed_at"]))
    assert get(f,"replacement")["predicate_result"] is True
    f["replacement_evidence"]["new_feed_identity"]="a"
    assert get(f,"replacement")["predicate_result"] is False


def test_submit_receipt_never_published():
    f=facts("publish_submit",artifact_sha256=SHA,canonical_publisher="app.api.avito.feed_send_to_avito",
        result=dict(status="ok",delivery_state="submitted",upload_id=123))
    d=get(f,"publish");assert d["predicate_result"] is True and d["facts"]["published_proven"] is False


@pytest.mark.parametrize("state", ["scheduled_url","delivery_unknown","error"])
def test_nonsubmission_is_not_success(state):
    f=facts("publish_submit",artifact_sha256=SHA,canonical_publisher="app.api.avito.feed_send_to_avito",
        result=dict(status="ok",delivery_state=state,upload_id=None))
    assert get(f,"publish")["predicate_result"] is None


def test_review_exact_terminal_scope():
    f=review();assert get(f,"review")["predicate_result"] is True
    assert get(f,"terminal_states")["predicate_result"] is True


@pytest.mark.parametrize("field,value", [("snapshot_account_id","other"),("snapshot_expected_ad_ids",["wrong"]),("requested_upload_id",892),("snapshot_source","local_echo")])
def test_report_provenance_scope_fails_closed(field,value):
    f=review();f[field]=value;assert get(f,"review")["predicate_result"] is None
    assert get(f,"terminal_states")["predicate_result"] is None


def test_pending_waits_without_fake_skip_or_terminal():
    f=review();f["snapshot"].update(status="pending",terminal=False)
    d=get(f,"review");assert d["evaluation_status"]=="WAITING_EVENT" and not d["evaluation_due_applicable"]
    assert "rule.feed_factory.terminal_states" not in mod.evaluate_feed_rule_facts(f)


def test_old_schedule_upload_not_consumption():
    f=review();f.update(scheduled_watch=True,baseline_upload_id=891,requested_upload_id=None)
    assert get(f,"schedule")["predicate_result"] is None
    assert get(f,"review")["predicate_result"] is None


def test_partial_and_rejection_preserve_scope():
    f=review();f["snapshot"].update(successful=1,error=1,partial=True,full_success=False,item_errors=[{"ad_id":"ff-12","code":"2010"}]);f["persisted_terminal_status"]="partial"
    assert get(f,"partial_publish")["predicate_result"] is True
    assert get(f,"rejection")["predicate_result"] is True
    assert get(f,"terminal_states")["predicate_result"] is True
    f["snapshot"]["item_errors"][0]["ad_id"]="alien"
    assert get(f,"rejection")["predicate_result"] is None


def test_terminal_lie_false_and_count_unknown_none():
    f=review();f["persisted_terminal_status"]="failed";assert get(f,"terminal_states")["predicate_result"] is False
    f["snapshot"]["successful"]=1;assert get(f,"terminal_states")["predicate_result"] is None


def test_superseded_and_unauthorized_retry_exact_noop():
    f=review();f.update(superseded_guard_triggered=True,campaign_status="superseded",external_action=False,
        retry_guard_triggered=True,retry_policy="explicit_retry_authorization",retry_authorized=False)
    assert get(f,"superseded")["predicate_result"] is True and get(f,"retry")["predicate_result"] is True
    f["external_action"]=True
    assert get(f,"superseded")["predicate_result"] is False and get(f,"retry")["predicate_result"] is False


def test_stable_identity_replay_and_changed_input():
    assert mod.feed_event_identity("x",{"a":1,"b":2})==mod.feed_event_identity("x",{"b":2,"a":1})
    assert mod.feed_event_identity("x",{"a":1})!=mod.feed_event_identity("x",{"a":2})

@pytest.mark.parametrize('unchanged,present,result', [(True,True,True),(False,True,None),(True,False,None),(None,True,None)])
def test_source_json_is_not_xml_and_requires_unchanged_input(unchanged,present,result):
    f=facts('publish_submit',artifact_sha256=SHA,artifact_kind='canonical_feed_items_json',canonical_publisher='app.api.avito.feed_send_to_avito',
        source_input_unchanged=unchanged,source_items_present=present,result=dict(status='ok',delivery_state='submitted',upload_id=123))
    assert get(f,'publish')['predicate_result'] is result


def test_guard_no_fake_review_pending():
    f=facts('delivery_guard',superseded_guard_triggered=True,campaign_status='superseded',external_action=False)
    assert list(mod.evaluate_feed_rule_facts(f))==['rule.feed_factory.superseded']
