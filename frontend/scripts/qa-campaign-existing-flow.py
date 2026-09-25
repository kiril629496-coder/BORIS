#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe UI flow on existing QA campaigns. Guardian prepare is blocked."""
import json,re,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
CASES=[("desktop",1440,1000,286),("phone-320",320,760,287)]

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(400)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2200)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def run(page,cid):
    page.goto(WEB+f"/dashboard/campaign/{cid}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1100)
    hits=[]
    def block(route):
        hits.append(route.request.url)
        route.abort()
    page.route("**/api/campaign/**/guardian/prepare",block)
    page.wait_for_timeout(1200)
    btn=page.get_by_role("button",name=re.compile("Подготовить кампанию|Продолжить подготовку"))
    label=page.get_by_text("В каких городах размещать объявления?",exact=True)
    city=page.locator('input[placeholder^="Например: Санкт-Петербург"]')
    saved=False
    # The current QA fixtures are intentionally parked on the mandatory-city
    # question. Exercise the real user path directly; saving the answer triggers
    # the normal continuation automatically. The guardian call is intercepted
    # above, so this QA cannot create/publish/charge anything.
    if label.count() and label.is_visible() and city.count():
        city.fill("Москва")
        save=page.get_by_role("button",name="Сохранить уточнения")
        save.click()
        saved=True
        page.wait_for_timeout(2200)
    elif btn.count():
        btn.click()
        page.wait_for_timeout(900)
    dialog=page.locator('[role="dialog"][aria-labelledby="feed-intake-title"]')
    result={
      "savedCity":saved,
      "guardianAttemptBlocked":len(hits)>0,
      "intakeVisible":dialog.is_visible() if dialog.count() else False,
      "categoryOptions":page.locator('.boris-intake-category-options button').count(),
      "overflowX":page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1"),
      "continueButtonPresent":btn.count()>0,
      "continueButtonDisabled":btn.is_disabled() if btn.count() else None,
      "bodyHasStuckBusy":"Подготавливаю…" in page.inner_text("body")
    }
    page.unroute("**/api/campaign/**/guardian/prepare",block)
    return result

def main():
    out={}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for label,w,h,cid in CASES:
            ctx=br.new_context(viewport={"width":w,"height":h})
            p=ctx.new_page(); login(p)
            out[label]={"campaign_id":cid,**run(p,cid)}
            ctx.close()
        br.close()
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
