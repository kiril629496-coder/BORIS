import unittest, inspect
import app.reactivation_runtime as rr
class ReadyTransportBlockTests(unittest.TestCase):
    def test_source_has_no_provider_or_send_call(self):
        src=inspect.getsource(rr.reconcile_ready_transport_blocks)
        self.assertIn('chat_transport_blocked',src)
        self.assertIn("'transport_unavailable'",src)
        self.assertNotIn('sync_chat_messages(',src)
        self.assertNotIn('fetch_chat_messages(',src)
        self.assertNotIn('send_message(',src)
        self.assertNotIn('generate(',src)
    def test_process_preblocks_before_transport_recovery(self):
        src=inspect.getsource(rr.process)
        self.assertLess(src.index('reconcile_ready_transport_blocks'),src.index('reconcile_transport_unavailable'))
        self.assertIn('transport_preblocked',src)
        pre=inspect.getsource(rr.reconcile_ready_transport_blocks); rec=inspect.getsource(rr.reconcile_transport_unavailable)
        self.assertIn('_mop_active',pre); self.assertIn('skipped_inactive',pre); self.assertIn('_mop_active',rec); self.assertIn('skipped_inactive',rec)
if __name__=='__main__': unittest.main()
