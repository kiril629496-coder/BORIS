import time
from datetime import datetime, timezone
from unittest.mock import patch

from app.api import cpx_advisor as adv


class _DB:
    def close(self):
        pass


def _kpi():
    return {
        "daily_budget_limit_rub": 3000.0,
        "max_cost_per_lead_rub": 400.0,
        "daily_budget_authorization": {
            "policy_version": "MONEY_BUDGET_OWNER_PROVENANCE_V1",
            "authorized_by_user_id": 60,
            "daily_budget_limit_rub": 3000.0,
            "source": "authenticated_set_kpi_settings",
        },
    }


def _stats():
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "completeness": {"complete": True, "inventory_complete": True},
        "items": [],
    }


def _spend():
    return {
        "status": "ok",
        "spent_today_rub": 200.0,
        "timestamp": time.time(),
        "spending_date": adv.marketing_today_iso(),
    }


def _load(_db, _account_id, key):
    if key == "kpi_settings":
        return _kpi()
    if key.startswith("daily_stats:"):
        return _stats()
    return {}


def _run(balance):
    ctx = {
        "account_id": "qa_wallet_floor",
        "status": adv.BALANCE_KNOWN,
        "value": float(balance),
        "fetched_at": datetime.utcnow().isoformat(),
    }
    with patch("app.db.session.SessionLocal", return_value=_DB()), \
         patch.object(adv, "_load_json", side_effect=_load), \
         patch("app.services.marketing_money_policy.latest_confirmed_spend", return_value=_spend()), \
         patch("app.services.marketing_money_policy.presence_budget_pressure", return_value={"blocked": False}), \
         patch("app.services.marketing_money_policy.adaptive_budget_brake_policy", return_value={"cutoff_ratio": 0.90}), \
         patch("app.services.marketing_signal_guard.money_spend_signal_eligible", return_value=True):
        return adv.check_raise_allowed("qa_wallet_floor", balance_ctx=ctx)


def test_insufficient_wallet_blocks_before_spend_freshness_is_required():
    ctx = {
        "account_id": "qa_wallet_floor",
        "status": adv.BALANCE_KNOWN,
        "value": 36.11,
        "fetched_at": datetime.utcnow().isoformat(),
    }
    with patch("app.db.session.SessionLocal", return_value=_DB()), \
         patch.object(adv, "_load_json", side_effect=_load), \
         patch("app.services.marketing_money_policy.latest_confirmed_spend") as spend:
        out = adv.check_raise_allowed("qa_wallet_floor", balance_ctx=ctx)
    assert out["allowed"] is False
    assert out["reason_code"] == "blocked_insufficient_balance"
    assert out["red_cpl_rub"] == 400.0
    spend.assert_not_called()


def test_positive_wallet_below_one_red_cpl_is_not_enough_for_auto_raise():
    out = _run(36.11)
    assert out["allowed"] is False
    assert out["reason_code"] == "blocked_insufficient_balance"
    assert out["red_cpl_rub"] == 400.0
    assert out["required_balance_floor_rub"] == 400.0
    assert out["balance"]["value"] == 36.11


def test_wallet_at_red_cpl_floor_passes_balance_floor():
    out = _run(400.0)
    assert out["allowed"] is True
    assert out["reason_code"] is None
