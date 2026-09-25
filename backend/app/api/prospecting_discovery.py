"""
BORIS Prospecting Stage B API.
"""

from typing import (
    List,
    Optional,
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.services import prospect_discovery as discovery


router = APIRouter(
    prefix="/api/prospecting",
    tags=["prospecting-discovery"],
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
        "id",
        None,
    )

    if value is None and isinstance(
        user,
        dict,
    ):
        value = (
            user.get("id")
            or user.get("user_id")
        )

    if value is None:
        raise HTTPException(
            401,
            "user id unavailable",
        )

    return int(value)


class BulkCompany(BaseModel):

    name: str = Field(
        min_length=2,
        max_length=500,
    )

    city: Optional[str] = Field(
        default=None,
        max_length=300,
    )

    website: Optional[str] = Field(
        default=None,
        max_length=2000,
    )


class BulkImport(BaseModel):

    companies: List[BulkCompany] = Field(
        min_length=1,
        max_length=1000,
    )


class DiscoverRequest(BaseModel):

    company_id: int

    auto_threshold: float = Field(
        default=62.0,
        ge=0,
        le=100,
    )


class PipelineRequest(BaseModel):

    company_id: int

    auto_threshold: float = Field(
        default=62.0,
        ge=0,
        le=100,
    )

    max_pages: int = Field(
        default=8,
        ge=1,
        le=20,
    )


class SelectRequest(BaseModel):

    company_id: int

    discovery_id: int


@router.get("/discovery/health")
def health(
    user=Depends(get_current_user),
):

    return {
        "ok": True,
        "stage": "B",
        "name_search": True,
        "bulk": True,
        "ai": False,
        "email_send": False,
    }


@router.post("/bulk")
def bulk_import(
    body: BulkImport,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    inserted = []

    skipped = []

    for item in body.companies:

        normalized = (
            " ".join(
                item.name
                .strip()
                .lower()
                .split()
            )
        )

        existing = db.execute(
            text(
                """
                SELECT id
                FROM prospect_companies
                WHERE owner_id=:owner
                  AND normalized_name=:normalized
                  AND COALESCE(city,'')=COALESCE(:city,'')
                LIMIT 1
                """
            ),
            {
                "owner": owner,
                "normalized": normalized,
                "city": item.city,
            },
        ).scalar()

        if existing:

            skipped.append(
                {
                    "name": item.name,
                    "id": int(existing),
                    "reason": "duplicate",
                }
            )

            continue

        website = item.website

        domain = None

        if website:

            from app.services.prospecting import (
                normalize_url,
            )

            website = normalize_url(
                website
            )

            if website:

                from urllib.parse import (
                    urlparse,
                )

                domain = (
                    urlparse(
                        website
                    ).hostname
                    or ""
                ).lower().removeprefix(
                    "www."
                )

        company_id = db.execute(
            text(
                """
                INSERT INTO prospect_companies
                (
                    owner_id,
                    name,
                    normalized_name,
                    city,
                    website,
                    domain,
                    status
                )
                VALUES
                (
                    :owner,
                    :name,
                    :normalized,
                    :city,
                    :website,
                    :domain,
                    'new'
                )
                RETURNING id
                """
            ),
            {
                "owner": owner,
                "name": item.name.strip(),
                "normalized": normalized,
                "city": item.city,
                "website": website,
                "domain": domain,
            },
        ).scalar_one()

        inserted.append(
            {
                "id": int(company_id),
                "name": item.name,
            }
        )

    db.commit()

    return {
        "ok": True,
        "inserted": inserted,
        "inserted_count": len(
            inserted
        ),
        "skipped": skipped,
        "skipped_count": len(
            skipped
        ),
    }


@router.post("/discover")
def discover(
    body: DiscoverRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    try:

        return discovery.discover_company(
            db,
            uid(user),
            body.company_id,
            auto_threshold=body.auto_threshold,
        )

    except ValueError as exc:

        raise HTTPException(
            400,
            str(exc),
        )

    except Exception as exc:

        raise HTTPException(
            502,
            type(exc).__name__
            + ": "
            + str(exc),
        )


@router.post("/pipeline")
def pipeline(
    body: PipelineRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    try:

        result = (
            discovery.discover_and_parse(
                db,
                uid(user),
                body.company_id,
                auto_threshold=body.auto_threshold,
                max_pages=body.max_pages,
            )
        )

        crawl_result = result[
            "crawl"
        ]

        return {
            "ok": True,
            "discovery": result[
                "discovery"
            ],
            "crawl": (
                {
                    "run_id": crawl_result[
                        "run_id"
                    ],
                    "pages_fetched": crawl_result[
                        "pages_fetched"
                    ],
                    "emails_found": len(
                        crawl_result[
                            "emails"
                        ]
                    ),
                    "phones_found": len(
                        crawl_result[
                            "phones"
                        ]
                    ),
                }
                if crawl_result
                else None
            ),
        }

    except ValueError as exc:

        raise HTTPException(
            400,
            str(exc),
        )

    except Exception as exc:

        raise HTTPException(
            502,
            type(exc).__name__
            + ": "
            + str(exc),
        )


@router.post("/candidate/select")
def candidate_select(
    body: SelectRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    try:

        return discovery.select_candidate(
            db,
            uid(user),
            body.company_id,
            body.discovery_id,
        )

    except ValueError as exc:

        raise HTTPException(
            400,
            str(exc),
        )


@router.get("/candidates/{company_id}")
def candidates(
    company_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    rows = db.execute(
        text(
            """
            SELECT
                d.id,
                d.provider,
                d.candidate_url,
                d.candidate_domain,
                d.title,
                d.snippet,
                d.score,
                d.rank,
                d.selected
            FROM prospect_discovery d
            JOIN prospect_companies c
              ON c.id=d.company_id
            WHERE d.company_id=:company
              AND c.owner_id=:owner
            ORDER BY
                d.score DESC,
                d.rank ASC
            """
        ),
        {
            "company": company_id,
            "owner": owner,
        },
    ).mappings().all()

    return {
        "items": [
            dict(x)
            for x in rows
        ]
    }


@router.get("/benchmark")
def benchmark(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):

    owner = uid(user)

    row = db.execute(
        text(
            """
            SELECT

                COUNT(*) AS companies,

                COUNT(*) FILTER (
                    WHERE website IS NOT NULL
                ) AS websites_found,

                COUNT(*) FILTER (
                    WHERE discovery_status IN
                    (
                        'selected',
                        'selected_manual'
                    )
                ) AS websites_selected,

                COUNT(*) FILTER (
                    WHERE discovery_status='needs_review'
                ) AS needs_review,

                COUNT(*) FILTER (
                    WHERE discovery_status='not_found'
                ) AS not_found,

                COUNT(*) FILTER (
                    WHERE status='parsed'
                ) AS parsed

            FROM prospect_companies
            WHERE owner_id=:owner
            """
        ),
        {
            "owner": owner,
        },
    ).mappings().one()

    contacts = db.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE pc.kind='email'
                ) AS emails,

                COUNT(*) FILTER (
                    WHERE pc.kind='phone'
                ) AS phones,

                COUNT(
                    DISTINCT pc.company_id
                ) AS companies_with_contacts

            FROM prospect_contacts pc

            JOIN prospect_companies c
              ON c.id=pc.company_id

            WHERE c.owner_id=:owner
            """
        ),
        {
            "owner": owner,
        },
    ).mappings().one()

    runs = db.execute(
        text(
            """
            SELECT
                COALESCE(
                    SUM(network_requests),
                    0
                ) AS crawl_requests,

                COALESCE(
                    SUM(pages_fetched),
                    0
                ) AS pages_fetched,

                COALESCE(
                    SUM(ai_calls),
                    0
                ) AS ai_calls,

                COALESCE(
                    SUM(total_cost_rub),
                    0
                ) AS paid_cost_rub

            FROM prospect_runs
            WHERE owner_id=:owner
            """
        ),
        {
            "owner": owner,
        },
    ).mappings().one()

    result = dict(row)

    result.update(
        dict(contacts)
    )

    result.update(
        dict(runs)
    )

    companies = int(
        result["companies"] or 0
    )

    useful = int(
        result[
            "companies_with_contacts"
        ] or 0
    )

    emails = int(
        result["emails"] or 0
    )

    cost = float(
        result["paid_cost_rub"] or 0
    )

    result[
        "website_find_rate_pct"
    ] = (
        round(
            100
            * int(
                result[
                    "websites_found"
                ] or 0
            )
            / companies,
            2,
        )
        if companies
        else 0
    )

    result[
        "contact_rate_pct"
    ] = (
        round(
            100
            * useful
            / companies,
            2,
        )
        if companies
        else 0
    )

    result[
        "cost_per_company_rub"
    ] = (
        round(
            cost / companies,
            6,
        )
        if companies
        else 0
    )

    result[
        "cost_per_useful_company_rub"
    ] = (
        round(
            cost / useful,
            6,
        )
        if useful
        else 0
    )

    result[
        "cost_per_email_rub"
    ] = (
        round(
            cost / emails,
            6,
        )
        if emails
        else 0
    )

    return result
