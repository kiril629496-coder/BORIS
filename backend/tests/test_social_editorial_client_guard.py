import importlib.machinery
import json
import types
import unittest
from pathlib import Path

GUARD="/root/BORIS/backend/run/social_editorial_client_guard.py"
AUTH="/root/BORIS/backend/run/social_editorial_client_authority_v3.json"


def load_guard():
    m=types.ModuleType("social_editorial_client_guard_test")
    importlib.machinery.SourceFileLoader(m.__name__,GUARD).exec_module(m)
    return m


class ClientGuardTest(unittest.TestCase):
    def test_pure_reconcile_repairs_only_authority_fields(self):
        m=load_guard()
        auth=json.loads(Path(AUTH).read_text(encoding="utf-8"))
        projects=[]
        for pid, expected in auth["projects"].items():
            p={"id":pid,"unrelated":"keep-me",**expected}
            projects.append(p)
        by_input={x["id"]:x for x in projects}
        by_input["a374ced25745"]["footer_signature"]="BROKEN"
        by_input["a374ced25745"]["allow_hashtags"]=False
        by_input["f938caa3a944"]["contact_tg"]=""
        by_input["270dbf472f7f"]["contact_vk"]="unexpected-value"
        fixed,changed,missing=m.reconcile_projects(projects,auth)
        self.assertFalse(missing)
        by={x["id"]:x for x in fixed}
        for pid, expected in auth["projects"].items():
            for key,value in expected.items():
                self.assertEqual(by[pid].get(key),value)
            self.assertEqual(by[pid]["unrelated"],"keep-me")
        self.assertGreaterEqual(len(changed),4)

    def test_missing_project_is_reported_not_created(self):
        m=load_guard()
        auth=json.loads(Path(AUTH).read_text(encoding="utf-8"))
        fixed,changed,missing=m.reconcile_projects([],auth)
        self.assertEqual(fixed,[])
        self.assertEqual(changed,[])
        self.assertEqual(len(missing),3)


if __name__=="__main__":
    unittest.main()
