#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast read-only mobile production gate for the shared BORIS frontend.

Runs read-only 320px client/campaign sessions across critical routes. Client
screens use the dedicated QA client; campaign fixtures use the existing campaign
QA identity. It never submits forms, starts campaigns or calls paid generation.
Backend API 5xx are recorded as evidence but do not masquerade as layout failure.
"""
from __future__ import annotations
import fcntl,json,subprocess,sys,time,requests
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
SESSION_ACCOUNT="qanewbie_47723"
ROUTE_PACE_SEC=0.75
WATCH_LOCK="/root/BORIS/frontend/.boris-builds/mobile-campaign-runtime-watch.lock"
ROUTES=[
  "/dashboard/command-center",
  "/dashboard/campaign",
  "/dashboard/campaign/287",
  "/messages",
  "/dashboard/crm",
  "/dashboard/billing",
  "/dashboard/company",
  "/command",
]

def login(page, credentials, account):
    ctx=page.context
    # Block only the login-success redirect. The intended route is opened
    # explicitly after the real auth response has been received.
    ctx.route("**/dashboard/command-center*",lambda route: route.abort())
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(350)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",account)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(credentials[0])
        page.locator("input[type=password]").fill(credentials[1])
        with page.expect_response(lambda r: r.request.method=="POST" and "/api/auth/login" in r.url,timeout=15000) as info:
            page.get_by_text("Войти",exact=True).first.click()
        response=info.value
        if response.status!=200:
            raise RuntimeError(f"QA login failed: http_{response.status}")
        data=response.json(); user=data.get("user") or {}
        token=str(data.get("access_token") or "")
        if not token:
            raise RuntimeError("QA login token missing")
        init=json.dumps({
            "token":token,
            "email":str(user.get("email") or credentials[0]),
            "role":str(user.get("role") or "client"),
            "account":account,
        },ensure_ascii=False)
        ctx.add_init_script(script=f"""(()=>{{const d={init};localStorage.setItem('boris_token',d.token);localStorage.setItem('boris_user_email',d.email);localStorage.setItem('boris_user_role',d.role);localStorage.setItem('boris_currentAccount',d.account);}})();""")
        page.wait_for_timeout(250)

def deploy_in_progress():
    p=subprocess.run(
        ["pgrep","-f",r"^bash (scripts/boris-frontend-deploy\.sh|/root/BORIS/frontend/scripts/boris-frontend-deploy\.sh)( |$)"],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    )
    return p.returncode==0

def metrics(page):
    return page.evaluate("""() => {
      const root=document.documentElement;
      const dock=[...document.querySelectorAll('nav')].find(n=>(n.getAttribute('aria-label')||'')==='Быстрая навигация');
      const dr=dock?.getBoundingClientRect();
      const launcher=document.querySelector('.boris-contextual-launcher');
      const lr=launcher?.getBoundingClientRect();
      const body=(document.body.innerText||'').trim();
      return {
        path:location.pathname,
        bodyText:body.length,
        overflowX:root.scrollWidth>innerWidth+1,
        scrollWidth:root.scrollWidth,
        shell:document.querySelectorAll('[data-testid="app-shell"]').length,
        back:document.querySelectorAll('[data-testid="nav-back"]').length,
        account:localStorage.getItem('boris_currentAccount'),
        dock:dr?{top:Math.round(dr.top),bottom:Math.round(dr.bottom),w:Math.round(dr.width),h:Math.round(dr.height)}:null,
        launcher:lr?{top:Math.round(lr.top),bottom:Math.round(lr.bottom),w:Math.round(lr.width),h:Math.round(lr.height)}:null,
        launcherDockOverlap:!!(dr&&lr&&lr.bottom>dr.top&&lr.top<dr.bottom),
        looks404:/страница не найдена|page not found|\b404\b/i.test(body.slice(0,2600))
      };
    }""")

def main():
    out={"status":"PASS","viewport":"320x760","routes":{},"failures":[],"backend5xx":[]}
    lock_handle=open(WATCH_LOCK,"a+")
    fcntl.flock(lock_handle.fileno(),fcntl.LOCK_EX)
    if deploy_in_progress():
        print(json.dumps({"status":"SKIP","reason":"deploy_in_progress"},ensure_ascii=False))
        fcntl.flock(lock_handle.fileno(),fcntl.LOCK_UN); lock_handle.close()
        return 0
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        client_ctx=br.new_context(viewport={"width":320,"height":760},user_agent="BORIS-QA-Mobile-Critical/1.0")
        client_page=client_ctx.new_page(); login(client_page,qa_suite.NEWBIE,SESSION_ACCOUNT)
        campaign_ctx=br.new_context(viewport={"width":320,"height":760},user_agent="BORIS-QA-Mobile-Critical/1.0")
        campaign_page=campaign_ctx.new_page(); login(campaign_page,qa_suite.OWNER,ACCOUNT)
        for route in ROUTES:
            page=campaign_page if route.startswith("/dashboard/campaign") else client_page
            if page is client_page and route=="/dashboard/command-center":
                client_ctx.unroute("**/dashboard/command-center*")
            responses=[]
            def on_response(resp):
                if resp.status>=500:
                    responses.append((resp.status,resp.url))
            page.on("response",on_response)
            route_account=ACCOUNT if route.startswith("/dashboard/campaign") else SESSION_ACCOUNT
            page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",route_account)
            page.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
            page.wait_for_timeout(650)
            if route in {"/dashboard/clients-control","/dashboard/development"}:
                ready_text="Что это даёт" if route.endswith("clients-control") else "Зачем это делаем"
                try:
                    page.wait_for_function("(expected)=>document.body && document.body.innerText.includes(expected)",ready_text,timeout=8000)
                except Exception:
                    page.reload(wait_until="domcontentloaded",timeout=30000)
                    page.wait_for_timeout(900)
                    try:
                        page.wait_for_function("(expected)=>document.body && document.body.innerText.includes(expected)",ready_text,timeout=8000)
                    except Exception:
                        pass
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
                    card.wait_for(state="visible", timeout=4000)
                except PlaywrightTimeoutError:
                    pass
                if not card.count() or not card.is_visible():
                    problems.append("campaign-business-status-missing")
                else:
                    business_text=card.inner_text()
                    for required in ("BORIS сейчас","Практический результат","От вас сейчас"):
                        if required not in business_text:
                            problems.append("campaign-business-status-labels")
                            break
            if route in {"/dashboard/clients-control","/dashboard/development"}:
                body=page.locator("body").inner_text()
                title="Управление клиентами" if route.endswith("clients-control") else "Онлайн-задачи и разработка"
                meaning="Что это даёт" if route.endswith("clients-control") else "Зачем это делаем"
                owner_state="main"
                if "Админский доступ не подтверждён" in body:
                    owner_state="denied"; problems.append("owner-auth-denied")
                elif "Проверяю доступ" in body or "Подключаю журнал" in body:
                    owner_state="checking"; problems.append("owner-auth-checking")
                if title not in body: problems.append("owner-center-title-missing")
                if meaning not in body: problems.append("owner-center-meaning-missing")
                if not page.locator('a[href="/dashboard/clients-control"]').count(): problems.append("clients-menu-link-missing")
                if not page.locator('a[href="/dashboard/development"]').count(): problems.append("development-menu-link-missing")
                m["ownerState"]=owner_state
            page5xx=[x for x in responses if "/api/" not in x[1]]
            api5xx=[x for x in responses if "/api/" in x[1]]
            if page5xx: problems.append("page-http5xx")
            if api5xx: out["backend5xx"].append({"route":route,"responses":api5xx[:5]})
            out["routes"][route]={"metrics":m,"problems":problems}
            if problems: out["failures"].append({"route":route,"problems":problems})
            page.remove_listener("response",on_response)
            time.sleep(ROUTE_PACE_SEC)
            if deploy_in_progress():
                out["status"]="SKIP"; out["reason"]="deploy_in_progress"
                break
        client_page.close(); campaign_page.close()
        client_ctx.close(); campaign_ctx.close()

        # OWNER_AUTH_NEGATIVE_GATE_V1: qa_suite.OWNER has role=owner but is not
        # the private platform owner. Local email/role hints must never open
        # owner-only APIs; the page must fail closed after real /api/auth/me.
        owner_ctx=br.new_context(viewport={"width":320,"height":760},user_agent="BORIS-QA-Mobile-Critical/1.0")
        owner_seed=owner_ctx.new_page(); login(owner_seed,qa_suite.OWNER,SESSION_ACCOUNT)
        owner_seed.close()

        out["owner_ui"]={}
        for viewport_label,size in (
            ("mobile-320",{"width":320,"height":760}),
            ("desktop-1440",{"width":1440,"height":900}),
        ):
            vr={}
            for route in ("/dashboard/clients-control","/dashboard/development"):
                opage=owner_ctx.new_page()
                opage.set_viewport_size(size)
                auth_hits=[]; page_auth_hits=[]
                def owner_auth_me_counted(route):
                    url=route.request.url
                    auth_hits.append(url)
                    if "surface=" in url:
                        page_auth_hits.append(url)
                    route.continue_()
                opage.route("**/api/auth/me*",owner_auth_me_counted)
                responses=[]; auth_errors=[]; page_errors=[]; console_errors=[]; request_failed=[]
                def on_owner_response(resp):
                    if resp.status>=500:
                        responses.append((resp.status,resp.url))
                    if resp.status in (401,403) and "/api/control-plane/" in resp.url:
                        auth_errors.append((resp.status,resp.url))
                def on_owner_page_error(exc):
                    page_errors.append(str(exc))
                def on_owner_console(msg):
                    if msg.type=="error":
                        console_errors.append(msg.text)
                def on_owner_request_failed(req):
                    request_failed.append((req.url, str(req.failure)))
                opage.on("response",on_owner_response)
                opage.on("pageerror",on_owner_page_error)
                opage.on("console",on_owner_console)
                opage.on("requestfailed",on_owner_request_failed)
                opage.goto(WEB+route,wait_until="domcontentloaded",timeout=30000)
                try:
                    opage.wait_for_function("()=>document.body && document.body.innerText.includes('Админский доступ не подтверждён')",timeout=10000)
                except Exception:
                    pass
                body=opage.locator("body").inner_text(timeout=5000)
                saved_owner_email=opage.evaluate("localStorage.getItem('boris_user_email') || ''")
                om=metrics(opage); oproblems=[]
                if "Админский доступ не подтверждён" not in body: oproblems.append("non-platform-owner-not-denied")
                if om["overflowX"]: oproblems.append("horizontal-overflow")
                if om["shell"]!=1: oproblems.append("shell")
                if om["back"]!=1: oproblems.append("back")
                if not opage.locator('a[href="/dashboard/clients-control"]').count(): oproblems.append("clients-menu-link-missing")
                if not opage.locator('a[href="/dashboard/development"]').count(): oproblems.append("development-menu-link-missing")
                page5xx=[x for x in responses if "/api/" not in x[1]]
                api5xx=[x for x in responses if "/api/" in x[1]]
                if page5xx: oproblems.append("page-http5xx")
                if api5xx: oproblems.append("api-http5xx")
                if auth_errors: oproblems.append("owner-control-plane-auth")
                owner_state="denied" if "Админский доступ не подтверждён" in body else "unexpected"
                vr[route]={
                    "metrics":om,
                    "problems":oproblems,
                    "ownerState":owner_state,
                    "authHits":len(auth_hits),
                    "pageAuthHits":len(page_auth_hits),
                    "savedOwnerEmail":saved_owner_email,
                    "bodySample":body[:500],
                    "bodyTail":body[-500:],
                    "page5xx":page5xx[:5],
                    "api5xx":api5xx[:5],
                    "authErrors":auth_errors[:5],
                    "pageErrors":page_errors[:5],
                    "consoleErrors":console_errors[:8],
                    "requestFailed":request_failed[:8],
                }
                if oproblems:
                    out["failures"].append({"route":route,"viewport":viewport_label,"problems":oproblems})
                opage.close()
            out["owner_ui"][viewport_label]=vr
        owner_ctx.close()
        br.close()
    fcntl.flock(lock_handle.fileno(),fcntl.LOCK_UN); lock_handle.close()
    if out.get("status")!="SKIP" and out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"] in {"PASS","SKIP"} else 2

if __name__=="__main__":
    raise SystemExit(main())
