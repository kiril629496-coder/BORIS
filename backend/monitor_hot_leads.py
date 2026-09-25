#!/usr/bin/env python3

###############################################################################
# BORIS HOT LEAD RUNNER
#
# Uses the SAME source data as monitor_lead_digest:
#   monitor_messages
#   monitor_sources
#   contacts
#
# Uses the SAME commercial philosophy:
#   BORIS FIT >= 75 only
#
# Uses SAME dedup:
#   monitor_lead_digest_sent
#
# Therefore a HOT lead alerted immediately
# will not be repeated in the daily digest.
###############################################################################

import ast
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

MIN_BORIS_FIT = 75
LIMIT = 5
LOOKBACK_HOURS = 48

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

    k,v = item.split(b"=",1)

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
# LOAD BORIS FIT FROM PRODUCTION DIGEST
#
# We deliberately extract only:
#   BORIS_FIT_POSITIVE
#   BORIS_FIT_NEGATIVE
#   calculate_boris_fit
#
# This avoids importing monitor_lead_digest.py,
# whose top-level code executes the digest.
###############################################################################

digest_path = Path(
    "/root/BORIS/backend/monitor_lead_digest.py"
)

src = digest_path.read_text(
    encoding="utf-8"
)

tree = ast.parse(src)

selected = []

for node in tree.body:

    if (
        isinstance(
            node,
            ast.Assign,
        )
        and any(
            isinstance(t, ast.Name)
            and t.id in {
                "BORIS_FIT_POSITIVE",
                "BORIS_FIT_NEGATIVE",
            }
            for t in node.targets
        )
    ):
        selected.append(node)

    elif (
        isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == "calculate_boris_fit"
    ):
        selected.append(node)

module = ast.Module(
    body=selected,
    type_ignores=[],
)

ast.fix_missing_locations(
    module
)

ns = {
    "re":re,
}

exec(
    compile(
        module,
        "<boris_fit>",
        "exec",
    ),
    ns,
)

calculate_boris_fit = ns.get(
    "calculate_boris_fit"
)

if not calculate_boris_fit:
    raise SystemExit(
        "FAIL CLOSED:"
        " calculate_boris_fit unavailable"
    )

###############################################################################
# HELPERS
###############################################################################

USERNAME_RE = re.compile(
    r"@([A-Za-z][A-Za-z0-9_]{4,31})"
)

def esc(value):

    return html.escape(
        str(value or ""),
        quote=False,
    )


def short(value, limit=900):

    s = re.sub(
        r"\s+",
        " ",
        str(value or "").strip(),
    )

    if len(s) > limit:
        s = (
            s[:limit-1]
            + "…"
        )

    return s


def is_bot(username):

    u = str(
        username or ""
    ).lower().lstrip("@")

    return (
        u.endswith("_bot")
        or u.endswith("bot")
    )


def real_username(row):

    # Real author first.
    for value in (
        row.get("contact_username"),
        row.get("author_username"),
    ):

        u = str(
            value or ""
        ).strip().lstrip("@")

        if u and not is_bot(u):
            return u

    # Aggregator: extract contact from message text.
    blocked = {
        str(
            row.get("author_username")
            or ""
        ).lower().lstrip("@"),

        str(
            row.get("source_username")
            or ""
        ).lower().lstrip("@"),
    }

    for u in USERNAME_RE.findall(
        row.get("text")
        or ""
    ):

        if is_bot(u):
            continue

        if u.lower() in blocked:
            continue

        return u

    return ""


def tg_link(row):

    direct = str(
        row.get("direct_url")
        or ""
    ).strip()

    if direct.startswith("http"):
        return direct

    source = str(
        row.get("source_username")
        or ""
    ).strip().lstrip("@")

    mid = str(
        row.get("external_message_id")
        or ""
    ).strip()

    if (
        source
        and mid.isdigit()
    ):
        return (
            "https://t.me/"
            + source
            + "/"
            + mid
        )

    return ""

###############################################################################
# QUERY
###############################################################################

with engine.connect() as db:

    rows = db.execute(
        text(
            f"""
            SELECT
                m.id,
                m.external_message_id,
                m.author_name,
                m.author_username,
                m.text,
                m.intent,
                m.priority,
                m.score_total,
                m.summary,
                m.url AS direct_url,

                s.username
                    AS source_username,

                s.title
                    AS source_title,

                c.username
                    AS contact_username,

                c.display_name
                    AS contact_name

            FROM monitor_messages m

            JOIN monitor_sources s
              ON s.id=m.source_id

            LEFT JOIN contacts c
              ON c.id=m.contact_id

            WHERE
                m.platform='telegram'

                AND COALESCE(
                    m.posted_at,
                    m.collected_at
                ) >= now()
                   - interval '{LOOKBACK_HOURS} hours'

                AND m.text IS NOT NULL

                AND length(
                    trim(m.text)
                ) >= 10

                AND COALESCE(
                    m.intent,
                    ''
                ) NOT IN
                (
                    'offer_service',
                    'discussion',
                    'job_post',
                    'other'
                )

                AND NOT EXISTS
                (
                    SELECT 1

                    FROM
                        monitor_lead_digest_sent d

                    WHERE
                        d.monitor_message_id
                            = CAST(
                                m.id AS TEXT
                              )

                        AND
                        d.target_chat_id=:chat

                        AND
                        d.target_thread_id=:thread

                        AND
                        d.status='sent'
                )

            ORDER BY
                COALESCE(
                    m.posted_at,
                    m.collected_at
                ) DESC

            LIMIT 500
            """
        ),
        {
            "chat":CHAT_ID,
            "thread":THREAD_ID,
        },
    ).mappings().all()

###############################################################################
# FIT
###############################################################################

hot = []

for raw in rows:

    row = dict(raw)

    fit = calculate_boris_fit(
        row.get("text"),
        row.get("intent"),
        row.get("score_total"),
    )

    if (
        int(
            fit.get("score")
            or 0
        )
        < MIN_BORIS_FIT
    ):
        continue

    username = real_username(
        row
    )

    person = (
        row.get("contact_name")
        or row.get("author_name")
        or (
            "@"+username
            if username
            else "Автор сообщения"
        )
    )

    # Never display known bot as person.
    if (
        is_bot(
            row.get("author_username")
        )
        and username
    ):
        person = (
            "@"
            + username
        )

    elif is_bot(
        row.get("author_username")
    ):
        person = (
            "Контакт не извлечён"
        )

    hot.append(
        {
            "id":
                str(
                    row["id"]
                ),

            "person":
                short(
                    person,
                    120,
                ),

            "username":
                username,

            "source":
                short(
                    row.get(
                        "source_title"
                    )
                    or row.get(
                        "source_username"
                    )
                    or "Telegram",
                    180,
                ),

            "text":
                short(
                    row.get("text"),
                    1000,
                ),

            "intent":
                row.get("intent")
                or "—",

            "monitor_score":
                row.get(
                    "score_total"
                ),

            "boris_fit":
                int(
                    fit["score"]
                ),

            "bucket":
                fit.get(
                    "bucket"
                )
                or "HOT",

            "reason":
                short(
                    row.get("summary")
                    or (
                        "Высокое совпадение"
                        " с продуктом BORIS"
                    ),
                    500,
                ),

            "link":
                tg_link(
                    row
                ),
        }
    )

hot.sort(
    key=lambda x:(
        x["boris_fit"],
        float(
            x["monitor_score"]
            or 0
        ),
    ),
    reverse=True,
)

hot = hot[
    :LIMIT
]

print(
    "candidate_pool=",
    len(rows)
)

print(
    "hot_leads=",
    len(hot)
)

for i,lead in enumerate(
    hot,
    1,
):

    print(
        "HOT",
        i,
        lead["boris_fit"],
        lead["person"],
        lead["username"],
    )

if not hot:

    print(
        "NO_NEW_HOT_LEADS"
    )
    raise SystemExit(0)

###############################################################################
# FORMAT
###############################################################################

parts = [
    "🚨 <b>HOT-лид для BORIS</b>",
    "",
]

for i,lead in enumerate(
    hot,
    1,
):

    parts.append(
        f"<b>{i}. {esc(lead['person'])}</b>"
    )

    if lead["username"]:

        parts.append(
            "👤 @"
            + esc(
                lead["username"]
            )
        )

    parts.append(
        "🎯 <b>BORIS FIT: "
        + str(
            lead["boris_fit"]
        )
        + "/100</b>"
    )

    if (
        lead["monitor_score"]
        is not None
    ):

        parts.append(
            "Monitor score: "
            + esc(
                lead[
                    "monitor_score"
                ]
            )
        )

    parts.append(
        "📍 "
        + esc(
            lead["source"]
        )
    )

    parts.append("")

    parts.append(
        "💬 <b>Запрос:</b>"
    )

    parts.append(
        esc(
            lead["text"]
        )
    )

    if lead["reason"]:

        parts.append("")

        parts.append(
            "🧠 <b>Почему стоит связаться:</b> "
            + esc(
                lead["reason"]
            )
        )

    if lead["link"]:

        parts.append(
            "🔗 "
            + esc(
                lead["link"]
            )
        )

    parts.append("")

message = "\n".join(
    parts
).strip()

if len(message) > 3900:

    message = (
        message[:3880]
        + "\n…"
    )

###############################################################################
# INTERNAL SEND ONLY
###############################################################################

from app.telegram_bot import (
    send_telegram_message
)

result = send_telegram_message(
    CHAT_ID,
    message,
    THREAD_ID,
)

print(
    "SEND_RESULT=",
    result
)

if not (
    isinstance(
        result,
        dict,
    )
    and result.get("ok")
):
    raise SystemExit(
        "FAIL: internal hot alert send failed"
    )

telegram_message_id = (
    result
    .get("result",{})
    .get("message_id")
)

###############################################################################
# DEDUP AFTER SUCCESS
###############################################################################

with engine.begin() as db:

    for lead in hot:

        db.execute(
            text("""
                INSERT INTO
                    monitor_lead_digest_sent
                (
                    monitor_message_id,
                    target_chat_id,
                    target_thread_id,
                    status,
                    telegram_message_id,
                    sent_at
                )
                VALUES
                (
                    :mid,
                    :chat,
                    :thread,
                    'sent',
                    :tg,
                    now()
                )

                ON CONFLICT
                (
                    monitor_message_id,
                    target_chat_id,
                    target_thread_id
                )
                DO UPDATE
                SET
                    status='sent',
                    telegram_message_id=
                        EXCLUDED.telegram_message_id,
                    sent_at=now()
            """),
            {
                "mid":
                    lead["id"],

                "chat":
                    CHAT_ID,

                "thread":
                    THREAD_ID,

                "tg":
                    telegram_message_id,
            },
        )

print(
    "HOT_ALERT_SEND=PASS"
)
