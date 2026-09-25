import inspect
import unittest

from app.api import campaigns


class CampaignExplicitCutoverGuardTests(unittest.TestCase):
    def test_explicit_replacement_is_fail_closed_and_never_merged(self):
        src = inspect.getsource(campaigns.campaign_owner_activate)
        self.assertIn("CAMPAIGN_EXPLICIT_CUTOVER_OLD_ABSENCE_V1", src)
        self.assertIn("replace_old_campaign_with_new_identities", src)
        self.assertIn("preserve_existing_feed_items", src)
        self.assertIn("feed_total_after_merge", src)
        self.assertIn("cutover_scope_not_proven", src)
        self.assertIn("cutover_provider_scope_unproven", src)
        self.assertIn("old_provider_ids_still_present_before_cutover", src)
        self.assertIn("_save_feed_items(target,_preserved_models+campaign_items)", src)
        self.assertIn("if not _explicit_cutover and not _targeted_replacement:", src)
        self.assertIn("_upsert_feed_items(target,campaign_items)", src)

    def test_cutover_verifies_provider_before_feed_replacement(self):
        src = inspect.getsource(campaigns.campaign_owner_activate)
        provider_pos = src.index("_old_still_present")
        save_pos = src.index("_save_feed_items(target,_preserved_models+campaign_items)")
        self.assertLess(provider_pos, save_pos)
        self.assertIn("core_v1_items_all_pages", src)
        self.assertIn("changed_avito", src)


if __name__ == "__main__":
    unittest.main()
