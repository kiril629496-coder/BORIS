#!/usr/bin/env python3

###############################################################################
# BORIS — Owner approval inbox
#
# Sends INTERNAL approval cards only.
#
# DOES NOT CONTACT PROSPECTS.
###############################################################################

import html
import os
import re
import subprocess
from pathlib import Path

from sqlalchemy import (
    create_engine,
    text,
)

CHAT_ID = "-1003952038222"
THREAD_ID = 126

LIMIT = 5

###############################################################################
# SERVER GATE
###############################################################################

host = subprocess.check_output(
    ["hostname"],
    text=True,
).strip()

if not host.startswith(
    "8509713-oh794426"
):
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
# ENV
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

for item in Path(
    f"/proc/{pid}/environ"
).read_bytes().split(b"\0"):

    if b"=" not in item:
        continue

    k,v = item.split(
        b"=",
        1,
    )

    try:
        os.environ.setdefault(
            k.decode(),
            v.decode(),
        )
    except Exception:
        pass

url = (
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

engine = create_engine(
    url,
    pool_pre_ping=True,
)

###############################################################################
# HELPERS
###############################################################################

def esc(v):

    return html.escape(
        str(v or ""),
        quote=False,
    )


def compact(v, limit):

    value = re.sub(
        r"\s+",
        " ",
        str(v or "").strip(),
    )

    if len(value) > limit:

        value = (
            value[:limit-1]
            + "…"
        )

    return value

###############################################################################
# SELECT NOT-YET-NOTIFIED
###############################################################################

with engine.connect() as db:

    rows = db.execute(
        text("""
            SELECT
                id,
                contact_name,
                contact_username,
                source_name,
                original_text,
                monitor_intent,
                monitor_score,
                boris_fit,
                boris_bucket,
                first_message_draft,
                source_posted_at

            FROM boris_sales_hot_leads

            WHERE
                status='new'

                AND approval_required=true

                AND approval_notified_at
                    IS NULL

            ORDER BY
                boris_fit DESC,
                discovered_at ASC

            LIMIT :limit
        """),
        {
            "limit":
                LIMIT,
        },
    ).mappings().all()

print(
    "pending_approval=",
    len(rows)
)

if not rows:

    print(
        "NO_NEW_APPROVAL_CARDS"
    )

    raise SystemExit(0)

###############################################################################
# SEND INTERNAL CARDS
###############################################################################

from app.telegram_bot import (
    send_telegram_message
)

sent = 0

for r in rows:

    lead_id = int(
        r["id"]
    )

    username = str(
        r["contact_username"]
        or ""
    ).strip().lstrip("@")

    person = (
        r["contact_name"]
        or (
            "@"+username
            if username
            else "Контакт"
        )
    )

    request = compact(
        r["original_text"],
        1150,
    )

    draft = compact(
        r["first_message_draft"],
        1150,
    )

    parts = [
        "💰 <b>HOT-лид BORIS — требуется решение</b>",
        "",
        f"ID: <b>{lead_id}</b>",
        f"🎯 BORIS FIT: <b>{r['boris_fit']}/100</b>",
        f"👤 {esc(person)}",
    ]

    if username:

        parts.append(
            "Telegram: @"
            + esc(username)
        )

    if r["source_name"]:

        parts.append(
            "Источник: "
            + esc(
                r["source_name"]
            )
        )

    parts += [
        "",
        "💬 <b>Что ищет клиент:</b>",
        esc(request),
        "",
        "✍️ <b>Предлагаемое первое сообщение:</b>",
        esc(draft),
        "",
        "⚠️ <b>Самому клиенту ничего ещё не отправлено.</b>",
        "",
        "Следующий этап:",
        f"• одобрить: lead approve {lead_id}",
        f"• пропустить: lead skip {lead_id}",
        f"• отложить: lead later {lead_id}",
    ]

    message = "\n".join(
        parts
    )

    if len(message) > 3900:

        message = (
            message[:3880]
            + "\n…"
        )

    result = send_telegram_message(
        CHAT_ID,
        message,
        THREAD_ID,
    )

    ok = (
        isinstance(
            result,
            dict,
        )
        and result.get("ok")
    )

    print()
    print(
        "lead_id=",
        lead_id,
        "send=",
        "PASS"
        if ok
        else "FAIL",
    )

    if not ok:

        print(
            "result=",
            result,
        )

        continue

    message_id = (
        result
        .get("result",{})
        .get("message_id")
    )

    with engine.begin() as db:

        db.execute(
            text("""
                UPDATE
                    boris_sales_hot_leads

                SET
                    approval_notified_at=now(),

                    approval_message_id=:mid,

                    approval_chat_id=:chat,

                    approval_thread_id=:thread,

                    updated_at=now()

                WHERE
                    id=:id

                    AND status='new'
            """),
            {
                "mid":
                    message_id,

                "chat":
                    CHAT_ID,

                "thread":
                    THREAD_ID,

                "id":
                    lead_id,
            },
        )

    sent += 1

print()
print(
    "APPROVAL_CARDS_SENT=",
    sent
)

print(
    "PROSPECT_SEND=0"
)

print(
    "APPROVAL_INBOX=PASS"
)
