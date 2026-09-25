# -*- coding: utf-8 -*-
from app.ext_api import a2a, executor


def test_executor_has_concrete_worker_lease_loop():
    assert callable(a2a.heartbeat_claim)
    assert callable(a2a.release_stale_claims)
    assert callable(executor._lease_loop)


def test_stale_requeue_uses_live_updated_at_and_recovers_orphans():
    source = open(a2a.__file__, encoding="utf-8").read()
    block = source[source.index("def release_stale_claims"):source.index("def claim_order")]
    assert "updated_at < NOW()" in block
    assert "claimed_at < NOW()" not in block
    # A RUNNING row without claimed_by cannot heartbeat either; it must not
    # become a permanent zombie merely because the worker field is missing.
    assert "claimed_by IS NOT NULL" not in block
    assert "orphan" in block


def test_executor_sweeps_stale_claims_before_claiming_new_work():
    source = open(executor.__file__, encoding="utf-8").read()
    run_block = source[source.index("def run("):]
    assert run_block.index("a2a.release_stale_claims") < run_block.index("o = once()")


def test_lease_is_bound_to_claimed_worker():
    source = open(a2a.__file__, encoding="utf-8").read()
    block = source[source.index("def heartbeat_claim"):source.index("def release_stale_claims")]
    assert "claimed_by=:worker" in block
    assert "status=:run" in block
