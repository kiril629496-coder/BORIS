import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "chat_task_ownership.json"
PLANETA = "planetazayavki_65985"


class ChatTaskOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.scopes = self.data.get("active_scopes") or []

    def _scope(self, scope_id):
        return next(s for s in self.scopes if s.get("scope_id") == scope_id)

    def test_registry_uses_domain_ownership_v3(self):
        self.assertEqual(self.data.get("policy"), "CROSS_CHAT_DOMAIN_OWNERSHIP_V3")
        self.assertEqual(self.data.get("version"), 3)
        self.assertIn("(account_id, domain)", self.data.get("rule") or "")

    def test_current_chat_owns_only_planeta_feed_autoload_lifecycle(self):
        scope = self._scope("planeta_feed_autoload")
        self.assertTrue(scope.get("exclusive"))
        self.assertEqual(scope.get("account_id"), PLANETA)
        self.assertEqual(set(scope.get("campaign_ids") or []), {144, 249, 250, 252})
        domains = set(scope.get("domains") or [])
        self.assertEqual(
            domains,
            {"feed", "autoload", "campaign_lifecycle", "publication_provider_verification"},
        )
        not_owned = " ".join(scope.get("does_not_own") or []).lower()
        self.assertIn("cpx", not_owned)
        self.assertIn("shared client supervisor/system brain implementation", not_owned)
        self.assertIn("crm/mop", not_owned)

    def test_planeta_money_loop_has_separate_owner(self):
        scope = self._scope("global_cpx_money")
        self.assertTrue(scope.get("exclusive"))
        self.assertIn("cpx_money", scope.get("domains") or [])
        self.assertIn(PLANETA, scope.get("accounts_including") or [])
        owns = " ".join(scope.get("owns") or []).lower()
        for phrase in ("bid raise/lower", "daily spend/budget", "budget brake"):
            self.assertIn(phrase, owns)
        blocked = " ".join(scope.get("must_not_mutate") or []).lower()
        self.assertIn("campaigns 144/249/250/252", blocked)
        self.assertIn("desired feed", blocked)

    def test_planeta_product_ui_mop_has_separate_owner(self):
        scope = self._scope("planeta_product_ui_mop")
        self.assertTrue(scope.get("exclusive"))
        self.assertEqual(scope.get("account_id"), PLANETA)
        domains = set(scope.get("domains") or [])
        self.assertEqual(domains, {"product_ui", "mop_crm_messages"})
        blocked = " ".join(scope.get("must_not_mutate") or []).lower()
        self.assertIn("feed/autoload", blocked)
        self.assertIn("cpx", blocked)
        self.assertIn("shared client supervisor/system brain implementation", blocked)

    def test_planeta_content_review_is_read_only_for_production(self):
        scope = self._scope("planeta_content_review")
        self.assertFalse(scope.get("exclusive"))
        blocked = " ".join(scope.get("must_not_mutate") or []).lower()
        self.assertIn("campaign 144/249/250/252", blocked)
        self.assertIn("cpx", blocked)
        self.assertIn("feed/autoload", blocked)

    def test_shared_supervisor_is_single_global_owner(self):
        shared = [s for s in self.scopes if "shared_supervisor" in (s.get("domains") or [])]
        self.assertEqual(len(shared), 1)
        scope = shared[0]
        self.assertEqual(scope.get("scope_id"), "shared_client_supervisor")
        self.assertTrue(scope.get("exclusive"))
        self.assertIn(
            "must not create a second supervisor",
            str(scope.get("rule") or "").lower(),
        )

    def test_no_exclusive_domain_is_owned_twice_for_same_account(self):
        # Scopes without an explicit account_id are global owners for their named
        # domain. PLANETA-specific scopes may coexist only when their domains differ.
        seen = {}
        conflicts = {}
        for scope in self.scopes:
            if scope.get("exclusive") is not True:
                continue
            account = str(scope.get("account_id") or "").strip()
            if not account:
                continue
            sid = str(scope.get("scope_id") or "")
            for domain in scope.get("domains") or []:
                key = (account, str(domain))
                if key in seen:
                    conflicts.setdefault(key, [seen[key]]).append(sid)
                else:
                    seen[key] = sid
        self.assertEqual(conflicts, {})

    def test_campaign_lifecycle_ids_have_one_mutating_owner(self):
        owners = {}
        for scope in self.scopes:
            if scope.get("exclusive") is not True:
                continue
            sid = str(scope.get("scope_id") or "")
            for cid in scope.get("campaign_ids") or []:
                owners.setdefault(int(cid), []).append(sid)
        duplicates = {cid: sids for cid, sids in owners.items() if len(sids) > 1}
        self.assertEqual(duplicates, {})
        for cid in (144, 249, 250, 252):
            self.assertEqual(owners.get(cid), ["planeta_feed_autoload"])

    def test_owned_files_do_not_overlap_between_planeta_and_money(self):
        feed = set(self._scope("planeta_feed_autoload").get("owned_files") or [])
        money = set(self._scope("global_cpx_money").get("owned_files") or [])
        self.assertEqual(feed & money, set())
        self.assertIn("backend/app/services/campaign_post_publish_watch.py", feed)
        self.assertIn("backend/cpx_budget_brake.py", money)
        self.assertIn("backend/cpx_budget_resume.py", money)

    def test_outreach_is_outside_planeta_scope(self):
        scope = self._scope("owner_outreach")
        owned = " ".join(scope.get("owns") or [])
        self.assertIn("owner_outreach_volume_safety.py", owned)
        self.assertIn(
            "current_planeta_production_chat",
            scope.get("must_not_be_mutated_by") or [],
        )


if __name__ == "__main__":
    unittest.main()
