#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only QA of existing CRM detail routes on real existing data.
Does not create, edit or complete any CRM entity.
"""
import json,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="prodazha_bytovok_25677"
ROUTES=["/dashboard/crm/contact/1494","/dashboard/crm/deal/1156"]

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(300)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(1600)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def main():
    out={"status":"PASS","viewports":{},"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for label,w,h in [("phone-320",320,760),("phone-390",390,844),("desktop-1440",1440,1000)]:
            ctx=br.new_context(viewport={"width":w,"height":h})
            p=ctx.new_page(); login(p)
            view={}
            for route in ROUTES:
                p.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
                p.wait_for_timeout(900)
                m=p.evaluate("""() => ({
                  path:location.pathname,
                  overflowX:document.documentElement.scrollWidth>innerWidth+1,
                  scrollWidth:document.documentElement.scrollWidth,
                  bodyLen:(document.body.innerText||'').length,
                  account:localStorage.getItem('boris_currentAccount'),
                  shell:document.querySelectorAll('[data-testid="app-shell"]').length,
                  back:document.querySelectorAll('[data-testid="nav-back"]').length,
                  dock:document.querySelectorAll('nav[aria-label="Быстрая навигация"]').length
                })""")
                probs=[]
                if m["overflowX"]: probs.append("horizontal-overflow")
                if m["account"]!=ACCOUNT: probs.append("account-state-lost")
                if m["shell"]!=1: probs.append("shell")
                if m["back"]!=1: probs.append("back")
                if w<=390 and m["dock"]!=1: probs.append("dock")
                if m["bodyLen"]<120: probs.append("empty")
                view[route]={"metrics":m,"problems":probs}
                if probs:
                    out["failures"].append({"viewport":label,"route":route,"problems":probs})
            out["viewports"][label]=view
            ctx.close()
        br.close()
    if out["failures"]:
        out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
