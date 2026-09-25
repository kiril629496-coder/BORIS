import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT=Path("/root/BORIS/backend")
GUARD=ROOT/"run/social_authority_guard.py"
WRAPPER=ROOT/"run/deploy_social_owner_oceanfooter_20260905.py"
EDITORIAL_WRAPPER=ROOT/"run/deploy_social_owner_editorial_merge_20260905.py"
SCOPE_FENCE=ROOT/"run/fence_neighbor_social_tasks_20260905.py"
REGISTRY=ROOT/"config/workstream_scope_registry.json"
CONTRACT=Path("/usr/local/share/boris-social-authority-contract.json")
CONTRACT_PTR=Path("/usr/local/share/boris-social-authority-contract.sha")
SERVICE=Path("/etc/systemd/system/boris-social-authority-guard.service")
INTEGRITY_UNIT=Path("/etc/systemd/system/boris-integrity-lock01.service")


def sha(path:Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


spec=importlib.util.spec_from_file_location("social_authority_guard",GUARD)
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class SocialAuthorityGuardTest(unittest.TestCase):
    def test_live_authority_has_no_drift(self):
        snap=guard.snapshot()
        self.assertEqual(guard.drift(snap),[],snap)
        objects=Path("/var/lib/boris-integrity/objects")
        for expected in (guard.POLICY_SHA,guard.PREFLIGHT_SHA,guard.HARDGUARD_SHA,guard.LOCK_SHA):
            self.assertEqual(sha(objects/expected),expected)
        source=GUARD.read_text(encoding="utf-8")
        self.assertIn("SOCIAL_AUTHORITY_GUARD_READ_ONLY_V1",source)
        self.assertNotIn("def heal(",source)

    def test_immutable_social_contract_matches_guard(self):
        contract=json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["owner_workstream"],"social_reliability_core")
        self.assertTrue(contract["single_writer"])
        self.assertEqual(contract["policy_sha"],guard.POLICY_SHA)
        self.assertEqual(contract["preflight_sha"],guard.PREFLIGHT_SHA)
        self.assertEqual(contract["hardguard_sha"],guard.HARDGUARD_SHA)
        self.assertEqual(contract["integrity_lock_sha"],guard.LOCK_SHA)
        self.assertEqual(
            contract["legacy_ocean_client_only_sha"],
            guard.LEGACY_OCEAN_DEPLOY_SHA,
        )
        self.assertEqual(contract["editorial_client_only_deploy_sha"],guard.EDITORIAL_DEPLOY_SHA)
        self.assertEqual(contract["scope_fence_sha"],guard.SCOPE_FENCE_SHA)
        self.assertEqual(CONTRACT_PTR.read_text().strip(),sha(CONTRACT))

    def test_registry_only_declares_boundary_and_contract_reference(self):
        data=json.loads(REGISTRY.read_text(encoding="utf-8"))
        core=data["workstreams"]["social_reliability_core"]
        ref=core.get("authority_contract_ref") or {}
        self.assertEqual(ref.get("ownership"),"social_reliability_core")
        self.assertEqual(ref.get("path"),str(CONTRACT))
        ocean=next(
            x for x in core.get("neighbor_chat_aliases") or []
            if x.get("chat")=="Проверка VK Океан"
        )
        self.assertEqual(ocean.get("shared_authority_write"),"forbidden")
        self.assertEqual(ocean.get("client_config_write"),"allowed")

    def test_ocean_compat_entrypoint_is_client_only(self):
        text=WRAPPER.read_text(encoding="utf-8")
        self.assertIn('"shared_authority_modified":False',text)
        self.assertIn('delegated_shared_runtime_to":"social_reliability_core"',text)
        for forbidden in (
            "social_owner_policy_v3.oceanfooter.next",
            "boris-social-owner-preflight.oceanfooter.next",
            "hardguard.oceanfooter.next",
            "integrity-lock01.oceanfooter.next",
            "chattr -i /usr/local/share",
            "systemctl stop boris-integrity-lock01",
        ):
            self.assertNotIn(forbidden,text)

    def test_editorial_compat_entrypoint_cannot_write_shared_authority(self):
        text=EDITORIAL_WRAPPER.read_text(encoding="utf-8")
        self.assertEqual(sha(EDITORIAL_WRAPPER),guard.EDITORIAL_DEPLOY_SHA)
        self.assertIn("shared_authority_modified",text)
        self.assertIn("social_reliability_core",text)
        for forbidden in (
            "social_owner_policy_v3.editorial_merge.next",
            "boris-social-owner-preflight.editorial-merge.next",
            "hardguard.editorial-merge.next",
            "integrity-lock01.editorial-merge.next",
            "stop_guarded_runtime",
        ):
            self.assertNotIn(forbidden,text)

    def test_scope_fence_is_client_config_only(self):
        text=SCOPE_FENCE.read_text(encoding="utf-8")
        self.assertEqual(sha(SCOPE_FENCE),guard.SCOPE_FENCE_SHA)
        self.assertIn("client_editorial_config_only",text)
        self.assertIn("owns_paths':[]",text.replace(" ",""))
        self.assertIn("delegates_shared_runtime_to",text)

    def test_service_runs_content_addressed_current_guard(self):
        text=SERVICE.read_text(encoding="utf-8")
        m=re.search(r"/var/lib/boris-integrity/objects/([0-9a-f]{64})",text)
        self.assertIsNotNone(m,text)
        self.assertEqual(m.group(1),sha(GUARD))

    def test_integrity_unit_points_current_lock(self):
        text=INTEGRITY_UNIT.read_text(encoding="utf-8")
        self.assertIn(guard.LOCK_SHA,text)


if __name__=="__main__":
    unittest.main()
