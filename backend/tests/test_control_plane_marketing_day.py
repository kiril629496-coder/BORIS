import unittest
from unittest.mock import patch
from app.services import control_plane_adapters as cpa

class ControlPlaneMarketingDayTests(unittest.TestCase):
    def test_scoped_marketing_state_reads_canonical_moscow_business_day(self):
        seen=[]
        def fake_storage(db, account_id, key):
            seen.append(key)
            if key == 'daily_stats:2026-09-05':
                return {'stats_date':'2026-09-05','items_count':1,'items':[{'status':'active','contacts':2}]}
            return {}
        with patch.object(cpa,'_storage_json',side_effect=fake_storage), \
             patch('app.services.marketing_clock.marketing_today_iso',return_value='2026-09-05'):
            out=cpa.marketing_state(object(),'acc')
        self.assertIn('daily_stats:2026-09-05',seen)
        self.assertEqual(out['stats_date'],'2026-09-05')
        self.assertEqual(out['actual_leads'],2.0)
        self.assertEqual(out['active_items'],1)

if __name__=='__main__': unittest.main()

class ControlPlaneProviderLagTruthTests(unittest.TestCase):
    def test_yesterday_provider_contacts_are_not_exposed_as_today_fact(self):
        def fake_storage(db, account_id, key):
            if key == 'daily_stats:2026-09-05':
                return {'date':'2026-09-05','stats_date':'2026-09-04','items_count':1,'items':[{'status':'active','contacts':5}]}
            return {}
        with patch.object(cpa,'_storage_json',side_effect=fake_storage), \
             patch('app.services.marketing_clock.marketing_today_iso',return_value='2026-09-05'):
            out=cpa.marketing_state(object(),'acc')
        self.assertIsNone(out['actual_leads'])
        self.assertEqual(out['actual_leads_observed'],5.0)
        self.assertTrue(out['provider_stats_lagging'])
        self.assertEqual(out['business_date'],'2026-09-05')
        self.assertEqual(out['stats_date'],'2026-09-04')
