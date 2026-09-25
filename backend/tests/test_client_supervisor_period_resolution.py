import unittest

from app.services.client_supervisor import _resolve_client_period_state


class ClientSupervisorPeriodResolutionTests(unittest.TestCase):
    def test_explicit_active_wins(self):
        self.assertEqual(
            _resolve_client_period_state(["mop"], ["inbox"], {"messages_recent_30d": False}),
            "active",
        )

    def test_explicit_expired_is_not_runtime_active(self):
        self.assertEqual(
            _resolve_client_period_state([], ["inbox"], {"messages_recent_30d": True}),
            "expired",
        )

    def test_no_period_no_service_is_inactive_unprovisioned(self):
        self.assertEqual(
            _resolve_client_period_state([], [], {
                "binding_configured": False,
                "messages_recent_30d": False,
                "crm_recent_30d": False,
            }),
            "inactive_unprovisioned",
        )

    def test_runtime_without_paid_period_is_billing_gap(self):
        self.assertEqual(
            _resolve_client_period_state([], [], {
                "mop_mode_recent_30d": True,
                "messages_recent_30d": True,
            }),
            "billing_gap",
        )


if __name__ == "__main__":
    unittest.main()
