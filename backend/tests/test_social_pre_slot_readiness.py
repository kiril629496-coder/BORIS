import inspect
import json
import uuid
import unittest
from unittest.mock import patch
from pathlib import Path

import posting_runner as pr
from app.models.storage import Storage


class SocialPreSlotReadinessTest(unittest.TestCase):
    def _put_projects(self, account_id, projects):
        s=pr.SessionLocal()
        try:
            s.add(Storage(
                account_id=account_id,
                key="posting_projects",
                value=json.dumps(projects,ensure_ascii=False),
            ))
            s.commit()
        finally:s.close()

    def _cleanup(self, account_id):
        s=pr.SessionLocal()
        try:
            s.query(Storage).filter(Storage.account_id==account_id).delete(
                synchronize_session=False
            )
            s.commit()
        finally:s.close()

    def _base(self):
        return {
            "id":"p1","name":"От Души",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","auto_publish_hours":3,
            "expected_auto_generation":True,
            "paid_auto_generation":True,
            "platforms":"both",
            "channel_tg":"@qa",
            "vk_owner_id":-123,
            "image_source":"ai",
            "themes":["тема 1"],
        }

    def test_current_owner_projects_have_only_known_external_blocker(self):
        h=pr.social_owner_pre_slot_readiness_health("u2")
        by={x["project"]:x for x in h["projects"]}
        self.assertEqual(by["Океан Клиентов и Борис"]["blocked_count"],0,h)
        self.assertEqual(by["От Души"]["blocked_count"],0,h)
        self.assertEqual(by["Океан Клиентов и Борис"]["tz_offset"],3)
        self.assertEqual(by["От Души"]["tz_offset"],3)
        self.assertGreaterEqual(by["Океан Клиентов и Борис"]["media"]["valid_assets"],1)
        manicure=by["Маникюр Евпатория"]
        self.assertEqual(manicure["blocked_count"],0,h)
        blockers=[
            x for x in h["delivery_blockers"]
            if x.get("project_id")=="270dbf472f7f"
        ]
        self.assertEqual(blockers,[],h)
        self.assertTrue(h["delivery_ready"])
        self.assertTrue(h["ok"])
        self.assertEqual(h["runtime_blocked_count"],0)
        self.assertEqual(h["waiting_external_count"],0)
        self.assertFalse(h["owner_action_required"])

        contract=pr.social_scheduler_contract_health()
        self.assertTrue(contract["ok"],contract)
        self.assertEqual(contract["blocked_count"],0,contract)
        external=[
            x for x in contract.get("waiting_external") or []
            if x.get("project_id")=="270dbf472f7f"
        ]
        self.assertEqual(external,[],contract)

    def test_vk_required_action_is_blocked_before_generation(self):
        acc="qa_pre_slot_"+uuid.uuid4().hex[:10]
        p=self._base()
        p["platforms"]="vk"
        p["channel_tg"]=""
        p["vk_action_delivery_required"]=True
        p["vk_action_delivery_ready"]=False
        p["vk_action_delivery_blocker"]="VK_PUBLIC_WALL_POST_NO_NATIVE_MESSAGE_BUTTON"
        try:
            self._put_projects(acc,[p])
            h=pr.social_owner_pre_slot_readiness_health(acc)
            mine=[
                x for x in h["delivery_blockers"]
                if x.get("project_id")=="p1"
            ]
            self.assertIn(
                "VK_PUBLIC_WALL_POST_NO_NATIVE_MESSAGE_BUTTON",
                [x.get("reason") for x in mine],
            )
            self.assertFalse(h["delivery_ready"])
            self.assertFalse(h["generation_ready"])
        finally:
            self._cleanup(acc)

    def test_runtime_secrets_are_found_from_production_env_file(self):
        self.assertTrue(pr._runtime_secret_present("BORIS_TELEGRAM_BOT_TOKEN"))
        self.assertTrue(pr._runtime_secret_present("VK_TOKEN"))
        self.assertTrue(pr._runtime_secret_present("OPENAI_API_KEY"))

    def test_runtime_role_does_not_fake_loaded_secret_from_env_file(self):
        with patch.dict("os.environ", {"BORIS_RUNTIME_ROLE":"worker"}, clear=True):
            self.assertFalse(pr._runtime_secret_present("OPENAI_API_KEY"))

    def test_global_contract_catches_mode_off_expected(self):
        acc="qa_contract_"+uuid.uuid4().hex[:10]
        p=self._base();p["mode"]="off"
        try:
            self._put_projects(acc,[p])
            h=pr.social_scheduler_contract_health()
            mine=[x for x in h["blocked_projects"] if x.get("account_id")==acc]
            self.assertIn("mode_off_but_autopilot_expected",
                          [x.get("reason") for x in mine])
        finally:self._cleanup(acc)

    def test_global_contract_catches_missing_schedule_expected(self):
        acc="qa_contract_"+uuid.uuid4().hex[:10]
        p=self._base();p["times"]=[]
        try:
            self._put_projects(acc,[p])
            h=pr.social_scheduler_contract_health()
            mine=[x for x in h["blocked_projects"] if x.get("account_id")==acc]
            self.assertIn("schedule_missing_or_invalid",
                          [x.get("reason") for x in mine])
        finally:self._cleanup(acc)

    def test_stable_owner_id_with_name_drift_is_fail_closed(self):
        acc="qa_pre_slot_"+uuid.uuid4().hex[:10]
        p=self._base()
        p["id"]="f938caa3a944"
        p["name"]="Переименованный проект"
        try:
            self._put_projects(acc,[p])
            h=pr.social_owner_pre_slot_readiness_health(acc)
            mine=[
                x for x in h["blocked"]
                if x.get("project_id")=="f938caa3a944"
            ]
            self.assertIn(
                "owner_project_identity_name_drift",
                [x.get("reason") for x in mine],
            )
            self.assertFalse(h["generation_ready"])
        finally:self._cleanup(acc)

    def test_missing_owner_project_is_explicit_blocker(self):
        acc="qa_pre_slot_"+uuid.uuid4().hex[:10]
        ocean={
            "id":"a374ced25745","name":"Океан Клиентов и Борис",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","auto_publish_hours":3,
            "expected_auto_generation":True,
            "paid_auto_generation":True,
            "platforms":"both","channel_tg":"@qa",
            "vk_owner_id":-123,"image_source":"reuse",
            "themes":["тема"],
        }
        try:
            self._put_projects(acc,[ocean])
            h=pr.social_owner_pre_slot_readiness_health(acc)
            missing=[
                x for x in h["blocked"]
                if x.get("reason")=="owner_project_missing"
            ]
            self.assertEqual(len(missing),2,h)
            self.assertEqual(
                {x["project_id"] for x in missing},
                {"f938caa3a944","270dbf472f7f"},
            )
            for expected_id in ("f938caa3a944","270dbf472f7f"):
                synthetic=next(
                    x for x in h["projects"]
                    if x.get("project_id")==expected_id
                )
                self.assertEqual(synthetic["blocked_reasons"],["owner_project_missing"])
            self.assertFalse(h["generation_ready"])
        finally:self._cleanup(acc)

    def test_both_owner_projects_present_no_presence_blocker(self):
        h=pr.social_owner_pre_slot_readiness_health("u2")
        self.assertNotIn(
            "owner_project_missing",
            [x.get("reason") for x in h["blocked"]],
        )

    def test_mode_off_expected_is_blocked(self):
        acc="qa_pre_slot_"+uuid.uuid4().hex[:10]
        p=self._base();p["mode"]="off"
        try:
            self._put_projects(acc,[p])
            h=pr.social_owner_pre_slot_readiness_health(acc)
            self.assertIn("mode_off_but_autopilot_expected",
                          [x["reason"] for x in h["blocked"]])
            self.assertFalse(h["generation_ready"])
        finally:self._cleanup(acc)

    def test_missing_schedule_is_blocked(self):
        acc="qa_pre_slot_"+uuid.uuid4().hex[:10]
        p=self._base();p["times"]=[]
        try:
            self._put_projects(acc,[p])
            h=pr.social_owner_pre_slot_readiness_health(acc)
            self.assertIn("schedule_missing_or_invalid",
                          [x["reason"] for x in h["blocked"]])
            self.assertFalse(h["generation_ready"])
        finally:self._cleanup(acc)

    def test_self_heal_restores_paid_flag_and_timezone_only(self):
        acc="qa_pre_slot_"+uuid.uuid4().hex[:10]
        p=self._base()
        p["paid_auto_generation"]=False
        p["tz_offset"]=None
        p["mode"]="off"
        try:
            self._put_projects(acc,[p])
            r=pr.social_owner_pre_slot_self_heal(acc)
            self.assertTrue(r["ok"],r)
            self.assertEqual(r["changed_count"],1)
            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_projects"
                ).first()
                saved=json.loads(row.value)[0]
            finally:s.close()
            self.assertTrue(saved["paid_auto_generation"])
            self.assertEqual(saved["tz_offset"],3)
            self.assertEqual(saved["mode"],"off")
        finally:self._cleanup(acc)

    def test_autopost_self_heals_expected_paid_flag_before_skip_guard(self):
        src=inspect.getsource(pr._autopost_projects_impl)
        heal=src.index("_self_heal_expected_auto_generation(account_id, proj)")
        disabled=src.index("PAID_AI_AUTO_DISABLED")
        self.assertLess(heal,disabled)
        self.assertIn('if respect_time and not bool(proj.get("paid_auto_generation")):',src)

    def test_worker_wires_pre_slot_before_generation(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        self.assertIn('pre_slot_self_heal = social_owner_pre_slot_self_heal("u2")',text)
        self.assertIn('pre_slot_readiness = social_owner_pre_slot_readiness_health("u2")',text)
        self.assertIn('details["generation_blocked"] = "PRE_SLOT_READINESS_PARTIAL"',text)
        self.assertIn('active_paid_pre_slot_self_heal = social_active_paid_pre_slot_self_heal()',text)
        self.assertIn('active_paid_pre_slot_readiness = social_active_paid_pre_slot_readiness_health()',text)
        self.assertIn('skip_accounts=set(paid_blocked_accounts)',text)
        self.assertIn('skip_accounts=skip_accounts or None',text)
        self.assertIn('"other_accounts_continued":True',text)

    def test_incident_reconciler_consumes_active_paid_pre_slot_readiness(self):
        text=Path("app/services/incident_reconciler.py").read_text(encoding="utf-8")
        self.assertIn('incident_type="SOCIAL_PAID_PRE_SLOT_NOT_READY"',text)
        self.assertIn('paid_readiness=dict(hbd.get("active_paid_pre_slot_readiness") or {})',text)
        pos=text.index('incident_type="SOCIAL_PAID_PRE_SLOT_NOT_READY"')
        self.assertIn('service_date="pre_slot"',text[pos:pos+1700])
        self.assertIn('"source_account_id":acc',text[pos:pos+1700])

    def test_incident_reconciler_consumes_pre_slot_readiness(self):
        text=Path("app/services/incident_reconciler.py").read_text(encoding="utf-8")
        self.assertIn('incident_type="OWNER_SOCIAL_PRE_SLOT_NOT_READY"',text)
        self.assertIn('readiness=dict(hbd.get("pre_slot_readiness") or {})',text)
        pos=text.index('incident_type="OWNER_SOCIAL_PRE_SLOT_NOT_READY"')
        block=text[pos:pos+2200]
        self.assertIn('service_date="pre_slot"',block)
        self.assertIn('_dependency_states',text)
        self.assertIn('"dependency_state":dependency_state',block)
        self.assertIn('"owner_action_required":False',block)


    def test_control_plane_treats_external_preslot_blocker_as_waiting_dependency(self):
        text=Path("app/services/control_plane_adapters.py").read_text(encoding="utf-8")
        self.assertIn("SOCIAL_PRE_SLOT_EXTERNAL_WAIT_V1",text)
        self.assertIn('"classification":"SOCIAL_PRE_SLOT_WAITING_EXTERNAL"',text)
        self.assertIn('"dependency_state":"waiting_external"',text)
        self.assertIn('"owner_action_required":False',text)


if __name__=="__main__":
    unittest.main()
