import json
from types import SimpleNamespace

from app.services.campaign_identity import record_observation


def test_observation_freezes_v7_creative_evidence_with_kpi():
    row=SimpleNamespace(
        id=10,
        account_id="qa",
        campaign_id=20,
        feed_identity="ci:test",
        avito_item_id="8270000099",
        identity_status="published_identity_bound",
        status="published",
        source="feed_factory",
        source_ref="item:10",
        content_hash=None,
        external_item_id=None,
        payload_json=json.dumps({
            "identity":{
                "resolution":"publication_result",
                "publication_timestamp":"2026-10-05T12:00:00+00:00",
                "avito_item_id":"8270000099",
            },
            "banner_layout":"premium_photo_glass_light_adaptive_v7",
            "banner_engine":"full_ai_premium",
            "banner_model":"gpt-image-2",
            "copy_formula_id":"formula-a",
            "creative_angle":"warm trust",
            "hypothesis":"delivery speed",
            "hypothesis_type":"BANNER",
            "active_optimization_version":1,
            "optimization_versions":[{
                "version":1,
                "published_at":"2026-10-05T12:00:00+00:00",
                "observation_window":{"min_complete_days":1},
                "kpi_before":{"views":10,"contacts":1,"conversion":10.0},
                "formula_id":"formula-a",
                "hypothesis_type":"BANNER",
                "hypothesis":"delivery speed",
                "creative_angle":"warm trust",
            }],
        }),
    )
    out=record_observation(row,{
        "snapshot_date":"2026-10-07",
        "views":40,
        "contacts":5,
        "conversion":12.5,
    })
    assert out["status"]=="improved"
    payload=json.loads(row.payload_json)
    obs=payload["optimization_versions"][0]["observations"][0]
    evidence=obs["creative_evidence"]
    assert evidence["banner_family"]=="glass_light"
    assert evidence["banner_layout"]=="premium_photo_glass_light_adaptive_v7"
    assert evidence["banner_engine"]=="full_ai_premium"
    assert evidence["banner_model"]=="gpt-image-2"
    assert evidence["formula_id"]=="formula-a"
    assert evidence["hypothesis_type"]=="BANNER"
    assert evidence["hypothesis"]=="delivery speed"
    assert evidence["creative_angle"]=="warm trust"
    assert obs["views"]==40
    assert obs["contacts"]==5
