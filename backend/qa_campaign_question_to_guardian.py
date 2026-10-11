# -*- coding: utf-8 -*-
import json,re,os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import qa_suite

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
ACCOUNT_NAME="test-ref"
BRIEF="Тестовая уборка, 1 объявление, цена от 1000 ₽."
OUT={}

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(600)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2500)
    return "/login" not in page.url

def open_overview(page):
    if not login(page):
        raise RuntimeError("login failed")
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)
    page.goto(WEB+"/dashboard/campaign",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1800)
    saved=page.evaluate("localStorage.getItem('boris_currentAccount')")
    body=page.inner_text("body")
    if saved!=ACCOUNT or ACCOUNT_NAME not in body:
        raise RuntimeError("safe QA account not selected: saved=%r body_has_name=%r"%(saved,ACCOUNT_NAME in body))

def common(page):
    return page.evaluate("""() => {
      const de=document.documentElement;
      const dock=[...document.querySelectorAll('nav,div')].find(x=>String(x.className||'').includes('mobileDock')&&!String(x.className||'').includes('mobileDockItem'));
      return {path:location.pathname,width:innerWidth,scrollWidth:de.scrollWidth,overflowX:de.scrollWidth>innerWidth+1,
        shellCount:document.querySelectorAll('[data-testid="app-shell"]').length,
        campaignScope:document.querySelectorAll('[data-boris-area="campaign"]').length,
        backCount:document.querySelectorAll('[data-testid="nav-back"]').length,
        dockDisplay:dock?getComputedStyle(dock).display:null};
    }""")

def create_campaign(page):
    page.locator("#boris-campaign-brief").fill(BRIEF)
    with page.expect_request(lambda r:r.url.endswith("/api/campaign/create") and r.method=="POST",timeout=15000) as req:
        page.get_by_role("button",name="Начать создание кампании").click()
    data=req.value.post_data_json or {}
    if data.get("account_id")!=ACCOUNT:
        raise RuntimeError("unsafe account in create request: %r"%data.get("account_id"))
    try:
        page.wait_for_url(re.compile(r".*/dashboard/campaign/[0-9]+$"),timeout=20000)
    except PWTimeout:
        pass
    page.wait_for_timeout(1200)
    m=re.search(r"/dashboard/campaign/([0-9]+)$",page.url)
    return int(m.group(1)) if m else None

def detail(page):
    q=page.evaluate("""() => {
      const row=document.querySelector('[data-testid="campaign-quantity-row"]');
      if(!row)return {present:false};
      const a=row.children[0]?.getBoundingClientRect(), b=row.children[1]?.getBoundingClientRect(),r=row.getBoundingClientRect();
      return {present:true,rowWidth:Math.round(r.width),inputWidth:a?Math.round(a.width):0,helpWidth:b?Math.round(b.width):0,
        helpBelow:!!(a&&b&&b.top>=a.bottom-1)};
    }""")
    b=page.evaluate("""() => {
      const x=[...document.querySelectorAll('button')].find(el=>/Подготовить кампанию|Продолжить подготовку/.test(el.textContent||''));
      if(!x)return null;
      const r=x.getBoundingClientRect();
      const dock=[...document.querySelectorAll('nav,div')].find(el=>String(el.className||'').includes('mobileDock')&&!String(el.className||'').includes('mobileDockItem'));
      const d=dock?.getBoundingClientRect();
      return {text:(x.textContent||'').trim(),disabled:x.disabled,overlapsDock:!!(d&&r.bottom>d.top&&r.top<d.bottom)};
    }""")
    return {"page":common(page),"quantity":q,"mainButton":b}

def question_to_guardian(page):
    blocked=[]
    def block_guardian(route):
        blocked.append(route.request.url)
        route.abort()
    page.route("**/api/campaign/**/guardian/prepare",block_guardian)
    btn=page.get_by_role("button",name=re.compile("Подготовить кампанию|Продолжить подготовку"))
    btn.click()
    page.wait_for_timeout(900)
    label=page.get_by_text("В каких городах размещать объявления?",exact=True)
    city=page.locator('input[placeholder^="Например: Санкт-Петербург"]')
    result={"questionVisible":label.is_visible() if label.count() else False}
    if city.count():
        city.fill("Москва")
        page.get_by_role("button",name="Сохранить уточнения").click()
        page.wait_for_timeout(2200)
    dialog=page.locator('[role="dialog"][aria-labelledby="feed-intake-title"]')
    result["intakeVisible"]=dialog.is_visible() if dialog.count() else False
    result["categoryOptions"]=page.locator('.boris-intake-category-options button').count()
    result["guardianAttemptBlocked"]=len(blocked)
    result["overflowX"]=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    result["buttonDisabled"]=btn.is_disabled() if btn.count() else None
    page.unroute("**/api/campaign/**/guardian/prepare",block_guardian)
    return result

os.makedirs(qa_suite.QA_DIR,exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for width,tag in ((1440,"desktop"),(390,"mobile")):
        ctx=browser.new_context(viewport={"width":width,"height":1000 if width>500 else 844})
        page=ctx.new_page()
        errs=[]
        page.on("pageerror",lambda e:errs.append("pageerror:"+str(e)))
        page.on("console",lambda m:errs.append("console:"+m.text) if m.type=="error" else None)
        open_overview(page)
        OUT[tag]={"overview":common(page)}
        cid=create_campaign(page)
        OUT[tag]["campaign_id"]=cid
        OUT[tag]["detail"]=detail(page)
        OUT[tag]["question_to_guardian"]=question_to_guardian(page)
        page.screenshot(path=os.path.join(qa_suite.QA_DIR,"campaign_core_%s_20260905.png"%tag),full_page=True)
        OUT[tag]["errors"]=errs[-20:]
        ctx.close()
    browser.close()
print(json.dumps(OUT,ensure_ascii=False,indent=2))
