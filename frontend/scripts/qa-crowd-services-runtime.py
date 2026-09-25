#!/usr/bin/env python3
from __future__ import annotations
import json,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
EXPECTED={
    "seen490":"Всего сайтов уже нашли / проверили\n490",
    "registry398":"398 в рабочем реестре",
    "allowed62":"Правила разрешают\n62",
    "bootstrap39":"Разовая подготовка\n39",
    "current18":"Для текущего проекта\n18 / 18",
    "missing0":"Ещё найти\n0",
    "sellable":"Товары и услуги готовы к продаже",
    "services22":"Услуги\nМОЖНО ПРОДАВАТЬ\n22 / 18",
    "goods20":"Товары\nМОЖНО ПРОДАВАТЬ\n20 / 18",
    "warmup":"ПРОГРЕВ АККАУНТОВ",
}

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(250)
    if "/login" not in page.url:
        return
    page.locator("input[type=email]").fill(qa_suite.OWNER[0])
    page.locator("input[type=password]").fill(qa_suite.OWNER[1])
    page.get_by_text("Войти",exact=True).first.click()
    page.wait_for_timeout(1200)

def main():
    out={"status":"PASS","views":{},"failures":[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for label,w,h in [("desktop",1440,1000),("phone",320,760)]:
            ctx=browser.new_context(viewport={"width":w,"height":h})
            page=ctx.new_page()
            login(page)
            response=page.goto(WEB+"/dashboard/prospecting/services",wait_until="domcontentloaded",timeout=30000)
            page.wait_for_function("""() => Array.from(document.querySelectorAll('div')).some(el => {
              const own=(el.childNodes.length===1 ? (el.textContent||'').trim() : '');
              const parent=(el.parentElement?.innerText||'');
              return own==='490' && parent.includes('Всего сайтов уже нашли / проверили');
            })""",timeout=15000)
            body=page.locator("body").inner_text()
            checks={key:(text in body) for key,text in EXPECTED.items()}
            checks.update({
                "http": response.status if response else None,
                "overflowX": page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"),
                "shell": page.locator('[data-testid="app-shell"]').count(),
            })
            out["views"][label]=checks
            for key in EXPECTED:
                if checks[key] is not True:
                    out["failures"].append(f"{label}:{key}=false")
            if checks["http"]!=200: out["failures"].append(f"{label}:http={checks['http']}")
            if checks["overflowX"]: out["failures"].append(f"{label}:overflowX=true")
            if checks["shell"]!=1: out["failures"].append(f"{label}:shell={checks['shell']}")
            ctx.close()
        browser.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
