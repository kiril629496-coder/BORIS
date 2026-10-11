from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app/api/campaigns.py"


def test_campaign_ab_projection_is_read_only():
    src = SOURCE.read_text(encoding="utf-8")
    block = src[src.index('@router.get("/ab-tests")'):src.index("class CampaignABActionBody")]
    assert "optimization_versions" in block
    assert "history_total" in block
    assert "state_map" in block
    assert "avito_ab_learning_memory" not in block
    assert "db.commit()" not in block
    assert "UPDATE storage" not in block
    assert "INSERT INTO storage" not in block


def test_campaign_ab_owner_actions_are_separate_from_read_only_projection():
    src = SOURCE.read_text(encoding="utf-8")
    block = src[src.index("class CampaignABActionBody"):src.index('@router.get("/{campaign_id}")')]
    assert '@router.post("/ab-tests/action")' in block
    assert 'action=="finish"' in block
    assert 'action=="scale"' in block
    assert "confirmation_required" in block
    assert "_ab_finish_managed_feed_item" in block
    assert "feed_send_to_avito" in block
    assert 'scenario_type="ab_scale"' in block


def test_formula_selector_reads_canonical_optimization_history_before_projection():
    src = SOURCE.read_text(encoding="utf-8")
    helper = src[src.index("def _ff_learned_formula"):src.index("def _ff_formula_meta")]
    assert "CampaignItem" in helper
    assert "optimization_versions" in helper
    assert "avito_ab_learning_memory" in helper
    assert helper.index("optimization_versions") < helper.index("avito_ab_learning_memory")
    assert "views < 30 and contacts < 3" in helper


def test_canonical_history_wins_over_legacy_projection():
    from app.api.campaigns import _ff_learned_formula

    class Result:
        def scalar(self):
            return '{"items":[{"copy_formula_id":"PAS","views":1000,"contacts":1}]}'

    class Query:
        def filter(self, *_args):
            return self
        def all(self):
            class Row:
                account_id = "account-a"
                payload_json = '{"copy_formula_id":"EXPERT","optimization_versions":[{"formula_id":"EXPERT","observations":[{"views":100,"contacts":8}]}]}'
            return [Row()]

    class DB:
        def query(self, *_args):
            return Query()
        def execute(self, *_args):
            return Result()

    assert _ff_learned_formula(DB(), "account-a") == "EXPERT"


def test_delivery_blocks_exact_duplicate_images_when_enabled():
    src = SOURCE.read_text(encoding="utf-8")
    validator = src[src.index("def _ff_validate_row"):src.index("def _ff_delivery_snapshot")]
    delivery = src[src.index("def _ff_delivery_snapshot"):src.index("def _campaign_guardian_job_public")]
    assert "images_duplicate" in validator
    assert "unique_images" in validator
    assert "image_unique" in delivery
    assert "image_duplicate_reports" in delivery
    assert "and image_unique and" in delivery


def test_formula_controller_emits_adaptive_modes_and_metadata():
    from app.api.campaigns import _ff_formula_meta

    cold=_ff_formula_meta({"variant":1},"desk")
    winner=_ff_formula_meta({"variant":1,"learned_formula_id":"PAS","formula_memory_source":"campaign_item.optimization_versions"},"desk")
    explore=_ff_formula_meta({"variant":4,"learned_formula_id":"PAS"},"desk")
    assert cold["selection_mode"] == "EXPLORATION"
    assert winner["selection_mode"] == "EXPLOITATION"
    assert explore["selection_mode"] == "CONTROLLED_EXPLORATION"
    for key in ("formula_family","selection_reason","memory_source","memory_version","hypothesis_id","angle","hook_type","proof_type","cta_type"):
        assert key in winner
    assert winner["memory_source"] == "campaign_item.optimization_versions"


def test_target_deficit_and_posting_readiness_are_read_only_and_fail_closed():
    src = SOURCE.read_text(encoding="utf-8")
    block = src[src.index("def _ff_target_readiness_snapshot"):src.index("def _campaign_guardian_job_public")]
    assert "target_active_ads" in block
    assert "current_active" in block
    assert "deficit = max(0, target_active - current_active)" in block
    assert "ready_to_publish" in block
    assert "CONTENT_CAPACITY_EXHAUSTED" in block
    assert "account_scope_pass" in block
    assert "db.commit()" not in block
    assert "feed_send_to_avito" not in block


def test_readiness_carries_feed_final_gates_and_image_source_trace():
    src = SOURCE.read_text(encoding="utf-8")
    block = src[src.index("def _ff_target_readiness_snapshot"):src.index("def _ff_ai_marketer_projection")]
    for key in ("category_pass", "attributes_pass", "image_source_trace", "selected_source", "selection_reason", "fallback_chain", "provenance"):
        assert key in block
    for forbidden in ("db.commit()", "campaign_guardian_enqueue", "owner_activation"):
        assert forbidden not in block


def test_readiness_endpoint_is_account_scoped():
    src = SOURCE.read_text(encoding="utf-8")
    assert '@router.get("/{campaign_id}/readiness")' in src
    endpoint = src[src.index('@router.get("/{campaign_id}/readiness")'):src.index("def _campaign_guardian_job_public")]
    assert "_campaign_or_404(db, campaign_id, allowed)" in endpoint
    assert "target not in allowed" in endpoint
    assert "_ff_target_readiness_snapshot" in endpoint


def test_ai_marketer_projection_is_read_only_and_returns_next_hypothesis_plan():
    src = SOURCE.read_text(encoding="utf-8")
    block = src[src.index("def _ff_ai_marketer_projection"):src.index("def _campaign_guardian_job_public")]
    for key in ("next_best_hypothesis", "hypothesis_type", "expected_signal", "what_to_keep_constant", "what_to_change", "measurement_window", "stop_condition", "fallback_action", "confidence", "evidence_refs"):
        assert key in block
    for key in ("creative_plan", "exploit_count", "explore_count", "formula_distribution", "hypothesis_distribution"):
        assert key in block
    assert "optimization_versions" in block
    assert "db.commit()" not in block
    assert "feed_send_to_avito" not in block


def test_ai_marketer_endpoint_is_account_scoped():
    src = SOURCE.read_text(encoding="utf-8")
    assert '@router.get("/{campaign_id}/ai-marketer")' in src
    endpoint = src[src.index('@router.get("/{campaign_id}/ai-marketer")'):src.index("def _campaign_guardian_job_public")]
    assert "target not in allowed" in endpoint
    assert "_ff_ai_marketer_projection" in endpoint


def test_campaign_list_surfaces_current_feed_reconcile_read_only():
    src = SOURCE.read_text(encoding="utf-8")
    block = src[src.index('@router.get("/list")'):src.index('@router.get("/ab-tests")')]
    assert "CAMPAIGN_CURRENT_FEED_RECONCILE_V1" in block
    assert "CURRENT_FEED_WITHOUT_VISIBLE_CAMPAIGN" in block
    assert "current_feed_count" in block
    assert "superseded_campaign_ids" in block
    assert "db.commit()" not in block
