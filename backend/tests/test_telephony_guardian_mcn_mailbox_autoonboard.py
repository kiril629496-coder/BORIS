import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import telephony_guardian_runner as G


class McnMailboxAutoonboardGuardianTests(unittest.TestCase):
    def setUp(self):
        self._owner_alert_patcher = patch.object(
            G, "_mcn_owner_action_alert_once",
            return_value={"status": "not_needed", "sent": False},
        )
        self._owner_alert_patcher.start()
        self.addCleanup(self._owner_alert_patcher.stop)

    def test_existing_mcn_short_circuits_everything(self):
        with patch.object(G, "current_mcn_accounts", return_value=["acc"]), \
             patch.object(G, "_single_active_phone_account") as target, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist, \
             patch.object(G.subprocess, "run") as run:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "not_needed")
        self.assertEqual(out["primary_next_action"]["code"], "mcn_connected")
        self.assertFalse(out["owner_action_required"])
        persist.assert_called_once()
        target.assert_not_called()
        run.assert_not_called()

    def test_no_phone_account_still_discovers_mail_read_only(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "no_complete_mcn_letter",
                "unique_complete_candidates": 0,
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "waiting_phone_account", "accounts": 0
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }) as mailbox, \
             patch.object(G.subprocess, "run", return_value=cp) as run, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "waiting_mcn_setup_letter")
        self.assertEqual(out["phone_account_status"], "waiting_phone_account")
        self.assertTrue(out["discovery_only"])
        self.assertFalse(out["owner_action_required"])
        mailbox.assert_called_once_with()
        args = run.call_args.args[0]
        self.assertNotIn("--apply", args)
        self.assertNotIn("--account-id", args)
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[0], "__mcn_mailbox_watch__")

    def test_force_refresh_bypasses_recent_throttle_after_explicit_business_change(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "no_complete_mcn_letter",
                "unique_complete_candidates": 0,
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]),              patch.object(G, "_single_active_phone_account", return_value={
                 "status": "waiting_phone_account", "accounts": 0
             }),              patch.object(G, "_mailbox_autoonboard_recent", return_value={
                 "status": "throttled",
                 "owner_action_required": False,
             }) as recent,              patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }),              patch.object(G.subprocess, "run", return_value=cp) as run,              patch.object(G, "_persist_mailbox_autoonboard"):
            out = G.mcn_mailbox_autoonboard_once(force_refresh=True)
        recent.assert_not_called()
        self.assertEqual(out["status"], "waiting_mcn_setup_letter")
        self.assertNotIn("--apply", run.call_args.args[0])

    def test_complete_letter_without_phone_account_is_detected_but_not_applied(self):
        cp = SimpleNamespace(
            returncode=5,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "no_active_phone_account",
                "unique_complete_candidates": 1,
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "waiting_phone_account", "accounts": 0
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }), \
             patch.object(G.subprocess, "run", return_value=cp) as run, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "mcn_letter_ready_waiting_phone_account")
        self.assertEqual(out["candidate_count"], 1)
        self.assertTrue(out["discovery_only"])
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_phone_entitlement_required")
        self.assertNotIn("--apply", run.call_args.args[0])
        persist.assert_called_once()

    def test_complete_letter_with_multiple_phone_accounts_requires_binding_choice(self):
        cp = SimpleNamespace(
            returncode=5,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "ambiguous_active_phone_accounts",
                "unique_complete_candidates": 1,
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "ambiguous_phone_accounts", "accounts": 2
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }), \
             patch.object(G.subprocess, "run", return_value=cp) as run, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["candidate_count"], 1)
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_phone_account_binding_required")
        self.assertNotIn("--apply", run.call_args.args[0])
        persist.assert_called_once()


    def test_contract_blocker_without_phone_account_becomes_owner_action(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "mcn_contract_required_before_credentials",
                "unique_complete_candidates": 0,
                "operational_reply": {
                    "code": "mcn_contract_required_before_credentials",
                    "date": "Mon, 7 Sep 2026 09:31:46 +0300",
                    "subject": "RE: RE: Партнерская программа",
                    "sender_domain": "mcn.ru",
                    "owner_action_required": True,
                    "requirements": [
                        "company_or_ip_card",
                        "registration_document",
                        "director_appointment_decision",
                    ],
                    "provider_path": ["client_contract_for_test", "agency_contract_in_parallel"],
                },
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "waiting_phone_account", "accounts": 0
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }), \
             patch.object(G.subprocess, "run", return_value=cp), \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "owner_action")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_contract_documents")
        self.assertEqual(out["operational_reply"]["sender_domain"], "mcn.ru")
        saved = persist.call_args.args[1]
        self.assertNotIn("password", json.dumps(saved).lower())
        self.assertNotIn("username", json.dumps(saved).lower())

    def test_company_card_request_becomes_current_owner_action(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "mcn_company_card_required",
                "unique_complete_candidates": 0,
                "operational_reply": {
                    "code": "mcn_company_card_required",
                    "date": "Mon, 7 Sep 2026 16:07:30 +0300",
                    "subject": "RE: RE: Партнерская программа",
                    "sender_domain": "mcn.ru",
                    "owner_action_required": True,
                    "requirements": ["company_or_ip_card"],
                    "provider_path": [
                        "confirm_current_company_card",
                        "send_company_card_to_mcn",
                        "mcn_prepares_client_and_agency_contracts",
                        "choose_offer_or_edo",
                        "receive_sip_credentials",
                    ],
                },
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "waiting_phone_account", "accounts": 0
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }), \
             patch.object(G, "_mcn_company_card_sent_evidence", return_value={"sent":False,"source":"sent_folder","reason":"no_company_card_after_request"}), \
             patch.object(G, "_prepare_mcn_company_card_draft", return_value={
                 "status": "draft_exists", "reason": "ok", "ready": True, "returncode": 0
             }), \
             patch.object(G.subprocess, "run", return_value=cp), \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "owner_action")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_send_approval")
        self.assertTrue(out["owner_company_card"]["complete"])
        self.assertTrue(out["company_card_draft"]["ready"])
        self.assertEqual(out["operational_reply"]["requirements"], ["company_or_ip_card"])
        saved = persist.call_args.args[1]
        self.assertNotIn("password", json.dumps(saved).lower())
        self.assertNotIn("username", json.dumps(saved).lower())

    def test_company_card_sent_evidence_keeps_prior_exact_provider_delivery(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "ok",
                "items": [{
                    "company_card_signal": True,
                    "current_card_match": True,
                    "boris_action_signal": False,
                    "date": prior.isoformat(),
                    "in_reply_to": "<old@mcn.ru>",
                }],
            }),
            stderr="",
        )
        with patch.object(G, "_company_card_send_state_evidence", return_value={"sent":False}), \
             patch.object(G, "_company_card_manual_delivery_evidence", return_value={"sent":False}), \
             patch.object(G.subprocess, "run", return_value=cp):
            out = G._mcn_company_card_sent_evidence(
                2,
                request_date=now.isoformat(),
                request_message_id="<new@mcn.ru>",
            )
        self.assertFalse(out["sent"])
        self.assertTrue(out["prior_current_card_sent_to_provider"])
        self.assertEqual(out["prior_current_card_sent_at"], prior.isoformat())

    def test_repeat_card_auto_resend_helper_requires_exact_mcn_domain(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        with patch.object(G, "_mcn_repeat_card_auto_resend_enabled", return_value=True), \
             patch.object(G.subprocess, "run") as run:
            out = G._mcn_company_card_auto_resend_if_authorized(
                2,
                {
                    "code": "mcn_company_card_required",
                    "sender_domain": "example.com",
                    "date": now.isoformat(),
                    "message_id": "<new@example.com>",
                },
                {
                    "prior_current_card_sent_to_provider": True,
                    "prior_current_card_sent_at": prior.isoformat(),
                },
                {"ready": True, "card_fingerprint": "a" * 64},
            )
        self.assertFalse(out["authorized"])
        self.assertEqual(out["reason"], "provider_domain_not_exact")
        run.assert_not_called()

    def test_repeat_card_auto_resend_helper_uses_prior_exact_delivery(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "sent",
                "reason": "ok",
                "recipient_domain_verified": True,
                "sent_copy_saved": True,
                "send_state_persisted": True,
                "retry_blocked": True,
            }),
            stderr="",
        )
        with patch.object(G, "_mcn_repeat_card_auto_resend_enabled", return_value=True), \
             patch.object(G.subprocess, "run", return_value=cp) as run, \
             patch("app.services.telephony_core._audit") as audit:
            out = G._mcn_company_card_auto_resend_if_authorized(
                2,
                {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "date": now.isoformat(),
                    "message_id": "<new@mcn.ru>",
                },
                {
                    "prior_current_card_sent_to_provider": True,
                    "prior_current_card_sent_at": prior.isoformat(),
                },
                {"ready": True, "card_fingerprint": "a" * 64},
            )
        self.assertTrue(out["authorized"])
        self.assertEqual(out["status"], "sent")
        self.assertTrue(out["recipient_domain_verified"])
        args = run.call_args.args[0]
        self.assertIn("--apply", args)
        self.assertIn("--confirm-share-banking", args)
        audit.assert_called_once()
        metadata = audit.call_args.kwargs["metadata"]
        self.assertEqual(
            metadata["authorization_basis"],
            "same_current_card_previously_delivered_to_mcn",
        )

    def test_repeat_request_same_card_becomes_ownerless_waiting_after_auto_resend(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        before = {
            "sent": False,
            "source": "sent_folder",
            "reason": "no_company_card_after_request",
            "prior_current_card_sent_to_provider": True,
            "prior_current_card_sent_at": prior.isoformat(),
        }
        after = {
            "sent": True,
            "source": "send_state",
            "reason": "smtp_accepted",
            "sent_at": now.isoformat(),
        }
        op = {
            "code": "mcn_company_card_required",
            "sender_domain": "mcn.ru",
            "date": now.isoformat(),
            "message_id": "<new@mcn.ru>",
        }
        with patch.object(G, "_mcn_company_card_sent_evidence", side_effect=[before, after]), \
             patch.object(G, "_prepare_mcn_company_card_draft", return_value={
                 "status": "draft_exists", "ready": True, "sent": False,
                 "delivery_ambiguous": False, "card_fingerprint": "a" * 64,
             }), \
             patch.object(G, "_mcn_company_card_auto_resend_if_authorized", return_value={
                 "authorized": True, "status": "sent", "recipient_domain_verified": True,
                 "send_state_persisted": True, "retry_blocked": True,
             }) as resend, \
             patch.object(G, "_cleanup_mcn_company_card_draft_after_sent", return_value={
                 "removed": True, "reason": "ok", "count": 1,
             }), \
             patch.object(G, "_mcn_company_card_followup", return_value={"status":"scheduled"}):
            out = G._mcn_company_card_progress(2, op)
        resend.assert_called_once()
        self.assertEqual(out["status"], "waiting_mcn_response")
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["actor"], "boris")
        self.assertEqual(
            out["primary_next_action"]["code"],
            "mcn_company_card_sent_waiting_reply",
        )

    def test_repeat_card_auto_resend_gate_disabled_fails_closed(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        with patch.object(G, "_mcn_repeat_card_auto_resend_enabled", return_value=False), \
             patch.object(G.subprocess, "run") as run:
            out = G._mcn_company_card_auto_resend_if_authorized(
                2,
                {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "date": now.isoformat(),
                    "message_id": "<new@mcn.ru>",
                },
                {
                    "prior_current_card_sent_to_provider": True,
                    "prior_current_card_sent_at": prior.isoformat(),
                },
                {"ready": True, "card_fingerprint": "a" * 64},
            )
        self.assertFalse(out["authorized"])
        self.assertEqual(out["reason"], "repeat_card_autonomy_disabled")
        run.assert_not_called()

    def test_repeat_card_auto_resend_rejects_stale_prior_delivery(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=181)
        with patch.object(G, "_mcn_repeat_card_auto_resend_enabled", return_value=True), \
             patch.object(G.subprocess, "run") as run:
            out = G._mcn_company_card_auto_resend_if_authorized(
                2,
                {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "date": now.isoformat(),
                    "message_id": "<new@mcn.ru>",
                },
                {
                    "prior_current_card_sent_to_provider": True,
                    "prior_current_card_sent_at": prior.isoformat(),
                },
                {"ready": True, "card_fingerprint": "a" * 64},
            )
        self.assertFalse(out["authorized"])
        self.assertEqual(out["reason"], "prior_delivery_not_reusable")
        run.assert_not_called()

    def test_repeat_card_auto_resend_rejects_request_without_message_id(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        with patch.object(G, "_mcn_repeat_card_auto_resend_enabled", return_value=True), \
             patch.object(G.subprocess, "run") as run:
            out = G._mcn_company_card_auto_resend_if_authorized(
                2,
                {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "date": now.isoformat(),
                    "message_id": "",
                },
                {
                    "prior_current_card_sent_to_provider": True,
                    "prior_current_card_sent_at": prior.isoformat(),
                },
                {"ready": True, "card_fingerprint": "a" * 64},
            )
        self.assertFalse(out["authorized"])
        self.assertEqual(out["reason"], "fresh_request_evidence_missing")
        run.assert_not_called()

    def test_repeat_card_auto_resend_delivery_ambiguous_escalates_once(self):
        now = G.datetime.now(G.timezone.utc)
        prior = now - G.timedelta(days=1)
        before = {
            "sent": False,
            "source": "sent_folder",
            "reason": "no_company_card_after_request",
            "prior_current_card_sent_to_provider": True,
            "prior_current_card_sent_at": prior.isoformat(),
        }
        op = {
            "code": "mcn_company_card_required",
            "sender_domain": "mcn.ru",
            "date": now.isoformat(),
            "message_id": "<new@mcn.ru>",
        }
        with patch.object(G, "_mcn_company_card_sent_evidence", return_value=before), \
             patch.object(G, "_prepare_mcn_company_card_draft", return_value={
                 "status": "draft_exists", "ready": True, "sent": False,
                 "delivery_ambiguous": False, "card_fingerprint": "a" * 64,
             }), \
             patch.object(G, "_mcn_company_card_auto_resend_if_authorized", return_value={
                 "authorized": True, "status": "delivery_ambiguous",
                 "retry_blocked": True,
             }) as resend:
            out = G._mcn_company_card_progress(2, op)
        resend.assert_called_once()
        self.assertEqual(out["status"], "owner_action")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(
            out["primary_next_action"]["code"],
            "mcn_company_card_delivery_verify",
        )

    def test_company_card_already_sent_becomes_boris_waiting_state(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "mcn_company_card_required",
                "unique_complete_candidates": 0,
                "operational_reply": {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "requirements": ["company_or_ip_card"],
                },
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={"status":"ok","mailbox_id":2,"mailbox":"owner@test"}), \
             patch.object(G, "_mcn_company_card_sent_evidence", return_value={"sent":False,"source":"sent_folder","reason":"no_company_card_after_request"}), \
             patch.object(G, "_prepare_mcn_company_card_draft", return_value={
                 "status":"already_sent","reason":"exactly_once_guard","ready":False,
                 "sent":True,"delivery_ambiguous":False,"returncode":0,
             }), \
             patch.object(G.subprocess, "run", return_value=cp), \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "waiting_mcn_response")
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["actor"], "boris")
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_sent_waiting_reply")
        self.assertFalse(persist.call_args.args[1]["owner_action_required"])

    def test_company_card_delivery_ambiguous_blocks_resend_and_escalates(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "mcn_company_card_required",
                "unique_complete_candidates": 0,
                "operational_reply": {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "requirements": ["company_or_ip_card"],
                },
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={"status":"ok","mailbox_id":2,"mailbox":"owner@test"}), \
             patch.object(G, "_mcn_company_card_sent_evidence", return_value={"sent":False,"source":"sent_folder","reason":"no_company_card_after_request"}), \
             patch.object(G, "_prepare_mcn_company_card_draft", return_value={
                 "status":"delivery_ambiguous","reason":"previous_send_may_have_reached_smtp",
                 "ready":False,"sent":False,"delivery_ambiguous":True,"returncode":8,
             }), \
             patch.object(G.subprocess, "run", return_value=cp), \
             patch.object(G, "_persist_mailbox_autoonboard"):
            out = G.mcn_mailbox_autoonboard_once()
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_delivery_verify")

    def test_throttled_send_approval_refreshes_to_waiting_after_actual_send(self):
        recent = {
            "status": "owner_action",
            "reason": "mcn_company_card_required",
            "mailbox_id": 2,
            "owner_action_required": True,
            "primary_next_action": {
                "code": "mcn_company_card_send_approval",
                "actor": "owner",
                "owner_action_required": True,
            },
            "company_card_draft": {"status":"draft_exists","ready":True},
        }
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=recent), \
             patch.object(G, "_mcn_company_card_sent_evidence", return_value={"sent":False,"source":"sent_folder","reason":"no_company_card_after_request"}), \
             patch.object(G, "_prepare_mcn_company_card_draft", return_value={
                 "status":"already_sent","reason":"exactly_once_guard","ready":False,
                 "sent":True,"delivery_ambiguous":False,"returncode":0,
             }), \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "waiting_mcn_response")
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_sent_waiting_reply")
        self.assertEqual(persist.call_args.args[1]["status"], "waiting_mcn_response")

    def test_throttled_stale_sent_waiting_refreshes_to_delivery_verify(self):
        recent = {
            "status": "waiting_mcn_response",
            "reason": "mcn_company_card_required",
            "mailbox_id": 2,
            "owner_action_required": False,
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
            },
            "operational_reply": {
                "date": "Mon, 7 Sep 2026 16:07:30 +0300",
                "message_id": "<request@mcn.ru>",
            },
            "company_card_sent_evidence": {
                "sent": True,
                "source": "sent_folder",
                "reason": "attachment_seen_after_request",
            },
        }
        strict = {
            "status": "owner_action",
            "owner_action_required": True,
            "company_card_sent_evidence": {
                "sent": False,
                "source": "sent_folder",
                "reason": "unverified_manual_attachment_after_request",
                "delivery_ambiguous": True,
            },
            "primary_next_action": {
                "code": "mcn_company_card_delivery_verify",
                "actor": "owner",
                "owner_action_required": True,
            },
        }
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=recent), \
             patch.object(G, "_mcn_company_card_progress", return_value=strict) as progress, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        progress.assert_called_once()
        self.assertEqual(out["status"], "owner_action")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_delivery_verify")
        saved = persist.call_args.args[1]
        self.assertEqual(saved["status"], "owner_action")
        self.assertFalse(saved["company_card_sent_evidence"]["sent"])
        self.assertTrue(saved["company_card_sent_evidence"]["delivery_ambiguous"])

    def test_throttled_delivery_verify_rechecks_current_mail_state(self):
        recent = {
            "status": "owner_action",
            "reason": "mcn_company_card_required",
            "mailbox_id": 2,
            "owner_action_required": True,
            "primary_next_action": {
                "code": "mcn_company_card_delivery_verify",
                "actor": "owner",
                "owner_action_required": True,
            },
            "operational_reply": {
                "date": "Mon, 7 Sep 2026 16:07:30 +0300",
                "message_id": "<request@mcn.ru>",
            },
        }
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=recent), \
             patch.object(G, "_mcn_company_card_progress", return_value={
                 "status": "waiting_mcn_response",
                 "owner_action_required": False,
                 "company_card_sent_evidence": {
                     "sent": True,
                     "source": "send_state",
                     "reason": "smtp_accepted",
                 },
                 "primary_next_action": {
                     "code": "mcn_company_card_sent_waiting_reply",
                     "actor": "boris",
                     "owner_action_required": False,
                 },
             }) as progress, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        progress.assert_called_once()
        self.assertEqual(out["status"], "waiting_mcn_response")
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(persist.call_args.args[1]["status"], "waiting_mcn_response")

    def test_unchanged_waiting_reply_does_not_refresh_throttle_timestamp(self):
        recent = {
            "status": "waiting_mcn_response",
            "reason": "mcn_company_card_required",
            "mailbox_id": 2,
            "owner_action_required": False,
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
            },
            "operational_reply": {
                "date": "Mon, 7 Sep 2026 16:07:30 +0300",
                "message_id": "<request@mcn.ru>",
            },
        }
        same = {
            "status": "waiting_mcn_response",
            "owner_action_required": False,
            "company_card_sent_evidence": {
                "sent": True,
                "source": "sent_folder",
                "reason": "current_card_content_verified_after_request",
            },
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
            },
        }
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=recent), \
             patch.object(G, "_mcn_company_card_progress", return_value=same) as progress, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        progress.assert_called_once()
        self.assertEqual(out["status"], "waiting_mcn_response")
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_sent_waiting_reply")
        persist.assert_not_called()

    def test_manual_sent_folder_after_request_is_ambiguous_and_never_resends(self):
        with patch.object(G, "_mcn_company_card_sent_evidence", return_value={
            "sent": False, "source": "sent_folder",
            "reason": "unverified_manual_attachment_after_request",
            "sent_at": "2026-09-07T14:15:00+00:00",
            "delivery_ambiguous": True,
        }), patch.object(G, "_cleanup_mcn_company_card_draft_after_sent") as cleanup, \
             patch.object(G, "_prepare_mcn_company_card_draft") as prepare:
            out = G._mcn_company_card_progress(2, {
                "date": "Mon, 7 Sep 2026 16:07:30 +0300",
                "message_id": "<request@mcn.ru>",
            })
        self.assertEqual(out["status"], "owner_action")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_delivery_verify")
        self.assertTrue(out["company_card_sent_evidence"]["delivery_ambiguous"])
        cleanup.assert_not_called()
        prepare.assert_not_called()

    def test_manual_delivery_confirmation_is_trusted_only_for_exact_current_thread(self):
        with patch.object(G, "mcn_company_card_manual_delivery_confirmation", return_value={
            "status": "confirmed",
            "mailbox_id": 2,
            "request_message_id": "<request@mcn.ru>",
            "sent_at": "2026-09-07T16:16:14+00:00",
        }):
            ok = G._company_card_manual_delivery_evidence(
                2,
                request_message_id="<request@mcn.ru>",
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
            )
            wrong = G._company_card_manual_delivery_evidence(
                2,
                request_message_id="<other@mcn.ru>",
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
            )
        self.assertTrue(ok["sent"])
        self.assertEqual(ok["reason"], "owner_confirmed_manual_delivery")
        self.assertFalse(wrong["sent"])
        self.assertEqual(wrong["reason"], "thread_mismatch")

    def test_manual_delivery_confirmation_prevents_sent_folder_rescan(self):
        with patch.object(G, "_company_card_send_state_evidence", return_value={
            "sent": False, "source": "send_state", "reason": "state_not_found",
        }), patch.object(G, "_company_card_manual_delivery_evidence", return_value={
            "sent": True,
            "source": "manual_confirmation",
            "reason": "owner_confirmed_manual_delivery",
            "sent_at": "2026-09-07T16:16:14+00:00",
        }), patch.object(G.subprocess, "run") as run:
            out = G._mcn_company_card_sent_evidence(
                2,
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
                request_message_id="<request@mcn.ru>",
            )
        self.assertTrue(out["sent"])
        self.assertEqual(out["source"], "manual_confirmation")
        run.assert_not_called()

    def test_sent_folder_evidence_ignores_card_before_latest_request(self):
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "ok",
                "items": [{
                    "date": "Mon, 7 Sep 2026 15:00:00 +0300",
                    "company_card_signal": True,
                }],
            }),
            stderr="",
        )
        with patch.object(G, "_company_card_send_state_evidence", return_value={
            "sent": False, "source": "send_state", "reason": "state_not_found",
        }), patch.object(G.subprocess, "run", return_value=cp):
            out = G._mcn_company_card_sent_evidence(
                2,
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
                request_message_id="<request@mcn.ru>",
            )
        self.assertFalse(out["sent"])
        self.assertEqual(out["reason"], "no_company_card_after_request")

    def test_sent_folder_evidence_accepts_card_after_latest_request(self):
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "ok",
                "items": [{
                    "date": "Mon, 7 Sep 2026 17:00:00 +0300",
                    "company_card_signal": True,
                    "boris_action_signal": True,
                    "action_id": "boris-mcn-company-card-v4",
                    "in_reply_to": "<request@mcn.ru>",
                }],
            }),
            stderr="",
        )
        with patch.object(G, "_company_card_send_state_evidence", return_value={
            "sent": False, "source": "send_state", "reason": "state_not_found",
        }), patch.object(G.subprocess, "run", return_value=cp):
            out = G._mcn_company_card_sent_evidence(
                2,
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
                request_message_id="<request@mcn.ru>",
            )
        self.assertTrue(out["sent"])
        self.assertEqual(out["source"], "sent_folder")
        self.assertEqual(out["reason"], "boris_marked_attachment_after_request")

    def test_sent_folder_current_card_content_after_request_is_trusted(self):
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "ok",
                "items": [{
                    "date": "Mon, 7 Sep 2026 17:00:00 +0300",
                    "company_card_signal": True,
                    "current_card_match": True,
                    "boris_action_signal": False,
                    "action_id": "",
                    "in_reply_to": "",
                }],
            }),
            stderr="",
        )
        with patch.object(G, "_company_card_send_state_evidence", return_value={
            "sent": False, "source": "send_state", "reason": "state_not_found",
        }), patch.object(G.subprocess, "run", return_value=cp):
            out = G._mcn_company_card_sent_evidence(
                2,
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
                request_message_id="<request@mcn.ru>",
            )
        self.assertTrue(out["sent"])
        self.assertEqual(out["source"], "sent_folder")
        self.assertEqual(out["reason"], "current_card_content_verified_after_request")

    def test_sent_folder_manual_card_after_request_is_not_trusted(self):
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "ok",
                "items": [{
                    "date": "Mon, 7 Sep 2026 17:00:00 +0300",
                    "company_card_signal": True,
                    "boris_action_signal": False,
                    "action_id": "",
                    "in_reply_to": "",
                }],
            }),
            stderr="",
        )
        with patch.object(G, "_company_card_send_state_evidence", return_value={
            "sent": False, "source": "send_state", "reason": "state_not_found",
        }), patch.object(G.subprocess, "run", return_value=cp):
            out = G._mcn_company_card_sent_evidence(
                2,
                request_date="Mon, 7 Sep 2026 16:07:30 +0300",
                request_message_id="<request@mcn.ru>",
            )
        self.assertFalse(out["sent"])
        self.assertTrue(out["delivery_ambiguous"])
        self.assertEqual(out["reason"], "unverified_manual_attachment_after_request")

    def test_company_card_request_with_incomplete_owner_requisites_stays_confirmation(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "mcn_company_card_required",
                "unique_complete_candidates": 0,
                "operational_reply": {
                    "code": "mcn_company_card_required",
                    "sender_domain": "mcn.ru",
                    "requirements": ["company_or_ip_card"],
                },
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={"status":"waiting_phone_account","accounts":0}), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={"status":"ok","mailbox_id":2,"mailbox":"owner@test"}), \
             patch.object(G, "_owner_company_card_readiness", return_value={"status":"incomplete","complete":False,"missing":["bank_bik"]}), \
             patch.object(G.subprocess, "run", return_value=cp), \
             patch.object(G, "_persist_mailbox_autoonboard"):
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["primary_next_action"]["code"], "mcn_company_card_confirmation")
        self.assertFalse(out["owner_company_card"]["complete"])

    def test_run_guardian_propagates_mailbox_owner_action_to_top_level(self):
        base = {"status": "ok", "owner_action_required": False, "owner_action_codes": []}
        mailbox = {
            "status": "owner_action",
            "reason": "mcn_contract_required_before_credentials",
            "owner_action_required": True,
            "primary_next_action": {
                "code": "mcn_contract_documents",
                "actor": "owner",
                "owner_action_required": True,
            },
        }
        with patch.object(G, "ensure_schema"), \
             patch.object(G, "telephony_autonomy_guardian", return_value=base.copy()), \
             patch.object(G, "mcn_mailbox_autoonboard_once", return_value=mailbox), \
             patch.object(G, "real_mcn_acceptance_watch_once", return_value={"status":"waiting_external_inputs"}), \
             patch.object(G, "native_release_autopilot_once", return_value={"status":"waiting_external_inputs"}):
            out = G.run_guardian_once()
        self.assertTrue(out["owner_action_required"])
        self.assertIn("mcn_contract_documents", out["owner_action_codes"])
        self.assertEqual(out["mcn_mailbox_autoonboard"]["status"], "owner_action")

    def test_recent_check_throttles_without_mailbox_access(self):
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "ok", "account_id": "acc"
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value={
                 "status": "owner_action", "reason": "mcn_contract_required_before_credentials",
                 "owner_action_required": True,
                 "primary_next_action": {
                     "code": "mcn_contract_documents",
                     "actor": "owner",
                     "owner_action_required": True,
                 },
             }), \
             patch.object(G, "_single_active_imap_mailbox") as mailbox, \
             patch.object(G.subprocess, "run") as run:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "throttled")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["primary_next_action"]["code"], "mcn_contract_documents")
        mailbox.assert_not_called()
        run.assert_not_called()

    def test_ambiguous_mailbox_escalates_without_subprocess(self):
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "ok", "account_id": "acc"
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ambiguous_imap_mailboxes", "mailboxes": 2
             }), \
             patch.object(G, "_persist_mailbox_autoonboard") as persist, \
             patch.object(G.subprocess, "run") as run:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "ambiguous_imap_mailboxes")
        self.assertTrue(out["owner_action_required"])
        persist.assert_called_once()
        run.assert_not_called()

    def test_no_candidate_is_persisted_without_owner_escalation(self):
        cp = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "status": "blocked",
                "reason": "no_complete_mcn_letter",
                "unique_complete_candidates": 0,
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "ok", "account_id": "acc"
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }), \
             patch.object(G.subprocess, "run", return_value=cp) as run, \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "no_complete_mcn_letter")
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(out["candidate_count"], 0)
        self.assertIn("--apply", run.call_args.args[0])
        persist.assert_called_once()

    def test_success_marks_connected_and_persists_safe_projection(self):
        cp = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "ok",
                "account_id": "acc",
                "truth": "credentials were consumed inside BORIS and were not returned",
            }),
            stderr="",
        )
        with patch.object(G, "current_mcn_accounts", return_value=[]), \
             patch.object(G, "_single_active_phone_account", return_value={
                 "status": "ok", "account_id": "acc"
             }), \
             patch.object(G, "_mailbox_autoonboard_recent", return_value=None), \
             patch.object(G, "_single_active_imap_mailbox", return_value={
                 "status": "ok", "mailbox_id": 2, "mailbox": "owner@test"
             }), \
             patch.object(G.subprocess, "run", return_value=cp), \
             patch.object(G, "_persist_mailbox_autoonboard") as persist:
            out = G.mcn_mailbox_autoonboard_once()
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["connected"])
        saved = persist.call_args.args[1]
        self.assertNotIn("password", json.dumps(saved).lower())
        self.assertNotIn("username", json.dumps(saved).lower())


class McnCompanyCardAutoFollowupTests(unittest.TestCase):
    def setUp(self):
        import uuid
        self.storage_key = "mcn_company_card_followup_qa_" + uuid.uuid4().hex
        self._key_patcher = patch.object(G, "_MCN_FOLLOWUP_STORAGE_KEY", self.storage_key)
        self._key_patcher.start()
        self.addCleanup(self._key_patcher.stop)
        self.op = {
            "sender_email": "manager@mcn.ru",
            "sender_domain": "mcn.ru",
            "message_id": "<company-card-request@mcn.ru>",
            "subject": "RE: Партнерская программа",
        }
        self.evidence = {
            "sent": True,
            "sent_at": "2026-09-01T00:00:00+00:00",
        }

    def tearDown(self):
        db = G.SessionLocal()
        try:
            db.execute(G.text("""
                DELETE FROM storage
                WHERE account_id='__mcn_mailbox_watch__' AND key=:k
            """), {"k": self.storage_key})
            db.commit()
        finally:
            db.close()

    def test_followup_waits_full_48_hours(self):
        now = G.datetime(2026, 9, 2, 23, 59, tzinfo=G.timezone.utc)
        with patch("app.services.client_mailboxes.send_outbound") as send:
            out = G._mcn_company_card_followup(2, self.op, self.evidence, now=now)
        self.assertEqual(out["status"], "not_due")
        self.assertFalse(out["sent"])
        self.assertEqual(out["attempts"], 0)
        send.assert_not_called()

    def test_followup_sends_once_then_waits_72_hours(self):
        first_now = G.datetime(2026, 9, 3, 1, 0, tzinfo=G.timezone.utc)
        second_now = G.datetime(2026, 9, 3, 2, 0, tzinfo=G.timezone.utc)
        with patch("app.services.client_mailboxes.send_outbound", return_value=(True, "ok", "<followup-1@boris>")) as send:
            first = G._mcn_company_card_followup(2, self.op, self.evidence, now=first_now)
            second = G._mcn_company_card_followup(2, self.op, self.evidence, now=second_now)
        self.assertEqual(first["status"], "sent")
        self.assertTrue(first["sent"])
        self.assertEqual(first["attempts"], 1)
        self.assertEqual(second["status"], "not_due")
        self.assertEqual(second["attempts"], 1)
        self.assertEqual(send.call_count, 1)
        args = send.call_args.args
        kwargs = send.call_args.kwargs
        self.assertEqual(args[0], 2)
        self.assertEqual(args[1], "manager@mcn.ru")
        self.assertIn("Уточняем статус оформления", args[3])
        self.assertNotIn("банк", args[3].lower())
        self.assertNotIn("реквизит", args[3].lower())
        self.assertEqual(kwargs["headers"]["In-Reply-To"], "<company-card-request@mcn.ru>")
        self.assertIsNone(kwargs["attachments"])

    def test_ambiguous_delivery_blocks_automatic_resend(self):
        first_now = G.datetime(2026, 9, 3, 1, 0, tzinfo=G.timezone.utc)
        later = G.datetime(2026, 9, 10, 1, 0, tzinfo=G.timezone.utc)
        with patch("app.services.client_mailboxes.send_outbound", return_value=(False, "delivery_unknown:timeout", "<followup@boris>")) as send:
            first = G._mcn_company_card_followup(2, self.op, self.evidence, now=first_now)
            second = G._mcn_company_card_followup(2, self.op, self.evidence, now=later)
        self.assertEqual(first["status"], "delivery_ambiguous")
        self.assertTrue(first["auto_retry_blocked"])
        self.assertEqual(second["status"], "delivery_ambiguous")
        self.assertTrue(second["auto_retry_blocked"])
        self.assertEqual(send.call_count, 1)

    def test_followup_stops_after_two_confirmed_reminders(self):
        first_now = G.datetime(2026, 9, 3, 1, 0, tzinfo=G.timezone.utc)
        second_now = G.datetime(2026, 9, 6, 2, 0, tzinfo=G.timezone.utc)
        third_now = G.datetime(2026, 9, 10, 3, 0, tzinfo=G.timezone.utc)
        with patch("app.services.client_mailboxes.send_outbound", return_value=(True, "ok", "<followup@boris>")) as send:
            first = G._mcn_company_card_followup(2, self.op, self.evidence, now=first_now)
            second = G._mcn_company_card_followup(2, self.op, self.evidence, now=second_now)
            third = G._mcn_company_card_followup(2, self.op, self.evidence, now=third_now)
        self.assertEqual(first["attempts"], 1)
        self.assertEqual(second["attempts"], 2)
        self.assertEqual(third["status"], "max_attempts_reached")
        self.assertTrue(third["auto_retry_blocked"])
        self.assertEqual(send.call_count, 2)


class McnOwnerGlobalAlertTests(unittest.TestCase):
    def setUp(self):
        import uuid
        self.storage_key = "mcn_phone_owner_alert_qa_" + uuid.uuid4().hex
        self._key_patcher = patch.object(G, "_MCN_OWNER_ALERT_STORAGE_KEY", self.storage_key)
        self._key_patcher.start()
        self.addCleanup(self._key_patcher.stop)

    def tearDown(self):
        db = G.SessionLocal()
        try:
            db.execute(G.text("""
                DELETE FROM storage
                WHERE account_id='__mcn_mailbox_watch__' AND key=:k
            """), {"k": self.storage_key})
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _payload(code="mcn_phone_entitlement_required"):
        return {
            "owner_action_required": True,
            "candidate_count": 1,
            "phone_account_status": (
                "waiting_phone_account"
                if code == "mcn_phone_entitlement_required"
                else "ambiguous_phone_accounts"
            ),
            "operational_reply": {
                "message_id": "<mcn-sip-qa@test>",
                "username": "MUST_NOT_LEAK_USER",
                "password": "MUST_NOT_LEAK_PASSWORD",
            },
            "primary_next_action": {
                "code": code,
                "actor": "owner",
                "owner_action_required": True,
                "text": "Нужно активировать Phone для нужного аккаунта.",
            },
        }

    def test_owner_alert_is_sent_once_for_same_provider_letter(self):
        with patch("app.ext_api.notify.send", return_value=True) as send:
            first = G._mcn_owner_action_alert_once(self._payload())
            second = G._mcn_owner_action_alert_once(self._payload())
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "already_sent")
        self.assertEqual(send.call_count, 1)
        alert_text = send.call_args.args[0]
        self.assertNotIn("MUST_NOT_LEAK_USER", alert_text)
        self.assertNotIn("MUST_NOT_LEAK_PASSWORD", alert_text)

    def test_failed_owner_alert_is_throttled_without_spam(self):
        with patch("app.ext_api.notify.send", return_value=False) as send:
            first = G._mcn_owner_action_alert_once(
                self._payload("mcn_phone_account_binding_required")
            )
            second = G._mcn_owner_action_alert_once(
                self._payload("mcn_phone_account_binding_required")
            )
        self.assertEqual(first["status"], "retry")
        self.assertEqual(second["status"], "throttled")
        self.assertEqual(send.call_count, 1)

    def test_waiting_for_mcn_reply_never_notifies_owner(self):
        payload = {
            "owner_action_required": False,
            "candidate_count": 0,
            "phone_account_status": "waiting_phone_account",
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
                "text": "BORIS ждёт MCN.",
            },
        }
        with patch("app.ext_api.notify.send") as send:
            out = G._mcn_owner_action_alert_once(payload)
        self.assertEqual(out["status"], "not_needed")
        send.assert_not_called()



if __name__ == "__main__":
    unittest.main()
