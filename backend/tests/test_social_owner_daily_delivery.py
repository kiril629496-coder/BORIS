import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
import posting_runner as pr

class SocialOwnerDailyDeliveryTest(unittest.TestCase):
    def _historical_dushi_health(self, now_utc):
        # Historical SLA contract, independent of the mutable client's live DB.
        import json
        from unittest.mock import patch
        project = {"id":"f938caa3a944","name":"От Души","platforms":"both",
                   "expected_auto_generation":True,"paid_auto_generation":True,
                   "times":["9","18"],"tz_offset":3,"mode":"moderate","auto_publish_hours":3}
        posts = [{"id":record,"project_id":project["id"],"project_name":"От Души",
                  "status":"published","generation_key":f"social-slot:u2:f938caa3a944:20260904_{hour}",
                  "published_at":published,"ids":{"tg":{"message_id":i},"vk":{"post_id":i}}}
                 for hour,record,published,i in
                 [("09","df6a951bc312","2026-09-04T11:34:25+00:00",91),
                  ("18","fixture_evening","2026-09-04T18:01:00+00:00",181)]]
        class Query:
            def __init__(self, value): self.value=value
            def filter(self, *a): return self
            def first(self): return SimpleNamespace(value=json.dumps(self.value))
        class DB:
            def __init__(self): self.rows=iter(([project],posts))
            def query(self, *a): return Query(next(self.rows))
            def close(self): return None
        with patch.object(pr, "SessionLocal", DB):
            return pr.dushi_daily_delivery_health("u2", now_utc)

    def _qa_missing_today_health(self, now_utc):
        from app.models.storage import Storage
        account="qa_daily_missing_"+uuid.uuid4().hex[:10]
        project={
            "id":"qa_dushi",
            "name":"От Души",
            "expected_auto_generation":True,
            "paid_auto_generation":True,
            "times":["9","18"],
            "tz_offset":3,
            "mode":"moderate",
            "auto_publish_hours":3,
        }
        db=pr.SessionLocal()
        try:
            db.add(Storage(
                account_id=account,key="posting_projects",
                value=__import__("json").dumps([project],ensure_ascii=False),
            ))
            db.add(Storage(account_id=account,key="posting_posts",value="[]"))
            db.commit()
        finally:
            db.close()
        try:
            return pr.social_project_daily_delivery_health(
                account,"От Души",now_utc
            )
        finally:
            db=pr.SessionLocal()
            try:
                db.query(Storage).filter(Storage.account_id==account).delete(
                    synchronize_session=False
                )
                db.commit()
            finally:
                db.close()

    def test_ocean_today_is_complete(self):
        # Keep this contract hermetic. The real u2 Ocean schedule can change;
        # a historical fixed date must not be reinterpreted through today's
        # production schedule (for example an old 18:00 slot after moving to 19:00).
        from app.models.storage import Storage
        account="qa_ocean_complete_"+uuid.uuid4().hex[:10]
        pid="qa_ocean"
        project={
            "id":pid,
            "name":"Океан Клиентов и Борис",
            "platforms":"both",
            "channel_tg":"@qa",
            "vk_owner_id":-1,
            "expected_auto_generation":True,
            "times":["9","19"],
            "tz_offset":3,
            "mode":"auto",
        }
        posts=[]
        for hour,mid in ((9,"901"),(19,"1901")):
            posts.append({
                "id":"qa"+str(hour),
                "project_id":pid,
                "project_name":"Океан Клиентов и Борис",
                "status":"published",
                "generation_key":f"social-slot:{account}:{pid}:20260904_{hour:02d}",
                "published_at":f"2026-09-04T{hour-3:02d}:01:00+00:00",
                "ids":{
                    "tg":{"message_id":int(mid)},
                    "vk":{"post_id":int(mid)},
                },
            })
        db=pr.SessionLocal()
        try:
            db.add(Storage(account_id=account,key="posting_projects",value=__import__("json").dumps([project],ensure_ascii=False)))
            db.add(Storage(account_id=account,key="posting_posts",value=__import__("json").dumps(posts,ensure_ascii=False)))
            db.commit()
        finally:
            db.close()
        try:
            h=pr.ocean_daily_delivery_health(account,datetime(2026,9,4,20,0,tzinfo=timezone.utc))
        finally:
            db=pr.SessionLocal()
            try:
                db.query(Storage).filter(Storage.account_id==account).delete(synchronize_session=False)
                db.commit()
            finally:
                db.close()
        self.assertEqual(h["project_name"],"Океан Клиентов и Борис")
        self.assertEqual(h["planned_total"],2)
        self.assertEqual(h["complete_due"],2)
        self.assertEqual(h["overdue_due"],0)
        self.assertEqual(h["state"],"ok")
        self.assertTrue(h["sla_ok"])
        self.assertEqual(h["sla_breach_count"],0)

    def test_dushi_today_is_complete(self):
        h=self._historical_dushi_health(datetime(2026,9,4,20,0,tzinfo=timezone.utc))
        self.assertEqual(h["project_name"],"От Души")
        self.assertEqual(h["local_time"],"23:00")
        self.assertEqual(h["planned_total"],2)
        self.assertEqual(h["complete_due"],2)
        self.assertEqual(h["overdue_due"],0)
        self.assertEqual(h["state"],"ok")
        self.assertEqual(h["status"],"on_track_with_sla_breach")
        self.assertFalse(h["sla_ok"])
        self.assertEqual(h["sla_breach_count"],1)
        self.assertEqual(h["late_due"],1)
        breach=h["sla_breaches"][0]
        self.assertEqual(breach["record_id"],"df6a951bc312")
        self.assertEqual(
            breach["published_at"],"2026-09-04T11:34:25+00:00"
        )
        self.assertGreaterEqual(breach["late_minutes_after_grace"],139)

    def test_intentionally_disabled_project_is_not_failure(self):
        original=pr.SessionLocal

        class _Q:
            def __init__(self,row):
                self.row=row
            def filter(self,*a,**k):
                return self
            def first(self):
                return self.row

        class _DB:
            def __init__(self):
                self.calls=0
            def query(self,*a,**k):
                self.calls+=1
                if self.calls==1:
                    projects=[{
                        "id":"disabled1",
                        "name":"От Души",
                        "expected_auto_generation":False,
                        "times":["9","18"],
                        "mode":"moderate",
                        "auto_publish_hours":3,
                    }]
                    return _Q(SimpleNamespace(value=__import__("json").dumps(projects,ensure_ascii=False)))
                return _Q(SimpleNamespace(value="[]"))
            def close(self):
                pass

        try:
            pr.SessionLocal=lambda: _DB()
            h=pr.social_project_daily_delivery_health(
                "u2","От Души",datetime(2026,9,5,20,0,tzinfo=timezone.utc)
            )
        finally:
            pr.SessionLocal=original

        self.assertEqual(h["state"],"ok")
        self.assertEqual(h["status"],"disabled_intentionally")
        self.assertEqual(h["planned_total"],0)
        self.assertFalse(h["owner_action_required"])

    def test_moderation_window_is_not_false_overdue(self):
        # 10:00 local: 09:00 draft slot exists conceptually, but 3-hour
        # moderation window means delivery is not due until 12:00 local.
        h=self._historical_dushi_health(
            datetime(2026,9,5,7,0,tzinfo=timezone.utc)
        )
        self.assertEqual(h["delivery_delay_hours"],3)
        self.assertEqual(h["due_by_now"],0)
        self.assertEqual(h["overdue_due"],0)
        self.assertEqual(h["state"],"ok")

    def test_delivery_due_grace_does_not_alarm_early(self):
        # 12:05 local: delivery is due, but still inside 15-minute grace.
        h=self._qa_missing_today_health(
            datetime(2026,9,5,9,5,tzinfo=timezone.utc)
        )
        self.assertEqual(h["due_by_now"],1)
        self.assertEqual(h["missing_due"],1)
        self.assertEqual(h["overdue_due"],0)
        self.assertEqual(h["state"],"ok")

    def test_delivery_after_grace_enters_self_heal(self):
        # 12:16 local: 09:00 + 3h review + 15m grace has expired.
        h=self._qa_missing_today_health(
            datetime(2026,9,5,9,16,tzinfo=timezone.utc)
        )
        self.assertEqual(h["overdue_due"],1)
        self.assertEqual(h["state"],"degraded")
        self.assertFalse(h["owner_action_required"])

    def test_future_missing_slot_becomes_degraded_without_owner_alarm(self):
        h=self._qa_missing_today_health(
            datetime(2026,9,5,10,0,tzinfo=timezone.utc)
        )
        self.assertEqual(h["state"],"degraded")
        self.assertEqual(h["overdue_due"],1)
        self.assertFalse(h["owner_action_required"])

    def test_unrecovered_previous_day_becomes_critical(self):
        from app.models.storage import Storage
        account="qa_daily_previous_"+uuid.uuid4().hex[:10]
        project={
            "id":"qa_dushi",
            "name":"От Души",
            "expected_auto_generation":True,
            "paid_auto_generation":True,
            "times":["9","18"],
            "tz_offset":3,
            "mode":"moderate",
            "auto_publish_hours":3,
        }
        posts=[{
            "id":"qa_prev_incomplete",
            "project_id":"qa_dushi",
            "status":"draft",
            "generation_key":"social-slot:%s:qa_dushi:20260905_09"%account,
            "ids":{},
        }]
        db=pr.SessionLocal()
        try:
            db.add(Storage(
                account_id=account,key="posting_projects",
                value=__import__("json").dumps([project],ensure_ascii=False),
            ))
            db.add(Storage(
                account_id=account,key="posting_posts",
                value=__import__("json").dumps(posts,ensure_ascii=False),
            ))
            db.commit()
        finally:
            db.close()
        try:
            h=pr.social_project_daily_delivery_health(
                account,"От Души",datetime(2026,9,6,0,0,tzinfo=timezone.utc)
            )
        finally:
            db=pr.SessionLocal()
            try:
                db.query(Storage).filter(Storage.account_id==account).delete(
                    synchronize_session=False
                )
                db.commit()
            finally:
                db.close()

        self.assertEqual(h["state"],"critical")
        self.assertGreaterEqual(h["previous_day_missed"],1)
        self.assertTrue(h["owner_action_required"])

    def test_dushi_previous_day_keeps_sla_breach_without_owner_alarm(self):
        h=self._historical_dushi_health(
            datetime(2026,9,5,0,0,tzinfo=timezone.utc)
        )
        self.assertEqual(h["state"],"ok")
        self.assertEqual(h["status"],"on_track_with_sla_breach")
        self.assertEqual(h["previous_day_missed"],0)
        self.assertEqual(h["previous_day_late"],1)
        self.assertEqual(h["sla_breach_count"],1)
        self.assertFalse(h["owner_action_required"])
        self.assertEqual(
            h["sla_breaches"][0]["generation_key"]
            if "generation_key" in h["sla_breaches"][0]
            else h["sla_breaches"][0]["key"],
            "social-slot:u2:f938caa3a944:20260904_09",
        )

    def test_sla_journal_is_idempotent(self):
        import uuid
        from app.models.storage import Storage
        account="qa_sla_"+uuid.uuid4().hex[:10]
        health={
            "account_id":account,
            "project_id":"p1",
            "project_name":"QA",
            "sla_breaches":[{
                "key":"social-slot:"+account+":p1:20260904_09",
                "record_id":"r1",
                "service_date":"2026-09-04",
                "slot":"09:00",
                "delivery_due_at":"2026-09-04 12:00",
                "delivery_due_at_utc":"2026-09-04T09:00:00+00:00",
                "published_at":"2026-09-04T11:34:25+00:00",
                "delay_minutes":155,
                "late_minutes_after_grace":140,
            }],
        }
        try:
            first=pr.persist_social_delivery_sla_breaches(health)
            second=pr.persist_social_delivery_sla_breaches(health)
            self.assertTrue(first["ok"],first)
            self.assertEqual(first["created"],1)
            self.assertEqual(second["created"],0)
            self.assertEqual(second["existing"],1)
            db=pr.SessionLocal()
            try:
                rows=db.query(Storage).filter(
                    Storage.account_id==account,
                    Storage.key.like("social_delivery_sla_breach:%"),
                ).all()
                self.assertEqual(len(rows),1)
            finally: db.close()
        finally:
            db=pr.SessionLocal()
            try:
                db.query(Storage).filter(Storage.account_id==account).delete(
                    synchronize_session=False
                )
                db.commit()
            finally: db.close()

    def test_worker_persists_sla_breach_journal_without_owner_action(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        self.assertIn(
            "persist_social_delivery_sla_breaches = _social_runner.persist_social_delivery_sla_breaches",
            text,
        )
        self.assertIn('details["social_delivery_sla_journal"]',text)
        self.assertIn('details["daily_delivery_sla_breach_recorded"] = True',text)

    def test_worker_consumes_both_daily_guards(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        self.assertIn('dushi_daily_delivery_health = _social_runner.dushi_daily_delivery_health',text)
        self.assertIn('details["dushi_daily_delivery"] = dushi_daily_delivery',text)
        self.assertIn('"dushi": dushi_daily_delivery',text)

if __name__=="__main__":
    unittest.main()

class SocialOwnerIncidentReconcilerTest(unittest.TestCase):
    def test_incident_reconciler_consumes_both_owner_daily_checks(self):
        text=Path("app/services/incident_reconciler.py").read_text(encoding="utf-8")
        self.assertIn('"ocean_daily_delivery", "u2", "Океан Клиентов и Борис"',text)
        self.assertIn('"dushi_daily_delivery", "u2:f938caa3a944", "От Души"',text)
        self.assertIn('"manicure_daily_delivery", "u2:270dbf472f7f", "Маникюр Евпатория"',text)
        self.assertIn('"daily_delivery":daily,"project_name":project_label',text)
        self.assertIn('and not external_delivery_wait',text)

    def test_historical_social_p1_inherits_fresh_external_transport_classification(self):
        text=Path("app/services/incident_reconciler.py").read_text(encoding="utf-8")
        self.assertIn("SOCIAL_HISTORICAL_EXTERNAL_RECLASS_V1",text)
        self.assertIn('ControlIncident.account_id==incident_account_id',text)
        self.assertIn('"dependency_state":"waiting_external"',text)
        self.assertIn('"dependency_verified_at":now.isoformat()',text)
        self.assertIn('historical.details_json=payload',text)
        self.assertIn('historical.updated_at=now',text)
