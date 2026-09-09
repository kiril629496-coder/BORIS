import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

import cpx_budget_resume as resume


class BudgetResumeTests(unittest.TestCase):
    def test_provider_previous_day_stats_never_count_as_today_kpi(self):
        # Midnight/provider-day lag safety: spend may already belong to the new
        # Moscow day while Avito item counters still describe yesterday. Resume
        # must fail closed before summing those stale contacts, otherwise yesterday's
        # leads can falsely pause today's restoration as daily_kpi_met.
        day = "2026-09-09"
        db = MagicMock()
        with patch.object(resume, "effective_daily_budget_limit", return_value=1500.0), \
             patch.object(resume, "presence_budget_pressure", return_value={"blocked": False}), \
             patch.object(resume, "latest_confirmed_spend", return_value={
                 "status": "ok", "spending_date": day, "spent_today_rub": 0.0,
             }), \
             patch.object(resume, "_stats", return_value={
                 "stats_date": "2026-09-08",
                 "collected_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                 "completeness": {"complete": True},
                 "items": [{"contacts": 4}],
             }), \
             patch("app.services.marketing_signal_guard.money_raise_signals_eligible",
                   return_value=(False, {
                       "reason": "provider_stats_day_lagged",
                       "marketing_day": day,
                       "provider_stats_day": "2026-09-08",
                   })):
            ok, reason, detail = resume._fresh_business_gate(db, "qa_resume", day)
        self.assertFalse(ok)
        self.assertEqual(reason, "money_signal_stale_or_degraded")
        self.assertEqual((detail.get("money_signal") or {}).get("reason"), "provider_stats_day_lagged")
        self.assertNotIn("contacts_today", detail)

    def test_same_moscow_day_never_touches_provider(self):
        today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        state = {
            "brake_day_msk": today,
            "items": {"1": {"item_id": 1, "status": "braked"}},
        }
        with patch.object(resume, "_load_resume_state", return_value=state),              patch.object(resume, "_token") as token:
            out = resume.resume_account("qa_resume", apply=True)
        self.assertEqual(out["status"], "waiting_next_moscow_day")
        token.assert_not_called()

    def test_dry_run_prior_day_has_no_write(self):
        yesterday = (
            datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)
        ).isoformat()
        state = {
            "brake_day_msk": yesterday,
            "items": {"11": {"item_id": 11, "status": "braked"}},
        }
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        with patch.object(resume, "_load_resume_state", return_value=state),              patch.object(resume, "_fresh_business_gate",
                          return_value=(True, "ok", {"spent_today_rub": 0})),              patch.object(resume, "account_throttle_remaining", return_value=0),              patch.object(resume, "_token", return_value="qa-token"),              patch.object(resume.httpx, "Client", return_value=client),              patch.object(resume, "_active_item_ids", return_value=[11]),              patch.object(resume, "_bulk_promotions", return_value=[]),              patch.object(resume, "_cpx_receipt_prepare") as prepare:
            out = resume.resume_account("qa_resume", apply=False)
        self.assertEqual(out["status"], "would_resume")
        self.assertEqual(out["would_resume_ids"], [11])
        prepare.assert_not_called()
        client.post.assert_not_called()

    def test_apply_is_bounded_to_five_and_verified(self):
        yesterday = (
            datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)
        ).isoformat()
        items = {
            str(i): {"item_id": i, "status": "braked", "action_type_id": 5}
            for i in range(101, 108)
        }
        state = {"brake_day_msk": yesterday, "items": items}
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        ok = MagicMock(status_code=200, text="")
        client.post.return_value = ok

        first_promos = []
        verified = [
            {"itemID": i, "manualPromotion": {"bidPenny": 5000}, "autoPromotion": {}}
            for i in range(101, 106)
        ]
        detail = {
            "actionTypeID": 5,
            "manual": {
                "minBidPenny": 300,
                "maxBidPenny": 20000,
                "recBidPenny": 5000,
                "bids": [],
            },
        }
        with patch.object(resume, "_load_resume_state", return_value=state),              patch.object(resume, "_fresh_business_gate",
                          return_value=(True, "ok", {"spent_today_rub": 0})),              patch.object(resume, "account_throttle_remaining", return_value=0),              patch.object(resume, "_token", return_value="qa-token"),              patch.object(resume.httpx, "Client", return_value=client),              patch.object(resume, "_active_item_ids", return_value=list(range(101,108))),              patch.object(resume, "_bulk_promotions", side_effect=[first_promos, verified]),              patch.object(resume, "_get_bid_detail", return_value=detail),              patch.object(resume, "_target_from_detail",
                          return_value=(5000, "avito_live_rec_bid",
                                        {"provider_min_penny": 300})),              patch.object(resume, "can_execute_live_action",
                          return_value={"allowed": True, "mandate_id": 7,
                                        "mandate_version": 2, "reason_code": "allowed_by_mandate"}),              patch.object(resume, "check_raise_allowed",
                          return_value={"allowed": True, "balance": {"status": "KNOWN"}}),              patch("app.services.marketing_signal_guard.money_raise_signals_eligible",
                          return_value=(True, {"status": "ok"})),              patch.object(resume, "_cpx_receipt_get", return_value=None),              patch.object(resume, "_cpx_receipt_prepare",
                          return_value=({"status": "prepared"}, True)),              patch.object(resume, "_cpx_receipt_mark"),              patch.object(resume, "_mark_state_item"),              patch.object(resume, "log_action"),              patch.object(resume, "_audit_log"):
            out = resume.resume_account("qa_resume", apply=True)
        self.assertEqual(client.post.call_count, 5)
        self.assertEqual(out["restored"], 5)
        self.assertEqual(out["verified"], 5)


    def test_postwrite_mismatch_stays_delivery_unknown(self):
        yesterday = (
            datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)
        ).isoformat()
        state = {
            "brake_day_msk": yesterday,
            "items": {"501": {"item_id": 501, "status": "braked", "action_type_id": 5}},
        }
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = MagicMock(status_code=200, text="")
        detail = {
            "actionTypeID": 5,
            "manual": {
                "minBidPenny": 300, "maxBidPenny": 20000,
                "recBidPenny": 5000, "bids": [],
            },
        }
        verify = [{"itemID": 501, "manualPromotion": {"bidPenny": 4900}, "autoPromotion": {}}]
        receipt_mark = MagicMock()
        state_mark = MagicMock()
        with patch.object(resume, "_load_resume_state", return_value=state), \
             patch.object(resume, "_fresh_business_gate", return_value=(True, "ok", {"spent_today_rub": 0})), \
             patch.object(resume, "account_throttle_remaining", return_value=0), \
             patch.object(resume, "_token", return_value="qa-token"), \
             patch.object(resume.httpx, "Client", return_value=client), \
             patch.object(resume, "_active_item_ids", return_value=[501]), \
             patch.object(resume, "_bulk_promotions", side_effect=[[], verify]), \
             patch.object(resume, "_get_bid_detail", return_value=detail), \
             patch.object(resume, "_target_from_detail", return_value=(5000, "avito_live_rec_bid", {"provider_min_penny": 300})), \
             patch.object(resume, "can_execute_live_action", return_value={"allowed": True, "mandate_id": 7, "mandate_version": 2, "reason_code": "allowed_by_mandate"}), \
             patch.object(resume, "check_raise_allowed", return_value={"allowed": True, "balance": {"status": "KNOWN"}}), \
             patch("app.services.marketing_signal_guard.money_raise_signals_eligible", return_value=(True, {"status": "ok"})), \
             patch.object(resume, "_cpx_receipt_get", return_value=None), \
             patch.object(resume, "_cpx_receipt_prepare", return_value=({"status": "prepared"}, True)), \
             patch.object(resume, "_cpx_receipt_mark", receipt_mark), \
             patch.object(resume, "_mark_state_item", state_mark), \
             patch.object(resume, "log_action"), \
             patch.object(resume, "_audit_log"):
            out = resume.resume_account("qa_resume", apply=True)
        self.assertEqual(out["restored"], 0)
        self.assertEqual(out["verified"], 0)
        delivery_calls = [c for c in receipt_mark.call_args_list if len(c.args) >= 3 and c.args[2] == "delivery_unknown"]
        self.assertTrue(delivery_calls, receipt_mark.call_args_list)
        self.assertTrue(any(len(c.args) >= 3 and c.args[2] == "verify_pending" for c in state_mark.call_args_list))



    def test_setmanual_5xx_is_delivery_unknown_not_failed_retry(self):
        yesterday = (datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)).isoformat()
        state = {"brake_day_msk": yesterday, "items": {"601": {"item_id": 601, "status": "braked", "action_type_id": 5}}}
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = MagicMock(status_code=503, text="upstream unavailable")
        detail = {"actionTypeID": 5, "manual": {"minBidPenny": 300, "maxBidPenny": 20000, "recBidPenny": 5000, "bids": []}}
        receipt_mark = MagicMock()
        state_mark = MagicMock()
        with patch.object(resume, "_load_resume_state", return_value=state), \
             patch.object(resume, "_fresh_business_gate", return_value=(True, "ok", {"spent_today_rub": 0})), \
             patch.object(resume, "account_throttle_remaining", return_value=0), \
             patch.object(resume, "_token", return_value="qa-token"), \
             patch.object(resume.httpx, "Client", return_value=client), \
             patch.object(resume, "_active_item_ids", return_value=[601]), \
             patch.object(resume, "_bulk_promotions", return_value=[]), \
             patch.object(resume, "_get_bid_detail", return_value=detail), \
             patch.object(resume, "_target_from_detail", return_value=(5000, "avito_live_rec_bid", {"provider_min_penny": 300})), \
             patch.object(resume, "can_execute_live_action", return_value={"allowed": True, "mandate_id": 7, "mandate_version": 2, "reason_code": "allowed_by_mandate"}), \
             patch.object(resume, "check_raise_allowed", return_value={"allowed": True, "balance": {"status": "KNOWN"}}), \
             patch("app.services.marketing_signal_guard.money_raise_signals_eligible", return_value=(True, {"status": "ok"})), \
             patch.object(resume, "_cpx_receipt_get", return_value=None), \
             patch.object(resume, "_cpx_receipt_prepare", return_value=({"status": "prepared"}, True)), \
             patch.object(resume, "_cpx_receipt_mark", receipt_mark), \
             patch.object(resume, "_mark_state_item", state_mark):
            out = resume.resume_account("qa_resume", apply=True)
        self.assertEqual(out["restored"], 0)
        self.assertEqual(out["failed"], 1)
        self.assertTrue(any(len(c.args) >= 3 and c.args[2] == "delivery_unknown" for c in receipt_mark.call_args_list))
        self.assertTrue(any(len(c.args) >= 3 and c.args[2] == "verify_pending" for c in state_mark.call_args_list))



if __name__ == "__main__":
    unittest.main()
