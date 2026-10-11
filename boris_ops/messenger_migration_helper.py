#!/usr/bin/env python3
"""Fixed BORIS Messenger migration bridge.

This helper intentionally accepts only three exact operations and never prints
or forwards the backend .env contents. It is invoked only through the exact
sudoers rule for borisops.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PYTHON = "/root/BORIS/backend/venv/bin/python"
RUNNER = "/root/BORIS/backend/migrations/run_all.py"
BACKEND = "/root/BORIS/backend"
ENV_FILE = Path("/root/BORIS/backend/.env")
ALLOWED = {
    "status": ("status",),
    "verify": ("verify",),
    "apply060": ("apply", "--from", "060", "--to", "060"),
}


def load_database_url() -> str:
    if not ENV_FILE.is_file():
        raise SystemExit("MIGRATION_ENV_MISSING")
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("DATABASE_URL="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise SystemExit("MIGRATION_DATABASE_URL_MISSING")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ALLOWED:
        print("USAGE: status | verify | apply060", file=sys.stderr)
        return 2
    database_url = load_database_url()
    env = {"DATABASE_URL": database_url, "PYTHONUNBUFFERED": "1"}
    completed = subprocess.run(
        [PYTHON, RUNNER, *ALLOWED[sys.argv[1]]],
        cwd=BACKEND,
        env=env,
        shell=False,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
