# -*- coding: utf-8 -*-
import json,re
from playwright.sync_api import sync_playwright
import qa_suite

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
CASES=[("desktop",1440,286),("mobile",390,287)]
OUT={}

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(500)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2200)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

with sync_playwright() as pw:
    br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for tag,width,cid in CASES:
        ctx=br.new_context(viewport={"width":width,"height":844 if width<500 else 1000})
        p=ctx.new_page()
        login(p)
        p.goto(WEB+f"/dashboard/campaign/{cid}",wait_until="domcontentloaded",timeout=30000)
        p.wait_for_timeout(1500)
        hits=[]
        def block(route):
            hits.append(route.request.url)
            route.abort()
        p.route("**/api/campaign/**/guardian/prepare",block)
        btn=p.get_by_role("button",name=re.compile("Подготовить кампанию|Продолжить подготовку"))
        before={"text":btn.inner_text(),"disabled":btn.is_disabled()}
        btn.click()
        p.wait_for_timeout(1800)
        after={"text":btn.inner_text(),"disabled":btn.is_disabled()}
        textbody=p.inner_text("body")
        OUT[tag]={"before":before,"after":after,"guardianAttemptBlocked":bool(hits),
                  "needsAnswer":"нужен один обязательный ответ" in textbody.lower(),
                  "overflowX":p.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")}
        p.unroute("**/api/campaign/**/guardian/prepare",block)
        ctx.close()
    br.close()
print(json.dumps(OUT,ensure_ascii=False,indent=2))
