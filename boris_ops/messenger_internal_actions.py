#!/usr/bin/env python3
"""Bounded protected-action contracts for the internal Messenger backend.

This module is source-only preparation. It is intentionally not self-activated:
its caller must be added to the existing protected Ops action registry and then
checksum-guarded through the canonical self-update path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

ROOT: Final = Path("/root/BORIS")
BACKEND: Final = ROOT / "backend"
PYTHON: Final = BACKEND / "venv" / "bin" / "python"
MIGRATION_RUNNER: Final = BACKEND / "migrations" / "run_all.py"
MESSENGER_TEST: Final = BACKEND / "tests" / "test_internal_messenger_contract.py"
TIMEOUT_SECONDS: Final = 1800

# Fixed, reviewable command shapes. No request field is interpolated into argv.
ACTION_ARGV: Final[dict[str, tuple[str, ...]]] = {
    "messenger_migration_status": (
        str(PYTHON), str(MIGRATION_RUNNER), "status",
    ),
    "messenger_migration_verify": (
        str(PYTHON), str(MIGRATION_RUNNER), "verify",
    ),
    "messenger_migration_060": (
        str(PYTHON), str(MIGRATION_RUNNER), "apply", "--from", "060", "--to", "060",
    ),
    "messenger_backend_tests": (
        str(PYTHON), "-m", "pytest", str(MESSENGER_TEST), "-q",
    ),
}


def action_argv(action: str) -> tuple[str, ...]:
    """Return one of the immutable, allowlisted command vectors."""
    try:
        return ACTION_ARGV[action]
    except KeyError as exc:
        raise ValueError("unknown_internal_messenger_action") from exc


def run(action: str) -> dict[str, object]:
    """Run a fixed action for a trusted protected caller only."""
    argv = list(action_argv(action))
    cp = subprocess.run(
        argv,
        cwd=str(BACKEND),
        shell=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    return {
        "ok": cp.returncode == 0,
        "action": action,
        "returncode": cp.returncode,
        "stdout": cp.stdout[-524288:],
        "stderr": cp.stderr[-524288:],
    }


if __name__ == "__main__":
    raise SystemExit("protected_action_module_only")
