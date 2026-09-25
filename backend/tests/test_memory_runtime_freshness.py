import unittest
from datetime import datetime, timedelta, timezone

from app.api.client_memory import runtime_fact_expired


class MemoryFreshnessTests(unittest.TestCase):
    def test_explicit_promo_deadline_expires_even_if_recent(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.assertTrue(runtime_fact_expired(
            "faq",
            "Аренда бытовки",
            "АКЦИОННЫЕ ЦЕНЫ ДО 16.08.2026: обычная 6000",
            now - timedelta(days=2),
            now - timedelta(days=2),
            now=now,
        ))

    def test_promo_like_faq_uses_short_promo_ttl(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.assertTrue(runtime_fact_expired(
            "faq",
            "Цена аренды",
            "АКЦИОННЫЕ ЦЕНЫ: обычная 6000",
            None,
            now - timedelta(days=20),
            now=now,
        ))

    def test_old_availability_claim_expires_quickly(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.assertTrue(runtime_fact_expired(
            "faq",
            "Есть ли свежие варианты",
            "Сейчас в аренде есть бытовки 2024 года, в наличии несколько вариантов.",
            None,
            now - timedelta(days=8),
            now=now,
        ))

    def test_recent_regular_price_remains_usable(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.assertFalse(runtime_fact_expired(
            "cena",
            "Монтаж",
            "5000",
            now - timedelta(days=10),
            now - timedelta(days=10),
            now=now,
        ))

    def test_old_regular_price_expires_by_ttl(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.assertTrue(runtime_fact_expired(
            "cena",
            "Монтаж",
            "5000",
            now - timedelta(days=90),
            now - timedelta(days=90),
            now=now,
        ))


if __name__ == "__main__":
    unittest.main()
