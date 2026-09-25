import unittest
from app.services.control_plane_adapters import _social_diagnose

class SocialControlPlaneSlaTruthTest(unittest.TestCase):
    def test_delivered_but_late_is_not_flow_ok(self):
        a={"active_projects":3,"scheduler_age_sec":1,"scheduler_state":"ok","scheduler_details":{"contract_ok":True,"ocean_daily_delivery":{"project_name":"Ocean","sla_breach_count":1,"late_due":1,"missing_due":0,"status":"on_track_with_sla_breach"},"dushi_daily_delivery":{"sla_breach_count":0}}}
        d=_social_diagnose(None,None,None,a)
        self.assertEqual(d["classification"],"SOCIAL_DELIVERY_SLA_BREACH")
        self.assertEqual(d["health"],"degraded")
        self.assertFalse(d["owner_action_required"])
    def test_clean_delivery_is_flow_ok(self):
        a={"active_projects":3,"scheduler_age_sec":1,"scheduler_state":"ok","scheduler_details":{"contract_ok":True,"ocean_daily_delivery":{"sla_breach_count":0},"dushi_daily_delivery":{"sla_breach_count":0}}}
        self.assertEqual(_social_diagnose(None,None,None,a)["classification"],"SOCIAL_FLOW_OK")
    def test_client_dependency_does_not_hide_runtime_degraded(self):
        a={"active_projects":3,"scheduler_age_sec":1,"scheduler_state":"degraded","scheduler_details":{"contract_ok":True,"client_dependencies_waiting":True,"client_dependencies_count":1}}
        self.assertEqual(_social_diagnose(None,None,None,a)["classification"],"SOCIAL_FLOW_OK_WITH_CLIENT_DEPENDENCIES")
if __name__=='__main__': unittest.main()
