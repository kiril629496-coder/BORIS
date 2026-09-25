"""FACTORY_D_V1 — universal BORIS Factory refresh.

Pipeline:

A readiness
→ B planner
→ C persistent launch state
→ D onboarding enrichment

No client-specific account list.
No per-client cron.
"""

import argparse
import json
import subprocess
import sys

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import onboarding_bridge as OB


def run(cmd):
    print("$", " ".join(cmd))

    r = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
    )

    if r.stdout:
        print(r.stdout.rstrip())

    if r.returncode != 0:
        if r.stderr:
            print(
                r.stderr.rstrip(),
                file=sys.stderr,
            )

        raise RuntimeError(
            "command failed rc=%s: %s"
            % (
                r.returncode,
                " ".join(cmd),
            )
        )

    return r


def pipeline(apply=False):
    py = "venv/bin/python3"

    # A — evidence/readiness
    run([
        py,
        "factory_readiness.py",
    ])

    # B — deterministic planner
    run([
        py,
        "factory_b.py",
    ])

    # C — persistent Factory control state
    cmd = [
        py,
        "factory_c.py",
    ]

    if apply:
        cmd.append("--apply")

    run(cmd)

    if not apply:
        print(
            "D DRY RUN: onboarding bridge "
            "database update skipped"
        )
        return

    db = SessionLocal()

    try:
        rows = OB.refresh_all(db)
        db.commit()

        print(
            "D onboarding packets updated:",
            len(rows),
        )

        ready = sum(
            1
            for x in rows
            if x.get("ready_for_factory")
        )

        print(
            "ready_for_factory:",
            ready,
        )

        with open(
            "/root/BORIS/backend/run/factory_d_refresh.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                rows,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def main():
    p = argparse.ArgumentParser()

    p.add_argument(
        "--apply",
        action="store_true",
        help=(
            "write only Factory-owned "
            "factory_launch_state"
        ),
    )

    args = p.parse_args()

    pipeline(
        apply=args.apply
    )


if __name__ == "__main__":
    main()
