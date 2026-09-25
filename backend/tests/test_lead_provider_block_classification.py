# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from app.ext_api import a2a, lead
from app.ext_api.errors import ApiError


class LeadProviderBlockClassificationTest(unittest.TestCase):
    def test_reviewer_quota_is_blocked_infra_without_owner_notification(self):
        order = {"order_id": 88001}
        captured = {}

        def fake_review(key, order_id, verdict, note=None, fixes=None, blocked_reason=None):
            captured.update({
                "order_id": order_id,
                "verdict": verdict,
                "blocked_reason": blocked_reason,
            })
            return {
                "order_id": order_id,
                "status": a2a.BLOCKED_INFRA,
                "blocked_reason": blocked_reason,
            }

        with patch.object(
            lead, "decide",
            side_effect=ApiError("RATE_LIMITED", "quota exceeded for free reviewer"),
        ), patch.object(
            lead.aiprov if hasattr(lead, "aiprov") else __import__(
                "app.ext_api.aiprov", fromlist=["aiprov"]
            ),
            "weekly_limit_retry_after",
            return_value=None,
        ), patch.object(
            lead.a2a, "review", side_effect=fake_review
        ), patch.object(
            lead.notify, "blocked_human"
        ) as notify_owner:
            out = lead.handle(order)

        self.assertEqual(out["status"], a2a.BLOCKED_INFRA)
        self.assertEqual(captured["verdict"], "BLOCKED_INFRA")
        self.assertTrue(
            str(captured["blocked_reason"]).startswith("provider_unavailable:")
        )
        notify_owner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
