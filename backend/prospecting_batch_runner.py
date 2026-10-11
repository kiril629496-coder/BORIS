#!/usr/bin/env python3

"""
BORIS Prospecting batch runner.

No AI.
No email sending.

Examples:

    venv/bin/python prospecting_batch_runner.py --owner 42 --limit 10

    venv/bin/python prospecting_batch_runner.py \
        --owner 42 \
        --limit 100 \
        --apply
"""

import argparse
import time

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import prospect_discovery


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--owner",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=8,
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=62.0,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=2.5,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit(
            "limit must be >=1"
        )

    if args.limit > 500:
        raise SystemExit(
            "limit max=500"
        )

    db = SessionLocal()

    try:

        companies = db.execute(
            text(
                """
                SELECT
                    id,
                    name,
                    city,
                    website,
                    discovery_status,
                    status
                FROM prospect_companies
                WHERE owner_id=:owner
                  AND (
                        status='new'
                        OR status='error'
                      )
                ORDER BY id
                LIMIT :limit
                """
            ),
            {
                "owner": args.owner,
                "limit": args.limit,
            },
        ).mappings().all()

        print(
            "BORIS PROSPECTING BATCH"
        )

        print(
            "owner=%s candidates=%s apply=%s"
            % (
                args.owner,
                len(companies),
                args.apply,
            )
        )

        if not args.apply:

            for item in companies:
                print(
                    "DRY",
                    item["id"],
                    item["name"],
                    item["city"] or "",
                    item["website"] or "",
                )

            print(
                "DRY RUN ONLY"
            )

            return

        done = 0
        review = 0
        failed = 0

        total_emails = 0
        total_phones = 0

        started = time.monotonic()

        for index, item in enumerate(
            companies,
            start=1,
        ):

            cid = int(
                item["id"]
            )

            print()
            print(
                "[%s/%s] #%s %s"
                % (
                    index,
                    len(companies),
                    cid,
                    item["name"],
                )
            )

            try:

                result = (
                    prospect_discovery
                    .discover_and_parse(
                        db,
                        args.owner,
                        cid,
                        auto_threshold=args.threshold,
                        max_pages=args.max_pages,
                    )
                )

                d = result[
                    "discovery"
                ]

                c = result[
                    "crawl"
                ]

                print(
                    " discovery=%s score=%s website=%s"
                    % (
                        d.get(
                            "status"
                        ),
                        d.get(
                            "score"
                        ),
                        d.get(
                            "website"
                        ),
                    )
                )

                if c:

                    emails = len(
                        c["emails"]
                    )

                    phones = len(
                        c["phones"]
                    )

                    total_emails += emails
                    total_phones += phones

                    done += 1

                    print(
                        " crawl=done pages=%s emails=%s phones=%s"
                        % (
                            c[
                                "pages_fetched"
                            ],
                            emails,
                            phones,
                        )
                    )

                else:

                    review += 1

                    print(
                        " crawl=SKIP"
                    )

            except Exception as exc:

                failed += 1

                print(
                    " ERROR=%s: %s"
                    % (
                        type(exc).__name__,
                        str(exc)[:500],
                    )
                )

                db.rollback()

            if index < len(
                companies
            ):
                time.sleep(
                    max(
                        1.0,
                        args.delay,
                    )
                )

        elapsed = (
            time.monotonic()
            - started
        )

        print()
        print(
            "=" * 70
        )

        print(
            "BATCH COMPLETE"
        )

        print(
            "processed=%s"
            % len(companies)
        )

        print(
            "parsed=%s"
            % done
        )

        print(
            "review_or_not_found=%s"
            % review
        )

        print(
            "failed=%s"
            % failed
        )

        print(
            "emails=%s"
            % total_emails
        )

        print(
            "phones=%s"
            % total_phones
        )

        print(
            "elapsed_sec=%.2f"
            % elapsed
        )

        print(
            "paid_ai_cost=0"
        )

        print(
            "emails_sent=0"
        )

        print(
            "=" * 70
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
