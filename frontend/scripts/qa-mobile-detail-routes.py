#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only mobile QA for BORIS dynamic CRM and campaign detail routes."""
from __future__ import annotations
import json, sys
sys.path.insert(0, "/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
VIEWPORTS=[(320,760,"phone-320"),(390,844,"phone-390")]

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(450)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2200)
    if "/login" in page.url:
        raise RuntimeError("QA login failed")
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def metrics(page):
    return page.evaluate("""() => {
      const root=document.documentElement;
      const controls=[...document.querySelectorAll('button,input,select,textarea')].filter(el=>{
        const s=getComputedStyle(el),r=el.getBoundingClientRect();
        return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0&&r.right>0&&r.left<innerWidth;
      }).map(el=>{const r=el.getBoundingClientRect();return {tag:el.tagName,w:Math.round(r.width),h:Math.round(r.height),text:(el.getAttribute('aria-label')||el.textContent||el.getAttribute('placeholder')||'').trim().slice(0,80)}})
        .filter(x=>x.text&&(x.w<44||x.h<44)).slice(0,12);
      return {path:location.pathname,width:innerWidth,scrollWidth:root.scrollWidth,overflowX:root.scrollWidth>innerWidth+1,bodyText:(document.body.innerText||'').trim().length,tinyControls:controls};
    }""")

def discover(page):
    contact=None
    deal=None

    page.goto(WEB+"/dashboard/crm?tab=contacts",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1200)
    cards=page.locator(".boris-crm-contact-tile")
    if cards.count():
        before=page.url
        cards.first.click()
        page.wait_for_timeout(700)
        if "/dashboard/crm/contact/" in page.url:
            contact=page.url.replace(WEB,"").split("?")[0]
        page.goto(before,wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(500)

    page.goto(WEB+"/dashboard/crm?tab=kanban",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1200)
    deals=page.locator(".boris-crm-deal-row")
    if deals.count():
        before=page.url
        deals.first.click()
        page.wait_for_timeout(700)
        if "/dashboard/crm/deal/" in page.url:
            deal=page.url.replace(WEB,"").split("?")[0]
        page.goto(before,wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(500)
    return contact,deal

def main():
    out={"status":"PASS","routes":{},"failures":[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for width,height,label in VIEWPORTS:
            ctx=browser.new_context(viewport={"width":width,"height":height})
            page=ctx.new_page()
            login(page)
            contact,deal=discover(page)
            routes=[
                "/dashboard/crm",
                "/dashboard/crm?tab=contacts",
                "/dashboard/crm?tab=kanban",
                "/dashboard/crm?tab=tasks",
                "/dashboard/crm?tab=calendar",
            ]
            if contact: routes.append(contact)
            if deal: routes.append(deal)
            routes.append("/dashboard/campaign/282")
            out["routes"][label]={}
            for route in routes:
                errs=[]
                responses=[]
                page.on("response",lambda r,arr=responses: arr.append((r.status,r.url)) if r.status>=500 else None)
                page.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(1100)
                m=metrics(page)
                if m["overflowX"]: errs.append(f"overflow:{m['scrollWidth']}>{m['width']}")
                if m["bodyText"]<100: errs.append(f"empty:{m['bodyText']}")
                if m["tinyControls"]: errs.append(f"tiny-controls:{len(m['tinyControls'])}")
                if responses: errs.append("http5xx:"+str(responses[:3]))
                out["routes"][label][route]={"metrics":m,"problems":errs}
                if errs: out["failures"].append({"viewport":label,"route":route,"problems":errs})
            ctx.close()
        browser.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
