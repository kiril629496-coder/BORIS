#!/usr/bin/env python3

from __future__ import annotations

###############################################################################
# Standalone execution bootstrap
###############################################################################

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

###############################################################################

import argparse
import json
from collections import Counter
from typing import Any

from sqlalchemy import inspect, text

from app.db.session import engine


CLIENT_HINTS = (
    "client_key",
    "client_id",
    "account_key",
    "account_id",
    "account_name",
    "client_name",
    "account",
    "client",
    "slug",
)

SIGNAL_WORDS = (
    "status",
    "state",
    "ready",
    "readiness",
    "block",
    "reason",
    "product",
    "direction",
    "niche",
    "stage",
    "mode",
    "updated",
    "created",
    "active",
    "enabled",
)


def qi(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def find_identity_column(columns: list[str]) -> str | None:
    lower = {x.lower(): x for x in columns}

    for hint in CLIENT_HINTS:
        if hint in lower:
            return lower[hint]

    for column in columns:
        low = column.lower()

        if "client" in low or "account" in low:
            return column

    return None


def useful_table(table: str, columns: list[str]) -> bool:
    low_table = table.lower()

    if any(
        word in low_table
        for word in (
            "client",
            "account",
            "factory",
            "launch",
            "config",
            "product",
            "subscription",
            "entitlement",
            "onboarding",
        )
    ):
        return True

    return find_identity_column(columns) is not None


def compact_row(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)

    picked = {}

    for key, value in data.items():
        low = key.lower()

        if any(word in low for word in SIGNAL_WORDS):
            picked[key] = value

    if picked:
        return picked

    return dict(list(data.items())[:12])


def classify(signals: dict[str, Any]) -> tuple[str, list[str]]:
    raw = json.dumps(
        signals,
        ensure_ascii=False,
        default=str,
    ).lower()

    blockers = []

    for word in (
        "blocked_core",
        "no_products",
        "not_ready",
        '"blocked"',
        '"failed"',
        '"error"',
    ):
        if word in raw:
            blockers.append(word)

    if blockers:
        return "BLOCKED", blockers

    if any(
        word in raw
        for word in (
            "zero_touch_ready",
            "ready_to_launch",
            '"ready"',
            '"active"',
        )
    ):
        return "READY", []

    return "IN_PROGRESS", []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BORIS client launch control plane — SELECT ONLY"
    )

    parser.add_argument("--json", action="store_true")
    parser.add_argument("--client", default=None)
    parser.add_argument("--schema", action="store_true")

    args = parser.parse_args()

    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            f"Unexpected DB dialect: {engine.dialect.name}"
        )

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if not tables:
        raise RuntimeError(
            "PostgreSQL connected but no tables returned"
        )

    metadata: dict[str, dict[str, Any]] = {}

    for table in tables:
        try:
            columns = [
                item["name"]
                for item in inspector.get_columns(table)
            ]
        except Exception as exc:
            print(
                f"SKIP_METADATA={table} "
                f"reason={type(exc).__name__}",
                file=sys.stderr,
            )
            continue

        identity = find_identity_column(columns)

        if useful_table(table, columns):
            metadata[table] = {
                "columns": columns,
                "identity": identity,
            }

    identity_tables = {
        table: meta
        for table, meta in metadata.items()
        if meta["identity"] is not None
    }

    print(
        "PYTHON_BOOTSTRAP=PASS",
        file=sys.stderr,
    )
    print(
        f"BACKEND_ROOT={BACKEND_ROOT}",
        file=sys.stderr,
    )
    print(
        "DB_ENGINE_SOURCE=app.db.session.engine",
        file=sys.stderr,
    )
    print(
        f"DB_DIALECT={engine.dialect.name}",
        file=sys.stderr,
    )
    print(
        "DB_OPERATION=SELECT_ONLY",
        file=sys.stderr,
    )
    print(
        f"DB_TABLES_TOTAL={len(tables)}",
        file=sys.stderr,
    )
    print(
        f"CLIENT_RELATED_TABLES={len(metadata)}",
        file=sys.stderr,
    )
    print(
        f"IDENTITY_TABLES={len(identity_tables)}",
        file=sys.stderr,
    )

    if args.schema:
        for table, meta in sorted(metadata.items()):
            print(
                json.dumps(
                    {
                        "table": table,
                        "identity": meta["identity"],
                        "columns": meta["columns"],
                    },
                    ensure_ascii=False,
                )
            )

        return 0

    if not identity_tables:
        raise RuntimeError(
            "No client/account identity columns discovered"
        )

    clients: dict[str, dict[str, Any]] = {}

    #
    # READ-ONLY connection.
    # No transaction commit.
    # Only SELECT statements below.
    #
    with engine.connect() as conn:

        for table, meta in identity_tables.items():
            identity = meta["identity"]

            stmt = text(
                f"""
                SELECT DISTINCT
                    CAST({qi(identity)} AS TEXT) AS identity
                FROM {qi(table)}
                WHERE {qi(identity)} IS NOT NULL
                  AND CAST({qi(identity)} AS TEXT) <> ''
                LIMIT 5000
                """
            )

            try:
                rows = conn.execute(stmt)
            except Exception as exc:
                print(
                    f"SKIP_TABLE={table} "
                    f"reason={type(exc).__name__}",
                    file=sys.stderr,
                )
                continue

            for row in rows:
                value = str(
                    row._mapping["identity"]
                ).strip()

                if not value:
                    continue

                clients.setdefault(
                    value,
                    {
                        "client": value,
                        "tables": [],
                        "signals": {},
                        "launch_status": "IN_PROGRESS",
                        "blockers": [],
                    },
                )

                if table not in clients[value]["tables"]:
                    clients[value]["tables"].append(table)

        for client, item in clients.items():

            for table in item["tables"]:
                meta = identity_tables[table]

                identity = meta["identity"]
                columns = meta["columns"]

                order_col = None

                for candidate in (
                    "updated_at",
                    "created_at",
                    "last_at",
                    "timestamp",
                    "id",
                ):
                    if candidate in columns:
                        order_col = candidate
                        break

                order_sql = ""

                if order_col:
                    order_sql = (
                        f" ORDER BY {qi(order_col)} DESC"
                    )

                stmt = text(
                    f"""
                    SELECT *
                    FROM {qi(table)}
                    WHERE CAST({qi(identity)} AS TEXT)=:client
                    {order_sql}
                    LIMIT 1
                    """
                )

                try:
                    row = conn.execute(
                        stmt,
                        {"client": client},
                    ).first()
                except Exception as exc:
                    print(
                        f"SKIP_SIGNAL={table} "
                        f"reason={type(exc).__name__}",
                        file=sys.stderr,
                    )
                    continue

                if row is not None:
                    item["signals"][table] = compact_row(row)

            status, blockers = classify(
                item["signals"]
            )

            item["launch_status"] = status
            item["blockers"] = blockers

    if args.client:
        needle = args.client.casefold()

        clients = {
            key: item
            for key, item in clients.items()
            if needle in key.casefold()
        }

        if not clients:
            print("CLIENT_NOT_FOUND")
            return 2

    result = sorted(
        clients.values(),
        key=lambda x: (
            x["launch_status"],
            x["client"],
        ),
    )

    if args.json:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0

    print()
    print("=" * 120)
    print("BORIS CLIENT LAUNCH CONTROL")
    print("=" * 120)

    counter = Counter(
        item["launch_status"]
        for item in result
    )

    for item in result:
        print(
            f"{item['launch_status']:12} "
            f"{item['client'][:62]:62} "
            f"tables={len(item['tables'])}"
        )

        if item["blockers"]:
            print(
                " " * 13
                + "blockers="
                + ",".join(item["blockers"])
            )

    print("-" * 120)

    print(
        f"TOTAL={len(result)} "
        f"READY={counter['READY']} "
        f"IN_PROGRESS={counter['IN_PROGRESS']} "
        f"BLOCKED={counter['BLOCKED']}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
