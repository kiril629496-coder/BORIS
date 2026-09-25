import unittest
from pathlib import Path

from app.services.control_plane_adapters import _rop_diagnose


class RopAutoSchedulerTests(unittest.TestCase):
    def test_missing_scheduler_is_degraded_for_active_rop(self):
        d=_rop_diagnose(None,"acc",None,{
            "active_rop_accounts":1,
            "scheduler_age_sec":None,
            "scheduler_state":None,
            "avito_calls_pending_analysis_24h":0,
            "calls_without_quality_audit_24h":0,
        })
        self.assertEqual(d["classification"],"ROP_AUTO_SCHEDULER_STALLED")
        self.assertEqual(d["safe_action"],"restart_rop_auto_scheduler")

    def test_recent_pending_call_waits_for_internal_scheduler_not_owner(self):
        d=_rop_diagnose(None,"acc",None,{
            "active_rop_accounts":1,
            "scheduler_age_sec":60,
            "scheduler_state":"ok",
            "avito_calls_pending_analysis_24h":1,
            "oldest_pending_analysis_age_sec":300,
            "calls_without_quality_audit_24h":0,
        })
        self.assertEqual(d["classification"],"ROP_AVITO_CALL_ANALYSIS_WAITING_SCHEDULER")
        self.assertFalse(d["owner_action_required"])
        self.assertIsNone(d["safe_action"])

    def test_old_pending_call_becomes_recoverable_backlog(self):
        d=_rop_diagnose(None,"acc",None,{
            "active_rop_accounts":1,
            "scheduler_age_sec":60,
            "scheduler_state":"ok",
            "avito_calls_pending_analysis_24h":1,
            "oldest_pending_analysis_age_sec":1800,
            "calls_without_quality_audit_24h":0,
        })
        self.assertEqual(d["classification"],"ROP_AVITO_CALL_ANALYSIS_BACKLOG")
        self.assertEqual(d["safe_action"],"restart_rop_auto_scheduler")

    def test_inactive_package_never_becomes_analysis_backlog(self):
        d=_rop_diagnose(None,"expired",None,{
            "rop_bound_accounts":1,
            "active_rop_accounts":0,
            "inactive_rop_accounts":1,
            "scheduler_age_sec":999999,
            "scheduler_state":"stale",
            "avito_calls_pending_analysis_24h":99,
            "oldest_pending_analysis_age_sec":999999,
            "calls_without_quality_audit_24h":99,
        })
        self.assertEqual(d["classification"],"ROP_NOT_ENTITLED")
        self.assertEqual(d["health"],"pass")
        self.assertFalse(d["owner_action_required"])
        self.assertIsNone(d["safe_action"])

    def test_worker_owns_heartbeat_but_business_logic_stays_in_rop_auto(self):
        src=Path("/root/BORIS/backend/rop_auto_worker.py").read_text(encoding="utf-8")
        self.assertIn('WORKER_ID = "rop_auto_scheduler"',src)
        self.assertIn('SCRIPT = "/root/BORIS/backend/rop_auto.py"',src)
        self.assertIn('heartbeat("runtime",WORKER_ID+"_inflight",state="running"',src)
        self.assertIn('heartbeat(\n        "runtime",WORKER_ID,state=state',src)
        self.assertIn('"--apply"',src)

    def test_brain_recovery_never_forces_paid_rop_service(self):
        src=Path("/root/BORIS/backend/app/services/brain_recovery.py").read_text(encoding="utf-8")
        start=src.index("def _safe_rop_scheduler_recovery")
        end=src.index("def _safe_crm_next_action_recovery",start)
        block=src[start:end]
        self.assertIn("boris-rop-auto.timer",block)
        self.assertNotIn("boris-rop-auto.service",block)
        self.assertIn("'forced_paid_analysis':False",block)


if __name__=="__main__":
    unittest.main()
