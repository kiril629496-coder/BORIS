import json
import uuid
import unittest
from datetime import datetime
from unittest.mock import patch

import posting_runner as pr
from app.models.storage import Storage


class SocialActivePaidPreSlotTest(unittest.TestCase):
    def _cleanup(self, account_id):
        s=pr.SessionLocal()
        try:
            s.query(Storage).filter(Storage.account_id==account_id).delete(
                synchronize_session=False
            )
            s.commit()
        finally:
            s.close()

    def _seed(self, account_id, *, paid=True, expected=True, nested=True):
        sub={
            "paid_at":"2026-09-01",
            "period_days":30,
            "platforms":"both",
            "posts_per_day":2,
        }
        project={
            "id":"same-project-id",
            "name":"QA Paid Social",
            "times":["9","18"],
            "tz_offset":3,
            "mode":"moderate",
            "auto_publish_hours":3,
            "expected_auto_generation":expected,
            "paid_auto_generation":paid,
            "platforms":"both",
            "channel_tg":"@qa",
            "vk_owner_id":-123,
            "image_source":"ai",
            "themes":["тема"],
            "subscription":dict(sub) if nested else None,
        }
        s=pr.SessionLocal()
        try:
            s.add(Storage(account_id=account_id,key="posting_subscription",
                          value=json.dumps(sub,ensure_ascii=False)))
            s.add(Storage(account_id=account_id,key="posting_projects",
                          value=json.dumps([project],ensure_ascii=False)))
            s.add(Storage(account_id=account_id,key="posting_posts",value="[]"))
            s.commit()
        finally:s.close()
        return sub,project

    def test_expired_u42_is_not_monitored_as_active_paid(self):
        # Owner-confirmed production truth: Bytovki Social period ended; an
        # expired service must not trigger paid generation or delivery work.
        h=pr.social_active_paid_pre_slot_readiness_health()
        u42=[x for x in (h.get("projects") or [])
             if x.get("account_id")=="u42"
             and x.get("project_id")=="688556520514"]
        self.assertEqual(u42,[],h)

    def test_self_heal_restores_canonical_subscription_and_expected_paid_flag(self):
        acc="qa_paid_pre_"+uuid.uuid4().hex[:10]
        try:
            self._seed(acc,paid=False,expected=True,nested=False)
            r=pr.social_active_paid_pre_slot_self_heal()
            mine=[x for x in r["changed"] if x.get("account_id")==acc]
            self.assertEqual(len(mine),1,r)
            self.assertIn("subscription",mine[0]["fields"])
            self.assertIn("paid_auto_generation",mine[0]["fields"])

            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_projects"
                ).first()
                saved=json.loads(row.value)[0]
            finally:s.close()
            self.assertTrue(pr._sub_active(saved["subscription"]))
            self.assertTrue(saved["paid_auto_generation"])
        finally:self._cleanup(acc)

    def test_waiting_external_scaffold_counts_paid_account_as_checked(self):
        acc="qa_paid_gap_"+uuid.uuid4().hex[:10]
        sub={
            "paid_at":"2026-09-01",
            "period_days":30,
            "platforms":"both",
            "posts_per_day":2,
        }
        try:
            s=pr.SessionLocal()
            try:
                s.add(Storage(account_id=acc,key="posting_subscription",
                              value=json.dumps(sub,ensure_ascii=False)))
                s.commit()
            finally:s.close()

            r=pr.social_active_paid_pre_slot_self_heal()
            self.assertTrue(r.get("ok"),r)
            self.assertGreaterEqual(int(r.get("accounts_checked") or 0),1,r)

            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,
                    Storage.key=="posting_onboarding_scaffold_v1",
                ).first()
                scaffold=json.loads(row.value) if row else {}
            finally:s.close()
            self.assertEqual(scaffold.get("status"),"waiting_external_destinations",scaffold)
            self.assertEqual(
                set(scaffold.get("missing_external_fields") or []),
                {"channel_tg","vk_owner_id"},
                scaffold,
            )
            self.assertFalse(bool(scaffold.get("owner_action_required")),scaffold)
        finally:self._cleanup(acc)

    def test_same_project_id_is_transport_account_scoped(self):
        a1="qa_paid_pre_"+uuid.uuid4().hex[:8]
        a2="qa_paid_pre_"+uuid.uuid4().hex[:8]
        try:
            self._seed(a1)
            self._seed(a2)
            fake={
                "ok":True,"fresh":True,"state":"ok",
                "projects":[
                    {"account_id":a1,"project_id":"same-project-id","ready":True},
                    {"account_id":a2,"project_id":"same-project-id","ready":False,
                     "owner_action_required":True},
                ],
                "blocked":[],"blocked_count":1,"transport_errors":0,
                "owner_action_required":True,
            }
            with patch.object(pr,"social_transport_readiness_cached_health",return_value=fake),                  patch.object(pr,"_runtime_secret_present",return_value=True):
                h=pr.social_active_paid_pre_slot_readiness_health(
                    datetime(2026,9,5,12,0,0)
                )
            by={
                x["account_id"]:x for x in h["projects"]
                if x.get("account_id") in {a1,a2}
            }
            self.assertTrue(by[a1]["transport_ready"],by[a1])
            self.assertFalse(by[a2]["transport_ready"],by[a2])
            self.assertNotIn("transport_permission_not_ready",by[a1]["blocked_reasons"])
            self.assertIn("transport_permission_not_ready",by[a2]["blocked_reasons"])
        finally:
            self._cleanup(a1);self._cleanup(a2)

    def test_manual_project_is_not_silently_opted_into_paid_ai(self):
        acc="qa_paid_pre_"+uuid.uuid4().hex[:10]
        try:
            self._seed(acc,paid=False,expected=False,nested=True)
            before=pr.social_active_paid_pre_slot_self_heal()
            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_projects"
                ).first()
                saved=json.loads(row.value)[0]
            finally:s.close()
            self.assertFalse(saved["paid_auto_generation"])
            self.assertFalse(saved["expected_auto_generation"])
            mine=[x for x in before["changed"] if x.get("account_id")==acc]
            self.assertEqual(mine,[])
        finally:self._cleanup(acc)


if __name__=="__main__":
    unittest.main()
