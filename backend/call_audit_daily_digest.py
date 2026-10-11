#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)

CHAT="-1003952038222"
THREAD=0

ACCOUNTS=(
    "prodazha_bytovok_25677",
    "3411770_94346",
)

host=subprocess.check_output(
    ["hostname"],
    text=True,
).strip()

if not host.startswith(
    "8509713-oh794426"
):
    raise SystemExit(
        "FAIL CLOSED: wrong server"
    )

if Path.cwd()!=Path(
    "/root/BORIS/backend"
):
    raise SystemExit(
        "FAIL CLOSED: wrong cwd"
    )

pid=subprocess.check_output(
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

for item in Path(
    f"/proc/{pid}/environ"
).read_bytes().split(b"\0"):

    if b"=" not in item:
        continue

    k,v=item.split(b"=",1)

    try:
        os.environ.setdefault(
            k.decode(),
            v.decode(),
        )
    except Exception:
        pass

url=(
    os.getenv("DATABASE_URL")
    or os.getenv(
        "SQLALCHEMY_DATABASE_URL"
    )
    or os.getenv("DB_URL")
    or os.getenv("POSTGRES_URL")
)

if not url:
    raise SystemExit(
        "FAIL CLOSED: DB missing"
    )

engine=create_engine(
    url,
    pool_pre_ping=True,
)

def qi(x):
    return (
        '"'
        + str(x).replace('"','""')
        + '"'
    )

with engine.connect() as db:

    ins=inspect(db)

    matches=[]

    for table in ins.get_table_names():

        cols=[
            c["name"]
            for c in ins.get_columns(table)
        ]

        if "account_id" not in cols:
            continue

        analysis=next(
            (
                c for c in (
                    "analysis",
                    "ai_analysis",
                    "analysis_json",
                    "audit",
                    "audit_json",
                )
                if c in cols
            ),
            None,
        )

        dt=next(
            (
                c for c in (
                    "created_at",
                    "call_at",
                    "started_at",
                )
                if c in cols
            ),
            None,
        )

        if not analysis or not dt:
            continue

        try:

            n=db.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM {qi(table)}
                    WHERE account_id=:a
                      AND {qi(analysis)}
                          IS NOT NULL
                    """
                ),
                {"a":ACCOUNTS[0]},
            ).scalar() or 0

        except Exception:
            continue

        if n:
            matches.append(
                (
                    table,
                    analysis,
                    dt,
                )
            )

    if len(matches)!=1:
        raise SystemExit(
            "FAIL CLOSED:"
            " call table ambiguous"
        )

    table,analysis,dt=matches[0]

    lines=[
        "☎️ <b>Звонки — итог за сутки</b>",
        "",
    ]

    total=0
    analyzed=0

    for account in ACCOUNTS:

        n=db.execute(
            text(
                f"""
                SELECT
                    count(*) AS total,

                    count(*) FILTER
                    (
                        WHERE {qi(analysis)}
                              IS NOT NULL
                    ) AS analyzed

                FROM {qi(table)}

                WHERE account_id=:a

                  AND {qi(dt)}
                      >= now()
                         - interval '24 hours'
                """
            ),
            {"a":account},
        ).mappings().first()

        name=(
            "Продажа бытовок"
            if account
                == "prodazha_bytovok_25677"
            else
            "Аренда бытовок"
        )

        t=int(
            n["total"] or 0
        )

        a=int(
            n["analyzed"] or 0
        )

        total+=t
        analyzed+=a

        lines.append(
            f"<b>{name}</b>: "
            f"{t} звонков, "
            f"{a} разобрано BORIS"
        )

    lines += [
        "",
        f"Всего: {total}",
        f"Аудитов: {analyzed}",
    ]

    if total==0:
        lines.append(
            "За последние 24 часа новых звонков нет."
        )

    message="\n".join(lines)

    extra_routes={}

    # Daily call digest is a client report, not an operational notification.
    # Route it to the dedicated client_reports destination.
    for account in ACCOUNTS:
        r=db.execute(text("SELECT chat_id, thread_id FROM tg_routes WHERE account_id=:a AND thread_key='client_reports' LIMIT 1"), {"a":account}).mappings().first()
        if r and r["chat_id"]:
            extra_routes[(r["chat_id"], r["thread_id"])]=True

from app.telegram_bot import (
    send_telegram_message
)

result=send_telegram_message(
    CHAT,
    message,
    thread_id=THREAD or None,
)

print(
    "RESULT=",
    result
)

if not (
    isinstance(result,dict)
    and result.get("ok")
):
    raise SystemExit(
        "CALL_DAILY_DIGEST=FAIL"
    )

for (chat_id, thread_id) in extra_routes:
    extra_result=send_telegram_message(
        chat_id,
        message,
        thread_id=thread_id or None,
    )

    if (
        isinstance(extra_result, dict)
        and not extra_result.get("ok")
        and int(extra_result.get("error_code") or 0) == 400
        and "message thread not found" in str(
            extra_result.get("description") or ""
        ).lower()
    ):
        print(
            "CLIENT_REPORT_THREAD_INVALID -> "
            "retry without thread_id"
        )
        extra_result=send_telegram_message(
            chat_id,
            message,
        )

    print(
        "RESULT[client_reports]=",
        extra_result,
    )

    if not (
        isinstance(extra_result, dict)
        and extra_result.get("ok")
    ):
        raise SystemExit(
            "CLIENT_REPORT_DIGEST_SEND=FAIL "
            + str(extra_result)
        )

print("CALL_DAILY_DIGEST=PASS")
