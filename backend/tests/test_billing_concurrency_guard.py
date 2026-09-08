import inspect
import unittest
from app.api import billing


class BillingConcurrencyGuardTests(unittest.TestCase):
    def test_usage_consume_is_cross_process_serialized(self):
        src = inspect.getsource(billing.check_and_consume)
        self.assertIn('pg_try_advisory_xact_lock', src)
        self.assertIn('blake2b', src)
        self.assertIn('_check_and_consume_unlocked', src)
        self.assertIn('shared_scope = _owner_key(account_id)', src)
        self.assertIn('invalid_usage_request', src)
        self.assertIn('billing_busy', src)
        self.assertIn('lock_db.commit()', src)
        self.assertIn('lock_db.rollback()', src)

    def test_get_status_does_not_reference_undefined_db_session(self):
        src = inspect.getsource(billing.get_status)
        self.assertIn('_load_billing(account_id)', src)
        self.assertNotIn('_load_billing(account_id, db=db)', src)

    def test_get_status_never_writes_stale_billing_snapshot(self):
        src = inspect.getsource(billing.get_status)
        self.assertNotIn('_save_billing(account_id, billing)', src)
        warn_src = inspect.getsource(billing._check_expiry_warnings)
        self.assertIn('with_for_update()', warn_src)
        self.assertNotIn('_save_billing(account_id, billing)', warn_src)

    def test_locked_consume_reuses_trial_lookup_session(self):
        src = inspect.getsource(billing._check_and_consume_unlocked)
        self.assertIn('_active_user_trial(account_id, db=db)', src)
        trial_src = inspect.getsource(billing._active_user_trial)
        self.assertIn('owned_db = db is None', trial_src)

    def test_provider_io_is_not_inside_billing_lock(self):
        src = inspect.getsource(billing.check_and_consume)
        self.assertNotIn('requests.', src)
        self.assertNotIn('httpx.', src)
        self.assertNotIn('generate_', src)


if __name__ == '__main__':
    unittest.main()
