from app.services.campaign_scale_lineage import apply_scale_lineage


def test_scale_lineage_is_explicit_and_does_not_replace_derived_hypothesis():
    payload={"title":"","hypothesis_id":"derived-h-7","variant":7}
    meta={
        "source_campaign_id":517,
        "source_item_id":5859,
        "source_avito_item_id":"123456789",
        "formula_id":"PAS",
        "creative_angle":"Долговечность",
        "hypothesis":"Срок службы",
        "requested_count":50,
    }
    out=apply_scale_lineage(payload,meta)
    assert out["scale_lineage_contract"]==1
    assert out["scale_parent_campaign_id"]==517
    assert out["scale_parent_item_id"]==5859
    assert out["scale_parent_avito_item_id"]=="123456789"
    assert out["scale_parent_formula_id"]=="PAS"
    assert out["scale_parent_hypothesis"]=="Срок службы"
    assert out["scale_parent_creative_angle"]=="Долговечность"
    assert out["scale_requested_count"]==50
    assert out["scale_derivation"]=="winner_pattern_new_variant"
    assert out["hypothesis_id"]=="derived-h-7"


def test_non_scale_payload_remains_unmodified():
    payload={"title":"Обычная карточка","variant":1}
    out=apply_scale_lineage(payload,{})
    assert out==payload
    assert out is not payload


def test_invalid_source_item_does_not_create_fake_lineage():
    payload={"title":"Карточка"}
    out=apply_scale_lineage(payload,{"source_item_id":"not-an-id","source_campaign_id":517})
    assert out==payload


def test_optional_parent_fields_are_fail_closed_not_invented():
    out=apply_scale_lineage({"variant":1},{"source_item_id":10})
    assert out["scale_parent_item_id"]==10
    assert out["scale_parent_campaign_id"] is None
    assert out["scale_parent_avito_item_id"] is None
    assert out["scale_parent_formula_id"] is None
    assert out["scale_parent_hypothesis"] is None


def test_materialize_wires_scale_lineage_without_touching_copy_generation():
    import inspect
    from app.api import campaigns as C
    src=inspect.getsource(C.ff_workflow_materialize)
    assert 'ff.get("ab_scale")' in src
    assert "apply_scale_lineage" in src
    assert "scale_lineage" in src
