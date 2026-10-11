# -*- coding: utf-8 -*-
"""24/7 cycle: refresh candidates, then prepare/send through canonical runtime."""
import subprocess
import sys

from app.db.session import SessionLocal
from app.reactivation_runtime import process


def main():
    # Analysis is incremental: react_daily skips unchanged dialogs.
    p = subprocess.run(
        [sys.executable, "/root/BORIS/backend/react_daily.py", "--apply"],
        cwd="/root/BORIS/backend", timeout=900, capture_output=True, text=True,
    )
    if p.stdout:
        print(p.stdout.strip(), flush=True)
    if p.returncode != 0:
        print("react_daily_failed:", (p.stderr or "")[-2000:], flush=True)

    db = SessionLocal()
    try:
        stats = process(db, generate_limit=5, send_limit=25)
        print("reactivation_runtime", stats, flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
