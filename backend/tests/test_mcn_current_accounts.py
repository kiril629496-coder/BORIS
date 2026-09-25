import unittest

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import mcn_core
from app.services.telephony_core import ensure_schema as ensure_telephony_schema


QA_SELECTED = "__qa_mcn_current_selected__"
QA_SWITCHED = "__qa_mcn_current_switched__"
QA_LEGACY_TRUNK = "__qa_mcn_current_legacy_trunk__"
QA_LEGACY_DID = "__qa_mcn_current_legacy_did__"
QA_DISABLED = "__qa_mcn_current_disabled__"
QA_ACCOUNTS = (
    QA_SELECTED,
    QA_SWITCHED,
    QA_LEGACY_TRUNK,
    QA_LEGACY_DID,
    QA_DISABLED,
)


class CurrentMcnAccountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_telephony_schema()
        mcn_core.ensure_schema()

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = SessionLocal()
        try:
            for account_id in QA_ACCOUNTS:
                db.execute(text("DELETE FROM telephony_dids WHERE account_id=:a"), {"a": account_id})
                db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"), {"a": account_id})
                db.execute(text("DELETE FROM telephony_provider_configs WHERE account_id=:a"), {"a": account_id})
            db.commit()
        finally:
            db.close()

    def _provider(self, account_id: str, provider: str):
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO telephony_provider_configs(
                    account_id,provider,status,public_config_json
                ) VALUES(:a,:p,'credentials_required','{}'::jsonb)
                ON CONFLICT(account_id) DO UPDATE SET
                    provider=excluded.provider,
                    status='credentials_required',
                    credentials_enc=NULL,
                    public_config_json='{}'::jsonb,
                    updated_at=now()
            """), {"a": account_id, "p": provider})
            db.commit()
        finally:
            db.close()

    def _trunk(self, account_id: str, enabled: bool = True):
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO telephony_trunks(
                    account_id,provider,name,auth_mode,enabled,status
                ) VALUES(:a,'mcn','qa-current','registration',:enabled,'waiting_mcn')
                ON CONFLICT(account_id,provider,name) DO UPDATE SET
                    enabled=excluded.enabled,status='waiting_mcn',updated_at=now()
            """), {"a": account_id, "enabled": enabled})
            db.commit()
        finally:
            db.close()

    def _did(self, account_id: str, active: bool = True):
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO telephony_dids(
                    provider,number_e164,account_id,purpose,
                    inbound_enabled,outbound_cli_enabled,status
                ) VALUES('mcn',:n,:a,'main',true,false,:status)
            """), {
                "a": account_id,
                "n": "+7999" + str(abs(hash(account_id)) % 10000000).zfill(7),
                "status": "active" if active else "reserved",
            })
            db.commit()
        finally:
            db.close()

    def test_provider_selected_mcn_is_discovered_before_trunk_exists(self):
        self._provider(QA_SELECTED, "mcn")
        self.assertIn(
            QA_SELECTED,
            mcn_core.current_mcn_accounts(include_synthetic=True),
        )
        self.assertNotIn(QA_SELECTED, mcn_core.current_mcn_accounts())

    def test_explicit_other_provider_suppresses_orphan_mcn_trunk(self):
        self._provider(QA_SWITCHED, "telphin")
        self._trunk(QA_SWITCHED, enabled=True)
        self.assertNotIn(
            QA_SWITCHED,
            mcn_core.current_mcn_accounts(include_synthetic=True),
        )

    def test_enabled_legacy_mcn_trunk_without_provider_config_is_discovered(self):
        self._trunk(QA_LEGACY_TRUNK, enabled=True)
        self.assertIn(
            QA_LEGACY_TRUNK,
            mcn_core.current_mcn_accounts(include_synthetic=True),
        )

    def test_active_legacy_mcn_did_without_provider_config_is_discovered(self):
        self._did(QA_LEGACY_DID, active=True)
        self.assertIn(
            QA_LEGACY_DID,
            mcn_core.current_mcn_accounts(include_synthetic=True),
        )

    def test_disabled_or_inactive_legacy_rows_do_not_create_current_mcn(self):
        self._trunk(QA_DISABLED, enabled=False)
        self._did(QA_DISABLED, active=False)
        self.assertNotIn(
            QA_DISABLED,
            mcn_core.current_mcn_accounts(include_synthetic=True),
        )


if __name__ == "__main__":
    unittest.main()
