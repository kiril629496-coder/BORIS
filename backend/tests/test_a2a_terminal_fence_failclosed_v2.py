from pathlib import Path


def test_terminal_create_fence_is_direct_and_fail_closed():
    src = Path('app/ext_api/a2a.py').read_text()
    assert 'BUSINESS_GOAL_TERMINAL_CREATE_FENCE_V2_FAIL_CLOSED' in src
    block = src[src.index('BUSINESS_GOAL_TERMINAL_CREATE_FENCE_V2_FAIL_CLOSED'):src.index('if not goal or not str(goal).strip():')]
    assert "blocked_reason='business_goal_already_confirmed'" in block
    assert 'business_goal_terminal_job_ids()' not in block
    assert 'except Exception' not in block
