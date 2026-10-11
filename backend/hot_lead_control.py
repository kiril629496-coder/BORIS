#!/usr/bin/env python3

import argparse
import os
import re
import socket
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

###############################################################################
# HARD SERVER GATE
###############################################################################

EXPECTED_HOST = "8509713-oh794426"
EXPECTED_DIR = Path("/root/BORIS/backend")

host = socket.gethostname()

if not host.startswith(EXPECTED_HOST):
    raise SystemExit(
        f"FAIL CLOSED: wrong host={host}"
    )

if os.geteuid() != 0:
    raise SystemExit(
        "FAIL CLOSED: root required"
    )

if Path.cwd().resolve() != EXPECTED_DIR:
    raise SystemExit(
        f"FAIL CLOSED: wrong cwd={Path.cwd()}"
    )

###############################################################################
# DATABASE
###############################################################################

DB_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("SQLALCHEMY_DATABASE_URL")
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
# HELPERS
###############################################################################

USERNAME_RE = re.compile(
    r"^[A-Za-z0-9_]{5,32}$"
)


def clean_username(value):
    value = str(value or "").strip()
    value = value.lstrip("@")

    if not USERNAME_RE.fullmatch(value):
        return None

    return value


def fetch_lead(db, lead_id, lock=False):
    suffix = " FOR UPDATE" if lock else ""

    return db.execute(
        text(
            """
            SELECT *
            FROM boris_sales_hot_leads
            WHERE id=:id
            """
            + suffix
        ),
        {"id": lead_id},
    ).mappings().first()


def show_lead(r):
    if not r:
        return

    print("=" * 70)
    print("LEAD", r["id"])
    print("status=", r["status"])
    print("BORIS_FIT=", r["boris_fit"])
    print(
        "contact=",
        r["contact_name"],
        r["contact_username"],
    )
    print(
        "approval_required=",
        r["approval_required"],
    )
    print(
        "approval_notified_at=",
        r.get("approval_notified_at"),
    )
    print(
        "approved_at=",
        r.get("approved_at"),
    )
    print(
        "contacted_at=",
        r.get("contacted_at"),
    )
    print()
    print("REQUEST:")
    print(r.get("original_text") or "")
    print()
    print("DRAFT:")
    print(r.get("first_message_draft") or "")
    print("=" * 70)


###############################################################################
# LIST
###############################################################################

def cmd_list(args):
    with engine.connect() as db:
        rows = db.execute(
            text("""
                SELECT
                    id,
                    contact_name,
                    contact_username,
                    boris_fit,
                    status,
                    approval_required,
                    approval_notified_at,
                    approved_at,
                    contacted_at
                FROM boris_sales_hot_leads
                ORDER BY
                    CASE status
                        WHEN 'approved' THEN 1
                        WHEN 'new' THEN 2
                        WHEN 'later' THEN 3
                        WHEN 'contacted' THEN 4
                        WHEN 'skipped' THEN 5
                        ELSE 6
                    END,
                    boris_fit DESC,
                    id
            """)
        ).mappings().all()

    print("queue_total=", len(rows))

    for r in rows:
        print(dict(r))


###############################################################################
# SHOW
###############################################################################

def cmd_show(args):
    with engine.connect() as db:
        r = fetch_lead(
            db,
            args.lead,
        )

    if not r:
        raise SystemExit(
            f"FAIL CLOSED: lead {args.lead} not found"
        )

    show_lead(r)


###############################################################################
# APPROVE
###############################################################################

def cmd_approve(args):
    with engine.begin() as db:
        r = fetch_lead(
            db,
            args.lead,
            lock=True,
        )

        if not r:
            raise SystemExit(
                "FAIL CLOSED: lead not found"
            )

        if not r["approval_required"]:
            raise SystemExit(
                "FAIL CLOSED: approval_required=false"
            )

        if r["status"] == "contacted":
            raise SystemExit(
                "FAIL CLOSED: already contacted"
            )

        if r["status"] == "skipped":
            raise SystemExit(
                "FAIL CLOSED: skipped lead cannot be approved"
            )

        if r["status"] == "approved":
            print("ALREADY_APPROVED")
            return

        if r["status"] not in (
            "new",
            "later",
        ):
            raise SystemExit(
                f"FAIL CLOSED: invalid status={r['status']}"
            )

        db.execute(
            text("""
                UPDATE boris_sales_hot_leads
                SET
                    status='approved',
                    approved_at=now(),
                    approved_by='owner_cli',
                    later_at=NULL,
                    updated_at=now()
                WHERE id=:id
            """),
            {"id": args.lead},
        )

    print(
        f"LEAD_APPROVED={args.lead}"
    )
    print("PROSPECT_SEND=0")


###############################################################################
# SKIP
###############################################################################

def cmd_skip(args):
    with engine.begin() as db:
        r = fetch_lead(
            db,
            args.lead,
            lock=True,
        )

        if not r:
            raise SystemExit(
                "FAIL CLOSED: lead not found"
            )

        if r["status"] == "contacted":
            raise SystemExit(
                "FAIL CLOSED: contacted lead cannot be skipped"
            )

        db.execute(
            text("""
                UPDATE boris_sales_hot_leads
                SET
                    status='skipped',
                    skipped_at=now(),
                    updated_at=now()
                WHERE id=:id
            """),
            {"id": args.lead},
        )

    print(
        f"LEAD_SKIPPED={args.lead}"
    )
    print("PROSPECT_SEND=0")


###############################################################################
# LATER
###############################################################################

def cmd_later(args):
    with engine.begin() as db:
        r = fetch_lead(
            db,
            args.lead,
            lock=True,
        )

        if not r:
            raise SystemExit(
                "FAIL CLOSED: lead not found"
            )

        if r["status"] == "contacted":
            raise SystemExit(
                "FAIL CLOSED: contacted lead cannot move to later"
            )

        db.execute(
            text("""
                UPDATE boris_sales_hot_leads
                SET
                    status='later',
                    later_at=now(),
                    updated_at=now()
                WHERE id=:id
            """),
            {"id": args.lead},
        )

    print(
        f"LEAD_LATER={args.lead}"
    )
    print("PROSPECT_SEND=0")


###############################################################################
# SEND
###############################################################################

def cmd_send(args):
    ###########################################################################
    # STEP 1 — LOCK + VALIDATE
    ###########################################################################

    with engine.begin() as db:
        r = fetch_lead(
            db,
            args.lead,
            lock=True,
        )

        if not r:
            raise SystemExit(
                "FAIL CLOSED: lead not found"
            )

        if r["status"] != "approved":
            raise SystemExit(
                "FAIL CLOSED: lead must be approved before send; "
                f"current={r['status']}"
            )

        if not r["approval_required"]:
            raise SystemExit(
                "FAIL CLOSED: approval policy missing"
            )

        if not r.get("approved_at"):
            raise SystemExit(
                "FAIL CLOSED: approved_at missing"
            )

        if r.get("contacted_at"):
            raise SystemExit(
                "FAIL CLOSED: duplicate outbound blocked"
            )

        username = clean_username(
            r.get("contact_username")
        )

        if not username:
            raise SystemExit(
                "FAIL CLOSED: valid Telegram username missing"
            )

        draft = str(
            r.get("first_message_draft")
            or ""
        ).strip()

        if not draft:
            raise SystemExit(
                "FAIL CLOSED: draft empty"
            )

        if len(draft) > 3500:
            raise SystemExit(
                "FAIL CLOSED: draft too long"
            )

        print("=" * 70)
        print("OUTBOUND GATE=PASS")
        print("lead_id=", r["id"])
        print("recipient=@" + username)
        print("BORIS_FIT=", r["boris_fit"])
        print()
        print("MESSAGE:")
        print(draft)
        print("=" * 70)

        if not args.confirm:
            print()
            print("SEND_BLOCKED=CONFIRM_REQUIRED")
            print(
                "Use: hot_lead_control.py send "
                f"--lead {r['id']} --confirm"
            )
            print("PROSPECT_SEND=0")
            return

    ###########################################################################
    # STEP 2 — TRANSPORT
    #
    # We do NOT use Bot API to DM arbitrary usernames:
    # Telegram bots cannot initiate arbitrary private conversations.
    #
    # Existing BORIS monitoring uses a Telegram user/session transport.
    # B19 discovers that transport rather than silently pretending Bot API
    # can message @username.
    ###########################################################################

    transport = None

    candidates = [
        Path("monitor_runner.py"),
        Path("monitor_hot_leads.py"),
        Path("monitor_lead_digest.py"),
    ]

    evidence = []

    for p in candidates:
        if not p.exists():
            continue

        data = p.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        for needle in (
            "Telethon",
            "TelegramClient",
            "send_message",
            "MONITOR_TG_SESSION",
            "MONITOR_TG_API_ID",
        ):
            if needle in data:
                evidence.append(
                    f"{p}:{needle}"
                )

    if not evidence:
        raise SystemExit(
            "FAIL CLOSED: no verified Telegram user-session "
            "outbound transport discovered. "
            "Lead remains approved; nothing sent."
        )

    ###########################################################################
    # CRITICAL:
    # We intentionally STOP here in B19 installation unless an existing,
    # explicit prospect-send implementation exists.
    #
    # This prevents us from inventing a Telegram transport and accidentally
    # damaging the working monitor session.
    ###########################################################################

    print()
    print("USER_SESSION_EVIDENCE:")
    for x in evidence:
        print(" ", x)

    raise SystemExit(
        "OUTBOUND TRANSPORT GATE: "
        "approved lead is safe, but existing monitor session "
        "has not yet been proven writable by this CLI. "
        "PROSPECT_SEND=0"
    )


###############################################################################
# CLI
###############################################################################

parser = argparse.ArgumentParser()

sub = parser.add_subparsers(
    dest="command",
    required=True,
)

sub.add_parser("list")

p = sub.add_parser("show")
p.add_argument(
    "--lead",
    required=True,
    type=int,
)

p = sub.add_parser("approve")
p.add_argument(
    "--lead",
    required=True,
    type=int,
)

p = sub.add_parser("skip")
p.add_argument(
    "--lead",
    required=True,
    type=int,
)

p = sub.add_parser("later")
p.add_argument(
    "--lead",
    required=True,
    type=int,
)

p = sub.add_parser("send")
p.add_argument(
    "--lead",
    required=True,
    type=int,
)
p.add_argument(
    "--confirm",
    action="store_true",
)

args = parser.parse_args()

{
    "list": cmd_list,
    "show": cmd_show,
    "approve": cmd_approve,
    "skip": cmd_skip,
    "later": cmd_later,
    "send": cmd_send,
}[args.command](args)
