import inspect
import unittest

from app.api import campaigns


class CampaignMediaLinkSelfHealTests(unittest.TestCase):
    def test_exact_local_media_link_recovery_is_zero_cost_and_account_scoped(self):
        src = inspect.getsource(campaigns._ff_restore_exact_media_links_from_payload)
        self.assertIn('MediaAsset.account_id==str(row.account_id)', src)
        self.assertIn('MediaAsset.storage_key==rel', src)
        self.assertIn('MediaAsset.deleted_at.is_(None)', src)
        self.assertIn('M.link(', src)
        self.assertNotIn('classify_item_media_vision', src)
        self.assertNotIn('openai', src.lower())
        self.assertIn('status":"not_proven"', src)

    def test_worker_reconciles_payload_image_drift_before_revalidation(self):
        from app.services import jobs
        src = inspect.getsource(jobs._run_campaign_prepare)
        self.assertIn('media_semantic_link_payload_drift', src)
        self.assertIn('MEDIA_LINK_PAYLOAD_RECONCILE_V1', src)
        self.assertIn('MediaAsset.account_id==_row_account', src)
        self.assertIn('MediaAsset.storage_key==_key', src)
        self.assertIn('p["media_semantic_evidence"]=[]', src)
        self.assertIn('row.hero_media_id=int(_desired_assets[0].id)', src)
        self.assertIn('classify_item_media_vision', src)
        self.assertIn('stamp_item_semantic_evidence', src)

    def test_approval_only_self_heals_no_links_then_revalidates(self):
        src = inspect.getsource(campaigns.campaign_approval)
        self.assertIn('_codes=={"media_semantic_no_links"}', src)
        heal_pos = src.index('_ff_restore_exact_media_links_from_payload')
        revalidate_pos = src.index('vr=_ff_validate_row(r,db)', heal_pos)
        ready_pos = src.index('r.status="ready"', revalidate_pos)
        self.assertLess(heal_pos, revalidate_pos)
        self.assertLess(revalidate_pos, ready_pos)


if __name__ == "__main__":
    unittest.main()
