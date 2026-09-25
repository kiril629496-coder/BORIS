# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.telephony_core import (
    active_phone_entitlements,
    ensure_schema,
    phone_entitlement_status,
    provision_paid_phone_entitlement,
    revoke_phone_entitlement,
)


class PhoneEntitlementContractTests(unittest.TestCase):
    def setUp(self):
        ensure_schema()
        self.account_id = "__qa_phone_entitlement_" + uuid.uuid4().hex
        self.slot_id = None
        with SessionLocal() as db:
            db.execute(
                text("INSERT INTO accounts(account_id,name,is_own) VALUES(:a,:n,false)"),
                {"a": self.account_id, "n": "QA Phone Entitlement"},
            )
            db.commit()

    def tearDown(self):
        with SessionLocal() as db:
            db.execute(text("DELETE FROM telephony_audit WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM telephony_entitlements WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM account_slots WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM accounts WHERE account_id=:a"), {"a": self.account_id})
            db.commit()

    def test_account_existence_does_not_grant_phone(self):
        out = phone_entitlement_status(self.account_id)
        self.assertEqual(out["status"], "not_entitled")
        self.assertFalse(out["active"])

    def test_inbox_slot_does_not_grant_phone(self):
        with SessionLocal() as db:
            owner_id = db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar()
            if owner_id is None:
                self.skipTest("no users available for account_slots FK")
            slot_no = 900000000 + (uuid.uuid4().int % 90000000)
            db.execute(text("""
                INSERT INTO account_slots(
                    owner_user_id,product,slot_no,status,account_id,account_name,
                    period_start,paid_until,price_rub,payment_id
                ) VALUES(:u,'inbox',:s,'connected',:a,'QA Inbox',now(),now()+interval '30 days',1,'qa-inbox')
            """), {"u": int(owner_id), "s": int(slot_no), "a": self.account_id})
            db.commit()
        out = phone_entitlement_status(self.account_id)
        self.assertEqual(out["status"], "not_entitled")
        self.assertFalse(out["active"])

    def test_legacy_phone_slot_is_not_an_entitlement(self):
        with SessionLocal() as db:
            owner_id = db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar()
            if owner_id is None:
                self.skipTest("no users available for account_slots FK")
            slot_no = 800000000 + (uuid.uuid4().int % 90000000)
            db.execute(text("""
                INSERT INTO account_slots(
                    owner_user_id,product,slot_no,status,account_id,account_name,
                    period_start,paid_until,price_rub,payment_id
                ) VALUES(:u,'phone',:s,'connected',:a,'Legacy Phone',now(),now()+interval '30 days',15000,'legacy-phone')
            """), {"u": int(owner_id), "s": int(slot_no), "a": self.account_id})
            db.commit()
        out = phone_entitlement_status(self.account_id)
        self.assertEqual(out["status"], "not_entitled")
        self.assertFalse(out["active"])
        active = [x for x in active_phone_entitlements() if x.get("account_id") == self.account_id]
        self.assertEqual(active, [])

    def test_paid_activation_extension_guard_and_revoke(self):
        now = datetime.now(timezone.utc)
        activated = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=30),
            "qa-commercial-ref-1",
            price_rub=12345,
            source="qa",
        )
        self.assertTrue(activated["active"])
        self.assertTrue(activated["has_commercial_ref"])
        self.assertNotIn("commercial_ref", activated)

        shorter = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=10),
            "qa-commercial-ref-2",
            price_rub=12345,
            source="qa",
        )
        self.assertEqual(shorter["status"], "would_shorten_paid_period")

        extended = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=60),
            "qa-commercial-ref-3",
            price_rub=12345,
            source="qa-renewal",
        )
        self.assertTrue(extended["active"])
        self.assertEqual(extended["price_rub"], 12345)

        active = [
            x for x in active_phone_entitlements()
            if x.get("account_id") == self.account_id
        ]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["entitlement_source"], "telephony_entitlement")

        revoked = revoke_phone_entitlement(self.account_id, reason="qa")
        self.assertEqual(revoked["status"], "revoked")
        self.assertFalse(revoked["active"])
        active_after = [
            x for x in active_phone_entitlements()
            if x.get("account_id") == self.account_id
        ]
        self.assertEqual(active_after, [])

    def test_same_commercial_reference_is_idempotent_and_never_extends_twice(self):
        now = datetime.now(timezone.utc)
        first_until = now + timedelta(days=30)
        first = provision_paid_phone_entitlement(
            self.account_id,
            first_until,
            "qa-same-payment-ref",
            price_rub=12345,
            source="qa",
        )
        self.assertTrue(first["active"])
        replay = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=60),
            "qa-same-payment-ref",
            price_rub=12345,
            source="qa",
        )
        self.assertTrue(replay["active"])
        self.assertTrue(replay.get("idempotent_replay"))
        first_dt = datetime.fromisoformat(str(first["paid_until"]).replace("Z","+00:00"))
        replay_dt = datetime.fromisoformat(str(replay["paid_until"]).replace("Z","+00:00"))
        self.assertEqual(first_dt, replay_dt)

    def test_activation_requires_future_paid_until_commercial_reference_and_price(self):
        now = datetime.now(timezone.utc)
        missing_ref = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=30),
            "",
            price_rub=15000,
        )
        self.assertEqual(missing_ref["status"], "commercial_reference_required")
        past = provision_paid_phone_entitlement(
            self.account_id,
            now - timedelta(seconds=1),
            "qa-commercial-ref",
            price_rub=15000,
        )
        self.assertEqual(past["status"], "future_paid_until_required")
        missing_price = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=30),
            "qa-missing-price",
        )
        self.assertEqual(missing_price["status"], "price_required")
        zero_price = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=30),
            "qa-zero-price",
            price_rub=0,
        )
        self.assertEqual(zero_price["status"], "zero_price_confirmation_required")
        zero_price_confirmed = provision_paid_phone_entitlement(
            self.account_id,
            now + timedelta(days=30),
            "qa-zero-price-confirmed",
            price_rub=0,
            allow_zero_price=True,
        )
        self.assertTrue(zero_price_confirmed["active"])
        self.assertEqual(zero_price_confirmed["price_rub"], 0)

    def test_nonexistent_account_cannot_be_entitled(self):
        out = provision_paid_phone_entitlement(
            "__qa_missing_" + uuid.uuid4().hex,
            datetime.now(timezone.utc) + timedelta(days=30),
            "qa-commercial-ref",
            price_rub=15000,
        )
        self.assertEqual(out["status"], "account_not_found")
        self.assertFalse(out["active"])


if __name__ == "__main__":
    unittest.main()
