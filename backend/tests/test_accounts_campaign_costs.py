from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from app.api.accounts import _campaign_current_banner_cost, _campaign_cost_bucket
from app.services.ai_costs_private_access import require_ai_costs_private_owner


def test_current_banner_cost_counts_only_current_banner_artifacts():
    out=_campaign_current_banner_cost([
        (1,{"title":"A","banner_url":"/images/a.png","banner_qa":{"total_cost_rub":1.25}}),
        (2,{"title":"B","banner_url":"/images/b.png","banner_qa":{"total_cost_rub":"2.75"}}),
        (3,{"title":"C","banner_url":"/images/c.png","banner_qa":{"passed":True}}),
        (4,{"title":"D","banner_url":"","banner_qa":{"total_cost_rub":99}}),
    ])
    assert out["banner_items"]==3
    assert out["costed_banner_items"]==2
    assert out["banner_cost_coverage_pct"]==66.7
    assert out["current_banner_artifact_cost_rub"]==4.0
    assert out["cost_per_costed_banner_item_rub"]==2.0
    assert [x["cost_rub"] for x in out["items"]]==[1.25,2.75,None]


@pytest.mark.parametrize("operation,bucket",[
    ("banner_premium_vision_qa_batch_v1","banners"),
    ("campaign_banner_vision_qa","banners"),
    ("media_semantic_item_vision","media_qa"),
    ("stock_semantic_filter","media_qa"),
    ("copy_generate","text_copy"),
    ("title_rewrite","text_copy"),
    ("competitor_analysis","other"),
])
def test_cost_bucket(operation,bucket):
    assert _campaign_cost_bucket(operation)==bucket


def test_private_cost_guard_rejects_non_platform_owner():
    fake=SimpleNamespace(id=999999,role="manager",email="nobody@example.invalid")
    with pytest.raises(HTTPException) as exc:
        require_ai_costs_private_owner(fake)
    assert exc.value.status_code==403
