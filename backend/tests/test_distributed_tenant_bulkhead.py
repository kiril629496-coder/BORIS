import unittest
import uuid
from datetime import datetime, timezone, timedelta

from app.services import jobs
from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob


class DistributedTenantBulkheadTests(unittest.TestCase):
    def tearDown(self):
        # Every test releases its own locks; this hook intentionally stays empty
        # so a leaked lock causes the process/DB smoke around the suite to fail.
        pass

    def test_heavy_allows_one_slot_per_tenant(self):
        first, slot = jobs._try_distributed_tenant_slot("qa-bulkhead-heavy", "photo_session")
        second, second_slot = jobs._try_distributed_tenant_slot("qa-bulkhead-heavy", "photo_session")
        try:
            self.assertIsNotNone(first)
            self.assertEqual(slot, 0)
            self.assertIsNone(second)
            self.assertIsNone(second_slot)
        finally:
            jobs._release_distributed_tenant_slot(first, "qa-bulkhead-heavy", slot)
            jobs._release_distributed_tenant_slot(second, "qa-bulkhead-heavy", second_slot)

    def test_normal_allows_two_slots_and_blocks_third(self):
        one, slot_one = jobs._try_distributed_tenant_slot("qa-bulkhead-normal", "campaign_deliver")
        two, slot_two = jobs._try_distributed_tenant_slot("qa-bulkhead-normal", "campaign_deliver")
        three, slot_three = jobs._try_distributed_tenant_slot("qa-bulkhead-normal", "campaign_deliver")
        try:
            self.assertIsNotNone(one)
            self.assertIsNotNone(two)
            self.assertEqual({slot_one, slot_two}, {0, 1})
            self.assertIsNone(three)
            self.assertIsNone(slot_three)
        finally:
            jobs._release_distributed_tenant_slot(one, "qa-bulkhead-normal", slot_one)
            jobs._release_distributed_tenant_slot(two, "qa-bulkhead-normal", slot_two)
            jobs._release_distributed_tenant_slot(three, "qa-bulkhead-normal", slot_three)

    def test_other_tenant_is_independent(self):
        a, slot_a = jobs._try_distributed_tenant_slot("qa-bulkhead-A", "photo_session")
        b, slot_b = jobs._try_distributed_tenant_slot("qa-bulkhead-B", "photo_session")
        try:
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
        finally:
            jobs._release_distributed_tenant_slot(a, "qa-bulkhead-A", slot_a)
            jobs._release_distributed_tenant_slot(b, "qa-bulkhead-B", slot_b)

    def test_ten_tenants_keep_independent_heavy_slots(self):
        """Ten client jobs may coexist without one tenant consuming another tenant's slot."""
        held = []
        try:
            for index in range(10):
                account = f"qa-bulkhead-tenant-{index}"
                conn, slot = jobs._try_distributed_tenant_slot(account, "photo_session")
                self.assertIsNotNone(conn, account)
                self.assertEqual(slot, 0)
                held.append((conn, account, slot))
            self.assertEqual(len(held), 10)
        finally:
            for conn, account, slot in held:
                jobs._release_distributed_tenant_slot(conn, account, slot)

    def test_release_immediately_restores_capacity(self):
        first, slot = jobs._try_distributed_tenant_slot("qa-bulkhead-release", "photo_session")
        self.assertIsNotNone(first)
        jobs._release_distributed_tenant_slot(first, "qa-bulkhead-release", slot)
        again, again_slot = jobs._try_distributed_tenant_slot("qa-bulkhead-release", "photo_session")
        try:
            self.assertIsNotNone(again)
            self.assertEqual(again_slot, 0)
        finally:
            jobs._release_distributed_tenant_slot(again, "qa-bulkhead-release", again_slot)

    def test_exact_job_lock_distinguishes_live_from_stale(self):
        conn, slot = jobs._try_distributed_tenant_slot("qa-bulkhead-job-lock", "campaign_deliver")
        self.assertIsNotNone(conn)
        try:
            self.assertTrue(jobs._acquire_job_execution_lock(conn, 987654321))
            self.assertTrue(jobs._job_execution_lock_is_held(987654321))
        finally:
            jobs._release_distributed_tenant_slot(conn, "qa-bulkhead-job-lock", slot)
        self.assertFalse(jobs._job_execution_lock_is_held(987654321))

    def test_accountless_global_job_has_exact_execution_lock(self):
        conn = jobs._TENANT_SLOT_ENGINE.connect()
        try:
            self.assertTrue(jobs._acquire_job_execution_lock(conn, 987654322))
            self.assertTrue(jobs._job_execution_lock_is_held(987654322))
        finally:
            jobs._release_distributed_tenant_slot(conn, None, None)
        self.assertFalse(jobs._job_execution_lock_is_held(987654322))

    def test_startup_recovery_grace_does_not_steal_fresh_claim(self):
        db = SessionLocal()
        job_id = None
        account = "qa-grace-" + uuid.uuid4().hex[:10]
        try:
            fresh = BackgroundJob(
                kind="thumbnail_sweep", status="running", account_id=account,
                payload_json={"qa": True, "_recovery_count": 0},
                fingerprint="qa-grace-" + uuid.uuid4().hex,
                attempts=500, started_at=datetime.now(timezone.utc),
            )
            db.add(fresh); db.commit(); db.refresh(fresh); job_id = int(fresh.id)
            changed = jobs.recover_interrupted_campaign_jobs()
            db.expire_all()
            after = db.get(BackgroundJob, job_id)
            self.assertEqual(after.status, "running")
            self.assertEqual(int((after.payload_json or {}).get("_recovery_count") or 0), 0)
            self.assertGreaterEqual(changed, 0)
        finally:
            if job_id:
                db.query(BackgroundJob).filter(BackgroundJob.id == job_id).delete(synchronize_session=False)
                db.commit()
            db.close()

    def test_startup_recovery_covers_media_but_skips_live_remote_job(self):
        db = SessionLocal()
        lock_conn = None
        lock_slot = None
        unlocked_id = locked_id = None
        account = "qa-recovery-" + uuid.uuid4().hex[:10]
        try:
            old = datetime.now(timezone.utc) - timedelta(hours=1)
            unlocked = BackgroundJob(
                kind="media_reconcile", status="running", account_id=account,
                payload_json={"qa": True, "_recovery_count": 0}, fingerprint="qa-unlocked-" + uuid.uuid4().hex,
                attempts=999, started_at=old,
            )
            locked = BackgroundJob(
                kind="media_reconcile", status="running", account_id=account + "-locked",
                payload_json={"qa": True}, fingerprint="qa-locked-" + uuid.uuid4().hex,
                attempts=1, started_at=old,
            )
            db.add_all([unlocked, locked]); db.commit()
            db.refresh(unlocked); db.refresh(locked)
            unlocked_id, locked_id = int(unlocked.id), int(locked.id)
            lock_conn, lock_slot = jobs._try_distributed_tenant_slot(locked.account_id, locked.kind)
            self.assertIsNotNone(lock_conn)
            self.assertTrue(jobs._acquire_job_execution_lock(lock_conn, locked_id))

            changed = jobs.recover_interrupted_campaign_jobs()
            db.expire_all()
            unlocked_after = db.get(BackgroundJob, unlocked_id)
            locked_after = db.get(BackgroundJob, locked_id)
            self.assertGreaterEqual(changed, 1)
            self.assertEqual(unlocked_after.status, "queued")
            self.assertEqual(int(unlocked_after.attempts or 0), 999)
            self.assertEqual(int((unlocked_after.payload_json or {}).get("_recovery_count") or 0), 1)
            self.assertEqual(locked_after.status, "running")
        finally:
            jobs._release_distributed_tenant_slot(lock_conn, account + "-locked", lock_slot)
            if unlocked_id or locked_id:
                db.query(BackgroundJob).filter(BackgroundJob.id.in_([x for x in (unlocked_id, locked_id) if x])).delete(synchronize_session=False)
                db.commit()
            db.close()


if __name__ == "__main__":
    unittest.main()
