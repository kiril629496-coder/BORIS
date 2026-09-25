import json, unittest
from types import SimpleNamespace
from scripts.storage_singleton_reconcile import _merge_list_rows

class StorageSingletonReconcileTest(unittest.TestCase):
    def test_kpi_history_merges_without_losing_distinct_events(self):
        a={"ts":"1","action":"a"}; b={"ts":"2","action":"b"}
        rows=[SimpleNamespace(value=json.dumps([a,b])),SimpleNamespace(value=json.dumps([a]))]
        out=json.loads(_merge_list_rows("kpi_action_history",rows))
        self.assertEqual([x["action"] for x in out],["a","b"])
    def test_templates_keep_richer_same_id(self):
        rows=[SimpleNamespace(value=json.dumps([{"id":1,"name":"base"},{"id":2,"name":"rich","description":"long"}])),SimpleNamespace(value=json.dumps([{"id":1,"name":"base"}]))]
        out=json.loads(_merge_list_rows("templates",rows))
        self.assertEqual({x["id"] for x in out},{1,2})
        self.assertEqual(next(x for x in out if x["id"]==2)["description"],"long")
if __name__=='__main__': unittest.main()
