from unittest.mock import patch

from app.ext_api import aiprov as A


def _states(openai_usable=True, local_usable=True):
    return {
        "openai": {"state": A.AVAILABLE if openai_usable else A.UNAVAILABLE_BILLING, "usable": openai_usable},
        "local_renderer": {"state": A.AVAILABLE, "usable": local_usable},
    }


def test_banner_prefers_openai_when_healthy():
    with patch.object(A, "state", return_value=_states(True, True)), patch.object(A, "owner_policy", return_value=set()):
        assert A.candidates(A.BANNER)[:2] == ["openai", "local_renderer"]


def test_banner_falls_back_to_local_when_openai_billing_blocked():
    with patch.object(A, "state", return_value=_states(False, True)), patch.object(A, "owner_policy", return_value=set()):
        assert A.candidates(A.BANNER) == ["local_renderer"]


def test_non_banner_capabilities_keep_cost_first_policy():
    old_order = A.ORDER[A.TEXT]
    old_cost = dict(A.COST)
    try:
        A.ORDER[A.TEXT] = ("openai", "local_renderer")
        A.COST["openai"] = 2
        A.COST["local_renderer"] = 0
        with patch.object(A, "state", return_value=_states(True, True)), patch.object(A, "owner_policy", return_value=set()):
            assert A.candidates(A.TEXT)[:2] == ["local_renderer", "openai"]
    finally:
        A.ORDER[A.TEXT] = old_order
        A.COST.clear(); A.COST.update(old_cost)
