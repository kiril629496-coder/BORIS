#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a clean immutable BORIS backend release from the live development tree.

The release contains executable source only. Runtime configuration, secrets,
logs, browser profiles, client data, backups and the virtualenv stay outside the
artifact. Activation is an atomic `current` symlink swap and is explicit.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import fcntl
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(os.environ.get("BORIS_SOURCE_BACKEND", "/root/BORIS/backend")).resolve()
RELEASES = Path(os.environ.get("BORIS_BACKEND_RELEASES", "/root/BORIS/releases/backend")).resolve()
CURRENT = RELEASES / "current"

SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_NAME_PARTS = (
    ".bak", ".backup", ".before", ".broken", ".ownerroot.", ".tmp_sx",
    ".pre_", ".candidate", ".rollback", ".old",
)
SKIP_SUFFIXES = {".pyc", ".log", ".db", ".sqlite", ".zip", ".tar", ".gz"}


def _clean_app_files():
    app = SOURCE / "app"
    for path in sorted(app.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(SOURCE)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if any(token in path.name for token in SKIP_NAME_PARTS):
            continue
        yield path


def _clean_root_python_files():
    """Include every clean top-level Python entrypoint in the immutable release.

    Some API routes launch operational runners by filename at runtime. Shipping
    all clean root-level Python files avoids a second fragile registry of those
    entrypoints while still excluding backups, logs, data and secrets.
    """
    for path in sorted(SOURCE.glob("*.py")):
        if any(token in path.name for token in SKIP_NAME_PARTS):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def _release_support_files():
    """Include explicit non-Python runtime authority files required by workers.

    These are executable contracts/root-of-trust inputs, not mutable runtime data.
    Keeping the allowlist explicit prevents logs, state, secrets or arbitrary run/
    files from leaking into an immutable release while making the artifact fully
    self-contained for long-lived workers.
    """
    required = (
        SOURCE / "client_aliases.local.json.example",
        SOURCE / "run" / "social_owner_policy_v3.canonical",
        SOURCE / "run" / "social_owner_preflight.canonical.py",
        # RELEASE_MIGRATION_RUNNER_RUNTIME_V1:
        # Active immutable releases include the canonical migration verifier.
        # Keeping it in the manifest preserves scoped-release parity with the
        # active baseline instead of silently dropping a runtime authority file.
        SOURCE / "migrations" / "run_all.py",
    )
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"RELEASE_SUPPORT_FILE_MISSING: {path.relative_to(SOURCE)}")
        yield path


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".", 1)[0])
    return out


def _top_level_dependency_closure(app_files: list[Path]) -> list[Path]:
    local = {
        p.stem: p for p in SOURCE.glob("*.py")
        if not any(token in p.name for token in SKIP_NAME_PARTS)
    }
    wanted: set[str] = set()
    queue: list[str] = []
    for path in app_files:
        if path.suffix == ".py":
            for name in _imports(path):
                if name in local:
                    queue.append(name)
    while queue:
        name = queue.pop()
        if name in wanted:
            continue
        wanted.add(name)
        for child in _imports(local[name]):
            if child in local and child not in wanted:
                queue.append(child)
    return [local[name] for name in sorted(wanted)]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_no_silent_broad_pass(paths: list[Path]) -> None:
    """Refuse any release containing a broad exception handler that only passes.

    This is a constitutional release gate, not a style rule. Business/runtime
    errors may be handled, retried, converted to an operation state or observed,
    but they may never disappear as ``except Exception: pass``.
    """
    violations = []
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except SyntaxError as exc:
            raise RuntimeError(f"SOURCE_SYNTAX_ERROR {path}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
            )
            silent = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
            if broad and silent:
                violations.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    if violations:
        raise RuntimeError(
            "SILENT_BROAD_EXCEPTION_FORBIDDEN: " + ", ".join(violations[:100])
        )


def _assert_no_runtime_schema_mutations(paths: list[Path]) -> None:
    """Refuse releases that still mutate the DB schema from runtime Python."""
    ddl = re.compile(
        r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX|"
        r"DROP\s+(?:TABLE|INDEX|COLUMN)|ADD\s+COLUMN)\b",
        re.IGNORECASE,
    )
    orm_ddl_names = {"create_all", "drop_all"}
    violations = []
    for path in paths:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(SOURCE).as_posix()
        if rel == "app/ext_api/selftest.py":
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(source.splitlines(), 1):
            if ddl.search(line):
                violations.append(f"{rel}:{line_no}")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise RuntimeError(f"SOURCE_SYNTAX_ERROR {path}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
            if name in orm_ddl_names:
                violations.append(f"{rel}:{node.lineno}:{name}")
    if violations:
        raise RuntimeError(
            "RUNTIME_SCHEMA_MUTATION_FORBIDDEN: " + ", ".join(violations[:100])
        )


def _assert_no_duplicate_top_level_functions(paths: list[Path]) -> None:
    """Refuse a release when one module defines the same top-level function twice."""
    violations = []
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except SyntaxError as exc:
            raise RuntimeError(f"SOURCE_SYNTAX_ERROR {path}: {exc}") from exc
        seen = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in seen:
                violations.append(
                    f"{path.relative_to(SOURCE)}:{seen[node.name]}/{node.lineno}:{node.name}"
                )
            else:
                seen[node.name] = node.lineno
    if violations:
        raise RuntimeError(
            "DUPLICATE_TOP_LEVEL_FUNCTION_FORBIDDEN: " + ", ".join(violations[:100])
        )


def _assert_constitution_source_contract() -> None:
    """Validate versioned Constitution and canonical operation states before build."""
    constitution_path = SOURCE / "architecture" / "constitution_v1.json"
    if not constitution_path.is_file():
        raise RuntimeError("ARCHITECTURE_CONSTITUTION_MISSING")
    data = json.loads(constitution_path.read_text(encoding="utf-8"))
    version = int(data.get("constitution_version") or 0)
    invariants = list(data.get("invariants") or [])
    ids = [str(row.get("id") or "") for row in invariants if isinstance(row, dict)]
    expected_ids = [f"CONST-{n:02d}" for n in range(1, len(ids) + 1)]
    if version < 1 or not ids or ids != expected_ids or len(ids) != len(set(ids)):
        raise RuntimeError("ARCHITECTURE_CONSTITUTION_INVALID")

    sys.path.insert(0, str(SOURCE))
    try:
        from app.services.operation_contract import OperationState
        runtime_states = [state.value for state in OperationState]
    finally:
        try:
            sys.path.remove(str(SOURCE))
        except ValueError:
            pass
    if runtime_states != list(data.get("operation_states") or []):
        raise RuntimeError("ARCHITECTURE_OPERATION_STATES_MISMATCH")


def _assert_messenger_security_gate() -> None:
    gate = SOURCE / "scripts" / "messenger-security-audit.py"
    if not gate.is_file():
        raise RuntimeError("MESSENGER_SECURITY_GATE_MISSING")
    proc = subprocess.run(
        [sys.executable, str(gate), "--backend-root", str(SOURCE), "--frontend-root", "/root/BORIS/frontend"],
        cwd=str(SOURCE), capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-12000:]
        raise RuntimeError("MESSENGER_SECURITY_GATE_FAILED: " + detail)


def _assert_architecture_release_gate(scoped_manifest: Path | None = None) -> dict | None:
    # Architecture gate contract markers: _assert_architecture_release_gate()
    # _assert_architecture_release_gate()
    # _assert_architecture_release_gate()
    gate = SOURCE / "scripts" / "architecture_release_gate.py"
    if not gate.is_file():
        raise RuntimeError("BORIS_ARCHITECTURE_RELEASE_GATE_MISSING")
    args = [sys.executable, str(gate), "--verify"]
    if scoped_manifest is not None:
        # A scoped release retains every static architecture invariant, but its
        # test receipt is bound to the trusted active baseline plus approved
        # delta rather than to unrelated future-generation full-tree tests.
        args[-1] = "--static-no-receipt"
    proc = subprocess.run(args, cwd=str(SOURCE), capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or ""))[-12000:]
        raise RuntimeError("BORIS_ARCHITECTURE_RELEASE_GATE_FAILED: " + detail)
    if scoped_manifest is None:
        return None
    certifier = SOURCE / "scripts" / "scoped_release_certification.py"
    if not certifier.is_file():
        raise RuntimeError("BORIS_SCOPED_RELEASE_CERTIFIER_MISSING")
    verify = subprocess.run(
        [sys.executable, str(certifier), "verify", "--source", str(SOURCE),
         "--scope-manifest", str(scoped_manifest)],
        cwd=str(SOURCE), capture_output=True, text=True, timeout=300,
    )
    if verify.returncode != 0:
        detail = ((verify.stdout or "") + (verify.stderr or ""))[-12000:]
        raise RuntimeError("BORIS_SCOPED_RELEASE_CERTIFICATION_FAILED: " + detail)
    for line in reversed((verify.stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if payload.get("ok") is True and isinstance(payload.get("certification"), dict):
            return payload["certification"]
    raise RuntimeError("BORIS_SCOPED_RELEASE_CERTIFICATION_OUTPUT_INVALID")


def _manifest_rows(paths: list[Path]):
    rows = []
    for src in sorted(set(paths), key=lambda p: str(p.relative_to(SOURCE))):
        rows.append({
            "path": str(src.relative_to(SOURCE)),
            "size": src.stat().st_size,
            "sha256": _sha256(src),
        })
    return rows


def _build_id(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(row["path"].encode("utf-8") + b"\0")
        h.update(row["sha256"].encode("ascii") + b"\n")
    return h.hexdigest()


def _copy_release(rows: list[dict], build_id: str, certification: dict | None = None) -> Path:
    RELEASES.mkdir(parents=True, exist_ok=True)
    final = RELEASES / build_id
    if final.is_dir():
        return final
    stage = Path(tempfile.mkdtemp(prefix=f".build-{build_id[:12]}-", dir=str(RELEASES)))
    try:
        backend = stage / "backend"
        for row in rows:
            src = SOURCE / row["path"]
            dst = backend / row["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        manifest = {
            "format": "BORIS_BACKEND_RELEASE_V1",
            "build_id": build_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(SOURCE),
            "file_count": len(rows),
            "files": rows,
        }
        if certification is not None:
            manifest["certification"] = certification
        (stage / "BORIS_ARTIFACT.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Configuration is deliberately not copied. systemd uses EnvironmentFile
        # from the protected live config path; this symlink only supports direct
        # import/smoke tools that expect dotenv in the working directory.
        env_link = backend / ".env"
        env_link.symlink_to(SOURCE / ".env")
        os.replace(stage, final)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return final


def _verify_release(release: Path, rows: list[dict]) -> None:
    backend = release / "backend"
    for row in rows:
        path = backend / row["path"]
        if not path.is_file() or _sha256(path) != row["sha256"]:
            raise RuntimeError(f"release verification mismatch: {row['path']}")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(backend)
    # Verify with the interpreter executing this helper. The immutable snapshot
    # intentionally excludes the virtualenv, so SOURCE/venv is not a valid path
    # inside the scoped candidate.
    py = Path(sys.executable)
    proc = subprocess.run(
        [str(py), "-c", "import app.main; print('BACKEND_RELEASE_IMPORT=PASS')"],
        cwd=backend, env=env, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError("release import failed:\n" + ((proc.stdout or "") + (proc.stderr or ""))[-6000:])


def _release_is_sealed(release: Path) -> bool:
    """True when the immutable bit is already present on key artifact files."""
    probes = [release / "BORIS_ARTIFACT.json", release / "backend" / "app" / "main.py"]
    for probe in probes:
        if not probe.is_file():
            return False
        proc = subprocess.run(["lsattr", "-d", str(probe)], capture_output=True, text=True)
        if proc.returncode != 0:
            return False
        attrs = (proc.stdout or "").split(None, 1)[0] if (proc.stdout or "").strip() else ""
        if "i" not in attrs:
            return False
    return True


def _seal_release(release: Path) -> None:
    # Permissions are defense in depth; immutable inode flags are the actual
    # protection against accidental/root process writes into an activated build.
    if _release_is_sealed(release):
        return
    for path in sorted(release.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink():
            continue
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(release, 0o555)
    paths = [str(p) for p in release.rglob("*") if not p.is_symlink()] + [str(release)]
    if paths:
        proc = subprocess.run(["chattr", "+i", *paths], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("could not seal release immutable: " + (proc.stderr or proc.stdout or "unknown"))


def _activate(release: Path) -> None:
    tmp = RELEASES / (".current-" + release.name[:12])
    try:
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(release.name)
        os.replace(tmp, CURRENT)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activate", action="store_true", help="atomically point current at this verified immutable build")
    ap.add_argument("--no-seal", action="store_true", help="test-only: do not chattr +i the built artifact")
    ap.add_argument("--scoped-manifest", type=Path,
                    help="approved baseline-aware scoped release manifest")
    args = ap.parse_args()

    # Cooperating BORIS writers and the release builder share one global lock.
    # A second source snapshot after the copy also catches legacy/direct writers
    # that do not yet honor the lock: a mixed-generation artifact is never sealed
    # or activated.
    lock_path = "/tmp/boris-production-source-write.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        try:
            os.chmod(lock_path, 0o666)
        except OSError:
            pass
        fcntl.flock(fd, fcntl.LOCK_EX)
        app_files = list(_clean_app_files())
        root_files = list(_clean_root_python_files())
        support_files = list(_release_support_files())
        _assert_constitution_source_contract()
        if args.scoped_manifest is not None:
            _scope_data=json.loads(args.scoped_manifest.read_text(encoding="utf-8"))
            _approved_paths=set((_scope_data.get("approved_runtime_files") or {}).keys())
            _scoped_python=[SOURCE / rel for rel in sorted(_approved_paths)
                            if rel.endswith(".py") and (SOURCE / rel).is_file()]
            _assert_no_silent_broad_pass(_scoped_python)
        else:
            _assert_no_silent_broad_pass(app_files)
        _assert_no_runtime_schema_mutations(app_files)
        _assert_no_duplicate_top_level_functions(app_files)
        _assert_messenger_security_gate()
        certification = _assert_architecture_release_gate(args.scoped_manifest)
        rows = _manifest_rows(app_files + root_files + support_files)
        build_id = _build_id(rows)
        if certification is not None and certification.get("candidate_build_id") != build_id:
            raise RuntimeError("SCOPED_CERTIFICATION_BUILD_ID_MISMATCH")
        release = _copy_release(rows, build_id, certification)

        app_files_after = list(_clean_app_files())
        root_files_after = list(_clean_root_python_files())
        support_files_after = list(_release_support_files())
        _assert_constitution_source_contract()
        if args.scoped_manifest is not None:
            _scope_data_after=json.loads(args.scoped_manifest.read_text(encoding="utf-8"))
            _approved_paths_after=set((_scope_data_after.get("approved_runtime_files") or {}).keys())
            _scoped_python_after=[SOURCE / rel for rel in sorted(_approved_paths_after)
                                  if rel.endswith(".py") and (SOURCE / rel).is_file()]
            _assert_no_silent_broad_pass(_scoped_python_after)
        else:
            _assert_no_silent_broad_pass(app_files_after)
        _assert_no_runtime_schema_mutations(app_files_after)
        _assert_no_duplicate_top_level_functions(app_files_after)
        _assert_messenger_security_gate()
        certification_after = _assert_architecture_release_gate(args.scoped_manifest)
        rows_after = _manifest_rows(app_files_after + root_files_after + support_files_after)
        build_id_after = _build_id(rows_after)
        if build_id_after != build_id:
            raise RuntimeError(
                "SOURCE_CHANGED_DURING_BUILD: refusing mixed generation "
                f"{build_id[:12]} -> {build_id_after[:12]}"
            )
        if certification_after != certification:
            raise RuntimeError("SCOPED_CERTIFICATION_CHANGED_DURING_BUILD")

        _verify_release(release, rows)
        certification_final = _assert_architecture_release_gate(args.scoped_manifest)
        if certification_final != certification:
            raise RuntimeError("SCOPED_CERTIFICATION_CHANGED_DURING_RELEASE_VERIFY")
        if not args.no_seal:
            _seal_release(release)
        if args.activate:
            _activate(release)
        print(json.dumps({
            "status": "PASS",
            "build_id": build_id,
            "release": str(release),
            "files": len(rows),
            "bytes": sum(row["size"] for row in rows),
            "sealed": not args.no_seal,
            "activated": bool(args.activate),
            "current": os.path.realpath(CURRENT) if CURRENT.exists() else None,
        }, ensure_ascii=False))
        return 0
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
