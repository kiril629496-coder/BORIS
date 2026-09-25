import unittest
from pathlib import Path
import production_contract_runner as c
ROOT=Path('/root/BORIS/backend')
class T(unittest.TestCase):
 def test_contract_has_dynamic_single_owner_scan(self):
  src=(ROOT/'production_contract_runner.py').read_text()
  self.assertIn('GLOBAL_HA_SINGLE_ROLLOUT_OWNER_SCAN_V1',src)
  self.assertIn('single_backend_rollout_owner_runtime_scan',src)
 def test_active_helpers_have_no_direct_backend_restart(self):
  bad=[]
  for base in (ROOT/'run',ROOT/'scripts'):
   for p in base.rglob('*'):
    if not p.is_file() or p.suffix not in {'.py','.sh'} or '.bak' in p.name: continue
    if p.name=='rolling_restart_backend.sh': continue
    for line in p.read_text(errors='ignore').splitlines():
     if 'systemctl' in line and 'boris-backend' in line and any(x in line for x in ('restart','try-restart','stop','start')): bad.append(str(p)); break
  self.assertEqual(bad,[])
 def test_guardian_delegates_api_heal(self):
  src=(ROOT/'boris_guardian.py').read_text()
  self.assertIn('MOP_RUNTIME_CONVERGENCE_DELEGATED_TO_DEPLOY_V3',src)
  self.assertIn('version_drift_only=bool(',src)
  self.assertIn('rollout_active',src)
  self.assertIn("all('backend тренировки МОП на старом/неполном runtime-контракте' in x",src)
  self.assertIn('API_HEAL_DELEGATED_TO_DEPLOY_V2',src)
  self.assertNotIn("systemctl restart {service_name}",src)
 def test_contract_requires_manual_restart_refusal(self):
  src=(ROOT/'production_contract_runner.py').read_text()
  self.assertIn('BACKEND_MANUAL_RESTART_REFUSAL_CONTRACT_V1',src)
  self.assertIn('backend_manual_restart_refused',src)
 def test_legacy_deploy_backend_restart_uses_canonical_rolling(self):
  src=(ROOT/'app/ext_api/deploy.py').read_text()
  self.assertIn('BACKEND_RESTART_DELEGATED_TO_CANONICAL_ROLLING_V2',src)
  self.assertIn('rolling_restart_backend.sh',src)
  self.assertIn('BORIS_ROLLING_LANE',src)
  self.assertIn('boris-backend-ha-pair',src)
 def test_ops_cannot_manually_restart_backend(self):
  src=(ROOT/'app/ext_api/ops.py').read_text()
  self.assertIn('BACKEND_OPS_MANUAL_RESTART_DISABLED_V1',src)
  self.assertIn('RESTARTABLE_UNITS = {"boris-frontend"}',src)
 def test_gitdeploy_restart_failure_is_fail_closed(self):
  src=(ROOT/'app/ext_api/gitdeploy.py').read_text()
  self.assertIn('def _restart_detail_ok(units, detail):',src)
  self.assertIn('return bool(detail) and all(',src)
  self.assertIn('restart_ok = _restart_detail_ok(units, restart_detail)',src)
  self.assertIn('if not restart_ok:',src)
  self.assertIn('runtime restart failed',src)
  self.assertIn('rollback_restart = deploy.restart(units)',src)
