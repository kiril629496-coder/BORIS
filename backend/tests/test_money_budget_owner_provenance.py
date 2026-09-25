import inspect
import unittest

from app.api import avito
from app.api import cpx_advisor


class MoneyBudgetOwnerProvenanceTests(unittest.TestCase):
    def test_kpi_budget_write_requires_authenticated_user_and_persists_provenance(self):
        src = inspect.getsource(avito.set_kpi_settings)
        self.assertIn("MONEY_BUDGET_OWNER_PROVENANCE_V1", src)
        self.assertIn("authorized_by_user_id", src)
        self.assertIn("authenticated_set_kpi_settings", src)
        self.assertIn("is_platform_owner", src)

    def test_paid_tariff_raise_requires_budget_provenance(self):
        src = inspect.getsource(cpx_advisor.ensure_paid_tariff_raise_mandate)
        self.assertIn("MONEY_BUDGET_OWNER_PROVENANCE_GUARD_V1", src)
        self.assertIn("daily_budget_owner_provenance_missing", src)
        self.assertIn("cpx.lower_bid", src)

    def test_first_launch_raise_requires_same_budget_provenance(self):
        src = inspect.getsource(cpx_advisor._ensure_new_item_launch_mandate)
        self.assertIn("MONEY_BUDGET_OWNER_PROVENANCE_GUARD_V1", src)
        self.assertIn("daily_budget_owner_provenance_missing", src)
        self.assertIn("confirmation_version='moneyprov_v1'", src)


if __name__ == "__main__":
    unittest.main()
