import unittest
from pathlib import Path

ROOT=Path('/root/BORIS/backend')

class CampaignListingTariffOrderTest(unittest.TestCase):
    def test_new_rows_charge_before_durable_commit(self):
        s=(ROOT/'app/api/campaigns.py').read_text(encoding='utf-8')
        start=s.find('def ff_workflow_materialize')
        end=s.find('@router.post("/{campaign_id}/workflow/mutate")',start)
        block=s[start:end]
        marker=block.find('CAMPAIGN_LISTING_TARIFF_BEFORE_COMMIT_V1')
        charge=block.find('_billing_consume(account_id,"listings",created',marker)
        commit=block.find('    db.commit()',marker)
        self.assertGreaterEqual(marker,0)
        self.assertGreater(charge,marker)
        self.assertGreater(commit,charge)
        self.assertIn('created_refs.append(ref)',block)
        self.assertIn('sorted(created_refs)',block)
        post=block[marker:]
        self.assertNotIn('sorted(ids[-created:])',post)

    def test_billing_failure_rolls_back_new_rows(self):
        s=(ROOT/'app/api/campaigns.py').read_text(encoding='utf-8')
        start=s.find('CAMPAIGN_LISTING_TARIFF_BEFORE_COMMIT_V1')
        end=s.find('ff["item_ids"] = ids',start)
        block=s[start:end]
        self.assertGreaterEqual(start,0)
        self.assertIn('db.rollback()',block)
        self.assertIn('"created":0',block)
        self.assertIn('Новые карточки не сохранены',block)

if __name__=='__main__': unittest.main()
