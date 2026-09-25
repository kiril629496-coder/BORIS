import json
import uuid
import unittest

import posting_runner as pr
from app.models.storage import Storage
from app.services.social_entitlement import (
    normalize_posting_subscription,
    posting_entitlement_view,
)


class SocialEntitlementNormalizationTest(unittest.TestCase):
    def test_known_plan_restores_missing_runtime_entitlement(self):
        sub={
            "plan":"post_both","paid_at":"2026-09-01",
            "period_days":30,"amount_rub":0,
        }
        out,changes=normalize_posting_subscription(sub)
        self.assertEqual(out["platforms"],"both")
        self.assertEqual(out["posts_per_day"],3)
        self.assertIn("platforms",changes)
        self.assertIn("posts_per_day",changes)

    def test_posts_per_day_is_limit_not_schedule_requirement(self):
        view=posting_entitlement_view({
            "plan":"post_both","platforms":"both","posts_per_day":3,
            "paid_at":"2026-09-01","period_days":30,
        })
        self.assertEqual(view["posts_per_day_limit"],3)

    def test_unknown_plan_is_not_guessed(self):
        sub={"plan":"custom_social","paid_at":"2026-09-01","period_days":30}
        out,changes=normalize_posting_subscription(sub)
        self.assertEqual(out,sub)
        self.assertEqual(changes,{})

    def test_self_heal_backfills_root_and_project_from_same_entitlement(self):
        acc="qa_ent_"+uuid.uuid4().hex[:10]
        root={
            "plan":"post_both","platforms":"both",
            "paid_at":"2026-09-01","period_days":30,
        }
        project={
            "id":"p1","name":"QA",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","expected_auto_generation":True,
            "paid_auto_generation":True,
            "platforms":"both","channel_tg":"@qa","vk_owner_id":-1,
            "themes":["тема"],"image_source":"ai",
            "subscription":{
                "plan":"post_both","platforms":"both",
                "paid_at":"2026-09-01","period_days":30,
            },
        }
        s=pr.SessionLocal()
        try:
            s.add(Storage(account_id=acc,key="posting_subscription",
                          value=json.dumps(root,ensure_ascii=False)))
            s.add(Storage(account_id=acc,key="posting_projects",
                          value=json.dumps([project],ensure_ascii=False)))
            s.add(Storage(account_id=acc,key="posting_posts",value="[]"))
            s.commit()
        finally:s.close()
        try:
            result=pr.social_active_paid_pre_slot_self_heal()
            mine=[x for x in result["changed"] if x.get("account_id")==acc]
            self.assertTrue(mine,result)
            s=pr.SessionLocal()
            try:
                rr=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_subscription"
                ).first()
                prr=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_projects"
                ).first()
                root_saved=json.loads(rr.value)
                project_saved=json.loads(prr.value)[0]
            finally:s.close()
            self.assertEqual(root_saved["posts_per_day"],3)
            self.assertEqual(project_saved["subscription"]["posts_per_day"],3)
            self.assertEqual(project_saved["subscription"]["platforms"],"both")
        finally:
            s=pr.SessionLocal()
            try:
                s.query(Storage).filter(Storage.account_id==acc).delete(
                    synchronize_session=False
                )
                s.commit()
            finally:s.close()

    def test_two_slots_do_not_violate_three_post_package_limit(self):
        acc="qa_ent_slots_"+uuid.uuid4().hex[:10]
        sub={
            "plan":"post_both","platforms":"both",
            "paid_at":"2026-09-01","period_days":30,"posts_per_day":3,
        }
        project={
            "id":"p_slots","name":"QA Slots",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","expected_auto_generation":True,
            "paid_auto_generation":True,
            "platforms":"both","channel_tg":"@qa","vk_owner_id":-1,
            "themes":["тема"],"image_source":"ai",
            "subscription":dict(sub),
        }
        s=pr.SessionLocal()
        try:
            s.add(Storage(account_id=acc,key="posting_subscription",
                          value=json.dumps(sub,ensure_ascii=False)))
            s.add(Storage(account_id=acc,key="posting_projects",
                          value=json.dumps([project],ensure_ascii=False)))
            s.add(Storage(account_id=acc,key="posting_posts",value="[]"))
            s.commit()
        finally:s.close()
        try:
            h=pr.social_active_paid_pre_slot_readiness_health()
            row=next(
                x for x in h["projects"]
                if x.get("account_id")==acc and x.get("project_id")=="p_slots"
            )
            self.assertEqual(row["configured_slots_per_day"],2)
            self.assertEqual(row["package_posts_per_day_limit"],3)
            self.assertEqual(
                row["schedule_vs_package_note"],
                "posts_per_day_is_package_limit_not_required_slot_count",
            )
            self.assertNotIn(
                "schedule_count_below_package_limit",
                row["blocked_reasons"],
            )
        finally:
            s=pr.SessionLocal()
            try:
                s.query(Storage).filter(Storage.account_id==acc).delete(
                    synchronize_session=False
                )
                s.commit()
            finally:s.close()


if __name__=="__main__":
    unittest.main()
