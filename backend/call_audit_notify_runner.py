#!/usr/bin/env python3

import os
import sys
import json
import ast
import hashlib
import subprocess
from pathlib import Path

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)

EXPECTED_HOST="8509713-oh794426"

ACCOUNTS=(
    "prodazha_bytovok_25677",
    "3411770_94346",
)

OWNER_CHAT="-1003952038222"
OWNER_THREAD=0

MAX_PER_RUN=5

# -------------------------------------------------------------------
# SERVER GATE
# -------------------------------------------------------------------

host=subprocess.check_output(
    ["hostname"],
    text=True,
).strip()

if not host.startswith(
    EXPECTED_HOST
):
    raise SystemExit(
        "FAIL CLOSED: wrong server"
    )

if (
    Path.cwd()
    != Path("/root/BORIS/backend")
):
    raise SystemExit(
        "FAIL CLOSED: wrong cwd"
    )

# -------------------------------------------------------------------
# LOAD BACKEND ENV
# -------------------------------------------------------------------

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

if not pid or pid=="0":
    raise SystemExit(
        "FAIL CLOSED: backend PID missing"
    )

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
        "FAIL CLOSED: DB URL missing"
    )

# -------------------------------------------------------------------
# DISCOVER TABLE AGAIN EVERY RUN
# -------------------------------------------------------------------

engine=create_engine(
    url,
    pool_pre_ping=True,
)

def qi(v):
    return (
        '"'
        + str(v).replace('"','""')
        + '"'
    )

def parse_analysis(value):

    if value is None:
        return {}

    if isinstance(value,dict):
        return value

    s=str(value).strip()

    if not s:
        return {}

    try:
        x=json.loads(s)
        if isinstance(x,dict):
            return x
    except Exception:
        pass

    try:
        x=ast.literal_eval(s)
        if isinstance(x,dict):
            return x
    except Exception:
        pass

    return {
        "raw":s
    }

def first_value(
    row,
    names,
    default="",
):

    for name in names:

        if name in row:
            v=row.get(name)

            if v not in (
                None,
                "",
            ):
                return v

    return default

def analysis_summary(a):

    role=first_value(
        a,
        ("role","manager_role"),
        "—",
    )

    score=first_value(
        a,
        ("score","quality_score"),
        "—",
    )

    summary=first_value(
        a,
        (
            "summary",
            "conclusion",
            "feedback",
            "recommendation",
        ),
        "",
    )

    checklist=a.get(
        "checklist"
    ) or []

    good=[]
    bad=[]

    if isinstance(
        checklist,
        list,
    ):

        for item in checklist:

            if not isinstance(
                item,
                dict,
            ):
                continue

            label=str(
                item.get("item")
                or item.get("name")
                or ""
            ).strip()

            if not label:
                continue

            if item.get("done"):
                good.append(label)
            else:
                bad.append(label)

    return (
        role,
        score,
        str(summary).strip(),
        good,
        bad,
    )

with engine.begin() as db:

    ins=inspect(db)
    tables=ins.get_table_names()

    matches=[]

    for table in tables:

        cols=[
            c["name"]
            for c in ins.get_columns(table)
        ]

        if "account_id" not in cols:
            continue

        analysis_col=next(
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

        id_col=next(
            (
                c for c in (
                    "id",
                    "call_id",
                    "external_id",
                )
                if c in cols
            ),
            None,
        )

        date_col=next(
            (
                c for c in (
                    "created_at",
                    "call_at",
                    "started_at",
                    "date",
                )
                if c in cols
            ),
            None,
        )

        if not (
            analysis_col
            and id_col
            and date_col
        ):
            continue

        try:

            n=db.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM {qi(table)}
                    WHERE account_id=:a
                      AND {qi(analysis_col)}
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
                    cols,
                    analysis_col,
                    id_col,
                    date_col,
                )
            )

    if len(matches)!=1:
        raise SystemExit(
            "FAIL CLOSED:"
            " ambiguous call table:"
            f" {[m[0] for m in matches]}"
        )

    (
        call_table,
        cols,
        analysis_col,
        id_col,
        date_col,
    )=matches[0]

    # ---------------------------------------------------------------
    # DEDUP TABLE
    # ---------------------------------------------------------------

    db.execute(
        text("""
            CREATE TABLE IF NOT EXISTS
            call_audit_owner_notifications
            (
                id BIGSERIAL PRIMARY KEY,

                account_id TEXT NOT NULL,

                source_table TEXT NOT NULL,

                source_call_id TEXT NOT NULL,

                target_chat_id TEXT NOT NULL,

                target_thread_id INTEGER
                    NOT NULL DEFAULT 0,

                status TEXT NOT NULL,

                telegram_message_id BIGINT,

                error TEXT,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT now(),

                sent_at TIMESTAMPTZ,

                UNIQUE
                (
                    account_id,
                    source_table,
                    source_call_id,
                    target_chat_id,
                    target_thread_id
                )
            )
        """)
    )

    # ---------------------------------------------------------------
    # IMPORTANT INITIAL WATERMARK
    #
    # First execution does NOT dump all historical calls.
    # Existing analyses become baseline.
    # ---------------------------------------------------------------

    have_any=db.execute(
        text("""
            SELECT count(*)
            FROM call_audit_owner_notifications
            WHERE target_chat_id=:c
        """),
        {
            "c":OWNER_CHAT,
        },
    ).scalar() or 0

    if not have_any:

        inserted=0

        for account in ACCOUNTS:

            rows=db.execute(
                text(
                    f"""
                    SELECT
                        CAST({qi(id_col)}
                            AS TEXT) AS sid
                    FROM {qi(call_table)}
                    WHERE account_id=:a
                      AND {qi(analysis_col)}
                          IS NOT NULL
                    """
                ),
                {"a":account},
            ).fetchall()

            for r in rows:

                db.execute(
                    text("""
                        INSERT INTO
                        call_audit_owner_notifications
                        (
                            account_id,
                            source_table,
                            source_call_id,
                            target_chat_id,
                            target_thread_id,
                            status,
                            sent_at
                        )
                        VALUES
                        (
                            :a,:t,:id,:chat,:thr,
                            'baseline',
                            now()
                        )
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "a":account,
                        "t":call_table,
                        "id":str(r[0]),
                        "chat":OWNER_CHAT,
                        "thr":OWNER_THREAD,
                    },
                )

                inserted+=1

        print(
            "BASELINE_CREATED=",
            inserted
        )

        print(
            "No historical audit sent."
        )

        # Do not raise SystemExit inside engine.begin():
        # SQLAlchemy would roll back CREATE TABLE + baseline rows.
        # Commit transaction explicitly, then leave cleanly.
        db.commit()
        print("BASELINE_COMMIT=PASS")
        raise SystemExit(0)

    # ---------------------------------------------------------------
    # GET NEW ANALYSED CALLS
    # ---------------------------------------------------------------

    new=[]

    for account in ACCOUNTS:

        rows=db.execute(
            text(
                f"""
                SELECT c.*
                FROM {qi(call_table)} c

                WHERE
                    c.account_id=:a

                    AND
                    c.{qi(analysis_col)}
                        IS NOT NULL

                    AND NOT EXISTS
                    (
                        SELECT 1
                        FROM
                        call_audit_owner_notifications n

                        WHERE
                            n.account_id
                                = c.account_id

                            AND
                            n.source_table
                                = :table

                            AND
                            n.source_call_id
                                = CAST(
                                    c.{qi(id_col)}
                                    AS TEXT
                                  )

                            AND
                            n.target_chat_id
                                = :chat

                            AND
                            n.target_thread_id
                                = :thr
                    )

                ORDER BY
                    c.{qi(date_col)} ASC

                LIMIT :lim
                """
            ),
            {
                "a":account,
                "table":call_table,
                "chat":OWNER_CHAT,
                "thr":OWNER_THREAD,
                "lim":MAX_PER_RUN,
            },
        ).mappings().all()

        new.extend(rows)

    if not new:
        print("NEW_ANALYZED_CALLS=0")
        raise SystemExit(0)

    new.sort(
        key=lambda r:
        str(
            r.get(date_col)
            or ""
        )
    )

    new=new[:MAX_PER_RUN]

    from app.telegram_bot import (
        send_telegram_message
    )

    for row in new:

        account=str(
            row["account_id"]
        )

        call_id=str(
            row[id_col]
        )

        a=parse_analysis(
            row[analysis_col]
        )

        (
            role,
            score,
            summary,
            good,
            bad,
        )=analysis_summary(a)

        phone=first_value(
            row,
            (
                "phone",
                "client_phone",
                "caller",
                "caller_number",
                "from_number",
            ),
            "—",
        )

        duration=first_value(
            row,
            (
                "minutes",
                "duration_minutes",
                "duration",
                "duration_seconds",
            ),
            "—",
        )

        call_time=first_value(
            row,
            (
                date_col,
                "call_at",
                "started_at",
            ),
            "—",
        )

        account_name=(
            "Продажа бытовок"
            if account
                == "prodazha_bytovok_25677"
            else
            "Аренда бытовок"
        )

        parts=[
            "☎️ <b>Аудит звонка</b>",
            "",
            f"<b>{account_name}</b>",
            f"Время: {call_time}",
            f"Телефон: {phone}",
            f"Длительность: {duration}",
            "",
            f"Роль: {role}",
            f"Оценка: <b>{score}/100</b>",
        ]

        if summary:

            parts += [
                "",
                "🧠 <b>Вывод BORIS</b>",
                summary[:1000],
            ]

        if good:

            parts += [
                "",
                "✅ <b>Что сделано хорошо</b>",
            ]

            for x in good[:4]:
                parts.append(
                    "• "+x
                )

        if bad:

            parts += [
                "",
                "⚠️ <b>Что улучшить</b>",
            ]

            for x in bad[:6]:
                parts.append(
                    "• "+x
                )

        msg="\n".join(parts)

        if len(msg)>3900:
            msg=msg[:3890]+"…"

        try:

            result=send_telegram_message(
                OWNER_CHAT,
                msg,
                thread_id=(
                    OWNER_THREAD
                    or None
                ),
            )

            ok=(
                isinstance(result,dict)
                and result.get("ok")
            )

            message_id=None

            if ok:

                try:
                    message_id=(
                        result
                        .get("result",{})
                        .get("message_id")
                    )
                except Exception:
                    pass

            db.execute(
                text("""
                    INSERT INTO
                    call_audit_owner_notifications
                    (
                        account_id,
                        source_table,
                        source_call_id,
                        target_chat_id,
                        target_thread_id,
                        status,
                        telegram_message_id,
                        error,
                        sent_at
                    )
                    VALUES
                    (
                        :a,:t,:id,:chat,:thr,
                        :status,:mid,:err,
                        CASE
                            WHEN :status='sent'
                            THEN now()
                            ELSE NULL
                        END
                    )
                    ON CONFLICT
                    (
                        account_id,
                        source_table,
                        source_call_id,
                        target_chat_id,
                        target_thread_id
                    )
                    DO NOTHING
                """),
                {
                    "a":account,
                    "t":call_table,
                    "id":call_id,
                    "chat":OWNER_CHAT,
                    "thr":OWNER_THREAD,
                    "status":
                        "sent"
                        if ok
                        else "error",
                    "mid":message_id,
                    "err":
                        ""
                        if ok
                        else str(result)[:800],
                },
            )

            print(
                "CALL",
                call_id,
                "SEND=",
                "PASS" if ok else "FAIL",
                "message_id=",
                message_id,
            )

        except Exception as e:

            db.execute(
                text("""
                    INSERT INTO
                    call_audit_owner_notifications
                    (
                        account_id,
                        source_table,
                        source_call_id,
                        target_chat_id,
                        target_thread_id,
                        status,
                        error
                    )
                    VALUES
                    (
                        :a,:t,:id,:chat,:thr,
                        'error',:err
                    )
                    ON CONFLICT DO NOTHING
                """),
                {
                    "a":account,
                    "t":call_table,
                    "id":call_id,
                    "chat":OWNER_CHAT,
                    "thr":OWNER_THREAD,
                    "err":repr(e)[:800],
                },
            )

            print(
                "CALL",
                call_id,
                "SEND_EXCEPTION=",
                repr(e),
            )

print("CALL_AUDIT_NOTIFY_RUN=PASS")
