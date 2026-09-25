from __future__ import annotations

import os

import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import require_owner
from app.monitoring import outcomes


router=APIRouter(
    tags=["monitoring"],
    dependencies=[Depends(require_owner)],
)


class OutcomeBody(BaseModel):
    outcome: str
    reason: str | None = None
    note: str | None = None


def _con():
    return psycopg2.connect(
        os.environ["DATABASE_URL"]
    )


@router.post(
    "/findings/{message_id}/outcome"
)
def set_finding_outcome(
    message_id: str,
    body: OutcomeBody,
):

    con=_con()

    try:
        with con:
            with con.cursor() as cur:
                try:
                    result=outcomes.record(
                        cur,
                        message_id=message_id,
                        outcome=body.outcome,
                        reason=body.reason,
                        note=body.note,
                    )

                except (ValueError,KeyError) as e:
                    raise HTTPException(
                        status_code=422,
                        detail=str(e),
                    )

        return {
            "ok":True,
            "outcome":result,
        }

    finally:
        con.close()


@router.get(
    "/learning/overview"
)
def learning_overview():

    con=_con()

    try:
        with con.cursor() as cur:

            cur.execute("""
            SELECT
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
            FROM monitor_learning_stats
            ORDER BY
              dimension_type,
              evidence_score DESC
            """)

            cols=[
                d[0]
                for d in cur.description
            ]

            rows=[
                dict(zip(cols,row))
                for row in cur.fetchall()
            ]

        return {
            "ok":True,
            "items":rows,
        }

    finally:
        con.close()
