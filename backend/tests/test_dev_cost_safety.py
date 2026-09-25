# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from app.ext_api import aiprov, executor
from app.ext_api.errors import ApiError


class DevCostSafetyTests(unittest.TestCase):
    def test_client_openai_routing_is_unchanged(self):
        state = {
            "openai": {"state": aiprov.AVAILABLE, "usable": True},
            "claude_code": {"state": aiprov.DISABLED_BY_OWNER, "usable": False},
            "anthropic_api": {"state": aiprov.DISABLED_BY_OWNER, "usable": False},
        }
        with patch.object(aiprov, "state", side_effect=lambda provider=None: state if provider is None else {"state": aiprov.AVAILABLE, "usable": True}), \
             patch.object(aiprov, "owner_policy", return_value={"anthropic_api"}):
            self.assertIn("openai", aiprov.candidates(aiprov.TEXT))

    def test_dev_candidates_are_gemini_primary_and_paid_denied(self):
        state = {
            "gemini_cli": {"state": aiprov.AVAILABLE, "usable": True},
            "claude_code": {"state": aiprov.RATE_LIMITED, "usable": False},
            "openai": {"state": aiprov.AVAILABLE, "usable": True},
        }
        with patch.object(aiprov, "state", side_effect=lambda provider=None: state if provider is None else {}), \
             patch.object(aiprov, "owner_policy", return_value=set()), \
             patch.object(aiprov, "_durable_gemini_daily_reset_evidence",
                          return_value=(None, None)):
            self.assertEqual(aiprov.development_candidates(), ["gemini_cli"])
            for name in ("openai", "codex", "anthropic_api"):
                self.assertFalse(aiprov.development_provider_allowed(name))

    def test_dev_gate_rejects_stale_available_when_daily_reset_is_durable(self):
        state = {
            "gemini_cli": {"state": aiprov.AVAILABLE, "usable": True},
            "claude_code": {"state": aiprov.RATE_LIMITED, "usable": False},
        }
        with patch.object(
            aiprov, "state",
            side_effect=lambda provider=None: state if provider is None else state.get(provider),
        ), patch.object(
            aiprov, "owner_policy", return_value=set()
        ), patch.object(
            aiprov, "_durable_gemini_daily_reset_evidence",
            return_value=(2_000_000_000.0, "a2a_history"),
        ):
            self.assertEqual(aiprov.development_candidates(), [])
            self.assertFalse(aiprov.development_provider_allowed("gemini_cli"))

    def test_client_gemini_reservation_blocks_development_borrowing(self):
        state = {
            "gemini_cli": {"state": aiprov.AVAILABLE, "usable": True},
            "claude_code": {"state": aiprov.AUTH_ERROR, "usable": False},
        }
        with patch.object(
            aiprov, "state",
            side_effect=lambda provider=None: state if provider is None else state.get(provider),
        ), patch.object(
            aiprov, "owner_policy", return_value=set()
        ), patch.object(
            aiprov, "_gemini_reserved_for_sales", return_value=True
        ), patch.object(
            aiprov, "_durable_gemini_daily_reset_evidence"
        ) as durable:
            self.assertEqual(aiprov.development_candidates(), [])
        durable.assert_not_called()

    def test_reserved_gemini_reprobe_never_calls_network(self):
        with patch.object(
            aiprov, "_gemini_reserved_for_sales", return_value=True
        ), patch("subprocess.run") as run:
            out = aiprov.reprobe_free_development(min_interval_min=5)
        self.assertEqual(out["status"], "reserved_for_client_sales")
        self.assertFalse(out["probed"])
        self.assertFalse(out["auto_retry"])
        run.assert_not_called()

    def test_daily_quota_requires_successful_probe_even_after_retry_time(self):
        expired_daily = {
            "name": "gemini_cli",
            "state": aiprov.RATE_LIMITED,
            "note": (
                "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED "
                "reset_epoch=1000000000"
            ),
            "retry_at": None,
            "cooling": False,
        }
        with patch.object(aiprov.db, "rows", return_value=[expired_daily]):
            state = aiprov.state()
        self.assertFalse(state["gemini_cli"]["usable"])
        self.assertTrue(state["gemini_cli"]["daily_probe_required"])

    def test_executor_never_falls_back_to_paid_after_gemini_failure(self):
        order = {"order_id": 91603, "iteration": 0, "scope": {}}
        events = []
        patches = (
            patch.object(aiprov, "development_candidates", return_value=["gemini_cli"]),
            patch.object(aiprov, "_seen", return_value=None),
            patch.object(aiprov, "_remember", return_value=None),
            patch.object(aiprov, "why_empty", return_value=[]),
            patch.object(aiprov, "mark", return_value=None),
            patch.object(aiprov.time, "sleep", return_value=None),
            patch.object(executor, "_run_provider", side_effect=ApiError("DEPENDENCY_UNAVAILABLE", "gemini unavailable")),
            patch.object(executor.a2a, "message", side_effect=lambda *a, **k: events.append(a)),
        )
        for p in patches: p.start()
        try:
            with self.assertRaises(ApiError):
                executor._run_with_provider_failover(order)
        finally:
            for p in reversed(patches): p.stop()
        self.assertTrue(any(e[1] == "DEV_PAID_PROVIDER_BLOCKED" for e in events))

    def test_forced_dev_openai_is_denied_before_call(self):
        order = {"order_id": 91603, "iteration": 0, "scope": {"engine": "openai"}}
        events = []
        with patch.object(executor.a2a, "message", side_effect=lambda *a, **k: events.append(a)):
            with self.assertRaises(ApiError):
                executor._run_with_provider_failover(order)
        self.assertTrue(any(e[1] == "DEV_PAID_PROVIDER_BLOCKED" for e in events))


if __name__ == "__main__":
    unittest.main()
