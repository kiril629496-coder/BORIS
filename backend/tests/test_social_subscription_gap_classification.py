import unittest
from pathlib import Path
class SocialGapClassification(unittest.TestCase):
 def test_missing_external_targets_not_internal_p1(self):
  s=Path('app/services/incident_reconciler.py').read_text()
  anchor=s.index('incident_type="SOCIAL_SUBSCRIPTION_PROJECT_MISSING"')
  block=s[anchor:anchor+1400]
  self.assertIn('priority="P2"',block)
  self.assertIn('"dependency_state":"waiting_external"',block)
  self.assertIn('"owner_action_required":False',block)
  self.assertIn('"required_fields":list(',block)
  self.assertIn('"self_heal_after_configuration":True',block)
  self.assertIn('"onboarding_scaffold":"posting_onboarding_scaffold_v1"',block)
  self.assertIn('"auto_resume":"materialize_project_when_destinations_present"',block)
if __name__=='__main__': unittest.main()


def test_paid_social_gap_is_external_wait_not_owner_operation():
    import json,uuid
    from app.models.storage import Storage
    from app.db.session import SessionLocal
    from app.services.brain_world_state import _scope_social_diagnosis

    acc="qa_social_gap_scope_"+uuid.uuid4().hex[:8]
    db=SessionLocal()
    try:
        db.add(Storage(
            account_id=acc,
            key="posting_subscription",
            value=json.dumps({
                "plan":"post_both","platforms":"both",
                "paid_at":"2026-09-01","period_days":30,"posts_per_day":3,
            }),
        ))
        db.add(Storage(
            account_id=acc,
            key="posting_onboarding_scaffold_v1",
            value=json.dumps({
                "required_external_fields":["channel_tg","vk_owner_id"],
                "missing_external_fields":["channel_tg","vk_owner_id"],
            }),
        ))
        db.commit()
        out=_scope_social_diagnosis(
            db,acc,{"projects":0,"active_projects":0},
            {"classification":"SOCIAL_FLOW_OK","health":"pass"},
        )
        assert out["classification"]=="SOCIAL_SUBSCRIPTION_PROJECT_MISSING"
        assert out["health"]=="waiting_external"
        assert out["dependency_state"]=="waiting_external"
        assert out["owner_action_required"] is False
        assert out["safe_action"]=="wait_for_external_destinations_then_auto_resume"
        evidence=out["evidence_for"][0]
        assert set(evidence["missing_external_fields"])=={"channel_tg","vk_owner_id"}
        assert evidence["auto_resume"]=="materialize_project_when_destinations_present"
    finally:
        db.query(Storage).filter(Storage.account_id==acc).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()


def test_control_plane_social_adapter_does_not_leak_global_vk_backoff_into_paid_gap():
    from app.services.control_plane_adapters import _social_diagnose
    actual={
        "projects":0,
        "active_projects":0,
        "scheduler_age_sec":5,
        "scheduler_state":"degraded",
        "scheduler_details":{
            "social_vk_cleanup":{"status":"backoff","retry_after_seconds":900},
            "active_paid_pre_slot_readiness":{
                "account_gaps":[{
                    "account_id":"mbroker_27859",
                    "reason":"active_subscription_without_posting_project",
                    "dependency_state":"waiting_external",
                    "owner_action_required":False,
                    "required_external_fields":["channel_tg","vk_owner_id"],
                }],
            },
        },
    }
    out=_social_diagnose(None,"mbroker_27859",None,actual)
    assert out["classification"]=="SOCIAL_SUBSCRIPTION_PROJECT_MISSING"
    assert out["health"]=="waiting_external"
    assert out["dependency_state"]=="waiting_external"
    assert out["owner_action_required"] is False


def test_control_plane_social_adapter_no_project_is_not_global_external_failure():
    from app.services.control_plane_adapters import _social_diagnose
    actual={
        "projects":0,
        "active_projects":0,
        "scheduler_age_sec":5,
        "scheduler_state":"degraded",
        "scheduler_details":{
            "social_vk_cleanup":{"status":"backoff","retry_after_seconds":900},
            "active_paid_pre_slot_readiness":{"account_gaps":[]},
        },
    }
    out=_social_diagnose(None,"vasyailchenko_82650",None,actual)
    assert out["classification"]=="SOCIAL_NO_PROJECT_EXPECTED"
    assert out["health"]=="pass"
