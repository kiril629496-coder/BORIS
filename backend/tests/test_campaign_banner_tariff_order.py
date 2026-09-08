import unittest
from pathlib import Path

ROOT=Path('/root/BORIS/backend')

class CampaignBannerTariffOrderTest(unittest.TestCase):
    def test_child_tariff_settles_before_campaign_mutation(self):
        s=(ROOT/'app/api/campaigns.py').read_text(encoding='utf-8')
        block=s[s.find('def ff_workflow_banners'):s.find('@router.post("/{campaign_id}/workflow/category/{item_id}")')]
        semantic=block.find('_semantic_bad=[v for v in _semantic_verdicts')
        charge=block.find('CAMPAIGN_BANNER_CHILD_TARIFF_EXACTLY_ONCE_V1')
        mutate=block.find('M.register_asset(')
        self.assertGreaterEqual(semantic,0)
        self.assertGreater(charge,semantic)
        self.assertGreater(mutate,charge)
        self.assertIn('campaign-banner-item:{campaign_id}:{_row_id}:{_artifact_op}',block)
        tail=block[block.find('if generated:', block.find('CAMPAIGN_BANNER_CHILD_TARIFF_EXACTLY_ONCE_V1')):]
        self.assertNotIn('_billing_consume(_account_for_billing,"banners",len(generated)',tail)
        self.assertIn('Tariff units were settled per item before any campaign mutation',block)

if __name__=='__main__': unittest.main()
