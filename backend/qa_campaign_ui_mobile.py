# -*- coding: utf-8 -*-
import json, re, os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import qa_suite

WEB = "https://boris-ai.pro"
ACCOUNT = "qa_feed_test"
BRIEF = "QA проверка запуска. Сделай 1 объявление по услуге тестовой уборки в Москве. Цена от 1000 ₽."
OUT = {"campaign_id": None, "desktop": {}, "mobile": {}}

def login(page):
    page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(500)
    if "/login" not in page.url:
        return True
    page.locator("input[type=email]").fill(qa_suite.OWNER[0])
    page.locator("input[type=password]").fill(qa_suite.OWNER[1])
    page.get_by_text("Войти", exact=True).first.click()
    page.wait_for_timeout(3000)
    return "/login" not in page.url

def common(page):
    return page.evaluate("""() => ({
      path: location.pathname,
      width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      overflowX: document.documentElement.scrollWidth > innerWidth + 1,
      shellCount: document.querySelectorAll('[data-testid="app-shell"]').length,
      campaignScope: document.querySelectorAll('[data-boris-area="campaign"]').length,
      backCount: document.querySelectorAll('[data-testid="nav-back"]').length
    })""")

def quantity(page):
    return page.evaluate("""() => {
      const row=document.querySelector('[data-testid="campaign-quantity-row"]');
      if(!row) return {present:false};
      const a=row.children[0]?.getBoundingClientRect();
      const b=row.children[1]?.getBoundingClientRect();
      return {present:true,inputWidth:a?Math.round(a.width):0,helpWidth:b?Math.round(b.width):0,
        helpBelow:!!(a&&b&&b.top>=a.bottom-1)};
    }""")

def safe_prepare(page):
    hit=[]
    def block(route):
        hit.append(route.request.url)
        route.abort()
    page.route("**/api/campaign/**/guardian/prepare", block)
    btn=page.get_by_role("button", name=re.compile("Подготовить кампанию|Продолжить подготовку"))
    btn.scroll_into_view_if_needed()
    overlap=page.evaluate("""() => {
      const b=[...document.querySelectorAll('button')].find(x=>/Подготовить кампанию|Продолжить подготовку/.test(x.textContent||''))?.getBoundingClientRect();
      const d=[...document.querySelectorAll('div,nav')].find(x=>String(x.className||'').includes('mobileDock')&&!String(x.className||'').includes('mobileDockItem'))?.getBoundingClientRect();
      return !!(b&&d&&b.bottom>d.top&&b.top<d.bottom&&b.right>d.left&&b.left<d.right);
    }""")
    status=None
    try:
        with page.expect_response(lambda r: "/workflow/brief" in r.url and r.request.method=="POST", timeout=15000) as info:
            btn.click()
        status=info.value.status
    except PlaywrightTimeout:
        status="timeout"
    page.wait_for_timeout(1000)
    disabled=btn.is_disabled() if btn.count() else None
    page.unroute("**/api/campaign/**/guardian/prepare", block)
    return {"workflowBriefStatus":status,"buttonDisabledAfter":disabled,"guardianBlocked":bool(hit),"overlapsDock":overlap}

os.makedirs(qa_suite.QA_DIR, exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True, args=["--no-sandbox"])

    ctx=browser.new_context(viewport={"width":1440,"height":1000})
    page=ctx.new_page()
    errs=[]
    page.on("pageerror", lambda e: errs.append("pageerror:"+str(e)))
    page.on("console", lambda m: errs.append("console:"+m.text) if m.type=="error" else None)
    OUT["desktop"]["login"]=login(page)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)", ACCOUNT)
    page.goto(WEB+"/dashboard/campaign", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1600)
    OUT["desktop"]["overview"]=common(page)
    page.locator("#boris-campaign-brief").fill(BRIEF)
    with page.expect_response(lambda r: r.url.endswith("/api/campaign/create") and r.request.method=="POST", timeout=15000) as info:
        page.get_by_role("button", name="Начать создание кампании").click()
    OUT["desktop"]["createStatus"]=info.value.status
    try:
        page.wait_for_url(re.compile(r".*/dashboard/campaign/[0-9]+$"), timeout=20000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(1200)
    m=re.search(r"/dashboard/campaign/([0-9]+)$",page.url)
    OUT["campaign_id"]=int(m.group(1)) if m else None
    OUT["desktop"]["detail"]=common(page)
    OUT["desktop"]["quantity"]=quantity(page)
    if page.locator("textarea").count():
        page.locator("textarea").first.fill(BRIEF)
    OUT["desktop"]["prepare"]=safe_prepare(page)
    page.screenshot(path=os.path.join(qa_suite.QA_DIR,"campaign_desktop_20260905.png"), full_page=True)
    OUT["desktop"]["errors"]=errs[-15:]
    ctx.close()

    if OUT["campaign_id"]:
        ctx=browser.new_context(viewport={"width":390,"height":844})
        page=ctx.new_page()
        errs=[]
        page.on("pageerror", lambda e: errs.append("pageerror:"+str(e)))
        page.on("console", lambda m: errs.append("console:"+m.text) if m.type=="error" else None)
        OUT["mobile"]["login"]=login(page)
        page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)", ACCOUNT)
        page.goto(WEB+f"/dashboard/campaign/{OUT['campaign_id']}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        OUT["mobile"]["detail"]=common(page)
        OUT["mobile"]["quantity"]=quantity(page)
        if page.locator("textarea").count():
            page.locator("textarea").first.fill(BRIEF)
        OUT["mobile"]["prepare"]=safe_prepare(page)
        OUT["mobile"]["after"]=common(page)
        page.screenshot(path=os.path.join(qa_suite.QA_DIR,"campaign_mobile_20260905.png"), full_page=True)
        OUT["mobile"]["errors"]=errs[-15:]
        ctx.close()

    browser.close()

print(json.dumps(OUT,ensure_ascii=False,indent=2))
