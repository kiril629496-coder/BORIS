#!/usr/bin/env python3
"""Baseline-aware certification for immutable scoped BORIS releases.

This gate is deliberately separate from the full-tree release receipt.  It
starts from the exact active immutable artifact, proves an explicitly approved
runtime delta, runs the delta's affected and dependency tests, and retains a
receipt bound to both runtime and test-source hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "BORIS_SCOPED_RELEASE_SCOPE_V1"
RECEIPT_FORMAT = "BORIS_SCOPED_RELEASE_CERTIFICATION_V1"
POLICY_VERSION = 1
DEFAULT_RECEIPTS = Path("/var/lib/boris-integrity/scoped-release-certifications")


class CertificationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CertificationError(f"JSON_INVALID {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CertificationError(f"JSON_OBJECT_REQUIRED {path}")
    return data


def load_builder(source: Path):
    path = source / "scripts" / "build_backend_release.py"
    if not path.is_file():
        raise CertificationError("SCOPED_BUILDER_MISSING")
    old = os.environ.get("BORIS_SOURCE_BACKEND")
    os.environ["BORIS_SOURCE_BACKEND"] = str(source)
    try:
        spec = importlib.util.spec_from_file_location("boris_scoped_builder", path)
        if spec is None or spec.loader is None:
            raise CertificationError("SCOPED_BUILDER_IMPORT_FAILED")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            os.environ.pop("BORIS_SOURCE_BACKEND", None)
        else:
            os.environ["BORIS_SOURCE_BACKEND"] = old


def runtime_rows(source: Path) -> list[dict]:
    builder = load_builder(source)
    app = list(builder._clean_app_files())
    root = list(builder._clean_root_python_files())
    support = list(builder._release_support_files())
    return builder._manifest_rows(app + root + support)


def rows_map(rows: list[dict]) -> dict[str, str]:
    return {str(row["path"]): str(row["sha256"]) for row in rows}


def diff_maps(baseline: dict[str, str], candidate: dict[str, str]) -> dict[str, dict]:
    out = {}
    for path in sorted(set(baseline) | set(candidate)):
        before, after = baseline.get(path), candidate.get(path)
        if before != after:
            out[path] = {"baseline_sha256": before, "candidate_sha256": after}
    return out


def build_id(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["path"]):
        h.update(str(row["path"]).encode("utf-8") + b"\0")
        h.update(str(row["sha256"]).encode("ascii") + b"\n")
    return h.hexdigest()


def verify_baseline(baseline_release: Path, expected_build: str, current_link: Path) -> dict:
    baseline_release = baseline_release.resolve()
    artifact_path = baseline_release / "BORIS_ARTIFACT.json"
    artifact = read_json(artifact_path)
    if artifact.get("format") != "BORIS_BACKEND_RELEASE_V1":
        raise CertificationError("BASELINE_ARTIFACT_FORMAT_INVALID")
    if artifact.get("build_id") != expected_build or baseline_release.name != expected_build:
        raise CertificationError("BASELINE_BUILD_ID_MISMATCH")
    if current_link.resolve() != baseline_release:
        raise CertificationError("BASELINE_IS_NOT_ACTIVE_RELEASE")
    baseline_backend = baseline_release / "backend"
    for row in artifact.get("files") or []:
        path = baseline_backend / str(row.get("path") or "")
        if not path.is_file() or sha256(path) != row.get("sha256"):
            raise CertificationError(f"BASELINE_FILE_INTEGRITY_FAIL {row.get('path')}")
    probes = (artifact_path, baseline_backend / "app" / "main.py")
    for probe in probes:
        proc = subprocess.run(["lsattr", "-d", str(probe)], capture_output=True, text=True)
        attrs = (proc.stdout or "").split(None, 1)[0] if proc.returncode == 0 else ""
        if "i" not in attrs:
            raise CertificationError(f"BASELINE_NOT_IMMUTABLE {probe}")
    return artifact


def normalize_scope(scope: dict) -> dict:
    required = ("format", "policy_version", "workstream", "baseline_build_id",
                "baseline_release", "approved_runtime_files", "affected_tests",
                "integration_tests", "source_commit")
    missing = [key for key in required if key not in scope]
    if missing:
        raise CertificationError("SCOPED_METADATA_MISSING " + ",".join(missing))
    if scope["format"] != FORMAT or int(scope["policy_version"]) != POLICY_VERSION:
        raise CertificationError("SCOPED_POLICY_FORMAT_INVALID")
    approved = scope["approved_runtime_files"]
    if not isinstance(approved, dict):
        raise CertificationError("APPROVED_RUNTIME_FILES_OBJECT_REQUIRED")
    for path, row in approved.items():
        if not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts:
            raise CertificationError("APPROVED_RUNTIME_PATH_INVALID")
        if not isinstance(row, dict) or "baseline_sha256" not in row or "candidate_sha256" not in row:
            raise CertificationError(f"APPROVED_RUNTIME_HASHES_MISSING {path}")
    for key in ("affected_tests", "integration_tests"):
        if not isinstance(scope[key], list) or any(not isinstance(item, str) for item in scope[key]):
            raise CertificationError(f"{key.upper()}_LIST_REQUIRED")
    shared = list(scope.get("shared_dependency_paths") or [])
    if shared and not scope["integration_tests"]:
        raise CertificationError("SHARED_DEPENDENCY_REQUIRES_INTEGRATION_TESTS")
    if any(path not in approved for path in shared):
        raise CertificationError("SHARED_DEPENDENCY_OUTSIDE_APPROVED_SCOPE")
    if approved and not scope["affected_tests"]:
        raise CertificationError("SCOPED_DIFF_REQUIRES_AFFECTED_TESTS")
    return scope


def test_manifest(source: Path, tests: list[str]) -> list[dict]:
    rows = []
    for rel in tests:
        path = source / rel
        if not path.is_file() or path.suffix != ".py":
            raise CertificationError(f"SCOPED_TEST_MISSING {rel}")
        rows.append({"path": rel, "sha256": sha256(path), "size": path.stat().st_size})
    return rows


def source_identity(source: Path, runtime: list[dict], tests: list[dict], scope_hash: str) -> str:
    payload = {
        "runtime": runtime,
        "tests": tests,
        "scope_manifest_sha256": scope_hash,
        "builder_sha256": sha256(source / "scripts" / "build_backend_release.py"),
        "certifier_sha256": sha256(source / "scripts" / "scoped_release_certification.py"),
        "policy_version": POLICY_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_candidate(source: Path, scope_path: Path, current_link: Path) -> dict:
    scope = normalize_scope(read_json(scope_path))
    baseline_release = Path(scope["baseline_release"])
    artifact = verify_baseline(baseline_release, str(scope["baseline_build_id"]), current_link)
    runtime = runtime_rows(source)
    actual = diff_maps(rows_map(artifact.get("files") or []), rows_map(runtime))
    approved = scope["approved_runtime_files"]
    if actual != approved:
        extra = sorted(set(actual) - set(approved))
        missing = sorted(set(approved) - set(actual))
        wrong = sorted(path for path in set(actual) & set(approved) if actual[path] != approved[path])
        raise CertificationError(
            "SCOPED_RUNTIME_DIFF_MISMATCH " + json.dumps(
                {"extra": extra, "missing": missing, "hash_mismatch": wrong}, ensure_ascii=False
            )
        )
    tests = list(dict.fromkeys(scope["affected_tests"] + scope["integration_tests"]))
    test_rows = test_manifest(source, tests)
    scope_hash = sha256(scope_path)
    return {
        "scope": scope,
        "baseline_artifact": artifact,
        "runtime_rows": runtime,
        "runtime_diff": actual,
        "candidate_build_id": build_id(runtime),
        "test_rows": test_rows,
        "scope_manifest_sha256": scope_hash,
        "source_identity": source_identity(source, runtime, test_rows, scope_hash),
    }


def validate_test_identity(test_user: str | None, test_database_url: str | None) -> dict | None:
    if not test_user and not test_database_url:
        return None
    if not test_user or not test_database_url:
        raise CertificationError("SCOPED_TEST_IDENTITY_INCOMPLETE")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", test_user) or test_user == "root":
        raise CertificationError("SCOPED_TEST_USER_INVALID")
    try:
        account = pwd.getpwnam(test_user)
    except KeyError as exc:
        raise CertificationError("SCOPED_TEST_USER_MISSING") from exc
    if account.pw_uid == 0:
        raise CertificationError("SCOPED_TEST_USER_PRIVILEGED")
    parsed = urlsplit(test_database_url)
    query = parse_qs(parsed.query, strict_parsing=True)
    socket_dir = (query.get("host") or [""])[0]
    port = (query.get("port") or [""])[0]
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg2"}
        or parsed.password is not None
        or parsed.hostname is not None
        or parsed.username != test_user
        or parsed.path != "/postgres"
        or set(query) != {"host", "port"}
        or not (
            re.fullmatch(r"/tmp/boris-email-qa-[A-Za-z0-9._-]+", socket_dir)
            or re.fullmatch(r"/tmp/boris-email-qa-[A-Za-z0-9._-]+/socket", socket_dir)
        )
        or port != "55439"
    ):
        raise CertificationError("SCOPED_TEST_DATABASE_NOT_ISOLATED")
    return {
        "user": test_user,
        "uid": account.pw_uid,
        "database": "postgres",
        "socket_dir": socket_dir,
        "port": int(port),
    }


def run_command(
    argv: list[str], source: Path, timeout: int, *, test_identity: dict | None = None,
    test_database_url: str | None = None,
) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = argv
    if test_identity:
        account = pwd.getpwuid(int(test_identity["uid"]))
        clean_env = {
            "HOME": account.pw_dir,
            "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(source),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DATABASE_URL": str(test_database_url),
        }
        command = ["sudo", "--non-interactive", "-u", str(test_identity["user"]), "env", "-i"]
        command.extend(f"{key}={value}" for key, value in clean_env.items())
        command.extend(argv)
        env = None
    proc = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True, timeout=timeout)
    return {
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "tail": ((proc.stdout or "") + (proc.stderr or ""))[-12000:],
    }


def receipt_path(validated: dict, explicit: str | None) -> Path:
    return Path(explicit).resolve() if explicit else DEFAULT_RECEIPTS / f"{validated['candidate_build_id']}.json"


def certify(
    source: Path, scope_path: Path, current_link: Path, explicit_receipt: str | None,
    test_user: str | None = None, test_database_url: str | None = None,
) -> dict:
    test_identity = validate_test_identity(test_user, test_database_url)
    before = validate_candidate(source, scope_path, current_link)
    static = run_command([sys.executable, "scripts/architecture_release_gate.py", "--static-no-receipt"], source, 300)
    if not static["ok"]:
        raise CertificationError("SCOPED_STATIC_INVARIANTS_FAIL " + static["tail"])
    tests = [row["path"] for row in before["test_rows"]]
    test_result = {"ok": True, "returncode": 0, "tail": "NO_SCOPED_TESTS_BASELINE_NOOP"}
    if tests:
        test_result = run_command(
            [sys.executable, "-m", "pytest", "-q", *tests], source, 1800,
            test_identity=test_identity, test_database_url=test_database_url,
        )
        if not test_result["ok"]:
            raise CertificationError("SCOPED_TESTS_FAIL " + test_result["tail"])
    after = validate_candidate(source, scope_path, current_link)
    if after["source_identity"] != before["source_identity"]:
        raise CertificationError("SCOPED_SOURCE_CHANGED_DURING_CERTIFICATION")
    scope = before["scope"]
    receipt = {
        "format": RECEIPT_FORMAT,
        "policy_version": POLICY_VERSION,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workstream": scope["workstream"],
        "source_commit": scope["source_commit"],
        "parent_active_release": scope["baseline_build_id"],
        "baseline_release": scope["baseline_release"],
        "candidate_build_id": before["candidate_build_id"],
        "source_identity": before["source_identity"],
        "scope_manifest_sha256": before["scope_manifest_sha256"],
        "runtime_diff": before["runtime_diff"],
        "runtime_file_count": len(before["runtime_rows"]),
        "tests": before["test_rows"],
        "affected_tests": scope["affected_tests"],
        "integration_tests": scope["integration_tests"],
        "shared_dependency_paths": list(scope.get("shared_dependency_paths") or []),
        "static_invariants": static,
        "test_result": test_result,
        "test_identity": test_identity or {"user": os.geteuid(), "mode": "certifier-process"},
        "builder_sha256": sha256(source / "scripts" / "build_backend_release.py"),
        "certifier_sha256": sha256(source / "scripts" / "scoped_release_certification.py"),
    }
    target = receipt_path(before, explicit_receipt)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".scoped-certification-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o444)
        os.replace(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return {"ok": True, "receipt": str(target), "certification": receipt}


def verify(source: Path, scope_path: Path, current_link: Path, explicit_receipt: str | None) -> dict:
    validated = validate_candidate(source, scope_path, current_link)
    path = receipt_path(validated, explicit_receipt)
    receipt = read_json(path)
    expected = {
        "format": RECEIPT_FORMAT,
        "policy_version": POLICY_VERSION,
        "status": "PASS",
        "candidate_build_id": validated["candidate_build_id"],
        "source_identity": validated["source_identity"],
        "scope_manifest_sha256": validated["scope_manifest_sha256"],
        "runtime_diff": validated["runtime_diff"],
        "parent_active_release": validated["scope"]["baseline_build_id"],
    }
    wrong = [key for key, value in expected.items() if receipt.get(key) != value]
    if wrong:
        raise CertificationError("SCOPED_RECEIPT_MISMATCH " + ",".join(wrong))
    return {"ok": True, "receipt": str(path), "certification": receipt}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("operation", choices=("certify", "verify"))
    ap.add_argument("--source", default=os.environ.get("BORIS_SOURCE_BACKEND", "/root/BORIS/backend"))
    ap.add_argument("--scope-manifest", required=True)
    ap.add_argument("--current-link", default="/root/BORIS/releases/backend/current")
    ap.add_argument("--receipt")
    ap.add_argument("--test-user")
    ap.add_argument("--test-database-url")
    args = ap.parse_args()
    try:
        fn = certify if args.operation == "certify" else verify
        call_args = [Path(args.source).resolve(), Path(args.scope_manifest).resolve(),
                     Path(args.current_link), args.receipt]
        if args.operation == "certify":
            call_args.extend([args.test_user, args.test_database_url])
        result = fn(*call_args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
