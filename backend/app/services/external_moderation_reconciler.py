from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.services import service_marketplace as marketplace

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "service_marketplaces" / "external_moderation_watch.json"
UA = {"User-Agent": "BORIS-SEO-Moderation-Reconciler/1.0"}
DEFAULT_INTERVAL = timedelta(hours=6)
DEFAULT_TIMEOUT = 15

COMMON_PATHS = (
    "/tool/boris-ai/",
    "/tool/boris-ai",
    "/tools/boris-ai/",
    "/tools/boris-ai",
    "/ai-tool/boris-ai/",
    "/ai-tool/boris-ai",
    "/ai-tools/boris-ai/",
    "/ai-tools/boris-ai",
    "/boris-ai/",
    "/boris-ai",
)

PLATFORM_CANDIDATES = {
    "pubdisc_aoyii_com": ("https://www.aoyii.com/tool/boris-ai/",),
    "pubdisc_agenttool_sh": ("https://agenttool.sh/tools/boris-ai",),
    "pubdisc_b2bsaasmarket_com": ("https://b2bsaasmarket.com/tool/boris-ai",),
    "pubdisc_findaitools_app": ("https://findaitools.app/tools/boris-ai",),
    "pubdisc_toptrendy_org": ("https://toptrendy.org/tool/boris-ai/",),
    "pubdisc_myfreeaisource_com": (
        "https://myfreeaisource.com/ai-tool/boris-ai/",
        "https://myfreeaisource.com/tool/boris-ai/",
    ),
    "pubdisc_oneplaceforai_com": (
        "https://oneplaceforai.com/tool/boris-ai",
        "https://oneplaceforai.com/tools/boris-ai",
    ),
    "pubdisc_qwenai_site": (
        "https://qwenai.site/en/tools/boris-ai",
        "https://qwenai.site/en/tool/boris-ai",
    ),
    "pubdisc_reviewai_net": (
        "https://reviewai.net/tool/boris-ai/",
        "https://reviewai.net/tools/boris-ai/",
    ),
    "pubdisc_tooloradeals_com": (
        "https://tooloradeals.com/ai-tools/boris-ai",
        "https://tooloradeals.com/tools/boris-ai",
    ),
    "pubdisc_curatahub_com": (
        "https://curatahub.com/tool/boris-ai",
        "https://curatahub.com/tools/boris-ai",
    ),
    "pubdisc_aisotools_com": (
        "https://aisotools.com/tool/boris-ai",
        "https://aisotools.com/tools/boris-ai",
    ),
    "pubdisc_aisuperhub_io": (
        "https://aisuperhub.io/ai-tools/boris-ai",
        "https://www.aisuperhub.io/ai-tools/boris-ai",
    ),
    "pubdisc_autoventure_live": (
        "https://autoventure.live/tools/boris-ai",
        "https://autoventure.live/ai-tools/boris-ai",
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _norm(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _host(value: str | None) -> str:
    return (urlparse(str(value or "")).hostname or "").lower().removeprefix("www.")


def _load_state() -> dict:
    if not STATE.exists():
        return {"version": 1, "platforms": {}}
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"version": 1, "platforms": {}}
    except Exception:
        return {"version": 1, "platforms": {}}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now().isoformat()
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def _pending_latest() -> list[dict]:
    latest = {}
    for row in marketplace.list_attempts(limit=1000):
        platform = str(row.get("platform") or "").strip()
        if not platform or platform in latest:
            continue
        action = str(row.get("action") or "")
        if (
            action in {"guest_catalog_submit", "guest_catalog_email_submit", "guest_tool_submit", "human_listing_submit", "verify_publication"}
            or platform.startswith("guest_")
        ):
            latest[platform] = row
    out = []
    for platform, row in latest.items():
        status = str(row.get("status") or "")
        if not (status.startswith("submitted_pending") or status == "submitted"):
            continue
        meta = dict(row.get("meta") or {})
        target = str(meta.get("target_url") or "").strip()
        if not target.startswith(("http://", "https://")):
            continue
        out.append({
            "platform": platform,
            "row": row,
            "meta": meta,
            "target_url": target,
            "submitted_at": row.get("created_at"),
        })
    return out


def _robots_blocked(url: str, session: requests.Session) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=DEFAULT_TIMEOUT, headers=UA)
        if response.status_code != 200:
            return False
        path = parsed.path or "/"
        applies = False
        for raw in response.text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low.startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                applies = agent == "*"
                continue
            if applies and low.startswith("disallow:"):
                rule = line.split(":", 1)[1].strip()
                if rule and path.startswith(rule):
                    return True
    except Exception:
        return False
    return False


def _verify_page(url: str, target_url: str, session: requests.Session) -> dict:
    try:
        response = session.get(url, timeout=DEFAULT_TIMEOUT, headers=UA, allow_redirects=True)
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    if response.status_code != 200:
        return {"ok": False, "url": url, "http_status": response.status_code, "final_url": response.url}

    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        return {"ok": False, "url": url, "http_status": response.status_code, "error": f"parse:{exc}"}

    target_norm = _norm(target_url)
    target_host = _host(target_url)
    matched = None
    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, str(anchor.get("href") or "").strip())
        if _norm(href) == target_norm:
            matched = anchor
            break
        # Permit canonical scheme/www variants only when the path is the same.
        if _host(href) == target_host and (urlparse(href).path or "/").rstrip("/") == (urlparse(target_url).path or "/").rstrip("/"):
            matched = anchor
            break
    if matched is None:
        return {
            "ok": False,
            "url": url,
            "http_status": response.status_code,
            "final_url": response.url,
            "reason": "target_link_missing",
        }

    robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    robots_value = str(robots_meta.get("content") or "").lower() if robots_meta else ""
    x_robots = str(response.headers.get("X-Robots-Tag") or "").lower()
    blocked = _robots_blocked(response.url, session)
    indexable = "noindex" not in robots_value and "noindex" not in x_robots and not blocked
    rel = matched.get("rel")
    canonical = soup.find("link", rel=lambda x: x and "canonical" in x)
    return {
        "ok": bool(indexable),
        "publication_url": str(response.url),
        "http_status": response.status_code,
        "link_present": True,
        "href": urljoin(response.url, str(matched.get("href") or "").strip()),
        "rel": list(rel) if isinstance(rel, (list, tuple)) else rel,
        "indexable": bool(indexable),
        "canonical": canonical.get("href") if canonical else None,
        "title": soup.title.string.strip() if soup.title and soup.title.string else None,
        "reason": None if indexable else "publication_noindex_or_robots_blocked",
    }


def _json_search_candidates(platform: str, target_url: str, session: requests.Session) -> list[str]:
    """Use read-only public search APIs only to discover a listing URL."""
    probes = []
    if platform == "pubdisc_alieradox_com":
        probes.append(("https://alieradox.com/api/tools?search=BORIS", "tools"))
    elif platform == "pubdisc_clelp_ai":
        probes.append(("https://clelp.ai/api/skills?search=BORIS", "skills"))
    elif platform == "pubdisc_vibing_inc":
        probes.append(("https://vibing.inc/api/public/library/search?q=BORIS", "results"))
    elif platform == "pubdisc_qwenai_site":
        probes.append(("https://qwenai.site/api/tools?search=BORIS", "tools"))

    candidates = []
    for url, key in probes:
        try:
            response = session.get(url, timeout=DEFAULT_TIMEOUT, headers=UA)
            if response.status_code != 200:
                continue
            data = response.json()
        except Exception:
            continue
        if isinstance(data, dict):
            if key == "tools" and isinstance(data.get("data"), dict):
                rows = data["data"].get("tools") or []
            else:
                rows = data.get(key) or []
        else:
            rows = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or "").strip().casefold()
            website = str(item.get("url") or item.get("website") or item.get("website_url") or "").strip()
            if name != "boris ai".casefold() and _norm(website) != _norm(target_url):
                continue
            for field in ("public_url", "permalink", "listing_url"):
                value = str(item.get(field) or "").strip()
                if value.startswith(("http://", "https://")):
                    candidates.append(value)
            slug = str(item.get("slug") or "").strip()
            if slug:
                host = _host(url)
                base = f"https://{host}"
                for prefix in ("/tool/", "/tools/", "/skills/", "/ai-tools/"):
                    candidates.append(urljoin(base, prefix + slug))
    return candidates


def _candidate_urls(item: dict, session: requests.Session) -> list[str]:
    platform = item["platform"]
    meta = item["meta"]
    target = item["target_url"]
    domain = str(meta.get("domain") or "").strip().removeprefix("www.") or _host(item["row"].get("url"))
    values = list(PLATFORM_CANDIDATES.get(platform, ()))

    public_url = meta.get("public_url")
    if isinstance(public_url, str) and public_url.strip():
        base = f"https://{domain}/" if domain else target
        values.append(urljoin(base, public_url.strip()))

    values.extend(_json_search_candidates(platform, target, session))

    # Common deterministic slugs are cheap to check and harmless when absent.
    if domain:
        for path in COMMON_PATHS:
            values.append(f"https://{domain}{path}")
            values.append(f"https://www.{domain}{path}")

    out, seen = [], set()
    for value in values:
        value = str(value or "").strip()
        if not value.startswith(("http://", "https://")):
            continue
        key = value.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out[:14]


def run(*, max_checks: int = 5, interval: timedelta = DEFAULT_INTERVAL, force: bool = False) -> dict:
    now = _now()
    state = _load_state()
    platforms = state.setdefault("platforms", {})
    pending = _pending_latest()

    def due(item: dict) -> bool:
        row = platforms.get(item["platform"]) or {}
        checked = _iso(row.get("last_checked_at"))
        return bool(force or checked is None or now - checked >= interval)

    queue = [item for item in pending if due(item)]
    queue.sort(key=lambda item: (_iso(item.get("submitted_at")) or now, item["platform"]))
    queue = queue[:max(0, int(max_checks))]

    results = []
    session = requests.Session()
    for item in queue:
        platform = item["platform"]
        target = item["target_url"]
        candidates = _candidate_urls(item, session)
        checked = []
        verified = None
        for candidate in candidates:
            evidence = _verify_page(candidate, target, session)
            checked.append({
                "url": candidate,
                "http_status": evidence.get("http_status"),
                "reason": evidence.get("reason") or evidence.get("error"),
            })
            if evidence.get("ok"):
                verified = evidence
                break

        state_row = platforms.setdefault(platform, {})
        state_row.update({
            "last_checked_at": now.isoformat(),
            "target_url": target,
            "submitted_at": item.get("submitted_at"),
            "last_candidates": checked[-10:],
            "state": "verified" if verified else "pending",
        })

        if verified:
            publication = marketplace.upsert_verified_external_publication(
                platform=platform,
                url=str(verified["publication_url"]),
                target_url=target,
                title=verified.get("title"),
                verified_at=now.isoformat(),
                listing_url=str(verified["publication_url"]),
                verification={
                    "http_status": verified.get("http_status"),
                    "final_url": verified.get("publication_url"),
                    "link_present": True,
                    "indexable": True,
                    "rel": verified.get("rel"),
                    "canonical": verified.get("canonical"),
                    "counts_toward_new_direct_60": True,
                },
                source="external_moderation_reconciliation",
            )
            marketplace.record_attempt(
                platform=platform,
                action="verify_publication",
                status_value="verified",
                url=str(verified["publication_url"]),
                meta={
                    "target_url": target,
                    "publication_verified": True,
                    "link_present": True,
                    "indexable": True,
                    "rel": verified.get("rel"),
                    "counts_toward_new_direct_60": True,
                },
            )
            state_row["publication_url"] = verified["publication_url"]
            results.append({
                "platform": platform,
                "state": "verified",
                "publication_url": verified["publication_url"],
                "target_url": target,
                "publication": publication,
            })
        else:
            results.append({
                "platform": platform,
                "state": "pending",
                "target_url": target,
                "candidates_checked": len(candidates),
            })

    _save_state(state)
    return {
        "at": now.isoformat(),
        "pending_total": len(pending),
        "due_total": len([item for item in pending if due(item)]),
        "checked": len(queue),
        "verified": sum(1 for x in results if x["state"] == "verified"),
        "results": results,
        "state_file": str(STATE),
    }
