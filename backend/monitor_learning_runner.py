#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os

import psycopg2


OUTCOMES=(
    "in_work",
    "contacted",
    "replied",
    "qualified",
    "won",
    "lost",
    "not_fit",
)


def aggregate(cur, dimension: str):

    if dimension == "source":
        id_expr="m.source_id::text"
        nonnull="m.source_id IS NOT NULL"

    elif dimension == "rule":
        id_expr="m.matched_rule_id::text"
        nonnull="m.matched_rule_id IS NOT NULL"

    else:
        raise ValueError(dimension)

    cur.execute(f"""
    WITH latest AS (
      SELECT DISTINCT ON (o.message_id)
        o.message_id,
        o.outcome
      FROM monitor_finding_outcomes o
      ORDER BY
        o.message_id,
        o.created_at DESC,
        o.id DESC
    )

    SELECT
      {id_expr} AS dimension_id,

      COUNT(DISTINCT m.id) AS findings,

      COUNT(*) FILTER (
        WHERE latest.outcome='in_work'
      ) AS in_work,

      COUNT(*) FILTER (
        WHERE latest.outcome='contacted'
      ) AS contacted,

      COUNT(*) FILTER (
        WHERE latest.outcome='replied'
      ) AS replied,

      COUNT(*) FILTER (
        WHERE latest.outcome='qualified'
      ) AS qualified,

      COUNT(*) FILTER (
        WHERE latest.outcome='won'
      ) AS won,

      COUNT(*) FILTER (
        WHERE latest.outcome='lost'
      ) AS lost,

      COUNT(*) FILTER (
        WHERE latest.outcome='not_fit'
      ) AS not_fit,

      AVG(
        NULLIF(
          m.payload->'sale_score'->>'score',
          ''
        )::numeric
      ) AS avg_sale_score

    FROM monitor_messages m

    JOIN monitor_sources ms
      ON ms.id=m.source_id
     AND ms.is_active=TRUE

    LEFT JOIN latest
      ON latest.message_id=m.id

    WHERE
      m.platform='telegram'
      AND {nonnull}

      -- STAGE6_PRODUCTION_SCOPE_V1
      AND COALESCE(
        (
          m.payload
          ->'quality_gate'
          ->>'qualified'
        )::boolean,
        TRUE
      )=TRUE

    GROUP BY {id_expr}
    """)

    return cur.fetchall()


def recommendation(
    findings,
    in_work,
    contacted,
    replied,
    qualified,
    won,
    lost,
    not_fit,
):

    feedback=(
        in_work +
        contacted +
        replied +
        qualified +
        won +
        lost +
        not_fit
    )

    # Учиться рано.
    if feedback < 5:
        return 0,"insufficient_data"

    positive=(
        in_work * 1 +
        contacted * 2 +
        replied * 3 +
        qualified * 5 +
        won * 10
    )

    negative=(
        lost * 4 +
        not_fit * 6
    )

    evidence=positive-negative

    if won >= 2 and evidence >= 15:
        rec="consider_boost"

    elif not_fit >= 5 and evidence <= -15:
        rec="consider_reduce"

    else:
        rec="observe"

    return evidence,rec


def main():

    ap=argparse.ArgumentParser()
    ap.add_argument(
        "--apply",
        action="store_true",
    )
    args=ap.parse_args()

    con=psycopg2.connect(
        os.environ["DATABASE_URL"]
    )

    try:
        cur=con.cursor()

        all_rows=[]

        for dimension in ("source","rule"):

            for row in aggregate(
                cur,
                dimension,
            ):
                (
                    dimension_id,
                    findings,
                    in_work,
                    contacted,
                    replied,
                    qualified,
                    won,
                    lost,
                    not_fit,
                    avg_sale,
                )=row

                evidence,rec=recommendation(
                    findings,
                    in_work,
                    contacted,
                    replied,
                    qualified,
                    won,
                    lost,
                    not_fit,
                )

                all_rows.append((
                    dimension,
                    dimension_id,
                    findings,
                    in_work,
                    contacted,
                    replied,
                    qualified,
                    won,
                    lost,
                    not_fit,
                    float(avg_sale or 0),
                    evidence,
                    rec,
                ))

                if args.apply:

                    cur.execute("""
                    INSERT INTO monitor_learning_stats (
                        dimension_type,
                        dimension_id,
                        findings,
                        in_work,
                        contacted,
                        replied,
                        qualified,
                        won,
                        lost,
                        not_fit,
                        avg_sale_score,
                        evidence_score,
                        recommendation,
                        calculated_at
                    )
                    VALUES (
                        %s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,NOW()
                    )
                    ON CONFLICT (
                        dimension_type,
                        dimension_id
                    )
                    DO UPDATE SET
                        findings=EXCLUDED.findings,
                        in_work=EXCLUDED.in_work,
                        contacted=EXCLUDED.contacted,
                        replied=EXCLUDED.replied,
                        qualified=EXCLUDED.qualified,
                        won=EXCLUDED.won,
                        lost=EXCLUDED.lost,
                        not_fit=EXCLUDED.not_fit,
                        avg_sale_score=EXCLUDED.avg_sale_score,
                        evidence_score=EXCLUDED.evidence_score,
                        recommendation=EXCLUDED.recommendation,
                        calculated_at=NOW()
                    """,(
                        dimension,
                        dimension_id,
                        findings,
                        in_work,
                        contacted,
                        replied,
                        qualified,
                        won,
                        lost,
                        not_fit,
                        avg_sale,
                        evidence,
                        rec,
                    ))

        if args.apply:
            con.commit()
        else:
            con.rollback()

        print(
            "mode=",
            "APPLY" if args.apply else "DRY",
        )

        for row in sorted(
            all_rows,
            key=lambda x:(
                x[0],
                -x[11],
                x[1],
            ),
        ):
            print(
                "%-6s %-36s findings=%2d "
                "work=%2d contact=%2d reply=%2d "
                "qual=%2d won=%2d lost=%2d bad=%2d "
                "sale=%5.1f evidence=%3d %s"
                % row
            )

        print("LEARNING_RUNNER=PASS")

    finally:
        con.close()


if __name__=="__main__":
    main()
