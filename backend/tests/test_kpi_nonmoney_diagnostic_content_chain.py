import inspect
from app.api import avito


def test_nonmoney_expensive_cpl_diagnostic_chains_into_content():
    src=inspect.getsource(avito.kpi_goal_tick)
    assert 'KPI_NON_MONEY_DIAGNOSTIC_THEN_CONTENT_V1' in src
    assert 'non_money_cpl_diagnostic_content_fallback' in src
    diag=src.index('fallback_result = kpi_execute_cycle')
    content=src.index('content_fallback_action', diag)
    assert content > diag
    block=src[diag:content+300]
    assert 'kpi_prepare_cycle' in block
    assert '"action": "optimize_weak_items"' in block
    assert 'changed_avito":False' in block
