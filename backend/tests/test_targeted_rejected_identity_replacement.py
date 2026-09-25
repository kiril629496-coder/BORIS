import inspect
import json
import unittest

from app.api import campaigns as campaigns
from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.services.jobs import _finalize_targeted_rejected_identity_replacement


class TargetedRejectedIdentityReplacementTests(unittest.TestCase):
    def test_activation_guard_orders_provider_proof_before_atomic_mutation(self):
        src = inspect.getsource(campaigns.campaign_owner_activate)
        self.assertIn("replace_single_rejected_feed_identity", src)
        self.assertIn("targeted_replacement_provider_rejection_unproven", src)
        self.assertIn("_autoload_upload_snapshot", src)
        self.assertIn("_mutate_feed_items", src)
        self.assertIn("TARGETED_REPLACEMENT_SCOPE_DRIFT", src)
        self.assertIn("targeted_replacement_evidence", src)
        self.assertLess(src.index("_provider_snap=_autoload_upload_snapshot"), src.index("_mutate_feed_items(target,_swap_one)"))
        self.assertIn("if not _explicit_cutover and not _targeted_replacement", src)

    def test_finalize_supersedes_old_item_only_after_new_publication_truth(self):
        db = SessionLocal()
        try:
            parent = Campaign(
                name="test parent",
                default_account_id="test-targeted-repair",
                status="partial",
                settings_json=json.dumps({
                    "feed_factory": {
                        "step": "publication_partial",
                        "owner_activation": {"status": "partial", "published": 0},
                    }
                }),
            )
            repair = Campaign(
                name="test repair",
                default_account_id="test-targeted-repair",
                status="completed",
                settings_json="{}",
            )
            db.add_all([parent, repair])
            db.flush()

            old = CampaignItem(
                campaign_id=parent.id,
                account_id="test-targeted-repair",
                feed_identity="old-feed",
                avito_item_id="111",
                identity_status="publication_rejected_bound",
                status="draft",
                source="test",
                payload_json=json.dumps({
                    "identity": {
                        "publication_state": "rejected",
                        "publication_rejection_upload_id": 10,
                    }
                }),
            )
            new = CampaignItem(
                campaign_id=repair.id,
                account_id="test-targeted-repair",
                feed_identity="new-feed",
                avito_item_id="222",
                identity_status="published_identity_bound",
                status="published",
                source="test",
                payload_json=json.dumps({
                    "identity": {
                        "feed_identity": "new-feed",
                        "avito_item_id": "222",
                        "resolution": "autoload_report",
                        "upload_id": 11,
                    }
                }),
            )
            db.add_all([old, new])
            db.flush()
            repair.settings_json=json.dumps({
                "publication_mode": "replace_single_rejected_feed_identity",
                "parent_campaign_id": parent.id,
                "parent_campaign_item_id": old.id,
                "replace_feed_identity": "old-feed",
                "targeted_replacement": {
                    "state": "armed_waiting_avito",
                    "new_feed_identity": "new-feed",
                },
            })
            db.flush()

            out=_finalize_targeted_rejected_identity_replacement(db, repair, 11)
            self.assertEqual(out.get("changed"), 1)
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.identity_status, "superseded_by_replacement")
            self.assertEqual(parent.status, "completed")
            parent_state=json.loads(parent.settings_json)
            self.assertEqual((parent_state.get("feed_factory") or {}).get("step"), "publication_completed")
            self.assertEqual(len(parent_state.get("targeted_repairs") or []), 1)
            repair_state=json.loads(repair.settings_json)
            self.assertEqual((repair_state.get("targeted_replacement") or {}).get("state"), "completed")
        finally:
            db.rollback()
            db.close()


if __name__ == "__main__":
    unittest.main()
