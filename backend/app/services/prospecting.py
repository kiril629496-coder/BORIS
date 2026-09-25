"""
BORIS B2B Prospecting.

Stage A:
website crawl -> public emails / phones.

No AI.
No SMTP.
No automatic outreach.
"""

import re
import time
import html
import hashlib
import logging
import ipaddress
import socket

from urllib.parse import (
    unquote,
    urljoin,
    urlparse,
    urlunparse,
)

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

logger = logging.getLogger(__name__)


USER_AGENT = (
    "BORIS-Prospecting/1.0 "
    "(business contact discovery)"
)

TIMEOUT = (5, 12)

MAX_PAGE_BYTES = 2_000_000

MAX_PAGES_DEFAULT = 8

CONTACT_HINTS = (
    "contact",
    "contacts",
    "kontakt",
    "kontakty",
    "контакт",
    "контакты",
    "about",
    "company",
    "requisites",
    "rekvizit",
    "реквизит",
)

# Some business sites render navigation with JavaScript and the contact page is
# not present as a normal <a> in the homepage HTML. These are conservative,
# same-domain fallbacks only; crawl_website still applies public-host checks,
# request/byte limits and the existing max_pages cap.
COMMON_CONTACT_PATHS = (
    "/contacts", "/kontakty", "/contact", "/rekvizity", "/requisites",
)


EMAIL_RE = re.compile(
    r"""
    (?<![A-Za-z0-9._%+\-])
    [A-Za-z0-9._%+\-]{1,64}
    @
    [A-Za-z0-9.\-]{1,190}
    \.
    [A-Za-z]{2,24}
    (?![A-Za-z0-9._%+\-])
    """,
    re.X,
)

PHONE_RE = re.compile(
    r"""
    (?:
        (?:\+7|8)
        [\s\-\(\)]*
        \d{3}
        [\s\-\(\)]*
        \d{3}
        [\s\-]*
        \d{2}
        [\s\-]*
        \d{2}
    )
    """,
    re.X,
)


def normalize_company_name(value):
    value = (value or "").strip().lower()

    value = re.sub(
        r'\b(ооо|оао|зао|пао|ип|llc|inc|ltd)\b',
        ' ',
        value,
        flags=re.I,
    )

    value = re.sub(r'[^a-zа-яё0-9]+', ' ', value, flags=re.I)

    return ' '.join(value.split())


def lock_company_domain(db, owner_id, domain):
    """Serialize company creation for one owner/domain inside the DB transaction."""
    domain=(domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return
    key=f"{int(owner_id)}:{domain}"
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),
        {"k": key},
    )


def normalize_email(value):
    """Canonicalize public email values before scoring/storage/outreach.

    Website contact links are frequently percent-encoded (for example
    mailto:%20office@example.ru). Decode that transport encoding first so
    "%20office" can never be scored as a high-quality, sendable address.
    """
    value = html.unescape(str(value or ""))
    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded

    value = value.strip().lower()
    if value.startswith("mailto:"):
        value = value[7:]
    value = value.split("?", 1)[0]
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\s\u200b-\u200d\ufeff]+", "", value)

    value = value.strip(
        ".,;:()[]{}<>\"'"
    )

    return value


def normalize_phone(value):

    digits = re.sub(r'\D+', '', value or '')

    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]

    if len(digits) == 10:
        digits = '7' + digits

    if len(digits) != 11:
        return None

    if not digits.startswith('7'):
        return None

    return '+' + digits


def normalize_url(value):

    value = (value or "").strip()

    if not value:
        return None

    if not re.match(r'^https?://', value, re.I):
        value = 'https://' + value

    p = urlparse(value)

    if not p.hostname:
        return None

    host = p.hostname.lower().rstrip('.')

    scheme = p.scheme.lower()

    if scheme not in ('http', 'https'):
        return None

    netloc = host

    if p.port:
        netloc += ':' + str(p.port)

    return urlunparse(
        (
            scheme,
            netloc,
            p.path or '/',
            '',
            p.query,
            '',
        )
    )


def _is_public_ip(ip):

    try:
        obj = ipaddress.ip_address(ip)

        return not (
            obj.is_private
            or obj.is_loopback
            or obj.is_link_local
            or obj.is_reserved
            or obj.is_multicast
            or obj.is_unspecified
        )

    except Exception:
        return False


def assert_public_host(url):
    """
    SSRF gate.

    Prospecting must never crawl localhost/private infrastructure.
    """

    p = urlparse(url)

    host = p.hostname

    if not host:
        raise ValueError("hostname missing")

    if host.lower() in (
        'localhost',
        'localhost.localdomain',
    ):
        raise ValueError("private hostname")

    try:
        infos = socket.getaddrinfo(
            host,
            p.port or (443 if p.scheme == 'https' else 80),
            type=socket.SOCK_STREAM,
        )

    except socket.gaierror as exc:
        raise ValueError("DNS failed") from exc

    ips = {
        info[4][0]
        for info in infos
        if info and info[4]
    }

    if not ips:
        raise ValueError("DNS returned no addresses")

    for ip in ips:

        if not _is_public_ip(ip):
            raise ValueError(
                "private/reserved target blocked"
            )


def same_domain(a, b):

    ha = (urlparse(a).hostname or '').lower()
    hb = (urlparse(b).hostname or '').lower()

    ha = ha.removeprefix('www.')
    hb = hb.removeprefix('www.')

    return ha == hb



GENERIC_EMAIL_LOCALPARTS = {
    "info",
    "sales",
    "office",
    "hello",
    "contact",
    "support",
    "mail",
    "company",
    "zakaz",
    "order",
    "client",
    "clients",
    "marketing",
    "commerce",
    "commercial",
}

BAD_EMAIL_LOCALPARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "example",
    "test",
    "admin",
    "webmaster",
}

MAX_EMAILS_PER_PAGE = 15
MAX_PHONES_PER_PAGE = 10

MAX_EMAILS_PER_COMPANY = 15
MAX_PHONES_PER_COMPANY = 10


def _email_domain(value):
    try:
        return value.rsplit("@", 1)[1].lower()
    except Exception:
        return ""


def _site_domain(url):
    host = (
        urlparse(url).hostname
        or ""
    ).lower().removeprefix("www.")

    return host


def _domain_matches(site_domain, email_domain):

    site_domain = (
        site_domain
        or ""
    ).lower()

    email_domain = (
        email_domain
        or ""
    ).lower()

    if not site_domain or not email_domain:
        return False

    return (
        email_domain == site_domain
        or email_domain.endswith("." + site_domain)
        or site_domain.endswith("." + email_domain)
    )


def _email_score(value, page_url):

    value = normalize_email(value)

    if not value:
        return -100

    local, _, domain = value.partition("@")

    if not domain:
        return -100

    score = 0

    if local in BAD_EMAIL_LOCALPARTS:
        score -= 50

    if local in GENERIC_EMAIL_LOCALPARTS:
        score += 15

    if _domain_matches(
        _site_domain(page_url),
        domain,
    ):
        score += 50

    if any(
        x in local
        for x in (
            "sales",
            "info",
            "contact",
            "office",
            "order",
            "zakaz",
        )
    ):
        score += 10

    return score


def extract_contacts(page_url, body):

    decoded = html.unescape(
        body or ""
    )

    soup = BeautifulSoup(
        decoded,
        "lxml",
    )

    email_candidates = {}
    phone_candidates = {}

    ###########################################################################
    # 1. STRONG SIGNAL: mailto / tel
    ###########################################################################

    for a in soup.find_all(
        "a",
        href=True,
    ):

        href = (
            a.get("href", "")
            or ""
        ).strip()

        low = href.lower()

        if low.startswith(
            "mailto:"
        ):

            raw = href[7:].split("?")[0]

            value = normalize_email(
                raw
            )

            if (
                value
                and EMAIL_RE.fullmatch(
                    value
                )
            ):

                score = (
                    100
                    + _email_score(
                        value,
                        page_url,
                    )
                )

                email_candidates[
                    value
                ] = max(
                    email_candidates.get(
                        value,
                        -999,
                    ),
                    score,
                )

        elif low.startswith(
            "tel:"
        ):

            value = normalize_phone(
                href[4:]
            )

            if value:

                phone_candidates[
                    value
                ] = max(
                    phone_candidates.get(
                        value,
                        -999,
                    ),
                    100,
                )

    ###########################################################################
    # 2. CONTACT-LIKE BLOCKS
    ###########################################################################

    selectors = [
        "footer",
        "address",
        "[class*='contact']",
        "[id*='contact']",
        "[class*='kontakt']",
        "[id*='kontakt']",
        "[class*='footer']",
        "[id*='footer']",
    ]

    contact_texts = []

    for selector in selectors:

        try:
            nodes = soup.select(
                selector
            )
        except Exception:
            continue

        for node in nodes[:30]:

            text_value = node.get_text(
                " ",
                strip=True,
            )

            if text_value:
                contact_texts.append(
                    text_value
                )

    contact_blob = "\n".join(
        contact_texts
    )

    for item in EMAIL_RE.findall(
        contact_blob
    ):

        value = normalize_email(
            item
        )

        if not value:
            continue

        score = (
            50
            + _email_score(
                value,
                page_url,
            )
        )

        email_candidates[
            value
        ] = max(
            email_candidates.get(
                value,
                -999,
            ),
            score,
        )

    for item in PHONE_RE.findall(
        contact_blob
    ):

        value = normalize_phone(
            item
        )

        if value:

            phone_candidates[
                value
            ] = max(
                phone_candidates.get(
                    value,
                    -999,
                ),
                50,
            )

    ###########################################################################
    # 3. WEAK SIGNAL: whole HTML
    #
    # Only email addresses are allowed here.
    # Whole-page phone regex caused contact explosion on large directories.
    ###########################################################################

    for item in EMAIL_RE.findall(
        decoded
    ):

        value = normalize_email(
            item
        )

        if not value:
            continue

        score = _email_score(
            value,
            page_url,
        )

        # Weak HTML discoveries are accepted only
        # when they match the company's domain.
        if score < 40:
            continue

        email_candidates[
            value
        ] = max(
            email_candidates.get(
                value,
                -999,
            ),
            score,
        )

    ###########################################################################
    # 4. PAGE EXPLOSION PROTECTION
    ###########################################################################

    emails_sorted = sorted(
        email_candidates.items(),
        key=lambda x: (
            -x[1],
            x[0],
        ),
    )

    phones_sorted = sorted(
        phone_candidates.items(),
        key=lambda x: (
            -x[1],
            x[0],
        ),
    )

    emails_sorted = emails_sorted[
        :MAX_EMAILS_PER_PAGE
    ]

    phones_sorted = phones_sorted[
        :MAX_PHONES_PER_PAGE
    ]

    emails = {
        value
        for value, score
        in emails_sorted
        if score >= 40
    }

    phones = {
        value
        for value, score
        in phones_sorted
        if score >= 50
    }

    return emails, phones

def candidate_links(base_url, body):

    soup = BeautifulSoup(body, 'lxml')

    scored = []

    seen = set()

    for a in soup.find_all('a', href=True):

        href = a.get('href', '').strip()

        if not href:
            continue

        if href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
            continue

        url = urljoin(base_url, href)

        p = urlparse(url)

        if p.scheme not in ('http', 'https'):
            continue

        if not same_domain(base_url, url):
            continue

        clean = urlunparse(
            (
                p.scheme,
                p.netloc,
                p.path or '/',
                '',
                '',
                '',
            )
        )

        if clean in seen:
            continue

        seen.add(clean)

        low = (
            clean + ' ' + a.get_text(' ', strip=True)
        ).lower()

        score = 0

        for hint in CONTACT_HINTS:
            if hint in low:
                score += 10

        # homepage child pages are preferable
        if p.path.count('/') <= 2:
            score += 1

        if score > 0:
            scored.append((score, clean))

    scored.sort(
        key=lambda x: (-x[0], len(x[1]))
    )

    return [
        url
        for _, url in scored
    ]


def fetch(session, url):

    assert_public_host(url)

    response = session.get(
        url,
        timeout=TIMEOUT,
        allow_redirects=True,
        stream=True,
    )

    response.raise_for_status()

    final_url = response.url

    assert_public_host(final_url)

    ctype = (
        response.headers.get('content-type')
        or ''
    ).lower()

    if (
        'text/html' not in ctype
        and 'application/xhtml' not in ctype
    ):
        response.close()
        raise ValueError(
            'not HTML: ' + ctype[:100]
        )

    chunks = []

    size = 0

    for chunk in response.iter_content(65536):

        if not chunk:
            continue

        size += len(chunk)

        if size > MAX_PAGE_BYTES:
            response.close()
            raise ValueError(
                'page exceeds byte limit'
            )

        chunks.append(chunk)

    raw = b''.join(chunks)

    encoding = (
        response.encoding
        or response.apparent_encoding
        or 'utf-8'
    )

    try:
        body = raw.decode(
            encoding,
            errors='replace',
        )

    except LookupError:
        body = raw.decode(
            'utf-8',
            errors='replace',
        )

    return final_url, body, size


def crawl_website(
    website,
    max_pages=MAX_PAGES_DEFAULT,
    delay=0.35,
):

    website = normalize_url(website)

    if not website:
        raise ValueError('invalid website')

    max_pages = max(
        1,
        min(int(max_pages), 20)
    )

    delay = max(
        0.1,
        min(float(delay), 5.0)
    )

    session = requests.Session()

    session.headers.update(
        {
            'User-Agent': USER_AGENT,
            'Accept': (
                'text/html,application/xhtml+xml'
            ),
        }
    )

    queue = [website]

    visited = set()

    pages = []

    emails = {}

    phones = {}

    requests_count = 0

    bytes_downloaded = 0

    while queue and len(visited) < max_pages:

        url = queue.pop(0)

        if url in visited:
            continue

        visited.add(url)

        requests_count += 1

        try:

            final_url, body, size = fetch(
                session,
                url,
            )

        except Exception as exc:

            pages.append(
                {
                    'url': url,
                    'ok': False,
                    'error': (
                        type(exc).__name__
                        + ': '
                        + str(exc)[:300]
                    ),
                }
            )

            continue

        bytes_downloaded += size

        found_emails, found_phones = (
            extract_contacts(
                final_url,
                body,
            )
        )

        for value in found_emails:

            if len(
                emails
            ) >= MAX_EMAILS_PER_COMPANY:
                break

            emails.setdefault(
                value,
                final_url,
            )

        for value in found_phones:

            if len(
                phones
            ) >= MAX_PHONES_PER_COMPANY:
                break

            phones.setdefault(
                value,
                final_url,
            )

        pages.append(
            {
                'url': final_url,
                'ok': True,
                'bytes': size,
                'emails': len(found_emails),
                'phones': len(found_phones),
            }
        )

        for link in candidate_links(
            final_url,
            body,
        ):

            if (
                link not in visited
                and link not in queue
            ):
                queue.append(link)

        # After the homepage, add a few conventional same-domain contact URLs
        # only when there is still crawl budget. Real links discovered above keep
        # priority; guessed paths are merely a bounded fallback for JS menus.
        if len(visited) == 1 and len(queue) < max_pages:
            origin=urlunparse((urlparse(final_url).scheme,urlparse(final_url).netloc,'/','','',''))
            for path in COMMON_CONTACT_PATHS:
                guess=urljoin(origin,path.lstrip('/'))
                if same_domain(final_url,guess) and guess not in visited and guess not in queue:
                    queue.append(guess)

        if queue:
            time.sleep(delay)

    return {
        'website': website,
        'pages_requested': len(visited),
        'pages_fetched': sum(
            1
            for p in pages
            if p.get('ok')
        ),
        'network_requests': requests_count,
        'bytes_downloaded': bytes_downloaded,
        'emails': emails,
        'phones': phones,
        'pages': pages,
    }


def create_company(
    db,
    owner_id,
    name,
    website=None,
    city=None,
):

    normalized_name = normalize_company_name(
        name
    )

    website = normalize_url(
        website
    ) if website else None

    domain = None

    if website:
        domain = (
            urlparse(website).hostname
            or ''
        ).lower().removeprefix('www.')

    if domain:
        lock_company_domain(db, owner_id, domain)
        existing = db.execute(
            text(
                """
                SELECT id
                FROM prospect_companies
                WHERE owner_id=:owner_id AND lower(domain)=:domain
                ORDER BY id
                LIMIT 1
                """
            ),
            {"owner_id": owner_id, "domain": domain},
        ).scalar()
        if existing:
            return int(existing)

    row = db.execute(
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
                :owner_id,
                :name,
                :normalized_name,
                :city,
                :website,
                :domain,
                'new'
            )
            RETURNING id
            """
        ),
        {
            'owner_id': owner_id,
            'name': name.strip(),
            'normalized_name': normalized_name,
            'city': city,
            'website': website,
            'domain': domain,
        },
    ).scalar_one()

    return int(row)


def save_contact(
    db,
    company_id,
    kind,
    value,
    source_url,
    confidence=0.95,
):

    if kind == 'email':
        normalized = normalize_email(value)

    elif kind == 'phone':
        normalized = normalize_phone(value)

    else:
        normalized = value.strip()

    if not normalized:
        return False

    suppressed = db.execute(
        text(
            """
            SELECT 1
            FROM prospect_suppression
            WHERE kind=:kind
              AND normalized_value=:value
            LIMIT 1
            """
        ),
        {
            'kind': kind,
            'value': normalized,
        },
    ).first()

    if suppressed:
        return False

    db.execute(
        text(
            """
            INSERT INTO prospect_contacts
            (
                company_id,
                kind,
                value,
                normalized_value,
                source_url,
                confidence
            )
            VALUES
            (
                :company_id,
                :kind,
                :value,
                :normalized,
                :source_url,
                :confidence
            )
            ON CONFLICT
            (
                company_id,
                kind,
                normalized_value
            )
            DO UPDATE SET
                source_url=EXCLUDED.source_url,
                confidence=GREATEST(
                    prospect_contacts.confidence,
                    EXCLUDED.confidence
                )
            """
        ),
        {
            'company_id': company_id,
            'kind': kind,
            'value': value,
            'normalized': normalized,
            'source_url': source_url,
            'confidence': confidence,
        },
    )

    return True


def run_company(
    db,
    owner_id,
    company_id,
    max_pages=8,
):

    company = db.execute(
        text(
            """
            SELECT
                id,
                name,
                website
            FROM prospect_companies
            WHERE id=:id
              AND owner_id=:owner
            """
        ),
        {
            'id': company_id,
            'owner': owner_id,
        },
    ).mappings().first()

    if not company:
        raise ValueError(
            'company not found'
        )

    if not company['website']:
        raise ValueError(
            'website required in Stage A'
        )

    run_id = db.execute(
        text(
            """
            INSERT INTO prospect_runs
            (
                owner_id,
                company_id,
                status
            )
            VALUES
            (
                :owner,
                :company,
                'running'
            )
            RETURNING id
            """
        ),
        {
            'owner': owner_id,
            'company': company_id,
        },
    ).scalar_one()

    db.commit()

    try:

        result = crawl_website(
            company['website'],
            max_pages=max_pages,
        )

        for email, source_url in (
            result['emails'].items()
        ):
            save_contact(
                db,
                company_id,
                'email',
                email,
                source_url,
            )

        for phone, source_url in (
            result['phones'].items()
        ):
            save_contact(
                db,
                company_id,
                'phone',
                phone,
                source_url,
            )

        db.execute(
            text(
                """
                UPDATE prospect_companies
                SET
                    status='parsed',
                    updated_at=now()
                WHERE id=:id
                """
            ),
            {'id': company_id},
        )

        # Stage A uses only local parsing/network.
        # Therefore monetary cost is intentionally 0.
        db.execute(
            text(
                """
                UPDATE prospect_runs
                SET
                    status='done',
                    pages_requested=:pr,
                    pages_fetched=:pf,
                    bytes_downloaded=:bd,
                    emails_found=:emails,
                    phones_found=:phones,
                    network_requests=:nr,
                    total_cost_rub=0,
                    finished_at=now()
                WHERE id=:id
                """
            ),
            {
                'id': run_id,
                'pr': result[
                    'pages_requested'
                ],
                'pf': result[
                    'pages_fetched'
                ],
                'bd': result[
                    'bytes_downloaded'
                ],
                'emails': len(
                    result['emails']
                ),
                'phones': len(
                    result['phones']
                ),
                'nr': result[
                    'network_requests'
                ],
            },
        )

        db.commit()

        result['run_id'] = int(run_id)

        return result

    except Exception as exc:

        db.rollback()

        db.execute(
            text(
                """
                UPDATE prospect_runs
                SET
                    status='failed',
                    error=:error,
                    finished_at=now()
                WHERE id=:id
                """
            ),
            {
                'id': run_id,
                'error': (
                    type(exc).__name__
                    + ': '
                    + str(exc)[:1000]
                ),
            },
        )

        db.execute(
            text(
                """
                UPDATE prospect_companies
                SET
                    status='error',
                    updated_at=now()
                WHERE id=:id
                """
            ),
            {'id': company_id},
        )

        db.commit()

        raise


###############################################################################
# STAGE B.3 — CONTACT QUALITY POSTPROCESSOR
###############################################################################

def _legacy_contact_quality_score_unused(
    kind,
    value,
    company_domain,
    source_url,
):

    score = 0

    value = (
        value
        or ""
    ).lower()

    source_url = (
        source_url
        or ""
    ).lower()

    company_domain = (
        company_domain
        or ""
    ).lower()

    if "/contact" in source_url:
        score += 20

    if "/kontakt" in source_url:
        score += 20

    if "контакт" in source_url:
        score += 20

    if kind == "email":

        local = (
            value.split("@", 1)[0]
            if "@"
            in value
            else ""
        )

        domain = (
            value.split("@", 1)[1]
            if "@"
            in value
            else ""
        )

        if (
            company_domain
            and (
                domain == company_domain
                or domain.endswith(
                    "." + company_domain
                )
            )
        ):
            # A mailbox on the company's verified website domain is already a
            # strong ownership signal even when it was found on the homepage
            # instead of a /contacts URL. Keep the campaign threshold strict;
            # raise evidence quality rather than lowering the gate.
            score += 60

        preferred = (
            "sales",
            "info",
            "hello",
            "contact",
            "office",
            "order",
            "zakaz",
            "client",
            "clients",
            "marketing",
            "commercial",
        )

        if any(
            x in local
            for x in preferred
        ):
            score += 25

        bad = (
            "noreply",
            "no-reply",
            "support",
            "privacy",
            "security",
            "abuse",
            "webmaster",
            "admin",
        )

        if any(
            x in local
            for x in bad
        ):
            score -= 30

    elif kind == "phone":

        # tel: / explicit contact-area numbers have
        # already survived Stage B.2 extraction.
        score += 50

        if value.startswith(
            "+7800"
        ):
            score += 15

    return max(
        0,
        min(
            100,
            int(score),
        ),
    )


def _legacy_rank_company_contacts_unused(
    db,
    company_id,
):

    company = db.execute(
        text(
            """
            SELECT
                domain
            FROM prospect_companies
            WHERE id=:id
            """
        ),
        {
            "id": company_id,
        },
    ).mappings().first()

    if not company:
        return

    domain = (
        company["domain"]
        or ""
    )

    rows = db.execute(
        text(
            """
            SELECT
                id,
                kind,
                value,
                source_url
            FROM prospect_contacts
            WHERE company_id=:company
            """
        ),
        {
            "company": company_id,
        },
    ).mappings().all()

    scored = []

    for row in rows:

        score = contact_quality_score(
            row["kind"],
            row["value"],
            domain,
            row["source_url"],
        )

        status = (
            "high"
            if score >= 70
            else
            "medium"
            if score >= 45
            else
            "low"
        )

        db.execute(
            text(
                """
                UPDATE prospect_contacts
                SET
                    quality_score=:score,
                    quality_status=:status,
                    selected_for_outreach=false
                WHERE id=:id
                """
            ),
            {
                "score": score,
                "status": status,
                "id": row["id"],
            },
        )

        scored.append(
            (
                score,
                row["kind"],
                int(row["id"]),
            )
        )

    ###########################################################################
    # Select best contacts for future outreach.
    #
    # max:
    # 3 emails
    # 2 phones
    ###########################################################################

    for kind, limit in (
        ("email", 3),
        ("phone", 2),
    ):

        selected = sorted(
            (
                x
                for x in scored
                if x[1] == kind
            ),
            key=lambda x: (
                -x[0],
                x[2],
            ),
        )[:limit]

        for score, _, contact_id in selected:

            if score < 40:
                continue

            db.execute(
                text(
                    """
                    UPDATE prospect_contacts
                    SET selected_for_outreach=true
                    WHERE id=:id
                    """
                ),
                {
                    "id": contact_id,
                },
            )


def rerank_all_company_contacts(
    db,
    owner_id,
):

    ids = db.execute(
        text(
            """
            SELECT id
            FROM prospect_companies
            WHERE owner_id=:owner
            """
        ),
        {
            "owner": owner_id,
        },
    ).scalars().all()

    for company_id in ids:

        rank_company_contacts(
            db,
            int(company_id),
        )

    db.commit()

    return len(ids)


# BORIS_PROSPECT_CONTACT_RANKER_V1

def contact_quality_score(
    kind,
    value,
    company_domain,
    source_url,
):
    score = 0

    value = (value or "").lower()
    source_url = (source_url or "").lower()
    company_domain = (company_domain or "").lower().removeprefix("www.")
    try:
        source_host = (urlparse(source_url).hostname or "").lower().removeprefix("www.")
    except Exception:
        source_host = ""
    official_source = bool(company_domain and source_host and (
        source_host == company_domain
        or source_host.endswith("." + company_domain)
        or company_domain.endswith("." + source_host)
    ))

    contact_page = any(
        x in source_url
        for x in (
            "/contact",
            "/contacts",
            "/kontakt",
            "/kontakty",
            "контакт",
            "/about",
            "/company",
        )
    )

    if contact_page:
        score += 20

    if kind == "email":
        if "@" not in value:
            return 0

        local, domain = value.rsplit("@", 1)
        domain = domain.removeprefix("www.")

        if company_domain and (
            domain == company_domain
            or domain.endswith("." + company_domain)
            or company_domain.endswith("." + domain)
        ):
            score += 60
        elif official_source:
            # A public email explicitly published on the company's own website
            # is strong enough provenance for outreach even when the business
            # uses Yandex/Mail.ru/Gmail or another mail domain. Keep the global
            # campaign gate at 55; strengthen evidence instead of lowering it.
            score += 55

        good = (
            "sales",
            "sale",
            "info",
            "hello",
            "contact",
            "office",
            "order",
            "zakaz",
            "client",
            "marketing",
            "commercial",
            "commerce",
        )

        bad = (
            "noreply",
            "no-reply",
            "privacy",
            "security",
            "abuse",
            "webmaster",
            "postmaster",
        )

        if any(x in local for x in good):
            score += 25

        if any(x in local for x in bad):
            score -= 40

    elif kind == "phone":
        score += 45

        if value.startswith("+7800"):
            score += 15

        if contact_page:
            score += 10

    return max(
        0,
        min(100, int(score)),
    )


def rank_company_contacts(db, company_id):

    company = db.execute(
        text(
            """
            SELECT domain
            FROM prospect_companies
            WHERE id=:id
            """
        ),
        {"id": company_id},
    ).mappings().first()

    if not company:
        return {
            "emails": 0,
            "phones": 0,
        }

    domain = company["domain"] or ""

    rows = db.execute(
        text(
            """
            SELECT
                id,
                kind,
                value,
                source_url
            FROM prospect_contacts
            WHERE company_id=:company
            """
        ),
        {"company": company_id},
    ).mappings().all()

    scored = []

    for row in rows:

        score = contact_quality_score(
            row["kind"],
            row["value"],
            domain,
            row["source_url"],
        )

        if score >= 70:
            status = "high"
        elif score >= 45:
            status = "medium"
        else:
            status = "low"

        db.execute(
            text(
                """
                UPDATE prospect_contacts
                SET
                    quality_score=:score,
                    quality_status=:status,
                    selected_for_outreach=false
                WHERE id=:id
                """
            ),
            {
                "score": score,
                "status": status,
                "id": row["id"],
            },
        )

        scored.append(
            {
                "id": int(row["id"]),
                "kind": row["kind"],
                "score": score,
            }
        )

    selected_counts = {
        "email": 0,
        "phone": 0,
    }

    limits = {
        "email": 3,
        "phone": 2,
    }

    for kind in ("email", "phone"):

        candidates = sorted(
            (
                x
                for x in scored
                if x["kind"] == kind
                and x["score"] >= 40
            ),
            key=lambda x: (
                -x["score"],
                x["id"],
            ),
        )

        for item in candidates[:limits[kind]]:

            db.execute(
                text(
                    """
                    UPDATE prospect_contacts
                    SET selected_for_outreach=true
                    WHERE id=:id
                    """
                ),
                {"id": item["id"]},
            )

            selected_counts[kind] += 1

    return {
        "emails": selected_counts["email"],
        "phones": selected_counts["phone"],
    }


def rerank_owner_contacts(
    db,
    owner_id,
):

    ids = db.execute(
        text(
            """
            SELECT id
            FROM prospect_companies
            WHERE owner_id=:owner
            """
        ),
        {"owner": owner_id},
    ).scalars().all()

    count = 0

    for company_id in ids:
        rank_company_contacts(
            db,
            int(company_id),
        )
        count += 1

    db.commit()

    return count
