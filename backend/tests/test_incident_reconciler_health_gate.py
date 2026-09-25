import unittest

from app.services.incident_reconciler import _resolution_decision
from app.services.control_plane_adapters import _social_diagnose


class IncidentReconcilerHealthGateTests(unittest.TestCase):
    def test_degraded_business_incident_stays_open_while_still_degraded(self):
        ok,reason=_resolution_decision("BUSINESS_STATE_DEGRADED",{
            "diagnosis":{"classification":"CALL_BACKLOG","health":"degraded"},
            "state_error":None,
        })
        self.assertFalse(ok)
        self.assertEqual(reason,"diagnosis_still_degraded")

    def test_blocked_business_incident_stays_open_while_still_blocked(self):
        ok,reason=_resolution_decision("BUSINESS_STATE_DEGRADED",{
            "diagnosis":{"classification":"BUDGET_GUARD_BLOCKED","health":"blocked"},
            "state_error":None,
        })
        self.assertFalse(ok)
        self.assertEqual(reason,"diagnosis_still_blocked")

    def test_read_failure_can_close_after_read_recovers_even_if_business_is_degraded(self):
        ok,reason=_resolution_decision("BUSINESS_STATE_READ_FAILED",{
            "diagnosis":{"classification":"CALL_BACKLOG","health":"degraded"},
            "state_error":None,
        })
        self.assertTrue(ok)
        self.assertEqual(reason,"fresh_readback_verified")

    def test_external_wait_resolves_old_internal_degraded_incident(self):
        ok,reason=_resolution_decision("BUSINESS_STATE_DEGRADED",{
            "diagnosis":{
                "classification":"SOCIAL_EXTERNAL_CAPTCHA_WAIT",
                "health":"waiting_external",
                "dependency_state":"waiting_external",
            },
            "state_error":None,
        })
        self.assertTrue(ok)
        self.assertEqual(reason,"business_postcondition_recovered")

    def test_missing_or_failed_diagnosis_never_closes_incident(self):
        self.assertEqual(_resolution_decision("BUSINESS_STATE_DEGRADED",{}),(False,"state_unavailable"))
        self.assertEqual(
            _resolution_decision("BUSINESS_STATE_DEGRADED",{
                "diagnosis":{"classification":"BUSINESS_DIAGNOSIS_FAILED","health":"critical"},
            }),
            (False,"diagnosis_unavailable"),
        )

    def test_vk_captcha_is_external_wait_not_scheduler_failure(self):
        d=_social_diagnose(None,None,None,{
            "active_projects":4,
            "scheduler_age_sec":20,
            "scheduler_state":"degraded",
            "scheduler_details":{
                "contract_ok":True,
                "ocean_vk_cleanup":{
                    "status":"captcha",
                    "code":14,
                    "post_id":7314,
                },
            },
        })
        self.assertEqual(d["classification"],"SOCIAL_EXTERNAL_CAPTCHA_WAIT")
        self.assertEqual(d["health"],"waiting_external")
        self.assertEqual(d["dependency_state"],"waiting_external")
        self.assertFalse(d["owner_action_required"])
        self.assertIsNone(d["safe_action"])

    def test_stale_social_scheduler_still_wins_over_external_captcha(self):
        d=_social_diagnose(None,None,None,{
            "active_projects":4,
            "scheduler_age_sec":181,
            "scheduler_state":"degraded",
            "scheduler_details":{
                "ocean_vk_cleanup":{"status":"captcha"},
            },
        })
        self.assertEqual(d["classification"],"SOCIAL_SCHEDULER_STALLED")
        self.assertEqual(d["health"],"degraded")
        self.assertEqual(d["safe_action"],"restart_social_scheduler")


if __name__=="__main__":
    unittest.main()
