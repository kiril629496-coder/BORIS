import unittest
from app.services.jobs import _publication_repair_plan

class TargetedPublicationRepairTests(unittest.TestCase):
    def test_description_2017_only_failed_item_is_repairable(self):
        errors=[{"ad_id":"bad","avito_id":1,"status":"rejected","message_code":2017,"message_description":"Нарушения в описании"}]
        out=_publication_repair_plan(errors)
        self.assertEqual(out["state"],"repair_subset_prepare")
        self.assertEqual([x["ad_id"] for x in out["repairable"]],["bad"])
        self.assertEqual(out["retryable_count"],1)
        self.assertEqual(out["excluded_count"],0)

    def test_duplicate_is_terminal_exclusion_not_retry(self):
        errors=[{"ad_id":"dup","status":"blocked","message":"Объявление заблокировано по причине «Товар уже продаётся».","message_code":2014}]
        out=_publication_repair_plan(errors)
        self.assertEqual(out["state"],"no_retry_required")
        self.assertEqual(out["retryable_count"],0)
        self.assertEqual([x["ad_id"] for x in out["non_retryable_exclusions"]],["dup"])
        self.assertEqual(out["non_retryable_exclusions"][0]["next_action"],"exclude_from_replay")

    def test_generic_rejection_never_blindly_retries(self):
        errors=[{"ad_id":"unknown","status":"rejected","message":"Объявление отклонено"}]
        out=_publication_repair_plan(errors)
        self.assertEqual(out["state"],"diagnose_subset")
        self.assertEqual(out["retryable_count"],0)
        self.assertEqual([x["ad_id"] for x in out["needs_diagnosis"]],["unknown"])

    def test_mixed_success_is_not_part_of_repair_input(self):
        errors=[{"ad_id":"bad-only","status":"rejected","message_code":2017,"message_description":"Нарушения в описании"}]
        out=_publication_repair_plan(errors)
        ids={x["ad_id"] for bucket in ("repairable","needs_diagnosis","non_retryable_exclusions","waiting_external") for x in out[bucket]}
        self.assertEqual(ids,{"bad-only"})
        self.assertNotIn("successful-sibling",ids)

if __name__ == "__main__": unittest.main()
