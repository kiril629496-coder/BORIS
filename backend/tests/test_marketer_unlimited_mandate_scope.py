import unittest

from app.api.cpx_advisor import _marketer_mandate_entitlement


class MarketerEntitlementScopeTests(unittest.TestCase):
    def test_unlimited_does_not_override_expired_paid_period(self):
        ok,evidence=_marketer_mandate_entitlement(
            {"tier":"none","unlimited":True},
            {"state":"expired","source":"payment_status","until":"2026-08-03T00:00:00+00:00"},
        )
        self.assertFalse(ok)
        self.assertEqual(evidence["state"],"expired")

    def test_unlimited_does_not_override_unknown_paid_period(self):
        ok,evidence=_marketer_mandate_entitlement(
            {"tier":"none","unlimited":True},
            {"state":"unknown","source":"no_explicit_current_paid_period","until":None},
        )
        self.assertFalse(ok)
        self.assertEqual(evidence["state"],"unknown")

    def test_active_paid_period_remains_authorized(self):
        ok,evidence=_marketer_mandate_entitlement(
            {"tier":"tariff_1","unlimited":False},
            {"state":"active","source":"billing.paid_until"},
        )
        self.assertTrue(ok)
        self.assertEqual(evidence["source"],"billing.paid_until")

    def test_tariff_name_alone_never_authorizes_money(self):
        ok,evidence=_marketer_mandate_entitlement(
            {"tier":"tariff_1","unlimited":False},
            {"state":"unknown","source":"no_explicit_current_paid_period"},
        )
        self.assertFalse(ok)
        self.assertEqual(evidence["state"],"unknown")

    def test_expired_non_unlimited_period_stays_blocked(self):
        ok,evidence=_marketer_mandate_entitlement(
            {"tier":"tariff_2","unlimited":False},
            {"state":"expired","source":"billing.paid_until"},
        )
        self.assertFalse(ok)
        self.assertEqual(evidence["state"],"expired")


if __name__=="__main__":
    unittest.main()
