import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "frontend" / "app" / "phone-app" / "mcnSettingsParser.mjs"


class PhoneMcnSettingsParserTests(unittest.TestCase):
    def _parse(self, text_value: str) -> dict:
        js = r"""
import {pathToFileURL} from 'node:url';
const parserPath = process.argv[1];
const input = process.argv[2];
const mod = await import(pathToFileURL(parserPath).href);
const out = mod.parseMcnSettingsText(input);
process.stdout.write(JSON.stringify(out));
"""
        cp = subprocess.run(
            ["node", "--input-type=module", "-e", js, str(PARSER), text_value],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        return json.loads(cp.stdout)

    def test_russian_full_letter_parses_for_one_click_onboarding(self):
        out = self._parse(
            "Сервер SIP: sip.mcn.ru\n"
            "Логин SIP: 1234567\n"
            "Пароль SIP: Qa;Pass:=42\n"
            "Номер MCN: +7 (999) 123-45-67\n"
        )
        self.assertEqual(out["registrar"], "sip.mcn.ru")
        self.assertEqual(out["username"], "1234567")
        self.assertEqual(out["password"], "Qa;Pass:=42")
        self.assertEqual(out["number"], "+79991234567")
        self.assertTrue({"registrar", "username", "password", "number"}.issubset(out["found"]))

    def test_official_equipment_labels_are_supported(self):
        out = self._parse(
            "Proxy Server IP / Domain: sip.mcn.ru\n"
            "User ID / Account: 7654321\n"
            "Password: Zx!9;abc\n"
            "DID: 8 (999) 765-43-21\n"
        )
        self.assertEqual(out["registrar"], "sip.mcn.ru")
        self.assertEqual(out["username"], "7654321")
        self.assertEqual(out["password"], "Zx!9;abc")
        self.assertEqual(out["number"], "+79997654321")

    def test_ip_mode_inputs_parse_without_sip_credentials(self):
        out = self._parse(
            "IP-адрес: 85.94.32.2/32\n"
            "IP адрес: 85.94.63.222\n"
            "Номер телефона: +7 999 000-11-22\n"
        )
        self.assertEqual(out["source_ips"], ["85.94.32.2/32", "85.94.63.222"])
        self.assertEqual(out["number"], "+79990001122")
        self.assertEqual(out["username"], "")
        self.assertEqual(out["password"], "")

    def test_generic_number_field_is_not_mistaken_for_did(self):
        out = self._parse(
            "Number: 1234567\n"
            "User ID / Account: 1234567\n"
            "Password: secret\n"
            "Proxy Server IP / Domain: sip.mcn.ru\n"
        )
        self.assertEqual(out["username"], "1234567")
        self.assertEqual(out["number"], "")


if __name__ == "__main__":
    unittest.main()
