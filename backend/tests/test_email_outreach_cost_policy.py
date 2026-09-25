# -*- coding: utf-8 -*-
import inspect
import unittest
from unittest.mock import patch

from app.services import email_outreach_cost_policy as cost


class EmailOutreachCostPolicyTest(unittest.TestCase):
    def test_scale_projection_uses_one_shared_unit_cost(self):
        unit = 5.36
        self.assertEqual(
            cost.project_variable_cost(unit, clients=5, banners_per_client=10)[
                "projected_image_cost_rub"
            ],
            268.0,
        )
        self.assertEqual(
            cost.project_variable_cost(unit, clients=5, banners_per_client=20)[
                "projected_image_cost_rub"
            ],
            536.0,
        )
        self.assertEqual(
            cost.project_variable_cost(unit, clients=10, banners_per_client=10)[
                "projected_image_cost_rub"
            ],
            536.0,
        )
        self.assertEqual(
            cost.project_variable_cost(unit, clients=10, banners_per_client=20)[
                "projected_image_cost_rub"
            ],
            1072.0,
        )

    def test_internal_budget_has_room_for_20_banners(self):
        projected = cost.project_variable_cost(
            cost.FALLBACK_PREMIUM_BANNER_UNIT_RUB,
            clients=1,
            banners_per_client=cost.INCLUDED_BANNERS_MAX,
        )
        self.assertLessEqual(
            projected["projected_variable_cost_per_client_rub"],
            cost.VARIABLE_TECH_BUDGET_PER_CLIENT_RUB,
        )

    def test_cost_module_is_read_only_and_has_no_provider_transport(self):
        src = inspect.getsource(cost)
        self.assertNotIn("openai.", src.lower())
        self.assertNotIn("requests.post(", src.lower())
        self.assertNotIn("send_outbound(", src)
        self.assertNotIn("enqueue_email(", src)
        self.assertNotIn("generate_banner_financially_guarded(", src)

    @patch.object(cost, "_configured_bool", return_value=False)
    @patch.object(
        cost,
        "_usage_snapshot",
        return_value={
            "days": 30,
            "premium_banner": {
                "calls": 20,
                "images": 20,
                "cost_rub": 107.2,
                "unit_cost_rub": 5.36,
                "unit_cost_source": "api_usage_30d",
            },
            "owner_outreach": {
                "calls": 0,
                "images": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_rub": 0.0,
            },
            "recent_24h": {
                "paid_text_calls_24h": 0,
                "paid_text_cost_rub_24h": 0.0,
                "paid_outreach_banner_calls_24h": 0,
                "paid_outreach_banner_cost_rub_24h": 0.0,
            },
            "guard_recent_6h": {
                "cost_guard_events_6h": 0,
                "last_cost_guard_event_at": None,
            },
        },
    )
    @patch.object(
        cost,
        "banner_pool_stats",
        return_value={
            "mode": "reuse_only",
            "count": 41,
            "paid_generation_enabled": False,
        },
    )
    @patch.object(
        cost,
        "server_capacity_snapshot",
        return_value={
            "cpu_count": 8,
            "load_1m": 4.0,
            "load_5m": 4.0,
            "load_15m": 4.0,
            "load_5m_per_cpu": 0.5,
            "memory_available_mb": 13000,
            "disk_free_gb": 20.0,
            "disk_free_pct": 13.0,
            "disk_state": "ok",
        },
    )
    def test_healthy_reuse_only_model_is_green(
        self, _capacity, _pool, _usage, _paid_copy
    ):
        out = cost.health()
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["critical"], [])
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(
            out["snapshot"]["capacity"]["planned_sends_10_clients_per_day"], 200
        )

    @patch.object(cost, "_configured_bool", return_value=True)
    @patch.object(
        cost,
        "_usage_snapshot",
        return_value={
            "days": 30,
            "premium_banner": {
                "calls": 1,
                "images": 1,
                "cost_rub": 5.0,
                "unit_cost_rub": 5.0,
                "unit_cost_source": "api_usage_30d",
            },
            "owner_outreach": {
                "calls": 1,
                "images": 0,
                "prompt_tokens": 100,
                "completion_tokens": 100,
                "cost_rub": 1.0,
            },
            "recent_24h": {
                "paid_text_calls_24h": 1,
                "paid_text_cost_rub_24h": 1.0,
                "paid_outreach_banner_calls_24h": 1,
                "paid_outreach_banner_cost_rub_24h": 5.0,
            },
            "guard_recent_6h": {},
        },
    )
    @patch.object(
        cost,
        "banner_pool_stats",
        return_value={"mode": "paid", "paid_generation_enabled": True},
    )
    @patch.object(
        cost,
        "server_capacity_snapshot",
        return_value={
            "cpu_count": 8,
            "load_1m": 1.0,
            "load_5m": 1.0,
            "load_15m": 1.0,
            "load_5m_per_cpu": 0.125,
            "memory_available_mb": 13000,
            "disk_free_gb": 20.0,
            "disk_free_pct": 13.0,
            "disk_state": "ok",
        },
    )
    def test_hidden_paid_send_paths_fail_closed(
        self, _capacity, _pool, _usage, _paid_copy
    ):
        out = cost.health()
        self.assertEqual(out["state"], "critical")
        self.assertTrue(out["owner_action_required"])
        self.assertGreaterEqual(len(out["critical"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
