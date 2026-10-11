from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.services import crowd_seo
from app.services import service_marketplace as marketplace

_USER_AGENT = "BORIS-SEO-Moderation-Reconciler/1.0"
_PDFCATALOG_PAGES = (
    "https://pdfcatalog.ru/",
    "https://pdfcatalog.ru/index.php?page=date",
)


def _norm_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


@lru_cache(maxsize=8)
def _fetch(url: str) -> tuple[int, str, str]:
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": _USER_AGENT},
        allow_redirects=True,
    )
    return int(response.status_code), str(response.url), response.text


def _pdfcatalog_publications() -> dict[str, str]:
    """Map exact submitted target URL -> public PDFCatalog article URL."""
    found: dict[str, str] = {}
    for source in _PDFCATALOG_PAGES:
        try:
            status, final_url, html = _fetch(source)
        except Exception:
            continue
        if not (200 <= status < 400):
            continue
        soup = BeautifulSoup(html, "html.parser")
        for block in soup.select("div.stdl, div.stdlt"):
            text = " ".join(block.stripped_strings)
            match = re.search(r"https?://[^\s]+", text)
            if not match:
                continue
            target = _norm_url(match.group(0).rstrip(".,);]"))
            if not target:
                continue
            article = block.find("a", href=True)
            if article is None:
                parent = block.parent
                article = parent.find("a", href=True) if parent is not None else None
            if article is None:
                continue
            public_url = urljoin(final_url, str(article.get("href") or "").strip())
            if public_url.startswith(("http://", "https://")):
                found.setdefault(target, public_url)
    return found


def reconcile_project(project_id: str) -> dict:
    """Resolve already-submitted moderated placements to public URLs.

    This never submits or re-submits anything. It only discovers an already
    public moderation result and then uses the canonical mark_posted/sync path.
    """
    project = crowd_seo.get_project(project_id)
    placements = list(project.get("placements") or [])
    pending = [
        row for row in placements
        if row.get("status") == "publication_pending_verification"
        and row.get("platform") == "pubdisc_pdfcatalog_ru"
        and row.get("marketplace_draft_id")
        and row.get("target_url")
    ]
    if not pending:
        return {"project_id": project_id, "checked": 0, "reconciled": 0, "items": []}

    catalog = _pdfcatalog_publications()
    reconciled = []
    for placement in pending:
        target = _norm_url(placement.get("target_url"))
        public_url = catalog.get(target)
        if not public_url:
            continue
        draft_id = str(placement.get("marketplace_draft_id") or "")
        try:
            publication = marketplace.mark_posted(draft_id, public_url)
        except Exception as exc:
            reconciled.append({
                "placement_id": placement.get("id"),
                "target_url": placement.get("target_url"),
                "publication_url": public_url,
                "status": "mark_posted_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        crowd_seo.sync_publications(project_id)
        reconciled.append({
            "placement_id": placement.get("id"),
            "target_url": placement.get("target_url"),
            "publication_url": public_url,
            "status": "published_unverified",
            "publication": publication,
        })
    return {
        "project_id": project_id,
        "checked": len(pending),
        "reconciled": sum(1 for x in reconciled if x.get("status") == "published_unverified"),
        "items": reconciled,
    }
