import unittest
from unittest.mock import patch

import app.reactivation_runtime as rr


class _EmptyResult:
    def fetchall(self):
        return []


class _DB:
    def __init__(self):
        self.rollbacks = 0
    def execute(self, *args, **kwargs):
        return _EmptyResult()
    def rollback(self):
        self.rollbacks += 1


class ReactivationStaleReadyTests(unittest.TestCase):
    def test_process_runs_stale_ready_cleanup_before_candidate_generation(self):
        db=_DB()
        order=[]
        def stale(*args,**kwargs):
            order.append("stale")
            return {"cancelled":3,"replied":1,"manager_taken_over":2,"scanned":3}
        with patch.object(rr,"reconcile_sent_receipts",side_effect=lambda db:0),              patch.object(rr,"recover_stale_sending",side_effect=lambda db:0),              patch.object(rr,"reconcile_delivery_unknown",side_effect=lambda db: {"fixed":0,"unresolved":0,"scanned":0}),              patch.object(rr,"reconcile_stale_ready_messages",side_effect=stale),              patch.object(rr,"reconcile_client_replies",side_effect=lambda db: order.append("replies") or 0),              patch.object(rr,"reconcile_auto_activation_backlog",side_effect=lambda db,limit=10: {"checked":0,"revalidated":0,"invalidated":0,"sync_failed":0,"safety_blocked":0}),              patch.object(rr,"start_due_cooldowns",side_effect=lambda db:0):
            out=rr.process(db,generate_limit=0,send_limit=0)
        self.assertEqual(order,["stale","replies"])
        self.assertEqual(out["stale_ready_cancelled"],3)
        self.assertEqual(out["stale_ready_replied"],1)
        self.assertEqual(out["stale_ready_manager_taken_over"],2)
        self.assertEqual(out["sent"],0)
        self.assertEqual(out["generated"],0)
        self.assertEqual(out["errors"],0)

    def test_outgoing_dialog_change_is_terminal_manager_takeover_contract(self):
        src=open("/root/BORIS/backend/app/reactivation_runtime.py",encoding="utf-8").read()
        self.assertIn("dialog_changed_manager_takeover",src)
        self.assertIn('"manager_taken_over"',src)
        self.assertIn('"manager_takeover"',src)
        self.assertIn("latest.created_at > m.created_at",src)


if __name__=="__main__":
    unittest.main()
