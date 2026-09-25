import json

import daily_stats_collector as stats


def test_provider_alias_prefers_active_account_slot_canonical():
    rows = [
        ("inbox_60", "224840985", json.dumps({}), 0),
        ("planetazayavki_65985", "224840985", json.dumps({"target_leads_per_day": 5, "daily_budget_limit_rub": 3000}), 3000),
        ("other", "999", json.dumps({}), 0),
    ]
    selected = stats._dedupe_provider_alias_rows(rows, {"224840985": "planetazayavki_65985"})
    assert selected == ["other", "planetazayavki_65985"]


def test_provider_alias_fallback_prefers_money_kpi_alias():
    rows = [
        ("legacy", "42", json.dumps({}), 0),
        ("money", "42", json.dumps({"target_leads_per_day": 3, "daily_budget_limit_rub": 1700}), 1700),
    ]
    assert stats._dedupe_provider_alias_rows(rows, {}) == ["money"]


def test_accounts_without_provider_identity_remain_independent():
    rows = [
        ("a", None, json.dumps({}), 0),
        ("b", None, json.dumps({}), 0),
    ]
    assert stats._dedupe_provider_alias_rows(rows, {}) == ["a", "b"]
