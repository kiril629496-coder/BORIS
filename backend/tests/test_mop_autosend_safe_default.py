import unittest

from app.services.brain_recovery import _mop_safe_default_config


class MopAutosendSafeDefaultTests(unittest.TestCase):
    def test_paid_legacy_settings_materialize_manual_approval(self):
        src={"deal_trigger":"first_inquiry","work_schedule":{"enabled":False}}
        out=_mop_safe_default_config(src,True)
        self.assertIsNotNone(out)
        self.assertIs(out["auto_send"],False)
        self.assertEqual(out["auto_send_source"],"system_safe_default")
        self.assertEqual(out["auto_send_policy_version"],"MOP_AUTOSEND_SAFE_DEFAULT_V1")
        self.assertEqual(out["deal_trigger"],"first_inquiry")
        self.assertNotIn("auto_send",src)

    def test_unpaid_account_is_never_mutated(self):
        self.assertIsNone(_mop_safe_default_config({"deal_trigger":"first_inquiry"},False))

    def test_explicit_manual_policy_is_preserved(self):
        self.assertIsNone(_mop_safe_default_config({"auto_send":False},True))

    def test_explicit_auto_policy_is_preserved(self):
        self.assertIsNone(_mop_safe_default_config({"auto_send":True},True))


if __name__=="__main__":
    unittest.main()
