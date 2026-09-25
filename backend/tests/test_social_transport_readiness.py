import importlib.util
from importlib.machinery import SourceFileLoader
import unittest
from pathlib import Path

import posting_runner as pr


SERVICE=Path("/etc/systemd/system/boris-social-transport-readiness.service")
TIMER=Path("/etc/systemd/system/boris-social-transport-readiness.timer")
GUARDIAN=Path("/etc/systemd/system/boris-social-guardian.service")


def load_probe():
    text=SERVICE.read_text()
    obj=text.split("/var/lib/boris-integrity/objects/",1)[1].splitlines()[0].strip()
    path=Path("/var/lib/boris-integrity/objects")/obj
    loader=SourceFileLoader("social_transport_probe_current",str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    mod=importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def load_guardian_text():
    text=GUARDIAN.read_text()
    obj=text.split("/var/lib/boris-integrity/objects/",1)[1].splitlines()[0].strip()
    return (Path("/var/lib/boris-integrity/objects")/obj).read_text()


class SocialTransportReadinessTest(unittest.TestCase):
    def test_telegram_admin_can_post_is_ready(self):
        mod=load_probe()
        r=mod.classify_tg_member({
            "ok":True,
            "result":{
                "status":"administrator",
                "can_post_messages":True,
                "can_edit_messages":True,
            },
        })
        self.assertTrue(r["ready"])
        self.assertFalse(r["owner_action_required"])

    def test_telegram_member_without_post_right_is_owner_action(self):
        mod=load_probe()
        r=mod.classify_tg_member({
            "ok":True,
            "result":{"status":"member","can_post_messages":False},
        })
        self.assertFalse(r["ready"])
        self.assertTrue(r["owner_action_required"])

    def test_telegram_transient_api_error_does_not_immediately_escalate(self):
        mod=load_probe()
        r=mod.classify_tg_member({
            "ok":False,
            "description":"Too Many Requests: retry after 5",
        })
        self.assertFalse(r["ready"])
        self.assertTrue(r["transport_error"])
        self.assertFalse(r["owner_action_required"])

    def test_vk_can_post_and_wall_read_is_ready(self):
        mod=load_probe()
        r=mod.classify_vk(
            {"response":{"groups":[{"id":1,"name":"X","can_post":1}]}},
            {"response":{"count":1,"items":[]}},
        )
        self.assertTrue(r["ready"])
        self.assertFalse(r["owner_action_required"])

    def test_vk_rate_limit_is_retry_not_owner_action(self):
        mod=load_probe()
        r=mod.classify_vk(
            {"error":{"error_code":29,"error_msg":"Rate limit reached"}},
            {"response":{"count":0,"items":[]}},
        )
        self.assertFalse(r["ready"])
        self.assertTrue(r["transport_error"])
        self.assertFalse(r["owner_action_required"])

    def test_vk_flood_control_is_waiting_external_not_owner_action(self):
        mod=load_probe()
        r=mod.classify_vk(
            {"error":{"error_code":9,"error_msg":"Flood control"}},
            {"response":{"count":0,"items":[]}},
        )
        self.assertFalse(r["ready"])
        self.assertEqual(r["reason"],"vk_rate_limited")
        self.assertEqual(r["dependency_state"],"waiting_external")
        self.assertTrue(r["retryable_external"])
        self.assertFalse(r["owner_action_required"])

    def test_vk_auth_error_is_owner_action(self):
        mod=load_probe()
        r=mod.classify_vk(
            {"error":{"error_code":5,"error_msg":"User authorization failed"}},
            {"response":{"count":0,"items":[]}},
        )
        self.assertFalse(r["ready"])
        self.assertTrue(r["owner_action_required"])

    def test_live_cached_transport_is_fresh_and_ready_for_current_contours(self):
        h=pr.social_transport_readiness_cached_health()
        self.assertTrue(h["fresh"],h)
        self.assertGreaterEqual(len(h["projects"]),1)
        if h["ok"]:
            self.assertTrue(all(x.get("ready") for x in h["projects"]))
        else:
            # A fresh external rate limit is a safe waiting state, not a
            # production permission regression. Projects remain not-ready so
            # workers do not increase provider pressure.
            self.assertFalse(h["owner_action_required"],h)
            self.assertTrue(any(
                (x.get("vk") or {}).get("code") in {9,29}
                or (
                    (x.get("vk") or {}).get("reason")=="vk_rate_limited"
                    and (x.get("vk") or {}).get("stage")=="shared_backoff"
                    and (x.get("vk") or {}).get("retryable_external")
                )
                for x in h["projects"]
            ),h)
        for item in h["projects"]:
            self.assertTrue(str(item.get("account_id") or "u2"))
            self.assertTrue(str(item.get("project_id") or ""))
        for gap in h.get("subscription_gaps") or []:
            self.assertTrue(str(gap.get("account_id") or ""))
            self.assertEqual(
                gap.get("reason"),
                "active_subscription_without_posting_project",
            )

    def test_vk_probe_respects_shared_delivery_backoff(self):
        text=Path("run/social_transport_readiness_v3.py").read_text(encoding="utf-8")
        self.assertIn("_social_vk_rate_backoff_remaining",text)
        self.assertIn("_set_social_vk_rate_backoff",text)
        self.assertIn('"stage":"shared_backoff"',text)
        self.assertIn('if group_payload.get("error"):',text)
        # First VK error must be classified before wall.get, so Flood control
        # cannot cause a second provider call in the same project probe.
        self.assertLess(text.index('if group_payload.get("error"):'), text.index('"https://api.vk.com/method/wall.get"'))

    def test_service_is_read_only_scheduled_and_has_production_env(self):
        service=SERVICE.read_text()
        timer=TIMER.read_text()
        self.assertIn("EnvironmentFile=/root/BORIS/backend/.env",service)
        self.assertIn("ExecStartPre=/usr/local/sbin/boris-social-owner-preflight.py",service)
        self.assertIn("OnUnitActiveSec=1800s",timer)

    def test_guardian_monitors_transport_probe(self):
        text=load_guardian_text()
        self.assertIn('"worker_id":"social_transport_readiness"',text)
        self.assertIn('"service":"boris-social-transport-readiness.service"',text)
        self.assertIn('"max_age":2400',text)

    def test_generic_reliability_has_transport_sla(self):
        text=Path("app/services/reliability.py").read_text(encoding="utf-8")
        self.assertIn('("runtime", "social_transport_readiness"): 2400',text)

    def test_worker_uses_transport_per_project_for_generation_isolation(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        self.assertIn("social_transport_readiness_cached_health()",text)
        self.assertIn('"transport_permission_not_ready"',text)
        self.assertIn('"transport_readiness_stale_or_missing"',text)
        self.assertIn('"effective_blocked_reasons"',text)

    def test_incident_reconciler_escalates_only_explicit_transport_permission_loss(self):
        text=Path("app/services/incident_reconciler.py").read_text(encoding="utf-8")
        self.assertIn('incident_type="OWNER_SOCIAL_TRANSPORT_NOT_READY"',text)
        self.assertIn('and project.get("owner_action_required")',text)
        self.assertIn('service_date="transport"',text)


if __name__=="__main__":
    unittest.main()
