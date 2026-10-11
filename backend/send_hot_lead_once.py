#!/usr/bin/env python3

import argparse
import os
import socket
import sys
from pathlib import Path

from sqlalchemy import (
    create_engine,
    text,
)

###############################################################################
# CONSTANTS — INTENTIONALLY ONE LEAD
###############################################################################

ALLOWED_LEAD_ID = 2
EXPECTED_USERNAME = "b_a_r_o_T"

CONFIRM_TOKEN = "SEND_LEAD_2_ONCE"

MESSAGE = (
    "Добрый день. Увидел ваш запрос по Авито. "
    "Вы пишете, что уже пробовали работать с продвижением, "
    "но нужного результата не получили. "
    "Мы как раз занимаемся такими задачами: BORIS контролирует "
    "не только объявления и рекламу, но и обращения, работу менеджеров "
    "и конечный результат по продажам. "
    "Если вопрос ещё актуален, могу коротко показать, "
    "что бы мы проверили у вас в первую очередь."
)

###############################################################################
# SERVER GATE
###############################################################################

ROOT = Path("/root/BORIS/backend")

if not socket.gethostname().startswith(
    "8509713-oh794426"
):
    raise SystemExit(
        "FAIL CLOSED: wrong host"
    )

if os.geteuid() != 0:
    raise SystemExit(
        "FAIL CLOSED: root required"
    )

if Path.cwd().resolve() != ROOT:
    raise SystemExit(
        "FAIL CLOSED: wrong cwd"
    )

sys.path.insert(
    0,
    str(ROOT),
)

###############################################################################
# CLI
###############################################################################

parser = argparse.ArgumentParser()

parser.add_argument(
    "--lead",
    type=int,
    required=True,
)

parser.add_argument(
    "--confirm",
    default="",
)

parser.add_argument(
    "--preview",
    action="store_true",
)

args = parser.parse_args()

###############################################################################
# HARD SINGLE-LEAD LIMIT
###############################################################################

if args.lead != ALLOWED_LEAD_ID:

    raise SystemExit(
        "FAIL CLOSED: B21 may send ONLY lead #2"
    )

###############################################################################
# DB
###############################################################################

url = (
    os.getenv("DATABASE_URL")
    or os.getenv("SQLALCHEMY_DATABASE_URL")
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
# LOAD LEAD
###############################################################################

with engine.connect() as db:

    lead = db.execute(
        text("""
            SELECT
                id,
                contact_name,
                contact_username,
                boris_fit,
                status,
                approval_required,
                approved_at,
                contacted_at,
                outbound_message_id
            FROM boris_sales_hot_leads
            WHERE id=:id
        """),
        {
            "id":
                ALLOWED_LEAD_ID
        },
    ).mappings().first()

if not lead:

    raise SystemExit(
        "FAIL CLOSED: lead #2 missing"
    )

username = str(
    lead["contact_username"]
    or ""
).strip().lstrip("@")

if username != EXPECTED_USERNAME:

    raise SystemExit(
        "FAIL CLOSED:"
        f" unexpected recipient @{username}"
    )

if int(
    lead["boris_fit"]
    or 0
) < 75:

    raise SystemExit(
        "FAIL CLOSED: BORIS FIT below HOT"
    )

if lead["contacted_at"] is not None:

    raise SystemExit(
        "FAIL CLOSED: lead already contacted"
    )

if lead["outbound_message_id"] is not None:

    raise SystemExit(
        "FAIL CLOSED: outbound already recorded"
    )

###############################################################################
# PREVIEW
###############################################################################

print("=" * 70)
print("LEAD_ID=", lead["id"])
print("RECIPIENT=@" + username)
print("BORIS_FIT=", lead["boris_fit"])
print("CURRENT_STATUS=", lead["status"])
print()
print("MESSAGE:")
print(MESSAGE)
print("=" * 70)

if args.preview:

    print("MODE=PREVIEW")
    print("PROSPECT_SEND=0")
    raise SystemExit(0)

###############################################################################
# EXPLICIT CONFIRMATION
###############################################################################

if args.confirm != CONFIRM_TOKEN:

    print()
    print("SEND_BLOCKED=CONFIRM_TOKEN_REQUIRED")
    print("PROSPECT_SEND=0")
    raise SystemExit(20)

###############################################################################
# APPROVE EXACT LEAD AS PART OF EXPLICIT OWNER ACTION
###############################################################################

with engine.begin() as db:

    row = db.execute(
        text("""
            SELECT
                status,
                contacted_at
            FROM boris_sales_hot_leads
            WHERE id=:id
            FOR UPDATE
        """),
        {
            "id":
                ALLOWED_LEAD_ID
        },
    ).mappings().first()

    if not row:

        raise SystemExit(
            "FAIL CLOSED: lead vanished"
        )

    if row["contacted_at"] is not None:

        raise SystemExit(
            "FAIL CLOSED: duplicate blocked"
        )

    if row["status"] not in (
        "new",
        "approved",
    ):

        raise SystemExit(
            "FAIL CLOSED:"
            f" invalid status={row['status']}"
        )

    if row["status"] == "new":

        db.execute(
            text("""
                UPDATE boris_sales_hot_leads
                SET
                    status='approved',
                    approved_at=now(),
                    approved_by='owner_explicit_b21',
                    updated_at=now()
                WHERE id=:id
            """),
            {
                "id":
                    ALLOWED_LEAD_ID
            },
        )

###############################################################################
# RESERVE OUTBOUND BEFORE NETWORK SEND
###############################################################################

with engine.begin() as db:

    existing = db.execute(
        text("""
            SELECT
                id,
                status,
                telegram_message_id,
                reserved_at,
                sent_at
            FROM boris_sales_outbound
            WHERE lead_id=:lead
            FOR UPDATE
        """),
        {
            "lead":
                ALLOWED_LEAD_ID
        },
    ).mappings().first()

    if existing:

        print(
            "EXISTING_OUTBOUND=",
            dict(existing),
        )

        raise SystemExit(
            "FAIL CLOSED:"
            " outbound reservation already exists;"
            " duplicate send blocked"
        )

    db.execute(
        text("""
            INSERT INTO boris_sales_outbound
            (
                lead_id,
                transport,
                recipient,
                message_text,
                status
            )
            VALUES
            (
                :lead,
                'telegram_user_session',
                :recipient,
                :message,
                'reserved'
            )
        """),
        {
            "lead":
                ALLOWED_LEAD_ID,

            "recipient":
                "@" + EXPECTED_USERNAME,

            "message":
                MESSAGE,
        },
    )

print("OUTBOUND_RESERVATION=PASS")

###############################################################################
# EXACT PRODUCTION TELEGRAM TRANSPORT
###############################################################################

from app.crypto_utils import (
    decrypt_secret,
)

from app.monitoring.telegram import (
    build_client,
)

api_id_raw = os.getenv(
    "MONITOR_TG_API_ID"
)

api_hash = os.getenv(
    "MONITOR_TG_API_HASH"
)

session_enc = os.getenv(
    "MONITOR_TG_SESSION_ENC"
)

if not (
    api_id_raw
    and api_hash
    and session_enc
):

    raise SystemExit(
        "FAIL CLOSED: Telegram env incomplete"
    )

session = decrypt_secret(
    session_enc
)

if not session:

    raise SystemExit(
        "FAIL CLOSED: Telegram session decrypt empty"
    )

client = build_client(
    session,
    int(api_id_raw),
    api_hash,
)

###############################################################################
# RESOLVE + SEND EXACTLY ONE
###############################################################################

sent_message = None

try:

    me = client.get_me()

    if bool(
        getattr(
            me,
            "bot",
            False,
        )
    ):

        raise RuntimeError(
            "Telegram session unexpectedly belongs to bot"
        )

    entity = client.get_entity(
        EXPECTED_USERNAME
    )

    entity_username = str(
        getattr(
            entity,
            "username",
            ""
        )
        or ""
    )

    if entity_username.lower() != EXPECTED_USERNAME.lower():

        raise RuntimeError(
            "resolved Telegram username mismatch"
        )

    if bool(
        getattr(
            entity,
            "bot",
            False,
        )
    ):

        raise RuntimeError(
            "target unexpectedly resolved as bot"
        )

    ###########################################################################
    # THE ONLY ACTUAL OUTBOUND CALL IN B21
    ###########################################################################

    sent_message = client.send_message(
        entity,
        MESSAGE,
    )

    telegram_message_id = getattr(
        sent_message,
        "id",
        None,
    )

    if not telegram_message_id:

        raise RuntimeError(
            "Telegram returned no message id"
        )

    print(
        "TELEGRAM_SEND=PASS"
    )

    print(
        "telegram_message_id=",
        telegram_message_id,
    )

    ###########################################################################
    # JOURNAL SUCCESS
    ###########################################################################

    with engine.begin() as db:

        db.execute(
            text("""
                UPDATE boris_sales_outbound
                SET
                    status='sent',
                    sent_at=now(),
                    telegram_message_id=:mid,
                    error_text=NULL
                WHERE lead_id=:lead
                  AND status='reserved'
            """),
            {
                "mid":
                    telegram_message_id,

                "lead":
                    ALLOWED_LEAD_ID,
            },
        )

        db.execute(
            text("""
                UPDATE boris_sales_hot_leads
                SET
                    status='contacted',
                    contacted_at=now(),
                    outbound_message_id=:mid,
                    outbound_transport='telegram_user_session',
                    outbound_recipient=:recipient,
                    outbound_error=NULL,
                    updated_at=now()
                WHERE id=:lead
                  AND contacted_at IS NULL
            """),
            {
                "mid":
                    telegram_message_id,

                "recipient":
                    "@" + EXPECTED_USERNAME,

                "lead":
                    ALLOWED_LEAD_ID,
            },
        )

    print(
        "DB_CONTACTED_STATE=PASS"
    )

finally:

    try:
        client.disconnect()
    except Exception:
        pass

###############################################################################
# FINAL
###############################################################################

print()
print("=" * 70)
print("FIRST_REAL_OUTBOUND=PASS")
print("lead_id=2")
print("recipient=@b_a_r_o_T")
print("status=contacted")
print("=" * 70)
