import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api import campaigns as C


class _DB:
    pass


class CampaignPaidTextGuardTests(unittest.TestCase):
    def _base(self, rows):
        return (
            patch.object(C, "_allowed_accounts", return_value={"acc-a"}),
            patch.object(C, "_campaign_or_404", return_value=SimpleNamespace(id=1, default_account_id="acc-a")),
            patch.object(C, "_ff_state_load", return_value=(None, {"item_ids":[r.id for r in rows]})),
            patch.object(C, "_ff_items", return_value=rows),
        )

    def test_empty_selection_never_becomes_all(self):
        rows=[SimpleNamespace(id=i, account_id="acc-a") for i in range(1,6)]
        p1,p2,p3,p4=self._base(rows)
        with p1,p2,p3,p4:
            out=C.ff_workflow_texts(1,C.FFTextBody(item_ids=[]),_DB(),SimpleNamespace(id=1))
        self.assertEqual(out["status"],"selection_required")
        self.assertEqual(out["changed"],[])

    def test_multi_item_requires_idempotency_key(self):
        rows=[SimpleNamespace(id=i, account_id="acc-a") for i in range(1,3)]
        p1,p2,p3,p4=self._base(rows)
        with p1,p2,p3,p4:
            out=C.ff_workflow_texts(1,C.FFTextBody(item_ids=[1,2]),_DB(),SimpleNamespace(id=1))
        self.assertEqual(out["status"],"idempotency_required")
        self.assertEqual(out["requested"],2)

    def test_large_batch_stops_before_generation(self):
        rows=[SimpleNamespace(id=i, account_id="acc-a") for i in range(1,22)]
        p1,p2,p3,p4=self._base(rows)
        with p1,p2,p3,p4:
            out=C.ff_workflow_texts(1,C.FFTextBody(item_ids=list(range(1,22)),idempotency_key="job-1"),_DB(),SimpleNamespace(id=1))
        self.assertEqual(out["status"],"paid_confirmation_required")
        self.assertEqual(out["requested"],21)
        self.assertEqual(out["normal_paid_cap"],20)


if __name__ == "__main__":
    unittest.main()


def test_qa_campaigns_force_zero_paid_local_text_path():
    import inspect
    src=inspect.getsource(C.ff_workflow_texts)
    assert "_qa_local_only=_ff_paid_qa_forbidden(campaign)" in src
    assert "_use_local_text=bool(_qa_local_only or" in src
    assert "if _ff_paid_qa_forbidden(campaign): return _ff_paid_qa_response()" not in src
