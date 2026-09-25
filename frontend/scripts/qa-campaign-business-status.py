#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only QA for the plain-language campaign business status card."""
import json,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
CASES=[("phone-320",320,760,287),("phone-390",390,844,287),("desktop-1440",1440,1000,286)]

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(350)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(1900)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def main():
    out={"status":"PASS","viewports":{},"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for label,w,h,cid in CASES:
            ctx=br.new_context(viewport={"width":w,"height":h})
            p=ctx.new_page(); login(p)
            p.goto(WEB+f"/dashboard/campaign/{cid}",wait_until="domcontentloaded",timeout=30000)
            p.wait_for_timeout(1000)
            card=p.locator('[data-testid="campaign-business-status"]')
            problems=[]
            if not card.count() or not card.is_visible():
                problems.append("business-status-missing")
                data={}
            else:
                data=card.evaluate("""el=>{
                  const r=el.getBoundingClientRect();
                  const cells=[...el.querySelectorAll('.boris-campaign-business-status-cell')];
                  return {
                    text:(el.innerText||'').trim(),
                    cells:cells.length,
                    width:Math.round(r.width),
                    left:Math.round(r.left),
                    right:Math.round(r.right),
                    grid:getComputedStyle(el.querySelector('.boris-campaign-business-status-grid')).gridTemplateColumns,
                    ownerAction:cells[2]?.getAttribute('data-owner-action')||null,
                    overflowX:document.documentElement.scrollWidth>innerWidth+1
                  }
                }""")
                for required in ("BORIS сейчас","Практический результат","От вас сейчас"):
                    if required not in data.get("text",""): problems.append("missing-label:"+required)
                if cid in (286,287) and "Собрал структуру кампании" not in data.get("text",""):
                    problems.append("structure-business-status-wrong")
                if cid in (286,287) and "Ничего. BORIS продолжает подготовку сам" not in data.get("text",""):
                    problems.append("owner-action-not-plain")
                if data.get("cells")!=3: problems.append("cell-count")
                if data.get("overflowX"): problems.append("horizontal-overflow")
                if w<=390 and " " in str(data.get("grid","")).strip():
                    problems.append("mobile-not-single-column")
            out["viewports"][label]={"campaign_id":cid,"metrics":data,"problems":problems}
            if problems: out["failures"].append({"viewport":label,"problems":problems})
            ctx.close()
        br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
