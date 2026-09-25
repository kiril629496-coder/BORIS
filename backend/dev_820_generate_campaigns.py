#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Одноразовый runner для dev_820.

Генерирует campaigns/campaign_items для первых 9 аккаунтов
через существующие сервисы BORIS без публикации наружу.
"""

from app.db.session import SessionLocal
from app.services import campaign_service as CS
from app.services import campaign_bridge as BR
from sqlalchemy import text as T


def main() -> int:
    db = SessionLocal()
    try:
        accounts = db.execute(T("""
            SELECT id, account_id, name
            FROM accounts
            ORDER BY id
            LIMIT 9
        """)).fetchall()

        print(f"accounts={len(accounts)}")
        for idx, row in enumerate(accounts, start=1):
            acc = row[1]
            name = row[2] or acc
            campaign_name = f"AUTO BORIS {acc}"

            existing = db.execute(T("""
                SELECT id FROM campaigns
                WHERE default_account_id=:a AND name=:n
                ORDER BY id DESC LIMIT 1
            """), {"a": acc, "n": campaign_name}).fetchone()

            if existing:
                campaign_id = int(existing[0])
            else:
                c = CS.new_campaign(
                    db,
                    name=campaign_name,
                    default_account_id=acc,
                    scenario_type="avito_business",
                    source_type="storage",
                    settings={"autogen": True, "task": "dev_820"},
                )
                campaign_id = int(c.id)

            report = BR.import_from_storage(
                db,
                campaign_id=campaign_id,
                account_id=acc,
                sources=BR.SOURCES,
                dry_run=False,
                user_id=None,
            )

            print(
                f"{idx}. {acc} campaign_id={campaign_id} "
                f"found={report['found']} imported={report['imported']} skipped={report['skipped']}"
            )

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
