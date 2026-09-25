import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.api.control_plane import workspace_programming_journal
class ProgrammingJournalTruthTests(unittest.TestCase):
 def test_waiting_count_is_global_and_waiting_is_zero_progress(self):
  job={"id":1,"title":"queued work","status":"waiting","priority":10,"assigned_agent":None,"wait_reason":"WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically","created_at":None,"updated_at":None}
  def rows(sql,**kw):
   if "FROM ext_dev_jobs" in sql and "GROUP BY status" not in sql:return [job]
   if "FROM ext_dev_jobs GROUP BY status" in sql:return [{"status":"waiting","n":73},{"status":"completed","n":6}]
   if "FROM ext_a2a_orders GROUP BY status" in sql:return [{"status":"queued","n":59}]
   if "FROM ext_a2a_orders" in sql:return []
   raise AssertionError(sql)
  def one(sql,**kw):
   if "WAITING_FREE_CODING_PROVIDER" in sql:return {"n":73}
   raise AssertionError(sql)
  with patch("app.services.platform_roles.is_private_platform_owner",return_value=True),patch("app.ext_api.db.rows",side_effect=rows),patch("app.ext_api.db.one",side_effect=one):r=workspace_programming_journal(limit=20,user=SimpleNamespace(role="owner"))
  self.assertEqual(r["summary"]["waiting_free_provider"],73);self.assertEqual(r["summary"]["executing_now"],0);self.assertEqual(r["jobs"][0]["progress_percent"],0);self.assertEqual(r["jobs"][0]["progress_truth"],"not_executing")
if __name__=="__main__":unittest.main()
