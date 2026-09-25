import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api import campaigns
from app.services import image_service


class CampaignAutoMediaSafetyTests(unittest.TestCase):
    def test_empty_selection_stops_before_any_paid_image_path(self):
        body = campaigns.FFAutoMediaBody(item_ids=[], count_per_item=1)
        with patch.object(campaigns, "_allowed_accounts", return_value={"acc-a"}), \
             patch.object(campaigns, "_campaign_or_404", return_value=SimpleNamespace(id=7)), \
             patch.object(campaigns, "_ff_state_load", return_value=({}, {})), \
             patch.object(campaigns, "_ff_items", side_effect=AssertionError("items must not be loaded")), \
             patch.object(image_service, "generate", side_effect=AssertionError("paid image generate called")), \
             patch.object(image_service, "generate_banner_financially_guarded", side_effect=AssertionError("paid banner generate called")):
            out = campaigns.ff_workflow_auto_media(7, body, db=SimpleNamespace(), user=SimpleNamespace())
        self.assertEqual(out["status"], "selection_required")
        self.assertEqual(out["changed"], [])

    def test_auto_media_source_has_no_paid_image_generator_reference(self):
        import inspect
        source = inspect.getsource(campaigns.ff_workflow_auto_media)
        self.assertNotIn("generate_banner_financially_guarded", source)
        self.assertNotIn("image_service.generate", source)
        self.assertNotIn("generate_ai_image", source)


if __name__ == "__main__":
    unittest.main()

class CampaignDeliveryProjectionRegressionTests(unittest.TestCase):
    def test_delivery_projection_prefers_canonical_media_links_over_stale_payload_images(self):
        class Query:
            def join(self,*a,**k): return self
            def filter(self,*a,**k): return self
            def order_by(self,*a,**k): return self
            def all(self):
                return [
                    (SimpleNamespace(position=0,id=1), SimpleNamespace(storage_key="acc/card/a.jpg")),
                    (SimpleNamespace(position=1,id=2), SimpleNamespace(storage_key="acc/card/b.jpg")),
                    (SimpleNamespace(position=2,id=3), SimpleNamespace(storage_key="acc/card/b.jpg")),
                ]
        class DB:
            def query(self,*a,**k): return Query()
        row=SimpleNamespace(
            id=77, account_id="acc", geo="Москва", segment="Ипотека",
            payload_json='{"title":"Ипотечный брокер","description":"Помощь с ипотекой. Пишите нам.","address":"Москва","category":"Предложение услуг","category_path":"Услуги > Предложение услуг > Услуги посредников > Консультации","images":["/images/stale/one.jpg"]}',
        )
        captured=[]
        with patch("app.services.campaign_identity.ensure_feed_identity", return_value="ff:test"), \
             patch("app.api.avito._draft_to_feed_item", side_effect=lambda p: captured.append(dict(p)) or dict(p)), \
             patch("app.api.avito._build_xml", return_value="<Ads/>"):
            items, xml = campaigns._ff_delivery_projection([row], DB())
        self.assertEqual(xml,"<Ads/>")
        self.assertEqual(captured[0]["images"],["/images/acc/card/a.jpg","/images/acc/card/b.jpg"])
        self.assertNotIn("/images/stale/one.jpg",captured[0]["images"])

    def test_validation_blocks_template_category_path_mismatch(self):
        payload={
            "title":"Ипотечный брокер Москва",
            "description":"Помощь с ипотекой в Москве. Напишите сообщение, чтобы обсудить ситуацию и документы.",
            "address":"Москва", "category":"Предложение услуг", "price":0,
            "images":["https://example.test/image.jpg"], "template_id":"133104",
            "category_path":"Услуги > Предложение услуг > Деловые услуги > Консультации по недвижимости",
            "params":{},
        }
        row=SimpleNamespace(id=88,account_id="acc",status="draft",payload_json=__import__('json').dumps(payload,ensure_ascii=False))
        contract={"ok":True,"category_path":"Услуги > Предложение услуг > Услуги посредников > Консультации","fields":{},"contract_version":"v1"}
        with patch("app.api.feed_contract.resolve_feed_contract",return_value=contract), \
             patch("app.api.feed_contract.validate_feed_item",return_value={"errors":[],"contract_version":"v1"}), \
             patch("app.services.media_semantic_validator.validate_linked_media_semantics",return_value=[]), \
             patch.object(campaigns,"_ff_copy_quality_errors",return_value=[]):
            rep=campaigns._ff_validate_row(row,SimpleNamespace())
        codes=[e.get("code") for e in rep["errors"]]
        self.assertIn("category_contract_mismatch",codes)
        self.assertFalse(rep["valid"])

class CampaignSafeExportStateRegressionTests(unittest.TestCase):
    def test_safe_export_source_requires_current_official_sha_before_safe_feed(self):
        import inspect
        source=inspect.getsource(campaigns.campaign_safe_export)
        self.assertIn('_official_sha==xml_sha', source)
        self.assertIn('"safe_feed" if _verified else "validation"', source)
        self.assertIn('official_validation_current', source)
