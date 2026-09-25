from __future__ import annotations

import os
from typing import Any

import requests

API = "https://api.webmaster.yandex.net/v4"
API41 = "https://api.webmaster.yandex.net/v4.1"
SITE_URL = "https://boris-ai.pro"
SITEMAP_URL = "https://boris-ai.pro/sitemap.xml"

class YandexWebmasterError(RuntimeError):
    pass

def _token() -> str:
    return (os.getenv("YANDEX_WEBMASTER_OAUTH_TOKEN") or "").strip()

def readiness() -> dict:
    token = _token()
    return {
        "site_url": SITE_URL,
        "sitemap_url": SITEMAP_URL,
        "oauth_configured": bool(token),
        "oauth_env": "YANDEX_WEBMASTER_OAUTH_TOKEN",
        "ready_for_api_calls": bool(token),
        "manual_checkpoint": None if token else "Создать Yandex OAuth приложение с правами webmaster:hostinfo и webmaster:verify и сохранить токен в окружении BORIS.",
    }

def _request(method: str, url: str, *, json_body: dict | None = None) -> Any:
    token = _token()
    if not token:
        raise YandexWebmasterError("YANDEX_WEBMASTER_OAUTH_TOKEN is not configured")
    r = requests.request(
        method,
        url,
        headers={"Authorization": f"OAuth {token}", "Content-Type": "application/json"},
        json=json_body,
        timeout=20,
    )
    if r.status_code >= 400:
        raise YandexWebmasterError(f"Yandex Webmaster API {r.status_code}: {r.text[:500]}")
    if not r.content:
        return {}
    try:
        return r.json()
    except Exception:
        return {"text": r.text}

def get_user() -> dict:
    return _request("GET", f"{API}/user")

def list_hosts(user_id: int) -> dict:
    return _request("GET", f"{API}/user/{user_id}/hosts")

def add_host(user_id: int, host_url: str = SITE_URL) -> dict:
    return _request("POST", f"{API}/user/{user_id}/hosts", json_body={"host_url": host_url})

def add_sitemap(user_id: int, host_id: str, sitemap_url: str = SITEMAP_URL) -> dict:
    return _request("POST", f"{API}/user/{user_id}/hosts/{host_id}/user-added-sitemaps", json_body={"url": sitemap_url})

def recrawl(user_id: int, host_id: str, url: str) -> dict:
    if not url.startswith(SITE_URL):
        raise YandexWebmasterError("recrawl URL must belong to boris-ai.pro")
    return _request("POST", f"{API}/user/{user_id}/hosts/{host_id}/recrawl/queue", json_body={"url": url})

def recrawl_quota(user_id: int, host_id: str) -> dict:
    return _request("GET", f"{API}/user/{user_id}/hosts/{host_id}/recrawl/quota")

def diagnostics(user_id: int, host_id: str) -> dict:
    return _request("GET", f"{API}/user/{user_id}/hosts/{host_id}/diagnostics")

def bootstrap() -> dict:
    user = get_user()
    user_id = int(user.get("user_id"))
    hosts = list_hosts(user_id)
    items = hosts.get("hosts", hosts.get("items", []))
    current = next((h for h in items if str(h.get("unicode_host_url") or h.get("ascii_host_url") or h.get("host_url") or "").rstrip("/") == SITE_URL), None)
    created = False
    if not current:
        current = add_host(user_id, SITE_URL)
        created = True
    host_id = current.get("host_id")
    return {"user_id": user_id, "host_id": host_id, "host_created": created, "host": current}
