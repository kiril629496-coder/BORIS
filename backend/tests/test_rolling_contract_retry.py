# -*- coding: utf-8 -*-
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RollingContractRetryTests(unittest.TestCase):
    def test_contract_retry_is_bounded_and_same_instance(self):
        src = (ROOT / "rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_INSTANCE_CONTRACT_RETRY_V1", src)
        start = src.index("contract(){")
        end = src.index("capacity_gate(){", start)
        block = src[start:end]
        self.assertIn("seq 1 24", block)
        self.assertIn("sleep 0.25", block)
        self.assertIn('backend_contract_check.py "http://127.0.0.1:$p"', block)
        self.assertNotIn("8000,8001", block)
        self.assertNotIn("127.0.0.1:8000,http://127.0.0.1:8001", block)

    def test_interrupted_rollout_restores_both_only_when_both_are_healthy(self):
        src = (ROOT / "rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("ROLLING_INTERRUPT_SAFE_DRAIN_V1", src)
        self.assertIn("trap _interrupt_cleanup EXIT TERM INT", src)
        start = src.index("_interrupt_cleanup(){")
        end = src.index("trap _interrupt_cleanup EXIT TERM INT", start)
        block = src[start:end]
        self.assertIn("health 8000 && health 8001", block)
        self.assertIn("render none", block)
        self.assertNotIn("systemctl restart", block)

    def test_each_restarted_replica_must_pass_its_own_contract(self):
        src = (ROOT / "rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("contract 8001 ||", src)
        self.assertIn("contract 8000 ||", src)
        self.assertIn("FAIL: API2 contract; kept drained", src)
        self.assertIn("FAIL: API1 contract; kept drained", src)

    def test_public_5xx_is_immediate_failure_not_retry_convergence(self):
        src = (ROOT / "rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_5XX_STRICT_V1", src)
        start = src.index("public(){")
        end = src.index("nginx_workers_converged(){", start)
        block = src[start:end]
        self.assertIn('[[ "$code" =~ ^5[0-9][0-9]$ ]]', block)
        self.assertIn('return 1', block)
        self.assertNotIn("Treat a single transient 502", block)

    def test_opposite_replica_drain_is_fenced_from_old_nginx_workers(self):
        src = (ROOT / "rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("NGINX_DRAIN_GENERATION_FENCE_V1", src)
        self.assertIn("nginx_workers_converged", src)
        self.assertIn("nginx: worker process is shutting down", src)
        render_start = src.index("render(){")
        render_end = src.index("# Preconditions:", render_start)
        render = src[render_start:render_end]
        self.assertIn("if [ \"${drain:-none}\" = 'none' ]", render)
        self.assertIn("nginx_workers_converged", render)
        self.assertIn("upstream rolled back", render)

    def test_roll_pair_explicitly_propagates_nginx_fence_failures(self):
        src = (ROOT / "rolling_restart_backend.sh").read_text(encoding="utf-8")
        start = src.index("roll_pair(){")
        end = src.index("generation_current(){", start)
        block = src[start:end]
        self.assertIn("render 8001 ||", block)
        self.assertIn("render none || { echo 'FAIL: both-replica nginx generation did not converge after API2; API1 restart blocked'", block)
        self.assertIn("render 8000 ||", block)
        self.assertIn("render none || { echo 'FAIL: both-replica nginx generation did not converge after API1'", block)

    def test_api_restarts_use_protected_kill_not_manual_restart(self):
        src=(ROOT/"rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("BACKEND_REFUSE_MANUAL_STOP_V1",src)
        self.assertIn("restart_protected_unit",src)
        self.assertIn("systemctl kill --kill-whom=all --signal=TERM",src)
        self.assertNotIn("systemctl restart boris-backend.service",src)
        self.assertNotIn("systemctl restart boris-backend-replica.service",src)

    def test_source_change_before_primary_restart_preserves_primary(self):
        src=(ROOT/"rolling_restart_backend.sh").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_SOURCE_IMPORT_FENCE_V2",src)
        self.assertIn("source_import_smoke",src)
        block=src[src.index("roll_pair(){"):src.index("generation_current(){",src.index("roll_pair(){"))]
        fence=block.index('source_still_safe "$expected_fp"',block.index("contract 8001"))
        primary=block.index('restart_protected_unit boris-backend.service')
        self.assertLess(fence,primary)
        self.assertIn("API1 preserved",block)


if __name__ == "__main__":
    unittest.main()
