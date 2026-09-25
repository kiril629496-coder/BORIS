import importlib.util
import unittest
from email.message import EmailMessage
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mcn-mailbox-discovery.py"
SPEC = importlib.util.spec_from_file_location("mcn_mailbox_discovery", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(M)


class McnMailboxDiscoveryTests(unittest.TestCase):
    def test_russian_registration_letter_is_complete(self):
        out = M.analyze_text(
            "MCN\n"
            "Сервер SIP: sip.mcn.ru\n"
            "Логин SIP: 1234567\n"
            "Пароль SIP: SuperSecretValue\n"
            "Номер MCN: +7 999 123-45-67\n"
        )
        self.assertTrue(out["candidate_complete"])
        self.assertTrue(out["registration_complete"])
        self.assertTrue(out["fields_found"]["registrar"])
        self.assertTrue(out["fields_found"]["username"])
        self.assertTrue(out["fields_found"]["password"])
        self.assertTrue(out["fields_found"]["did"])

    def test_official_english_labels_are_complete(self):
        out = M.analyze_text(
            "Proxy Server IP / Domain: sip.mcn.ru\n"
            "User ID / Account: 7654321\n"
            "Password: AnotherSecret\n"
            "DID: +74951234567\n"
        )
        self.assertTrue(out["registration_complete"])

    def test_generic_number_is_not_a_did(self):
        out = M.analyze_text(
            "Proxy Server IP / Domain: sip.mcn.ru\n"
            "User ID / Account: 7654321\n"
            "Password: AnotherSecret\n"
            "Number: 7654321\n"
        )
        self.assertFalse(out["fields_found"]["did"])
        self.assertFalse(out["candidate_complete"])

    def test_ip_auth_letter_is_complete_without_password(self):
        out = M.analyze_text(
            "MCN телефония\n"
            "IP-адрес: 85.94.32.2/32\n"
            "Телефонный номер: +7 999 000-11-22\n"
        )
        self.assertTrue(out["ip_complete"])
        self.assertTrue(out["candidate_complete"])
        self.assertFalse(out["fields_found"]["password"])

    def test_message_output_never_contains_body_or_secret_values(self):
        msg = EmailMessage()
        msg["From"] = "Manager <manager@example.test>"
        msg["To"] = "owner@example.test"
        msg["Subject"] = "Настройки MCN SIP"
        msg.set_content(
            "Сервер SIP: sip.mcn.ru\n"
            "Логин SIP: 1234567\n"
            "Пароль SIP: NEVER_PRINT_THIS_SECRET\n"
            "DID: +79991234567\n"
        )
        out = M.analyze_message(msg.as_bytes(), "42")
        serialized = repr(out)
        self.assertTrue(out["candidate_complete"])
        self.assertNotIn("NEVER_PRINT_THIS_SECRET", serialized)
        self.assertNotIn("1234567", serialized)
        self.assertNotIn("+79991234567", serialized)
        self.assertNotIn("sip.mcn.ru", serialized)
        self.assertNotIn("body", out)


if __name__ == "__main__":
    unittest.main()
