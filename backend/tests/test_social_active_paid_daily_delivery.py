import unittest
from datetime import datetime, timezone
from pathlib import Path

import posting_runner as pr


class SocialActivePaidDailyDeliveryTest(unittest.TestCase):
    def _seed_paid(self, account_id, *, with_project=True):
        import json
        from app.models.storage import Storage
        sub={
            "plan":"post_both","platforms":"both",
            "paid_at":"2026-09-01","period_days":30,"posts_per_day":3,
        }
        project={
            "id":"paid_daily","name":"QA Paid Daily",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","platforms":"both",
            "paid_auto_generation":True,
            "expected_auto_generation":True,
            "channel_tg":"@qa","vk_owner_id":-1,
            "themes":["тема"],"image_source":"ai",
            "subscription":dict(sub),
        }
        post={
            "id":"rec_paid_daily",
            "project_id":"paid_daily",
            "project_name":"QA Paid Daily",
            "generation_key":f"social-slot:{account_id}:paid_daily:20260905_09",
            "status":"published",
            "created_at":"2026-09-05 06:00",
            "published_at":"2026-09-05T09:02:00+00:00",
            "ids":{
                "tg":{"message_id":1},
                "vk":{"post_id":2},
            },
        }
        s=pr.SessionLocal()
        try:
            s.add(Storage(
                account_id=account_id,key="posting_subscription",
                value=json.dumps(sub,ensure_ascii=False),
            ))
            if with_project:
                s.add(Storage(
                    account_id=account_id,key="posting_projects",
                    value=json.dumps([project],ensure_ascii=False),
                ))
                s.add(Storage(
                    account_id=account_id,key="posting_posts",
                    value=json.dumps([post],ensure_ascii=False),
                ))
            s.commit()
        finally:s.close()

    def _cleanup(self, account_id):
        from app.models.storage import Storage
        s=pr.SessionLocal()
        try:
            s.query(Storage).filter(Storage.account_id==account_id).delete(
                synchronize_session=False
            )
            s.commit()
        finally:s.close()

    def test_active_paid_project_is_in_generic_plan_fact(self):
        import uuid
        acc="qa_paid_daily_"+uuid.uuid4().hex[:8]
        try:
            self._seed_paid(acc)
            h=pr.social_active_paid_daily_delivery_health(
                now_utc=datetime(2026,9,5,10,0,tzinfo=timezone.utc)
            )
            rows=[
                x for x in h["projects"]
                if x.get("account_id")==acc
                and x.get("project_id")=="paid_daily"
            ]
            self.assertEqual(len(rows),1,h)
            row=rows[0]
            self.assertEqual(row["project_name"],"QA Paid Daily")
            self.assertEqual(row["planned_total"],2)
            self.assertEqual(row["due_by_now"],1)
            self.assertEqual(row["complete_due"],1)
            self.assertEqual(row["previous_day_missed"],0)
        finally:self._cleanup(acc)

    def test_active_subscription_without_project_is_configuration_gap(self):
        import uuid
        acc="qa_paid_gap_"+uuid.uuid4().hex[:8]
        try:
            self._seed_paid(acc,with_project=False)
            h=pr.social_active_paid_daily_delivery_health()
            gaps=[
                x for x in h["configuration_gaps"]
                if x.get("account_id")==acc
                and x.get("reason")=="active_subscription_without_posting_project"
            ]
            self.assertEqual(len(gaps),1,h)
            self.assertEqual(gaps[0].get("dependency_state"),"waiting_external")
            self.assertFalse(gaps[0].get("owner_action_required"))
            self.assertEqual(
                set(gaps[0].get("required_external_fields") or []),
                {"channel_tg","vk_owner_id"},
            )
            self.assertFalse(any(
                x.get("account_id")==acc
                for x in h["projects"]
            ))
        finally:self._cleanup(acc)


    def test_paid_onboarding_scaffold_waits_for_real_destinations(self):
        import json,uuid
        from app.models.storage import Storage
        acc="qa_paid_scaffold_"+uuid.uuid4().hex[:8]
        try:
            self._seed_paid(acc,with_project=False)
            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,
                    Storage.key=="posting_subscription",
                ).first()
                sub=json.loads(row.value or "{}")
                scaffold,project=pr._paid_social_onboarding_scaffold(s,acc,sub)
            finally:
                s.close()
            self.assertIsNone(project)
            self.assertEqual(scaffold["status"],"waiting_external_destinations")
            self.assertEqual(scaffold["dependency_state"],"waiting_external")
            self.assertFalse(scaffold["owner_action_required"])
            self.assertEqual(
                set(scaffold["missing_external_fields"]),
                {"channel_tg","vk_owner_id"},
            )
            self.assertTrue(scaffold["themes"])
            self.assertTrue(scaffold["times"])
        finally:
            self._cleanup(acc)

    def test_paid_onboarding_scaffold_materializes_from_saved_destinations(self):
        import json,uuid
        from app.models.storage import Storage
        acc="qa_paid_autoproject_"+uuid.uuid4().hex[:8]
        try:
            self._seed_paid(acc,with_project=False)
            s=pr.SessionLocal()
            try:
                s.add(Storage(
                    account_id=acc,
                    key="posting_settings",
                    value=json.dumps({
                        "channel_tg":"@qa_channel",
                        "vk_owner_id":-123456,
                        "themes":["ипотека","подбор квартиры"],
                        "times":["10","17"],
                    },ensure_ascii=False),
                ))
                s.commit()
                row=s.query(Storage).filter(
                    Storage.account_id==acc,
                    Storage.key=="posting_subscription",
                ).first()
                sub=json.loads(row.value or "{}")
                scaffold,project=pr._paid_social_onboarding_scaffold(s,acc,sub)
            finally:
                s.close()
            self.assertIsNotNone(project)
            self.assertEqual(scaffold["status"],"ready_to_materialize")
            self.assertEqual(scaffold["missing_external_fields"],[])
            self.assertEqual(project["channel_tg"],"@qa_channel")
            self.assertEqual(project["vk_owner_id"],-123456)
            self.assertEqual(project["platforms"],"both")
            self.assertEqual(project["mode"],"moderate")
            self.assertTrue(project["paid_auto_generation"])
            self.assertTrue(project["expected_auto_generation"])
        finally:
            self._cleanup(acc)

    def test_project_health_accepts_project_id(self):
        import uuid
        acc="qa_paid_id_"+uuid.uuid4().hex[:8]
        try:
            self._seed_paid(acc)
            by_id=pr.social_project_daily_delivery_health(
                account_id=acc,
                project_name="wrong name does not matter",
                project_id="paid_daily",
                now_utc=datetime(2026,9,5,10,0,tzinfo=timezone.utc),
            )
            self.assertEqual(by_id.get("project_name"),"QA Paid Daily")
            self.assertEqual(by_id.get("project_id"),"paid_daily")
        finally:self._cleanup(acc)

    def test_worker_persists_paid_sla_and_includes_paid_daily_states(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        self.assertIn(
            "active_paid_daily_delivery = social_active_paid_daily_delivery_health()",
            text,
        )
        self.assertIn('"active_paid": paid_sla_journal',text)
        self.assertIn('"paid:"',text)
        self.assertIn(
            'or active_paid_daily_delivery.get("owner_action_required")',
            text,
        )


if __name__=="__main__":
    unittest.main()

class SocialSinglePlatformDailyDeliveryTest(unittest.TestCase):
    def _seed(self, account_id, platform):
        import json
        from app.models.storage import Storage
        s=pr.SessionLocal()
        try:
            sub={
                "plan":"post_"+platform,
                "platforms":platform,
                "paid_at":"2026-09-01",
                "period_days":30,
                "posts_per_day":3,
            }
            project={
                "id":"single","name":"Single Platform",
                "times":["9"],"tz_offset":3,
                "mode":"auto","platforms":platform,
                "paid_auto_generation":True,
                "subscription":sub,
            }
            post={
                "id":"rec1","project_id":"single","project_name":"Single Platform",
                "generation_key":f"social-slot:{account_id}:single:20260905_09",
                "status":"published",
                "created_at":"2026-09-05 06:00",
                "published_at":"2026-09-05T06:02:00+00:00",
                "ids":{
                    "tg":{"message_id":1} if platform=="tg" else {},
                    "vk":{"post_id":2} if platform=="vk" else {},
                },
            }
            s.add(Storage(account_id=account_id,key="posting_projects",
                          value=json.dumps([project],ensure_ascii=False)))
            s.add(Storage(account_id=account_id,key="posting_posts",
                          value=json.dumps([post],ensure_ascii=False)))
            s.commit()
        finally:s.close()

    def _cleanup(self, account_id):
        from app.models.storage import Storage
        s=pr.SessionLocal()
        try:
            s.query(Storage).filter(Storage.account_id==account_id).delete(
                synchronize_session=False
            )
            s.commit()
        finally:s.close()

    def test_tg_only_does_not_require_vk(self):
        import uuid
        from datetime import datetime,timezone
        acc="qa_daily_tg_"+uuid.uuid4().hex[:8]
        try:
            self._seed(acc,"tg")
            h=pr.social_project_daily_delivery_health(
                account_id=acc,project_id="single",
                project_name="Single Platform",
                now_utc=datetime(2026,9,5,10,0,tzinfo=timezone.utc),
            )
            self.assertEqual(h["required_platforms"],"tg")
            self.assertEqual(h["complete_due"],1,h)
            self.assertEqual(h["partial_due"],0,h)
            self.assertEqual(h["state"],"ok",h)
        finally:self._cleanup(acc)

    def test_vk_only_does_not_require_tg(self):
        import uuid
        from datetime import datetime,timezone
        acc="qa_daily_vk_"+uuid.uuid4().hex[:8]
        try:
            self._seed(acc,"vk")
            h=pr.social_project_daily_delivery_health(
                account_id=acc,project_id="single",
                project_name="Single Platform",
                now_utc=datetime(2026,9,5,10,0,tzinfo=timezone.utc),
            )
            self.assertEqual(h["required_platforms"],"vk")
            self.assertEqual(h["complete_due"],1,h)
            self.assertEqual(h["partial_due"],0,h)
            self.assertEqual(h["state"],"ok",h)
        finally:self._cleanup(acc)
