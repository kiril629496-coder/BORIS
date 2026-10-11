import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from app.services import media_semantic_validator as V


class MediaSemanticCacheTests(unittest.TestCase):
    def test_vision_burst_cap_stops_before_provider(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY":"test"}), \
             patch("app.services.ai_budget.reserve", return_value={"status":"reserved"}), \
             patch("app.services.ai_budget.allow_next", return_value={"allowed":True}), \
             patch("app.services.ai_budget.release", return_value=None), \
             patch.object(V,"_vision_burst_guard",lambda account_id:nullcontext({"allowed":False,"used":20,"cap":20})), \
             patch("requests.post", side_effect=AssertionError("paid vision provider called")):
            with self.assertRaisesRegex(RuntimeError,"VISION_BURST_CAP"):
                V._vision_chat_json("acc-a","qa",[{"type":"text","text":"x"}],100,10)

    def test_vision_daily_cap_is_distinct_and_stops_before_provider(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY":"test"}), \
             patch("app.services.ai_budget.reserve", return_value={"status":"reserved"}), \
             patch("app.services.ai_budget.allow_next", return_value={"allowed":True}), \
             patch("app.services.ai_budget.release", return_value=None), \
             patch.object(V,"_vision_burst_guard",lambda account_id:nullcontext({"allowed":False,"code":"VISION_DAILY_CAP","used":2,"cap":20,"daily_used":100,"daily_cap":100})), \
             patch("requests.post", side_effect=AssertionError("paid vision provider called")):
            with self.assertRaisesRegex(RuntimeError,"VISION_DAILY_CAP:100/100"):
                V._vision_chat_json("acc-a","qa",[{"type":"text","text":"x"}],100,10)

    def test_vision_ambiguous_transport_is_quarantined_and_not_rebought(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {"OPENAI_API_KEY":"test", "BORIS_VISION_PAID_CACHE_DIR":td}), \
             patch("app.services.ai_budget.reserve", return_value={"status":"reserved"}), \
             patch("app.services.ai_budget.allow_next", return_value={"allowed":True}), \
             patch("app.services.ai_budget.release", return_value=None), \
             patch.object(V,"_vision_burst_guard",lambda account_id:nullcontext({"allowed":True,"used":0,"cap":20})), \
             patch("requests.post", side_effect=RuntimeError("socket closed after send")) as provider:
            with self.assertRaisesRegex(RuntimeError,"VISION_OUTCOME_UNCERTAIN:vision_transport_outcome_unknown"):
                V._vision_chat_json("acc-vision-uncertain","qa-uncertain",[{"type":"text","text":"unique-vision-intent"}],100,10)
            with self.assertRaisesRegex(RuntimeError,"VISION_OUTCOME_UNCERTAIN:vision_transport_outcome_unknown"):
                V._vision_chat_json("acc-vision-uncertain","qa-uncertain",[{"type":"text","text":"unique-vision-intent"}],100,10)
        self.assertEqual(provider.call_count,1)

    def test_same_family_asset_cache_skips_paid_vision(self):
        payload={"title":"Кухни на заказ", "description":"Изготовление кухни по размерам", "direction":"Кухни на заказ"}
        link=SimpleNamespace(position=0)
        asset=SimpleNamespace(id=10, account_id="acc-a", tags=[
            "vision_family_confirmed:kitchen",
            "vision_family_confidence:0.930",
            "vision_subject:kitchen with cabinets and countertop",
        ])
        with patch.object(V, "_vision_chat_json", side_effect=AssertionError("paid vision called")):
            out=V.classify_item_media_vision(payload,[(link,asset)],account_id="acc-a")
        self.assertEqual(len(out),1)
        self.assertTrue(out[0]["match"])
        self.assertTrue(out[0]["cached"])
        self.assertEqual(out[0]["checker"],"openai_vision_family_cache")

    def test_different_family_does_not_reuse_positive_cache(self):
        payload={"title":"Гардеробные на заказ", "description":"Гардеробная система хранения", "direction":"Гардеробные на заказ"}
        link=SimpleNamespace(position=0)
        asset=SimpleNamespace(id=10, account_id="acc-a", tags=[
            "vision_family_confirmed:kitchen",
            "vision_family_confidence:0.930",
            "vision_subject:kitchen with cabinets and countertop",
        ])
        with patch.object(V, "_image_data_uri", return_value="data:image/jpeg;base64,AA=="), \
             patch.object(V, "_vision_chat_json", return_value=[{"index":1,"match":False,"confidence":0.99,"subject":"kitchen cabinets","reason":"wrong furniture type"}]) as paid:
            out=V.classify_item_media_vision(payload,[(link,asset)],account_id="acc-a")
        paid.assert_called_once()
        self.assertFalse(out[0]["match"])

    def test_banner_qa_durable_cache_skips_second_paid_vision(self):
        import os, tempfile
        payload={"title":"Toyota Camry в аренду", "description":"Аренда автомобиля", "direction":"Аренда авто"}
        banner=SimpleNamespace(id=20,account_id="acc-a",storage_key="acc-a/banner.png")
        verdict={"pass":True,"identity_ok":True,"text_ok":True,"mobile_ok":True,"claims_ok":True,"plate_ok":True,"composition_ok":True,"professional_ok":True,"visual_hierarchy_ok":True,"issues":[]}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{"BORIS_VISION_QA_CACHE_DIR":td}), \
             patch.object(V,"_image_data_uri",return_value="data:image/jpeg;base64,AA=="), \
             patch.object(V,"_vision_chat_json",return_value=dict(verdict)) as paid:
            first=V.validate_campaign_banner_vision(payload,banner,None,"acc-a")
            self.assertFalse(first["cache_hit"])
            self.assertEqual(paid.call_count,1)
            paid.side_effect=AssertionError("paid banner vision called twice")
            second=V.validate_campaign_banner_vision(payload,banner,None,"acc-a")
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["checker"],"durable_banner_qa_cache")

    def test_banner_qa_cache_invalidates_when_copy_changes(self):
        import os, tempfile
        banner=SimpleNamespace(id=20,account_id="acc-a",storage_key="acc-a/banner.png")
        base={"pass":True,"identity_ok":True,"text_ok":True,"mobile_ok":True,"claims_ok":True,"plate_ok":True,"composition_ok":True,"professional_ok":True,"visual_hierarchy_ok":True,"issues":[]}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{"BORIS_VISION_QA_CACHE_DIR":td}), \
             patch.object(V,"_image_data_uri",return_value="data:image/jpeg;base64,AA=="), \
             patch.object(V,"_vision_chat_json",return_value=dict(base)) as paid:
            V.validate_campaign_banner_vision({"title":"Вариант 1","description":"Текст 1"},banner,None,"acc-a")
            V.validate_campaign_banner_vision({"title":"Вариант 2","description":"Текст 2"},banner,None,"acc-a")
        self.assertEqual(paid.call_count,2)

    def test_banner_qa_keeps_owner_reference_private_but_checks_public_artifact_sharp(self):
        import os, tempfile
        payload={"title":"Гардеробные на заказ","description":"Мебель на заказ"}
        banner=SimpleNamespace(id=20,account_id="acc-a",storage_key="acc-a/banner.png")
        reference=SimpleNamespace(id=21,account_id="acc-a",storage_key="acc-a/private-reference.jpg")
        verdict={"pass":True,"identity_ok":True,"text_ok":True,"mobile_ok":True,"claims_ok":True,"plate_ok":True,
                 "composition_ok":True,"professional_ok":True,"visual_hierarchy_ok":True,"issues":[]}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{"BORIS_VISION_QA_CACHE_DIR":td}), \
             patch.object(V,"_image_data_uri",return_value="data:image/jpeg;base64,AA==") as image_data, \
             patch.object(V,"_vision_chat_json",return_value=dict(verdict)):
            out=V.validate_campaign_banner_vision(payload,banner,reference,"acc-a")
        self.assertTrue(out["pass"])
        self.assertGreaterEqual(len(image_data.call_args_list),2)
        ref_call=image_data.call_args_list[0]
        banner_call=image_data.call_args_list[1]
        self.assertIs(ref_call.args[0],reference)
        self.assertEqual(ref_call.kwargs,{})
        self.assertIs(banner_call.args[0],banner)
        self.assertEqual(banner_call.kwargs,{"max_side":768,"blur_radius":0.0})

    def test_stamp_preserves_cache_checker(self):
        payload={"title":"Кухни на заказ", "description":"Кухня", "direction":"Кухни на заказ"}
        link=SimpleNamespace(position=0)
        asset=SimpleNamespace(id=10)
        verdict={"match":True,"confidence":0.93,"subject":"kitchen cabinets","reason":"cached","checker":"openai_vision_family_cache"}
        V.stamp_item_semantic_evidence(payload,[(link,asset)],[verdict])
        self.assertEqual(payload["media_semantic_evidence"][0]["checker"],"openai_vision_family_cache")

    def test_payload_image_drift_blocks_stale_green_evidence(self):
        payload={
            "title":"Кухни на заказ",
            "description":"Изготовление кухни по размерам",
            "direction":"Кухни на заказ",
            "feed_factory":True,
            "media_semantic_guard":{"mode":"universal_text_image","min_confidence":0.8},
            "images":["https://boris-ai.pro/images/acc-a/current-kitchen.jpg"],
        }
        fp=V.semantic_content_fingerprint(payload)
        payload["media_semantic_evidence"]=[{
            "media_id":10,"position":0,"content_fingerprint":fp,
            "match":True,"confidence":0.95,"subject":"kitchen cabinets"
        }]
        link=SimpleNamespace(position=0)
        asset=SimpleNamespace(
            id=10,storage_key="acc-a/old-kitchen.jpg",source_provider="pexels",
            source_ref="stock",tags=[]
        )
        with patch.object(V,"_linked_media",return_value=[(link,asset)]):
            errs=V.validate_linked_media_semantics(SimpleNamespace(),SimpleNamespace(id=1),payload)
        self.assertEqual([e["code"] for e in errs],["media_semantic_link_payload_drift"])
        self.assertEqual(errs[0]["expected_storage_key"],"acc-a/current-kitchen.jpg")
        self.assertEqual(errs[0]["linked_storage_key"],"acc-a/old-kitchen.jpg")

    def test_reviewed_placement_count_drift_blocks_without_paid_vision(self):
        payload={
            "title":"Кухни на заказ",
            "description":"Изготовление кухни по размерам",
            "direction":"Кухни на заказ",
            "feed_factory":True,
            "media_semantic_guard":{"mode":"universal_text_image","min_confidence":0.8},
            "media_semantic_review":{"passed":True,"placements":8},
            "images":[f"https://boris-ai.pro/images/acc-a/{i}.jpg" for i in range(7)],
        }
        links=[
            (
                SimpleNamespace(position=i),
                SimpleNamespace(
                    id=100+i,storage_key=f"acc-a/{i}.jpg",source_provider="pexels",
                    source_ref="stock",tags=[]
                ),
            )
            for i in range(7)
        ]
        with patch.object(V,"_linked_media",return_value=links), \
             patch.object(V,"_vision_chat_json",side_effect=AssertionError("paid vision called")):
            errs=V.validate_linked_media_semantics(SimpleNamespace(),SimpleNamespace(id=1),payload)
        self.assertEqual([e["code"] for e in errs],["media_semantic_placement_count_drift"])
        self.assertEqual(errs[0]["expected_placements"],8)
        self.assertEqual(errs[0]["payload_images"],7)
        self.assertEqual(errs[0]["linked_media"],7)

    def test_reviewed_placement_count_match_does_not_add_false_error(self):
        payload={
            "title":"Кухни на заказ",
            "description":"Изготовление кухни по размерам",
            "direction":"Кухни на заказ",
            "feed_factory":True,
            "media_semantic_guard":{"mode":"universal_text_image","min_confidence":0.8},
            "media_semantic_review":{"passed":True,"placements":1},
            "images":["https://boris-ai.pro/images/acc-a/current-kitchen.jpg"],
        }
        fp=V.semantic_content_fingerprint(payload)
        payload["media_semantic_evidence"]=[{
            "media_id":10,"position":0,"content_fingerprint":fp,
            "match":True,"confidence":0.95,"subject":"kitchen cabinets"
        }]
        link=SimpleNamespace(position=0)
        asset=SimpleNamespace(
            id=10,storage_key="acc-a/current-kitchen.jpg",source_provider="pexels",
            source_ref="stock",tags=[]
        )
        with patch.object(V,"_linked_media",return_value=[(link,asset)]), \
             patch.object(V,"_vision_chat_json",side_effect=AssertionError("paid vision called")):
            errs=V.validate_linked_media_semantics(SimpleNamespace(),SimpleNamespace(id=1),payload)
        self.assertEqual(errs,[])

    def test_payload_image_binding_accepts_exact_current_media(self):
        payload={
            "title":"Кухни на заказ",
            "description":"Изготовление кухни по размерам",
            "direction":"Кухни на заказ",
            "feed_factory":True,
            "media_semantic_guard":{"mode":"universal_text_image","min_confidence":0.8},
            "images":["https://boris-ai.pro/images/acc-a/current-kitchen.jpg"],
        }
        fp=V.semantic_content_fingerprint(payload)
        payload["media_semantic_evidence"]=[{
            "media_id":10,"position":0,"content_fingerprint":fp,
            "match":True,"confidence":0.95,"subject":"kitchen cabinets"
        }]
        link=SimpleNamespace(position=0)
        asset=SimpleNamespace(
            id=10,storage_key="acc-a/current-kitchen.jpg",source_provider="pexels",
            source_ref="stock",tags=[]
        )
        with patch.object(V,"_linked_media",return_value=[(link,asset)]):
            errs=V.validate_linked_media_semantics(SimpleNamespace(),SimpleNamespace(id=1),payload)
        self.assertEqual(errs,[])

    def test_mortgage_family_positive_asset_cache_skips_repeat_paid_vision(self):
        payload={"title":"Помощь с одобрением ипотеки", "description":"Ипотечный брокер, консультация по кредиту", "direction":"Одобрение ипотеки"}
        link=SimpleNamespace(position=1)
        asset=SimpleNamespace(id=12077, account_id="acc-a", tags=[
            "vision_family_confirmed:mortgage_broker",
            "vision_family_confidence:0.940",
            "vision_subject:mortgage broker meeting client",
        ])
        with patch.object(V, "_vision_chat_json", side_effect=AssertionError("paid vision called")):
            out=V.classify_item_media_vision(payload,[(link,asset)],account_id="acc-a")
        self.assertTrue(out[0]["match"])
        self.assertTrue(out[0]["cached"])
        self.assertEqual(out[0]["checker"],"openai_vision_family_cache")

    def test_mortgage_family_does_not_accept_unrelated_subject(self):
        payload={"title":"Ипотечный брокер", "description":"Помощь с ипотекой", "direction":"Одобрение ипотеки"}
        self.assertFalse(V._clear_subject_family_match(payload,"sports car on a road"))
        self.assertTrue(V._clear_subject_family_match(payload,"mortgage broker meeting client"))


if __name__ == "__main__":
    unittest.main()



def test_full_ai_premium_banner_uses_stricter_banner_qa_without_duplicate_generic_proof():
    payload={
        "title":"Кухни на заказ",
        "description":"Изготовление кухни по размерам",
        "direction":"Кухни на заказ",
        "feed_factory":True,
        "media_semantic_guard":{"mode":"universal_text_image","min_confidence":0.8},
        "banner_url":"/images/acc-a/banners/premium_v1.png",
        "banner_qa":{
            "passed":True,
            "source":"premium_full_ai_vision_qa",
            "vision_qa":{
                "passed":True,
                "semantic_match":True,
                "exact_text_ok":True,
                "composition_ok":True,
                "professional_ok":True,
                "mobile_ok":True,
            },
        },
        "images":["/images/acc-a/banners/premium_v1.png"],
        "media_semantic_evidence":[],
    }
    link=SimpleNamespace(position=0)
    asset=SimpleNamespace(
        id=501,
        storage_key="acc-a/banners/premium_v1.png",
        source_provider="openai_full_ai_premium",
        source_ref="premium:test:v1",
        tags=[],
    )
    with patch.object(V,"_linked_media",return_value=[(link,asset)]):
        errs=V.validate_linked_media_semantics(SimpleNamespace(),SimpleNamespace(id=1),payload)
    assert errs == []
