import unittest
import os
import tempfile
from unittest.mock import patch

from app.services import media_semantic_validator as V


class _Headers(dict):
    pass


class _Response:
    def __init__(self, body, request_id="req-vision-safe", status_code=200):
        self._body = body
        self.status_code = status_code
        self.headers = _Headers({"x-request-id": request_id})
    def raise_for_status(self):
        return None
    def json(self):
        return self._body


class _Link:
    def __init__(self, position=0):
        self.position = position


class _Asset:
    def __init__(self, media_id=7, account_id="acc-a"):
        self.id = media_id
        self.account_id = account_id


class MediaVisionCostSafetyTests(unittest.TestCase):
    def test_budget_denied_blocks_http(self):
        with patch("app.services.ai_budget.reserve", return_value={"status":"blocked"}), \
             patch("requests.post", side_effect=AssertionError("HTTP must not run")):
            with self.assertRaisesRegex(RuntimeError, "VISION_BUDGET_RESERVE_FAILED"):
                V._vision_chat_json("acc-a", "qa", [{"type":"text","text":"x"}], 20, 10)

    def test_success_logs_factual_request_and_commits_once(self):
        body={
            "choices":[{"message":{"content":"{\"ok\": true}"}}],
            "usage":{"prompt_tokens":321,"completion_tokens":12,"total_tokens":333},
        }
        with tempfile.TemporaryDirectory() as cache_dir, \
             patch.dict(os.environ, {"BORIS_VISION_PAID_CACHE_DIR": cache_dir}), \
             patch("app.services.ai_budget.reserve", return_value={"status":"reserved"}), \
             patch("app.services.ai_budget.allow_next", return_value={"allowed":True}), \
             patch("app.services.ai_budget.commit", return_value={"status":"committed"}) as commit, \
             patch("app.services.ai_budget.release", return_value={"status":"released"}) as release, \
             patch("app.usage.log_usage", return_value=0.02) as usage, \
             patch("requests.post", return_value=_Response(body)) as post:
            out=V._vision_chat_json("acc-a","media_semantic_item_vision",[{"type":"text","text":"x"}],50,10,provenance={"campaign_id":7,"item_id":11})
        self.assertEqual(out,{"ok":True})
        self.assertEqual(post.call_count,1)
        self.assertEqual(usage.call_count,1)
        kwargs=usage.call_args.kwargs
        self.assertEqual(kwargs["request_id"],"req-vision-safe")
        self.assertEqual(kwargs["prompt_tokens"],321)
        self.assertEqual(kwargs["completion_tokens"],12)
        self.assertEqual(kwargs["usage_details"]["boris_provenance"], {"campaign_id":7,"item_id":11})
        self.assertTrue(kwargs["usage_details"]["boris_intent_key"].startswith("vision:"))
        commit.assert_called_once_with(unittest.mock.ANY,"req-vision-safe")
        release.assert_called_once()

    def test_partial_item_cache_sends_only_unproven_media(self):
        payload={"title":"Кухни на заказ Уфа","description":"Кухня по размерам","category":"Мебель","direction":"Кухни на заказ"}
        fp=V.semantic_content_fingerprint(payload)
        payload["media_semantic_evidence"]=[{
            "media_id":7,"position":0,"content_fingerprint":fp,"match":True,
            "confidence":0.95,"subject":"kitchen with cabinets","reason":"matches",
            "checker":"openai_vision_text_image",
        }]
        linked=[(_Link(0),_Asset(7)),(_Link(1),_Asset(8))]
        seen={}
        def fake_vision(account_id,operation,content,max_tokens,timeout,return_meta=False,provenance=None):
            seen["images"]=sum(1 for x in content if x.get("type")=="image_url")
            return [{"match":True,"confidence":0.92,"subject":"modern kitchen cabinetry","reason":"matches"}]
        with patch.object(V,"_image_data_uri",return_value="data:image/jpeg;base64,AA"), \
             patch.object(V,"_vision_chat_json",side_effect=fake_vision):
            out=V.classify_item_media_vision(payload,linked,account_id="acc-a")
        self.assertEqual(seen["images"],1)
        self.assertEqual(len(out),2)
        self.assertTrue(out[0]["cached"])
        self.assertTrue(out[1]["match"])

    def test_provider_text_wrapper_recovers_valid_json(self):
        body={
            "choices":[{"message":{"content":"Проверка завершена. {\"ok\": true}"}}],
            "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15},
        }
        with (tempfile.TemporaryDirectory() as cache_dir,
              patch.dict(os.environ, {"BORIS_VISION_PAID_CACHE_DIR": cache_dir}),
              patch("app.services.ai_budget.reserve", return_value={"status":"reserved"}),
              patch("app.services.ai_budget.allow_next", return_value={"allowed":True}),
              patch("app.services.ai_budget.commit", return_value={"status":"committed"}),
              patch("app.services.ai_budget.release", return_value={"status":"released"}),
              patch("app.usage.log_usage", return_value=0.01),
              patch("requests.post", return_value=_Response(body))):
            out=V._vision_chat_json("acc-json-wrap","media_semantic_item_vision",
                                    [{"type":"text","text":"wrapped-json"}],50,10)
        self.assertEqual(out,{"ok":True})

    def test_large_gallery_is_chunked_four_four_one(self):
        payload={"title":"Панорамные окна Казань","description":"Остекление частного дома",
                 "category":"Окна","direction":"Панорамные окна в частный дом"}
        linked=[(_Link(i),_Asset(100+i)) for i in range(9)]
        calls=[]
        def fake_vision(account_id,operation,content,max_tokens,timeout,return_meta=False,provenance=None):
            n=sum(1 for x in content if x.get("type")=="image_url")
            calls.append(n)
            return [{"index":i+1,"match":True,"confidence":0.95,
                     "subject":"window glazing","reason":"matches"} for i in range(n)]
        with (patch.object(V,"_image_data_uri",return_value="data:image/jpeg;base64,AA"),
              patch.object(V,"_vision_chat_json",side_effect=fake_vision)):
            out=V.classify_item_media_vision(payload,linked,account_id="acc-a")
        self.assertEqual(calls,[4,4,1])
        self.assertEqual(len(out),9)
        self.assertTrue(all(x["match"] for x in out))

    def test_exact_item_evidence_cache_avoids_vision_http(self):
        payload={"title":"Кухни на заказ Уфа","description":"Кухня по размерам","category":"Мебель","direction":"Кухни на заказ"}
        fp=V.semantic_content_fingerprint(payload)
        payload["media_semantic_evidence"]=[{
            "media_id":7,"position":0,"content_fingerprint":fp,"match":True,
            "confidence":0.95,"subject":"kitchen with cabinets","reason":"matches",
            "checker":"openai_vision_text_image",
        }]
        linked=[(_Link(0),_Asset(7))]
        with patch.object(V,"_vision_chat_json",side_effect=AssertionError("paid vision must not run")):
            out=V.classify_item_media_vision(payload,linked,account_id="acc-a")
        self.assertEqual(len(out),1)
        self.assertTrue(out[0]["match"])
        self.assertTrue(out[0]["cached"])


if __name__ == "__main__":
    unittest.main()
