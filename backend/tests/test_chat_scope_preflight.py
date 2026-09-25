import json
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'venv/bin/python'
TOOL=ROOT/'.boris_ops/chat_scope_preflight.py'
SCOPE='planeta_feed_autoload'
ACCOUNT='planetazayavki_65985'

class ChatScopePreflightTests(unittest.TestCase):
    def run_guard(self,*args):
        cp=subprocess.run([str(PYTHON),str(TOOL),'--scope',SCOPE,*args],text=True,capture_output=True)
        payload=json.loads((cp.stdout or '{}').strip().splitlines()[-1])
        return cp.returncode,payload

    def test_allows_owned_feed_lifecycle_file(self):
        rc,p=self.run_guard('--file','app/services/campaign_post_publish_watch.py','--domain','campaign_lifecycle','--campaign','250','--account',ACCOUNT)
        self.assertEqual(rc,0)
        self.assertTrue(p['allowed'])

    def test_denies_money_domain_and_file(self):
        rc,p=self.run_guard('--file','cpx_budget_brake.py','--domain','cpx_money','--account',ACCOUNT)
        self.assertEqual(rc,3)
        self.assertFalse(p['allowed'])
        self.assertIn('domain_not_owned',p['reasons'])
        self.assertTrue(any(x.startswith('file_owned_by:global_cpx_money') for x in p['reasons']))

    def test_denies_product_ui_domain(self):
        rc,p=self.run_guard('--domain','product_ui','--account',ACCOUNT)
        self.assertEqual(rc,3)
        self.assertFalse(p['allowed'])

    def test_denies_shared_supervisor_domain(self):
        rc,p=self.run_guard('--domain','shared_supervisor')
        self.assertEqual(rc,3)
        self.assertFalse(p['allowed'])

    def test_denies_other_campaign(self):
        rc,p=self.run_guard('--domain','campaign_lifecycle','--campaign','999999','--account',ACCOUNT)
        self.assertEqual(rc,3)
        self.assertFalse(p['allowed'])
        self.assertIn('campaign_outside_scope',p['reasons'])

if __name__=='__main__':
    unittest.main()
