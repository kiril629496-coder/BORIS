"""
BORIS B2B Prospecting API.

Stage A:
manual company + known website -> public contact discovery.

No sending.
No AI.
"""

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from pydantic import BaseModel, Field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import SessionLocal

from app.services import prospecting as svc


router = APIRouter(
    prefix="/api/prospecting",
    tags=["prospecting"],
)


def get_db():

    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


def uid(user):

    value = getattr(
        user,
        'id',
        None,
    )

    if value is None and isinstance(
        user,
        dict,
    ):
        value = (
            user.get('id')
            or user.get('user_id')
        )

    if value is None:
        raise HTTPException(
            401,
            'user id unavailable',
        )

    return int(value)


class CompanyCreate(BaseModel):

    name: str = Field(
        min_length=2,
        max_length=500,
    )

    website: Optional[str] = Field(
        default=None,
        max_length=2000,
    )

    city: Optional[str] = Field(
        default=None,
        max_length=300,
    )


class ParseRequest(BaseModel):

    company_id: int

    max_pages: int = Field(
        default=8,
        ge=1,
        le=20,
    )


@router.get("/health")
def health(
    user=Depends(get_current_user),
):

    return {
        "ok": True,
        "module": "prospecting",
        "stage": "A",
        "sending": False,
        "ai": False,
    }


@router.post("/companies")
def create_company(
    body: CompanyCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    try:

        company_id = svc.create_company(
            db,
            owner,
            body.name,
            website=body.website,
            city=body.city,
        )

        db.commit()

    except Exception as exc:

        db.rollback()

        raise HTTPException(
            400,
            str(exc),
        )

    return {
        "ok": True,
        "company_id": company_id,
    }


@router.get("/companies")
def companies(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    rows = db.execute(
        text(
            """
            SELECT
                c.id,
                c.name,
                c.city,
                c.website,
                c.domain,
                c.status,
                c.created_at,

                COUNT(
                    DISTINCT pc.id
                ) FILTER (
                    WHERE pc.kind='email'
                ) AS emails,

                COUNT(
                    DISTINCT pc.id
                ) FILTER (
                    WHERE pc.kind='phone'
                ) AS phones

            FROM prospect_companies c

            LEFT JOIN prospect_contacts pc
                ON pc.company_id=c.id

            WHERE c.owner_id=:owner

            GROUP BY c.id

            ORDER BY c.id DESC

            LIMIT :limit
            OFFSET :offset
            """
        ),
        {
            'owner': owner,
            'limit': limit,
            'offset': offset,
        },
    ).mappings().all()

    return {
        "items": [
            dict(row)
            for row in rows
        ]
    }


@router.get("/companies/{company_id}")
def company_detail(
    company_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    company = db.execute(
        text(
            """
            SELECT *
            FROM prospect_companies
            WHERE id=:id
              AND owner_id=:owner
            """
        ),
        {
            'id': company_id,
            'owner': owner,
        },
    ).mappings().first()

    if not company:
        raise HTTPException(
            404,
            'company not found',
        )

    contacts = db.execute(
        text(
            """
            SELECT
                id,
                kind,
                value,
                normalized_value,
                source_url,
                confidence,
                created_at
            FROM prospect_contacts
            WHERE company_id=:id
            ORDER BY
                kind,
                id
            """
        ),
        {'id': company_id},
    ).mappings().all()

    runs = db.execute(
        text(
            """
            SELECT *
            FROM prospect_runs
            WHERE company_id=:id
            ORDER BY id DESC
            LIMIT 20
            """
        ),
        {'id': company_id},
    ).mappings().all()

    return {
        "company": dict(company),
        "contacts": [
            dict(row)
            for row in contacts
        ],
        "runs": [
            dict(row)
            for row in runs
        ],
    }


@router.post("/parse")
def parse_company(
    body: ParseRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    try:

        result = svc.run_company(
            db,
            owner,
            body.company_id,
            max_pages=body.max_pages,
        )

    except ValueError as exc:

        raise HTTPException(
            400,
            str(exc),
        )

    except Exception as exc:

        raise HTTPException(
            502,
            (
                type(exc).__name__
                + ': '
                + str(exc)
            ),
        )

    return {
        "ok": True,
        "run_id": result["run_id"],
        "pages_requested": result[
            "pages_requested"
        ],
        "pages_fetched": result[
            "pages_fetched"
        ],
        "network_requests": result[
            "network_requests"
        ],
        "bytes_downloaded": result[
            "bytes_downloaded"
        ],
        "emails_found": len(
            result["emails"]
        ),
        "phones_found": len(
            result["phones"]
        ),
    }


@router.get("/economics")
def economics(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    row = db.execute(
        text(
            """
            SELECT

                COUNT(*) AS runs,

                COUNT(*) FILTER (
                    WHERE status='done'
                ) AS successful_runs,

                COALESCE(
                    SUM(pages_fetched),
                    0
                ) AS pages_fetched,

                COALESCE(
                    SUM(network_requests),
                    0
                ) AS network_requests,

                COALESCE(
                    SUM(bytes_downloaded),
                    0
                ) AS bytes_downloaded,

                COALESCE(
                    SUM(emails_found),
                    0
                ) AS emails_found,

                COALESCE(
                    SUM(phones_found),
                    0
                ) AS phones_found,

                COALESCE(
                    SUM(ai_calls),
                    0
                ) AS ai_calls,

                COALESCE(
                    SUM(total_cost_rub),
                    0
                ) AS total_cost_rub

            FROM prospect_runs
            WHERE owner_id=:owner
            """
        ),
        {'owner': owner},
    ).mappings().one()

    companies = db.execute(
        text(
            """
            SELECT
                COUNT(*) AS companies,

                COUNT(*) FILTER (
                    WHERE EXISTS (
                        SELECT 1
                        FROM prospect_contacts pc
                        WHERE pc.company_id=c.id
                    )
                ) AS companies_with_contacts

            FROM prospect_companies c
            WHERE owner_id=:owner
            """
        ),
        {'owner': owner},
    ).mappings().one()

    result = dict(row)

    result.update(
        dict(companies)
    )

    total_cost = float(
        result['total_cost_rub'] or 0
    )

    company_count = int(
        result['companies'] or 0
    )

    useful = int(
        result[
            'companies_with_contacts'
        ] or 0
    )

    emails = int(
        result['emails_found'] or 0
    )

    result[
        'cost_per_company_rub'
    ] = (
        round(
            total_cost / company_count,
            6,
        )
        if company_count
        else 0
    )

    result[
        'cost_per_company_with_contact_rub'
    ] = (
        round(
            total_cost / useful,
            6,
        )
        if useful
        else 0
    )

    result[
        'cost_per_email_rub'
    ] = (
        round(
            total_cost / emails,
            6,
        )
        if emails
        else 0
    )

    return result
