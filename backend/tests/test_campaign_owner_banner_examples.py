"""Isolated checks for optional owner banner style memory; no providers or writes."""
import importlib.util
import unittest
from pathlib import Path

P=Path(__file__).parents[1]/"app/services/campaign_owner_banner_examples.py"
S=importlib.util.spec_from_file_location("owner_banner_example_under_test",P)
M=importlib.util.module_from_spec(S)
S.loader.exec_module(M)

class TestOwnerBannerExamples(unittest.TestCase):
    def test_scope(self):
        self.assertEqual(len(M.PROFILES),5)
        self.assertIsNone(M.owner_variant("unknown","sample"))
    def test_replay(self):
        for a in M.PROFILES:
            self.assertEqual(M.owner_variant(a,"same"),M.owner_variant(a,"same"))
    def test_color_variety(self):
        for a in M.PROFILES:
            colors={M.owner_variant(a,str(i))["palette_key"] for i in range(70)}
            self.assertEqual(len(colors),5)
    def test_history(self):
        for a in M.PROFILES:
            first=M.owner_variant(a,"single")["palette_key"]
            second=M.owner_variant(a,"single",previous_palettes=(first,))
            self.assertNotEqual(first,second["palette_key"])
    def test_claims(self):
        for a in M.PROFILES:
            s=M.owner_variant(a,"case")["reference_style"]
            self.assertIn("No made-up prices",s)
            self.assertIn("LARGE",s)
    def test_new_category_profiles_are_not_false_owner_image_references(self):
        for account in ("greench84_48016", "evgeniy_peregorodki_12447"):
            v = M.owner_variant(account, "preview")
            self.assertIsNotNone(v)
            self.assertIsNone(v["owner_library_file"])
            self.assertIsNone(v["owner_library_ref"])
            self.assertIsNone(v["local_preview_image"])
            self.assertIn("NO IMAGE REFERENCE", v["reference_style"])
            self.assertIn("Reject cropped", v["reference_style"])
            self.assertIn("No made-up prices", v["reference_style"])

    def test_quality_direction_flows_into_existing_vision_review(self):
        from app.services.banner_full_ai_premium import _vision_contract
        for account, title in (
            ("planetazayavki_65985", "Кухни на заказ"),
            ("greench84_48016", "Металлочерепица"),
            ("evgeniy_peregorodki_12447", "Стеклянная перегородка для душа"),
        ):
            v = M.owner_variant(account, "unique-banner")
            c = _vision_contract(
                description=title, exact_title=title, exact_subtitle="",
                exact_cta="Напишите в Avito", exact_price="", advantages=[],
                hypothesis="product_focus", fmt="square", owner_example=v,
            )
            self.assertIn("No made-up prices", c["owner_reference_notes"])
            self.assertIn("Reject cropped", c["owner_reference_notes"])
            self.assertEqual(c["cta"], "Напишите в Avito")
            self.assertEqual(c["owner_palette"], v["palette"])

    def test_custom(self):
        v=M.owner_variant("mbroker_27859","case")
        self.assertIn("CUSTOMER STYLE",M.combine_reference_style(v,"special"))
        self.assertLessEqual(len(M.combine_reference_style(v,"x"*1000)),2350)

if __name__=="__main__":
    unittest.main()
