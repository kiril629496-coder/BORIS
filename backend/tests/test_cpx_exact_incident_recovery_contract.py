import inspect
import run.cpx_exact_incident_recovery as recovery


def test_recovery_reconciles_before_money_write_gate():
    src=inspect.getsource(recovery.run)
    assert 'CPX_INCIDENT_RECOVERY_COMPLETE_STATE_V1' in src
    assert "out['status']='paused_money_guard'" in src
    assert src.index("reconciled_exact_state") < src.index("out['status']='paused_money_guard'")


def test_complete_provider_state_closes_durable_incident():
    src=inspect.getsource(recovery.run)
    assert "state['status']='complete'" in src
    assert "state['pending_items']=0" in src
    assert "out['incident_closed']=True" in src


def test_preflight_preserves_money_decision_without_early_raise():
    src=inspect.getsource(recovery._preflight)
    assert 'CPX_INCIDENT_RECOVERY_READ_FIRST_V1' in src
    assert "money=check_raise_allowed(account_id) or {}" in src
    assert "raise RuntimeError('money_guard:'" not in src
