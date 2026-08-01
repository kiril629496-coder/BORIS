"""Резидентный прокси-пул для обхода блокировок Avito (капча/фингерпринт)."""

import os
import random
import threading
from dotenv import load_dotenv
load_dotenv()

PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT_MIN = int(os.getenv("PROXY_PORT_MIN", 10000))
PROXY_PORT_MAX = int(os.getenv("PROXY_PORT_MAX", 10999))
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")


def get_random_proxy_url():
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        return None
    port = random.randint(PROXY_PORT_MIN, PROXY_PORT_MAX)
    return f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{port}"


def get_requests_proxies():
    url = get_random_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


def get_playwright_proxy():
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        return None
    port = random.randint(PROXY_PORT_MIN, PROXY_PORT_MAX)
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

_intl_host_lock = threading.Lock()
# Стартуем со случайного хоста, а дальше - строго по кругу: у старого кода был баг,
# когда прокси выбирался один раз до retry-цикла и повторные попытки долбились в тот
# же самый мёртвый IP. Теперь каждый вызов (= каждая retry-попытка) гарантированно
# отдаёт СЛЕДУЮЩИЙ хост из списка, а не тот же самый.
_intl_host_idx = [random.randrange(len(PROXY_INTL_HOSTS))] if PROXY_INTL_HOSTS else [0]


def _next_intl_host():
    with _intl_host_lock:
        host = PROXY_INTL_HOSTS[_intl_host_idx[0] % len(PROXY_INTL_HOSTS)]
        _intl_host_idx[0] += 1
        return host


def get_intl_requests_proxies():
    """Прокси для requests, дающий не-российский (США) IP — нужен для Pexels и других
    зарубежных API, заблокированных по гео на уровне Cloudflare."""
    if not all([PROXY_INTL_HOSTS, PROXY_INTL_USER, PROXY_INTL_PASS]):
        return None
    url = f"http://{PROXY_INTL_USER}:{PROXY_INTL_PASS}@{_next_intl_host()}"
    return {"http": url, "https": url}


def calc_intl_proxy_cost_rub(bytes_count: int) -> float:
    """Считает стоимость в рублях за скачанный через международный прокси объём трафика."""
    gb = bytes_count / (1024 ** 3)
    return round(gb * INTL_PROXY_COST_PER_GB_RUB, 4)


# ===== ПОИСК ЧИСТОГО ПОРТА (Avito банит ~94% IP пула, ~6% чистые) =====
import time as _time
_clean_port_cache = {"port": None, "ts": 0}
_CLEAN_TTL = 600  # 10 минут — держим найденный чистый порт, не перебираем каждый раз


async def find_clean_port(max_tries: int = 20):
    """Перебирает порты пула, возвращает первый, который Avito не банит. Кэширует на 10 мин."""
    global _clean_port_cache
    now = _time.time()
    if _clean_port_cache["port"] and (now - _clean_port_cache["ts"]) < _CLEAN_TTL:
        return _clean_port_cache["port"]
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        return None
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for _ in range(max_tries):
            port = random.randint(PROXY_PORT_MIN, PROXY_PORT_MAX)
            try:
                b = await pw.chromium.launch(headless=True, args=["--no-sandbox"],
                        proxy={"server": f"http://{PROXY_HOST}:{port}", "username": PROXY_USER, "password": PROXY_PASS})
                pg = await b.new_page()
                await pg.goto("https://www.avito.ru/autoload/documentation/templates/107132", timeout=22000, wait_until="domcontentloaded")
                t = (await pg.title()).lower()
                await b.close()
                if "ограничен" not in t and not ("доступ" in t and "проблема" in t):
                    _clean_port_cache = {"port": port, "ts": now}
                    return port
            except Exception:
                try:
                    await b.close()
                except Exception:
                    pass
                continue
    return None


async def get_clean_playwright_proxy():
    """Прокси-конфиг Playwright на ГАРАНТИРОВАННО чистом порту (или None если не нашли)."""
    port = await find_clean_port()
    if not port:
        return None
    return {"server": f"http://{PROXY_HOST}:{port}", "username": PROXY_USER, "password": PROXY_PASS}
