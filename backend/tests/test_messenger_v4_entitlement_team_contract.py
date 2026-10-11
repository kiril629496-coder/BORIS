from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.subscription_gate import base_subscription_access

ROOT = Path("/root/BORIS")
TEAM = (ROOT / "backend/app/api/team.py").read_text(encoding="utf-8")
AUTH = (ROOT / "backend/app/api/auth.py").read_text(encoding="utf-8")
INBOX = (ROOT / "backend/app/api/inbox_slots.py").read_text(encoding="utf-8")
BILLING = (ROOT / "backend/app/api/billing.py").read_text(encoding="utf-8")


class _Result:
    def __init__(self, *, scalar_value=None, mapping_value=None):
        self._scalar_value = scalar_value
        self._mapping_value = mapping_value

    def scalar(self):
        return self._scalar_value

    def mappings(self):
        return self

    def first(self):
        return self._mapping_value


class _FakeDb:
    def __init__(self, account_billing: str, owner_billing: str):
        self.account_billing = account_billing
        self.owner_billing = owner_billing

    def execute(self, statement, params):
        sql = str(statement)
        if "FROM accounts" in sql:
            return _Result(mapping_value={
                "is_own": False,
                "billing_mode": "auto",
                "owner_user_id": 60,
            })
        if "FROM storage" in sql:
            if params.get("a") == "user:60":
                return _Result(scalar_value=self.owner_billing)
            return _Result(scalar_value=self.account_billing)
        raise AssertionError(sql)


def test_planeta_active_entitlement_does_not_show_expired_when_owner_clock_is_future():
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    account_billing = '{"tier":"tariff_1","paid_until":"2026-09-27T00:00:00+00:00"}'
    owner_billing = '{"tier":"tariff_1","paid_until":"2026-10-30T00:00:00+00:00"}'
    result = base_subscription_access(_FakeDb(account_billing, owner_billing), "fixture_account", now=now)
    assert result["allowed"] is True
    assert result["state"] == "active"
    assert result["source"] == "owner_billing"
    assert result["paid_until"].startswith("2026-10-30")


def test_subscription_authority_is_shared_by_auth_and_inbox():
    assert "from app.services.subscription_gate import base_subscription_access" in AUTH
    assert "from app.services.subscription_gate import base_subscription_access" in INBOX
    assert "_slot_access_allowed" in INBOX


def test_team_manage_uses_canonical_permission_policy_and_no_raw_permission_name():
    assert "from app.services.command_policy import has_permission" in TEAM
    assert 'has_permission(db, user, account_id, "can_manage_team")' in TEAM
    assert "Требуется team.manage" not in TEAM
    assert "У вашей роли нет доступа к управлению командой" in TEAM


def test_manual_base_extension_does_not_fabricate_payment_ledger():
    assert "def extend_base_access(" in BILLING
    assert '@router.post("/base-access/extend")' in BILLING
    assert '"payment_ledger_touched": False' in BILLING
    block = BILLING.split("def extend_base_access(", 1)[1].split("# --- HTTP-эндпоинты ---", 1)[0]
    assert "INSERT INTO payments" not in block
