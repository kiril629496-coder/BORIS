"""Резидентный прокси-пул для обхода блокировок Avito (капча/фингерпринт)."""

import asyncio
import os
import random
import threading
from dotenv import load_dotenv, dotenv_values
load_dotenv()

PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT_MIN = int(os.getenv("PROXY_PORT_MIN", 10000))
PROXY_PORT_MAX = int(os.getenv("PROXY_PORT_MAX", 10999))
PROXY_PORT = int(os.getenv("PROXY_PORT", "0") or 0)
PROXY_USER = os.getenv("PROXY_USER")


def _avito_candidate_ports() -> list[int]:
    """Return the actual configured Avito proxy endpoints.

    PROXY_PORT is authoritative for providers that expose one static endpoint;
    the legacy range is used only when no explicit port is configured.
    """
    if PROXY_PORT > 0:
        return [PROXY_PORT]
    return list(range(PROXY_PORT_MIN, PROXY_PORT_MAX + 1))
PROXY_PASS = os.getenv("PROXY_PASS")


def get_random_proxy_url():
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        return None
    port = random.choice(_avito_candidate_ports())
    return f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{port}"


def get_requests_proxies():
    url = get_random_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


def get_playwright_proxy():
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        return None
    port = random.choice(_avito_candidate_ports())
    return {
        "server": f"http://{PROXY_HOST}:{port}",
        "username": PROXY_USER,
        "password": PROXY_PASS,
    }


async def fetch_avito_page_with_retry(url: str, max_attempts: int = 5):
    """
    Пытается загрузить страницу Avito через разные IP из пула,
    пока не получит чистую страницу без капчи (или не исчерпает попытки.
    Возвращает (html, success: bool).
    """
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    stealth = Stealth()

    for attempt in range(1, max_attempts + 1):
        proxy = get_playwright_proxy()
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.goto(url, timeout=25000, wait_until="domcontentloaded")

                is_captcha = False
                try:
                    await page.wait_for_selector('[data-marker="item-title"]', timeout=8000)
                except Exception:
                    html_check = await page.content()
                    if "firewallCaptcha" in html_check or "geetest_captcha" in html_check:
                        is_captcha = True

                html = await page.content()
                await browser.close()

            if not is_captcha and html.count('data-marker="item-title"') > 0:
                print(f"[fetch_avito_page_with_retry] Успех на попытке {attempt} (порт {proxy['server'].split(':')[-1]})")
                return html, True
            else:
                print(f"[fetch_avito_page_with_retry] Попытка {attempt}: капча/пусто, пробуем другой IP")

        except Exception as e:
            print(f"[fetch_avito_page_with_retry] Попытка {attempt}: ошибка - {e}")

    return None, False


# --- Международный прокси (США) — для обхода гео-блокировки зарубежных API (Pexels и т.п.) ---
# Раньше был ротационный пул (1 хост + диапазон портов). Сейчас у провайдера только 2
# статических адреса (порт зашит в каждый) - PROXY_INTL_HOSTS хранит их как "host:port".
PROXY_INTL_HOSTS = [h.strip() for h in os.getenv("PROXY_INTL_HOSTS", "").split(",") if h.strip()]
PROXY_INTL_USER = os.getenv("PROXY_INTL_USER")
PROXY_INTL_PASS = os.getenv("PROXY_INTL_PASS")

INTL_PROXY_COST_PER_GB_RUB = 340.0

# INTERNATIONAL_PROXY_SECRET_HOT_RELOAD_V1:
# PROXY_INTL credentials are operational secrets and may rotate independently
# of an immutable backend release. Read only these three values from the
# root-owned runtime env file on each call; ordinary Avito PROXY_* remains
# process-static and unchanged.
_INTL_ENV_PATH = os.getenv("BORIS_RUNTIME_ENV_FILE", "/root/BORIS/backend/.env")
_intl_host_lock = threading.Lock()
_intl_host_idx = [random.randrange(len(PROXY_INTL_HOSTS))] if PROXY_INTL_HOSTS else [0]


def _current_intl_config():
    hosts = list(PROXY_INTL_HOSTS)
    user = PROXY_INTL_USER
    password = PROXY_INTL_PASS
    try:
        vals = dotenv_values(_INTL_ENV_PATH)
        raw_hosts = vals.get("PROXY_INTL_HOSTS")
        if raw_hosts is not None:
            parsed = [h.strip() for h in str(raw_hosts).split(",") if h.strip()]
            if parsed:
                hosts = parsed
        if vals.get("PROXY_INTL_USER") is not None:
            user = str(vals.get("PROXY_INTL_USER") or "").strip()
        if vals.get("PROXY_INTL_PASS") is not None:
            password = str(vals.get("PROXY_INTL_PASS") or "").strip()
    except (OSError, ValueError, TypeError):
        # Runtime env remains the safe fallback when the file is temporarily
        # unavailable; never log credential values.
        pass
    return hosts, user, password


def _next_intl_host(hosts=None):
    pool = list(hosts or PROXY_INTL_HOSTS)
    if not pool:
        return None
    with _intl_host_lock:
        host = pool[_intl_host_idx[0] % len(pool)]
        _intl_host_idx[0] += 1
        return host


def get_intl_requests_proxies():
    """HTTP(S) proxy for foreign APIs, with secret rotation without restart."""
    hosts, user, password = _current_intl_config()
    if not all([hosts, user, password]):
        return None
    host = _next_intl_host(hosts)
    if not host:
        return None
    url = f"http://{user}:{password}@{host}"
    return {"http": url, "https": url}


def calc_intl_proxy_cost_rub(bytes_count: int) -> float:
    """Считает стоимость в рублях за скачанный через международный прокси объём трафика."""
    gb = bytes_count / (1024 ** 3)
    return round(gb * INTL_PROXY_COST_PER_GB_RUB, 4)


# ===== VALIDATED AVITO ROUTE POOL =====
import time as _time
_clean_port_cache: dict[str, dict] = {}
_CLEAN_TTL = 600
_avito_proxy_health = {"state": "unknown", "checked_at": 0.0, "detail": None, "open_ports": 0, "tested_ports": 0}


def get_avito_proxy_health() -> dict:
    """Return a secret-free diagnostic snapshot for the Avito route pool."""
    return dict(_avito_proxy_health)


async def _tcp_port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def avito_proxy_port_reachable(port: int, timeout: float = 1.5) -> bool:
    """Check one pinned proxy port without touching Avito or exposing credentials."""
    if not PROXY_HOST:
        return False
    try:
        return await _tcp_port_open(PROXY_HOST, int(port), timeout=timeout)
    except Exception:
        return False


async def probe_avito_proxy_transport(sample_size: int = 8) -> dict:
    """Fast transport preflight before launching Chromium.

    If every sampled proxy port refuses TCP, there is no value in starting dozens
    of Playwright instances. This turns a multi-minute fake "Avito block" into a
    precise infrastructure state in a few seconds.
    """
    global _avito_proxy_health
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        _avito_proxy_health = {
            "state": "not_configured", "checked_at": _time.time(), "detail": "proxy_pool_not_configured",
            "open_ports": 0, "tested_ports": 0,
        }
        return dict(_avito_proxy_health)
    ports = _avito_candidate_ports()
    if not ports:
        _avito_proxy_health = {
            "state": "transport_down", "checked_at": _time.time(), "detail": "empty_port_range",
            "open_ports": 0, "tested_ports": 0,
        }
        return dict(_avito_proxy_health)
    sample = random.sample(ports, min(max(1, int(sample_size)), len(ports)))
    results = await asyncio.gather(*[_tcp_port_open(PROXY_HOST, p) for p in sample])
    open_count = sum(1 for ok in results if ok)
    _avito_proxy_health = {
        "state": "transport_ok" if open_count else "transport_down",
        "checked_at": _time.time(),
        "detail": None if open_count else "proxy_connection_refused_or_unreachable",
        "open_ports": open_count,
        "tested_ports": len(sample),
    }
    return dict(_avito_proxy_health)


async def find_clean_port(max_tries: int = 20, exclude_ports: set[int] | None = None, cache_key: str = "global", force_refresh: bool = False):
    """Return one Avito-usable proxy port with transport-aware diagnostics.

    Ports are sampled without replacement. ``cache_key`` keeps sticky validated
    routes independent between client accounts; ``exclude_ports`` prevents two
    active accounts from being assigned the same exit while capacity is available.
    """
    global _clean_port_cache, _avito_proxy_health
    now = _time.time()
    exclude_ports = {int(p) for p in (exclude_ports or set())}
    cache_key = str(cache_key or "global")[:160]
    cached = _clean_port_cache.get(cache_key) or {}
    if (not force_refresh and cached.get("port")
            and int(cached.get("port")) not in exclude_ports
            and (now - float(cached.get("ts") or 0)) < _CLEAN_TTL):
        return int(cached["port"])
    health = await probe_avito_proxy_transport(sample_size=min(8, max(2, int(max_tries))))
    if health.get("state") != "transport_ok":
        return None

    ports = [p for p in _avito_candidate_ports() if p not in exclude_ports]
    random.shuffle(ports)
    ports = ports[:min(max(1, int(max_tries)), len(ports))]
    from playwright.async_api import async_playwright
    blocked = auth_or_proxy = transport_errors = 0
    async with async_playwright() as pw:
        for port in ports:
            browser = None
            try:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox"],
                    proxy={"server": f"http://{PROXY_HOST}:{port}", "username": PROXY_USER, "password": PROXY_PASS},
                )
                page = await browser.new_page()
                response = await page.goto("https://www.avito.ru/", timeout=16000, wait_until="domcontentloaded")
                body = (await page.locator("body").inner_text(timeout=5000)).lower()
                title = (await page.title()).lower()
                status = response.status if response else 0
                bad_markers = ("доступ ограничен", "проблема с ip", "firewallcaptcha", "geetest_captcha")
                is_blocked = any(x in body or x in title for x in bad_markers)
                if status == 407:
                    auth_or_proxy += 1
                elif status and status < 500 and not is_blocked:
                    _clean_port_cache[cache_key] = {"port": port, "ts": _time.time()}
                    _avito_proxy_health = {
                        "state": "ready", "checked_at": _time.time(), "detail": None,
                        "open_ports": health.get("open_ports", 0), "tested_ports": health.get("tested_ports", 0),
                    }
                    return port
                elif is_blocked:
                    blocked += 1
                else:
                    transport_errors += 1
            except Exception as exc:
                msg = str(exc).lower()
                if "407" in msg or "proxy authentication" in msg:
                    auth_or_proxy += 1
                elif "proxy" in msg or "connection" in msg or "tunnel" in msg:
                    transport_errors += 1
                else:
                    blocked += 1
            finally:
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
    if auth_or_proxy and auth_or_proxy >= max(blocked, transport_errors):
        state, detail = "auth_failed", "proxy_authentication_failed"
    elif transport_errors and transport_errors >= blocked:
        state, detail = "transport_degraded", "proxy_transport_failed_during_validation"
    else:
        state, detail = "avito_blocked", "no_avito_usable_exit"
    _avito_proxy_health = {
        "state": state, "checked_at": _time.time(), "detail": detail,
        "open_ports": health.get("open_ports", 0), "tested_ports": health.get("tested_ports", 0),
        "blocked_attempts": blocked, "transport_errors": transport_errors, "auth_errors": auth_or_proxy,
    }
    return None


async def get_clean_playwright_proxy():
    """Прокси-конфиг Playwright на ГАРАНТИРОВАННО чистом порту (или None если не нашли)."""
    port = await find_clean_port()
    if not port:
        return None
    return {"server": f"http://{PROXY_HOST}:{port}", "username": PROXY_USER, "password": PROXY_PASS}
