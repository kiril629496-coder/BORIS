from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


def _safe_host(hostname: str) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local or
            ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            return False
    return True


def _validate_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("Ссылка пустая")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Поддерживаются только http/https ссылки")
    if parsed.username or parsed.password:
        raise ValueError("Ссылка с логином/паролем не поддерживается")
    if not parsed.hostname or not _safe_host(parsed.hostname):
        raise ValueError("Адрес недоступен для публичного аудита")
    return value


def _fetch_public_html(url: str, max_redirects: int = 3) -> tuple[str, str]:
    current = _validate_url(url)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; BORIS-Audit/1.0)"}
    for _ in range(max_redirects + 1):
        response = requests.get(
            current, headers=headers, timeout=10, allow_redirects=False,
            stream=True,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                raise ValueError("Некорректный redirect")
            current = _validate_url(urljoin(current, location))
            continue
        if response.status_code >= 400:
            raise ValueError(f"Источник ответил HTTP {response.status_code}")
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            raise ValueError("Источник не является HTML-страницей")
        raw = response.raw.read(1_000_001, decode_content=True)
        if len(raw) > 1_000_000:
            raise ValueError("Страница слишком большая для быстрой подсказки")
        encoding = response.encoding or "utf-8"
        return current, raw.decode(encoding, errors="replace")
    raise ValueError("Слишком много redirect")


def _clean_candidate(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" -—|·")
    for sep in (" — ", " – ", " | ", " :: "):
        if sep in text:
            parts = [x.strip() for x in text.split(sep) if x.strip()]
            if parts:
                text = max(parts, key=lambda x: min(len(x), 120))
    return text[:180]


def infer_source_hint(source_url: str) -> dict[str, Any]:
    final_url, html = _fetch_public_html(source_url)
    soup = BeautifulSoup(html, "lxml")
    title = _clean_candidate(soup.title.get_text(" ", strip=True) if soup.title else "")
    h1_node = soup.find("h1")
    h1 = _clean_candidate(h1_node.get_text(" ", strip=True) if h1_node else "")
    og = soup.find("meta", attrs={"property": "og:title"})
    og_title = _clean_candidate(og.get("content") if og else "")

    candidates = [x for x in (h1, og_title, title) if 3 <= len(x) <= 180]
    suggestion = candidates[0] if candidates else ""
    parsed = urlparse(final_url)
    host = (parsed.hostname or "").lower()
    category_slug = None
    if host.endswith("avito.ru"):
        parts = [x for x in parsed.path.split("/") if x]
        if len(parts) >= 2:
            category_slug = parts[1]

    return {
        "status": "ok" if suggestion else "partial",
        "source_url": final_url,
        "source_host": host,
        "suggested_query": suggestion or None,
        "category_slug": category_slug,
        "evidence": {
            "h1": h1 or None,
            "og_title": og_title or None,
            "title": title or None,
        },
        "message": None if suggestion else "Не удалось уверенно определить направление по странице",
    }
