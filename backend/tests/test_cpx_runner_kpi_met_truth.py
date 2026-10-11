from app.db import session as db_session
from app.services import intraday
import cpx_advisor_runner as runner


class _DB:
    def close(self):
        pass


def _state(*, actual=5, target=4, waiting=0, cap=5, decision="RAISE_CANDIDATES"):
    return {
        "decision": decision,
        "selected_for_execution": 5,
        "candidates_total": 10,
        "top": [],
        "mandate": {"max_actions_run": 5},
        "kpi_pace": {
            "state": "BEHIND",
            "target": target,
            "actual": actual,
            "expected_by_now": 2.5,
            "elapsed_share": 0.5,
        },
        "cpl_headroom": {},
        "reach_signal": {},
        "reach_rescue_candidates": [],
    }, {
        "fact": {"item_signal_current_day": True},
        "measurement_cycle": {"waiting": waiting, "cap": cap},
    }


def _run(monkeypatch, state, advice, recs=None):
    monkeypatch.setattr(db_session, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(intraday, "select_intraday_raise_candidates", lambda db, account: state)
    return runner._build_plan("account", recs or {"raise": [], "lower": []}, advice=advice)


def test_kpi_met_is_not_reported_as_content_rescue(monkeypatch):
    state, advice = _state(actual=5, target=4, waiting=0, cap=5)
    plan, mode, diagnostics = _run(monkeypatch, state, advice)
    assert plan == []
    assert mode.startswith("KPI_MET:")
    assert diagnostics["reason"] == "kpi_met"
    assert diagnostics["next_action"] == "observe_kpi_and_preserve_budget"


def test_kpi_met_outranks_raise_measurement_wait(monkeypatch):
    state, advice = _state(actual=5, target=4, waiting=5, cap=5)
    plan, mode, diagnostics = _run(monkeypatch, state, advice)
    assert plan == []
    assert mode.startswith("KPI_MET:")
    assert diagnostics["measurement_waiting"] == 5
    assert diagnostics["measurement_cap"] == 5


def test_below_target_without_proof_remains_content_rescue(monkeypatch):
    state, advice = _state(actual=1, target=4, waiting=0, cap=5)
    plan, mode, diagnostics = _run(monkeypatch, state, advice)
    assert plan == []
    assert mode.startswith("CONTENT_RESCUE:")
    assert diagnostics["reason"] == "no_proven_eligible_converter"


def test_proven_red_cpl_lower_still_precedes_kpi_met(monkeypatch):
    state, advice = _state(actual=5, target=4, waiting=0, cap=5)
    recs = {"raise": [], "lower": [{
        "id": 123,
        "bid_rub": 20,
        "promotion_active": True,
        "reason_code": "red_cpl_paid_zero_contact_waste",
        "boris_measure_status": "ready",
    }]}
    plan, mode, diagnostics = _run(monkeypatch, state, advice, recs=recs)
    assert plan == [({"id": 123, "source": "red_cpl_recovery"}, "lower")]
    assert mode.startswith("RED_CPL_RECOVERY:")
    assert diagnostics["reason"] == "red_cpl_lower_proven_waste"
