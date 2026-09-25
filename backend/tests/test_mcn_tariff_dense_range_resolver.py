import unittest

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import mcn_core


TARIFF = "__qa_dense_mobile_range_resolver__"
DIRECTION = "qa_dense_mobile"


class McnTariffDenseRangeResolverTests(unittest.TestCase):
    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with SessionLocal() as db:
            db.execute(
                text("DELETE FROM telephony_tariff_versions WHERE operator='mcn' AND name=:n"),
                {"n": TARIFF},
            )
            db.commit()

    def test_matching_range_beyond_first_200_prefix_rows_is_resolved(self):
        version = mcn_core.tariff_version_upsert({
            "name": TARIFF,
            "effective_from": "2026-09-06",
            "status": "active",
            "vat_mode": "without_vat",
            "source_name": "qa_dense_ranges",
            "metadata": {"qa": True},
        })
        self.assertEqual(version["status"], "ok")
        vid = int(version["version"]["id"])

        rates = []
        # All 300 rows share the same prefix but intentionally do not contain
        # the target. The historical resolver LIMIT 200 discarded a later
        # valid range before applying Python-side range filtering.
        for i in range(300):
            lo = 79990000000 + i * 1000
            hi = lo + 999
            rates.append({
                "direction_type": DIRECTION,
                "label": f"nonmatch-{i}",
                "prefix": "7999",
                "range_from": str(lo),
                "range_to": str(hi),
                "price": "1.00",
            })
        rates.append({
            "direction_type": DIRECTION,
            "label": "target",
            "prefix": "7999",
            "range_from": "79999999000",
            "range_to": "79999999999",
            "price": "9.99",
        })
        loaded = mcn_core.tariff_rate_bulk_replace(vid, rates)
        self.assertEqual(loaded["status"], "ok")
        self.assertEqual(loaded["rates"], 301)

        row = mcn_core.tariff_rate_resolve(
            "+79999999999",
            direction_type=DIRECTION,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "target")
        self.assertEqual(str(row["price"]), "9.990000")


if __name__ == "__main__":
    unittest.main()
