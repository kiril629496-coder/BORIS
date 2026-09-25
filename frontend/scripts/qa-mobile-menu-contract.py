#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only mobile navigation contract for BORIS web UI."""
from __future__ import annotations
import json, sys
sys.path.insert(0, "/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
QA_ACCOUNT="testref_47784"
ROUTES=[
"/dashboard/command-center","/journal","/dashboard/factory-decisions",
"/dashboard/connect-avito","/dashboard/scenarios","/command","/messages",
"/dashboard/campaign","/dashboard/analytics","/dashboard/revenue-analytics",
"/dashboard/phone","/dashboard/work-mode","/dashboard/marketing",
"/dashboard/director","/dashboard/prospecting/manage","/dashboard/crm","/social",
"/dashboard/video-factory","/dashboard/sitebuild","/memory","/ai",
"/dashboard/ux-tester","/dashboard/control-plane","/dashboard/reliability",
"/team","/dashboard/billing","/dashboard/company","/messages/setup"
]
READ_ONLY_NEIGHBOR={"/dashboard/phone":"telephony_core"}

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
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",QA_ACCOUNT)

def metrics(page):
    return page.evaluate("""() => {
      const root=document.documentElement;
      const dock=[...document.querySelectorAll('nav')].find(
        n=>(n.getAttribute('aria-label')||'')==='Быстрая навигация'
      );
      const dr=dock?.getBoundingClientRect();
      const body=(document.body.innerText||'').trim();
      return {
        path:location.pathname,
        bodyText:body.length,
        overflowX:root.scrollWidth>innerWidth+1,
        scrollWidth:root.scrollWidth,
        shellCount:document.querySelectorAll('[data-testid="app-shell"]').length,
        backCount:document.querySelectorAll('[data-testid="nav-back"]').length,
        account:localStorage.getItem('boris_currentAccount'),
        dock:dr?{w:Math.round(dr.width),h:Math.round(dr.height)}:null,
        looks404:/страница не найдена|page not found|\b404\b/i.test(body.slice(0,2600))
      };
    }""")

def check_route(page,route):
    errors=[]
    responses=[]
    page.on("response",lambda r,arr=responses: arr.append((r.status,r.url)) if r.status>=500 else None)
    page.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(850)
    m=metrics(page)
    if "/login" in page.url: errors.append("redirect-login")
    if m["looks404"]: errors.append("404-content")
    if m["bodyText"]<80: errors.append("empty")
    if m["overflowX"]: errors.append(f"overflow:{m['scrollWidth']}")
    if m["shellCount"]!=1: errors.append(f"shell:{m['shellCount']}")
    if m["backCount"]!=1: errors.append(f"back:{m['backCount']}")
    if m["dock"] is None: errors.append("mobile-dock-missing")
    if m["account"]!=QA_ACCOUNT: errors.append("account-state-lost")
    page_5xx=[x for x in responses if "/api/" not in x[1]]
    api_5xx=[x for x in responses if "/api/" in x[1]]
    if page_5xx: errors.append("page-http5xx:"+str(page_5xx[:3]))
    m["backend5xx"]=api_5xx[:5]
    return m,errors

def sidebar_contract(page):
    page.goto(WEB+"/dashboard/command-center",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(700)
    menu=page.get_by_role("button",name="Меню",exact=True)
    if not menu.count():
        return {"problems":["menu-button-missing"]}
    menu.click()
    page.wait_for_timeout(250)
    out=page.evaluate("""() => {
      const side=document.querySelector('[data-testid="boris-sidebar"]');
      const close=side?.querySelector('button[aria-label="Закрыть меню"]');
      const sr=side?.getBoundingClientRect(), cr=close?.getBoundingClientRect();
      const nav=[...side?.querySelectorAll('nav a[href]')||[]].map(a=>({
        href:a.getAttribute('href'),
        text:(a.textContent||'').trim().replace(/\\s+/g,' ')
      }));
      const hrefs=nav.map(x=>x.href), texts=nav.map(x=>x.text);
      return {
        side:sr?{left:Math.round(sr.left),right:Math.round(sr.right),w:Math.round(sr.width)}:null,
        close:cr?{w:Math.round(cr.width),h:Math.round(cr.height)}:null,
        navCount:nav.length,
        duplicateHrefs:[...new Set(hrefs.filter((x,i)=>hrefs.indexOf(x)!==i))],
        duplicateTexts:[...new Set(texts.filter((x,i)=>x&&texts.indexOf(x)!==i))]
      };
    }""")
    problems=[]
    width=page.viewport_size["width"]
    if not out["side"] or out["side"]["left"]<0 or out["side"]["right"]>width+1:
        problems.append("sidebar-offscreen")
    if not out["close"] or out["close"]["w"]<44 or out["close"]["h"]<44:
        problems.append("close-touch-target")
    if out["navCount"]<20:
        problems.append("menu-incomplete")
    if out["duplicateHrefs"]:
        problems.append("duplicate-hrefs")
    if out["duplicateTexts"]:
        problems.append("duplicate-labels")
    page.get_by_role("button",name="Закрыть меню").first.click()
    page.wait_for_timeout(200)
    out["problems"]=problems
    return out

def history_contract(page):
    page.goto(WEB+"/dashboard/command-center",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(650)
    before=page.evaluate("localStorage.getItem('boris_currentAccount')")
    link=page.locator('nav[aria-label="Быстрая навигация"] a[href="/dashboard/campaign"]').first
    if not link.count():
        return {"problems":["campaign-link-missing"]}
    link.click()
    page.wait_for_timeout(500)
    forward=page.url
    page.go_back(wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(450)
    after=page.evaluate("localStorage.getItem('boris_currentAccount')")
    problems=[]
    if "/dashboard/campaign" not in forward:
        problems.append("forward-route")
    if page.url.split("?")[0] != WEB+"/dashboard/command-center":
        problems.append("back-route")
    if before!=QA_ACCOUNT or after!=QA_ACCOUNT:
        problems.append("account-state-lost")
    return {"forward":forward.replace(WEB,""),"back":page.url.replace(WEB,""),"account":after,"problems":problems}

def main():
    out={"status":"PASS","scope":"frontend_mobile_ui","delegated_read_only":READ_ONLY_NEIGHBOR,"viewports":{},"failures":[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for width,height,label in [(320,760,"phone-320"),(390,844,"phone-390")]:
            ctx=browser.new_context(viewport={"width":width,"height":height})
            page=ctx.new_page()
            login(page)
            current={"sidebar":sidebar_contract(page),"history":history_contract(page),"dock":dock_contract(page),"routes":{}}
            if current["sidebar"]["problems"]:
                out["failures"].append({"viewport":label,"route":"sidebar","problems":current["sidebar"]["problems"]})
            if current["history"]["problems"]:
                out["failures"].append({"viewport":label,"route":"history","problems":current["history"]["problems"]})
            if current["dock"]["problems"]:
                out["failures"].append({"viewport":label,"route":"dock","problems":current["dock"]["problems"]})
            for route in ROUTES:
                m,problems=check_route(page,route)
                owner=READ_ONLY_NEIGHBOR.get(route,"frontend_mobile_ui")
                current["routes"][route]={"metrics":m,"problems":problems,"owner":owner}
                if problems:
                    out["failures"].append({"viewport":label,"route":route,"owner":owner,"problems":problems})
            out["viewports"][label]=current
            ctx.close()
        browser.close()
    if out["failures"]:
        out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

def dock_contract(page):
    page.goto(WEB+"/dashboard/command-center",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(600)
    out=page.evaluate("""() => {
      const dock=[...document.querySelectorAll('nav')].find(n=>(n.getAttribute('aria-label')||'')==='Быстрая навигация');
      if(!dock) return {missing:true,items:[],active:[]};
      const items=[...dock.querySelectorAll('a,button')].map(el=>{
        const r=el.getBoundingClientRect();
        return {tag:el.tagName,text:(el.textContent||el.getAttribute('aria-label')||'').trim(),href:el.getAttribute('href'),w:Math.round(r.width),h:Math.round(r.height),active:el.getAttribute('aria-current')==='page'};
      });
      return {missing:false,items,active:items.filter(x=>x.active).map(x=>x.text)};
    }""")
    problems=[]
    if out.get("missing"): problems.append("dock-missing")
    if len(out.get("items") or [])!=5: problems.append("dock-item-count")
    if len(out.get("active") or [])!=1: problems.append("dock-active-count")
    if any(int(x.get("w") or 0)<44 or int(x.get("h") or 0)<44 for x in out.get("items") or []):
        problems.append("dock-touch-target")
    out["problems"]=problems
    return out

if __name__=="__main__":
    raise SystemExit(main())
