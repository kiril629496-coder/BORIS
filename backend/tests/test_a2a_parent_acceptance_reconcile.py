import unittest
from pathlib import Path
from unittest.mock import patch

from app.ext_api import a2a


class ParentAcceptanceReconcileTest(unittest.TestCase):
    def _job(self, criteria):
        return {
            "id": 42,
            "dev_job_id": "dev_42",
            "acceptance_criteria": [
                {"text": text, "done": done, "evidence": None}
                for text, done in criteria
            ],
        }

    def _order(self, acceptance, evidence, *, status=a2a.ACCEPTED, scope=None):
        return {
            "order_id": 7001,
            "dev_job_id": "dev_42",
            "status": status,
            "scope": scope or {},
            "acceptance_criteria": acceptance,
            "last_evidence": {"acceptance_criteria": evidence, "evidence": "overall"},
        }

    def test_deterministic_id_is_stable_and_whitespace_normalized(self):
        a = a2a._criterion_id(42, "Критерий   один")
        b = a2a._criterion_id("dev_42", "Критерий один")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("ac_"))

    def test_exact_pass_with_own_evidence_closes_parent_and_persists_id(self):
        job = self._job([("Критерий один", False), ("Критерий два", False)])
        links = a2a._parent_acceptance_links(job, ["Критерий один", "Критерий два"])
        order = self._order(
            ["Критерий один", "Критерий два"],
            [
                {"criterion": "Критерий один", "status": "PASS", "evidence": "proof-1"},
                {"criterion": "Критерий два", "status": "NOT_CHECKED", "evidence": ""},
            ],
            scope={"_parent_acceptance_links": links},
        )
        current = [job, self._job([("Критерий один", False), ("Критерий два", False)])]
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get", side_effect=current),
            patch.object(a2a.dev, "mark_criteria") as mark,
            patch.object(a2a, "_set") as aset,
            patch.object(a2a, "message") as msg,
            patch.object(a2a.auth, "actor_of", return_value={"name": "lead"}),
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        mark.assert_called_once()
        self.assertEqual(mark.call_args.args[2], 0)
        self.assertEqual(mark.call_args.args[3], True)
        self.assertEqual(mark.call_args.args[4], "proof-1")
        self.assertEqual(out["closed_indices"], [0])
        self.assertEqual(len(out["criterion_ids"]), 1)
        self.assertIn("criterion_id", aset.call_args.kwargs["last_evidence_json"])
        msg.assert_called_once()

    def test_fuzzy_rephrase_never_closes_parent(self):
        job = self._job([("Точный критерий", False)])
        order = self._order(
            ["Точный критерий"],
            [{"criterion": "Точный критерий с добавкой", "status": "PASS", "evidence": "proof"}],
        )
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get", return_value=job),
            patch.object(a2a.dev, "mark_criteria") as mark,
            patch.object(a2a, "_set"),
            patch.object(a2a, "message"),
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        mark.assert_not_called()
        self.assertEqual(out["closed_indices"], [])
        self.assertIn("no_exact_unique_parent_link", str(out["skipped"]))

    def test_duplicate_parent_text_fails_closed(self):
        job = self._job([("Одинаково", False), ("Одинаково", False)])
        order = self._order(
            ["Одинаково"],
            [{"criterion": "Одинаково", "status": "PASS", "evidence": "proof"}],
        )
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get", return_value=job),
            patch.object(a2a.dev, "mark_criteria") as mark,
            patch.object(a2a, "_set"),
            patch.object(a2a, "message"),
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        mark.assert_not_called()
        self.assertEqual(out["closed_indices"], [])

    def test_pass_without_criterion_evidence_never_closes(self):
        job = self._job([("Критерий", False)])
        order = self._order(
            ["Критерий"],
            [{"criterion": "Критерий", "status": "PASS", "evidence": ""}],
        )
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get", return_value=job),
            patch.object(a2a.dev, "mark_criteria") as mark,
            patch.object(a2a, "_set"),
            patch.object(a2a, "message"),
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        mark.assert_not_called()
        self.assertIn("missing_evidence", str(out["skipped"]))

    def test_non_accepted_order_never_closes(self):
        order = self._order(
            ["Критерий"],
            [{"criterion": "Критерий", "status": "PASS", "evidence": "proof"}],
            status=a2a.REVIEW,
        )
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get") as dget,
            patch.object(a2a.dev, "mark_criteria") as mark,
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        dget.assert_not_called()
        mark.assert_not_called()
        self.assertIn("order_not_accepted", out["skipped"])

    def test_already_done_is_idempotent(self):
        job = self._job([("Критерий", True)])
        order = self._order(
            ["Критерий"],
            [{"criterion": "Критерий", "status": "PASS", "evidence": "proof"}],
        )
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get", return_value=job),
            patch.object(a2a.dev, "mark_criteria") as mark,
            patch.object(a2a, "_set"),
            patch.object(a2a, "message") as msg,
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        mark.assert_not_called()
        msg.assert_not_called()
        self.assertEqual(out["matched_indices"], [0])
        self.assertEqual(out["closed_indices"], [])

    def test_wrong_criterion_id_fails_closed(self):
        job = self._job([("Критерий", False)])
        links = a2a._parent_acceptance_links(job, ["Критерий"])
        order = self._order(
            ["Критерий"],
            [{"criterion": "Критерий", "criterion_id": "ac_wrong",
              "status": "PASS", "evidence": "proof"}],
            scope={"_parent_acceptance_links": links},
        )
        with (
            patch.object(a2a, "get", return_value=order),
            patch.object(a2a.dev, "get", return_value=job),
            patch.object(a2a.dev, "mark_criteria") as mark,
            patch.object(a2a, "_set"),
            patch.object(a2a, "message"),
        ):
            out = a2a.reconcile_parent_acceptance({"actor_name": "lead"}, 7001)
        mark.assert_not_called()
        self.assertEqual(out["closed_indices"], [])
        self.assertIn("criterion_id_mismatch", str(out["skipped"]))

    def test_return_with_fixes_does_not_reconcile_parent(self):
        order = {
            "order_id": 7001,
            "dev_job_id": "dev_42",
            "status": a2a.REVIEW,
            "iteration": 0,
            "max_iterations": 6,
        }
        returned = dict(order, status=a2a.RETURNED, iteration=1)
        with (
            patch.object(a2a, "get", side_effect=[order, returned]),
            patch.object(a2a, "_set"),
            patch.object(a2a, "message"),
            patch.object(a2a.auth, "actor_of", return_value={"name": "lead"}),
            patch.object(a2a, "reconcile_parent_acceptance") as reconcile,
        ):
            out = a2a.review(
                {"actor_name": "lead"}, 7001, "RETURN_WITH_FIXES",
                fixes=["criterion still failing"]
            )
        self.assertEqual(out["status"], a2a.RETURNED)
        reconcile.assert_not_called()

    def test_lead_has_no_fuzzy_60_char_fallback(self):
        src = Path("app/ext_api/lead.py").read_text(encoding="utf-8")
        block = src[src.index("def _close_criteria"):src.index("def _next_phase")]
        self.assertIn("reconcile_parent_acceptance", block)
        self.assertNotIn("[:60]", block)
        self.assertNotIn(" in text", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
