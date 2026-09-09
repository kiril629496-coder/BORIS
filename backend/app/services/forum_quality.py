from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = BACKEND_ROOT / "data" / "service_marketplaces" / "forum_quality_iks.json"
SOURCE_BASE = "https://webmaster.yandex.ru/siteinfo/?site="
UA = "Mozilla/5.0 (compatible; BORIS-ForumQuality/1.0; +https://boris-ai.pro)"
TIMEOUT = 20
HIGH_MIN = 1000
MEDIUM_MIN = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def domain_from_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else "//" + raw)
        return (parsed.hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def tier_for_iks(value: int | None) -> str:
    if not isinstance(value, int):
        return "unknown"
    if value >= HIGH_MIN:
        return "high"
    if value >= MEDIUM_MIN:
        return "medium"
    return "low"


def load() -> dict:
    if not DATA_FILE.exists():
        return {
            "measured_at": None,
            "source": SOURCE_BASE,
            "thresholds": {"high_min": HIGH_MIN, "medium_min": MEDIUM_MIN},
            "domains_total": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "unknown": 0,
            "items": [],
        }
    try:
        row = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return row if isinstance(row, dict) else {}
    except Exception:
        return {}


def by_domain() -> dict[str, dict]:
    return {
        str(x.get("domain") or ""): x
        for x in (load().get("items") or [])
        if x.get("domain")
    }


def quality_for_url(url: str | None) -> dict:
    domain = domain_from_url(url)
    row = by_domain().get(domain) or {}
    return {
        "domain": domain,
        "iks": row.get("iks"),
        "iks_tier": row.get("tier") or tier_for_iks(row.get("iks")),
        "iks_measured_at": load().get("measured_at"),
    }


def fetch_iks(domain: str) -> dict:
    domain = domain_from_url(domain)
    source = SOURCE_BASE + domain
    try:
        response = requests.get(
            source,
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        match = re.search(r'"sqi"\s*:\s*([0-9]+)', response.text)
        iks = int(match.group(1)) if match else None
        return {
            "domain": domain,
            "iks": iks,
            "tier": tier_for_iks(iks),
            "http": int(response.status_code),
            "source": source,
        }
    except Exception as exc:
        return {
            "domain": domain,
            "iks": None,
            "tier": "unknown",
            "source": source,
            "error": f"{type(exc).__name__}: {exc}",
        }


def save_rows(rows: list[dict], *, measured_at: str | None = None) -> dict:
    normalized = []
    seen = set()
    for row in rows:
        domain = domain_from_url(row.get("domain"))
        if not domain or domain in seen:
            continue
        seen.add(domain)
        iks = row.get("iks")
        if isinstance(iks, str) and iks.isdigit():
            iks = int(iks)
        normalized.append({
            **row,
            "domain": domain,
            "iks": iks if isinstance(iks, int) else None,
            "tier": tier_for_iks(iks if isinstance(iks, int) else None),
        })
    normalized.sort(key=lambda x: (-(x.get("iks") or -1), x["domain"]))
    payload = {
        "measured_at": measured_at or _now(),
        "source": SOURCE_BASE,
        "thresholds": {"high_min": HIGH_MIN, "medium_min": MEDIUM_MIN},
        "domains_total": len(normalized),
        "high": sum(1 for x in normalized if x["tier"] == "high"),
        "medium": sum(1 for x in normalized if x["tier"] == "medium"),
        "low": sum(1 for x in normalized if x["tier"] == "low"),
        "unknown": sum(1 for x in normalized if x["tier"] == "unknown"),
        "items": normalized,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)
    return payload


def refresh_domains(domains: list[dict], *, workers: int = 6) -> dict:
    import concurrent.futures

    unique: dict[str, set[str]] = {}
    for row in domains:
        domain = domain_from_url(row.get("domain") or row.get("url"))
        if not domain:
            continue
        unique.setdefault(domain, set()).update(
            str(x) for x in (row.get("platforms") or []) if x
        )
        if row.get("platform"):
            unique[domain].add(str(row["platform"]))

    def one(item):
        domain, platforms = item
        result = fetch_iks(domain)
        result["platforms"] = sorted(platforms)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as pool:
        rows = list(pool.map(one, unique.items()))
    return save_rows(rows)


def priority_for_url(url: str | None) -> tuple[int, int]:
    q = quality_for_url(url)
    tier = q.get("iks_tier")
    tier_score = {"high": 3, "medium": 2, "low": 1, "unknown": 0}.get(str(tier), 0)
    return tier_score, int(q.get("iks") or 0)
