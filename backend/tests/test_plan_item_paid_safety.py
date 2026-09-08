import inspect
import unittest

from app.api import plan_items as P


class PlanItemPaidSafetyTests(unittest.TestCase):
    def test_planner_routes_paid_text_through_shared_exactly_once_guard(self):
        src = inspect.getsource(P.execute_plan_item)
        self.assertIn("_ff_guarded_openai_response", src)
        self.assertIn("plan-item:", src)
        self.assertIn('account_id=str(item.account_id or "")', src)
        self.assertNotIn("for _a in range(attempts)", src)
        self.assertNotIn("прокси-попытка", src)

    def test_planner_has_no_hidden_second_routing_provider_attempt(self):
        src = inspect.getsource(P.execute_plan_item)
        self.assertIn("range(1, 2)", src)
        self.assertIn("остановлен без скрытого повтора", src)
        self.assertNotIn("range(1, 3)", src)

    def test_planner_banner_action_uses_canonical_avito_safe_visual_path(self):
        src = inspect.getsource(P.execute_plan_item)
        self.assertIn("create_avito_safe_banner", src)
        self.assertNotIn('api/banners/full_ai", json=payload', src)
        self.assertIn("Never call legacy", src)

    def test_product_description_enrichment_has_no_outer_paid_retry_loop(self):
        from app.api import tasks as T
        src = inspect.getsource(T._run_enrich_parsed_descriptions)
        self.assertIn("остановлена без скрытого платного повтора", src)
        self.assertNotIn("for attempt in range(3)", src)
        self.assertNotIn("5 * (2 ** attempt)", src)

    def test_listing_draft_billing_is_fail_closed_and_checks_atomic_consume_result(self):
        src = inspect.getsource(P.execute_plan_item)
        self.assertIn("candidate_drafts = list(new_drafts[:_allowed_count])", src)
        self.assertIn("if not _chk.get(\"allowed\", False)", src)
        self.assertIn("listings check failed closed", src)
        self.assertNotIn("listings check failed (пропускаем, не блокируем)", src)
        self.assertNotIn("raise StopIteration  # пропускаем весь блок урезания партии", src)

    def test_listing_draft_tariff_consume_has_stable_business_idempotency(self):
        src = inspect.getsource(P.execute_plan_item)
        self.assertIn("plan-listings:{item.id}:{_step_index}:{batch_id}:{len(candidate_drafts)}", src)
        self.assertIn("idempotency_key=_listing_intent_key", src)

    def test_listing_draft_save_is_atomic_append_not_self_http_after_charge(self):
        src = inspect.getsource(P.execute_plan_item)
        self.assertIn("PLAN_DRAFT_APPEND_ATOMIC_V2", src)
        self.assertIn("_append_plan_drafts(item.account_id, new_drafts)", src)
        self.assertIn('"id": f"draft-{item.id}-a{city_idx}"', src)
        self.assertIn('"id": f"draft-{item.id}-b{city_idx}"', src)
        self.assertNotIn('save_resp = _httpx.post("http://127.0.0.1:8000/api/avito/drafts"', src)


if __name__ == "__main__":
    unittest.main()
