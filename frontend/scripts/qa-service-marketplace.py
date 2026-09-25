#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production QA for Crowd SEO service marketplace inventory/warmup."""
import json, sys
sys.path.insert(0, "/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB = "https://boris-ai.pro"
ACCOUNT = "testref_47784"

def login(page):
    diag = {"login_http": None, "token_present": False, "after_url": None, "error_text": ""}
    page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(400)
    if "/login" in page.url:
        for attempt in range(2):
            page.locator("input[type=email]").fill(qa_suite.OWNER[0])
            page.locator("input[type=password]").fill(qa_suite.OWNER[1])
            try:
                with page.expect_response(
                    lambda r: "/api/auth/login" in r.url and r.request.method == "POST",
                    timeout=15000,
                ) as info:
                    page.get_by_text("Войти", exact=True).first.click()
                diag["login_http"] = info.value.status
            except Exception:
                diag["login_http"] = "no_response"
            try:
                page.wait_for_function(
                    "() => Boolean(localStorage.getItem('boris_token'))",
                    timeout=8000,
                )
            except Exception:
                pass
            if page.evaluate("Boolean(localStorage.getItem('boris_token'))"):
                break
            if attempt == 0:
                page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(700)
        diag["token_present"] = bool(page.evaluate("Boolean(localStorage.getItem('boris_token'))"))
        diag["after_url"] = page.url
        if not diag["token_present"]:
            try:
                diag["error_text"] = " ".join(
                    page.locator("body").inner_text(timeout=3000).split()
                )[:500]
            except Exception:
                pass
    else:
        diag["token_present"] = bool(page.evaluate("Boolean(localStorage.getItem('boris_token'))"))
        diag["after_url"] = page.url
    if diag["token_present"]:
        page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)", ACCOUNT)
    return diag

def api_json(page, path):
    return page.evaluate("""async (path) => {
      const token = localStorage.getItem('boris_token') || '';
      const r = await fetch(path, {
        credentials: 'include',
        cache: 'no-store',
        headers: token ? {Authorization: 'Bearer ' + token} : {}
      });
      let data = {};
      try { data = await r.json(); } catch (_) {}
      return {status:r.status, data};
    }""", path)

def main():
    out = {"status":"PASS","views":{},"failures":[],"console_errors":[]}
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label, width, height in [("desktop",1440,1000),("phone",320,760)]:
            ctx = br.new_context(viewport={"width":width,"height":height})
            page = ctx.new_page()
            page.on("console", lambda m: out["console_errors"].append(m.text) if m.type == "error" else None)
            auth = login(page)
            resp = page.goto(WEB + "/dashboard/prospecting/services", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1600)
            body = page.locator("body").inner_text()
            norm = " ".join(body.split())

            inv = api_json(page, "/api/prospecting/service-marketplace/inventory-summary")
            boot = api_json(page, "/api/prospecting/service-marketplace/bootstrap-queue")
            warm = api_json(page, "/api/prospecting/service-marketplace/warmup-queue")
            i = inv.get("data") or {}
            b = boot.get("data") or {}
            w = warm.get("data") or {}

            checks = {
                "http": resp.status if resp else None,
                "api_inventory": inv.get("status"),
                "api_bootstrap": boot.get("status"),
                "api_warmup": warm.get("status"),
                "inventory_title": "БАЗА САЙТОВ CROWD SEO" in norm,
                "seen_matches": f"Всего сайтов уже нашли / проверили {i.get('unique_sites_seen_or_checked', -1)}" in norm,
                "registry_matches": f"{i.get('unique_forum_sites', -1)} в рабочем реестре" in norm,
                "allowed_matches": f"Правила разрешают {i.get('rules_allowed', -1)}" in norm,
                "quality_title": "Высокий / средний ИКС" in norm,
                "quality_allowed_title": "Качественные и разрешённые" in norm,
                "quality_top_title": "Топ разрешённых площадок по ИКС" in norm,
                "quality_high_medium_matches": f"Высокий / средний ИКС {i.get('quality_high_medium_sites', -1)}" in norm,
                "quality_allowed_matches": f"Качественные и разрешённые {i.get('quality_allowed_high_medium_sites', -1)}" in norm,
                "ready_matches": f"Готовы прямо сейчас {i.get('publication_ready', -1)}" in norm,
                "bootstrap_matches": f"Разовая подготовка {i.get('bootstrap_queue', -1)}" in norm,
                "warming_matches": f"Сейчас прогреваются {w.get('warming', -1)}" in norm,
                "goods_services": "Где уже есть база для товаров и услуг клиентов" in norm,
                "goods_label": "Товары" in norm,
                "services_label": "Услуги" in norm,
                "worker_not_owner": "Одноразовая подготовка — не задача владельца" in norm,
                "captcha_no_bypass": "антибот-проверку он не обходит" in norm,
                "reusable": ("Готовый аккаунт переиспользуется для следующих клиентов" in norm or "Один прогретый аккаунт переиспользуется" in norm),
                "active_bootstrap_card_count": page.locator('[data-testid="active-bootstrap-card"]').count(),
                "reserve_bootstrap_block": page.locator('[data-testid="reserve-bootstrap"]').count(),
                "shell": page.locator('[data-testid="app-shell"]').count(),
                "overflowX": page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"),
            }
            out["views"][label] = {
                "auth": auth,
                "checks": checks,
                "inventory": {
                    "seen": i.get("unique_sites_seen_or_checked"),
                    "registry": i.get("unique_forum_sites"),
                    "allowed": i.get("rules_allowed"),
                    "ready": i.get("publication_ready"),
                    "bootstrap": i.get("bootstrap_queue"),
                    "warming": i.get("warming_accounts"),
                    "current_after_bootstrap": i.get("current_relevant_after_bootstrap"),
                    "current_after_warmup": i.get("current_relevant_after_warmup"),
                    "missing_after_bootstrap": i.get("current_missing_after_bootstrap"),
                    "missing_after_warmup": i.get("current_missing_after_warmup"),
                    "quality_domains_total": i.get("quality_domains_total"),
                    "quality_high": i.get("quality_high_sites"),
                    "quality_medium": i.get("quality_medium_sites"),
                    "quality_high_medium": i.get("quality_high_medium_sites"),
                    "quality_allowed_high_medium": i.get("quality_allowed_high_medium_sites"),
                },
                "bootstrap_needed": b.get("needed"),
                "warmup_count": w.get("warming"),
            }
            for key, value in checks.items():
                if key == "active_bootstrap_card_count":
                    expected = int(b.get("needed_for_active_projects") or 0)
                elif key == "reserve_bootstrap_block":
                    expected = 1 if int(b.get("needed") or 0) > int(b.get("needed_for_active_projects") or 0) else 0
                else:
                    expected = 200 if key in {"http","api_inventory","api_bootstrap","api_warmup"} else (1 if key == "shell" else False if key == "overflowX" else True)
                if value != expected:
                    out["failures"].append(f"{label}:{key}={value!r}, expected={expected!r}")
            ctx.close()
        br.close()
    if any("500" in x or "Internal Server Error" in x for x in out["console_errors"]):
        out["failures"].append("backend_5xx_console")
    if out["failures"]:
        out["status"] = "FAIL"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    raise SystemExit(0 if out["status"] == "PASS" else 2)

if __name__ == "__main__":
    main()
