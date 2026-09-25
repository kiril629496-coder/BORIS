#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(400)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2200)

def inspect(page):
    return page.evaluate("""() => {
      const dock=[...document.querySelectorAll('nav')].find(n=>(n.getAttribute('aria-label')||'')==='Быстрая навигация');
      const launcher=document.querySelector('.boris-contextual-launcher');
      const panel=document.querySelector('.boris-contextual-panel');
      const rect=(e)=>{if(!e)return null;const r=e.getBoundingClientRect();return {top:Math.round(r.top),bottom:Math.round(r.bottom),left:Math.round(r.left),right:Math.round(r.right),w:Math.round(r.width),h:Math.round(r.height)}};
      const d=rect(dock),l=rect(launcher),p=rect(panel);
      const tiny=[];
      for(const el of panel?.querySelectorAll('button,input,textarea,select')||[]){
        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
        if(s.display==='none'||s.visibility==='hidden'||r.width<=0||r.height<=0)continue;
        if(r.width<44||r.height<44)tiny.push({tag:el.tagName,w:Math.round(r.width),h:Math.round(r.height),text:(el.textContent||el.getAttribute('placeholder')||'').trim().slice(0,60)});
      }
      return {
        dock:d,launcher:l,panel:p,tiny,
        launcherDockOverlap:!!(d&&l&&l.bottom>d.top&&l.top<d.bottom),
        panelDockOverlap:!!(d&&p&&p.bottom>d.top&&p.top<d.bottom),
        overflowX:document.documentElement.scrollWidth>innerWidth+1
      };
    }""")

def main():
    out={"status":"PASS","viewports":{},"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for width,height,label in [(320,760,"phone-320"),(390,844,"phone-390")]:
            ctx=br.new_context(viewport={"width":width,"height":height})
            page=ctx.new_page(); login(page)
            page.goto(WEB+"/dashboard/command-center",wait_until="domcontentloaded",timeout=30000)
            page.wait_for_timeout(600)
            launcher=page.get_by_role("button",name="Открыть помощника BORIS",exact=True)
            launcher.click(); page.wait_for_timeout(350)
            m=inspect(page); problems=[]
            if not m["panel"]: problems.append("panel-missing")
            if m["launcherDockOverlap"]: problems.append("launcher-dock-overlap")
            if m["panelDockOverlap"]: problems.append("panel-dock-overlap")
            if m["overflowX"]: problems.append("horizontal-overflow")
            if m["tiny"]: problems.append("tiny-controls")
            out["viewports"][label]={"metrics":m,"problems":problems}
            if problems: out["failures"].append({"viewport":label,"problems":problems})
            ctx.close()
        br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
