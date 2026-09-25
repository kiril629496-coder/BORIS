# -*- coding: utf-8 -*-
"""Fail-closed Avito daily-stats completeness contracts.

Stdlib unittest only: production venv intentionally has no pytest dependency.
All transports are mocked; no production DB writes or external calls occur.
"""
import unittest
from unittest.mock import patch
import httpx

from app.api import avito as A


class Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
    def json(self):
        return self._payload


class NoDB:
    def __call__(self):
        raise AssertionError("partial collection must not open persistence session")


class AvitoStatsCompletenessTests(unittest.TestCase):
    def auth_patches(self):
        return (
            patch.object(A, "get_avito_token", return_value={"access_token": "qa"}),
            patch.object(A, "_extract_token", return_value="qa"),
            patch.object(A, "_get_avito_credentials", return_value=("id", "secret", "42")),
            patch("app.db.session.SessionLocal", NoDB()),
        )

    def test_stats_batch_failure_preserves_previous_full_snapshot(self):
        items = [{"id": str(i), "title": f"item {i}", "price": 0,
                  "category": {"name": "x"}, "status": "active"} for i in range(250)]
        def fake_get(url, **kwargs):
            self.assertIn("/core/v1/items", url)
            page = int(kwargs["params"]["page"]); start = (page - 1) * 100
            return Resp(200, {"resources": items[start:start + 100]})
        calls = {"stats": 0}
        def fake_post(url, **kwargs):
            self.assertIn("/stats/v1/", url)
            calls["stats"] += 1
            if calls["stats"] == 1:
                ids = kwargs["json"]["itemIds"]
                return Resp(200, {"result": {"items": [{"itemId": x, "stats": []} for x in ids]}})
            return Resp(503, {})
        patches = self.auth_patches() + (patch.object(A, "_avito_http_get", side_effect=fake_get), patch.object(A, "_avito_http_post", side_effect=fake_post))
        for p in patches: p.start()
        try: out = A.collect_stats("qa")
        finally:
            for p in reversed(patches): p.stop()
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["reason"], "avito_stats_batches_incomplete")
        self.assertFalse(out["completeness"]["complete"])
        self.assertEqual(out["completeness"]["stats_batches_total"], 2)
        self.assertEqual(out["completeness"]["stats_batches_ok"], 1)
        self.assertEqual(calls["stats"], 3)

    def test_later_inventory_page_failure_preserves_previous_full_snapshot(self):
        first = [{"id": str(i)} for i in range(100)]
        def fake_get(url, **kwargs):
            if int(kwargs["params"]["page"]) == 1: return Resp(200, {"resources": first})
            raise httpx.ReadTimeout("synthetic")
        patches = self.auth_patches() + (patch.object(A, "_avito_http_get", side_effect=fake_get),)
        for p in patches: p.start()
        try: out = A.collect_stats("qa")
        finally:
            for p in reversed(patches): p.stop()
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["reason"], "avito_inventory_incomplete")
        self.assertFalse(out["completeness"]["complete"])
        self.assertEqual(out["collected_items"], 100)


    def test_kpi_check_blocks_actions_when_live_refresh_is_degraded(self):
        degraded = {"status":"degraded","reason":"avito_stats_batches_incomplete","retryable":True}
        with patch.object(A, "collect_stats", return_value=degraded), \
             patch("app.db.session.SessionLocal", NoDB()):
            out = A.kpi_check("qa", refresh_live=True)
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["reason"], "live_stats_incomplete")
        self.assertEqual(out["suggested_action"], "blocked_stats_incomplete")
        self.assertFalse(out["money_actions_allowed"])

    def test_exact_2000_inventory_is_not_silently_complete(self):
        page_items = [{"id": str(i)} for i in range(100)]
        def fake_get(url, **kwargs): return Resp(200, {"resources": page_items})
        patches = self.auth_patches() + (patch.object(A, "_avito_http_get", side_effect=fake_get),)
        for p in patches: p.start()
        try: out = A.collect_stats("qa")
        finally:
            for p in reversed(patches): p.stop()
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["completeness"]["inventory_issue"], "inventory_limit_2000_reached")
        self.assertEqual(out["collected_items"], 2000)


if __name__ == "__main__":
    unittest.main()
