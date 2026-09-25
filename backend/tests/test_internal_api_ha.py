import unittest
from unittest.mock import patch

import httpx

import kpi_goal_runner as kpi
import daily_stats_collector as stats
import cpx_advisor_runner as cpx


class _FakeResponse:
    def __init__(self, data=None, status_code=200):
        self._data = data if data is not None else {"status": "ok"}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad status",
                request=httpx.Request("GET", "http://local/test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._data


class _KpiClient:
    def __init__(self):
        self.urls = []

    def post(self, url, **kwargs):
        self.urls.append(url)
        if ":8000/" in url:
            raise httpx.ConnectError("primary unavailable")
        return _FakeResponse({"status": "ok", "via": "replica"})


class InternalApiHATests(unittest.TestCase):
    def test_kpi_connect_failure_fails_over_before_repeating_money_post(self):
        client = _KpiClient()
        out = kpi.call_tick(client, "qa-ha")
        self.assertEqual(out["via"], "replica")
        self.assertEqual(len(client.urls), 2)
        self.assertIn(":8000/", client.urls[0])
        self.assertIn(":8001/", client.urls[1])

    def test_daily_stats_get_fails_over_to_replica(self):
        seen = []

        def fake_get(url, **kwargs):
            seen.append(url)
            if ":8000/" in url:
                raise httpx.ConnectError("primary unavailable")
            return _FakeResponse({"status": "ok", "snapshot": {}})

        with patch.object(stats.httpx, "get", side_effect=fake_get):
            out = stats._collect_one("qa-ha", attempts=1)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(seen), 2)
        self.assertIn(":8001/", seen[1])

    def test_cpx_local_url_preserves_path_and_query_on_replica(self):
        seen = []

        def fake_request(method, url, **kwargs):
            seen.append((method, url, kwargs.get("json")))
            if ":8000/" in url:
                raise httpx.ConnectError("primary unavailable")
            return _FakeResponse({"status": "ok"})

        payload = {"account_id": "qa-ha", "request_id": "stable-id"}
        with patch.object(cpx.httpx, "request", side_effect=fake_request):
            response = cpx._request_with_retry(
                "POST",
                "http://127.0.0.1:8000/api/cpx_advisor/apply_one?source=qa",
                attempts=1,
                json=payload,
                timeout=2,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1][1].endswith("/api/cpx_advisor/apply_one?source=qa"))
        self.assertEqual(seen[1][2]["request_id"], "stable-id")


if __name__ == "__main__":
    unittest.main()
