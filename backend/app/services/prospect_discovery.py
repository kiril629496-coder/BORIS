"""
BORIS B2B Prospecting — discovery layer.

Stage B.

Company name -> public search result candidates -> scoring.

No AI.
No SMTP.
No paid API.

Discovery provider is deliberately isolated from the Stage A crawler.
"""

import re
import time
import logging

from urllib.parse import (
    urlparse,
    parse_qs,
    unquote,
)

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.services import prospecting as crawl


logger = logging.getLogger(__name__)


SEARCH_URL = "https://html.duckduckgo.com/html/"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

TIMEOUT = (5, 15)


BAD_DOMAINS = {
    "avito.ru",
    "vk.com",
    "vk.ru",
    "t.me",
    "telegram.me",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "2gis.ru",
    "2gis.com",
    "yandex.ru",
    "ya.ru",
    "maps.yandex.ru",
    "companies.rbc.ru",
    "rusprofile.ru",
    "checko.ru",
    "sbis.ru",
    "list-org.com",
    "spark-interfax.ru",
    "zoon.ru",
    "flamp.ru",
    "orgpage.ru",
    "yp.ru",
}


LEGAL_WORDS = {
    "ооо",
    "оао",
    "зао",
    "пао",
    "ао",
    "ип",
    "llc",
    "ltd",
    "inc",
}


def root_domain(url):

    try:
        host = (
            urlparse(url).hostname
            or ""
        ).lower()

    except Exception:
        return ""

    return host.removeprefix("www.").rstrip(".")


def bad_domain(domain):

    domain = (
        domain
        or ""
    ).lower().removeprefix("www.")

    for blocked in BAD_DOMAINS:

        if (
            domain == blocked
            or domain.endswith("." + blocked)
        ):
            return True

    return False


def tokens(value):

    value = (
        value
        or ""
    ).lower().replace("ё", "е")

    items = re.findall(
        r"[a-zа-я0-9]{2,}",
        value,
        flags=re.I,
    )

    return {
        x
        for x in items
        if x not in LEGAL_WORDS
    }


def unwrap_result_url(value):

    value = (value or "").strip()

    if not value:
        return None

    if value.startswith("//"):
        value = "https:" + value

    try:
        p = urlparse(value)

        qs = parse_qs(p.query)

        if "uddg" in qs and qs["uddg"]:
            value = unquote(
                qs["uddg"][0]
            )

    except Exception:
        pass

    if not value.startswith(
        ("http://", "https://")
    ):
        return None

    return value


def build_query(
    company_name,
    city=None,
):

    name = (
        company_name
        or ""
    ).strip()

    # Search engines often behave badly with brands such as
    # "1С", where the brand contains one digit + one Cyrillic letter.
    # Exact quotes are therefore not the only signal.
    bits = [
        name,
        "официальный сайт",
    ]

    if city:
        bits.append(
            city.strip()
        )

    return " ".join(
        x
        for x in bits
        if x
    )


def score_candidate(
    company_name,
    city,
    url,
    title,
    snippet,
    rank,
):

    domain = root_domain(url)

    if not domain:
        return -100

    if bad_domain(domain):
        return -100

    company_tokens = tokens(
        company_name
    )

    text_tokens = tokens(
        " ".join(
            [
                domain.replace(".", " "),
                title or "",
                snippet or "",
            ]
        )
    )

    score = 0.0

    if company_tokens:

        overlap = (
            company_tokens
            & text_tokens
        )

        coverage = (
            len(overlap)
            / len(company_tokens)
        )

        score += coverage * 55.0

        if coverage >= 0.999:
            score += 15.0

    # Domain name match is particularly valuable.
    compact_domain = re.sub(
        r"[^a-zа-я0-9]+",
        "",
        domain.split(".")[0].lower(),
    )

    compact_company = re.sub(
        r"[^a-zа-я0-9]+",
        "",
        " ".join(
            sorted(company_tokens)
        ).lower(),
    )

    for token in company_tokens:

        if len(token) >= 4 and token in compact_domain:
            score += 15.0
            break

    if (
        compact_company
        and len(compact_company) >= 5
        and compact_company in compact_domain
    ):
        score += 20.0

    if city:

        city_tokens = tokens(city)

        hay = tokens(
            (title or "")
            + " "
            + (snippet or "")
        )

        if city_tokens & hay:
            score += 8.0

    # Search rank signal.
    if rank == 1:
        score += 8.0
    elif rank == 2:
        score += 5.0
    elif rank == 3:
        score += 3.0

    if url.lower().startswith("https://"):
        score += 2.0

    return round(
        min(score, 100.0),
        3,
    )


def search_public(
    company_name,
    city=None,
    max_results=8,
):

    query = build_query(
        company_name,
        city,
    )

    session = requests.Session()

    response = session.post(
        SEARCH_URL,
        data={
            "q": query,
        },
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*",
            "Accept-Language": "ru,en;q=0.7",
        },
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    if len(response.content) > 3_000_000:
        raise ValueError(
            "search response too large"
        )

    soup = BeautifulSoup(
        response.text,
        "lxml",
    )

    results = []

    seen_domains = set()

    for node in soup.select(
        ".result"
    ):

        a = node.select_one(
            ".result__a"
        )

        if not a:
            continue

        url = unwrap_result_url(
            a.get("href")
        )

        if not url:
            continue

        domain = root_domain(url)

        if (
            not domain
            or domain in seen_domains
        ):
            continue

        seen_domains.add(domain)

        snippet_node = node.select_one(
            ".result__snippet"
        )

        title = a.get_text(
            " ",
            strip=True,
        )

        snippet = (
            snippet_node.get_text(
                " ",
                strip=True,
            )
            if snippet_node
            else ""
        )

        rank = len(results) + 1

        score = score_candidate(
            company_name,
            city,
            url,
            title,
            snippet,
            rank,
        )

        results.append(
            {
                "url": url,
                "domain": domain,
                "title": title[:1000],
                "snippet": snippet[:3000],
                "rank": rank,
                "score": score,
            }
        )

        if len(results) >= max_results:
            break

    return {
        "provider": "ddg_html",
        "query": query,
        "results": results,
    }


def discover_company(
    db,
    owner_id,
    company_id,
    auto_threshold=62.0,
):

    company = db.execute(
        text(
            """
            SELECT
                id,
                name,
                city,
                website
            FROM prospect_companies
            WHERE id=:id
              AND owner_id=:owner
            """
        ),
        {
            "id": company_id,
            "owner": owner_id,
        },
    ).mappings().first()

    if not company:
        raise ValueError(
            "company not found"
        )

    if company["website"]:

        return {
            "company_id": company_id,
            "status": "already_has_website",
            "website": company["website"],
            "selected": True,
            "score": 100.0,
            "results": [],
        }

    result = search_public(
        company["name"],
        company["city"],
    )

    db.execute(
        text(
            """
            DELETE FROM prospect_discovery
            WHERE company_id=:company
              AND provider=:provider
            """
        ),
        {
            "company": company_id,
            "provider": result["provider"],
        },
    )

    for item in result["results"]:

        if item["score"] < 0:
            continue

        db.execute(
            text(
                """
                INSERT INTO prospect_discovery
                (
                    company_id,
                    provider,
                    query,
                    candidate_url,
                    candidate_domain,
                    title,
                    snippet,
                    score,
                    rank
                )
                VALUES
                (
                    :company,
                    :provider,
                    :query,
                    :url,
                    :domain,
                    :title,
                    :snippet,
                    :score,
                    :rank
                )
                ON CONFLICT
                (
                    company_id,
                    provider,
                    candidate_url
                )
                DO UPDATE SET
                    title=EXCLUDED.title,
                    snippet=EXCLUDED.snippet,
                    score=EXCLUDED.score,
                    rank=EXCLUDED.rank
                """
            ),
            {
                "company": company_id,
                "provider": result["provider"],
                "query": result["query"],
                **item,
            },
        )

    viable = [
        x
        for x in result["results"]
        if x["score"] >= 0
        and not bad_domain(
            x["domain"]
        )
    ]

    viable.sort(
        key=lambda x: (
            -x["score"],
            x["rank"],
        )
    )

    best = (
        viable[0]
        if viable
        else None
    )

    selected = False

    status = "not_found"

    if best:

        if best["score"] >= auto_threshold:

            selected = True

            status = "selected"

            db.execute(
                text(
                    """
                    UPDATE prospect_discovery
                    SET selected=false
                    WHERE company_id=:company
                    """
                ),
                {
                    "company": company_id,
                },
            )

            db.execute(
                text(
                    """
                    UPDATE prospect_discovery
                    SET selected=true
                    WHERE company_id=:company
                      AND provider=:provider
                      AND candidate_url=:url
                    """
                ),
                {
                    "company": company_id,
                    "provider": result["provider"],
                    "url": best["url"],
                },
            )

            db.execute(
                text(
                    """
                    UPDATE prospect_companies
                    SET
                        website=:website,
                        domain=:domain,
                        source='web_discovery',
                        source_url=:website,
                        search_query=:query,
                        discovery_status='selected',
                        discovery_provider=:provider,
                        discovery_score=:score,
                        discovered_at=now(),
                        updated_at=now()
                    WHERE id=:company
                    """
                ),
                {
                    "website": best["url"],
                    "domain": best["domain"],
                    "query": result["query"],
                    "provider": result["provider"],
                    "score": best["score"],
                    "company": company_id,
                },
            )

        else:

            status = "needs_review"

            db.execute(
                text(
                    """
                    UPDATE prospect_companies
                    SET
                        search_query=:query,
                        discovery_status='needs_review',
                        discovery_provider=:provider,
                        discovery_score=:score,
                        discovered_at=now(),
                        updated_at=now()
                    WHERE id=:company
                    """
                ),
                {
                    "query": result["query"],
                    "provider": result["provider"],
                    "score": best["score"],
                    "company": company_id,
                },
            )

    else:

        db.execute(
            text(
                """
                UPDATE prospect_companies
                SET
                    search_query=:query,
                    discovery_status='not_found',
                    discovery_provider=:provider,
                    discovery_score=NULL,
                    discovered_at=now(),
                    updated_at=now()
                WHERE id=:company
                """
            ),
            {
                "query": result["query"],
                "provider": result["provider"],
                "company": company_id,
            },
        )

    db.commit()

    return {
        "company_id": company_id,
        "status": status,
        "selected": selected,
        "website": (
            best["url"]
            if selected
            else None
        ),
        "score": (
            best["score"]
            if best
            else None
        ),
        "query": result["query"],
        "results": viable,
    }


def discover_and_parse(
    db,
    owner_id,
    company_id,
    auto_threshold=62.0,
    max_pages=8,
):

    discovery = discover_company(
        db,
        owner_id,
        company_id,
        auto_threshold=auto_threshold,
    )

    if (
        discovery["status"]
        not in (
            "selected",
            "already_has_website",
        )
    ):
        return {
            "discovery": discovery,
            "crawl": None,
        }

    crawl_result = crawl.run_company(
        db,
        owner_id,
        company_id,
        max_pages=max_pages,
    )

    return {
        "discovery": discovery,
        "crawl": crawl_result,
    }


def select_candidate(
    db,
    owner_id,
    company_id,
    discovery_id,
):

    row = db.execute(
        text(
            """
            SELECT
                d.id,
                d.candidate_url,
                d.candidate_domain,
                d.score
            FROM prospect_discovery d
            JOIN prospect_companies c
              ON c.id=d.company_id
            WHERE d.id=:discovery
              AND d.company_id=:company
              AND c.owner_id=:owner
            """
        ),
        {
            "discovery": discovery_id,
            "company": company_id,
            "owner": owner_id,
        },
    ).mappings().first()

    if not row:
        raise ValueError(
            "candidate not found"
        )

    db.execute(
        text(
            """
            UPDATE prospect_discovery
            SET selected=false
            WHERE company_id=:company
            """
        ),
        {
            "company": company_id,
        },
    )

    db.execute(
        text(
            """
            UPDATE prospect_discovery
            SET selected=true
            WHERE id=:id
            """
        ),
        {
            "id": discovery_id,
        },
    )

    db.execute(
        text(
            """
            UPDATE prospect_companies
            SET
                website=:website,
                domain=:domain,
                source='manual_candidate_selection',
                source_url=:website,
                discovery_status='selected_manual',
                discovery_score=:score,
                updated_at=now()
            WHERE id=:company
              AND owner_id=:owner
            """
        ),
        {
            "website": row["candidate_url"],
            "domain": row["candidate_domain"],
            "score": row["score"],
            "company": company_id,
            "owner": owner_id,
        },
    )

    db.commit()

    return {
        "ok": True,
        "company_id": company_id,
        "website": row["candidate_url"],
    }
