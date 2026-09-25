import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"scripts"/"phone-real-e2e-check.py"
SPEC=importlib.util.spec_from_file_location("phone_real_e2e_check",SCRIPT)
MOD=importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MOD)

class PhoneRealE2EAutoAccountTests(unittest.TestCase):
    def test_discovery_delegates_to_canonical_mcn_resolver(self):
        with patch.object(MOD, "current_mcn_accounts", return_value=["client-a"]) as resolver:
            self.assertEqual(MOD._discover_real_mcn_accounts(), ["client-a"])
        resolver.assert_called_once_with(include_synthetic=False)

    def test_explicit_account_wins(self):
        self.assertEqual(MOD._select_account_id("client-a",["other"]),("client-a","explicit"))

    def test_zero_real_accounts_is_blocker(self):
        self.assertEqual(MOD._select_account_id(None,[]),(None,"no_real_mcn_account"))

    def test_one_real_account_is_auto_selected(self):
        self.assertEqual(MOD._select_account_id(None,["client-a"]),("client-a","auto_single_real_mcn"))

    def test_multiple_real_accounts_are_not_guessed(self):
        self.assertEqual(MOD._select_account_id(None,["client-b","client-a","client-b"]),(None,"multiple_real_mcn_accounts"))

    def test_normalize_global_mcn_action_preserves_boris_waiting_state(self):
        out=MOD._normalize_global_mcn_action({
            "primary_next_action":{
                "code":"mcn_company_card_sent_waiting_reply",
                "actor":"boris",
                "owner_action_required":False,
                "text":"Карточка уже отправлена; ждём MCN",
            }
        })
        self.assertEqual(out["code"],"mcn_company_card_sent_waiting_reply")
        self.assertEqual(out["actor"],"boris")
        self.assertFalse(out["owner_action_required"])

    def test_commercial_truth_reports_activation_path_without_granting_phone(self):
        with patch.object(MOD, "active_phone_entitlements", return_value=[]):
            out = MOD._phone_commercial_activation_truth()
        self.assertEqual(out["status"], "activation_path_available")
        self.assertEqual(out["active_phone_entitlements"], 0)
        self.assertTrue(out["activation_service_ready"])
        self.assertTrue(out["activation_api_ready"])
        self.assertTrue(out["activation_path_ready"])

    def test_commercial_truth_reports_active_entitlement_when_present(self):
        with patch.object(MOD, "active_phone_entitlements", return_value=[
            {"account_id": "client-a", "entitlement_source": "telephony_entitlement"}
        ]):
            out = MOD._phone_commercial_activation_truth()
        self.assertEqual(out["status"], "active_phone_entitlement_present")
        self.assertEqual(out["active_phone_entitlements"], 1)

    def test_main_reports_waiting_external_instead_of_fake_owner_action(self):
        action={
            "code":"mcn_company_card_sent_waiting_reply",
            "actor":"boris",
            "owner_action_required":False,
            "text":"Карточка MCN уже отправлена. BORIS сам ждёт ответ.",
        }
        buf=io.StringIO()
        with patch.object(MOD,"_discover_real_mcn_accounts",return_value=[]), \
             patch.object(MOD,"_global_mcn_next_action",return_value=action), \
             patch("sys.argv",["prog"]), redirect_stdout(buf):
            rc=MOD.main()
        payload=json.loads(buf.getvalue())
        self.assertEqual(rc,2)
        self.assertEqual(payload["status"],"waiting_external")
        self.assertTrue(payload["external_wait"])
        self.assertFalse(payload["primary_next_action"]["owner_action_required"])
        self.assertEqual(payload["primary_next_action"]["actor"],"boris")

if __name__=="__main__":
    unittest.main()
