import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
import unittest

UNIT=Path("/etc/systemd/system/boris-social-guardian.service")

def load_guardian():
    text=UNIT.read_text()
    sha=text.split("/objects/",1)[1].split()[0].strip()
    path=Path("/var/lib/boris-integrity/objects")/sha
    loader=SourceFileLoader("social_guardian_current",str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    mod=importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

class SocialGuardianLivenessTest(unittest.TestCase):
    def test_business_degraded_does_not_restart_live_worker(self):
        mod=load_guardian()
        mod.AUX=[]
        calls=[]
        written=[]

        sched={
            "exists":True,
            "age":0,
            "state":"degraded",
            "details":{
                "daily_delivery_self_heal_active":True,
                "owner_action_required":False,
            },
            "last_seen_at":"2026-09-04T20:00:00+00:00",
        }
        mod._active=lambda unit: True
        mod._heartbeat=lambda worker_id: dict(sched)
        def fake_run(cmd,timeout=30):
            calls.append(list(cmd))
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        mod._run=fake_run
        mod._write_heartbeat=lambda state,details: written.append((state,details))

        rc=mod.main()
        self.assertEqual(rc,0)
        restarts=[x for x in calls if "restart" in x]
        self.assertEqual(restarts,[])
        self.assertEqual(written[-1][0],"ok")
        scheduler=written[-1][1]["scheduler"]
        self.assertEqual(scheduler["observed_state"],"degraded")
        self.assertFalse(scheduler["runtime_fault"])
        self.assertFalse(scheduler["restart_needed"])
        self.assertTrue(scheduler["healthy"])

    def test_external_wait_survives_integrity_race_without_owner_escalation(self):
        mod=load_guardian()
        mod.AUX=[{
            "worker_id":"social_due_rescue",
            "timer":"boris-social-due-rescue.timer",
            "service":"boris-social-due-rescue.service",
            "max_age":360,
        }]
        written=[]
        authority_attempts={"n":0}
        sched={
            "exists":True,"age":0,"state":"ok","details":{},
            "last_seen_at":"2026-09-08T21:00:00+00:00",
        }
        external={
            "exists":True,"age":0,"state":"degraded",
            "details":{
                "dependency_state":"waiting_external",
                "owner_action_required":False,
                "reason":"vk_rate_limited",
            },
            "last_seen_at":"2026-09-08T21:00:00+00:00",
        }
        mod._active=lambda unit: True
        mod._heartbeat=lambda worker_id: dict(sched if worker_id=="social_posting_scheduler" else external)
        def fake_run(cmd,timeout=30):
            if cmd[:3]==["systemctl","start","boris-integrity-lock01.service"]:
                authority_attempts["n"]+=1
                return SimpleNamespace(returncode=1 if authority_attempts["n"]==1 else 0,stdout="",stderr="")
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        mod._run=fake_run
        mod.time.sleep=lambda _seconds: None
        mod._write_heartbeat=lambda state,details: written.append((state,details))

        rc=mod.main()
        self.assertEqual(rc,0)
        self.assertEqual(authority_attempts["n"],2)
        self.assertEqual(written[-1][1]["errors"],[])
        self.assertEqual(len(written[-1][1]["external_blocks"]),1)
        self.assertFalse(written[-1][1]["owner_action_required"])

    def test_runtime_failed_restarts_worker_and_verifies_fresh_heartbeat(self):
        mod=load_guardian()
        mod.AUX=[]
        calls=[]
        written=[]

        failed={
            "exists":True,
            "age":0,
            "state":"failed",
            "details":{"error":"runtime loop died"},
            "last_seen_at":"2026-09-04T20:00:00+00:00",
        }
        fresh={
            "exists":True,
            "age":0,
            "state":"ok",
            "details":{},
            "last_seen_at":"2026-09-04T20:00:01+00:00",
        }
        mod._active=lambda unit: True
        mod._heartbeat=lambda worker_id: dict(failed)
        mod._wait_fresh=lambda *a,**k: dict(fresh)
        def fake_run(cmd,timeout=30):
            calls.append(list(cmd))
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        mod._run=fake_run
        mod._write_heartbeat=lambda state,details: written.append((state,details))

        rc=mod.main()
        self.assertEqual(rc,0)
        self.assertTrue(any(x[:2]==["systemctl","restart"] for x in calls))
        self.assertIn("background_worker_restarted",written[-1][1]["healed"])
        self.assertEqual(written[-1][0],"ok")
        self.assertFalse(written[-1][1]["scheduler"]["runtime_fault"])

if __name__=="__main__":
    unittest.main()
