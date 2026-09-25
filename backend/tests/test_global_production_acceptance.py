import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import global_production_acceptance as gpa
from global_production_acceptance import decide, SOURCE_QUIET_SEC


class GlobalProductionAcceptanceTests(unittest.TestCase):
    def health(self, ok=True):
        return {k: {'ok': ok, 'status': 200 if ok else 503} for k in ('api1','api2','public','dashboard')}

    def test_source_age_uses_ctime_when_safe_editor_preserves_old_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app = root / 'app'
            app.mkdir()
            target = app / 'fresh.py'
            target.write_text('x=1\n', encoding='utf-8')
            old = time.time() - 3600
            os.utime(target, (old, old))
            # chmod changes ctime while preserving the deliberately old mtime,
            # matching atomic/safe-editor metadata behaviour seen in production.
            os.chmod(target, 0o600)
            now = time.time()
            with patch.object(gpa, 'ROOT', root):
                age = gpa._newest_runtime_source_age(now=now)
        self.assertIsNotNone(age)
        self.assertLess(age, 10)

    def test_exact_full_pass(self):
        r=decide({'status':'pass','accounts':20,'passed_accounts':20,'generation_fingerprint':'g'},0,self.health(),False,999,{},1000)
        self.assertEqual(r['state'],'pass')
        self.assertFalse(r['owner_action_required'])
        self.assertEqual(r['last_pass_at'],1000)

    def test_dashboard_down_cannot_be_final_pass(self):
        h=self.health()
        h['dashboard']={'ok':False,'status':502}
        r=decide({'status':'pass','accounts':20,'passed_accounts':20},0,h,False,999,{},1000)
        self.assertNotEqual(r['state'],'pass')
        self.assertFalse(r['all_health_ok'])

    def test_contract_pass_during_active_roll_is_not_final_pass(self):
        r=decide({'status':'pass','accounts':20,'passed_accounts':20},0,self.health(),True,999,{},1000)
        self.assertEqual(r['state'],'converging')
        self.assertIsNone(r['last_pass_at'])


    def test_active_roll_is_convergence_not_incident(self):
        r=decide({'status':'converging','accounts':20,'passed_accounts':0},75,self.health(),True,999,{},1000)
        self.assertEqual(r['state'],'converging')
        self.assertIsNone(r['self_heal_action'])
        self.assertFalse(r['owner_action_required'])

    def test_recent_source_change_is_convergence_not_incident(self):
        r=decide({'status':'converging','accounts':20,'passed_accounts':0},75,self.health(),False,SOURCE_QUIET_SEC-1,{},1000)
        self.assertEqual(r['state'],'converging')
        self.assertEqual(r['consecutive_stalled_ticks'],0)

    def test_quiet_stall_self_heals_on_second_tick(self):
        prev={'consecutive_stalled_ticks':1,'last_self_heal_at':None}
        r=decide({'status':'converging','accounts':20,'passed_accounts':0},75,self.health(),False,SOURCE_QUIET_SEC+10,prev,4000)
        self.assertEqual(r['state'],'stalled_convergence')
        self.assertEqual(r['self_heal_action'],'restart_boris_deploy')
        self.assertFalse(r['owner_action_required'])

    def test_owner_not_needed_before_self_heal_has_time_to_work(self):
        prev={'consecutive_stalled_ticks':6,'last_self_heal_at':3900}
        r=decide({'status':'converging','accounts':20,'passed_accounts':0},75,self.health(),False,SOURCE_QUIET_SEC+10,prev,4000)
        self.assertFalse(r['owner_action_required'])

    def test_persistent_failure_after_self_heal_can_escalate(self):
        prev={'consecutive_stalled_ticks':6,'last_self_heal_at':1000}
        r=decide({'status':'fail','accounts':20,'passed_accounts':0},1,self.health(),False,SOURCE_QUIET_SEC+10,prev,4000)
        self.assertTrue(r['owner_action_required'])


if __name__ == '__main__':
    unittest.main()
