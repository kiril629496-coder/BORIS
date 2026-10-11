import json
from types import SimpleNamespace

from app.services.campaign_creative_memory import creative_family_memory


def _row(item_id, family, views, contacts):
    return SimpleNamespace(
        id=item_id,
        payload_json=json.dumps({
            "optimization_versions":[{
                "observations":[{
                    "snapshot_date":"2026-10-07",
                    "views":views,
                    "contacts":contacts,
                    "creative_evidence":{"banner_family":family},
                }]
            }]
        }),
    )


def test_no_evidence_is_equal_exploration():
    out=creative_family_memory([])
    assert out["plan"]["weights_pct"]=={
        "glass_light":25,
        "glass_dark":25,
        "warm_accent":25,
        "charcoal_center":25,
    }

def test_one_family_uses_moderate_exploit():
    out=creative_family_memory([
        _row(1,"glass_light",20,2),
        _row(2,"glass_light",20,2),
        _row(3,"glass_dark",10,0),
    ])
    assert out["families"]["glass_light"]["views"]==40
    assert out["families"]["glass_light"]["contacts"]==4
    assert out["plan"]["status"]=="provisional_winner"
    assert out["plan"]["weights_pct"]=={
        "glass_light":40,
        "glass_dark":20,
        "warm_accent":20,
        "charcoal_center":20,
    }

def test_two_families_use_45_25_with_exploration():
    out=creative_family_memory([
        _row(1,"glass_light",50,10),
        _row(2,"glass_dark",50,5),
        _row(3,"warm_accent",20,1),
    ])
    assert out["plan"]["winner"]=="glass_light"
    assert out["plan"]["runner_up"]=="glass_dark"
    assert out["plan"]["weights_pct"]=={
        "glass_light":45,
        "glass_dark":25,
        "warm_accent":15,
        "charcoal_center":15,
    }
    assert max(out["plan"]["weights_pct"].values())<=60


def test_legacy_family_is_not_backfilled_into_v7_memory():
    out=creative_family_memory([
        _row(7,"premium_photo_adaptive_glass_caption_v6",100,20),
    ])
    assert out["total_views"]==0
    assert out["families_with_evidence"]==0
    assert out["plan"]["status"]=="collecting"
