#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production UI gate for BORIS private owner centers.

The browser uses the ordinary QA login, which intentionally has no platform-owner
privileges. Only /api/auth/me is mocked inside the browser so the private UI can
be rendered. All real owner APIs remain protected by backend 403 for the QA user.
The actual platform-owner identity rule is verified separately against the DB.
"""
from __future__ import annotations
import json, sys
sys.path.insert(0, "/root/BORIS/backend")

import qa_suite
from playwright.sync_api import sync_playwright
from app.db.session import SessionLocal
from app.models.user import User
from app.services.platform_roles import is_private_platform_owner

WEB = "https://boris-ai.pro"
ACCOUNT = "testref_47784"
ROUTES = {
    "/dashboard/clients-control": ("Управление клиентами", "Что это даёт"),
    "/dashboard/development": ("Онлайн-задачи и разработка", "Зачем это делаем"),
}

def owner_identity_ok():
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.id == 2).first()
        qa = db.query(User).filter(User.email == qa_suite.OWNER[0]).first()
        return {
            "real_owner": bool(owner and is_private_platform_owner(owner)),
            "qa_not_owner": bool(qa and not is_private_platform_owner(qa)),
        }
    finally:
        db.close()

def login_qa(page):
    page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти", exact=True).first.click()
        page.wait_for_timeout(1800)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)", ACCOUNT)

def main():
    identity = owner_identity_ok()
    out={"status":"PASS","backend_identity":identity,"viewports":{},"failures":[]}
    if not identity["real_owner"] or not identity["qa_not_owner"]:
        out["status"]="FAIL"
        out["failures"].append({"scope":"backend","problem":"private-platform-owner-identity"})

    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for label,viewport in (("mobile-320",{"width":320,"height":760}),("desktop-1440",{"width":1440,"height":900})):
            ctx=br.new_context(viewport=viewport)
            page=ctx.new_page()
            login_qa(page)

            def auth_me(route):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "status":"ok",
                        "user":{"id":2,"role":"owner","account_id":ACCOUNT,"platform_admin":True}
                    }, ensure_ascii=False),
                )
            page.route("**/api/auth/me", auth_me)

            vr={}
            for path,(title,meaning) in ROUTES.items():
                responses=[]
                def on_response(resp):
                    if resp.status >= 500:
                        responses.append((resp.status, resp.url))
                page.on("response", on_response)
                page.goto(WEB+path,wait_until="domcontentloaded",timeout=30000)
                try:
                    page.wait_for_function(
                        "(m)=>document.body && document.body.innerText.includes(m)",
                        meaning, timeout=8000
                    )
                except Exception:
                    pass
                body=page.locator("body").inner_text(timeout=5000)
                m=page.evaluate("""() => ({
                  path: location.pathname,
                  width: innerWidth,
                  scrollWidth: document.documentElement.scrollWidth,
                  bodyText: (document.body.innerText||'').length,
                  shell: document.querySelectorAll('[data-testid="app-shell"]').length,
                  back: document.querySelectorAll('[data-testid="nav-back"]').length,
                  clientsLinks: document.querySelectorAll('a[href="/dashboard/clients-control"]').length,
                  devLinks: document.querySelectorAll('a[href="/dashboard/development"]').length
                })""")
                problems=[]
                if title not in body: problems.append("title-missing")
                if meaning not in body: problems.append("business-meaning-missing")
                if m["shell"] != 1: problems.append("shell")
                if m["back"] != 1: problems.append("back")
                if m["clientsLinks"] < 1: problems.append("clients-menu-link")
                if m["devLinks"] < 1: problems.append("development-menu-link")
                if m["scrollWidth"] > m["width"] + 1: problems.append("horizontal-overflow")
                page5xx=[x for x in responses if "/api/" not in x[1]]
                api5xx=[x for x in responses if "/api/" in x[1]]
                if page5xx: problems.append("page-5xx")
                if api5xx: problems.append("api-5xx")
                vr[path]={"metrics":m,"problems":problems,"page5xx":page5xx[:3],"api5xx":api5xx[:3]}
                if problems:
                    out["status"]="FAIL"
                    out["failures"].append({"viewport":label,"route":path,"problems":problems})
                page.remove_listener("response", on_response)
            out["viewports"][label]=vr
            ctx.close()
        br.close()

    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
