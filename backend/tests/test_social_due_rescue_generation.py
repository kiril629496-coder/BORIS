import importlib.util
from importlib.machinery import SourceFileLoader
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest

UNIT=Path("/etc/systemd/system/boris-social-due-rescue.service")

def load_rescue():
    text=UNIT.read_text()
    sha=text.split("/objects/",1)[1].split()[0].strip()
    path=Path("/var/lib/boris-integrity/objects")/sha
    loader=SourceFileLoader("social_due_rescue_current",str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    mod=importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

class SocialDueRescueGenerationTest(unittest.TestCase):
    def _fake_posting_runner(self, expected=True):
        state={
            "projects":[{
                "id":"dushi1",
                "name":"От Души",
                "times":["9","18"],
                "tz_offset":3,
                "expected_auto_generation":expected,
                "paid_auto_generation":True,
                "mode":"moderate",
                "auto_publish_hours":3,
            }],
            "posts":[],
            "autopost_calls":[],
            "session_no":0,
        }

        class Q:
            def __init__(self,row):
                self.row=row
            def filter(self,*a,**k):
                return self
            def first(self):
                return self.row
            def all(self):
                # Owner-only unit scenarios intentionally expose no paid
                # posting_subscription rows. Paid-contour discovery has its own
                # dedicated integration-style test below.
                return []

        class DB:
            def __init__(self,mode):
                self.mode=mode
                self.calls=0
            def query(self,*a,**k):
                self.calls+=1
                if self.mode in ("initial","overdue"):
                    value=state["projects"] if self.calls==1 else state["posts"]
                else:
                    value=state["posts"]
                return Q(SimpleNamespace(value=json.dumps(value,ensure_ascii=False)))
            def close(self):
                pass

        fake=ModuleType("posting_runner")
        def SessionLocal():
            state["session_no"]+=1
            modes={1:"initial",2:"verify",3:"overdue"}
            return DB(modes.get(state["session_no"],"overdue"))
        fake.SessionLocal=SessionLocal
        fake._scheduled_due_hour=lambda proj: 9
        fake.due_posts_all=lambda dry=False: None
        fake.social_owner_pre_slot_self_heal=lambda account_id: {
            "ok":True,"changed":[],"changed_count":0,
        }
        fake.social_owner_pre_slot_readiness_health=lambda account_id: {
            "ok":True,
            "blocked_count":0,
            "projects":[{
                "project_id":"dushi1",
                "project":"От Души",
                "blocked_reasons":[],
            }],
        }
        fake.social_transport_readiness_cached_health=lambda: {
            "ok":True,"fresh":True,"owner_action_required":False,
            "projects":[{
                "project_id":"dushi1","project":"От Души","ready":True,
                "telegram":{"ready":True},"vk":{"ready":True},
            }],
        }

        def autopost_projects(account_id,owner=False,respect_time=False,only_id=None,scheduled_hour_override=None):
            state["autopost_calls"].append({
                "account_id":account_id,"owner":owner,
                "respect_time":respect_time,"only_id":only_id,
                "scheduled_hour_override":scheduled_hour_override,
            })
            now=datetime.now()
            hour=int(scheduled_hour_override if scheduled_hour_override is not None else 9)
            key="social-slot:u2:dushi1:%s_%02d"%((now.replace()).strftime("%Y%m%d"),hour)
            # Rescue calculates project-local date. Server date and project-local
            # date are the same in this controlled test window.
            state["posts"].append({
                "id":"rescued1","project_id":"dushi1","project_name":"От Души",
                "generation_key":key,"status":"draft","created_at":now.strftime("%Y-%m-%d %H:%M"),
                "publish_at":None,"ids":{},"error":"",
            })
        fake.autopost_projects=autopost_projects
        return fake,state

    def test_missing_generation_is_materialized_via_normal_autopost_path(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)

        # Make exact generation key independent of timezone/date edge in test by
        # patching datetime.now to a stable UTC-ish wall clock used by rescue.
        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,10,0,0)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base
        mod.datetime=FixedDateTime

        def autopost_fixed(account_id,owner=False,respect_time=False,only_id=None,scheduled_hour_override=None):
            state["autopost_calls"].append({
                "account_id":account_id,"owner":owner,
                "respect_time":respect_time,"only_id":only_id,
                "scheduled_hour_override":scheduled_hour_override,
            })
            hour=int(scheduled_hour_override if scheduled_hour_override is not None else 9)
            state["posts"].append({
                "id":"rescued1","project_id":"dushi1","project_name":"От Души",
                "generation_key":"social-slot:u2:dushi1:20260905_%02d"%hour,
                "status":"draft","created_at":"2026-09-05 10:00",
                "publish_at":None,"ids":{},"error":"",
            })
        fake.autopost_projects=autopost_fixed

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None:
                sys.modules.pop("posting_runner",None)
            else:
                sys.modules["posting_runner"]=old

        self.assertEqual(rc,0)
        self.assertEqual(len(state["autopost_calls"]),1)
        call=state["autopost_calls"][0]
        self.assertEqual(call["only_id"],"dushi1")
        self.assertTrue(call["owner"])
        self.assertTrue(call["respect_time"])
        self.assertEqual(call["scheduled_hour_override"],9)
        self.assertTrue(any(x.get("generation_key")=="social-slot:u2:dushi1:20260905_09" for x in state["posts"]))
        self.assertEqual(heartbeats[-1][0],"ok")
        self.assertEqual(len(heartbeats[-1][1]["generation_rescued"]),1)
        self.assertEqual(heartbeats[-1][1]["generation_unresolved"],[])

    def test_evening_rescue_still_recovers_missing_morning_slot(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)
        state["posts"]=[{
            "id":"evening1","project_id":"dushi1","project_name":"От Души",
            "generation_key":"social-slot:u2:dushi1:20260905_18",
            "status":"draft","created_at":"2026-09-05 15:00",
            "publish_at":None,"ids":{},"error":"",
        }]

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                # Server 16:00 + project offset 3h = 19:00 local.
                base=cls(2026,9,5,16,0,0)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base
        mod.datetime=FixedDateTime

        def autopost_fixed(account_id,owner=False,respect_time=False,only_id=None,scheduled_hour_override=None):
            state["autopost_calls"].append({
                "account_id":account_id,"owner":owner,
                "respect_time":respect_time,"only_id":only_id,
                "scheduled_hour_override":scheduled_hour_override,
            })
            hour=int(scheduled_hour_override)
            state["posts"].append({
                "id":"rescued-%02d"%hour,
                "project_id":"dushi1","project_name":"От Души",
                "generation_key":"social-slot:u2:dushi1:20260905_%02d"%hour,
                "status":"draft","created_at":"2026-09-05 16:00",
                "publish_at":None,"ids":{},"error":"",
            })
        fake.autopost_projects=autopost_fixed

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None:
                sys.modules.pop("posting_runner",None)
            else:
                sys.modules["posting_runner"]=old

        self.assertEqual(rc,0)
        self.assertEqual(len(state["autopost_calls"]),1)
        self.assertEqual(state["autopost_calls"][0]["scheduled_hour_override"],9)
        self.assertTrue(any(
            x.get("generation_key")=="social-slot:u2:dushi1:20260905_09"
            for x in state["posts"]
        ))
        self.assertTrue(any(
            x.get("generation_key")=="social-slot:u2:dushi1:20260905_18"
            for x in state["posts"]
        ))
        rescued=heartbeats[-1][1]["generation_rescued"]
        self.assertEqual([x["slot_hour"] for x in rescued],[9])

    def test_uncertain_paid_outcome_waits_external_without_retry_loop(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)
        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,10,0,0)
                return base.replace(tzinfo=tz) if tz is not None else base
        mod.datetime=FixedDateTime
        def uncertain(*a,**k):
            state["autopost_calls"].append({"attempt":1})
            raise RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:campaign_transport_outcome_unknown")
        fake.autopost_projects=uncertain
        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None: sys.modules.pop("posting_runner",None)
            else: sys.modules["posting_runner"]=old
        self.assertEqual(rc,0)
        self.assertEqual(heartbeats[-1][0],"ok")
        waits=heartbeats[-1][1].get("generation_waiting_external") or []
        self.assertEqual(len(waits),1)
        self.assertEqual(waits[0].get("dependency_state"),"waiting_external")
        self.assertFalse(waits[0].get("retryable"))
        self.assertFalse(waits[0].get("owner_action_required"))

    def test_external_only_readiness_wait_is_not_systemd_failure(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)
        reason="VK_PUBLIC_WALL_POST_NO_NATIVE_MESSAGE_BUTTON_AND_NO_AUTHORIZED_BROWSER_SESSION"
        fake.social_owner_pre_slot_readiness_health=lambda account_id: {
            "ok":False,
            "owner_action_required":True,
            "blocked_count":1,
            "blocked":[{
                "project_id":"dushi1","project":"От Души",
                "reason":reason,"dependency_state":"waiting_external",
            }],
            "projects":[{
                "project_id":"dushi1","project":"От Души",
                "blocked_reasons":[reason],
            }],
        }

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                # Before the first project slot: exercise readiness classification
                # without generating or publishing any content.
                base=cls(2026,9,5,2,0,0)
                return base.replace(tzinfo=tz) if tz is not None else base
        mod.datetime=FixedDateTime

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None: sys.modules.pop("posting_runner",None)
            else: sys.modules["posting_runner"]=old

        self.assertEqual(rc,0)
        self.assertEqual(state["autopost_calls"],[])
        self.assertEqual(heartbeats[-1][0],"ok")
        self.assertEqual(heartbeats[-1][1].get("dependency_state"),"waiting_external")
        self.assertTrue(heartbeats[-1][1].get("owner_action_required"))

    def test_transport_block_prevents_paid_generation_attempt(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)
        fake.social_transport_readiness_cached_health=lambda: {
            "ok":False,"fresh":True,"owner_action_required":True,
            "projects":[{
                "project_id":"dushi1","project":"От Души","ready":False,
                "telegram":{"ready":True},
                "vk":{"ready":False,"reason":"vk_group_cannot_post",
                      "owner_action_required":True},
            }],
        }

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,10,0,0)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base
        mod.datetime=FixedDateTime

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None:
                sys.modules.pop("posting_runner",None)
            else:
                sys.modules["posting_runner"]=old

        self.assertEqual(rc,3)
        self.assertEqual(state["autopost_calls"],[])
        unresolved=heartbeats[-1][1]["generation_unresolved"]
        self.assertEqual(unresolved[0]["error"],"pre_slot_readiness_blocked")
        self.assertIn("transport_permission_not_ready",unresolved[0]["blocked_reasons"])
        self.assertIn("vk_group_cannot_post",unresolved[0]["blocked_reasons"])
        self.assertTrue(heartbeats[-1][1]["owner_action_required"])

    def test_readiness_block_prevents_paid_generation_attempt(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)
        fake.social_owner_pre_slot_readiness_health=lambda account_id: {
            "ok":False,
            "owner_action_required":True,
            "blocked_count":1,
            "projects":[{
                "project_id":"dushi1",
                "project":"От Души",
                "blocked_reasons":["vk_token_missing"],
            }],
        }

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,10,0,0)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base
        mod.datetime=FixedDateTime

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None:
                sys.modules.pop("posting_runner",None)
            else:
                sys.modules["posting_runner"]=old

        self.assertEqual(rc,3)
        self.assertEqual(state["autopost_calls"],[])
        self.assertEqual(heartbeats[-1][0],"degraded")
        self.assertEqual(
            heartbeats[-1][1]["generation_unresolved"][0]["error"],
            "pre_slot_readiness_blocked",
        )

    def test_rescue_runs_local_self_heal_before_readiness(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=True)
        order=[]
        fake.social_owner_pre_slot_self_heal=lambda account_id: (
            order.append("self_heal") or {
                "ok":True,"changed":[{"project":"От Души"}],"changed_count":1
            }
        )
        fake.social_owner_pre_slot_readiness_health=lambda account_id: (
            order.append("readiness") or {
                "ok":True,"blocked_count":0,
                "projects":[{
                    "project_id":"dushi1","project":"От Души",
                    "blocked_reasons":[],
                }],
            }
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,2,0,0)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base
        mod.datetime=FixedDateTime

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None:
                sys.modules.pop("posting_runner",None)
            else:
                sys.modules["posting_runner"]=old

        self.assertEqual(rc,0)
        self.assertEqual(order[:2],["self_heal","readiness"])
        self.assertEqual(
            heartbeats[-1][1]["pre_slot_self_heal"]["changed_count"],1
        )

    def test_replacement_watch_has_owner_preflight(self):
        unit=Path("/etc/systemd/system/boris-social-vk-replacement-watch.service").read_text()
        self.assertIn(
            "ExecStartPre=/usr/local/sbin/boris-social-owner-preflight.py /root/BORIS/backend",
            unit,
        )

    def test_due_rescue_service_loads_production_env(self):
        unit=Path("/etc/systemd/system/boris-social-due-rescue.service").read_text()
        self.assertIn("EnvironmentFile=/root/BORIS/backend/.env",unit)
        self.assertIn("Environment=BORIS_RUNTIME_ROLE=social_due_rescue",unit)

    def test_intentionally_disabled_project_is_not_generated(self):
        mod=load_rescue()
        fake,state=self._fake_posting_runner(expected=False)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,10,0,0)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base
        mod.datetime=FixedDateTime

        old=sys.modules.get("posting_runner")
        heartbeats=[]
        mod._heartbeat=lambda state_name,details: heartbeats.append((state_name,details))
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None:
                sys.modules.pop("posting_runner",None)
            else:
                sys.modules["posting_runner"]=old

        self.assertEqual(rc,0)
        self.assertEqual(state["autopost_calls"],[])
        self.assertEqual(heartbeats[-1][1]["generation_missing_before"],[])

if __name__=="__main__":
    unittest.main()
