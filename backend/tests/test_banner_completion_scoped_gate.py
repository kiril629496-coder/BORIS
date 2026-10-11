"""Pytest-safe entry point for the complete banner release gate.

An operator/test harness must initialize a disposable socket-only PostgreSQL
cluster BEFORE certification. This wrapper never starts PostgreSQL or selects
the application database. Supply BORIS_QA_POSTGRES_URL and BORIS_QA_CLUSTER_ROOT,
or the certifier's isolated DATABASE_URL under /tmp/boris-email-qa-*.

Only this wrapper belongs in affected_tests/integration_tests. The suites below
must run in separate processes: several install deliberate sys.modules doubles.
All PostgreSQL gates are required; missing configuration or skipped tests fail.
BORIS_QA_BASELINE_ROOT may pin a distinct immutable dependency tree. Alternatively
BORIS_QA_SCOPE_MANIFEST supplies baseline_release, or current/backend is checked.
"""

import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit


OFFLINE_SUITES = (
    ("unittest", "tests/test_banner_workers.py"),
    ("unittest", "tests/test_execution_fencing.py"),
    ("unittest", "tests/test_premium_prepare.py"),
    ("unittest", "tests/test_campaign_cpu_admission.py"),
    ("unittest", "tests/test_campaign_banner_media_contract.py"),
    ("unittest", "tests/test_image_service_reconciliation.py"),
    ("unittest", "tests/test_feed_contract_missing_facts.py"),
    ("unittest", "tests/test_premium_qa_contract.py"),
    ("unittest", "tests/test_validator_receipts.py"),
    ("pytest", "tests/test_campaign_creative_memory.py"),
)
POSTGRES_SUITES = (
    ("lease", "tests/test_postgres_banner_lease.py"),
    ("prepare", "tests/test_premium_prepare_postgres.py"),
    ("pytest", "tests/test_campaign_banner_postgres_integration.py"),
    ("unittest", "tests/test_premium_postgres_receipts.py"),
)
TEST_HELPERS = ("tests/banner_jobs_before_fixture.py", "tests/banner_prepare_before_fixture.py")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(root)
        return True
    except (ValueError, TypeError):
        return False


def isolated_database_config(env):
    """Validate configuration without importing app code or connecting anywhere."""
    explicit_url = env.get("BORIS_QA_POSTGRES_URL", "")
    explicit_root = env.get("BORIS_QA_CLUSTER_ROOT", "")
    explicit = bool(explicit_url or explicit_root)
    if explicit:
        require(bool(explicit_url and explicit_root), "both explicit QA database variables are required")
        raw_url = explicit_url
        root = Path(explicit_root).resolve()
    else:
        raw_url = env.get("DATABASE_URL", "")
        require(bool(raw_url), "isolated PostgreSQL is required; this gate never skips")
        root = None
    try:
        parsed = urlsplit(raw_url)
        query = parse_qs(parsed.query, strict_parsing=True)
    except (TypeError, ValueError):
        raise AssertionError("invalid isolated PostgreSQL URL") from None
    require(parsed.scheme in {"postgresql", "postgresql+psycopg2"}, "PostgreSQL URL required")
    require(parsed.hostname is None and parsed.password is None and parsed.port is None,
            "TCP hosts and passwords are forbidden in the QA connection")
    require(bool(parsed.username) and bool(re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", parsed.username)),
            "explicit QA database user required")
    require(parsed.path == "/postgres" and not parsed.fragment, "isolated postgres database required")
    require(set(query) == {"host", "port"} and all(len(v) == 1 for v in query.values()),
            "QA URL must contain only one Unix socket host and port")
    socket = Path(query["host"][0])
    require(socket.is_absolute(), "absolute QA Unix socket path required")
    port = query["port"][0]
    require(port.isdigit() and 1024 <= int(port) <= 65535, "invalid QA PostgreSQL port")
    if not explicit:
        parts = socket.resolve().parts
        require(len(parts) >= 3 and parts[1] == "tmp"
                and re.fullmatch(r"boris-email-qa-[A-Za-z0-9._-]+", parts[2]),
                "inherited DATABASE_URL is not a certifier isolated connection")
        require(port == "55439" and parsed.username != "root",
                "inherited connection does not match certifier identity")
        root = Path("/tmp") / parts[2]
    require(root.parent == Path("/tmp") and root.name.startswith("boris-") and root.is_dir(),
            "existing immediate /tmp/boris-* cluster root required")
    require(socket.is_dir() and _inside(socket, root), "QA socket escapes the cluster root")
    return raw_url, root


def verify_isolated_server(raw_url, root):
    """Read-only connection proof before executing any test suite."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    engine = create_engine(raw_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as db:
            info = dict(db.execute(text("""
                SELECT current_setting('data_directory') AS data_directory,
                       current_setting('listen_addresses') AS listen_addresses,
                       current_setting('unix_socket_directories') AS sockets,
                       inet_server_addr() AS server_addr
            """)).mappings().one())
            require(_inside(info["data_directory"], root), "server data directory escapes QA cluster")
            require(info["listen_addresses"] == "" and info["server_addr"] is None,
                    "QA server must be socket-only")
            for socket in info["sockets"].split(","):
                socket = socket.strip().strip("'")
                require(socket.startswith("/") and _inside(socket, root), "server socket escapes QA cluster")
            for name in ("background_jobs", "storage", "api_usage", "campaigns", "campaign_items", "media_assets", "media_links"):
                require(db.execute(text("SELECT to_regclass(:table)"), {"table": "public." + name}).scalar() is None,
                        "public application tables are forbidden in the QA cluster")
    finally:
        engine.dispose()


def baseline_backend(env, candidate):
    explicit = env.get("BORIS_QA_BASELINE_ROOT", "")
    scope_path = env.get("BORIS_QA_SCOPE_MANIFEST", "")
    if explicit:
        baseline = Path(explicit).resolve()
    elif scope_path:
        scope = json.loads(Path(scope_path).read_text(encoding="utf-8"))
        require(scope.get("format") == "BORIS_SCOPED_RELEASE_SCOPE_V1", "invalid QA scope manifest")
        release = Path(str(scope.get("baseline_release") or "")).resolve()
        require(release.name == scope.get("baseline_build_id"), "QA baseline build ID mismatch")
        baseline = release / "backend"
    else:
        release = Path("/root/BORIS/releases/backend/current").resolve()
        artifact = json.loads((release / "BORIS_ARTIFACT.json").read_text(encoding="utf-8"))
        require(artifact.get("format") == "BORIS_BACKEND_RELEASE_V1"
                and artifact.get("build_id") == release.name, "current baseline artifact mismatch")
        baseline = release / "backend"
    require(baseline != candidate and not _inside(candidate, baseline) and not _inside(baseline, candidate),
            "lease gate needs distinct candidate and baseline trees")
    require((baseline / "app/db/session.py").is_file(), "baseline dependency tree is missing")
    return baseline


_PYTEST_BOOTSTRAP = """
import importlib, pathlib, sys
candidate, baseline, test = sys.argv[1:4]
sys.path.insert(0, baseline)
sys.path.insert(0, candidate)
for name, suffix in (("app", "app"), ("app.services", "app/services"), ("app.api", "app/api")):
    module = importlib.import_module(name)
    local = str(pathlib.Path(candidate) / suffix)
    module.__path__ = [local, *[p for p in module.__path__ if p != local]]
import pytest
raise SystemExit(pytest.main(["-q", "-rs", "--color=no", test]))
"""


def _require_completed_tests(output, runner):
    require(not re.search(r"\bskipped(?:[= :]|$)", output, re.I), "required suite skipped tests")
    if runner == "unittest":
        match = re.search(r"Ran (\d+) tests?", output)
    else:
        match = re.search(r"\b(\d+) passed\b", output)
    require(match is not None and int(match.group(1)) > 0, "suite did not prove any executed tests")
    return int(match.group(1))


def run_scoped_gate():
    root = Path(__file__).resolve().parents[1]
    raw_url, cluster = isolated_database_config(os.environ)
    baseline = baseline_backend(os.environ, root)
    for _, rel in OFFLINE_SUITES + POSTGRES_SUITES:
        require((root / rel).is_file(), "required test missing: " + rel)
    for rel in TEST_HELPERS:
        require((root / rel).is_file(), "required test helper missing: " + rel)
    verify_isolated_server(raw_url, cluster)
    env = dict(os.environ)
    env.update(BORIS_QA_POSTGRES_URL=raw_url, BORIS_QA_CLUSTER_ROOT=str(cluster),
               BORIS_QA_BASELINE_ROOT=str(baseline), DATABASE_URL=raw_url,
               BORIS_QA_PREMIUM_SOURCE=str(root / "app/services/banner_full_ai_premium.py"),
               PYTHONPATH=os.pathsep.join((str(root), str(baseline))),
               PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    results = []
    with tempfile.TemporaryDirectory(prefix="boris-banner-scoped-gate-") as reports:
        for phase, suites in (("offline", OFFLINE_SUITES), ("postgres", POSTGRES_SUITES)):
            for runner, rel in suites:
                if runner in {"lease", "prepare"}:
                    report_file = Path(reports) / (runner + ".json")
                    argv = [sys.executable, "-B", rel, "--allow-isolated-cluster",
                            "--cluster-root", str(cluster), "--candidate-root", str(root),
                            "--runtime-root", str(baseline), "--report", str(report_file)]
                elif runner == "pytest":
                    argv = [sys.executable, "-B", "-c", _PYTEST_BOOTSTRAP, str(root), str(baseline), rel]
                else:
                    argv = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
                            "-p", Path(rel).name, "-v"]
                result = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True,
                                        timeout=300 if phase == "postgres" else 90)
                output = (result.stdout or "") + (result.stderr or "")
                require(result.returncode == 0, rel + " failed:\n" + output[-6000:])
                if runner in {"lease", "prepare"}:
                    receipt = json.loads(report_file.read_text(encoding="utf-8"))
                    require(receipt.get("status") == "PASS" and receipt.get("cleanup") == "schema_removed",
                            runner + " gate did not complete and clean up")
                    checks = receipt.get("tests") or []
                    require(len(checks) >= (6 if runner == "lease" else 3)
                            and all(r.get("status") == "PASS" for r in checks),
                            "required " + runner + " checks did not run")
                    count = len(checks)
                else:
                    count = _require_completed_tests(output, runner)
                result_row = {"suite": rel, "phase": phase, "executed_checks": count, "status": "PASS"}
                if runner in {"lease", "prepare"}:
                    result_row["evidence"] = receipt
                results.append(result_row)
    report = {"gate": "banner_completion_scoped", "status": "PASS", "isolated_processes": len(results),
              "baseline_root": str(baseline), "candidate_root": str(root), "suites": results,
              "postgres_required": True, "cluster_initialized_by_wrapper": False}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return report


def test_banner_completion_scoped_gate():
    assert run_scoped_gate()["status"] == "PASS"


if __name__ == "__main__":
    run_scoped_gate()
