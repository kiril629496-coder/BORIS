import importlib.util
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "scoped_release_certification", ROOT / "scripts" / "scoped_release_certification.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def scope(**updates):
    value = {
        "format": M.FORMAT,
        "policy_version": M.POLICY_VERSION,
        "workstream": "WS-OUTREACH",
        "baseline_build_id": "base",
        "baseline_release": "/release/base",
        "source_commit": "f752362",
        "approved_runtime_files": {},
        "affected_tests": [],
        "integration_tests": [],
        "shared_dependency_paths": [],
    }
    value.update(updates)
    return value


class ScopedReleaseCertificationTests(unittest.TestCase):
    def test_split_test_identity_accepts_only_isolated_unprivileged_database(self):
        account = SimpleNamespace(pw_uid=1234)
        url = "postgresql+psycopg2://sentinelx@/postgres?host=/tmp/boris-email-qa-run&port=55439"
        with mock.patch.object(M.pwd, "getpwnam", return_value=account):
            identity = M.validate_test_identity("sentinelx", url)
        self.assertEqual(identity["user"], "sentinelx")
        self.assertEqual(identity["socket_dir"], "/tmp/boris-email-qa-run")

    def test_split_test_identity_accepts_nested_socket_inside_isolated_root(self):
        account = SimpleNamespace(pw_uid=1234)
        url = "postgresql+psycopg2://sentinelx@/postgres?host=/tmp/boris-email-qa-run/socket&port=55439"
        with mock.patch.object(M.pwd, "getpwnam", return_value=account):
            identity = M.validate_test_identity("sentinelx", url)
        self.assertEqual(identity["socket_dir"], "/tmp/boris-email-qa-run/socket")

    def test_split_test_identity_rejects_production_or_credentialed_database(self):
        account = SimpleNamespace(pw_uid=1234)
        urls = [
            "postgresql+psycopg2://sentinelx@db.internal/postgres?host=/tmp/boris-email-qa-run&port=55439",
            "postgresql+psycopg2://sentinelx:secret@/postgres?host=/tmp/boris-email-qa-run&port=55439",
            "postgresql+psycopg2://sentinelx@/postgres?host=/var/run/postgresql&port=5432",
        ]
        with mock.patch.object(M.pwd, "getpwnam", return_value=account):
            for url in urls:
                with self.subTest(url=url), self.assertRaisesRegex(M.CertificationError, "NOT_ISOLATED"):
                    M.validate_test_identity("sentinelx", url)

    def test_exact_baseline_no_change_is_valid_scope(self):
        normalized = M.normalize_scope(scope())
        self.assertEqual(normalized["approved_runtime_files"], {})
        self.assertEqual(M.diff_maps({"a": "1"}, {"a": "1"}), {})

    def test_baseline_plus_email_only_delta_matches_exact_hashes(self):
        before = {"app/services/email_queue.py": "old", "app/main.py": "same"}
        after = {"app/services/email_queue.py": "new", "app/main.py": "same"}
        self.assertEqual(
            M.diff_maps(before, after),
            {"app/services/email_queue.py": {"baseline_sha256": "old", "candidate_sha256": "new"}},
        )

    def test_unrelated_future_test_is_not_implicitly_selected(self):
        normalized = M.normalize_scope(scope(
            approved_runtime_files={"app/services/email_queue.py": {
                "baseline_sha256": "old", "candidate_sha256": "new"}},
            affected_tests=["tests/test_email_queue.py"],
        ))
        self.assertEqual(normalized["affected_tests"], ["tests/test_email_queue.py"])
        self.assertNotIn("tests/test_future_avito.py", normalized["affected_tests"])

    def test_shared_dependency_requires_integration_test(self):
        with self.assertRaisesRegex(M.CertificationError, "SHARED_DEPENDENCY_REQUIRES"):
            M.normalize_scope(scope(
                approved_runtime_files={"app/shared.py": {
                    "baseline_sha256": "old", "candidate_sha256": "new"}},
                affected_tests=["tests/test_local.py"],
                shared_dependency_paths=["app/shared.py"],
            ))

    def test_shared_dependency_with_integration_test_passes_metadata_gate(self):
        normalized = M.normalize_scope(scope(
            approved_runtime_files={"app/shared.py": {
                "baseline_sha256": "old", "candidate_sha256": "new"}},
            affected_tests=["tests/test_local.py"],
            integration_tests=["tests/test_cross_workstream.py"],
            shared_dependency_paths=["app/shared.py"],
        ))
        self.assertEqual(normalized["integration_tests"], ["tests/test_cross_workstream.py"])

    def test_foreign_runtime_in_candidate_fails_exact_diff(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            manifest = base / "scope.json"
            manifest.write_text(json.dumps(scope(
                approved_runtime_files={"app/services/email_queue.py": {
                    "baseline_sha256": "old", "candidate_sha256": "new"}},
                affected_tests=["tests/test_email.py"],
            )))
            (base / "tests").mkdir()
            (base / "tests/test_email.py").write_text("def test_ok(): pass\n")
            artifact = {"files": [
                {"path": "app/services/email_queue.py", "sha256": "old"},
                {"path": "app/services/avito.py", "sha256": "old-avito"},
            ]}
            rows = [
                {"path": "app/services/email_queue.py", "sha256": "new", "size": 1},
                {"path": "app/services/avito.py", "sha256": "new-avito", "size": 1},
            ]
            with mock.patch.object(M, "verify_baseline", return_value=artifact), \
                 mock.patch.object(M, "runtime_rows", return_value=rows), \
                 mock.patch.object(M, "source_identity", return_value="identity"):
                with self.assertRaisesRegex(M.CertificationError, "SCOPED_RUNTIME_DIFF_MISMATCH"):
                    M.validate_candidate(base, manifest, Path("/current"))

    def test_runtime_diff_wider_than_approved_fails(self):
        self.test_foreign_runtime_in_candidate_fails_exact_diff()

    def test_missing_baseline_metadata_fails_closed(self):
        bad = scope()
        del bad["baseline_build_id"]
        with self.assertRaisesRegex(M.CertificationError, "SCOPED_METADATA_MISSING"):
            M.normalize_scope(bad)

    def test_retained_receipt_is_atomic_and_verifiable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            (root / "scripts/build_backend_release.py").write_text("# builder\n")
            (root / "scripts/scoped_release_certification.py").write_text("# certifier\n")
            scope_path = root / "scope.json"
            scope_path.write_text(json.dumps(scope(source_commit="abc")))
            validated = {
                "scope": scope(source_commit="abc"),
                "runtime_rows": [], "runtime_diff": {}, "candidate_build_id": "candidate",
                "test_rows": [], "scope_manifest_sha256": "scopehash", "source_identity": "identity",
            }
            receipt = root / "receipt.json"
            with mock.patch.object(M, "validate_candidate", side_effect=[validated, validated]), \
                 mock.patch.object(M, "run_command", return_value={"ok": True, "returncode": 0, "tail": "PASS"}):
                result = M.certify(root, scope_path, Path("/current"), str(receipt))
            self.assertTrue(result["ok"])
            self.assertEqual(json.loads(receipt.read_text())["status"], "PASS")

    def test_candidate_source_race_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            (root / "scripts/build_backend_release.py").write_text("# builder\n")
            (root / "scripts/scoped_release_certification.py").write_text("# certifier\n")
            scope_path = root / "scope.json"
            scope_path.write_text(json.dumps(scope()))
            first = {"scope": scope(), "runtime_rows": [], "runtime_diff": {},
                     "candidate_build_id": "a", "test_rows": [],
                     "scope_manifest_sha256": "x", "source_identity": "before"}
            second = dict(first, source_identity="after")
            with mock.patch.object(M, "validate_candidate", side_effect=[first, second]), \
                 mock.patch.object(M, "run_command", return_value={"ok": True, "returncode": 0, "tail": "PASS"}):
                with self.assertRaisesRegex(M.CertificationError, "SOURCE_CHANGED"):
                    M.certify(root, scope_path, Path("/current"), str(root / "receipt.json"))

    def test_builder_activates_only_after_certification_and_release_verify(self):
        text = (ROOT / "scripts/build_backend_release.py").read_text()
        self.assertLess(text.index("certification = _assert_architecture_release_gate"), text.index("release = _copy_release"))
        self.assertLess(text.index("_verify_release(release, rows)"), text.index("if args.activate:"))
        self.assertIn("SCOPED_CERTIFICATION_CHANGED_DURING_BUILD", text)


if __name__ == "__main__":
    unittest.main()
