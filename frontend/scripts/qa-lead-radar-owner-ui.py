#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production QA for owner Lead Radar panel."""
from __future__ import annotations
import json,sys,requests
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"

def auth():
    r=requests.post(WEB+"/api/auth/login",json={"email":qa_suite.OWNER[0],"password":qa_suite.OWNER[1]},timeout=20)
    r.raise_for_status()
    d=r.json()
    return d["access_token"],(d.get("user") or {}).get("role") or "owner"

def api(token,path,params=None):
    r=requests.get(WEB+path,headers={"Authorization":"Bearer "+token},params=params,timeout=20)
    r.raise_for_status()
    return r.json()

def main():
    token,role=auth()
    h=api(token,"/api/lead-radar/health")
    s=api(token,"/api/lead-radar/stats")
    draft=(api(token,"/api/lead-radar/leads",{"limit":12,"status":"draft_ready"}).get("items") or [])
    qualified=(api(token,"/api/lead-radar/leads",{"limit":12,"status":"qualified"}).get("items") or [])
    ls=sorted(draft+qualified,key=lambda x:int(x.get("score") or 0),reverse=True)[:12]
    cov=h.get("source_coverage") or {}
    expected={
        "status":h.get("status"),
        "total":h.get("total_leads"),
        "coverage":f"{cov.get('configured_groups',0)}/{cov.get('target_groups',20)}",
        "qualified":(s.get("by_status") or {}).get("qualified",0),
        "draft_ready":(s.get("by_status") or {}).get("draft_ready",0),
        "quality_pending":int(h.get("quality_recheck_pending") or 0),
        "leads":len(ls),
    }
    out={"api":expected,"viewports":{},"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for width,height in ((390,844),(1440,1000)):
            ctx=br.new_context(viewport={"width":width,"height":height})
            page=ctx.new_page()
            page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
            page.evaluate("""([token,email,role])=>{
              localStorage.setItem('boris_token',token);
              localStorage.setItem('boris_user_email',email);
              localStorage.setItem('boris_user_role',role);
              localStorage.setItem('boris_currentAccount','testref_47784');
            }""",[token,qa_suite.OWNER[0],role])
            page.goto(WEB+"/dashboard/prospecting/manage",wait_until="domcontentloaded",timeout=30000)
            page.get_by_text("LEAD RADAR · ЖИВОЙ ПОИСК ЗАКАЗОВ",exact=True).wait_for(state="visible",timeout=10000)
            page.wait_for_timeout(900)
            sec=page.locator("section").filter(has_text="LEAD RADAR · ЖИВОЙ ПОИСК ЗАКАЗОВ").first
            txt=sec.inner_text()
            checks={
                "visible":sec.is_visible(),
                "health_pass":"работает" in txt.lower(),
                "total":str(expected["total"]) in txt,
                "coverage":expected["coverage"] in txt,
                "qualified":str(expected["qualified"]) in txt,
                "draft_ready":str(expected["draft_ready"]) in txt,
                "quality_pending":f"Переоценка качества: {expected['quality_pending']}" in txt,
                "source_link":sec.locator('a[target="_blank"]').count()>0 if expected["leads"] else True,
                "owner_action":"Нужно участие владельца: нет" in txt,
                "overflow":page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),
            }
            bad=[k for k,v in checks.items() if not v]
            out["viewports"][str(width)]=checks
            if bad:
                out["failures"].append({"viewport":width,"checks":bad,"sample":txt[:800]})
            ctx.close()
        br.close()
    out["status"]="PASS" if not out["failures"] else "FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
