#!/usr/bin/env python3

"""
BORIS — Call Ingest Runner

Default:
    discovery only, no AI cost.

--apply:
    sends NEW call_ids into existing
    /api/calltracking/analyze_call endpoint.

Safety:
    - hard server gate
    - only accounts with connected Avito and an active ROP allowance
    - already analyzed call_ids skipped
    - zero-duration calls skipped
    - max 5 per account/run
    - does not implement Whisper/OpenAI itself
"""

import argparse
import json
import os
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from sqlalchemy import create_engine, text

EXPECTED_HOST = "8509713-oh794426"

MAX_PER_ACCOUNT = 5

LOCAL_API = "http://127.0.0.1:8000"


###############################################################################
# SERVER GATE
###############################################################################

host = subprocess.check_output(
    ["hostname"],
    text=True,
).strip()

if not host.startswith(EXPECTED_HOST):
    raise SystemExit(
        "FAIL CLOSED: wrong server"
    )

if Path.cwd() != Path(
    "/root/BORIS/backend"
):
    raise SystemExit(
        "FAIL CLOSED: wrong cwd"
    )


###############################################################################
# LOAD SAME ENV AS BACKEND
###############################################################################

pid = subprocess.check_output(
    [
        "systemctl",
        "show",
        "boris-backend",
        "-p",
        "MainPID",
        "--value",
    ],
    text=True,
).strip()

if not pid or pid == "0":
    raise SystemExit(
        "FAIL CLOSED: backend PID missing"
    )

env_raw = Path(
    f"/proc/{pid}/environ"
).read_bytes()

for item in env_raw.split(b"\0"):

    if b"=" not in item:
        continue

    k, v = item.split(b"=", 1)

    try:
        os.environ.setdefault(
            k.decode(),
            v.decode(),
        )
    except Exception:
        pass


###############################################################################
# DATABASE
###############################################################################

DB_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv(
        "SQLALCHEMY_DATABASE_URL"
    )
    or os.getenv("DB_URL")
    or os.getenv("POSTGRES_URL")
)

if not DB_URL:
    raise SystemExit(
        "FAIL CLOSED: DB URL missing"
    )

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
)


###############################################################################
# USE EXISTING BORIS CALLTRACKING SOURCE
###############################################################################

from app.api.calltracking import (
    calltracking_stats,
    get_rop_minutes,
)


###############################################################################
# HELPERS
###############################################################################

def eligible_accounts():
    """All Avito-connected accounts whose ROP analysis allowance is active."""
    with engine.connect() as db:
        rows = db.execute(text("""
            SELECT account_id
            FROM accounts
            WHERE COALESCE(avito_client_id, '') <> ''
            ORDER BY account_id
        """)).fetchall()
    out = []
    for row in rows:
        account_id = str(row[0] or "").strip()
        if not account_id:
            continue
        try:
            allowance = get_rop_minutes(account_id) or {}
            if bool(allowance.get("active")) and (
                bool(allowance.get("unlimited"))
                or float(allowance.get("left") or 0) > 0
            ):
                out.append(account_id)
        except Exception as exc:
            print("ROP_ELIGIBILITY_ERROR =", account_id, type(exc).__name__)
    return out


def existing_ids(account_id):

    with engine.connect() as db:

        rows = db.execute(
            text("""
                SELECT call_id
                FROM call_analysis
                WHERE account_id=:account
            """),
            {
                "account": account_id,
            },
        ).fetchall()

    return {
        str(r[0])
        for r in rows
    }


def extract_calls(stats):

    if not isinstance(
        stats,
        dict,
    ):
        return []

    # current BORIS calltracking_stats may expose
    # calls directly or nested.
    for key in (
        "calls",
        "items",
    ):

        value = stats.get(key)

        if isinstance(
            value,
            list,
        ):
            return value

    result = stats.get(
        "result"
    )

    if isinstance(
        result,
        list,
    ):
        return result

    if isinstance(
        result,
        dict,
    ):

        for key in (
            "calls",
            "items",
        ):

            value = result.get(key)

            if isinstance(
                value,
                list,
            ):
                return value

    return []


def get_call_id(call):

    if not isinstance(
        call,
        dict,
    ):
        return None

    for key in (
        "call_id",
        "callId",
    ):

        v = call.get(key)

        if v not in (
            None,
            "",
        ):
            return str(v)

    nested = call.get(
        "call"
    )

    if isinstance(
        nested,
        dict,
    ):

        v = (
            nested.get("callId")
            or nested.get("call_id")
        )

        if v not in (
            None,
            "",
        ):
            return str(v)

    return None


def get_talk_duration(call):

    if not isinstance(
        call,
        dict,
    ):
        return 0

    keys = (
        "talk_duration",
        "talkDuration",
        "duration",
        "duration_sec",
        "duration_seconds",
    )

    for key in keys:

        value = call.get(key)

        if value not in (
            None,
            "",
        ):

            try:
                return float(value)
            except Exception:
                pass

    nested = call.get(
        "call"
    )

    if isinstance(
        nested,
        dict,
    ):

        for key in keys:

            value = nested.get(key)

            if value not in (
                None,
                "",
            ):

                try:
                    return float(value)
                except Exception:
                    pass

    return 0


def analyze_call(
    account_id,
    call_id,
):

    url = (
        LOCAL_API
        + "/api/calltracking/analyze_call"
        + "?account_id="
        + account_id
    )

    payload = json.dumps(
        {
            "call_id": call_id,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type":
            "application/json"
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=420,
        ) as response:

            body = response.read().decode(
                "utf-8",
                errors="replace",
            )

            return (
                response.status,
                body,
            )

    except urllib.error.HTTPError as e:

        body = e.read().decode(
            "utf-8",
            errors="replace",
        )

        return (
            e.code,
            body,
        )


###############################################################################
# MAIN
###############################################################################

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "анализировать найденные новые"
            " звонки через существующий"
            " BORIS endpoint"
        ),
    )

    args = parser.parse_args()

    total_candidates = 0

    print(
        "MODE=",
        "APPLY"
        if args.apply
        else "DRY_RUN",
    )

    accounts = eligible_accounts()
    print("ELIGIBLE_ACCOUNTS =", len(accounts))

    for account in accounts:

        print()
        print("=" * 68)
        print(
            "ACCOUNT =",
            account,
        )

        try:

            stats = calltracking_stats(
                account,
                days=7,
            )

        except Exception as e:

            print(
                "CALLTRACKING_STATS_ERROR =",
                repr(e),
            )

            continue

        if not isinstance(
            stats,
            dict,
        ):

            print(
                "INVALID_STATS_TYPE =",
                type(stats).__name__,
            )

            continue

        print(
            "stats_keys =",
            sorted(
                stats.keys()
            ),
        )

        calls = extract_calls(
            stats
        )

        print(
            "avito_calls_7d =",
            len(calls),
        )

        known = existing_ids(
            account
        )

        print(
            "already_analyzed =",
            len(known),
        )

        candidates = []

        skipped_known = 0
        skipped_zero = 0
        skipped_no_id = 0

        seen = set()

        for call in calls:

            call_id = get_call_id(
                call
            )

            if not call_id:
                skipped_no_id += 1
                continue

            if call_id in seen:
                continue

            seen.add(call_id)

            if call_id in known:
                skipped_known += 1
                continue

            duration = get_talk_duration(
                call
            )

            if duration <= 0:
                skipped_zero += 1
                continue

            candidates.append(
                {
                    "call_id":
                        call_id,

                    "talk_duration":
                        duration,
                }
            )

        print(
            "skipped_known =",
            skipped_known,
        )

        print(
            "skipped_zero_duration =",
            skipped_zero,
        )

        print(
            "skipped_no_id =",
            skipped_no_id,
        )

        print(
            "all_new_candidates =",
            len(candidates),
        )

        limited = candidates[
            :MAX_PER_ACCOUNT
        ]

        print(
            "selected_this_run =",
            len(limited),
        )

        total_candidates += len(
            candidates
        )

        for candidate in limited:

            print(
                "NEW",
                "call_id=",
                candidate["call_id"],
                "talk_duration=",
                candidate["talk_duration"],
            )

        if not args.apply:

            print(
                "AI_CALLS=0"
            )

            continue

        for candidate in limited:

            call_id = candidate[
                "call_id"
            ]

            print()
            print(
                "ANALYZE_START",
                account,
                call_id,
            )

            try:

                http_status, body = (
                    analyze_call(
                        account,
                        call_id,
                    )
                )

                print(
                    "HTTP_STATUS =",
                    http_status,
                )

                try:

                    obj = json.loads(
                        body
                    )

                    print(
                        "BORIS_STATUS =",
                        obj.get(
                            "status"
                        ),
                    )

                    print(
                        "CACHED =",
                        obj.get(
                            "cached"
                        ),
                    )

                    if obj.get(
                        "message"
                    ):

                        print(
                            "MESSAGE =",
                            obj.get(
                                "message"
                            ),
                        )

                except Exception:

                    print(
                        "BODY =",
                        body[:700],
                    )

            except Exception as e:

                print(
                    "ANALYZE_EXCEPTION =",
                    repr(e),
                )

    print()
    print("=" * 68)

    print(
        "TOTAL_NEW_CANDIDATES =",
        total_candidates,
    )

    if args.apply:
        print(
            "MODE=APPLY"
        )
    else:
        print(
            "MODE=DRY_RUN"
        )
        print(
            "WHISPER_CALLS=0"
        )
        print(
            "OPENAI_CALLS=0"
        )
        print(
            "TELEGRAM_SEND=0"
        )


if __name__ == "__main__":
    main()
