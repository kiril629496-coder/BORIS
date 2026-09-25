import unittest
from unittest.mock import patch
from app.ext_api import a2a

class BusinessTerminalCreateFenceTests(unittest.TestCase):
    def test_shared_create_boundary_reuses_terminal_receipt(self):
        job={'id':619,'dev_job_id':'dev_619','current_phase':'FIXING'}
        with patch.object(a2a.dev,'get',return_value=job), \
             patch.object(a2a.dev,'business_goal_terminal_job_ids',return_value={619}), \
             patch.object(a2a.db,'one',return_value={'id':92131}) as one, \
             patch.object(a2a,'get',return_value={'order_id':92131,'status':'cancelled','blocked_reason':'business_goal_already_confirmed'}) as get:
            out=a2a.create_order('k','dev_619','FIXING','should not recreate',acceptance=['done'])
        self.assertEqual(out['order_id'],92131)
        self.assertEqual(out['blocked_reason'],'business_goal_already_confirmed')
        self.assertIn("blocked_reason='business_goal_already_confirmed'",one.call_args.args[0])
        get.assert_called_once_with(92131)

    def test_cancelled_job_cancels_latest_open_order_instead_of_recreating(self):
        job={'id':1008,'dev_job_id':'dev_1008','current_phase':'PLANNING','status':'cancelled'}
        with patch.object(a2a.dev,'get',return_value=job), \
             patch.object(a2a.db,'one',side_effect=[None,{'id':92302,'status':'queued'}]) as one, \
             patch.object(a2a,'_set') as setter, \
             patch.object(a2a,'message') as msg, \
             patch.object(a2a,'get',return_value={'order_id':92302,'status':'cancelled'}) as get:
            out=a2a.create_order('k','dev_1008','PLANNING','do not recreate',acceptance=['done'])
        self.assertEqual(out['order_id'],92302)
        self.assertEqual(one.call_count,2)
        setter.assert_called_once()
        self.assertEqual(setter.call_args.kwargs['status'],a2a.CANCELLED)
        msg.assert_called_once()
        get.assert_called_once_with(92302)

    def test_terminal_job_without_history_fails_closed(self):
        job={'id':2000,'dev_job_id':'dev_2000','current_phase':'PLANNING','status':'failed'}
        with patch.object(a2a.dev,'get',return_value=job), \
             patch.object(a2a.db,'one',side_effect=[None,None]):
            with self.assertRaises(a2a.ApiError):
                a2a.create_order('k','dev_2000','PLANNING','do not recreate',acceptance=['done'])

    def test_reconcile_terminal_dev_orders_cancels_open_orders(self):
        rows=[{'order_id':77,'order_status':'running','dev_job_id':10,'job_status':'cancelled'}]
        with patch.object(a2a.db,'rows',return_value=rows) as rows_call, \
             patch.object(a2a,'_set') as setter, \
             patch.object(a2a,'message') as msg:
            out=a2a.reconcile_terminal_dev_orders(50)
        self.assertEqual(out['count'],1)
        self.assertEqual(out['healed'][0]['to'],a2a.CANCELLED)
        setter.assert_called_once()
        self.assertEqual(setter.call_args.kwargs['claimed_by'],None)
        msg.assert_called_once()
        child_states=rows_call.call_args.kwargs['st']
        self.assertIn(a2a.BLOCKED_INFRA,child_states)
        self.assertIn(a2a.BLOCKED_HUMAN,child_states)

    def test_terminal_completed_job_cancels_stale_blocked_infra_order(self):
        rows=[{'order_id':78,'order_status':'blocked_infra','dev_job_id':11,'job_status':'completed'}]
        with patch.object(a2a.db,'rows',return_value=rows), \
             patch.object(a2a,'_set') as setter, \
             patch.object(a2a,'message'):
            out=a2a.reconcile_terminal_dev_orders(50)
        self.assertEqual(out['count'],1)
        self.assertEqual(out['healed'][0]['from'],'blocked_infra')
        self.assertEqual(setter.call_args.kwargs['status'],a2a.CANCELLED)
        self.assertEqual(
            setter.call_args.kwargs['blocked_reason'],
            'terminal_dev_job_completed',
        )

if __name__=='__main__': unittest.main()
