from types import SimpleNamespace

from app.services import campaign_review_page as R


def _row(payload):
    return SimpleNamespace(payload_json=__import__("json").dumps(payload))


def test_engine_snapshot_collects_runtime_and_item_contracts(monkeypatch):
    monkeypatch.setenv("BORIS_RELEASE_BUILD_ID","build-abc")
    campaign=SimpleNamespace(id=526)
    ff={
        "version":2,
        "source_contract":{"version":1},
        "order_contract":{"version":1},
        "direction_intake":{
            "A":{"contract_version":"dir-v1"},
            "B":{"contract_version":"dir-v2"},
        },
    }
    rows=[
        _row({
            "banner_engine":"full_ai_premium",
            "banner_model":"gpt-image-2",
            "banner_provider":"openai_full_ai_premium",
            "copy_quality_contract":"sales_v4",
            "intake_contract_version":"item-v1",
            "banner_qa":{"source":"premium_full_ai_batch_vision_qa","mode":"batch_v1"},
            "media_semantic_evidence":[{"policy_version":"sem-v1","checker":"vision"}],
        }),
        _row({
            "banner_engine":"full_ai_premium",
            "banner_model":"gpt-image-2",
            "copy_quality_contract":"sales_v4",
            "intake_contract_version":"item-v2",
        }),
    ]
    out=R._engine_snapshot(campaign,ff,rows)
    assert out["snapshot_contract"]==1
    assert out["backend_build_id"]=="build-abc"
    assert out["campaign_id"]==526
    assert out["feed_factory_version"]==2
    assert out["source_contract_version"]==1
    assert out["order_contract_version"]==1
    assert out["direction_contract_versions"]==["dir-v1","dir-v2"]
    assert out["item_intake_contract_versions"]==["item-v1","item-v2"]
    assert out["copy_quality_contracts"]==["sales_v4"]
    assert out["banner_engines"]==["full_ai_premium"]
    assert out["banner_models"]==["gpt-image-2"]
    assert out["media_semantic_policy_versions"]==["sem-v1"]


def test_submitted_review_preserves_previous_engine_snapshot():
    previous={"snapshot_contract":1,"backend_build_id":"old-build","banner_models":["old-model"]}
    current={"snapshot_contract":1,"backend_build_id":"new-build","banner_models":["new-model"]}
    out=R._select_engine_snapshot(True,{"engine_snapshot":previous},current)
    assert out==previous
    assert out is not previous


def test_ready_review_uses_current_engine_snapshot():
    previous={"backend_build_id":"old"}
    current={"backend_build_id":"new"}
    assert R._select_engine_snapshot(False,{"engine_snapshot":previous},current)==current
    assert R._select_engine_snapshot(True,{},current)==current


def test_same_ready_xml_sha_preserves_existing_engine_snapshot():
    previous={"snapshot_contract":1,"backend_build_id":"old-build","banner_models":["old-model"]}
    current={"snapshot_contract":1,"backend_build_id":"new-build","banner_models":["new-model"]}
    review={"official_xml_sha256":"sha-1","engine_snapshot":previous}
    out=R._select_engine_snapshot(False,review,current,current_xml_sha="sha-1")
    assert out==previous
    assert out is not previous


def test_new_ready_xml_sha_gets_new_engine_snapshot():
    previous={"snapshot_contract":1,"backend_build_id":"old-build"}
    current={"snapshot_contract":1,"backend_build_id":"new-build"}
    review={"official_xml_sha256":"sha-old","engine_snapshot":previous}
    assert R._select_engine_snapshot(False,review,current,current_xml_sha="sha-new")==current


def test_engine_snapshot_carries_validated_xml_identity(monkeypatch):
    monkeypatch.setenv("BORIS_RELEASE_BUILD_ID","build-xyz")
    out=R._engine_snapshot(SimpleNamespace(id=10),{"version":2},[],official_xml_sha="xml-abc")
    assert out["backend_build_id"]=="build-xyz"
    assert out["official_xml_sha256"]=="xml-abc"


def test_review_page_wires_private_decision_snapshot_only_for_ready_state():
    import inspect
    src=inspect.getsource(R.generate_campaign_review_page)
    assert "persist_ready_decision_snapshot(" in src
    publication=src.index("if publication_snapshot:")
    persist=src.index("decision_meta=persist_ready_decision_snapshot(")
    assert publication < persist
    assert 'decision_meta["decision_snapshot_status"]="legacy_submitted_without_ready_snapshot"' in src
    assert "**decision_meta" in src


def test_existing_decision_snapshot_overrides_current_engine_and_hides_internal_copy():
    current={"backend_build_id":"new-build"}
    meta={
        "decision_snapshot_engine":{"backend_build_id":"frozen-build","banner_models":["old-model"]},
        "decision_snapshot_sha256":"abc",
    }
    out=R._apply_decision_snapshot_engine(current,meta)
    assert out=={"backend_build_id":"frozen-build","banner_models":["old-model"]}
    assert "decision_snapshot_engine" not in meta
    assert meta["decision_snapshot_sha256"]=="abc"


def test_new_decision_snapshot_uses_current_engine_when_no_frozen_engine():
    current={"backend_build_id":"new-build"}
    meta={"decision_snapshot_sha256":"abc"}
    assert R._apply_decision_snapshot_engine(current,meta)==current
