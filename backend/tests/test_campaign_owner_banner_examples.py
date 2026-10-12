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

    def test_mobile_central_safe_zone_and_one_design_per_image(self):
        from app.services.banner_full_ai_premium import build_prompt, _vision_contract
        for account in M.PROFILES:
            with self.subTest(account=account):
                variant = M.owner_variant(account, "mobile-preview")
                style = variant["reference_style"]
                self.assertIn("ONE BANNER PER IMAGE", style)
                self.assertIn("x=15%-85%, y=15%-85%", style)
                self.assertIn("320px mobile thumbnail", style)
                self.assertIn("9-12% of final image height", style)
                self.assertIn("NO small footer badges", style)
                merged = M.combine_reference_style(variant, "client preference")
                self.assertIn("ONE BANNER PER IMAGE", merged)
                self.assertIn("x=15%-85%, y=15%-85%", merged)
                prompt = build_prompt(
                    description="Verified offer", exact_title="ОКНА НА ЗАКАЗ",
                    exact_cta="Напишите в Avito",
                    reference_style=merged, fmt="1:1",
                )
                self.assertIn("ONE BANNER PER IMAGE", prompt)
                self.assertIn("320px mobile thumbnail", prompt)
                qa = _vision_contract(
                    description="Verified offer", exact_title="ОКНА НА ЗАКАЗ",
                    exact_subtitle="", exact_cta="Напишите в Avito",
                    exact_price="", advantages=[], hypothesis="product_focus",
                    fmt="1:1", owner_example=variant,
                )
                self.assertIn("ONE BANNER PER IMAGE", qa["owner_reference_notes"])
                self.assertIn("x=15%-85%, y=15%-85%", qa["owner_reference_notes"])

    def test_required_claim_and_product_guards_survive_long_customer_style(self):
        from app.services.banner_full_ai_premium import build_prompt, _vision_contract
        for account, profile in M.PROFILES.items():
            with self.subTest(account=account):
                v = M.owner_variant(account, "large-customer-brief")
                merged = M.combine_reference_style(v, "X" * 850)
                self.assertLessEqual(len(merged), 2350)
                for required in (
                    "ONE BANNER PER IMAGE", "No made-up prices",
                    "Subject rule: " + profile["subject"],
                    "x=15%-85%, y=15%-85%", "320px mobile thumbnail",
                    "CUSTOMER STYLE:",
                ):
                    self.assertIn(required, merged)
                prompt = build_prompt(
                    description="Товар по подтверждённой спецификации",
                    exact_title="ТОЛЬКО ПРОВЕРЕННЫЙ ТЕКСТ",
                    exact_cta="Напишите в Avito",
                    reference_style=merged,
                )
                self.assertIn("No made-up prices", prompt)
                self.assertIn("Subject rule: " + profile["subject"], prompt)
                c = _vision_contract(
                    description="Товар по подтверждённой спецификации",
                    exact_title="ТОЛЬКО ПРОВЕРЕННЫЙ ТЕКСТ",
                    exact_subtitle="", exact_cta="Напишите в Avito",
                    exact_price="", advantages=[], hypothesis="product_hero",
                    fmt="1:1", owner_example=v,
                )
                self.assertIn("Subject rule: " + profile["subject"], c["owner_reference_notes"])

    def test_custom(self):
        v=M.owner_variant("mbroker_27859","case")
        self.assertIn("CUSTOMER STYLE",M.combine_reference_style(v,"special"))
        self.assertLessEqual(len(M.combine_reference_style(v,"x"*1000)),2350)

if __name__=="__main__":
    unittest.main()
