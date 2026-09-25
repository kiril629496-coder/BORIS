import inspect,unittest
from app.ext_api import recover
class RecoverBusinessReconcileOrder(unittest.TestCase):
 def test_business_reconcile_precedes_scheduler(self):
  src=inspect.getsource(recover.once)
  self.assertIn('BUSINESS_GOAL_RECONCILE_BEFORE_SCHEDULE_V1',src)
  self.assertLess(src.index('biz = _cl.ensure_business_orders()'),src.index('plan = dev.schedule(200)'))
  self.assertEqual(src.count('biz = _cl.ensure_business_orders()'),1)
if __name__=='__main__': unittest.main()
