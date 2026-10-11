"""Read-only verification of already-installed Editorial release provenance."""
import ast
import hashlib
import importlib.machinery
import json
from pathlib import Path
import tempfile
import types
import unittest

ROOT=Path('/root/BORIS/backend/client-data/u2/social-editorial')
AUTH=Path('/usr/local/share/boris-editorial-client-v3.json')
GUARD=Path('/usr/local/sbin/boris-editorial-client-guard.py')
EXPECTED_AUTH='0ac9baee70be8763488a68dfac39fd187987fc82ef058bbf647a5772adbdcf19'
EXPECTED_GUARD='b0d66c431ff12e7900561ad621ee5a94334bfcf0f14c5f9fe3342bae8824cbb8'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def literals(path):
    out={}
    for n in ast.parse(path.read_text()).body:
        if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant):
            for t in n.targets:
                if isinstance(t,ast.Name):out[t.id]=n.value.value
    return out


class EditorialIdentityTests(unittest.TestCase):
    def test_installer_independently_pins_the_installed_release(self):
        values=literals(ROOT/'tools/install_editorial_client_guard_20260905.py')
        self.assertEqual(values['AUTH_SHA'],EXPECTED_AUTH)
        self.assertEqual(values['GUARD_SHA'],EXPECTED_GUARD)

    def test_installed_and_canonical_source_copies_are_identical(self):
        self.assertEqual(sha(AUTH),EXPECTED_AUTH)
        self.assertEqual(sha(ROOT/'authority/editorial_client_v3.json'),EXPECTED_AUTH)
        self.assertEqual(sha(GUARD),EXPECTED_GUARD)
        self.assertEqual(sha(ROOT/'tools/editorial_client_guard.py'),EXPECTED_GUARD)

    def test_guard_pins_exact_authority(self):
        self.assertEqual(literals(GUARD)['AUTHORITY_FILE_SHA256'],EXPECTED_AUTH)

    def test_tampered_authority_is_still_rejected(self):
        mod=types.ModuleType('editorial_guard_identity_test')
        importlib.machinery.SourceFileLoader(mod.__name__,str(GUARD)).exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'altered.json';p.write_bytes(AUTH.read_bytes()+b' ')
            with self.assertRaisesRegex(RuntimeError,'EDITORIAL_CLIENT_AUTHORITY_HASH_MISMATCH'):
                mod._load_authority(p)

    def test_correct_authority_keeps_exact_scope(self):
        data=json.loads(AUTH.read_text())
        self.assertEqual(data['account_id'],'u2')
        self.assertEqual(data['scope'],'client_config_only_no_shared_runtime_no_transport')
        self.assertEqual(set(data['projects']),{'a374ced25745','f938caa3a944','270dbf472f7f'})

    def test_neighbor_checker_matches_approved_installer_not_stale_version(self):
        current=literals(ROOT/'tools/check_neighbor_scope_health.py')
        self.assertEqual(current['AUTH_SHA'],EXPECTED_AUTH)
        self.assertEqual(current['GUARD_SHA'],EXPECTED_GUARD)


if __name__=='__main__':unittest.main()
