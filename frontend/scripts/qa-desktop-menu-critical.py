#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only desktop navigation smoke for critical BORIS product entries."""
import json,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
STEPS=[
 ("Поиск клиентов и Email","/dashboard/prospecting/manage"),
 ("Привлечение клиентов","/dashboard/prospecting/acquisition"),
 ("Продажи","/dashboard/crm"),
 ("Тарифы и финансы","/dashboard/billing"),
 ("Объявления","/dashboard/campaign"),
]

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(400)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2000)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def metrics(page):
    return page.evaluate("""() => ({
      path:location.pathname,
      width:innerWidth,
      scrollWidth:document.documentElement.scrollWidth,
      overflowX:document.documentElement.scrollWidth>innerWidth+1,
      shell:document.querySelectorAll('[data-testid="app-shell"]').length,
      back:document.querySelectorAll('[data-testid="nav-back"]').length,
      account:localStorage.getItem('boris_currentAccount')
    })""")

def main():
    out={"status":"PASS","steps":[],"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        ctx=br.new_context(viewport={"width":1440,"height":1000})
        p=ctx.new_page(); login(p)
        for label,target in STEPS:
            # Every menu item is tested from the same canonical shell. Some
            # destination pages intentionally have a reduced local action set;
            # chaining from one destination to the next would create false
            # "link missing" failures unrelated to the global BORIS menu.
            p.goto(WEB+"/dashboard/command-center",wait_until="domcontentloaded",timeout=30000)
            p.wait_for_timeout(650)
            link=p.get_by_role("link",name=label,exact=True)
            problems=[]
            try:
                link.first.wait_for(state="visible",timeout=3000)
            except Exception:
                problems.append("link-missing")
            if not problems:
                link.first.click()
                p.wait_for_timeout(650)
                m=metrics(p)
                if m["path"]!=target: problems.append("wrong-route:"+m["path"])
                if m["overflowX"]: problems.append("horizontal-overflow")
                if m["shell"]!=1: problems.append("shell")
                if m["back"]!=1: problems.append("back")
                if m["account"]!=ACCOUNT: problems.append("account-state-lost")
            m=metrics(p)
            out["steps"].append({"label":label,"target":target,"metrics":m,"problems":problems})
            if problems: out["failures"].append({"label":label,"problems":problems})
        ctx.close(); br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
