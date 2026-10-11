#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deploy gate for the shared client mobile/campaign frontend only.

No owner-center/admin checks live here. Authentication is seeded from the local
QA API token so this gate cannot fail because the public login form was slow.
Read-only: no forms, publications or paid actions are submitted.
"""
from __future__ import annotations
import json,sys,time,requests
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
BASE_ROUTES=[
 "/dashboard/command-center","/dashboard/campaign",
 "/messages","/dashboard/crm","/dashboard/billing","/dashboard/company",
 "/command"
]
FORBIDDEN_CLIENT_ROUTES={"/dashboard/prospecting/manage","/dashboard/reliability","/journal"}

_PUBLIC_AUTH=None
_CLIENT_AUTH=None

def public_auth():
    global _PUBLIC_AUTH
    if _PUBLIC_AUTH:
        return _PUBLIC_AUTH
    last=""
    for attempt in range(3):
        try:
            r=requests.post(WEB+"/api/auth/login",json={"email":qa_suite.OWNER[0],"password":qa_suite.OWNER[1]},timeout=20)
            if r.status_code==200:
                d=r.json()
                user=d.get("user") or {}
                token=d.get("access_token")
                role=user.get("role") or "owner"
                if token:
                    _PUBLIC_AUTH=(token,role,ACCOUNT)
                    return _PUBLIC_AUTH
            last=f"http_{r.status_code}"
        except Exception as e:
            last=f"{type(e).__name__}:{str(e)[:120]}"
        time.sleep(2*(attempt+1))
    raise RuntimeError("public QA auth failed: "+last)

def client_auth():
    global _CLIENT_AUTH
    if _CLIENT_AUTH:
        return _CLIENT_AUTH
    last=""
    for attempt in range(3):
        try:
            r=requests.post(WEB+"/api/auth/login",json={"email":qa_suite.CLIENT[0],"password":qa_suite.CLIENT[1]},timeout=20)
            if r.status_code==200:
                d=r.json(); user=d.get("user") or {}
                token=d.get("access_token"); role=user.get("role") or "client"
                account=str(user.get("account_id") or "").strip()
                if token and account:
                    sr=requests.get(
                        WEB+"/api/control-plane/workspace/client-supervisor",
                        headers={"Authorization":"Bearer "+token},
                        params={"account_id":account},
                        timeout=15,
                    )
                    if sr.status_code==200:
                        _CLIENT_AUTH=(token,role,account)
                        return _CLIENT_AUTH
                    last=f"supervisor_http_{sr.status_code}"
                else:
                    last="client_account_missing"
            else:
                last=f"http_{r.status_code}"
        except Exception as e:
            last=f"{type(e).__name__}:{str(e)[:120]}"
        time.sleep(2*(attempt+1))
    raise RuntimeError("client QA auth failed: "+last)

def apply_auth(page,mode,account):
    if mode=="campaign":
        token,role,_=public_auth(); email=qa_suite.OWNER[0]
    else:
        token,role,_=client_auth(); email=qa_suite.NEWBIE[0]
    page.evaluate("""([token,email,role,account])=>{
      localStorage.setItem('boris_token',token);
      localStorage.setItem('boris_user_email',email);
      localStorage.setItem('boris_user_role',role);
      localStorage.setItem('boris_currentAccount',account);
    }""",[token,email,role,account])

def choose_campaign_route():
    token,_,_=public_auth()
    headers={"Authorization":"Bearer "+token}
    r=requests.get(WEB+"/api/campaign/list",headers=headers,params={"account_id":ACCOUNT},timeout=20)
    r.raise_for_status()
    # QA_FIXTURE_SCAN_BOUND_V1: bound discovery so campaign history cannot
    # exhaust the canonical deploy job timeout.
    for c in r.json().get("campaigns",[])[:20]:
        cid=c.get("id")
        if not cid:
            continue
        try:
            wf=requests.get(WEB+f"/api/campaign/{cid}/workflow",headers=headers,timeout=12)
            if wf.status_code!=200:
                continue
            d=wf.json(); w=d.get("workflow") or {}
            step=str(w.get("step") or "")
            questions=d.get("questions") or []
            if step in {"structure","texts","media","category","settings","validation","preview","safe_feed"} and not questions:
                return f"/dashboard/campaign/{cid}", {"id":cid,"step":step,"name":c.get("name")}
        except Exception:
            continue
    raise RuntimeError("no stable QA campaign fixture found")

def go(page,route,account,mode):
    apply_auth(page,mode,account)
    page.goto(WEB+route,wait_until="commit",timeout=30000)
    page.wait_for_timeout(650)
    if "/login" in page.url:
        apply_auth(page,mode,account)
        page.goto(WEB+route,wait_until="commit",timeout=30000)
        page.wait_for_timeout(700)

    # Commit precedes hydration; a fixed sleep can measure the loading fallback.
    # Bound readiness, then retain every shell/content/overflow assertion below.
    try:
        page.locator('[data-testid="app-shell"]').wait_for(state="visible",timeout=4000)
    except PlaywrightTimeoutError:
        pass

def metrics(page):
    return page.evaluate("""() => {
      const root=document.documentElement;
      const dock=[...document.querySelectorAll('nav')].find(n=>(n.getAttribute('aria-label')||'')==='Быстрая навигация');
      const dr=dock?.getBoundingClientRect();
      const launcher=document.querySelector('.boris-contextual-launcher');
      const lr=launcher?.getBoundingClientRect();
      const body=(document.body.innerText||'').trim();
      return {
        path:location.pathname,bodyText:body.length,
        overflowX:root.scrollWidth>innerWidth+1,scrollWidth:root.scrollWidth,
        shell:document.querySelectorAll('[data-testid="app-shell"]').length,
        back:document.querySelectorAll('[data-testid="nav-back"]').length,
        account:localStorage.getItem('boris_currentAccount'),
        dock:dr?{top:Math.round(dr.top),bottom:Math.round(dr.bottom),w:Math.round(dr.width),h:Math.round(dr.height)}:null,
        launcherDockOverlap:!!(dr&&lr&&lr.bottom>dr.top&&lr.top<dr.bottom),
        looks404:/страница не найдена|page not found|\b404\b/i.test(body.slice(0,2600))
      };
    }""")

def main():
    forbidden=sorted(set(BASE_ROUTES)&FORBIDDEN_CLIENT_ROUTES)
    if forbidden:
        raise RuntimeError("client QA contains owner/internal routes: "+",".join(forbidden))
    campaign_route,fixture=choose_campaign_route()
    _,_,session_account=client_auth()
    routes=BASE_ROUTES[:2]+[campaign_route]+BASE_ROUTES[2:]
    out={"status":"PASS","scope":"frontend_mobile_ui","viewport":"320x760","fixture":fixture,"session_account":session_account,"routes":{},"failures":[],"backend5xx":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        ctx=br.new_context(viewport={"width":320,"height":760},user_agent="BORIS-QA-Mobile-DeployGate/1.0")
        page=ctx.new_page()
        page.goto(WEB+"/login",wait_until="commit",timeout=30000)
        for route in routes:
            responses=[]; auth_errors=[]
            def on_response(resp):
                if resp.status>=500: responses.append((resp.status,resp.url))
                if resp.status in (401,403) and "/api/control-plane/workspace/client-supervisor" in resp.url:
                    auth_errors.append((resp.status,resp.url))
            page.on("response",on_response)
            campaign_mode=route.startswith("/dashboard/campaign")
            route_account=ACCOUNT if campaign_mode else session_account
            go(page,route,route_account,"campaign" if campaign_mode else "client")
            m=metrics(page); problems=[]
            if "/login" in page.url: problems.append("redirect-login")
            if m["looks404"]: problems.append("404-content")
            if m["bodyText"]<80: problems.append("empty")
            if m["overflowX"]: problems.append("horizontal-overflow")
            if m["shell"]!=1: problems.append("shell")
            if m["back"]!=1: problems.append("back")
            if m["account"]!=route_account: problems.append("account-state-lost")
            if not m["dock"]: problems.append("dock-missing")
            if m["launcherDockOverlap"]: problems.append("assistant-dock-overlap")
            if route.startswith("/dashboard/campaign/"):
                card=page.locator('[data-testid="campaign-business-status"]')
                # Dynamic campaign data can finish hydrating just after DOMContentLoaded.
                # Wait briefly for the required business-status card instead of treating
                # normal startup/hydration as a production regression. The assertion is
                # still fail-closed: if the card is not visible after the grace window,
                # deployment/runtime watch remains red.
                try:
                    card.wait_for(state="visible", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                if not card.count() or not card.is_visible():
                    problems.append("campaign-business-status-missing")
                else:
                    t=card.inner_text()
                    if not all(x in t for x in ("BORIS сейчас","Практический результат","От вас сейчас")):
                        problems.append("campaign-business-status-labels")
            page5xx=[x for x in responses if "/api/" not in x[1]]
            api5xx=[x for x in responses if "/api/" in x[1]]
            if page5xx: problems.append("page-http5xx")
            if api5xx: out["backend5xx"].append({"route":route,"responses":api5xx[:5]})
            if auth_errors: problems.append("supervisor-auth")
            out["routes"][route]={"metrics":m,"problems":problems,"authErrors":auth_errors[:5]}
            if problems: out["failures"].append({"route":route,"problems":problems})
            page.remove_listener("response",on_response)
        ctx.close()

        # Explicit customer-facing regression target from the mobile UI contour:
        # /messages/setup must remain usable both on a real phone width and on
        # desktop. This is read-only navigation; no form is submitted.
        out["messages_setup"]={}
        for label,w,h in [("phone-390",390,844),("desktop-1440",1440,1000)]:
            sctx=br.new_context(viewport={"width":w,"height":h},user_agent="BORIS-QA-MessagesSetup/1.0")
            spage=sctx.new_page()
            spage.goto(WEB+"/login",wait_until="commit",timeout=30000)
            responses=[]; auth_errors=[]
            def on_setup_response(resp):
                if resp.status>=500: responses.append((resp.status,resp.url))
                if resp.status in (401,403) and "/api/control-plane/workspace/client-supervisor" in resp.url:
                    auth_errors.append((resp.status,resp.url))
            spage.on("response",on_setup_response)
            go(spage,"/messages/setup",session_account,"client")
            sm=metrics(spage); sproblems=[]
            if spage.url.split("?")[0].rstrip("/") != WEB+"/messages/setup":
                sproblems.append("wrong-route")
            if "/login" in spage.url: sproblems.append("redirect-login")
            if sm["looks404"]: sproblems.append("404-content")
            if sm["bodyText"]<80: sproblems.append("empty")
            if sm["overflowX"]: sproblems.append("horizontal-overflow")
            if sm["shell"]!=1: sproblems.append("shell")
            if sm["back"]!=1: sproblems.append("back")
            if sm["account"]!=session_account: sproblems.append("account-state-lost")
            if w<=700 and not sm["dock"]: sproblems.append("dock-missing")
            if w<=700 and sm["launcherDockOverlap"]: sproblems.append("assistant-dock-overlap")
            page5xx=[x for x in responses if "/api/" not in x[1]]
            api5xx=[x for x in responses if "/api/" in x[1]]
            if page5xx: sproblems.append("page-http5xx")
            if api5xx: out["backend5xx"].append({"route":"/messages/setup","viewport":label,"responses":api5xx[:5]})
            if auth_errors: sproblems.append("supervisor-auth")
            out["messages_setup"][label]={"metrics":sm,"problems":sproblems,"authErrors":auth_errors[:5]}
            if sproblems: out["failures"].append({"route":"/messages/setup","viewport":label,"problems":sproblems})
            sctx.close()

        # CRM_DETAIL_ERROR_STATE_RUNTIME_V1: a missing/stale CRM entity must not
        # dump raw HTTP/JSON jargon on the client. The real backend access issue
        # remains diagnosable inside the collapsed technical details.
        out["crm_detail_error"]={}
        for kind,route in [("contact","/dashboard/crm/contact/999999999"),("deal","/dashboard/crm/deal/999999999")]:
            cctx=br.new_context(viewport={"width":390,"height":844},user_agent="BORIS-QA-CRM-DetailError/1.0")
            cpage=cctx.new_page()
            cpage.goto(WEB+"/login",wait_until="commit",timeout=30000)
            go(cpage,route,session_account,"client")
            card=cpage.locator('[data-testid="crm-detail-error"]')
            cproblems=[]
            try:
                card.wait_for(state="visible",timeout=4000)
            except PlaywrightTimeoutError:
                pass
            if not card.count() or not card.is_visible(): cproblems.append("friendly-error-missing")
            visible_text=cpage.locator("body").inner_text()
            if "CRM_HTTP_" in visible_text or "CONTACT_NOT_FOUND" in visible_text or "DEAL_NOT_FOUND" in visible_text:
                cproblems.append("raw-technical-error-visible")
            if not cpage.get_by_role("button",name="Повторить",exact=True).count(): cproblems.append("retry-missing")
            if not cpage.get_by_role("button",name="Вернуться в CRM",exact=True).count(): cproblems.append("crm-back-missing")
            cm=metrics(cpage)
            if cm["overflowX"]: cproblems.append("horizontal-overflow")
            if cm["shell"]!=1: cproblems.append("shell")
            if cm["back"]!=1: cproblems.append("back")
            out["crm_detail_error"][kind]={"metrics":cm,"problems":cproblems}
            if cproblems: out["failures"].append({"route":route,"viewport":"phone-390","problems":cproblems})
            cctx.close()
        br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
