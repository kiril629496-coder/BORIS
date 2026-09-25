import unittest
from datetime import datetime
import posting_runner as p
class T(unittest.TestCase):
 def test_generation_key_deadline_is_slot_plus_review_window(self):
  rec={'generation_key':'social-slot:u2:p1:20260905_18'}
  proj={'mode':'moderate','auto_publish_hours':3}
  self.assertEqual(p._scheduled_record_delivery_due_local(rec,proj),datetime(2026,9,5,21,0))
 def test_auto_mode_has_no_review_delay(self):
  rec={'generation_key':'social-slot:u2:p1:20260905_18'}
  proj={'mode':'auto','auto_publish_hours':3}
  self.assertEqual(p._scheduled_record_delivery_due_local(rec,proj),datetime(2026,9,5,18,0))
