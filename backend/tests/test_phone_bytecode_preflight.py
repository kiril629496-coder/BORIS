import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phone-python-bytecode-preflight.py"
SPEC = importlib.util.spec_from_file_location("phone_bytecode_preflight", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(M)


class PhoneBytecodePreflightTests(unittest.TestCase):
    def test_unrelated_syntax_error_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "telephony_guardian_runner.py").write_text("x=1\n", encoding="utf-8")
            unrelated = root / "app/services/lead_radar/open_web.py"
            unrelated.parent.mkdir(parents=True, exist_ok=True)
            unrelated.write_text('x = "broken"\nthis is ???\n', encoding="utf-8")
            files = M.source_files(root)
            self.assertIn(root / "telephony_guardian_runner.py", files)
            self.assertNotIn(unrelated, files)
            ok, errors = M.check_files(files)
            self.assertTrue(ok, errors)

    def test_phone_syntax_error_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            phone = root / "telephony_guardian_runner.py"
            phone.write_text("if:\n", encoding="utf-8")
            files = M.source_files(root)
            ok, errors = M.check_files(files)
            self.assertFalse(ok)
            self.assertTrue(any("telephony_guardian_runner.py" in x for x in errors))

    def test_real_scope_has_expected_critical_files(self):
        rel = {str(p.relative_to(M.ROOT)) for p in M.source_files(M.ROOT)}
        self.assertIn("telephony_guardian_runner.py", rel)
        self.assertIn("app/services/telephony_core.py", rel)
        self.assertIn("app/services/mcn_core.py", rel)
        self.assertIn("app/services/asterisk_gateway.py", rel)
        self.assertIn("scripts/phone-guardian-contract-check.py", rel)


if __name__ == "__main__":
    unittest.main()
