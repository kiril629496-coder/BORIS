#!/usr/bin/env python3
"""
BORIS Factory F — unified periodic orchestrator.

Pipeline:
    Factory A-D
        ->
    Factory E

Properties:
- fail closed
- single process lock
- D must succeed before E
- E must succeed before overall success
- no direct advertising executor
- no publishing
- no money actions
- subprocess output preserved
"""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOCK = Path("/tmp/boris_factory_f.lock")

D = ROOT / "factory_d_runner.py"
E = ROOT / "factory_e_runner.py"


def run(label, argv):
    print()
    print("=" * 60)
    print(label)
    print("=" * 60)

    started = time.time()

    p = subprocess.run(
        argv,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if p.stdout:
        print(p.stdout, end="" if p.stdout.endswith("\n") else "\n")

    elapsed = time.time() - started

    print(
        "%s exit=%s elapsed=%.2fs"
        % (label, p.returncode, elapsed)
    )

    if p.returncode != 0:
        raise SystemExit(
            "%s FAILED exit=%s"
            % (label, p.returncode)
        )


def main():
    apply = "--apply" in sys.argv

    mode = "APPLY" if apply else "DRY RUN"

    print("=" * 60)
    print("BORIS FACTORY F —", mode)
    print("=" * 60)

    LOCK.touch(exist_ok=True)

    with LOCK.open("r+") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            print("SKIP: another Factory F run is active")
            return 0

        py = sys.executable

        d_args = [py, str(D)]
        e_args = [py, str(E)]

        if apply:
            d_args.append("--apply")
            e_args.append("--apply")

        # Strict ordering.
        # E never runs from stale A-D state if D failed.
        run("STAGE A-D", d_args)

        run("STAGE E", e_args)

        print()
        print("=" * 60)
        print("FACTORY F COMPLETE")
        print("mode =", mode)
        print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
