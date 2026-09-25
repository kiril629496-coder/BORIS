from pathlib import Path

def test_business_terminal_db_guard_source_present():
    s=Path('sql/a2a_business_terminal_guard_v1.sql').read_text()
    assert 'BUSINESS_GOAL_TERMINAL_ORDER_DB_GUARD_V1' in s
    assert "blocked_reason = 'business_goal_already_confirmed'" in s
    assert 'BEFORE INSERT ON ext_a2a_orders' in s
    assert 'BORIS_A2A_GUARD' in s
