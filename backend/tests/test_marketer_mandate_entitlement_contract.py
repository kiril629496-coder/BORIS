import unittest
from app.api.cpx_advisor import _marketer_mandate_entitlement


class MarketerMandateEntitlementContract(unittest.TestCase):
    def test_current_paid_period_is_entitled(self):
        ok,e=_marketer_mandate_entitlement(
            {"tier":"tariff_1","unlimited":False},
            {"state":"active","source":"billing.paid_until"},
        )
        self.assertTrue(ok)
        self.assertEqual(e["state"],"active")

    def test_tariff_label_without_current_period_is_not_entitled(self):
        ok,_=_marketer_mandate_entitlement(
            {"tier":"tariff_1","unlimited":False},
            {"state":"expired","source":"billing.paid_until"},
        )
        self.assertFalse(ok)

    def test_unlimited_metadata_does_not_grant_marketer_money_authority(self):
        ok,e=_marketer_mandate_entitlement(
            {"tier":"none","unlimited":True},
            {"state":"expired","source":"payment_status"},
        )
        self.assertFalse(ok)
        self.assertEqual(e["state"],"expired")
        self.assertEqual(e["source"],"payment_status")

    def test_unknown_non_unlimited_is_fail_closed(self):
        ok,_=_marketer_mandate_entitlement(
            {"tier":"none","unlimited":False},
            {"state":"unknown","source":"no_explicit_current_paid_period"},
        )
        self.assertFalse(ok)


if __name__=="__main__":
    unittest.main()
