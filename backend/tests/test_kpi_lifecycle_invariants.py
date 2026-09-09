from pathlib import Path
ROOT=Path('/root/BORIS/backend')

def _src(name):
    return (ROOT/name).read_text(encoding='utf-8')

def test_goal_tick_releases_db_before_nested_controllers():
    s=_src('app/api/avito.py')
    assert s.count('KPI_GOAL_SESSION_BOUNDARY_V1') >= 12
    assert 'db.close()  # KPI_GOAL_SESSION_BOUNDARY_V1\n                result = kpi_proposal_apply_next' in s
    assert 'db.close()  # KPI_GOAL_SESSION_BOUNDARY_V1\n                result = kpi_confirm_rollback' in s

def test_live_confirmation_releases_controller_session():
    s=_src('app/api/avito.py')
    assert s.count('KPI_CONFIRM_SESSION_ISOLATION_V1') >= 2
    assert 'db.close()\n        live = _kpi_fetch_live_item' in s

def test_rollback_is_field_scoped_not_full_snapshot_replace():
    s=_src('app/api/avito.py')
    assert 'KPI_FIELD_SCOPED_ROLLBACK_V2' in s
    block=s[s.index('KPI_FIELD_SCOPED_ROLLBACK_V2'):s.index('ROLLBACK_MEDIA_SCOPE_V2')]
    assert 'restored = dict(_item)' in block
    assert 'restored = dict(snapshot)' not in block

def test_mapping_wait_is_bounded_and_orphans_terminalize():
    s=_src('app/api/avito.py')
    assert 'KPI_MAPPING_BOUNDED_V2' in s
    assert 'mapping_unresolvable' in s
    assert 'nonpublishable_live_import_without_official_feed_pair' in s
    assert '_attempts >= 6' in s

def test_template_title_live_proof_accepts_only_declared_variant():
    import sys
    sys.path.insert(0,str(ROOT))
    from app.api.avito import _kpi_title_matches_expected
    assert _kpi_title_matches_expected('{A|B|C}','B') is True
    assert _kpi_title_matches_expected('{A|B|C}','D') is False
    assert _kpi_title_matches_expected('A','A') is True
    assert _kpi_title_matches_expected('A','B') is False

def test_publish_wait_has_bounded_retry_and_stall():
    s=_src('app/api/avito.py')
    assert 'STALE_PUBLISH_AUTORETRY_V1' in s
    assert 'publish_retry_count' in s
    assert 'avito_publish_not_applying_after_bounded_retries' in s


def test_goal_met_requires_cpl_inside_owner_red_line():
    s=_src('app/api/avito.py')
    assert 'KPI_GOAL_MET_ECONOMICS_GUARD_V1' in s
    start=s.index('KPI_GOAL_MET_ECONOMICS_GUARD_V1')
    end=s.index('if active:', start)
    block=s[start:end]
    assert 'check.get("cost_per_lead_today")' in block
    assert 'check.get("max_cost_per_lead_rub")' in block
    assert 'and not _cpl_over_red' in block
    assert '_actual_cpl > _max_cpl + 1e-9' in block


def test_planner_goal_met_does_not_hide_red_cpl_breach():
    s=_src('app/api/avito.py')
    assert 'KPI_PLAN_GOAL_MET_ECONOMICS_GUARD_V1' in s
    start=s.index('KPI_PLAN_GOAL_MET_ECONOMICS_GUARD_V1')
    end=s.index('# FACTORY_G_RUNTIME_TIME_V1', start)
    block=s[start:end]
    assert 'check.get("cost_per_lead_today")' in block
    assert 'and not _goal_cpl_over_red' in block
    assert '_goal_actual_cpl > max_cpl + 1e-9' in block


def test_prepare_mapping_preflight_self_heals_exactly_once_per_item():
    s=_src('app/api/avito.py')
    assert 'KPI_PREPARE_MAPPING_SELF_HEAL_V1' in s
    start=s.index('KPI_PREPARE_MAPPING_SELF_HEAL_V1')
    end=s.index('# Learning / concurrency guard.', start)
    block=s[start:end]
    assert '_recover_canonical_identities_batch_from_autoload' in block
    assert '_mapping_recovery_attempted_ids_pre' in block
    assert 'set(_unpublishable_mapping_ids) - _mapping_recovery_attempted_ids_pre' in block
    assert 'mapping_recovery_attempted' in block
    assert 'fuzzy' in block.lower()


def test_mutation_candidates_require_live_db_publication_proof():
    s=_src('app/api/avito.py')
    assert 'KPI_CANONICAL_MUTATION_AUTHORITY_V3' in s
    start=s.index('KPI_CANONICAL_MUTATION_AUTHORITY_V3')
    end=s.index('# KPI_TITLE_COHORT_BATCH_ROTATION_V1 compatibility contract', start)
    block=s[start:end]
    assert 'is_canonical_writable as _is_mutation_writable' in block
    assert 'len(_rows)==1 and _is_mutation_writable(_rows[0])' in block
    assert '_mutation_weak_items=list(canonical_weak_items)' in block
    # Unmapped items may be proposed only to the prepare preflight; the existing
    # exact mapping recovery must prove them before AI or publication.
    assert 'KPI_CONTENT_PREPARE_MAPPING_RECOVERY_CANDIDATES_V1' in block
    assert '_prepare_weak_items=list(weak_items)' in block
    planner=s[start:s.index('# ---------------------------------------------------------\n        # 7. Сортировка', start)]
    assert 'candidate_items=_prepare_weak_items[:_content_cohort_size]' in planner
    assert '_mutation_weak_items' in planner
    selection=s[s.index('KPI_ACTIVE_EXPERIMENT_PLAN_EXCLUSION_V1', start):]
    assert 'for _extra in _prepare_weak_items' in selection
    prepare=s[s.index('KPI_PREPARE_MAPPING_SELF_HEAL_V1'):]
    assert '_recover_canonical_identities_batch_from_autoload' in prepare
    assert 'items = [x for x in items if str(x.get("id")) in _publishable_avito_ids_pre]' in prepare


def test_daily_money_journal_does_not_duplicate_content_hypotheses():
    s=_src("app/services/marketing_experiment_journal.py")
    assert "WS_AVITO_MONEY_JOURNAL_SCOPE_V1" in s
    assert "WS-AVITO-MONEY" in s
    assert "content/apply history is intentionally" in s
    assert "Content experiments are owned by WS-AVITO-CONTENT" in s

def test_title_winner_memory_is_evidence_only():
    s=_src('app/api/avito.py')
    assert 'KPI_TITLE_WINNER_MEMORY_V1' in s
    start=s.index('KPI_TITLE_WINNER_MEMORY_V1')
    block=s[start:start+1800]
    assert 'contacts_per_day_improved_after_complete_observation_window' in block
    assert 'similar_proven_items_only_after_independent_weak_item_evidence' in block


def test_profitable_day_push_continues_after_owner_target_when_economics_good():
    s=_src('app/api/cpx_advisor.py')
    assert 'PROFITABLE_DAY_CONTINUE_AFTER_TARGET_OWNER_RULE_V1' in s
    start=s.index('PROFITABLE_DAY_CONTINUE_AFTER_TARGET_OWNER_RULE_V1')
    block=s[start:start+1400]
    assert 'daily_kpi_already_met' not in block
    assert 'cpl>red*0.80' in block
    assert 'spent>=budget*0.90' in block


def test_goal_auto_keeps_guarded_bid_autopilot_enabled():
    s=_src('app/api/avito.py')
    assert 'AUTOPILOT_GOAL_AUTO_BID_SYNC_V1' in s
    start=s.index('AUTOPILOT_GOAL_AUTO_BID_SYNC_V1')
    block=s[start:start+700]
    assert 'mode in {"always_auto", "goal_auto"}' in block


def test_paid_boris_tariff_can_finish_existing_effect_lifecycle():
    s=_src('app/api/avito.py')
    assert 'ACTIVE_BORIS_TARIFF_EFFECT_RESOLVE_V1' in s
    start=s.index('ACTIVE_BORIS_TARIFF_EFFECT_RESOLVE_V1')
    block=s[start:start+1200]
    assert '_paid_boris_tariff' in block
    assert 'mode != "goal_auto" and not _paid_boris_tariff' in block


def test_owner_growth_top10_step10_cumulative70_contract():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_WINNER_PORTFOLIO_TOP10_V1" in s
    assert ")[:10]" in s[s.index("KPI_WINNER_PORTFOLIO_TOP10_V1"):s.index("KPI_WINNER_SCALE_HOURLY_IDEMPOTENCY_V1")]
    block=s[s.index("KPI_WINNER_CUMULATIVE_70_V1"):s.index("KPI_WINNER_SCALE_HOURLY_IDEMPOTENCY_V1")]
    assert "min(10, _requested_step)" in block
    assert "_baseline_bid * 1.70" in block
    assert "winner_cumulative_70_reached" in block


def test_owner_growth_title_active_cohort_at_least_20pct_contract():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_TITLE_ACTIVE_COHORT_EXACT20_V2" in s
    block=s[s.index("KPI_TITLE_ACTIVE_COHORT_EXACT20_V2"):s.index("KPI_RECENT_PUBLICATION_CONTENT_COOLDOWN_V1")]
    assert "_active_items" in block
    assert "_title_active_cohort_min_pct = 20" in block
    assert "ceil(len(_active_items)" in block
    assert "[:_title_cohort_target]" in block


def test_owner_growth_learning_policy_declares_money_contract_without_content_overlap():
    s=(ROOT/"app/services/marketing_experiment_journal.py").read_text(encoding="utf-8")
    assert "WS_AVITO_MONEY_JOURNAL_SCOPE_V1" in s
    assert "winner_portfolio_max" in s and "max_cumulative_bid_growth_pct" in s
    assert "autonomous_bid_write_step_pct_max" in s
    assert "title_active_cohort_min_pct" not in s
    assert "finalized" in s and "winner" in s

def test_owner_priority_rotation_is_not_fixed_prefix_only():
    s=(ROOT/"marketer_rollout_runner.py").read_text(encoding="utf-8")
    # Fixed priority[:N] can starve accounts with equal counters forever.
    assert "priority[:OWNER_PRIORITY_MAX_PER_CYCLE]" not in s
    assert ("last_serv" in s or "cursor" in s or "round_robin" in s)
    assert "checked_at" in s  # durable per-account fallback is already persisted in rollout state


def test_lowviews_zero_bid_is_handed_to_bounded_first_activation_lane():
    s=(ROOT/"marketer_rollout_runner.py").read_text(encoding="utf-8")
    assert "LOWVIEWS_ZERO_BID_HANDOFF_V1" in s
    block=s[s.index("LOWVIEWS_ZERO_BID_HANDOFF_V1")-700:s.index("LOWVIEWS_ZERO_BID_HANDOFF_V1")+1800]
    assert "blocked_bid_delta_unprovable" in block
    assert "bootstrap_new_no_promo" in block
    assert "max_items=1" in block
    # Handoff may only happen after ordinary lowviews produced no live mutation.
    assert "changed_avito" in block


def test_account_money_growth_pauses_when_measurement_backlog_is_excessive():
    s=(ROOT/"app/api/cpx_advisor.py").read_text(encoding="utf-8")
    # All autonomous raise lanes share the same small account-level evidence
    # ceiling. Inventory size must never inflate unresolved paid experiments.
    assert "PROFITABLE_DAY_CANONICAL_MEASUREMENT_CAP_V1" in s
    assert "MEASUREMENT_BACKLOG_LIMIT = MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT" in s
    assert "MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT = 2" in s
    block=s[s.index("def profitable_day_push"):s.index("# PROFITABLE_DAY_ACTIVE_PROMOTION_ONLY_V1")]
    assert "waiting_measurement" in block
    assert "measurement_backlog_limit" in block
    assert "MEASUREMENT_BACKLOG_LIMIT" in block
    assert ">= MEASUREMENT_BACKLOG_LIMIT" in block
    assert "changed_avito" in block and "False" in block
    assert "max(20, _active_today)" not in s


def test_no_signal_timeout_uses_elapsed_complete_days_not_only_item_presence_days():
    s=(ROOT/"app/api/cpx_advisor.py").read_text(encoding="utf-8")
    assert "MEASURE_NO_SIGNAL_ELAPSED_TIMEOUT_V2" in s
    block=s[s.index("MEASURE_NO_SIGNAL_ELAPSED_TIMEOUT_V2")-250:s.index("MEASURE_NO_SIGNAL_ELAPSED_TIMEOUT_V2")+1200]
    assert "MEASURE_NO_SIGNAL_MAX_DAYS" in block
    assert "started_date" in block
    assert "today" in block
    assert "elapsed_complete_days" in block
    assert "inconclusive_no_signal" in block


def test_owner_title_program_runtime_covers_20pct_without_fixed_tiny_batch():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_TITLE_ACTIVE_COHORT_EXACT20_V2" in s
    root=s[s.index("KPI_TITLE_ACTIVE_COHORT_EXACT20_V2"):s.index("KPI_TITLE_ACTIVE_COHORT_EXACT20_V2")+1800]
    assert "_active_items" in root and "ceil(len(_active_items)" in root
    assert "_title_active_cohort_min_pct = 20" in root
    assert "KPI_TITLE_COHORT_BATCH_ROTATION_V1" in s
    _rotation_marker="KPI_TITLE_COHORT_BATCH_ROTATION_V1 compatibility contract"
    plan=s[s.index(_rotation_marker):s.index('KPI_RECENT_PUBLICATION_CONTENT_COOLDOWN_V1', s.index(_rotation_marker))]
    assert "_content_cohort_target = len(_prepare_weak_items)" in plan
    assert "_content_batch_cap = (" in plan
    assert "min(25, _content_remaining_today)" in plan
    assert "if _content_only_plan else 25" in plan
    assert "1 if urgency == \"normal\" else (2 if urgency == \"high\" else 3)" not in s
    exec_start=s.index("def _kpi_exec_optimize_weak_items")
    exec_block=s[exec_start:exec_start+2500]
    assert "weak[:10]" not in exec_block
    assert "for item in weak:" in exec_block


def test_parallel_title_cohort_refills_around_claimed_money_items():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_PARALLEL_TITLE_DISJOINT_REFILL_V1" in s
    i=s.index("KPI_PARALLEL_TITLE_DISJOINT_REFILL_V1")
    block=s[i-250:i+2200]
    assert "_claimed_mutation_items" in block
    assert "_filtered_claimed" in block
    assert "_content_pool" in block
    assert "_content_cohort_size" in block
    assert "excluded_claimed_items" in block
    assert "single_hypothesis_per_item" in block


def test_bad_economics_still_runs_parallel_title_on_disjoint_items():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_CPL_RECOVERY_PARALLEL_TITLE_V1" in s
    i=s.index("KPI_CPL_RECOVERY_PARALLEL_TITLE_V1")
    block=s[i-700:i+1800]
    assert '"reduce_cpl", 110' in block
    assert '"optimize_weak_items", 105' in block
    assert '_cpl_content_pool' in block
    assert 'requires_budget=False' in block
    assert '_content_cohort_size' in block
    assert "KPI_PARALLEL_TITLE_DISJOINT_REFILL_V1" in s


def test_published_item_observation_does_not_serialize_disjoint_title_cohort():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_PUBLISHED_OBSERVATION_PARALLEL_PREPARE_V1" in s
    assert "parallel_prepare" in s
    assert "exclude_active_item_ids" in s


def test_title_cohort_refills_from_exact_writable_active_capacity_and_reports_gap():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_TITLE_WRITABLE_CAPACITY_REFILL_V1" in s
    assert "title_cohort_capacity_gap" in s
    assert "title_exact_writable_active" in s
    assert "_canonical_active_refill" in s


def test_autoload_mapping_recovery_fetches_current_upload_id_when_items_meta_omits_it():
    s=(ROOT/"app/api/avito.py").read_text(encoding="utf-8")
    assert "AUTOLOAD_CURRENT_UPLOAD_ID_FALLBACK_V1" in s
    block=s[s.index("def _recover_canonical_identities_batch_from_autoload"):s.index("def _kpi_promote_campaign_safe_feed")]
    assert '"https://api.avito.ru/autoload/v4/uploads/current"' in block
    assert "_authoritative_upload_id" in block


def test_no_signal_elapsed_timeout_executes_without_datetime_nameerror():
    import sys
    from datetime import datetime, timedelta
    sys.path.insert(0,str(ROOT))
    import app.api.cpx_advisor as c
    old_load,old_collect=c._load_json,c._collect_measure_period
    try:
        c._load_json=lambda *a,**k: {"items": []}
        c._collect_measure_period=lambda *a,**k: {"views":0,"contacts":0,"days":0,"conversion":None}
        out=c._calculate_measure_effect(object(),"test",123,datetime.now()-timedelta(days=8))
        assert out.get("status")=="measured", out
        assert out.get("effect")=="inconclusive_no_signal", out
    finally:
        c._load_json, c._collect_measure_period=old_load,old_collect


def test_non_money_effect_check_can_prepare_disjoint_content_in_parallel():
    src = Path("app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_NON_MONEY_PARALLEL_PREPARE_DURING_EFFECT_V1" in src
    assert "_parallel_content_actions" in src
    assert "kpi_prepare_cycle(" in src
    assert '"parallel_prepare": _parallel_prepare' in src
    assert "allow_stale_non_money" in src


def test_non_money_effect_check_advances_existing_disjoint_obligation_before_new_work():
    src = Path("app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_NON_MONEY_PARALLEL_EXISTING_OBLIGATION_V1" in src
    i = src.index("KPI_NON_MONEY_PARALLEL_EXISTING_OBLIGATION_V1")
    block = src[i:i+5200]
    assert '"publish_requested": 40' in block
    assert '"feed_applied": 30' in block
    assert '"safe_feed_ready": 20' in block
    assert "kpi_confirm_publish(" in block
    assert "kpi_publish_changes(" in block
    assert "_kpi_promote_campaign_safe_feed(" in block
    assert "if _parallel_lifecycle is None and _parallel_candidates" in block


def test_publish_wait_does_not_repeat_root_cause_analysis():
    src = Path("app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_PUBLISH_WAIT_NO_REPEAT_ANALYSIS_V1" in src
    i = src.index("KPI_PUBLISH_WAIT_NO_REPEAT_ANALYSIS_V1")
    block = src[i-500:i+1800]
    assert '"reason": "publish_wait_no_repeat_analysis"' in block
    assert "kpi_root_cause(account_id)" not in block


def test_first_image_action_preserves_zero_cost_intent_and_no_fake_progress():
    src = Path("app/api/avito.py").read_text(encoding="utf-8")
    assert "KPI_FIRST_IMAGE_INTENT_PRESERVATION_V1" in src
    i = src.index("KPI_FIRST_IMAGE_INTENT_PRESERVATION_V1")
    prep = src[i:i+1800]
    assert 'if action == "test_first_image"' in prep
    assert '"action": "prepare_first_image_test"' in prep
    goal_i = src.index('first_image_actions = [')
    goal = src[goal_i:goal_i+1800]
    assert 'x.get("action") == "test_first_image"' in goal
    assert "actions=first_image_actions" in goal
    assert 'if not (result.get("prepared") or [])' in goal
    assert '"stage": "no_action"' in goal
    accounting_i = src.index("KPI_FIRST_IMAGE_ZERO_AI_ACCOUNTING_V1")
    accounting = src[accounting_i:accounting_i+900]
    assert "ai_estimate_rub=0" in accounting
    assert "3.80 * _visual_test_n" not in accounting

def test_owner_workspace_goal_met_requires_cpl_inside_red_line():
    s=_src('app/api/avito.py')
    assert 'KPI_OWNER_WORKSPACE_GOAL_ECONOMICS_PARITY_V1' in s
    start=s.index('KPI_OWNER_WORKSPACE_GOAL_ECONOMICS_PARITY_V1')
    end=s.index('next_candidate = None', start)
    block=s[start:end]
    assert '_lead_goal_met_now' in block
    assert '_owner_cpl_over_red_now' in block
    assert '_owner_actual_cpl_now > _owner_red_cpl_now + 1e-9' in block
    assert 'goal_met_now = bool(_lead_goal_met_now and not _owner_cpl_over_red_now)' in block
    human=s[s.index('_waiting = groups.get("Ждём Avito")', start):]
    assert 'if _lead_goal_met_now and _owner_cpl_over_red_now:' in human
    assert 'Лиды выполнены' in human
    assert 'не меняет ставки вслепую' in human

def test_mapping_retry_scheduler_alignment_avoids_hourly_edge_miss():
    s=_src('app/api/avito.py')
    assert 'KPI_MAPPING_RETRY_SCHEDULER_ALIGNMENT_V1' in s
    start=s.index('KPI_MAPPING_RETRY_SCHEDULER_ALIGNMENT_V1')
    end=s.index('return {', s.index('payload = {', start))
    block=s[start:end]
    assert 'retry_alignment_seconds = 60 if retry_seconds >= 3600 else 0' in block
    assert 'retry_due_seconds = max(60, retry_seconds - retry_alignment_seconds)' in block
    assert '"next_retry_at": (now + _td_map_recovery(seconds=retry_due_seconds)).isoformat()' in block
    assert '"retry_backoff_seconds": retry_seconds' in block
    assert '"retry_scheduler_alignment_seconds": retry_alignment_seconds' in block


def test_publish_confirm_suppresses_retry_during_exact_avito_2214_budget_hold():
    s=_src('app/api/avito.py')
    assert 'KPI_PUBLISH_2214_BUDGET_HOLD_V1' in s
    assert 'AUTOLOAD_RAW_PROVIDER_ROWS_V1' in s
    i=s.index('KPI_PUBLISH_2214_BUDGET_HOLD_V1')
    block=s[i:i+7000]
    assert 'publish_external_hold' in block
    assert 'avito_advance_required_2214' in block
    assert '_kpi_publish_money_policy' in block
    assert '_kpi_publish_provider_blocker' in block
    assert 'waiting_budget_policy' in block
    assert 'waiting_owner_funds' in block
    assert 'owner_action_required' in block
    # A proven external-funds hold must return before STALE_PUBLISH_AUTORETRY
    # can increment retry_count or send another feed.
    assert s.index('KPI_PUBLISH_2214_BUDGET_HOLD_V1') < s.index('STALE_PUBLISH_AUTORETRY_V1')

def test_owner_workspace_projects_guardian_funding_pause_truth():
    s=_src('app/api/avito.py')
    assert 'KPI_OWNER_WORKSPACE_FUNDING_HEALTH_BRIDGE_V1' in s
    i=s.index('KPI_OWNER_WORKSPACE_FUNDING_HEALTH_BRIDGE_V1')
    block=s[i:s.index('@router.get("/kpi_action_memory")', i)]
    assert 'guardian_balance_funding_health' in block
    assert '_funding_work_pause' in block
    assert '_funding_owner_required' in block
    assert '_funding_suppressed_by_money_guard' in block
    assert 'Пополнить аванс/баланс Avito' in block
    assert '"external_funding_pause"' in block
    assert 'Новых действий владельца не требуется.' in block
    assert 'if _funding_owner_required:' in block


def test_publish_confirm_uses_durable_account_2214_before_ephemeral_current_upload():
    s=_src('app/api/avito.py')
    assert 'KPI_ACCOUNT_DURABLE_2214_BUDGET_HOLD_V1' in s
    assert 'def _kpi_account_durable_2214_budget_hold' in s
    helper_start=s.index('def _kpi_account_durable_2214_budget_hold')
    provider_start=s.index('def _kpi_publish_provider_blocker', helper_start)
    provider_end=s.index('@router.post("/kpi_confirm_publish")', provider_start)
    helper=s[helper_start:provider_start]
    provider=s[provider_start:provider_end]
    # Durable evidence is accepted only while the canonical money guard is red
    # and only when campaign_post_publish_watch confirmed provider code 2214.
    assert '_kpi_publish_money_policy(account_id, db=_db)' in helper
    assert 'bool(_money.get("blocked")) is not True' in helper
    assert 'waiting_budget_policy' in helper
    assert 'restore_provider_code' in helper
    assert '_provider_state.get("confirmed") is True' in helper
    # The local durable guard must run before ephemeral current-upload I/O.
    assert provider.index('_kpi_account_durable_2214_budget_hold(account_id)') < provider.index('_autoload_upload_snapshot(')
    assert 'evidence_source": "durable_account_provider_hold"' in provider


def test_owner_projection_explains_budget_hold_without_making_owner_operator():
    import sys
    sys.path.insert(0,str(ROOT))
    from app.api.avito import _ai_marketing_human_operation
    out=_ai_marketing_human_operation({
        "status":"publish_requested",
        "item_id":"x",
        "reason":"test",
        "effect":{"status":"waiting_budget_policy","decision":"wait_budget_policy_no_republish"},
    })
    assert out["group"] == "Ждём Avito"
    assert "остановил повторные отправки" in out["current_status"]
    assert "сам ждёт снятия ограничения" in out["current_status"]
    assert "сам перепроверяет денежную политику" in out["when_result"]
    assert "Ничего от владельца не требуется" in out["next_step"]
    assert "без повторных отправок" in out["next_step"]
    assert out["result"] == "waiting_budget_policy"

def test_unknown_or_expired_service_period_blocks_new_kpi_mutations_but_keeps_safe_tail():
    s=_src('app/api/avito.py')
    assert 'KPI_GOAL_SERVICE_PERIOD_FAIL_CLOSED_V2' in s
    start=s.index('KPI_GOAL_SERVICE_PERIOD_FAIL_CLOSED_V2')
    end=s.index('KPI_LOW_WALLET_GLOBAL_PAUSE_V1', start)
    block=s[start:end]
    assert '_service_new_work_allowed = _marketing_entitlement_state == "active"' in block
    for status in (
        '"publish_requested"',
        '"published"',
        '"effect_rollback_requested"',
        '"rollback_feed_ready"',
        '"rollback_publish_failed"',
        '"rollback_publish_requested"',
    ):
        assert status in block
    assert '"record_explicit_marketing_paid_period"' in block
    assert '"service_period_unknown"' in block
    assert 'safe_feed_ready' in block
    # New work gates must all share canonical entitlement and the
    # stricter funding boundary. Funding may allow only an existing safe tail.
    fund=s[s.index('KPI_LOW_WALLET_SAFE_TAIL_V1'):s.index('KPI_ADDITIVE_GROWTH_BEFORE_CONTENT_WAIT_V1')]
    assert '_service_new_work_allowed' in fund
    assert '_funding_new_work_allowed = bool(' in fund
    assert 'not _funding_work_pause_kgt' in fund
    assert 'if _funding_new_work_allowed and not _content_only_placement_kgt:' in s[s.index('KPI_ADDITIVE_GROWTH_BEFORE_CONTENT_WAIT_V1'):s.index('NEW_ITEM_NO_PROMO_BOOTSTRAP_V2')]
    bootstrap=s[s.index('NEW_ITEM_NO_PROMO_BOOTSTRAP_V2'):s.index('NEW_ITEM_NO_PROMO_BOOTSTRAP_V2')+2200]
    assert '_funding_new_work_allowed' in bootstrap
    assert 'and not allow_stale_non_money' in bootstrap
    assert 'if allow_stale_non_money and _funding_new_work_allowed:' in s

def test_planner_and_owner_workspace_project_service_period_fail_closed_truth():
    s=_src('app/api/avito.py')
    assert 'KPI_PLANNER_SERVICE_PERIOD_FAIL_CLOSED_V1' in s
    i=s.index('KPI_PLANNER_SERVICE_PERIOD_FAIL_CLOSED_V1')
    block=s[i:i+6000]
    assert '"actions": []' in block
    assert '"execute": False' in block
    assert '"service_period_unknown"' in block
    assert '"owner_action_required"' in block
    assert 'record_explicit_marketing_paid_period' in block
    assert 'KPI_OWNER_WORKSPACE_SERVICE_PERIOD_BRIDGE_V1' in s
    j=s.index('KPI_OWNER_WORKSPACE_SERVICE_PERIOD_BRIDGE_V1')
    ui=s[j:]
    assert 'Зафиксировать текущий оплаченный период AI-маркетолога' in ui
    assert '_held_prepublication_items_ui' in ui
    assert 'held_prepublication_count' in ui
    assert 'BORIS заморозил' in ui


def test_rejected_revision_exact_identity_is_not_misdiagnosed_as_missing_mapping():
    s=_src('app/api/avito.py')
    assert 'KPI_TITLE_IDENTITY_VS_MUTATION_CAPACITY_V1' in s
    i=s.index('KPI_TITLE_IDENTITY_VS_MUTATION_CAPACITY_V1')
    block=s[i:i+14000]
    assert '"publication_rejected_bound"' in block
    assert '_active_title_exact_identity_ids' in block
    assert '_title_mapping_capacity_gap=max(0,_title_cohort_target-_title_exact_identity_active)' in block
    assert '_title_mutation_capacity_gap=max(0,_title_cohort_target-_title_exact_writable_active)' in block
    assert 'not in _active_title_exact_identity_ids' in block
    assert '"mapping_capacity_gap": _title_mapping_capacity_gap' in s
    assert '"mutation_capacity_gap": _title_mutation_capacity_gap' in s
    assert '"revision_hold_active": len(_active_title_revision_hold_ids)' in s


def test_publication_rejected_bound_stays_non_writable_even_with_exact_pair():
    import json, sys
    sys.path.insert(0,str(ROOT))
    from app.models.campaign_item import CampaignItem
    from app.services.campaign_identity import is_canonical_writable
    row=CampaignItem(
        account_id='qa',
        campaign_id=1,
        feed_identity='ci-exact',
        avito_item_id='123',
        identity_status='publication_rejected_bound',
        status='draft',
        source='qa',
        payload_json=json.dumps({
            'identity':{
                'feed_identity':'ci-exact',
                'avito_item_id':'123',
                'resolution':'autoload_report',
                'upload_id':999,
            }
        }),
    )
    assert is_canonical_writable(row) is False


def test_external_2214_hold_autoresume_requires_authoritative_clear_and_clears_stale_hold():
    s=_src('app/api/avito.py')
    assert 'KPI_PUBLISH_EXTERNAL_HOLD_AUTORESUME_V1' in s
    start=s.index('KPI_PUBLISH_EXTERNAL_HOLD_AUTORESUME_V1')
    retry=s.index('STALE_PUBLISH_AUTORETRY_V1', start)
    block=s[start:retry]
    assert '_provider_status != "clear"' in block
    assert 'provider_blocker_not_authoritatively_clear' in block
    assert 'waiting_owner_funds' in block
    assert 'resume_after_owner_funds_detected' in block
    assert 'op.pop("publish_external_hold", None)' in block
    assert 'avito_advance_required_2214' in block
    # Unknown/retryable provider evidence must return before any bounded retry.
    assert s.index('provider_blocker_not_authoritatively_clear', start) < retry


def test_planeta_external_hold_watchdog_services_all_hold_states_without_publish_write():
    s=_src('app/services/campaign_post_publish_watch.py')
    assert 'PLANETA_KPI_EXTERNAL_HOLD_WATCHDOG_V2' in s
    assert 'PLANETA_KPI_EXTERNAL_HOLD_WATCHDOG_WIRE_V2' in s
    assert 'PLANETA_EXTERNAL_HOLD_RECHECK_MAX_PER_TICK = 1' in s
    start=s.index('def _recheck_planeta_kpi_external_publication_hold')
    end=s.index('\ndef _dt(', start)
    block=s[start:end]
    assert '"waiting_budget_policy"' in block
    assert '"waiting_provider_after_budget_release"' in block
    assert '"waiting_owner_funds"' in block
    assert 'last_hold_checked_at' in block
    assert '_planeta_kpi_hold_fresh_spend_gate' in s
    assert 'fresh_same_day_spend_required_before_confirm_publish' in block
    assert '"confirm_publish_called": False' in block
    assert 'A.kpi_confirm_publish(' in block
    assert 'A.KpiConfirmPublishRequest(' in block
    assert block.index('fresh_spend_gate = gate_fn') < block.index('A.kpi_confirm_publish(')
    assert 'kpi_publish_changes(' not in block
    assert 'feed_send_to_avito(' not in block
    assert '"external_write_performed": False' in block

def test_planeta_external_hold_watchdog_skips_confirm_when_spend_is_stale():
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _recheck_planeta_kpi_external_publication_hold,
    )

    history = [{
        "operation_id": "qa-stale-spend",
        "status": "publish_requested",
        "publish_retry_count": 1,
        "publish_external_hold": {
            "state": "waiting_budget_policy",
            "provider_code": 2214,
            "detected_at": "2026-09-07T10:00:00+00:00",
        },
    }]
    confirm_calls = []

    class _DB:
        def close(self):
            return None

    class _A:
        @staticmethod
        def _kpi_apply_log_load(db, account_id):
            assert account_id == "planetazayavki_65985"
            return None, history

        @staticmethod
        def kpi_confirm_publish(req):
            confirm_calls.append(req)
            raise AssertionError("confirm_publish must not run on stale spend")

    def _stale_gate(account_id):
        assert account_id == "planetazayavki_65985"
        return {
            "fresh": False,
            "status": "waiting_fresh_spend",
            "signal_status": "stale",
            "signal_reason": "spending_stale",
            "age_seconds": 1801,
            "spending_date": "2026-09-07",
            "max_age_seconds": 900,
        }

    fence_calls = []
    def _fence(active):
        fence_calls.append(bool(active))
        return {
            "status": "already_active" if active else "released",
            "active": bool(active),
            "managed": True,
        }

    refresh_calls = []
    def _refresh(account_id):
        refresh_calls.append(account_id)
        return {
            "status": "degraded",
            "reason": "avito_account_throttled",
            "retryable": True,
            "money_actions_allowed": False,
        }

    def _pressure(account_id):
        return {
            "blocked": False,
            "reason": "presence_budget_compatible",
            "authoritative": True,
        }

    out = _recheck_planeta_kpi_external_publication_hold(
        "planetazayavki_65985",
        datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc),
        db_factory=_DB,
        avito_module=_A,
        fresh_spend_gate_fn=_stale_gate,
        freshness_fence_fn=_fence,
        stats_refresh_fn=_refresh,
        budget_pressure_fn=_pressure,
    )
    assert out["status"] == "checked"
    assert out["attempted"] == 1
    assert confirm_calls == []
    row = out["results"][0]
    assert row["status"] == "waiting_fresh_spend_after_budget_clear"
    assert row["retry_count"] == 1
    assert row["changed_avito"] is False
    assert row["owner_action_required"] is False
    assert row["confirm_publish_called"] is False
    assert row["fresh_spend_gate"]["fresh"] is False
    assert refresh_calls == ["planetazayavki_65985"]
    assert row["stats_self_heal"]["attempted"] is True
    assert row["stats_self_heal"]["reason"] == "avito_account_throttled"
    assert row["local_budget_pressure"]["blocked"] is False
    assert fence_calls == [True, True]
    assert out["hourly_freshness_fence"]["active"] is True
    assert out["external_write_performed"] is False


def test_planeta_external_hold_watchdog_does_not_refresh_provider_while_budget_policy_red():
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _recheck_planeta_kpi_external_publication_hold,
    )

    history = [{
        "operation_id": "qa-policy-red",
        "status": "publish_requested",
        "publish_retry_count": 1,
        "publish_external_hold": {
            "state": "waiting_budget_policy",
            "provider_code": 2214,
            "detected_at": "2026-09-07T10:00:00+00:00",
        },
    }]
    calls = {"refresh": 0, "confirm": 0}

    class _DB:
        def close(self):
            return None

    class _A:
        @staticmethod
        def _kpi_apply_log_load(db, account_id):
            return None, history

        @staticmethod
        def kpi_confirm_publish(req):
            calls["confirm"] += 1
            raise AssertionError("confirm must not run while spend is stale")

    def _stale_gate(account_id):
        return {
            "fresh": False,
            "status": "waiting_fresh_spend",
            "signal_status": "stale",
            "signal_reason": "spending_stale",
            "age_seconds": 1801,
            "spending_date": "2026-09-07",
            "max_age_seconds": 900,
        }

    def _pressure(account_id):
        return {
            "blocked": True,
            "reason": "presence_spend_exceeds_daily_budget_history",
            "authoritative": True,
        }

    def _refresh(account_id):
        calls["refresh"] += 1
        raise AssertionError("provider refresh must not run while policy is red")

    def _fence(active):
        return {
            "status": "already_active" if active else "released",
            "active": bool(active),
            "managed": True,
        }

    out = _recheck_planeta_kpi_external_publication_hold(
        "planetazayavki_65985",
        datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc),
        db_factory=_DB,
        avito_module=_A,
        fresh_spend_gate_fn=_stale_gate,
        freshness_fence_fn=_fence,
        stats_refresh_fn=_refresh,
        budget_pressure_fn=_pressure,
    )
    row = out["results"][0]
    assert calls == {"refresh": 0, "confirm": 0}
    assert row["status"] == "waiting_budget_policy"
    assert row["reason"] == "money_policy_still_blocks_before_stats_refresh"
    assert row["stats_self_heal"]["attempted"] is False
    assert row["local_budget_pressure"]["blocked"] is True
    assert row["confirm_publish_called"] is False


def test_planeta_external_hold_watchdog_releases_hourly_fence_after_budget_state_exit():
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _recheck_planeta_kpi_external_publication_hold,
    )

    history = [{
        "operation_id": "qa-fresh-release",
        "status": "publish_requested",
        "publish_retry_count": 1,
        "publish_external_hold": {
            "state": "waiting_budget_policy",
            "provider_code": 2214,
            "detected_at": "2026-09-07T10:00:00+00:00",
        },
    }]
    confirm_calls = []
    fence_calls = []

    class _DB:
        def close(self):
            return None

    class _Req:
        def __init__(self, account_id, operation_id):
            self.account_id = account_id
            self.operation_id = operation_id

    class _A:
        KpiConfirmPublishRequest = _Req

        @staticmethod
        def _kpi_apply_log_load(db, account_id):
            return None, history

        @staticmethod
        def kpi_confirm_publish(req):
            confirm_calls.append((req.account_id, req.operation_id))
            return {
                "status": "waiting_provider_after_budget_release",
                "changed_avito": False,
                "owner_action_required": False,
                "retry_count": 1,
                "reason": "provider_truth_pending",
            }

    gate_calls = []
    def _fresh_gate(account_id):
        gate_calls.append(account_id)
        if len(gate_calls) == 1:
            return {
                "fresh": False,
                "status": "waiting_fresh_spend",
                "signal_status": "stale",
                "signal_reason": "spending_stale",
                "spending_date": "2026-09-07",
                "age_seconds": 1800,
                "max_age_seconds": 900,
            }
        return {
            "fresh": True,
            "status": "fresh",
            "spending_date": "2026-09-08",
            "age_seconds": 120,
            "max_age_seconds": 900,
        }

    refresh_calls = []
    def _refresh(account_id):
        refresh_calls.append(account_id)
        return {
            "status": "ok",
            "snapshot": {
                "spending": {
                    "status": "ok",
                    "date": "2026-09-08",
                }
            },
        }

    def _pressure(account_id):
        return {
            "blocked": False,
            "reason": "presence_budget_compatible",
            "authoritative": True,
        }

    def _fence(active):
        fence_calls.append(bool(active))
        return {
            "status": "already_active" if active else "released",
            "active": bool(active),
            "managed": True,
        }

    out = _recheck_planeta_kpi_external_publication_hold(
        "planetazayavki_65985",
        datetime(2026, 9, 7, 21, 11, tzinfo=timezone.utc),
        db_factory=_DB,
        avito_module=_A,
        fresh_spend_gate_fn=_fresh_gate,
        freshness_fence_fn=_fence,
        stats_refresh_fn=_refresh,
        budget_pressure_fn=_pressure,
    )
    assert confirm_calls == [
        ("planetazayavki_65985", "qa-fresh-release")
    ]
    assert gate_calls == [
        "planetazayavki_65985",
        "planetazayavki_65985",
    ]
    assert refresh_calls == ["planetazayavki_65985"]
    assert fence_calls == [True, False]
    assert out["results"][0]["confirm_publish_called"] is True
    assert out["results"][0]["status"] == "waiting_provider_after_budget_release"
    assert out["results"][0]["stats_self_heal"]["attempted"] is True
    assert out["results"][0]["stats_self_heal"]["status"] == "ok"
    assert out["results"][0]["local_budget_pressure"]["blocked"] is False
    assert out["hourly_freshness_fence"]["active"] is False


def test_planeta_hourly_freshness_fence_preserves_external_kill_switch():
    import sys
    from datetime import datetime, timezone
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _sync_planeta_kpi_hourly_freshness_fence,
    )

    row = SimpleNamespace(
        module="marketing",
        account_id="planetazayavki_65985",
        blocked=True,
        reason="OWNER: emergency marketing stop",
    )
    commits = []

    class _Query:
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return row

    class _DB:
        def query(self, *args, **kwargs):
            return _Query()
        def commit(self):
            commits.append(True)
        def close(self):
            return None

    now = datetime.now(timezone.utc)
    active = _sync_planeta_kpi_hourly_freshness_fence(
        True,
        now,
        db_factory=_DB,
    )
    release = _sync_planeta_kpi_hourly_freshness_fence(
        False,
        now,
        db_factory=_DB,
    )
    assert active["status"] == "external_fence_preserved"
    assert release["status"] == "external_fence_preserved"
    assert row.blocked is True
    assert row.reason == "OWNER: emergency marketing stop"
    assert commits == []


def test_planeta_hourly_freshness_fence_releases_only_managed_switch():
    import sys
    from datetime import datetime, timezone
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON,
        _sync_planeta_kpi_hourly_freshness_fence,
    )

    row = SimpleNamespace(
        module="marketing",
        account_id="planetazayavki_65985",
        blocked=True,
        reason=(
            PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON
            + ": waiting_budget_policy"
        ),
    )
    commits = []

    class _Query:
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return row

    class _DB:
        def query(self, *args, **kwargs):
            return _Query()
        def commit(self):
            commits.append(True)
        def close(self):
            return None

    out = _sync_planeta_kpi_hourly_freshness_fence(
        False,
        datetime(2026, 9, 8, 0, 11, tzinfo=timezone.utc),
        db_factory=_DB,
    )
    assert out["status"] == "released"
    assert out["active"] is False
    assert out["managed"] is True
    assert row.blocked is False
    assert row.reason.startswith(
        PLANETA_KPI_HOURLY_FRESHNESS_FENCE_REASON + ": released_at="
    )
    assert commits == [True]


def test_planeta_kpi_hold_fresh_spend_gate_accepts_only_eligible_signal():
    import sys
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _planeta_kpi_hold_fresh_spend_gate,
    )

    closed = []
    class _DB:
        def close(self):
            closed.append(True)

    seen = {}
    def _latest(db, account_id, max_age_seconds):
        seen["latest"] = (account_id, max_age_seconds)
        return {
            "status": "ok",
            "storage_key": "daily_stats:2026-09-08",
            "spending_date": "2026-09-08",
            "age_seconds": 120,
            "spent_today_rub": 200.0,
        }
    def _eligible(db, account_id, signal, max_age_seconds):
        seen["eligible"] = (
            account_id,
            signal["spending_date"],
            max_age_seconds,
        )
        return True

    out = _planeta_kpi_hold_fresh_spend_gate(
        "planetazayavki_65985",
        db_factory=_DB,
        latest_spend_fn=_latest,
        eligible_fn=_eligible,
    )
    assert out["fresh"] is True
    assert out["status"] == "fresh"
    assert out["spending_date"] == "2026-09-08"
    assert out["spent_today_rub"] == 200.0
    assert seen["latest"] == ("planetazayavki_65985", 900)
    assert seen["eligible"] == ("planetazayavki_65985", "2026-09-08", 900)
    assert closed == [True]


def test_low_wallet_pause_allows_only_existing_safe_tail_and_blocks_new_work():
    s=_src('app/api/avito.py')
    assert 'KPI_LOW_WALLET_SAFE_TAIL_V1' in s
    start=s.index('KPI_LOW_WALLET_SAFE_TAIL_V1')
    end=s.index('KPI_ADDITIVE_GROWTH_BEFORE_CONTENT_WAIT_V1', start)
    block=s[start:end]
    for status in (
        '"publish_requested"',
        '"published"',
        '"effect_rollback_requested"',
        '"rollback_feed_ready"',
        '"rollback_publish_failed"',
        '"rollback_publish_requested"',
    ):
        assert status in block
    assert '_funding_new_work_allowed = bool(' in block
    assert 'not _funding_work_pause_kgt' in block
    additive=s[s.index('KPI_ADDITIVE_GROWTH_BEFORE_CONTENT_WAIT_V1'):s.index('NEW_ITEM_NO_PROMO_BOOTSTRAP_V2')]
    assert 'if _funding_new_work_allowed and not _content_only_placement_kgt:' in additive
    bootstrap=s[s.index('NEW_ITEM_NO_PROMO_BOOTSTRAP_V2'):s.index('NEW_ITEM_NO_PROMO_BOOTSTRAP_V2')+2200]
    assert '_funding_new_work_allowed' in bootstrap
    assert 'and not allow_stale_non_money' in bootstrap
    assert 'if allow_stale_non_money and _funding_new_work_allowed:' in s


def _run_external_hold_confirm_with_fake_provider(
    provider_status, *, hold_state="waiting_owner_funds", money_blocked=False
):
    import sys
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT))
    import app.api.avito as a
    import app.db.session as db_session

    class FakeDB:
        def close(self): pass
        def commit(self): pass

    owner_required = hold_state == "waiting_owner_funds"
    op = {
        "operation_id": "qa-owner-funds",
        "status": "publish_requested",
        "item_id": "123",
        "avito_item_id": "123",
        "feed_identity": "ci-qa",
        "changed_avito": False,
        "publish_retry_count": 1,
        "publish_error": "avito_advance_required_2214",
        "publish_external_hold": {
            "state": hold_state,
            "provider_code": 2214,
            "owner_action_required": owner_required,
            "detected_at": "2026-09-06T10:00:00",
            "last_policy_checked_at": "2026-09-06T10:00:00",
        },
        "effect": {
            "status": hold_state,
            "decision": (
                "wait_owner_funds_no_republish"
                if owner_required
                else "wait_budget_policy_no_republish"
            ),
            "provider_code": 2214,
        },
    }
    history = [op]
    with (
        patch.object(db_session, "SessionLocal", lambda: FakeDB()),
        patch.object(a, "_kpi_find_apply_operation", lambda db, account_id, operation_id: (history, op)),
        patch.object(
            a,
            "_kpi_publish_money_policy",
            lambda account_id, db=None: {
                "blocked": money_blocked,
                "reason": (
                    "presence_spend_exceeds_daily_budget_history"
                    if money_blocked else "policy_clear"
                ),
                "daily_budget_limit_rub": 3000.0,
                "completed_over_budget_days": 0,
            },
        ),
        patch.object(a, "_kpi_fetch_live_item", lambda account_id, item_id: {"status": "ok", "item": {"id": item_id}}),
        patch.object(a, "_kpi_compare_expected_with_live", lambda operation, live: {"status": "ok", "all_match": False}),
        patch.object(a, "_kpi_publish_provider_blocker", lambda account_id, feed_identity: {"status": provider_status}),
        patch.object(a, "_kpi_apply_log_save", lambda db, account_id, payload: None),
    ):
        out = a.kpi_confirm_publish(
            a.KpiConfirmPublishRequest(
                account_id="qa",
                operation_id="qa-owner-funds",
            )
        )
    return op, out


def test_waiting_owner_funds_autoresumes_when_provider_explicitly_clears():
    op, out = _run_external_hold_confirm_with_fake_provider("clear")
    assert out["status"] == "waiting"
    assert "publish_external_hold" not in op
    assert op.get("publish_error") is None
    assert op["publish_retry_count"] == 1
    assert op["effect"]["status"] == "waiting_avito_confirmation"
    assert op["effect"]["decision"] == "resume_after_owner_funds_detected"
    assert op["changed_avito"] is False


def test_waiting_owner_funds_unknown_provider_does_not_consume_retry():
    op, out = _run_external_hold_confirm_with_fake_provider("unknown")
    assert out["status"] == "waiting"
    assert out["reason"] == "provider_blocker_not_authoritatively_clear"
    assert out["retry_count"] == 1
    assert op["publish_external_hold"]["state"] == "waiting_owner_funds"
    assert op["publish_external_hold"].get("last_hold_checked_at")
    assert op["publish_retry_count"] == 1
    assert op["changed_avito"] is False


def test_budget_policy_release_unknown_provider_preserves_watchdog_hold():
    op, out = _run_external_hold_confirm_with_fake_provider(
        "unknown",
        hold_state="waiting_budget_policy",
        money_blocked=False,
    )
    assert out["status"] == "waiting"
    assert out["reason"] == "provider_blocker_not_authoritatively_clear"
    assert op["publish_external_hold"]["state"] == "waiting_provider_after_budget_release"
    assert op["publish_external_hold"].get("last_hold_checked_at")
    assert op["publish_retry_count"] == 1
    assert op["publish_error"] == "avito_advance_required_2214"
    assert op["changed_avito"] is False


def test_budget_policy_release_provider_clear_removes_hold_without_blind_retry():
    op, out = _run_external_hold_confirm_with_fake_provider(
        "clear",
        hold_state="waiting_budget_policy",
        money_blocked=False,
    )
    assert out["status"] == "waiting"
    assert "publish_external_hold" not in op
    assert op.get("publish_error") is None
    assert op["publish_retry_count"] == 1
    assert op["effect"]["status"] == "waiting_avito_confirmation"
    assert op["effect"]["decision"] == "resume_after_provider_clear"
    assert op["changed_avito"] is False


def test_budget_policy_still_red_refreshes_hold_without_provider_retry():
    op, out = _run_external_hold_confirm_with_fake_provider(
        "unknown",
        hold_state="waiting_budget_policy",
        money_blocked=True,
    )
    assert out["status"] == "waiting_budget_policy"
    assert out["retry_count"] == 1
    assert op["publish_external_hold"]["state"] == "waiting_budget_policy"
    assert op["publish_external_hold"].get("last_hold_checked_at")
    assert op["publish_external_hold"].get("last_policy_checked_at")
    assert op["publish_retry_count"] == 1
    assert op["changed_avito"] is False


def test_owner_projection_owner_funds_requires_only_funding_then_auto_resume():
    import sys
    sys.path.insert(0, str(ROOT))
    from app.api.avito import _ai_marketing_human_operation
    out = _ai_marketing_human_operation({
        "status": "publish_requested",
        "item_id": "qa",
        "reason": "provider 2214",
        "effect": {
            "status": "waiting_owner_funds",
            "decision": "wait_owner_funds_no_republish",
            "provider_code": 2214,
        },
    })
    assert out["group"] == "Нужно решение владельца"
    assert "пополнить аванс/баланс" in out["current_status"]
    assert "без ручного перезапуска" in out["current_status"]
    assert "сам перепроверит Avito" in out["when_result"]
    assert "запускать операцию заново не нужно" in out["when_result"]
    assert "вручную перезапускать ничего не нужно" in out["next_step"]
    assert out["result"] == "waiting_owner_funds"

def test_planeta_effect_window_self_heals_to_moscow_business_days():
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _repair_planeta_kpi_observation_business_day,
    )

    op = {
        "operation_id": "qa-midnight",
        "status": "published",
        # 2026-09-08 00:11 Moscow, but still Sep7 in UTC.
        "published_at": "2026-09-07T21:11:00",
        "observation_window": {
            "min_complete_days": 2,
            "status": "active",
            # Shared UTC-date behavior would incorrectly include Sep8.
            "start_date": "2026-09-08",
            "end_date": "2026-09-09",
            "complete_after": "2026-09-10T00:00:00",
        },
        "effect": {"status": "waiting_observation_window"},
    }
    history = [op]
    saves = []
    commits = []

    class _DB:
        def commit(self):
            commits.append(True)
        def close(self):
            return None

    class _A:
        @staticmethod
        def _kpi_find_apply_operation(db, account_id, operation_id):
            assert account_id == "planetazayavki_65985"
            assert operation_id == "qa-midnight"
            return history, op

        @staticmethod
        def _kpi_apply_log_save(db, account_id, passed_history):
            assert passed_history is history
            saves.append(True)

    now = datetime(2026, 9, 7, 21, 12, tzinfo=timezone.utc)
    first = _repair_planeta_kpi_observation_business_day(
        "qa-midnight",
        {"status": "published"},
        now,
        db_factory=_DB,
        avito_module=_A,
    )
    assert first["status"] == "repaired"
    assert first["changed"] is True
    assert first["publication_business_day"] == "2026-09-08"
    assert first["start_date"] == "2026-09-09"
    assert first["end_date"] == "2026-09-10"
    assert first["complete_after"] == "2026-09-11T00:00:00+03:00"
    assert op["observation_window"]["start_date"] == "2026-09-09"
    assert op["observation_window"]["end_date"] == "2026-09-10"
    assert op["observation_window"]["business_day_timezone"] == "Europe/Moscow"
    assert (
        op["observation_window"]["business_day_policy_version"]
        == "PLANETA_KPI_EFFECT_MOSCOW_DAY_SELF_HEAL_V1"
    )
    assert saves == [True]
    assert commits == [True]

    second = _repair_planeta_kpi_observation_business_day(
        "qa-midnight",
        {"status": "published"},
        now,
        db_factory=_DB,
        avito_module=_A,
    )
    assert second["status"] == "already_correct"
    assert second["changed"] is False
    assert saves == [True]
    assert commits == [True]


def test_planeta_effect_window_repair_not_required_before_publication():
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _repair_planeta_kpi_observation_business_day,
    )

    def _forbidden_db():
        raise AssertionError("DB must not be touched before publication")

    out = _repair_planeta_kpi_observation_business_day(
        "qa-hold",
        {"status": "waiting_budget_policy"},
        datetime.now(timezone.utc),
        db_factory=_forbidden_db,
    )
    assert out == {"status": "not_required", "changed": False}


def test_planeta_owner_funds_notice_never_fires_while_budget_policy_red():
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _notify_planeta_owner_funds_once,
    )

    calls = []
    def _forbidden_db():
        raise AssertionError("DB must not be touched for non-owner state")
    def _notify(text):
        calls.append(text)
        return True

    out = _notify_planeta_owner_funds_once(
        "qa-op",
        {
            "status": "waiting_budget_policy",
            "owner_action_required": False,
            "provider_code": 2214,
        },
        datetime.now(timezone.utc),
        db_factory=_forbidden_db,
        notify_fn=_notify,
    )
    assert out["status"] == "not_required"
    assert out["sent"] is False
    assert calls == []


def test_planeta_owner_funds_notice_sends_once_and_dedupes():
    import sys
    from datetime import datetime, timezone, timedelta
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _notify_planeta_owner_funds_once,
    )

    shared = {"rows": [], "next_id": 1}
    class _Query:
        def __init__(self, state):
            self.state = state
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def first(self):
            return self.state["rows"][-1] if self.state["rows"] else None
    class _DB:
        def __init__(self, state):
            self.state = state
        def query(self, *args, **kwargs):
            return _Query(self.state)
        def add(self, row):
            if getattr(row, "id", None) is None:
                row.id = self.state["next_id"]
                self.state["next_id"] += 1
            self.state["rows"].append(row)
        def commit(self):
            return None
        def refresh(self, row):
            return None
        def close(self):
            return None
    def _factory():
        return _DB(shared)

    calls = []
    def _notify(text):
        calls.append(text)
        return True

    now = datetime.now(timezone.utc)
    payload = {
        "status": "waiting_owner_funds",
        "owner_action_required": True,
        "provider_code": 2214,
    }
    first = _notify_planeta_owner_funds_once(
        "qa-owner-funds",
        payload,
        now,
        db_factory=_factory,
        notify_fn=_notify,
    )
    second = _notify_planeta_owner_funds_once(
        "qa-owner-funds",
        payload,
        now + timedelta(minutes=15),
        db_factory=_factory,
        notify_fn=_notify,
    )
    assert first["status"] == "sent"
    assert first["sent"] is True
    assert second["status"] == "already_sent"
    assert second["sent"] is True
    assert len(calls) == 1
    assert "2214" in calls[0]
    assert "сам перепроверит" in calls[0]
    marker = __import__("json").loads(shared["rows"][-1].value)
    assert marker["state"] == "sent"
    assert marker["operation_id"] == "qa-owner-funds"
    assert marker["owner_action_required"] is True


def test_planeta_owner_funds_notice_failure_has_hourly_cooldown():
    import sys
    from datetime import datetime, timezone, timedelta
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _notify_planeta_owner_funds_once,
    )

    shared = {"rows": [], "next_id": 1}
    class _Query:
        def __init__(self, state):
            self.state = state
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def first(self):
            return self.state["rows"][-1] if self.state["rows"] else None
    class _DB:
        def __init__(self, state):
            self.state = state
        def query(self, *args, **kwargs):
            return _Query(self.state)
        def add(self, row):
            if getattr(row, "id", None) is None:
                row.id = self.state["next_id"]
                self.state["next_id"] += 1
            self.state["rows"].append(row)
        def commit(self):
            return None
        def refresh(self, row):
            return None
        def close(self):
            return None
    def _factory():
        return _DB(shared)

    calls = []
    def _notify(text):
        calls.append(text)
        return False

    now = datetime.now(timezone.utc)
    payload = {
        "status": "waiting_owner_funds",
        "owner_action_required": True,
        "provider_code": 2214,
    }
    first = _notify_planeta_owner_funds_once(
        "qa-owner-funds-fail",
        payload,
        now,
        db_factory=_factory,
        notify_fn=_notify,
    )
    second = _notify_planeta_owner_funds_once(
        "qa-owner-funds-fail",
        payload,
        now + timedelta(minutes=15),
        db_factory=_factory,
        notify_fn=_notify,
    )
    assert first["status"] == "retryable_failed"
    assert first["sent"] is False
    assert second["status"] == "retry_cooldown"
    assert second["sent"] is False
    assert len(calls) == 1
    marker = __import__("json").loads(shared["rows"][-1].value)
    assert marker["state"] == "retryable_failed"
    assert marker["retry_after_seconds"] == 3600


def test_planeta_rejected_publication_fail_closed_repairs_false_published_truth():
    import sys, json
    from types import SimpleNamespace
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _reconcile_planeta_rejected_publication_truth,
    )

    row = SimpleNamespace(
        id=9001,
        identity_status="published_identity_bound",
        status="published",
        payload_json=json.dumps({
            "identity": {
                "resolution": "autoload_report",
                "publication_state": "rejected",
                "publication_rejection_upload_id": 100,
                "upload_id": 101,
            }
        }),
    )
    out = _reconcile_planeta_rejected_publication_truth(
        [row],
        "planetazayavki_65985",
        datetime.now(timezone.utc),
    )
    assert out["changed"] == 1
    assert row.identity_status == "publication_rejected_bound"
    assert row.status == "draft"
    payload = json.loads(row.payload_json)
    assert payload["identity"]["publication_state"] == "rejected"
    assert payload["identity"]["publication_truth_reconcile_reason"] == (
        "durable_rejection_without_authoritative_superseding_success"
    )


def test_planeta_rejected_publication_fail_closed_cleans_stale_reject_after_publication_result():
    import sys, json
    from types import SimpleNamespace
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _reconcile_planeta_rejected_publication_truth,
    )

    proven = SimpleNamespace(
        id=9002,
        identity_status="published_identity_bound",
        status="published",
        payload_json=json.dumps({
            "identity": {
                "resolution": "publication_result",
                "publication_state": "rejected",
                "publication_error_status": "rejected",
                "publication_error_section": "error_rejected",
                "publication_rejection_upload_id": 100,
            }
        }),
    )
    out = _reconcile_planeta_rejected_publication_truth(
        [proven],
        "planetazayavki_65985",
        datetime.now(timezone.utc),
    )
    assert out["changed"] == 1
    assert proven.identity_status == "published_identity_bound"
    assert proven.status == "published"
    payload = json.loads(proven.payload_json)
    assert "publication_state" not in payload["identity"]
    assert "publication_error_status" not in payload["identity"]
    assert "publication_error_section" not in payload["identity"]
    assert payload["identity"]["publication_rejection_upload_id"] == 100
    assert payload["identity"]["publication_rejection_resolution"] == (
        "authoritative_publication_result"
    )


def test_planeta_rejected_publication_fail_closed_preserves_newer_causal_success():
    import sys, json
    from types import SimpleNamespace
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _reconcile_planeta_rejected_publication_truth,
    )

    proven = SimpleNamespace(
        id=9003,
        identity_status="published_identity_bound",
        status="published",
        payload_json=json.dumps({
            "identity": {
                "resolution": "autoload_report",
                "publication_state": "rejected",
                "publication_rejection_upload_id": 100,
                "publication_rejection_superseded_by_upload_id": 101,
            }
        }),
    )
    out = _reconcile_planeta_rejected_publication_truth(
        [proven],
        "planetazayavki_65985",
        datetime.now(timezone.utc),
    )
    assert out["changed"] == 0
    assert proven.identity_status == "published_identity_bound"
    assert proven.status == "published"


def test_planeta_distinct_slot_fallback_is_single_writer_before_legacy_cleanup():
    s = _src('app/services/campaign_post_publish_watch.py')
    assert 'PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_V1' in s
    assert 'PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_WIRE_V1' in s
    assert 'guardian_balance_funding_health' in s
    assert 'slot_fallback_wait_owner_funds' in s
    assert 'SLOT_FALLBACK_RELEASE_SCOPE_DRIFT' in s
    assert 'slot_fallback_completed_36_logical_slots' in s
    wire = s.index('PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_WIRE_V1')
    legacy = s.index('_pilot_scale_replacement_cleanup(', wire)
    block = s[wire:legacy]
    assert '_planeta_distinct_slot_fallback_lifecycle(' in block
    assert 'if str((_slot_fallback or {}).get("status") or "") != "not_configured"' in block


def test_planeta_distinct_slot_fallback_preserves_blocked_new_on_release():
    s = _src('app/services/campaign_post_publish_watch.py')
    start = s.index('PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_V1')
    end = s.index('def _pilot_scale_replacement_cleanup', start)
    block = s[start:end]
    assert 'len(desired_old_fids) != 10' in block
    assert 'len(blocked_new_fids) != 10' in block
    assert 'len(active_new_fids) != 26' in block
    assert 'model.date_end = ""' in block
    assert 'blocked_new_fids' in block
    assert '!= hold_dateend' in block
    assert 'work_pause_required' in block
    assert 'funding_age <= 900' in block
    assert 'feed_send_to_avito' in block
    assert 'active_fallback == desired_old_avitos' in block


def test_planeta_rejected_publication_fail_closed_is_wired_before_revision_projection():
    s = _src('app/services/campaign_post_publish_watch.py')
    assert 'PLANETA_REJECTED_PUBLICATION_FAIL_CLOSED_V1' in s
    assert 'PLANETA_REJECTED_PUBLICATION_FAIL_CLOSED_WIRE_V1' in s
    wire = s.index('PLANETA_REJECTED_PUBLICATION_FAIL_CLOSED_WIRE_V1')
    revision = s.index('BASE_LIVE_REVISION_PENDING_MEASUREMENT_V1', wire)
    block = s[wire:revision]
    assert '_reconcile_planeta_rejected_publication_truth(' in block
    assert 'db.commit()' in block
    assert 'rejected_publication_truth_repairs' in s
    assert 'rejected_publication_truth_repaired_ids' in s


def test_conversion_consumer_truth_confirms_active_content_lifecycle():
    import sys, json
    from types import SimpleNamespace
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _conversion_action_consumer_state,
    )

    row = SimpleNamespace(value=json.dumps([{
        "operation_id": "kpi-ai-prepare_weak_titles-test:apply:1",
        "status": "publish_requested",
        "avito_item_id": "1",
        "changes": {"title": {"old": "A", "new": "B"}},
        "effect": {"status": "waiting_budget_policy"},
        "publish_external_hold": {
            "state": "waiting_budget_policy",
            "owner_action_required": False,
        },
    }]))

    class Query:
        def filter(self, *args, **kwargs): return self
        def order_by(self, *args, **kwargs): return self
        def first(self): return row
    class DB:
        def query(self, *args, **kwargs): return Query()

    out = _conversion_action_consumer_state(
        DB(),
        "qa",
        {
            "signal": "traffic_without_contacts",
            "system_action": "handoff_conversion_optimization",
            "source_stats_fresh": True,
        },
        datetime.now(timezone.utc),
    )
    assert out["confirmed"] is True
    assert out["state"] == "consumer_active"
    assert out["consumer"] == "kpi_content_lifecycle"
    assert out["operation_status"] == "publish_requested"
    assert out["effect_status"] == "waiting_budget_policy"
    assert out["content_fields"] == ["title"]
    assert out["external_hold_state"] == "waiting_budget_policy"
    assert out["owner_action_required"] is False


def test_conversion_consumer_truth_does_not_confirm_terminal_experiment():
    import sys, json
    from types import SimpleNamespace
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    from app.services.campaign_post_publish_watch import (
        _conversion_action_consumer_state,
    )

    row = SimpleNamespace(value=json.dumps([{
        "operation_id": "kpi-ai-prepare_weak_titles-done:apply:1",
        "status": "published",
        "avito_item_id": "1",
        "changes": {"title": {"old": "A", "new": "B"}},
        "effect": {"status": "improved"},
    }]))

    class Query:
        def filter(self, *args, **kwargs): return self
        def order_by(self, *args, **kwargs): return self
        def first(self): return row
    class DB:
        def query(self, *args, **kwargs): return Query()

    out = _conversion_action_consumer_state(
        DB(),
        "qa",
        {
            "signal": "traffic_without_contacts",
            "system_action": "handoff_conversion_optimization",
            "source_stats_fresh": True,
        },
        datetime.now(timezone.utc),
    )
    assert out["confirmed"] is False
    assert out["state"] == "signal_unconsumed"


def test_conversion_consumer_truth_is_wired_into_account_rollup():
    s = _src('app/services/campaign_post_publish_watch.py')
    assert 'POST_PUBLISH_CONVERSION_CONSUMER_TRUTH_V1' in s
    assert 'POST_PUBLISH_CONVERSION_CONSUMER_WIRE_V1' in s
    start = s.index('POST_PUBLISH_CONVERSION_CONSUMER_WIRE_V1')
    end = s.index('_save_json_storage(', start)
    block = s[start:end]
    assert '_conversion_action_consumer_state(' in block
    assert '"action_consumer_confirmed"' in block
    assert '"consumer_active"' in block
    assert '"signal_unconsumed"' in block
    assert '"adjacent_action_required"' in block


def test_post_publish_provider_day_lag_is_stale_and_repairs_duplicate_days(monkeypatch):
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    import app.services.campaign_post_publish_watch as W

    monkeypatch.setattr(W, "marketing_today_iso", lambda: "2026-09-07")
    existing = {
        "campaign_id": 249,
        "account_id": "qa",
        "expected_feed_ids": ["a", "b", "c"],
        "days": {
            "2026-09-04": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "views_delta": 4,
                "contacts_delta": 0,
            },
            "2026-09-05": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "views_delta": 36,
                "contacts_delta": 0,
            },
            "2026-09-06": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "views_delta": 36,
                "contacts_delta": 0,
            },
            "2026-09-07": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "views_delta": 36,
                "contacts_delta": 0,
            },
        },
    }
    metrics = {
        "matched": 3,
        "active": 3,
        "canonical": 0,
        "views": 36,
        "contacts": 0,
        "items": {
            "a": {"views": 12, "contacts": 0},
            "b": {"views": 12, "contacts": 0},
            "c": {"views": 12, "contacts": 0},
        },
    }
    out = W._update_watch(
        existing,
        metrics=metrics,
        collected_at=datetime(2026, 9, 7, 14, 15, tzinfo=timezone.utc),
        published_at=datetime(2026, 9, 4, 18, 58, tzinfo=timezone.utc),
        expected=3,
        now=datetime(2026, 9, 7, 14, 26, tzinfo=timezone.utc),
        stats_date="2026-09-05",
        base_live_revision_pending=True,
    )

    assert out["status"] == "waiting_fresh_stats"
    assert out["reason"] == "provider_stats_date_lag"
    assert out["stats_date"] == "2026-09-05"
    assert out["stats_date_current"] is False
    assert sorted(out["days"]) == ["2026-09-04", "2026-09-05"]
    assert out["days"]["2026-09-05"]["provider_stats_date"] == "2026-09-05"
    assert out["days"]["2026-09-05"]["collection_date"] == "2026-09-07"
    assert out["views_since_baseline"] == 40
    bo = out["business_outcome"]
    assert bo["source_freshness_policy_version"] == "POST_PUBLISH_SOURCE_FRESHNESS_V2"
    assert bo["source_stats_fresh"] is False
    assert bo["source_stats_date"] == "2026-09-05"
    assert bo["source_stats_date_current"] is False
    assert bo["system_action"] == "wait_fresh_stats_before_conversion_action"
    assert bo["adjacent_action_required"] is False
    assert bo["action_delivery_state"] == "signal_stale_waiting_fresh_stats"


def test_post_publish_current_provider_day_can_be_fresh(monkeypatch):
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    import app.services.campaign_post_publish_watch as W

    monkeypatch.setattr(W, "marketing_today_iso", lambda: "2026-09-07")
    out = W._update_watch(
        {
            "campaign_id": 249,
            "account_id": "qa",
            "expected_feed_ids": ["a"],
            "days": {
                "2026-09-05": {
                    "baseline_views": 0,
                    "baseline_contacts": 0,
                    "views_delta": 10,
                    "contacts_delta": 0,
                },
            },
        },
        metrics={
            "matched": 1,
            "active": 1,
            "canonical": 1,
            "views": 5,
            "contacts": 0,
            "items": {"a": {"views": 5, "contacts": 0}},
        },
        collected_at=datetime(2026, 9, 7, 14, 15, tzinfo=timezone.utc),
        published_at=datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc),
        expected=1,
        now=datetime(2026, 9, 7, 14, 20, tzinfo=timezone.utc),
        stats_date="2026-09-07",
    )

    assert out["status"] == "observing"
    assert out["reason"] is None
    assert out["stats_date_current"] is True
    bo = out["business_outcome"]
    assert bo["source_stats_fresh"] is True
    assert bo["source_stats_date_current"] is True
    assert bo["system_action"] == "handoff_conversion_optimization"


def test_post_publish_watch_uses_provider_stats_date_and_drops_duplicate_future_buckets(monkeypatch):
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    import app.services.campaign_post_publish_watch as W

    monkeypatch.setattr(W, "marketing_today_iso", lambda: "2026-09-07")
    existing = {
        "campaign_id": 249,
        "account_id": "qa",
        "expected_feed_ids": ["f1"],
        "days": {
            "2026-09-04": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-04T19:20:00+00:00",
                "latest_views": 4,
                "latest_contacts": 0,
                "views_delta": 4,
                "contacts_delta": 0,
            },
            "2026-09-05": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-05T06:00:00+00:00",
                "latest_views": 36,
                "latest_contacts": 0,
                "views_delta": 36,
                "contacts_delta": 0,
            },
            "2026-09-06": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-06T00:10:00+00:00",
                "latest_views": 36,
                "latest_contacts": 0,
                "views_delta": 36,
                "contacts_delta": 0,
            },
            "2026-09-07": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-07T00:10:00+00:00",
                "latest_views": 36,
                "latest_contacts": 0,
                "views_delta": 36,
                "contacts_delta": 0,
            },
        },
    }
    metrics = {
        "matched": 1,
        "active": 1,
        "canonical": 1,
        "views": 36,
        "contacts": 0,
        "items": {
            "f1": {
                "status": "active",
                "views": 36,
                "contacts": 0,
            }
        },
    }
    out = W._update_watch(
        existing,
        metrics=metrics,
        collected_at=datetime(2026, 9, 7, 14, 15, tzinfo=timezone.utc),
        published_at=datetime(2026, 9, 4, 19, 0, tzinfo=timezone.utc),
        expected=1,
        now=datetime(2026, 9, 7, 14, 20, tzinfo=timezone.utc),
        stats_date="2026-09-05",
    )
    assert sorted(out["days"]) == ["2026-09-04", "2026-09-05"]
    assert out["stats_date"] == "2026-09-05"
    assert out["stats_date_current"] is False
    assert out["status"] == "waiting_fresh_stats"
    assert out["reason"] == "provider_stats_date_lag"
    assert out["views_since_baseline"] == 40
    assert out["business_outcome"]["source_stats_fresh"] is False
    assert out["business_outcome"]["system_action"] == "wait_fresh_stats_before_conversion_action"
    assert out["business_outcome"]["action_delivery_state"] == "signal_stale_waiting_fresh_stats"


def test_post_publish_watch_self_heals_baseline_that_predates_publication(monkeypatch):
    import sys
    from datetime import datetime, timezone
    sys.path.insert(0, str(ROOT))
    import app.services.campaign_post_publish_watch as W

    monkeypatch.setattr(W, "marketing_today_iso", lambda: "2026-09-07")
    existing = {
        "campaign_id": 252,
        "account_id": "qa",
        "expected_feed_ids": ["f1"],
        "days": {
            "2026-09-04": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-04T20:36:00+00:00",
                "latest_views": 1,
                "latest_contacts": 0,
                "views_delta": 1,
                "contacts_delta": 0,
            },
            "2026-09-05": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-05T06:00:00+00:00",
                "latest_views": 6,
                "latest_contacts": 0,
                "views_delta": 6,
                "contacts_delta": 0,
            },
            "2026-09-06": {
                "baseline_views": 0,
                "baseline_contacts": 0,
                "baseline_captured_at": "2026-09-06T00:10:00+00:00",
                "latest_views": 6,
                "latest_contacts": 0,
                "views_delta": 6,
                "contacts_delta": 0,
            },
        },
    }
    metrics = {
        "matched": 1,
        "active": 1,
        "canonical": 1,
        "views": 6,
        "contacts": 0,
        "items": {
            "f1": {
                "status": "active",
                "views": 6,
                "contacts": 0,
            }
        },
    }
    published = datetime(2026, 9, 4, 21, 14, 36, tzinfo=timezone.utc)
    out = W._update_watch(
        existing,
        metrics=metrics,
        collected_at=datetime(2026, 9, 7, 14, 15, tzinfo=timezone.utc),
        published_at=published,
        expected=1,
        now=datetime(2026, 9, 7, 14, 20, tzinfo=timezone.utc),
        stats_date="2026-09-05",
    )
    day = out["days"]["2026-09-04"]
    assert day["baseline_self_healed"] is True
    assert day["baseline_views"] == 1
    assert day["views_delta"] == 0
    assert day["baseline_captured_at"] == published.isoformat()
    assert out["views_since_baseline"] == 6


def test_safe_mapping_recovery_precedes_lower_priority_first_image_prepare():
    s=_src('app/api/avito.py')
    assert 'KPI_SAFE_ACTION_PRECEDES_FIRST_IMAGE_V1' in s
    start=s.index('KPI_SAFE_ACTION_PRECEDES_FIRST_IMAGE_V1')
    end=s.index('# =====================================================\n        # 6. Остальные операции', start)
    block=s[start:end]
    safe_i=block.index('if safe_present:')
    image_i=block.index('first_image_actions = [')
    assert safe_i < image_i
    assert '"recover_canonical_mapping"' in s[s.rfind('safe_actions = {',0,start):start]
    assert 'stage": "safe_executor"' in block
    assert 'stage": "first_image_prepare"' in block

def test_mapping_recovery_requires_authoritative_feed_before_provider_io():
    s=_src('app/api/avito.py')
    assert 'KPI_MAPPING_RECOVERY_REQUIRES_AUTHORITATIVE_FEED_V1' in s
    start=s.index('def _kpi_exec_recover_canonical_mapping')
    end=s.index('def _kpi_exec_optimize_weak_items', start)
    block=s[start:end]
    feed_guard=block.index('KPI_MAPPING_RECOVERY_REQUIRES_AUTHORITATIVE_FEED_V1')
    provider_call=block.index('_recover_canonical_identities_batch_from_autoload(')
    assert feed_guard < provider_call
    assert 'not_applicable_no_authoritative_feed' in block
    assert 'provider_status": "not_called"' in block
    assert 'provider_reason": "no_authoritative_feed_items"' in block
    assert 'changed_avito": False' in block
    planner_marker=s.index('KPI_MAPPING_RECOVERY_PLANNER_REQUIRES_FEED_V1')
    assert planner_marker > s.index('KPI_MAPPING_RECOVERY_ACTIVE_OBLIGATION_V1')
    planner=s[planner_marker-300:planner_marker+700]
    assert 'if _title_feed_ids and _title_cohort_capacity_gap > 0' in planner
    assert 'recover_canonical_mapping' in planner


def test_mapping_recovery_minute_rescue_is_narrow_nonmoney_singleton_lane():
    s=_src('boris_background_worker.py')
    assert 'KPI_MAPPING_RECOVERY_MINUTE_RESCUE_V1' in s
    start=s.index('KPI_MAPPING_RECOVERY_MINUTE_RESCUE_V1')
    end=s.index('def _campaign_post_publish_watch_loop', start)
    block=s[start:end]
    assert '/root/BORIS/backend/run/kpi_goal_runner.lock' in block
    assert 'LOCK_EX | fcntl.LOCK_NB' in block
    assert 'marketing_service_entitlement' in block
    assert '!= "active"' in block
    assert 'reliability_kill_switches' in block
    assert 'ap_mode not in {"goal_auto", "always_auto"}' in block
    assert 'selected = due[:max(1, min(int(limit or 2), 2))]' in block
    assert '_kpi_exec_recover_canonical_mapping(' in block
    assert 'kpi_goal_tick(' not in block
    assert '"changed_avito": False' in block
    assert '"ai_calls_made": 0' in block
    wire=s[s.index('def start_singleton_pollers'):s.index('if singleton_lock is not None')]
    assert '("mapping_recovery_rescue", _mapping_recovery_rescue_loop)' in wire

def test_non_money_effect_parallel_lane_never_executes_paid_ai():
    s=_src('app/api/avito.py')
    assert 'KPI_NON_MONEY_PARALLEL_AI_FENCE_V1' in s
    start=s.index('KPI_NON_MONEY_PARALLEL_PREPARE_DURING_EFFECT_V1')
    end=s.index('# -------------------------------------------------\n            # Rollback lifecycle', start)
    block=s[start:end]
    assert '"stage": "ai_prepare_deferred"' in block
    assert '"status": "deferred_non_money_ai_fence"' in block
    assert '"ai_calls_made": 0' in block
    assert '"ai_cost_rub": 0.0' in block
    fenced=block[block.index('KPI_NON_MONEY_PARALLEL_AI_FENCE_V1'):]
    assert 'kpi_ai_prepare_execute(' not in fenced

def test_owner_workspace_mapping_recovery_truth_survives_transient_planner_block():
    s=_src('app/api/avito.py')
    assert 'KPI_OWNER_MAPPING_RECOVERY_TRUTH_V1' in s
    start=s.index('KPI_OWNER_MAPPING_RECOVERY_TRUTH_V1')
    end=s.index('@router.get("/kpi_action_memory")', start)
    block=s[start:end]
    assert 'Storage.key == "kpi_mapping_recovery_state"' in block
    assert '"waiting_official_mapping_evidence"' in block
    assert '"provider_deferred"' in block
    assert 'or _mapping_owner_active_ui' in block
    assert '"mapping_recovery"' in block
    assert '"recover_canonical_mapping"' in block
    assert 'сам повторит проверку после' in block
    assert 'AI, публикации и деньги' in block


def test_active_publish_obligation_serializes_proposal_queue():
    s=_src('app/api/avito.py')
    tick=s[s.index('def kpi_goal_tick('):s.index('@router.post("/kpi_ai_prepare_execute")')]
    active_i=tick.index('if active:')
    publish_i=tick.index('if op_status == "publish_requested":', active_i)
    confirm_i=tick.index('result = kpi_confirm_publish(', publish_i)
    return_i=tick.index('return {', confirm_i)
    proposal_i=tick.find('kpi_proposal_apply_next(', active_i)
    assert publish_i < confirm_i < return_i
    # Any proposal-queue advancement must occur only after the active
    # publication branch has already returned, never in parallel with it.
    assert proposal_i == -1 or return_i < proposal_i


def test_exact_managed_supply_caps_impossible_title_target_only_with_fresh_matching_proof():
    s=_src('app/api/avito.py')
    assert 'AUTOLOAD_EXACT_MANAGED_SUPPLY_SNAPSHOT_V1' in s
    assert 'KPI_TITLE_MANAGED_SUPPLY_CAP_V1' in s
    assert 'KPI_TITLE_MANAGED_SUPPLY_CAP_PROOF_V1' in s
    assert '"active_ids_fingerprint"' in s
    assert '"feed_fingerprint"' in s
    assert '_proof_age <= 8*3600' in s
    assert '_title_cohort_target=min(_title_cohort_raw_target,_title_managed_supply_cap)' in s
    assert '"raw_target_items": _title_cohort_raw_target' in s
    assert '"managed_supply_cap": _title_managed_supply_cap' in s
    assert '"managed_supply_proof": _title_managed_supply_evidence' in s


def test_guardian_separates_mapping_gap_from_revision_mutation_hold():
    s=_src('guardian_lifecycle_snapshot.py')
    assert 'GUARDIAN_TITLE_IDENTITY_VS_MUTATION_CAPACITY_V1' in s
    assert 'GUARDIAN_TITLE_MANAGED_SUPPLY_CAP_V1' in s
    assert '"published_identity_bound","publication_rejected_bound"' in s
    assert 'mapping_capacity_gap=max(0,target-len(exact_identity))' in s
    assert 'mutation_capacity_gap=max(0,target-len(writable))' in s
    assert 'capacity_gap=mapping_capacity_gap' in s
    assert 'severity="revision_hold"' in s
    assert '"mapping_capacity_gap":mapping_capacity_gap' in s
    assert '"mutation_capacity_gap":mutation_capacity_gap' in s
    assert '"revision_hold_active":len(revision_hold)' in s
    assert '"mutation_blocked_by_revision_state":bool(' in s


def test_experiment_journal_escalates_only_real_mapping_gap():
    s=_src('app/services/marketing_experiment_journal.py')
    assert 'EXPERIMENT_JOURNAL_MAPPING_GAP_TRUTH_V1' in s
    assert "mapping_gap=int((title_health or {}).get(" in s
    assert "'mapping_capacity_gap'" in s
    assert "if mapping_gap>0 and title_severity.startswith('degraded')" in s
    assert 'if writable_gap>0' not in s
