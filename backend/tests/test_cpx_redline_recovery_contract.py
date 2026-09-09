from pathlib import Path


def _src():
    return Path("app/api/cpx_advisor.py").read_text(encoding="utf-8")


def test_red_cpl_blocks_all_raise_recommendations_before_classification():
    s = _src()
    marker = s.index("CPX_ADVISOR_RED_CPL_RECOVERY_V1")
    classify = s.index("# 5) формула: разбор по объявлениям")
    assert marker < classify
    block = s[marker:classify]
    assert "_cpl_redline_block = bool(" in block
    assert "block_raises = True" in block
    assert "float(acct_cpl) > float(max_cpl)" in block


def test_under_kpi_preserve_reach_has_red_cpl_paid_waste_exception():
    s = _src()
    start = s.index("UNDER_KPI_GROWTH_BEFORE_SHRINK_V1")
    end = s.index("try:\n            from app.services.avito_position_monitor", start)
    block = s[start:end]
    assert "_red_cpl_paid_waste = bool(" in block
    assert "_cpl_redline_block" in block
    assert 'entry.get("promotion_active")' in block
    assert "bid_rub is not None" in block
    assert "float(bid_rub) > 0" in block
    assert "and not _red_cpl_paid_waste" in block
    assert '"red_cpl_paid_zero_contact_waste"' in block
    assert "archive.append(entry)" in block


def test_zero_contact_evidence_threshold_still_precedes_red_cpl_lower_lane():
    s = _src()
    start = s.index("# 5) формула: разбор по объявлениям")
    end = s.index("UNDER_KPI_GROWTH_BEFORE_SHRINK_V1", start)
    block = s[start:end]
    assert "elif v7 < MIN_VIEWS_FOR_JUDGEMENT_7D:" in block
    assert "elif v7 < ARCHIVE_MIN_VIEWS_7D:" in block
    assert "elif _is_archived(db, account_id, iid):" in block


def test_measurement_supersede_requires_exact_item_provider_query_scope():
    s = _src()
    assert "CPX_MEASURE_PROVIDER_QUERY_SCOPE_V1" in s
    assert "_bids_queried_item_ids = set()" in s
    assert "_bids_queried_item_ids.update(" in s
    assert "int(x) for x in _chunk_ids if str(x).isdigit()" in s
    start = s.index("CPX_MEASURE_MONEY_IDENTITY_ONLY_V1")
    end = s.index("if _truth_reason:", start)
    block = s[start:end]
    assert "_item_cpx_truth_complete" in block
    assert "_measure_iid in _bids_queried_item_ids" in block
    assert "if _wc not in" not in block
    assert '"write_capability_lost"' not in block
    assert "_item_cpx_truth_complete and _expected_bid is not None" in block


def test_all_raise_boundaries_converge_on_canonical_money_guard():
    advisor = Path("app/api/cpx_advisor.py").read_text(encoding="utf-8")
    autonomy = Path("app/services/autonomy.py").read_text(encoding="utf-8")
    promo = Path("app/api/cpxpromo.py").read_text(encoding="utf-8")
    resume = Path("cpx_budget_resume.py").read_text(encoding="utf-8")

    assert "FINAL_RAISE_RED_CPL_ECONOMICS_V1" in advisor
    assert 'if operation == "cpx.raise_bid":' in autonomy
    assert "check_raise_allowed as _check_raise_allowed" in autonomy
    assert "can_execute_live_action as _direct_live_guard" in promo
    assert "_direct_guard = _direct_live_guard(" in promo
    assert '"cpx.raise_bid"' in resume
    assert "final_money = check_raise_allowed(account_id) or {}" in resume


def test_red_cpl_lower_is_not_frozen_by_raise_measurement_backlog():
    from unittest.mock import patch
    import cpx_advisor_runner as runner

    class _DB:
        def close(self):
            pass

    intraday = {
        "kpi_pace": {"state": "AHEAD"},
        "decision": "HOLD_RAISE",
        "selected_for_execution": 0,
        "candidates_total": 10,
        "top": [],
        "mandate": {"max_actions_run": 5},
    }
    recs = {"lower": [{
        "id": 123456,
        "bid_rub": 26.0,
        "promotion_active": True,
        "reason_code": "red_cpl_paid_zero_contact_waste",
    }]}
    advice = {
        "measurement_cycle": {
            "waiting": 2,
            "backlog_compaction": {"cap": 2},
        }
    }
    with patch("app.db.session.SessionLocal", return_value=_DB()), \
         patch("app.services.intraday.select_intraday_raise_candidates", return_value=intraday):
        plan, mode, diagnostics = runner._build_plan("qa_red_cpl", recs, advice=advice)

    assert plan == [({"id": 123456, "source": "red_cpl_recovery"}, "lower")]
    assert mode.startswith("RED_CPL_RECOVERY:")
    assert diagnostics["reason"] == "red_cpl_lower_during_raise_measurement_wait"
    assert diagnostics["planned_after_guard"] == 1
    assert diagnostics["measurement_waiting"] == 2
    assert diagnostics["measurement_cap"] == 2


def test_raise_measurement_backlog_still_blocks_when_no_red_cpl_lower_exists():
    from unittest.mock import patch
    import cpx_advisor_runner as runner

    class _DB:
        def close(self):
            pass

    intraday = {
        "kpi_pace": {"state": "BEHIND"},
        "decision": "RAISE_CANDIDATES",
        "selected_for_execution": 5,
        "candidates_total": 10,
        "top": [{"status": "eligible", "item_id": 1}],
        "mandate": {"max_actions_run": 5},
    }
    advice = {
        "measurement_cycle": {
            "waiting": 2,
            "backlog_compaction": {"cap": 2},
        }
    }
    with patch("app.db.session.SessionLocal", return_value=_DB()), \
         patch("app.services.intraday.select_intraday_raise_candidates", return_value=intraday):
        plan, mode, diagnostics = runner._build_plan("qa_wait", {"lower": []}, advice=advice)

    assert plan == []
    assert mode.startswith("MEASUREMENT_WAIT:")
    assert diagnostics["reason"] == "account_measurement_backlog_wait"
    assert diagnostics["planned_after_guard"] == 0


def test_advisor_queries_full_active_promotion_inventory_in_bounded_chunks():
    s = _src()
    assert "CPX_MEASURE_PROVIDER_QUERY_PRIORITIZES_WAITING_V2" in s
    assert "CPX_ADVISOR_FULL_ACTIVE_PROMOTION_TRUTH_V1" in s
    assert "item_ids = _priority_ids" in s
    assert "item_ids = _priority_ids[:200]" not in s
    assert "for _pos in range(0, len(item_ids), 200):" in s
    assert "_chunk_ids = item_ids[_pos:_pos + 200]" in s
    assert "_bids_queried_item_ids.update(" in s
    assert "len(_bids_queried_item_ids) == len(set(item_ids))" in s
