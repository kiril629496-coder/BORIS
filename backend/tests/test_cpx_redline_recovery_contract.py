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
    assert "int(x) for x in item_ids if str(x).isdigit()" in s
    start = s.index("CPX_MEASURE_MONEY_IDENTITY_ONLY_V1")
    end = s.index("if _truth_reason:", start)
    block = s[start:end]
    assert "_item_cpx_truth_complete" in block
    assert "_measure_iid in _bids_queried_item_ids" in block
    assert "if _wc not in" not in block
    assert '"write_capability_lost"' not in block
    assert "_item_cpx_truth_complete and _expected_bid is not None" in block
