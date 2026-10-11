# -*- coding: utf-8 -*-
import json, re
from playwright.sync_api import sync_playwright
import qa_suite

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
CID=285
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
    ctx=br.new_context(viewport={"width":390,"height":844})
    p=ctx.new_page()
    login(p)
    p.goto(WEB+f"/dashboard/campaign/{CID}",wait_until="domcontentloaded",timeout=30000)
    p.wait_for_timeout(1600)
    dialog=p.locator('[role="dialog"][aria-labelledby="feed-intake-title"]')
    footer=p.locator('#feed-intake-review')
    continue_btn=footer.get_by_role("button",name="Продолжить")
    OUT["before"]={
      "dialogVisible":dialog.is_visible() if dialog.count() else False,
      "dialogRect":dialog.evaluate("(e)=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height}}") if dialog.count() else None,
      "footerVisible":footer.is_visible() if footer.count() else False,
      "footerRect":footer.evaluate("(e)=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height}}") if footer.count() else None,
      "continueVisible":continue_btn.is_visible() if continue_btn.count() else False,
      "requiredCards":p.locator('.boris-intake-field-card').count(),
      "categoryPickerVisible":p.locator('[data-testid="campaign-category-picker"]').is_visible() if p.locator('[data-testid="campaign-category-picker"]').count() else False,
      "categoryOptionCount":p.locator('.boris-intake-category-options button').count(),
      "categoryOptions":p.locator('.boris-intake-category-options button').all_inner_texts()[:8],
      "cityRow":p.locator('[data-testid="campaign-search-city-row"]').evaluate("(e)=>{const r=e.getBoundingClientRect();return {width:r.width,height:r.height}}") if p.locator('[data-testid="campaign-search-city-row"]').count() else None,
      "overflowX":p.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    }
    blocked=[]
    def block(route):
        blocked.append(route.request.url)
        route.abort()
    p.route("**/api/campaign/**/guardian/prepare",block)
    # Inspect-only pass: do not mutate category/answers on a production-shaped campaign.
    p.wait_for_timeout(300)
    OUT["after"]={
      "dialogVisible":dialog.is_visible() if dialog.count() else False,
      "continueDisabled":continue_btn.is_disabled() if continue_btn.count() else None,
      "warningVisible":"Чтобы продолжить, заполните" in p.inner_text("body"),
      "guardianBlocked":len(blocked),
      "overflowX":p.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    }
    p.unroute("**/api/campaign/**/guardian/prepare",block)
    ctx.close()
    br.close()
print(json.dumps(OUT,ensure_ascii=False,indent=2))
