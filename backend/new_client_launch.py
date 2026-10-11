#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys

from app.db.session import SessionLocal
from app.factory.client_launch_entry import classify


def main():
    parser=argparse.ArgumentParser()

    parser.add_argument(
        "account_id",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args=parser.parse_args()

    account_id=args.account_id.strip()

    if not account_id:
        raise SystemExit(
            "STOP: empty account_id"
        )

    print(
        "============================================================"
    )
    print(
        " BORIS NEW CLIENT LAUNCH"
    )
    print(
        "============================================================"
    )
    print(
        "account_id=",
        account_id,
    )

    if args.apply:
        print()
        print(
            "=== RUN FACTORY PIPELINE ==="
        )

        proc=subprocess.run(
            [
                sys.executable,
                "factory_pipeline_runner.py",
            ],
            cwd="/root/BORIS/backend",
        )

        print(
            "factory_exit=",
            proc.returncode,
        )

        if proc.returncode != 0:
            raise SystemExit(
                proc.returncode
            )

        print(
            "factory_pipeline=PASS"
        )

    db=SessionLocal()

    try:
        result=classify(
            db,
            account_id,
        )

    finally:
        db.close()

    print()
    print(
        "=== CLIENT RESULT ==="
    )

    print(
        "STATUS:",
        result["status"],
    )

    print(
        "REASON:",
        result["reason"],
    )

    print(
        "MESSAGE:",
        result["human_message"],
    )

    if result.get("questions"):
        print()
        print(
            "--- WHAT WE STILL NEED ---"
        )

        for q in result["questions"]:
            print(
                "*",
                q.get("question_text"),
            )

    if result.get("owner_items"):
        print()
        print(
            "--- OWNER DECISIONS ---"
        )

        for x in result[
            "owner_items"
        ]:
            print(
                "*",
                x.get("title"),
                "-",
                x.get("detail"),
            )

    print()
    print(
        "--- JSON ---"
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__=="__main__":
    main()
