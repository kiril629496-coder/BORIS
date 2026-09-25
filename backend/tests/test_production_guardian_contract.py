# -*- coding: utf-8 -*-
import pathlib, unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class ProductionGuardianContractTest(unittest.TestCase):
    def test_guardian_never_redirects_to_empty_path(self):
        src=(ROOT/'production_guardian_tick.sh').read_text(encoding='utf-8')
        self.assertNotIn('>> ""', src)
        self.assertNotIn('echo "=" >> ""', src)
        self.assertIn('GUARDIAN_ATOMIC_LOG_V1', src)

    def test_backend_uses_only_canonical_rolling_heal(self):
        src=(ROOT/'production_guardian_tick.sh').read_text(encoding='utf-8')
        self.assertIn('GUARDIAN_BACKEND_RECONCILE_DELEGATED_TO_DEPLOY_V1', src)
        self.assertIn('BACKEND_RECONCILE_OWNED_BY_DEPLOY', src)
        self.assertIn('boris-deploy.service', src)
        self.assertNotIn('BORIS_ROLLING_LANE=production_guardian_v1', src)
        self.assertNotIn('systemctl restart boris-backend.service', src)
        self.assertNotIn('systemctl restart boris-backend-replica.service', src)

    def test_contract_snapshot_is_unique_per_tick(self):
        src=(ROOT/'production_guardian_tick.sh').read_text(encoding='utf-8')
        self.assertIn('mktemp /tmp/boris_guardian_contract.', src)
        self.assertIn('FINAL_CONTRACT_TMP="$(mktemp /tmp/boris_guardian_contract_final.', src)
        self.assertIn('trap \'rm -f "$CONTRACT_TMP" "$FINAL_CONTRACT_TMP"\' EXIT', src)

if __name__ == '__main__': unittest.main()
