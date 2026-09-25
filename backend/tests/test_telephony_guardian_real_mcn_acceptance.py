import unittest
from unittest.mock import patch

from sqlalchemy import text

import telephony_guardian_runner as T
from app.db.session import SessionLocal


QA_ACCOUNT = "__qa_mcn_acceptance_watch__"
QA_EARLY = "__qa_mcn_acceptance_early__"
QA_SWITCH = "__qa_mcn_acceptance_switch__"
QA_RETIRE = "__qa_mcn_acceptance_retire__"
QA_ACCOUNTS = (QA_ACCOUNT, QA_EARLY, QA_SWITCH, QA_RETIRE)


def readiness_all_true():
    gates = {
        "provider_selected": True,
        "provider_connected": True,
        "device_online": True,
        "real_provider_call_seen": True,
        "real_inbound_seen": True,
        "real_outbound_seen": True,
        "real_answer_seen": True,
        "real_call_completed": True,
        "real_media_provider": True,
        "real_hold_completed": True,
        "real_resume_completed": True,
        "real_crm_linked": True,
        "real_cleanup_completed": True,
        "mcn_carrier_linked": True,
        "recording_received": True,
        "transcription_completed": True,
        "ai_analysis_completed": True,
        "real_inbound_e2e_chain": True,
        "real_outbound_lifecycle": True,
    }
    return {
        "telephony_readiness_pct": 100.0,
        "telephony_gates": gates,
        "media_diagnostics": {"turn_runtime": {"runtime_ready": True}},
        "mcn_readiness": {"ready": True},
        "mcn_transport": {"ready": True},
        "mcn_network": {"ready": True},
        "provider_evidence": {"provider": "mcn", "status": "connected", "last_health_status": "ok"},
        "primary_next_action": {
            "code": "ready",
            "actor": "none",
            "owner_action_required": False,
            "text": "Полный телефонный E2E подтверждён реальными событиями.",
        },
    }


class RealMcnAcceptanceWatchTests(unittest.TestCase):
    def tearDown(self):
        db = SessionLocal()
        try:
            for account_id in QA_ACCOUNTS:
                db.execute(text("DELETE FROM storage WHERE account_id=:a AND key=:k"), {
                    "a": account_id,
                    "k": T._MCN_ACCEPTANCE_STORAGE_KEY,
                })
                db.execute(text("DELETE FROM telephony_dids WHERE account_id=:a"), {"a": account_id})
                db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"), {"a": account_id})
                db.execute(text("DELETE FROM telephony_provider_configs WHERE account_id=:a"), {"a": account_id})
                db.execute(text("DELETE FROM telephony_manager_actions WHERE account_id=:a"), {"a": account_id})
            db.commit()
        finally:
            db.close()

    def test_no_real_account_is_wait_not_incident(self):
        with patch.object(T, "_real_mcn_accounts", return_value=[]):
            out = T.real_mcn_acceptance_watch_once()
        self.assertEqual(out["status"], "waiting_external_inputs")
        self.assertEqual(out["real_accounts"], 0)
        self.assertFalse(out["owner_action_required"])

    def test_verified_requires_all_real_evidence_checks(self):
        payload = T._mcn_acceptance_payload("client-a", readiness_all_true())
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["remaining_codes"], [])
        self.assertFalse(payload["owner_action_required"])

    def test_owner_action_is_preserved_only_when_evidence_incomplete(self):
        r = readiness_all_true()
        r["telephony_gates"]["real_inbound_seen"] = False
        r["primary_next_action"] = {
            "code": "real_inbound_test",
            "actor": "owner",
            "owner_action_required": True,
            "text": "Сделайте один реальный входящий звонок.",
        }
        payload = T._mcn_acceptance_payload("client-a", r)
        self.assertEqual(payload["status"], "owner_action")
        self.assertIn("real_inbound_seen", payload["remaining_codes"])
        self.assertTrue(payload["owner_action_required"])

    def test_all_real_accounts_are_processed_without_guessing(self):
        ready = readiness_all_true()
        waiting = readiness_all_true()
        waiting["telephony_gates"]["real_outbound_lifecycle"] = False
        waiting["primary_next_action"] = {
            "code": "real_outbound_test",
            "actor": "manager",
            "owner_action_required": False,
            "text": "Сделайте один реальный исходящий звонок.",
        }
        with patch.object(T, "_real_mcn_accounts", return_value=["client-a", "client-b"]), \
             patch.object(T, "phone_client_readiness", side_effect=[ready, waiting]), \
             patch.object(T, "_persist_mcn_acceptance", return_value=False), \
             patch.object(T, "reconcile_phone_manager_action", side_effect=[
                 {"status": "no_manager_action"},
                 {"status": "created", "action": {"id": 777, "action_code": "real_outbound_test", "assigned_user_id": None}},
             ]):
            out = T.real_mcn_acceptance_watch_once()
        self.assertEqual(out["real_accounts"], 2)
        self.assertEqual(out["status"], "waiting_evidence")
        self.assertEqual(out["verified"], 1)
        self.assertEqual(out["waiting_evidence"], 1)
        self.assertEqual(out["owner_action_accounts"], 0)
        self.assertEqual(out["manager_action_accounts"], 1)
        self.assertEqual(out["boris_action_accounts"], 0)
        self.assertEqual([x["account_id"] for x in out["results"]], ["client-a", "client-b"])

    def test_watcher_persists_manager_step_without_duplicate_crm_or_push(self):
        waiting = readiness_all_true()
        waiting["telephony_gates"]["real_outbound_lifecycle"] = False
        waiting["primary_next_action"] = {
            "code": "real_outbound_test",
            "actor": "manager",
            "owner_action_required": False,
            "text": "Сделайте один реальный исходящий звонок.",
        }
        with patch.object(T, "_real_mcn_accounts", return_value=[QA_ACCOUNT]), \
             patch.object(T, "phone_client_readiness", return_value=waiting), \
             patch.object(T, "_persist_mcn_acceptance", return_value=False):
            out = T.real_mcn_acceptance_watch_once(include_synthetic=True)
        self.assertEqual(out["manager_action_accounts"], 1)
        self.assertEqual(out["results"][0]["manager_action"]["action_code"], "real_outbound_test")
        db = SessionLocal()
        try:
            rows = db.execute(text("""
                SELECT action_code,status,metadata_json
                FROM telephony_manager_actions
                WHERE account_id=:a ORDER BY id
            """), {"a": QA_ACCOUNT}).mappings().all()
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action_code"], "real_outbound_test")
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["metadata_json"]["source"], "mcn_acceptance_watcher")

    def test_provider_selection_without_trunk_is_discovered_early(self):
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO telephony_provider_configs(
                    account_id,provider,status,public_config_json
                ) VALUES(:a,'mcn','credentials_required','{}'::jsonb)
                ON CONFLICT(account_id) DO UPDATE SET
                    provider='mcn',status='credentials_required',
                    credentials_enc=NULL,public_config_json='{}'::jsonb,
                    updated_at=now()
            """), {"a": QA_EARLY})
            db.commit()
        finally:
            db.close()

        self.assertNotIn(QA_EARLY, T._real_mcn_accounts())
        self.assertIn(QA_EARLY, T._real_mcn_accounts(include_synthetic=True))

        waiting = readiness_all_true()
        waiting["telephony_gates"]["provider_connected"] = False
        waiting["mcn_readiness"] = {
            "ready": False,
            "blockers": ["mcn_trunk_missing", "mcn_active_did_missing"],
        }
        waiting["mcn_transport"] = {"ready": False}
        waiting["primary_next_action"] = {
            "code": "mcn_trunk_credentials",
            "actor": "owner",
            "owner_action_required": True,
            "text": "Добавьте данные SIP-линии.",
        }
        with patch.object(T, "_real_mcn_accounts", return_value=[QA_EARLY]), \
             patch.object(T, "phone_client_readiness", return_value=waiting) as read, \
             patch.object(T, "_persist_mcn_acceptance", return_value=False):
            out = T.real_mcn_acceptance_watch_once(include_synthetic=True)
        self.assertEqual(out["real_accounts"], 1)
        self.assertEqual(out["status"], "owner_action")
        self.assertEqual(out["owner_action_accounts"], 1)
        self.assertEqual(out["manager_action_accounts"], 0)
        self.assertEqual(out["boris_action_accounts"], 0)
        read.assert_called_once_with(QA_EARLY)

    def test_explicit_other_provider_suppresses_orphan_mcn_trunk(self):
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO telephony_provider_configs(
                    account_id,provider,status,public_config_json
                ) VALUES(:a,'telphin','credentials_required','{}'::jsonb)
                ON CONFLICT(account_id) DO UPDATE SET
                    provider='telphin',status='credentials_required',
                    credentials_enc=NULL,public_config_json='{}'::jsonb,
                    updated_at=now()
            """), {"a": QA_SWITCH})
            db.execute(text("""
                INSERT INTO telephony_trunks(
                    account_id,provider,name,auth_mode,enabled,status
                ) VALUES(:a,'mcn','legacy-orphan','registration',true,'waiting_mcn')
                ON CONFLICT(account_id,provider,name) DO UPDATE SET
                    enabled=true,status='waiting_mcn',updated_at=now()
            """), {"a": QA_SWITCH})
            db.commit()
        finally:
            db.close()

        self.assertNotIn(QA_SWITCH, T._real_mcn_accounts(include_synthetic=True))

    def test_stale_manager_action_is_cancelled_when_mcn_stops_being_current(self):
        T.reconcile_phone_manager_action(
            QA_RETIRE,
            {
                "primary_next_action": {
                    "code": "real_inbound_test",
                    "actor": "manager",
                    "owner_action_required": False,
                    "text": "Сделайте входящий звонок.",
                },
                "telephony_gates": {},
            },
            include_synthetic=True,
        )
        out = T._retire_stale_mcn_manager_actions(set(), include_synthetic=True)
        self.assertEqual(out["cancelled"], 1)
        self.assertEqual(out["errors"], 0)
        db = SessionLocal()
        try:
            row = db.execute(text("""
                SELECT status,resolution
                FROM telephony_manager_actions
                WHERE account_id=:a ORDER BY id DESC LIMIT 1
            """), {"a": QA_RETIRE}).mappings().one()
        finally:
            db.close()
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["resolution"], "mcn_not_current")

    def test_stale_runtime_is_retired_not_left_verified(self):
        payload = T._mcn_acceptance_payload(QA_RETIRE, readiness_all_true())
        self.assertTrue(T._persist_mcn_acceptance(payload))

        retired = T._retire_stale_mcn_acceptance(set(), include_synthetic=True)
        self.assertEqual(retired, 1)

        db = SessionLocal()
        try:
            raw = db.execute(text("""
                SELECT value FROM storage
                WHERE account_id=:a AND key=:k
                ORDER BY id DESC LIMIT 1
            """), {"a": QA_RETIRE, "k": T._MCN_ACCEPTANCE_STORAGE_KEY}).scalar()
        finally:
            db.close()
        state = __import__("json").loads(raw)
        self.assertEqual(state["status"], "retired")
        self.assertEqual(state["previous_status"], "verified")
        self.assertEqual(state["primary_next_action"]["code"], "mcn_not_configured")
        self.assertFalse(state["owner_action_required"])
        self.assertEqual(
            T._retire_stale_mcn_acceptance(set(), include_synthetic=True),
            0,
        )

    def test_persistence_is_idempotent_inside_refresh_window(self):
        payload = T._mcn_acceptance_payload(QA_ACCOUNT, readiness_all_true())
        self.assertTrue(T._persist_mcn_acceptance(payload))
        payload2 = T._mcn_acceptance_payload(QA_ACCOUNT, readiness_all_true())
        self.assertFalse(T._persist_mcn_acceptance(payload2))
        db = SessionLocal()
        try:
            n = int(db.execute(text(
                "SELECT count(*) FROM storage WHERE account_id=:a AND key=:k"
            ), {"a": QA_ACCOUNT, "k": T._MCN_ACCEPTANCE_STORAGE_KEY}).scalar() or 0)
        finally:
            db.close()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
