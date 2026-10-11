import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.campaign_ready_decision_snapshot import (
    build_ready_decision_snapshot,
    canonical_snapshot_bytes,
)


def row(item_id=1):
    payload={
        "title":"Тестовое объявление",
        "description":"Проверенный текст объявления",
        "template_id":"111166",
        "category_path":"Для дома и дачи > Ремонт и строительство > Тротуарная плитка",
        "params":{"GoodsType":"Тротуарная плитка"},
        "price":1200,
        "price_unit":"за м²",
        "banner_url":"/images/a.png",
        "banner_layout":"premium_photo_glass_light_adaptive_v7",
        "banner_provider":"openai_full_ai_premium",
        "banner_engine":"full_ai_premium",
        "banner_model":"gpt-image-2",
        "copy_quality_contract":"sales_v4",
        "banner_qa":{
            "passed":True,
            "source":"premium_full_ai_batch_vision_qa",
            "mode":"batch_v1",
            "total_cost_rub":1.8,
            "technical_qa":{"passed":True,"artifact_sha256":"abc","perceptual_hash":"00ff","issues":[]},
            "vision_qa":{"passed":True,"semantic_match":True,"headline_ok":True,"issues":[]},
        },
    }
    return SimpleNamespace(
        id=item_id,
        payload_json=json.dumps(payload,ensure_ascii=False),
        updated_at=datetime(2026,10,8,5,0,tzinfo=timezone.utc),
        status="ready",
        feed_identity=f"ci:{item_id}",
        external_item_id=None,
        avito_item_id=None,
        identity_status="feed_identity_ready",
        hypothesis_id=f"h-{item_id}",
        hero_media_id=10,
        readiness_avito=100,
        readiness_sales=100,
        content_hash="c"*64,
        analysis_hash="a"*64,
    )


def test_ready_decision_snapshot_contains_business_and_technical_evidence():
    campaign=SimpleNamespace(id=526)
    engine={
        "snapshot_contract":1,
        "captured_at":"2026-10-08T05:00:00+00:00",
        "backend_build_id":"build-x",
        "banner_models":["gpt-image-2"],
    }
    media={1:[
        {"media_id":10,"position":0,"role":"hero","sha256":"m1","phash":"p1",
         "source_provider":"openai","source_ref":"banner","generation_id":"g1"},
        {"media_id":11,"position":1,"role":"gallery","sha256":"m2","phash":"p2",
         "source_provider":"client","source_ref":"photo","generation_id":None},
    ]}
    out=build_ready_decision_snapshot(
        campaign=campaign,
        account_id="qa",
        rows=[row()],
        media_by_item=media,
        engine_snapshot=engine,
        official_xml_sha="xml-sha",
    )
    assert out["snapshot_contract"]==1
    assert out["official_xml_sha256"]=="xml-sha"
    assert out["engine_snapshot"]["backend_build_id"]=="build-x"
    assert out["content_diversity"]["diversity_contract"]==1
    assert out["content_diversity"]["items"]==1
    assert out["content_diversity"]["status"]=="PASS"
    assert len(out["items"])==1
    item=out["items"][0]
    assert item["hypothesis_id"]=="h-1"
    assert item["category"]["template_id"]=="111166"
    assert item["price"]["value"]==1200
    assert item["media"]["media_ids"]==[10,11]
    assert item["banner"]["family"]=="premium_photo_glass_light_adaptive_v7"
    assert item["banner"]["qa"]["passed"] is True
    assert item["banner"]["qa"]["vision"]["semantic_match"] is True
    assert item["copy"]["title_sha256"]
    assert item["copy"]["description_sha256"]


def test_canonical_snapshot_bytes_are_stable_for_key_order():
    a={"b":2,"a":{"y":2,"x":1}}
    b={"a":{"x":1,"y":2},"b":2}
    assert canonical_snapshot_bytes(a)==canonical_snapshot_bytes(b)
