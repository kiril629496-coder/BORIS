import unittest
from unittest.mock import patch

from app.api import avito


class GenerationIdempotencyTests(unittest.TestCase):
    def test_cached_result_short_circuits_paid_provider(self):
        req = avito.GenerateAdsRequest(topic="Тест", count=1, account_id="qa", idempotency_key="job:1:item:1")
        cached = {"status": "ok", "count": 1, "ads": [{"title": "Готово", "description": "Текст", "price": 1}], "idempotency_cache_hit": True}
        with patch.object(avito, "_generation_idempotency_get", return_value=cached), \
             patch.object(avito, "chat_with_fallback") as paid:
            out = avito.generate_ads(req)
        self.assertTrue(out["idempotency_cache_hit"])
        paid.assert_not_called()

    def test_python_literal_repair_avoids_second_paid_call(self):
        req = avito.GenerateAdsRequest(topic="Ремонт", count=1, account_id="qa", use_my_ads=False)
        almost_json = "[{'title': 'Ремонт квартир', 'description': 'Сделаем аккуратно', 'price': 1000,}]"
        with patch.object(avito, "_generation_idempotency_get", return_value=None), \
             patch.object(avito, "_generation_idempotency_put"), \
             patch.object(avito, "chat_with_fallback", return_value=almost_json) as paid:
            out = avito.generate_ads(req)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(out["count"], 1)

    def test_unrecoverable_format_uses_local_fallback_without_second_paid_call(self):
        req = avito.GenerateAdsRequest(topic="Ремонт фар", count=1, account_id="qa", use_my_ads=False, goal="message")
        with patch.object(avito, "_generation_idempotency_get", return_value=None), \
             patch.object(avito, "_generation_idempotency_put"), \
             patch.object(avito, "chat_with_fallback", return_value="совсем не json") as paid:
            out = avito.generate_ads(req)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out.get("local_fallback"))
        self.assertEqual(paid.call_count, 1)
        text = " ".join(a.get("description", "") for a in out.get("ads", []))
        self.assertNotIn("5 минут", text)
        self.assertNotIn("бесплат", text.lower())

    def test_storage_key_is_account_scoped_and_stable(self):
        a = avito.GenerateAdsRequest(topic="x", account_id="acc-a", idempotency_key="job:7:item:9")
        b = avito.GenerateAdsRequest(topic="different", account_id="acc-a", idempotency_key="job:7:item:9")
        self.assertEqual(avito._generation_idempotency_storage_key(a), avito._generation_idempotency_storage_key(b))
        self.assertTrue(avito._generation_idempotency_storage_key(a).startswith("generation_idempotency:"))
        self.assertEqual(avito._generation_idempotency_storage_key(avito.GenerateAdsRequest(topic="x", idempotency_key="")), "")


if __name__ == "__main__":
    unittest.main()
