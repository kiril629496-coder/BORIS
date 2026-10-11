# -*- coding: utf-8 -*-
import json,re,os
from playwright.sync_api import sync_playwright
import qa_suite

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
ROUTES=[
 "/dashboard/command-center",
 "/dashboard/campaign",
 "/messages",
 "/dashboard/crm",
 "/manager/clients",
 "/dashboard/analytics",
 "/dashboard/marketing",
 "/dashboard/company",
 "/dashboard/billing",
 "/dashboard/ai",
 "/dashboard/rop",
 "/social",
]
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

def audit(page):
    return page.evaluate("""() => {
      const de=document.documentElement;
      const els=[...document.querySelectorAll('body *')];
      const offenders=[];
      for(const el of els){
        const s=getComputedStyle(el);
        if(s.position==='fixed'||s.position==='sticky'||s.display==='none') continue;
        const r=el.getBoundingClientRect();
        if(r.width>0 && (r.right>innerWidth+8 || r.left<-8)){
          const p=el.parentElement ? getComputedStyle(el.parentElement) : null;
          if(p && ['auto','scroll'].includes(p.overflowX)) continue;
          offenders.push({tag:el.tagName,cls:String(el.className||'').slice(0,90),left:Math.round(r.left),right:Math.round(r.right),w:Math.round(r.width),text:(el.textContent||'').trim().slice(0,80)});
          if(offenders.length>=12) break;
        }
      }
      return {
        path:location.pathname,
        width:innerWidth,
        scrollWidth:de.scrollWidth,
        overflowX:de.scrollWidth>innerWidth+1,
        bodyText:(document.body.innerText||'').length,
        shellCount:document.querySelectorAll('[data-testid="app-shell"]').length,
        backCount:document.querySelectorAll('[data-testid="nav-back"]').length,
        offenders
      };
    }""")

os.makedirs(qa_suite.QA_DIR,exist_ok=True)
with sync_playwright() as pw:
    br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for width,tag in ((1440,"desktop"),(390,"mobile")):
        ctx=br.new_context(viewport={"width":width,"height":900})
        p=ctx.new_page()
        login(p)
        OUT[tag]={}
        for route in ROUTES:
            errs=[]
            p.remove_listener("console",lambda m:None) if False else None
            p.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
            p.wait_for_timeout(1300)
            OUT[tag][route]=audit(p)
        ctx.close()
    br.close()
print(json.dumps(OUT,ensure_ascii=False,indent=2))
