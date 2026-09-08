from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text

from app.api import payments as PAY
from app.api import wallet as WALLET
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.telephony_core import ensure_schema, phone_entitlement_status


class PhonePaymentCommercialPathTests(unittest.TestCase):
    def setUp(self):
        ensure_schema()
        suffix = uuid.uuid4().hex
        self.account_id = "__qa_phone_pay_" + suffix
        self.email = f"qa-phone-pay-{suffix}@example.test"
        with SessionLocal() as db:
            self.user_id = int(db.execute(text("""
                INSERT INTO users(email,password_hash,role,is_active,email_verified,status,email_normalized)
                VALUES(:e,'qa','client',true,true,'active',:e)
                RETURNING id
            """), {"e": self.email}).scalar_one())
            self.wallet_key = f"user:{self.user_id}"
            db.execute(text("""
                INSERT INTO accounts(account_id,owner_user_id,name,is_own)
                VALUES(:a,:u,'QA Phone Payment',false)
            """), {"a": self.account_id, "u": self.user_id})
            db.add(Storage(
                account_id=self.wallet_key,
                key="wallet",
                value=json.dumps({"balance": 5000.0, "history": []}, ensure_ascii=False),
            ))
            db.commit()

    def tearDown(self):
        PAY.PACKAGES.pop("__qa_phone_wallet", None)
        with SessionLocal() as db:
            db.execute(text("DELETE FROM telephony_audit WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM telephony_entitlements WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM payments WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM storage WHERE account_id=:w"), {"w": self.wallet_key})
            db.execute(text("DELETE FROM accounts WHERE account_id=:a"), {"a": self.account_id})
            db.execute(text("DELETE FROM users WHERE id=:u"), {"u": self.user_id})
            db.commit()

    def test_real_payment_grants_phone_once_and_duplicate_does_not_extend(self):
        package = {"sum": 1234, "unit": "phone_subscription", "days": 30, "title": "QA Phone"}
        ref = "qa-" + uuid.uuid4().hex
        with patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={"status": "ok"}):
            with SessionLocal() as db:
                PAY.grant_package(
                    self.account_id, "phone_monthly", package, db,
                    amount=1234, source="qa", inv_id=ref,
                )
            first = phone_entitlement_status(self.account_id)
            self.assertTrue(first["active"])
            self.assertEqual(first["price_rub"], 1234)
            first_until = first["paid_until"]

            with SessionLocal() as db:
                replay = PAY.grant_package(
                    self.account_id, "phone_monthly", package, db,
                    amount=1234, source="qa", inv_id=ref,
                )
            second = phone_entitlement_status(self.account_id)

        self.assertEqual(replay["status"], "duplicate_payment")
        self.assertTrue(replay["phone_recovery"]["active"])
        self.assertTrue(replay["phone_recovery"].get("idempotent_replay"))
        self.assertEqual(first_until, second["paid_until"])

    def test_duplicate_ledger_recovers_missing_phone_entitlement(self):
        package = {"sum": 2345, "unit": "phone_subscription", "days": 30, "title": "QA Phone"}
        ref = "qa-recovery-" + uuid.uuid4().hex
        with SessionLocal() as db:
            db.execute(text("""
                INSERT INTO payments(account_id,source,pack,amount_rub,inv_id,comment)
                VALUES(:a,'qa_recovery','phone_monthly',2345,:i,'QA recovery')
            """), {"a": self.account_id, "i": ref})
            db.commit()

        self.assertFalse(phone_entitlement_status(self.account_id)["active"])
        with patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={"status": "ok"}):
            with SessionLocal() as db:
                out = PAY.grant_package(
                    self.account_id, "phone_monthly", package, db,
                    amount=2345, source="qa_recovery", inv_id=ref,
                )
        self.assertEqual(out["status"], "duplicate_payment")
        self.assertTrue(out["phone_recovery"]["active"])
        self.assertTrue(phone_entitlement_status(self.account_id)["active"])

    def test_phone_purchase_does_not_enter_reactivation_product_limits(self):
        package = {"sum": 1999, "unit": "phone_subscription", "days": 30, "title": "QA Phone"}
        ref = "qa-no-reactivation-" + uuid.uuid4().hex
        with patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={"status": "ok"}), \
             patch("app.product_limits.grant_product_limits") as grant_limits:
            with SessionLocal() as db:
                PAY.grant_package(
                    self.account_id, "phone_monthly", package, db,
                    amount=1999, source="qa_no_reactivation", inv_id=ref,
                )
        grant_limits.assert_not_called()
        self.assertTrue(phone_entitlement_status(self.account_id)["active"])

    def test_wallet_purchase_charges_once_and_uses_account_owner_wallet(self):
        PAY.PACKAGES["__qa_phone_wallet"] = {
            "sum": 1500, "unit": "phone_subscription", "days": 30, "title": "QA Wallet Phone"
        }
        pid = "web:" + uuid.uuid4().hex
        user = SimpleNamespace(id=self.user_id, role="client", email=self.email)
        with patch("telephony_guardian_runner.mcn_mailbox_autoonboard_once", return_value={"status": "ok"}):
            first = WALLET._purchase_pack_from_wallet(
                self.account_id, "__qa_phone_wallet", pid, user
            )
            replay = WALLET._purchase_pack_from_wallet(
                self.account_id, "__qa_phone_wallet", pid, user
            )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["charged"], 1500.0)
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(replay["charged"], 0)
        with SessionLocal() as db:
            raw = db.query(Storage).filter(
                Storage.account_id == self.wallet_key, Storage.key == "wallet"
            ).first()
            wallet = json.loads(raw.value)
        self.assertEqual(float(wallet["balance"]), 3500.0)
        self.assertTrue(phone_entitlement_status(self.account_id)["active"])


if __name__ == "__main__":
    unittest.main()
