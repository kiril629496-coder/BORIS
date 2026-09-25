#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import socket
import sys


EXPECTED_HOST_PREFIX = (
    "8509713-oh794426"
)

EXPECTED_DIR = (
    "/root/BORIS/backend"
)


def gate():
    host = socket.gethostname()

    if not host.startswith(
        EXPECTED_HOST_PREFIX
    ):
        raise SystemExit(
            f"FAIL: wrong host={host}"
        )

    if os.path.realpath(
        os.getcwd()
    ) != EXPECTED_DIR:
        raise SystemExit(
            "FAIL: wrong directory="
            + os.getcwd()
        )


def main():
    gate()

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    args = parser.parse_args()

    from app.services.ai_cost_alerts import run

    result = run(
        dry_run=args.dry_run,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
