# -*- coding: utf-8 -*-
import json
from playwright.sync_api import sync_playwright
import qa_suite

WEB="https://boris-ai.pro"
OUT={}

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(500)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2200)

with sync_playwright() as pw:
    br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    ctx=br.new_context(viewport={"width":390,"height":844})
    p=ctx.new_page()
    login(p)
    blocked=[]
    def block_read(route):
        blocked.append(route.request.url)
        route.abort()
    p.route("**/api/inbox/mark_read",block_read)
    p.goto(WEB+"/messages",wait_until="domcontentloaded",timeout=30000)
    p.wait_for_timeout(2200)
    ws=p.locator('[data-testid="messages-workspace"]')
    before={
      "view":ws.get_attribute("data-mobile-view") if ws.count() else None,
      "dialogs":p.locator('[data-testid="message-dialog-card"]').count(),
      "threadVisible":p.locator('[data-testid="message-thread"]').is_visible() if p.locator('[data-testid="message-thread"]').count() else False,
      "overflowX":p.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    }
    clicked=False
    after_open={}
    after_back={}
    if before["dialogs"]>0:
        p.locator('[data-testid="message-dialog-card"]').first.click()
        clicked=True
        p.wait_for_timeout(1200)
        back=p.locator('[data-testid="message-thread-back"]')
        composer=p.locator('.boris-messages-composer')
        after_open={
          "view":ws.get_attribute("data-mobile-view"),
          "backVisible":back.is_visible() if back.count() else False,
          "threadVisible":p.locator('[data-testid="message-thread"]').is_visible(),
          "composerVisible":composer.is_visible() if composer.count() else False,
          "replyVisible":p.locator('[data-testid="message-reply-input"]').is_visible() if p.locator('[data-testid="message-reply-input"]').count() else False,
          "overflowX":p.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
        }
        if back.count():
            back.click()
            p.wait_for_timeout(500)
            after_back={
              "view":ws.get_attribute("data-mobile-view"),
              "dialogsVisible":p.locator('[data-testid="message-dialog-card"]').first.is_visible() if p.locator('[data-testid="message-dialog-card"]').count() else False,
              "threadVisible":p.locator('[data-testid="message-thread"]').is_visible() if p.locator('[data-testid="message-thread"]').count() else False
            }
    OUT={"before":before,"clicked":clicked,"afterOpen":after_open,"afterBack":after_back,"markReadBlocked":len(blocked)}
    p.unroute("**/api/inbox/mark_read",block_read)
    ctx.close()
    br.close()
print(json.dumps(OUT,ensure_ascii=False,indent=2))
