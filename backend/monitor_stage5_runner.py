#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import psycopg2

from app.monitoring.sale_score import calculate


def load_messages(cur):

    cur.execute("""
    SELECT
        m.id,
        m.source_id,
        m.text,
        m.score_total,
        m.intent,
        m.author_username,
        m.payload,
        m.status,
        s.is_active,
        s.title
    FROM monitor_messages m
    JOIN monitor_sources s
      ON s.id=m.source_id
    WHERE m.platform='telegram'
    """)

    cols=[d[0] for d in cur.description]

    return [
        dict(zip(cols,row))
        for row in cur.fetchall()
    ]


def apply_sale_scores(cur, write: bool):

    messages=load_messages(cur)

    changed=0
    scored=[]

    for item in messages:

        quality=(
            item.get("payload") or {}
        ).get("quality_gate",{})

        if quality.get("qualified",True) is False:
            continue

        result=calculate(item)

        scored.append((
            item["id"],
            item["source_id"],
            result,
        ))

        if write:
            cur.execute("""
            UPDATE monitor_messages
            SET payload =
                COALESCE(payload,'{}'::jsonb)
                || %s::jsonb
            WHERE id=%s
            """,(
                json.dumps({
                    "sale_score":result
                },ensure_ascii=False),
                item["id"],
            ))

        changed += 1

    return scored, changed


def rebuild_health(cur, scored, write: bool):

    sale_by_message={
        mid:data["score"]
        for mid,source,data in scored
    }

    cur.execute("""
    SELECT
        s.id,
        s.title,
        s.is_active,

        COUNT(m.id) AS matched,

        COUNT(m.id) FILTER (
          WHERE COALESCE(
            (m.payload->'quality_gate'->>'qualified')::boolean,
            TRUE
          )=TRUE
        ) AS qualified,

        COUNT(m.id) FILTER (
          WHERE (
            m.payload->'quality_gate'->>'qualified'
          )::boolean=FALSE
        ) AS rejected,

        COUNT(DISTINCT l.id) AS leads

    FROM monitor_sources s

    LEFT JOIN monitor_messages m
      ON m.source_id=s.id

    LEFT JOIN leads l
      ON l.message_id=m.id

    WHERE s.platform='telegram'

    GROUP BY
        s.id,
        s.title,
        s.is_active

    ORDER BY s.title
    """)

    rows=cur.fetchall()

    results=[]

    for (
        source_id,
        title,
        is_active,
        matched,
        qualified,
        rejected,
        leads,
    ) in rows:

        cur.execute("""
        SELECT
            COALESCE(
              AVG(
                NULLIF(
                  m.payload->'sale_score'->>'score',
                  ''
                )::numeric
              ),
              0
            )
        FROM monitor_messages m
        WHERE
            m.source_id=%s
            AND COALESCE(
              (
                m.payload
                ->'quality_gate'
                ->>'qualified'
              )::boolean,
              TRUE
            )=TRUE
        """,(source_id,))

        avg_sale=float(cur.fetchone()[0] or 0)

        q_rate=(
            qualified / matched
            if matched else 0
        )

        r_rate=(
            rejected / matched
            if matched else 0
        )

        # Health — консервативный.
        # Ноль сообщений не означает плохой источник.
        if matched < 3:
            health=50
            health_status="insufficient_data"
            recommendation="observe"

        else:
            health=50

            health += min(30, qualified * 8)
            health += min(20, leads * 10)
            health += min(15, int(avg_sale / 6))

            health -= min(
                45,
                int(r_rate * 50),
            )

            health=max(0,min(100,health))

            if health >= 75:
                health_status="strong"
                recommendation="keep"

            elif health >= 45:
                health_status="normal"
                recommendation="observe"

            else:
                health_status="weak"

                # Автопауза не включается.
                # Только рекомендация при достаточной статистике.
                if (
                    matched >= 8
                    and qualified == 0
                    and rejected >= 8
                    and leads == 0
                ):
                    recommendation="consider_pause"
                else:
                    recommendation="observe"

        result={
            "source_id":str(source_id),
            "title":title,
            "active":bool(is_active),
            "matched":int(matched),
            "qualified":int(qualified),
            "rejected":int(rejected),
            "leads":int(leads),
            "qualified_rate":round(q_rate,4),
            "rejected_rate":round(r_rate,4),
            "avg_sale_score":round(avg_sale,2),
            "health_score":int(health),
            "health_status":health_status,
            "recommendation":recommendation,
        }

        results.append(result)

        if write:

            cur.execute("""
            INSERT INTO monitor_source_health (
                source_id,
                matched_count,
                qualified_count,
                rejected_count,
                leads_count,
                qualified_rate,
                rejected_rate,
                avg_sale_score,
                health_score,
                health_status,
                recommendation,
                calculated_at
            )
            VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,
                NOW()
            )
            ON CONFLICT (source_id)
            DO UPDATE SET
                matched_count=EXCLUDED.matched_count,
                qualified_count=EXCLUDED.qualified_count,
                rejected_count=EXCLUDED.rejected_count,
                leads_count=EXCLUDED.leads_count,
                qualified_rate=EXCLUDED.qualified_rate,
                rejected_rate=EXCLUDED.rejected_rate,
                avg_sale_score=EXCLUDED.avg_sale_score,
                health_score=EXCLUDED.health_score,
                health_status=EXCLUDED.health_status,
                recommendation=EXCLUDED.recommendation,
                calculated_at=NOW()
            """,(
                source_id,
                matched,
                qualified,
                rejected,
                leads,
                q_rate,
                r_rate,
                avg_sale,
                health,
                health_status,
                recommendation,
            ))

    return results


def main():

    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    conn=psycopg2.connect(
        os.environ["DATABASE_URL"]
    )

    try:
        cur=conn.cursor()

        scored,count=apply_sale_scores(
            cur,
            args.apply,
        )

        health=rebuild_health(
            cur,
            scored,
            args.apply,
        )

        if args.apply:
            conn.commit()
        else:
            conn.rollback()

        print(
            "mode=",
            "APPLY" if args.apply else "DRY",
        )

        print(
            "sale_scored=",
            count,
        )

        print()
        print("SOURCE HEALTH:")

        for row in sorted(
            health,
            key=lambda x:(
                -x["health_score"],
                x["title"],
            ),
        ):
            print(
                "%3d | %-17s | match=%2d good=%2d bad=%2d leads=%2d sale=%5.1f | %s"
                % (
                    row["health_score"],
                    row["health_status"],
                    row["matched"],
                    row["qualified"],
                    row["rejected"],
                    row["leads"],
                    row["avg_sale_score"],
                    row["title"][:45],
                )
            )

        print()
        print("STAGE5_RUNNER=PASS")

    finally:
        conn.close()


if __name__=="__main__":
    main()
