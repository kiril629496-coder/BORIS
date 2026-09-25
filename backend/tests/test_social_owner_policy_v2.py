# -*- coding: utf-8 -*-
import inspect
import unittest
from pathlib import Path

import posting_runner as pr
import boris_background_worker as worker
from app.services import social_public_contract as owner
from app.services import social_owner_preflight as preflight


class SocialOwnerPolicyContractTest(unittest.TestCase):
    def test_public_contract_is_compatibility_shim_to_current_owner_policy(self):
        source=Path("app/services/social_public_contract.py").read_text(encoding="utf-8")
        self.assertTrue(getattr(owner,"OWNER_POLICY_COMPAT_SHIM",False))
        self.assertIn("social_owner_policy_v3 as _owner",source)

    def test_python_preflight_facade_delegates_to_external_root_of_trust(self):
        source=inspect.getsource(preflight)
        self.assertIn("/usr/local/sbin/boris-social-owner-preflight.py",source)
        self.assertIn("--runtime",source)
        result=preflight.run()
        self.assertTrue(result.get("ok"),result)
        self.assertTrue((result.get("source") or {}).get("ok"),result)
        self.assertTrue((result.get("consumers") or {}).get("ok"),result)
        self.assertEqual((result.get("consumers") or {}).get("drift"),[])

    def test_worker_source_and_consumer_guards_use_external_preflight(self):
        source=inspect.getsource(worker._social_owner_policy_source_guard)
        consumer=inspect.getsource(worker._social_owner_policy_consumer_guard)
        self.assertIn("/usr/local/sbin/boris-social-owner-preflight.py",source)
        self.assertIn("/usr/local/sbin/boris-social-owner-preflight.py",consumer)

    def test_owner_policy_has_current_fields(self):
        source=inspect.getsource(owner.ensure_owner_project_policy)
        for marker in (
            '"public_copy_policy": "human_editorial_v2"',
            '"allow_hashtags": True',
            '"forced_ecosystem_slot": False',
            '"forced_occasion": False',
            '"vk_cleanup_order": "newest_first"',
            '"image_source"',
        ):
            self.assertIn(marker,source)

    def test_current_public_boundary_is_green(self):
        health=owner.public_copy_runtime_boundary_health()
        self.assertTrue(health.get("ok"),health)
        ocean_full=owner.render_public_text(
            "<b>Проверка</b>\n\nАвито и продажи.",
            {"name":"Океан Клиентов и Борис"},"tg"
        )
        dushi_full=owner.render_public_text(
            "<b>Проверка</b>\n\nТёплые слова.",
            {"name":"От Души","contact_tg":"@test"},"tg"
        )
        self.assertIn("#BORIS",ocean_full)
        self.assertIn("#отдуши",dushi_full)

    def test_verified_repost_engine_exists(self):
        source=inspect.getsource(pr.repost_vk_post_preserving_photos)
        self.assertIn("engagement_guard",source)
        self.assertIn("verify_new",source)
        self.assertIn("wall.delete",source)
        self.assertIn("attachments",source)


if __name__=="__main__":
    unittest.main(verbosity=2)
