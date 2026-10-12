"""Offline, no-money regression tests for Russian niche copy quality."""
import unittest
from app.services.campaign_niche_copy import compose_niche_copy, factual_niche_example


class TestNicheCopy(unittest.TestCase):
    cases = (
        ("Стеклянная перегородка в душ", "Санкт-Петербург"),
        ("Металлочерепица Grand Line", "Санкт-Петербург"),
        ("Панорамные окна", "Казань"),
        ("Кухни на заказ", "Уфа"),
        ("Подбор квартир", "Москва"),
        ("Семейная ипотека", "Москва"),
    )

    def test_commercial_copy_has_punctuation_headings_and_direct_action(self):
        for title, city in self.cases:
            for variant in (1, 2):
                with self.subTest(title=title, variant=variant):
                    copy = compose_niche_copy(title, {"city": city, "variant": variant})
                    self.assertIsNotNone(copy)
                    self.assertIn(title.upper(), copy)
                    self.assertIn(city.upper(), copy)
                    self.assertIn("<strong>✅", copy)
                    self.assertIn("<strong>💬", copy)
                    self.assertTrue("Напишите в Avito" in copy or "Пришлите в Avito" in copy)
                    self.assertGreater(len(copy), 650)
                    self.assertNotIn("под ваш сценарий", copy.lower())
                    self.assertNotIn("Пример услуги: Направление:", copy)
                    self.assertNotIn("61000", copy)
                    self.assertNotIn("решение под вашу задачу", copy.lower())
                    self.assertNotIn("в ·", copy.lower())
                    self.assertNotIn("руб..", copy.lower())
                    self.assertTrue(copy.rstrip().endswith("."))

    def test_variants_differ_without_changing_product_facts(self):
        for title, city in self.cases:
            a = compose_niche_copy(title, {"city": city, "variant": 1})
            b = compose_niche_copy(title, {"city": city, "variant": 2})
            self.assertNotEqual(a, b)
            self.assertIn(title.upper(), b)

    def test_factual_glass_specs_exact_only(self):
        fields = {"glass_type": "Закалённое", "glass_thickness": "8 мм",
                  "glass_size": "700 × 1900 мм", "glass_color": "Прозрачное",
                  "construction_size": "750 × 1950 мм"}
        text, meta = factual_niche_example("Душевая стеклянная перегородка", fields)
        self.assertTrue(meta["present"])
        self.assertIn("700 × 1900 мм", text)
        self.assertIn("750 × 1950 мм", text)
        self.assertIn("8 мм", text)
        self.assertNotIn("900 × 1900", text)
        self.assertNotIn("цвет фурнитуры", text.lower())

    def test_missing_facts_are_not_invented(self):
        for title, _ in self.cases:
            text, meta = factual_niche_example(title, {"city": "Москва", "price": 99999})
            self.assertFalse(meta["present"])
            self.assertTrue(meta["needs_data"])
            self.assertEqual(text, "")

    def test_mortgage_is_not_mistaken_for_real_estate_search(self):
        text = compose_niche_copy("Семейная ипотека", {"city": "Москва"})
        self.assertIn("предложении банка", text.lower())
        self.assertNotIn("количество комнат", text.lower())

    def test_unknown_topic_keeps_legacy_path(self):
        self.assertIsNone(compose_niche_copy("Ремонт компьютеров", {"city": "Тула"}))
        self.assertIsNone(factual_niche_example("Ремонт компьютеров", {}))


if __name__ == "__main__":
    unittest.main()
