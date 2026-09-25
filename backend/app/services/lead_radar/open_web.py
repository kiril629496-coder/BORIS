"""Public-search source groups for Lead Radar.

Each connector is a real read-only source lane backed by Bing's public RSS
search response. Search results are filtered fail-closed by host/category
markers and require a provider timestamp; no result is treated as fresh merely
because BORIS discovered it now.

OPEN_WEB_SOURCE_GROUPS_V1
"""
from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import requests

from .connectors import SourceConnector, _owner, _parse_dt
from .coverage import validate_group
from .models import RawLead


BING_RSS_URL = "https://www.bing.com/search"

DEFAULT_OPEN_WEB_GROUPS = {
    "freelance": {
        "family": "freelance",
        "queries": [
            {"q": "FL.ru проекты разработка сайт приложение автоматизация",
             "hosts": ["fl.ru", "freelance.ru", "workspace.ru"],
             "any_terms": ["проект", "заказ", "разработ", "программ", "автоматизац", "website", "app"]},
        ],
    },
    "linkedin": {
        "family": "social",
        "queries": [
            {"q": "LinkedIn posts looking for developer software agency contract",
             "hosts": ["linkedin.com"],
             "any_terms": ["developer", "software", "agency", "contract", "hiring", "разработ"]},
        ],
    },
    "maps": {
        "family": "audit",
        "queries": [
            {"q": "site:yandex.ru/maps/ автосервис Москва",
             "hosts": ["yandex.ru"],
             "any_terms": ["автосервис", "сто", "отзыв"]},
            {"q": "site:yandex.ru/maps/ мебель Москва",
             "hosts": ["yandex.ru"],
             "any_terms": ["мебел", "отзыв", "организац"]},
        ],
    },
    "seo_agencies": {
        "family": "partner",
        "queries": [
            {"q": "SEO агентство разработка сайтов Москва",
             "exclude_hosts": ["vc.ru", "yandex.ru", "jsprav.ru", "ratingruneta.ru"],
             "required_groups": [
                 ["seo", "сео"],
                 ["агентств", "digital", "интернет-агентств"],
                 ["разработ", "создание сайтов", "поддержка сайтов", "web"],
             ]},
        ],
    },
    "design_studios": {
        "family": "partner",
        "queries": [
            {"q": "\"дизайн-студия\" \"разработка сайтов\" Москва",
             "exclude_hosts": ["vc.ru", "yandex.ru", "jsprav.ru", "2gis.ru"],
             "required_groups": [
                 ["дизайн", "студи", "studio"],
                 ["разработ", "создание сайтов", "web"],
             ]},
        ],
    },
    "new_companies": {
        "family": "startup",
        "queries": [
            {"q": "new startup launched software platform app 2026",
             "required_groups": [
                 ["startup", "стартап", "new company"],
                 ["launch", "launched", "запуск", "founded"],
                 ["software", "platform", "app", "saas", "прилож"],
             ]},
        ],
    },
    "franchises": {
        "family": "audit",
        "queries": [
            {"q": "franchise CRM mobile app automation digital platform",
             "required_groups": [
                 ["franchise", "франшиз"],
                 ["crm", "mobile app", "automation", "platform", "прилож", "автоматизац"],
             ]},
        ],
    },
    "news": {
        "family": "money",
        "queries": [
            {"q": "startup raised funding software platform app 2026",
             "required_groups": [
                 ["startup", "стартап", "company"],
                 ["raised", "funding", "seed", "series a", "series b", "investment", "инвестиц", "финансирован"],
                 ["software", "platform", "app", "saas", "прилож"],
             ]},
        ],
    },
    "social": {
        "family": "social",
        "queries": [
            {"q": "VK Reddit looking for developer need developer project",
             "hosts": ["vk.com", "vk.ru", "reddit.com"],
             "any_terms": ["developer", "разработ", "looking for", "need", "нужен", "ищу"]},
        ],
    },
    "reviews": {
        "family": "reviews",
        "queries": [
            {"q": "Trustpilot Otzovik CRM software app not working review",
             "hosts": ["trustpilot.com", "otzovik.com"],
             "required_groups": [
                 ["crm", "software", "app", "прилож", "сервис"],
                 ["review", "отзыв", "not working", "problem", "не работает", "ошибка"],
             ]},
        ],
    },
}

_BLOCKED_HOSTS = {
    "wikipedia.org", "wiktionary.org", "microsoft.com", "support.microsoft.com",
    "calculatorsoup.com", "omnicalculator.com",
}


def _host_matches(host: str, allowed: list[str]) -> bool:
    host=(host or "").lower().removeprefix("www.")
    return any(host==x or host.endswith("." + x) for x in allowed)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


class BingRSSSearchConnector(SourceConnector):
    """Timestamped public-web discovery. Read-only and fail-closed."""

    supports_outreach=False
    outreach_idempotent=False

    def __init__(
        self,
        key: str,
        group_key: str,
        family: str,
        queries: list[dict],
        owner_user_id: int,
        timeout: int = 15,
        max_results_per_query: int = 20,
        min_request_interval_seconds: float = 1.2,
    ):
        self.key=str(key)
        self.group_key=validate_group(group_key)
        self.family=str(family or "audit")
        self.owner=_owner(owner_user_id)
        self.queries=[dict(x) for x in (queries or []) if isinstance(x,dict) and str(x.get("q") or "").strip()]
        if not self.queries:
            raise ValueError("LEAD_RADAR_OPEN_WEB_QUERIES_REQUIRED")
        self.timeout=max(5,int(timeout))
        self.max_results=max(1,min(50,int(max_results_per_query)))
        self.min_request_interval=max(0.0,float(min_request_interval_seconds))
        self._last_request_at=None

    def _rss(self, query: str) -> bytes:
        """Fetch one public RSS search with bounded TLS/network self-heal.

        OPEN_WEB_TRANSPORT_RETRY_V1
        One transient TLS EOF/handshake failure must not mark a source broken.
        The first attempt keeps the lightweight stdlib path; retries use
        requests/urllib3 as an independent TLS transport.  All attempts remain
        read-only and bounded.
        """
        if self._last_request_at is not None and self.min_request_interval>0:
            wait=self.min_request_interval-(time.monotonic()-self._last_request_at)
            if wait>0:
                time.sleep(wait)
        url=BING_RSS_URL+"?"+urllib.parse.urlencode({
            "q": query,
            "format": "rss",
            "setlang": "en-us",
            "count": self.max_results,
        })
        headers={
            "User-Agent":"BORIS-Lead-Radar/1.0",
            "Accept":"application/rss+xml, application/xml;q=0.9, */*;q=0.1",
        }
        errors=[]
        try:
            for attempt in range(3):
                try:
                    if attempt == 0:
                        req=urllib.request.Request(url,headers=headers)
                        with urllib.request.urlopen(req,timeout=self.timeout) as response:
                            status=int(getattr(response,"status",200) or 200)
                            if status >= 400:
                                raise RuntimeError("BING_RSS_HTTP_"+str(status))
                            return response.read()
                    response=requests.get(
                        url,headers=headers,timeout=(5,self.timeout),
                        allow_redirects=True,
                    )
                    response.raise_for_status()
                    return response.content
                except Exception as exc:
                    errors.append(type(exc).__name__+":"+str(exc)[:220])
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
            raise RuntimeError("BING_RSS_FETCH_FAILED_AFTER_RETRY|"+"|".join(errors))
        finally:
            self._last_request_at=time.monotonic()

    def fetch(self, since):
        out=[]
        seen=set()
        for spec in self.queries:
            query=str(spec.get("q") or "").strip()
            allowed=[str(x).lower().removeprefix("www.") for x in (spec.get("hosts") or []) if str(x).strip()]
            excluded=[str(x).lower().removeprefix("www.") for x in (spec.get("exclude_hosts") or []) if str(x).strip()]
            any_terms=[str(x).lower() for x in (spec.get("any_terms") or []) if str(x).strip()]
            partner_terms=[str(x).lower() for x in (spec.get("partner_terms") or []) if str(x).strip()]
            required_groups=[
                [str(x).lower() for x in group if str(x).strip()]
                for group in (spec.get("required_groups") or [])
                if isinstance(group,(list,tuple))
            ]
            root=ET.fromstring(self._rss(query))
            for item_index,item in enumerate(root.findall(".//item")):
                if item_index >= self.max_results:
                    break
                def tx(name):
                    node=item.find(name)
                    return (node.text or "").strip() if node is not None and node.text else ""
                title=_clean(tx("title"))
                body=_clean(tx("description"))
                link=tx("link")
                if not link:
                    continue
                host=(urllib.parse.urlparse(link).hostname or "").lower().removeprefix("www.")
                if not host:
                    continue
                if any(host==x or host.endswith("."+x) for x in _BLOCKED_HOSTS):
                    continue
                if allowed and not _host_matches(host,allowed):
                    continue
                if excluded and _host_matches(host,excluded):
                    continue
                published=_parse_dt(tx("pubDate"))
                if published is None or (since and published < since):
                    continue
                hay=(title+" "+body).lower()
                if any_terms and not any(term in hay for term in any_terms):
                    continue
                if partner_terms and not any(term in hay for term in partner_terms):
                    continue
                if required_groups and any(not any(term in hay for term in group) for group in required_groups):
                    continue
                stable=hashlib.sha256((self.key+"|"+link+"|"+title).encode("utf-8")).hexdigest()
                if stable in seen:
                    continue
                seen.add(stable)
                out.append(RawLead(
                    source=self.key,
                    external_id=stable,
                    url=link,
                    title=title,
                    text=body,
                    published_at=published,
                    source_family=self.family,
                    company_domain=host,
                    owner_user_id=self.owner,
                    metadata={
                        "connector":"bing_public_rss_search",
                        "source_group":self.group_key,
                        "search_query":query,
                        "provider_timestamp":tx("pubDate"),
                        "provider_timestamp_kind":"search_index_pubDate",
                        "read_only":True,
                    },
                ))
        return out
