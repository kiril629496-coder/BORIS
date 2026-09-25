#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production browser audit for BORIS responsive web UI.

Scope: frontend_mobile_ui only. No content writes, no campaign launch, no provider
calls. It authenticates with the dedicated QA client and checks canonical product
routes at phone/tablet/desktop widths. The script is intentionally stored with
frontend QA so mobile regressions are caught without touching backend modules.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

BACKEND = Path("/root/BORIS/backend")
sys.path.insert(0, str(BACKEND))
import qa_suite  # existing non-client QA credentials / constants
from playwright.sync_api import sync_playwright

WEB = "https://boris-ai.pro"
QA_CRED = qa_suite.CLIENT

# Canonical web routes. Native phone/PBX routes are deliberately excluded: they
# belong to telephony_core, not frontend_mobile_ui.
ROUTES = [
    "/dashboard/command-center",
    "/dashboard/campaign",
    "/messages",
    "/dashboard/crm",
    "/dashboard/analytics",
    "/dashboard/marketing",
    "/dashboard/company",
    "/dashboard/billing",
    "/dashboard/sitebuild",
    "/ai",
    "/rop",
    "/social",
    "/command",
]
VIEWPORTS = [
    (320, 760, "phone-320"),
    (360, 800, "phone-360"),
    (390, 844, "phone-390"),
    (768, 900, "tablet-768"),
    (1440, 1000, "desktop-1440"),
]
ROUTE_PACE_SEC=float(os.environ.get("BORIS_QA_ROUTE_PACE_SEC","0.75"))
WATCH_LOCK=Path("/root/BORIS/frontend/.boris-builds/mobile-campaign-runtime-watch.lock")

def deploy_in_progress():
    p=subprocess.run(
        ["pgrep","-f",r"^bash (scripts/boris-frontend-deploy\.sh|/root/BORIS/frontend/scripts/boris-frontend-deploy\.sh)( |$)"],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    )
    return p.returncode==0


_QA_SESSION=None

def qa_session():
    global _QA_SESSION
    if _QA_SESSION:
        return _QA_SESSION
    last=""
    for attempt in range(3):
        try:
            r=requests.post(
                WEB + "/api/auth/login",
                json={"email":QA_CRED[0],"password":QA_CRED[1]},
                timeout=20,
            )
            if r.status_code==200:
                data=r.json(); user=data.get("user") or {}
                token=data.get("access_token")
                role=user.get("role") or "client"
                account=str(user.get("account_id") or "").strip()
                if token and account:
                    _QA_SESSION=(token,role,account,"public")
                    return _QA_SESSION
            last=f"http_{r.status_code}"
        except Exception as exc:
            last=f"{type(exc).__name__}:{str(exc)[:120]}"
        time.sleep(2*(attempt+1))

    # Broad responsive QA must not become useless because the public login
    # endpoint is briefly rate-limited while other QA jobs are authenticating.
    # A local token is valid for the same BORIS backend and only seeds browser
    # storage; the pages themselves are still loaded from public production.
    local=qa_suite.login(QA_CRED)
    if local:
        token,role,account=local
        account=str(account or "").strip()
        if token and account:
            _QA_SESSION=(token,role,account,"local-fallback")
            return _QA_SESSION
    raise RuntimeError("QA auth failed: public="+last+" local=fallback_failed")


def login(page):
    token,role,account,_source=qa_session()
    page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
    page.evaluate("""([token,email,role,account])=>{
      localStorage.setItem('boris_token',token);
      localStorage.setItem('boris_user_email',email);
      localStorage.setItem('boris_user_role',role);
      localStorage.setItem('boris_currentAccount',account);
    }""",[token,QA_CRED[0],role,account])
    return account


def page_metrics(page):
    return page.evaluate(
        """() => {
        const root=document.documentElement;
        const visible=(el)=>{
          const s=getComputedStyle(el), r=el.getBoundingClientRect();
          if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0) return false;
          if(r.width<=0||r.height<=0) return false;
          // Closed mobile sidebar is intentionally translated off-screen.
          // Keep controls below the fold in the audit: touch targets must remain
          // usable after scrolling. Only elements fully outside the horizontal
          // canvas (for example the closed sidebar) are excluded.
          if(r.right<=0||r.left>=innerWidth) return false;
          return true;
        };
        const offenders=[];
        for(const el of document.querySelectorAll('body *')){
          if(!visible(el)) continue;
          const s=getComputedStyle(el), r=el.getBoundingClientRect();
          if(s.position==='fixed' || s.position==='sticky') continue;
          if(r.right>innerWidth+4 || r.left<-4){
            let p=el.parentElement, intentionalClip=false;
            while(p && p!==document.body){
              const ps=getComputedStyle(p);
              // Horizontal scrollers and clipped carousels are allowed to contain
              // children wider than the viewport as long as the document itself
              // never expands. They are not responsive-layout defects.
              if(['auto','scroll','hidden','clip'].includes(ps.overflowX)){
                intentionalClip=true;break;
              }
              p=p.parentElement;
            }
            if(intentionalClip) continue;
            offenders.push({tag:el.tagName, cls:String(el.className||'').slice(0,90),
              left:Math.round(r.left), right:Math.round(r.right), width:Math.round(r.width),
              text:(el.textContent||'').trim().slice(0,80)});
            if(offenders.length>=10) break;
          }
        }
        const tiny=[];
        if(innerWidth<=390){
          for(const el of document.querySelectorAll('button,a,input,select,textarea')){
            if(!visible(el)) continue;
            const r=el.getBoundingClientRect();
            const txt=(el.getAttribute('aria-label')||el.textContent||el.getAttribute('placeholder')||'').trim();
            // Ignore inline text links and hidden utility inputs; flag controls that
            // visually behave as buttons but are too small for reliable touch.
            const isInlineLink=el.tagName==='A' && getComputedStyle(el).display==='inline';
            if(isInlineLink) continue;
            if((r.height<44 || r.width<44) && txt){
              tiny.push({tag:el.tagName, w:Math.round(r.width), h:Math.round(r.height), text:txt.slice(0,60)});
              if(tiny.length>=10) break;
            }
          }
        }
        const tinyText=[];
        if(innerWidth<=390){
          for(const el of document.querySelectorAll('p,small,label,a,button,li,td,th')){
            if(!visible(el)) continue;
            const text=(el.textContent||'').trim().replace(/\\s+/g,' ');
            if(text.length<18) continue;
            const size=parseFloat(getComputedStyle(el).fontSize||'0');
            if(size>0 && size<12){
              tinyText.push({tag:el.tagName,size:Math.round(size*10)/10,text:text.slice(0,80)});
              if(tinyText.length>=10) break;
            }
          }
        }
        const dock=[...document.querySelectorAll('nav')].find(x => (x.getAttribute('aria-label')||'')==='Быстрая навигация');
        const ds=dock?getComputedStyle(dock):null;
        const dr=dock?.getBoundingClientRect();
        const dockOverlap=[];
        if(innerWidth<=900 && dr && ds?.display!=='none'){
          for(const el of document.querySelectorAll('button,a,input,select,textarea')){
            if(dock?.contains(el) || !visible(el)) continue;
            let p=el, persistent=false, persistentPosition='';
            while(p && p!==document.body){
              const pos=getComputedStyle(p).position;
              if(pos==='fixed'||pos==='sticky'){persistent=true;persistentPosition=pos;break;}
              p=p.parentElement;
            }
            if(!persistent) continue;
            const r=el.getBoundingClientRect();
            if(r.bottom>dr.top+1 && r.top<dr.bottom-1 && r.right>dr.left && r.left<dr.right){
              dockOverlap.push({
                tag:el.tagName,
                text:(el.getAttribute('aria-label')||el.textContent||el.getAttribute('placeholder')||'').trim().slice(0,60),
                position:persistentPosition,
                top:Math.round(r.top),bottom:Math.round(r.bottom)
              });
              if(dockOverlap.length>=8) break;
            }
          }
        }
        return {
          path:location.pathname,
          width:innerWidth,
          scrollWidth:root.scrollWidth,
          overflowX:root.scrollWidth>innerWidth+1,
          bodyText:(document.body.innerText||'').trim().length,
          shellCount:document.querySelectorAll('[data-testid="app-shell"]').length,
          backCount:document.querySelectorAll('[data-testid="nav-back"]').length,
          account:localStorage.getItem('boris_currentAccount'),
          dockDisplay:ds?ds.display:null,
          dockOverlap,
          offenders,
          tiny,
          tinyText
        };
      }"""
    )


def route_contract(page, route, metrics, expected_account):
    problems=[]
    if metrics["path"] != route:
        problems.append(f"redirect:{metrics['path']}")
    if metrics["overflowX"]:
        problems.append(f"overflow:{metrics['scrollWidth']}>{metrics['width']}")
    if metrics["bodyText"] < 80:
        problems.append(f"empty:{metrics['bodyText']}")
    if metrics["offenders"]:
        problems.append(f"offscreen:{len(metrics['offenders'])}")
    if metrics["width"] <= 900 and metrics["dockDisplay"] == "none":
        problems.append("mobile-dock-hidden")
    if metrics["width"] > 900 and metrics["dockDisplay"] not in (None, "none"):
        problems.append("mobile-dock-on-desktop")
    if metrics.get("account") != expected_account:
        problems.append("account-state-lost")
    if metrics["width"] <= 900 and metrics.get("dockOverlap"):
        problems.append(f"dock-overlap:{len(metrics['dockOverlap'])}")
    if metrics["width"] <= 390 and metrics["tiny"]:
        problems.append(f"tiny-controls:{len(metrics['tiny'])}")
    if metrics["width"] <= 390 and metrics.get("tinyText"):
        problems.append(f"tiny-text:{len(metrics['tinyText'])}")

    if route == "/dashboard/campaign" and metrics["width"] <= 390:
        q=page.locator('[data-testid="campaign-quantity-row"]')
        if q.count():
            stack=q.evaluate("""e=>{const a=e.children[0]?.getBoundingClientRect(),b=e.children[1]?.getBoundingClientRect();return !!(a&&b&&b.top>=a.bottom-1)}""")
            if not stack:
                problems.append("campaign-quantity-not-stacked")
    if route == "/dashboard/command-center":
        for tid in ("command-center-root","command-center-status","command-center-attention","command-center-today","command-center-journal","command-center-quick"):
            if page.locator(f'[data-testid="{tid}"]').count()!=1:
                problems.append("missing:"+tid)
    if route == "/messages" and metrics["width"] <= 390:
        ws=page.locator('[data-testid="messages-workspace"]')
        if not ws.count():
            problems.append("messages-workspace-missing")
        elif ws.get_attribute("data-mobile-view") not in ("list","thread"):
            problems.append("messages-mobile-view-invalid")
    return problems


def main():
    result={"status":"PASS","viewports":{},"failures":[],"warnings":[]}
    _,_,expected_account,auth_source=qa_session()
    result["auth_source"]=auth_source
    result["qa_account"]=expected_account
    if auth_source!="public":
        result["warnings"].append({"scope":"auth","reason":"public-login-unavailable-local-token-fallback"})
    WATCH_LOCK.parent.mkdir(parents=True,exist_ok=True)
    lock_handle=WATCH_LOCK.open("a+")
    fcntl.flock(lock_handle.fileno(),fcntl.LOCK_EX)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for width,height,label in VIEWPORTS:
            if deploy_in_progress():
                result["status"]="SKIP"
                result["reason"]="deploy_in_progress"
                break
            ctx=browser.new_context(viewport={"width":width,"height":height},user_agent="BORIS-QA-Mobile-Audit/1.0")
            page=ctx.new_page()
            session_account=login(page)
            result["viewports"][label]={}
            for route in ROUTES:
                if deploy_in_progress():
                    result["status"]="SKIP"
                    result["reason"]="deploy_in_progress"
                    break
                server_errors=[]
                failed_requests=[]
                page.on("response",lambda r,arr=server_errors: arr.append((r.status,r.url)) if r.status>=500 else None)
                page.on("requestfailed",lambda r,arr=failed_requests: arr.append((str(r.failure or ''),r.url)) if 'ERR_ABORTED' not in str(r.failure or '') else None)
                try:
                    page.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
                    page.wait_for_timeout(900)
                    m=page_metrics(page)
                    problems=route_contract(page,route,m,session_account)
                    # This audit owns responsive/frontend behavior only. A document
                    # 5xx means the page itself failed and is a frontend/runtime FAIL.
                    # API 5xx stays visible as delegated backend evidence instead of
                    # making a healthy layout red because of another workstream.
                    page5xx=[x for x in server_errors if "/api/" not in x[1]]
                    api5xx=[x for x in server_errors if "/api/" in x[1]]
                    if page5xx:
                        problems.append("page-http5xx:"+str(page5xx[:3]))
                    if api5xx:
                        result["warnings"].append({"viewport":label,"route":route,"backend5xx":api5xx[:3]})
                    # External certificate/provider noise is warning evidence, not a
                    # layout failure. Keep it visible so it can be investigated.
                    req_warn=[x for x in failed_requests if 'ERR_FAILED' in x[0] or 'CERT_' in x[0]]
                    if req_warn:
                        result["warnings"].append({"viewport":label,"route":route,"requests":req_warn[:3]})
                    result["viewports"][label][route]={"metrics":m,"problems":problems}
                    if problems:
                        result["failures"].append({"viewport":label,"route":route,"problems":problems})
                except Exception as exc:
                    rec={"viewport":label,"route":route,"problems":["navigation:"+type(exc).__name__+":"+str(exc)[:100]]}
                    result["viewports"][label][route]=rec
                    result["failures"].append(rec)
                time.sleep(ROUTE_PACE_SEC)
            ctx.close()
            if result.get("status")=="SKIP":
                break
        browser.close()
    fcntl.flock(lock_handle.fileno(),fcntl.LOCK_UN)
    lock_handle.close()
    if result.get("status")!="SKIP" and result["failures"]:
        result["status"]="FAIL"
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"] in {"PASS","SKIP"} else 2


if __name__=="__main__":
    raise SystemExit(main())
