# -*- coding: utf-8 -*-
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute
from app.api import telephony as API

TEST_CARD_FP = "a" * 64

def current_state():
    return {
        "status": "owner_action",
        "reason": "mcn_company_card_required",
        "mailbox_id": 2,
        "phone_account_status": "waiting_phone_account",
        "owner_action_required": True,
        "primary_next_action": {
            "code": "mcn_company_card_send_approval",
            "actor": "owner",
            "owner_action_required": True,
            "text": "Разрешить отправку",
        },
        "operational_reply": {
            "code": "mcn_company_card_required",
            "date": "Mon, 7 Sep 2026 16:07:30 +0300",
            "subject": "RE: RE: Партнерская программа",
            "sender_domain": "mcn.ru",
            "requirements": ["company_or_ip_card"],
            "provider_path": ["send_company_card_to_mcn", "receive_sip_credentials"],
            "password": "SENSITIVE_PROVIDER_VALUE",
        },
        "company_card_draft": {
            "status": "draft_exists",
            "reason": "ok",
            "ready": True,
            "sent": False,
            "delivery_ambiguous": False,
            "card_fingerprint": TEST_CARD_FP,
            "bank_account": "SENSITIVE_BANK_VALUE",
        },
        "sip_password": "SENSITIVE_SIP_VALUE",
        "bank_account": "SENSITIVE_BANK_VALUE",
    }


def manual_ambiguous_state():
    out = current_state()
    out["primary_next_action"] = {
        "code": "mcn_company_card_delivery_verify",
        "actor": "owner",
        "owner_action_required": True,
        "text": "Проверить ручную отправку",
    }
    out["operational_reply"]["message_id"] = "<request@mcn.ru>"
    out["company_card_draft"] = {}
    out["company_card_sent_evidence"] = {
        "sent": False,
        "source": "sent_folder",
        "reason": "unverified_manual_attachment_after_request",
        "sent_at": "2026-09-07T16:16:14+00:00",
        "delivery_ambiguous": True,
    }
    return out


def binding_state():
    return {
        "status": "mcn_letter_ready_waiting_phone_account",
        "reason": "ambiguous_active_phone_accounts",
        "mailbox_id": 2,
        "candidate_count": 1,
        "candidate_evidence": {
            "uid": "302900",
            "date": "Mon, 7 Sep 2026 20:00:00 +0300",
            "sender_domain": "mcn.ru",
            "subject": "Настройки MCN SIP",
            "auth_mode": "registration",
            "fields_found": {
                "registrar": True,
                "username": True,
                "password": True,
                "did": True,
                "source_ip": False,
            },
            "password": "SENSITIVE_SIP_VALUE",
            "did": "+79990000000",
            "registrar": "sip.mcn.ru",
        },
        "phone_account_status": "ambiguous_phone_accounts",
        "owner_action_required": True,
        "primary_next_action": {
            "code": "mcn_phone_account_binding_required",
            "actor": "owner",
            "owner_action_required": True,
            "text": "Выберите Phone-аккаунт",
        },
    }


def connected_state():
    return {
        "status": "not_needed",
        "reason": "mcn_already_configured",
        "owner_action_required": False,
        "primary_next_action": {
            "code": "mcn_connected",
            "actor": "boris",
            "owner_action_required": False,
            "text": "MCN подключён",
        },
    }


class TelephonyMcnOwnerApiTests(unittest.TestCase):
    def setUp(self):
        self.owner = SimpleNamespace(id=101, role="owner")
        self.body = {
            "confirm_share_banking": True,
            "expected_action_code": "mcn_company_card_send_approval",
            "expected_provider_reply_date": "Mon, 7 Sep 2026 16:07:30 +0300",
            "expected_card_fingerprint": TEST_CARD_FP,
        }

    def test_mcn_routes_require_platform_owner_dependency(self):
        found = {}
        for route in API.router.routes:
            if isinstance(route, APIRoute) and route.path in {
                "/api/telephony/mcn/onboarding",
                "/api/telephony/platform/phone-entitlement",
                "/api/telephony/platform/phone-entitlement/activate",
                "/api/telephony/platform/phone-entitlement/revoke",
                "/api/telephony/mcn/account-binding/confirm",
                "/api/telephony/mcn/company-card/send",
                "/api/telephony/mcn/company-card/manual-delivery/confirm",
            }:
                found[route.path] = [dep.call for dep in route.dependant.dependencies]
        self.assertEqual(set(found), {
            "/api/telephony/mcn/onboarding",
            "/api/telephony/platform/phone-entitlement",
            "/api/telephony/platform/phone-entitlement/activate",
            "/api/telephony/platform/phone-entitlement/revoke",
            "/api/telephony/mcn/account-binding/confirm",
            "/api/telephony/mcn/company-card/send",
            "/api/telephony/mcn/company-card/manual-delivery/confirm",
        })
        for deps in found.values():
            self.assertIn(API._require_private_platform_owner, deps)
            self.assertNotIn(API.require_owner, deps)

    def test_private_owner_gate_rejects_other_owner_role(self):
        qa_owner = SimpleNamespace(id=7, role="owner", email="qa@example.test")
        with self.assertRaises(HTTPException) as ctx:
            API._require_private_platform_owner(qa_owner)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_private_owner_gate_accepts_canonical_platform_owner(self):
        from app.services.platform_roles import PLATFORM_OWNER_ID, PLATFORM_OWNER_EMAIL
        owner = SimpleNamespace(
            id=PLATFORM_OWNER_ID,
            role="owner",
            email=PLATFORM_OWNER_EMAIL,
        )
        self.assertIs(API._require_private_platform_owner(owner), owner)

    def test_safe_projection_never_returns_sensitive_values(self):
        out = API._mcn_onboarding_safe_projection(current_state())
        raw = json.dumps(out, ensure_ascii=False)
        for value in ("SENSITIVE_PROVIDER_VALUE", "SENSITIVE_BANK_VALUE", "SENSITIVE_SIP_VALUE"):
            self.assertNotIn(value, raw)
        self.assertTrue(out["approval_supported"])
        self.assertEqual(out["operational_reply"]["sender_domain"], "mcn.ru")

    def test_manual_delivery_safe_projection_exposes_only_confirmation_metadata(self):
        raw = manual_ambiguous_state()
        raw["company_card_sent_evidence"]["bank_account"] = "SENSITIVE_BANK_VALUE"
        out = API._mcn_onboarding_safe_projection(raw)
        self.assertTrue(out["manual_delivery_confirmation_supported"])
        self.assertTrue(out["company_card_sent_evidence"]["delivery_ambiguous"])
        serialized = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("SENSITIVE_BANK_VALUE", serialized)

    def test_manual_delivery_requires_explicit_confirmation(self):
        body = {
            "confirm_manual_delivery": False,
            "expected_action_code": "mcn_company_card_delivery_verify",
            "expected_provider_reply_date": "Mon, 7 Sep 2026 16:07:30 +0300",
            "expected_sent_at": "2026-09-07T16:16:14+00:00",
        }
        with patch("app.services.mcn_core.confirm_mcn_company_card_manual_delivery") as persist:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_manual_delivery_confirm(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 400)
        persist.assert_not_called()

    def test_manual_delivery_stale_evidence_rejects_before_persist(self):
        body = {
            "confirm_manual_delivery": True,
            "expected_action_code": "mcn_company_card_delivery_verify",
            "expected_provider_reply_date": "Mon, 7 Sep 2026 16:07:30 +0300",
            "expected_sent_at": "2026-09-07T15:00:00+00:00",
        }
        with patch.object(API, "_mcn_onboarding_raw_state", return_value=manual_ambiguous_state()), \
             patch("app.services.mcn_core.confirm_mcn_company_card_manual_delivery") as persist:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_manual_delivery_confirm(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 409)
        persist.assert_not_called()

    def test_valid_manual_delivery_confirmation_persists_and_refreshes_without_sending(self):
        before = manual_ambiguous_state()
        after = {
            **manual_ambiguous_state(),
            "status": "waiting_mcn_response",
            "owner_action_required": False,
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
                "text": "BORIS ждёт MCN",
            },
            "company_card_sent_evidence": {
                "sent": True,
                "source": "manual_confirmation",
                "reason": "owner_confirmed_manual_delivery",
                "sent_at": "2026-09-07T16:16:14+00:00",
                "delivery_ambiguous": False,
            },
        }
        body = {
            "confirm_manual_delivery": True,
            "expected_action_code": "mcn_company_card_delivery_verify",
            "expected_provider_reply_date": "Mon, 7 Sep 2026 16:07:30 +0300",
            "expected_sent_at": "2026-09-07T16:16:14+00:00",
        }
        confirmed = {
            "status": "confirmed",
            "mailbox_id": 2,
            "request_message_id": "<request@mcn.ru>",
            "sent_at": "2026-09-07T16:16:14+00:00",
        }
        with patch.object(API, "_mcn_onboarding_raw_state", side_effect=[before, after]), \
             patch("app.services.mcn_core.confirm_mcn_company_card_manual_delivery", return_value=confirmed) as persist, \
             patch("app.services.telephony_core._audit") as audit, \
             patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={
                 "status": "waiting_mcn_response"
             }) as refresh, \
             patch.object(API, "_run_mcn_company_card_send") as send:
            out = API.mcn_company_card_manual_delivery_confirm(body, self.owner)
        persist.assert_called_once_with(
            2,
            "<request@mcn.ru>",
            "2026-09-07T16:16:14+00:00",
            101,
        )
        refresh.assert_called_once()
        send.assert_not_called()
        audit.assert_called_once()
        self.assertEqual(out["status"], "confirmed")
        self.assertFalse(out["onboarding"]["owner_action_required"])
        self.assertEqual(
            out["onboarding"]["primary_next_action"]["code"],
            "mcn_company_card_sent_waiting_reply",
        )

    def test_phone_entitlement_activate_requires_explicit_paid_confirmation(self):
        body = {
            "confirm_paid_phone": False,
            "account_id": "acc-a",
            "paid_until": "2026-10-08T00:00:00+00:00",
            "commercial_ref": "invoice-1",
        }
        with patch.object(API, "provision_paid_phone_entitlement") as provision:
            with self.assertRaises(HTTPException) as ctx:
                API.platform_phone_entitlement_activate(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 400)
        provision.assert_not_called()

    def test_phone_entitlement_activate_uses_exact_commercial_evidence_and_refreshes(self):
        body = {
            "confirm_paid_phone": True,
            "account_id": "acc-a",
            "paid_until": "2026-10-08T00:00:00+00:00",
            "commercial_ref": "invoice-2026-001",
            "price_rub": 15000,
        }
        activated = {
            "status": "active",
            "active": True,
            "account_id": "acc-a",
            "paid_until": "2026-10-08T00:00:00+00:00",
            "price_rub": 15000,
            "has_commercial_ref": True,
            "commercial_ref_hash": "deadbeefdeadbeef",
        }
        with patch.object(API, "provision_paid_phone_entitlement", return_value=activated) as provision, \
             patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={"status": "ok"}) as refresh, \
             patch.object(API, "_mcn_onboarding_raw_state", return_value=connected_state()), \
             patch.object(API, "_phone_entitlement_transport_reconcile", return_value={"status":"ok","changed":True,"applied":True}) as reconcile:
            out = API.platform_phone_entitlement_activate(body, self.owner)
        provision.assert_called_once_with(
            "acc-a",
            "2026-10-08T00:00:00+00:00",
            "invoice-2026-001",
            actor_user_id=101,
            price_rub=15000,
            source="platform_owner_confirmed",
            allow_zero_price=False,
        )
        refresh.assert_called_once_with(force_refresh=True)
        reconcile.assert_called_once_with()
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["transport_reconcile"]["applied"])
        serialized = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("invoice-2026-001", serialized)

    def test_phone_entitlement_activate_rejects_shortening_paid_period(self):
        body = {
            "confirm_paid_phone": True,
            "account_id": "acc-a",
            "paid_until": "2026-09-20T00:00:00+00:00",
            "commercial_ref": "invoice-short",
        }
        with patch.object(API, "provision_paid_phone_entitlement", return_value={
            "status": "would_shorten_paid_period",
            "active": True,
            "account_id": "acc-a",
            "paid_until": "2026-10-08T00:00:00+00:00",
        }):
            with self.assertRaises(HTTPException) as ctx:
                API.platform_phone_entitlement_activate(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_phone_entitlement_revoke_requires_confirmation(self):
        with patch.object(API, "revoke_phone_entitlement") as revoke:
            with self.assertRaises(HTTPException) as ctx:
                API.platform_phone_entitlement_revoke(
                    {"confirm_revoke_phone": False, "account_id": "acc-a"},
                    self.owner,
                )
        self.assertEqual(ctx.exception.status_code, 400)
        revoke.assert_not_called()

    def test_phone_entitlement_revoke_reconciles_transport_immediately(self):
        revoked = {"status":"revoked","active":False,"account_id":"acc-a"}
        with patch.object(API, "revoke_phone_entitlement", return_value=revoked) as revoke, \
             patch.object(API, "_phone_entitlement_transport_reconcile", return_value={
                 "status":"ok","changed":True,"applied":True,"deferred":False
             }) as reconcile:
            out = API.platform_phone_entitlement_revoke(
                {"confirm_revoke_phone": True, "account_id": "acc-a", "reason": "payment_ended"},
                self.owner,
            )
        revoke.assert_called_once_with("acc-a", actor_user_id=101, reason="payment_ended")
        reconcile.assert_called_once_with()
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["entitlement"]["active"])
        self.assertTrue(out["transport_reconcile"]["applied"])

    def test_transport_reconcile_projection_exposes_no_trunk_or_provider_details(self):
        raw={
            "status":"ok","changed":True,"applied":False,"deferred":True,
            "action":"defer_active_calls","rendered":1,
            "trunks_verified":1,"trunks_degraded":0,
            "provider_verified":1,"provider_degraded":0,
            "error_code":None,"owner_action_required":False,
            "trunks":[{"account_id":"secret-account","registration_state":"registered"}],
            "provider_results":[{"account_id":"secret-account","status":"ok"}],
            "firewall":{"desired_networks":["10.0.0.0/8"]},
        }
        with patch("app.services.asterisk_gateway.mcn_pjsip_guardian", return_value=raw):
            out=API._phone_entitlement_transport_reconcile()
        self.assertEqual(out["status"],"ok")
        self.assertTrue(out["deferred"])
        self.assertEqual(out["action"],"defer_active_calls")
        self.assertNotIn("trunks",out)
        self.assertNotIn("provider_results",out)
        self.assertNotIn("firewall",out)
        self.assertNotIn("secret-account",json.dumps(out,ensure_ascii=False))

    def test_phone_entitlement_status_never_needs_commercial_reference_value(self):
        safe = {
            "status": "active",
            "active": True,
            "account_id": "acc-a",
            "commercial_ref_hash": "deadbeefdeadbeef",
            "has_commercial_ref": True,
        }
        with patch.object(API, "phone_entitlement_status", return_value=safe) as status:
            out = API.platform_phone_entitlement_status("acc-a", self.owner)
        status.assert_called_once_with("acc-a")
        self.assertNotIn("commercial_ref", out)

    def test_binding_projection_is_safe_and_supported(self):
        eligible=[
            {"account_id":"acc-a","display_name":"Клиент А","slot_no":1,"paid_until":None},
            {"account_id":"acc-b","display_name":"Клиент Б","slot_no":1,"paid_until":None},
        ]
        with patch.object(API,"_mcn_eligible_phone_accounts",return_value=eligible):
            out=API._mcn_onboarding_safe_projection(binding_state())
        self.assertTrue(out["account_binding_supported"])
        self.assertEqual(out["candidate_evidence"]["uid"],"302900")
        serialized=json.dumps(out,ensure_ascii=False)
        for value in ("SENSITIVE_SIP_VALUE","+79990000000","sip.mcn.ru"):
            self.assertNotIn(value,serialized)

    def test_binding_stale_candidate_is_rejected_before_apply(self):
        eligible=[
            {"account_id":"acc-a","display_name":"Клиент А","slot_no":1,"paid_until":None},
            {"account_id":"acc-b","display_name":"Клиент Б","slot_no":1,"paid_until":None},
        ]
        body={
            "confirm_account_binding":True,
            "account_id":"acc-a",
            "expected_action_code":"mcn_phone_account_binding_required",
            "expected_candidate_uid":"stale-uid",
            "expected_candidate_date":"Mon, 7 Sep 2026 20:00:00 +0300",
        }
        with patch.object(API,"_mcn_onboarding_raw_state",return_value=binding_state()),              patch.object(API,"_mcn_eligible_phone_accounts",return_value=eligible),              patch.object(API,"_run_mcn_account_binding") as bind:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_account_binding_confirm(body,self.owner)
        self.assertEqual(ctx.exception.status_code,409)
        bind.assert_not_called()

    def test_valid_binding_applies_exact_candidate(self):
        eligible=[{"account_id":"acc-a"},{"account_id":"acc-b"}]
        body={"confirm_account_binding":True,"account_id":"acc-b","expected_action_code":"mcn_phone_account_binding_required","expected_candidate_uid":"302900","expected_candidate_date":"Mon, 7 Sep 2026 20:00:00 +0300"}
        result={"status":"ok","account_id":"acc-b","message_uid":"302900","message_date":"Mon, 7 Sep 2026 20:00:00 +0300","returncode":0}
        with patch.object(API,"_mcn_onboarding_raw_state",side_effect=[binding_state(),connected_state()]), patch.object(API,"_mcn_eligible_phone_accounts",return_value=eligible), patch.object(API,"_run_mcn_account_binding",return_value=result) as bind, patch("app.services.telephony_core._audit"), patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once",return_value={"status":"not_needed"}):
            out=API.mcn_account_binding_confirm(body,self.owner)
        bind.assert_called_once_with(2,"acc-b","302900","Mon, 7 Sep 2026 20:00:00 +0300")
        self.assertEqual(out["status"],"ok")
        self.assertEqual(out["onboarding"]["primary_next_action"]["code"],"mcn_connected")

    def test_missing_explicit_banking_confirmation_rejects_before_send(self):
        body = dict(self.body)
        body["confirm_share_banking"] = False
        with patch.object(API, "_run_mcn_company_card_send") as send:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_send(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 400)
        send.assert_not_called()

    def test_stale_expected_reply_rejects_before_send(self):
        body = dict(self.body)
        body["expected_provider_reply_date"] = "stale-date"
        with patch.object(API, "_mcn_onboarding_raw_state", return_value=current_state()), \
             patch.object(API, "_run_mcn_company_card_send") as send:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_send(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 409)
        send.assert_not_called()

    def test_stale_card_fingerprint_rejects_before_send(self):
        body = dict(self.body)
        body["expected_card_fingerprint"] = "b" * 64
        with patch.object(API, "_mcn_onboarding_raw_state", return_value=current_state()), \
             patch.object(API, "_run_mcn_company_card_send") as send:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_send(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 409)
        send.assert_not_called()

    def test_missing_card_fingerprint_rejects_before_send(self):
        body = dict(self.body)
        body.pop("expected_card_fingerprint")
        with patch.object(API, "_run_mcn_company_card_send") as send:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_send(body, self.owner)
        self.assertEqual(ctx.exception.status_code, 400)
        send.assert_not_called()

    def test_approval_not_supported_rejects_before_send(self):
        raw = current_state()
        raw["company_card_draft"]["ready"] = False
        with patch.object(API, "_mcn_onboarding_raw_state", return_value=raw), \
             patch.object(API, "_run_mcn_company_card_send") as send:
            with self.assertRaises(HTTPException) as ctx:
                API.mcn_company_card_send(self.body, self.owner)
        self.assertEqual(ctx.exception.status_code, 409)
        send.assert_not_called()

    def test_valid_owner_approval_sends_once_audits_and_refreshes(self):
        before = current_state()
        after = {
            **current_state(),
            "status": "waiting_mcn_response",
            "owner_action_required": False,
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
                "text": "BORIS ждёт MCN",
            },
            "company_card_draft": {
                "status": "already_sent",
                "reason": "exactly_once_guard",
                "ready": False,
                "sent": True,
                "delivery_ambiguous": False,
            },
        }
        send_result = {
            "status": "sent",
            "reason": "ok",
            "message_id": "<sent@test>",
            "sent_copy_saved": True,
            "send_state_persisted": True,
            "retry_blocked": True,
            "recipient_domain_verified": True,
            "returncode": 0,
        }
        with patch.object(API, "_mcn_onboarding_raw_state", side_effect=[before, after]), \
             patch.object(API, "_run_mcn_company_card_send", return_value=send_result) as send, \
             patch("app.services.telephony_core._audit") as audit, \
             patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={
                 "status": "waiting_mcn_response"
             }) as refresh:
            out = API.mcn_company_card_send(self.body, self.owner)

        send.assert_called_once_with(2)
        refresh.assert_called_once()
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(out["status"], "sent")
        self.assertFalse(out["onboarding"]["owner_action_required"])
        self.assertEqual(
            out["onboarding"]["primary_next_action"]["code"],
            "mcn_company_card_sent_waiting_reply",
        )
        serialized = json.dumps(out, ensure_ascii=False)
        for value in ("SENSITIVE_BANK_VALUE", "SENSITIVE_SIP_VALUE"):
            self.assertNotIn(value, serialized)
        audit_serialized = json.dumps(
            [x.kwargs for x in audit.call_args_list],
            ensure_ascii=False,
            default=str,
        )
        for value in ("SENSITIVE_BANK_VALUE", "SENSITIVE_SIP_VALUE"):
            self.assertNotIn(value, audit_serialized)

    def test_delivery_ambiguous_is_returned_without_retry_loop(self):
        before = current_state()
        after = {
            **current_state(),
            "status": "owner_action",
            "primary_next_action": {
                "code": "mcn_company_card_delivery_verify",
                "actor": "owner",
                "owner_action_required": True,
                "text": "Проверить доставку",
            },
            "company_card_draft": {
                "status": "delivery_ambiguous",
                "reason": "previous_send_may_have_reached_smtp",
                "ready": False,
                "sent": False,
                "delivery_ambiguous": True,
            },
        }
        result = {
            "status": "delivery_ambiguous",
            "reason": "previous_send_may_have_reached_smtp",
            "message_id": None,
            "sent_copy_saved": False,
            "send_state_persisted": True,
            "retry_blocked": True,
            "recipient_domain_verified": True,
            "returncode": 8,
        }
        with patch.object(API, "_mcn_onboarding_raw_state", side_effect=[before, after]), \
             patch.object(API, "_run_mcn_company_card_send", return_value=result) as send, \
             patch("app.services.telephony_core._audit"), \
             patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={
                 "status": "owner_action"
             }):
            out = API.mcn_company_card_send(self.body, self.owner)
        send.assert_called_once()
        self.assertEqual(out["status"], "delivery_ambiguous")
        self.assertTrue(out["send"]["retry_blocked"])


if __name__ == "__main__":
    unittest.main()
