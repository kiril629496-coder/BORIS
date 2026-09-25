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
    loader=SourceFileLoader("social_due_rescue_all_active_current",str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    mod=importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class SocialDueRescueAllActiveTest(unittest.TestCase):
    def test_active_paid_client_missing_slot_is_recovered(self):
        mod=load_rescue()
        acc="u42"
        pid="paid1"
        project={
            "id":pid,"name":"Paid Client",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","auto_publish_hours":3,
            "paid_auto_generation":True,
            "expected_auto_generation":None,
            "platforms":"both","channel_tg":"@paid",
            "vk_owner_id":-123,
            "themes":["полезная тема"],
            "subscription":{
                "paid_at":"2026-09-01","period_days":30,
                "posts_per_day":3,"platforms":"both",
            },
        }
        state={"posts":[],"calls":[]}

        class Query:
            def __init__(self,kind=None):
                self.kind=kind
                self.filters=[]
            def filter(self,*args,**kwargs):
                self.filters.extend(args)
                return self
            def all(self):
                # Discovery query Storage.key=="posting_subscription"
                if self.kind=="storage":
                    return [SimpleNamespace(
                        account_id=acc,
                        key="posting_subscription",
                        value=json.dumps(project["subscription"],ensure_ascii=False),
                    )]
                return []
            def first(self):
                # We cannot introspect SQLAlchemy expressions cheaply in this
                # lightweight fake, so session call order supplies known rows.
                return None

        class DB:
            def __init__(self,no):
                self.no=no
                self.qno=0
            def query(self,*models):
                self.qno+=1
                # Rescue call pattern:
                # 1 owner projects, 2 owner posts
                # then paid discovery subscriptions/projects/posts/verify/overdue.
                return Query("storage")
            def close(self): pass
            def commit(self): pass

        # Easier deterministic DB facade through a custom Query that recognizes
        # access order globally.
        counters={"session":0,"query":0}
        class Q2:
            def filter(self,*a,**k): return self
            def all(self):
                counters["query"]+=1
                return [SimpleNamespace(
                    account_id=acc,key="posting_subscription",
                    value=json.dumps(project["subscription"],ensure_ascii=False),
                )]
            def first(self):
                counters["query"]+=1
                n=counters["query"]
                # owner projects/posts are empty; paid project lookup thereafter.
                if n in (1,2):
                    return SimpleNamespace(value="[]")
                # after .all() n=3; n=4 = paid projects
                if n==4:
                    return SimpleNamespace(value=json.dumps([project],ensure_ascii=False))
                # paid posts / verify / overdue reads
                return SimpleNamespace(value=json.dumps(state["posts"],ensure_ascii=False))
        class DB2:
            def query(self,*a,**k): return Q2()
            def close(self): pass
            def commit(self): pass

        fake=ModuleType("posting_runner")
        fake.SessionLocal=lambda: DB2()
        fake._sub_active=lambda sub: bool(sub and sub.get("paid_at"))
        fake.social_owner_pre_slot_self_heal=lambda account_id: {
            "ok":True,"changed":[],"changed_count":0
        }
        fake.social_owner_pre_slot_readiness_health=lambda account_id: {
            "ok":True,"blocked_count":0,"projects":[]
        }
        fake.social_transport_readiness_cached_health=lambda: {
            "ok":True,"fresh":True,"owner_action_required":False,
            "subscription_gaps":[],
            "projects":[{
                "account_id":acc,"project_id":pid,"project":"Paid Client",
                "ready":True,"telegram":{"ready":True},"vk":{"ready":True},
            }],
        }
        fake._scheduled_record_delivery_due_local=lambda rec,proj: None
        fake.due_posts_all=lambda dry=False: None

        def autopost_projects(account_id,owner=False,respect_time=False,only_id=None,scheduled_hour_override=None):
            state["calls"].append({
                "account_id":account_id,"owner":owner,
                "only_id":only_id,"hour":scheduled_hour_override,
            })
            state["posts"].append({
                "id":"rescued-paid","project_id":pid,"project_name":"Paid Client",
                "generation_key":"social-slot:u42:paid1:20260905_09",
                "status":"draft","created_at":"2026-09-05 10:00",
                "publish_at":None,"ids":{},"error":"",
            })
        fake.autopost_projects=autopost_projects

        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                base=cls(2026,9,5,10,0,0)
                return base.replace(tzinfo=tz) if tz is not None else base
        mod.datetime=FixedDateTime
        mod.subprocess=SimpleNamespace(run=lambda *a,**k: SimpleNamespace(returncode=0,stdout="",stderr=""))
        heartbeats=[]
        mod._heartbeat=lambda st,details: heartbeats.append((st,details))

        old=sys.modules.get("posting_runner")
        sys.modules["posting_runner"]=fake
        try:
            rc=mod.main()
        finally:
            if old is None: sys.modules.pop("posting_runner",None)
            else: sys.modules["posting_runner"]=old

        self.assertEqual(rc,0)
        self.assertEqual(len(state["calls"]),1)
        self.assertEqual(state["calls"][0]["account_id"],acc)
        self.assertFalse(state["calls"][0]["owner"])
        self.assertEqual(state["calls"][0]["only_id"],pid)
        self.assertEqual(state["calls"][0]["hour"],9)
        rescued=heartbeats[-1][1].get("generation_rescued") or []
        self.assertTrue(any(x.get("account_id")==acc and x.get("project_id")==pid for x in rescued))
        contours=heartbeats[-1][1].get("active_paid_contours") or []
        self.assertTrue(any(x.get("account_id")==acc and x.get("project_id")==pid for x in contours))


if __name__=="__main__":
    unittest.main()
