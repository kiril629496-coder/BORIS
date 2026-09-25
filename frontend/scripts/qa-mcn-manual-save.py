#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,sys,urllib.parse
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
        page.wait_for_timeout(1800)

def main():
    out={"status":"PASS","viewports":{},"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for w,h,label in [(320,760,"phone-320"),(390,844,"phone-390")]:
            ctx=br.new_context(viewport={"width":w,"height":h})
            page=ctx.new_page()
            problems=[]
            captured={"onboard":None,"legacy_posts":0,"verify_posts":0}
            try:
                login(page)
                page.goto(WEB+"/dashboard/phone?tab="+urllib.parse.quote("Подключение"),wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(1200)
                card=page.locator("button.bp-provider-card").filter(has_text="MCN").first
                if card.count():
                    card.click()
                    page.wait_for_timeout(650)

                def onboard_handler(route,request):
                    captured["onboard"]=request.post_data_json
                    route.fulfill(status=200,content_type="application/json",body=json.dumps({
                        "status":"ok",
                        "atomic":True,
                        "trunk":{"id":777001,"status":"configured_unverified"},
                        "did":{"id":777002,"number_e164":"+74953334455","trunk_id":777001},
                        "pjsip_guardian":{"status":"ok","deferred":False},
                        "provider_verification":{"status":"ok","connected":False},
                    }))

                def legacy_handler(route,request):
                    captured["legacy_posts"]+=1
                    route.abort()

                def verify_handler(route,request):
                    captured["verify_posts"]+=1
                    route.abort()

                page.route("**/api/mcn/onboard",onboard_handler)
                page.route("**/api/mcn/trunks",legacy_handler)
                page.route("**/api/mcn/dids",legacy_handler)
                page.route("**/api/telephony/provider-config/verify",verify_handler)

                page.get_by_label("Сервер SIP / registrar").fill("sip.mcn.ru")
                page.get_by_label("Outbound proxy, если указан MCN").fill("edge.mcn.ru:5090")
                page.get_by_label("SIP login").fill("qa-manual-user")
                page.get_by_label("SIP password").fill("qa-manual-secret")
                page.get_by_label("Ваш номер MCN").fill("+74953334455")
                page.get_by_role("button",name="Сохранить MCN и проверить",exact=True).click()
                page.wait_for_timeout(1700)

                body=captured["onboard"] or {}
                trunk=body.get("trunk") or {}
                did=body.get("did") or {}
                if not body:
                    problems.append("manual-onboard-post-missing")
                if body.get("account_id") in (None,""):
                    problems.append("manual-account-missing")
                if trunk.get("registrar")!="sip.mcn.ru":
                    problems.append("manual-bad-registrar")
                if trunk.get("outbound_proxy")!="edge.mcn.ru:5090":
                    problems.append("manual-bad-proxy")
                if trunk.get("username")!="qa-manual-user":
                    problems.append("manual-bad-login")
                if not trunk.get("password"):
                    problems.append("manual-password-not-sent")
                if trunk.get("allowed_cli")!=["+74953334455"]:
                    problems.append("manual-bad-allowed-cli")
                if did.get("number")!="+74953334455":
                    problems.append("manual-bad-did")
                if captured["legacy_posts"]:
                    problems.append("manual-used-legacy-multi-post")
                if captured["verify_posts"]:
                    problems.append("manual-duplicated-frontend-verify")

                out["viewports"][label]={
                    "onboard_posted":bool(body),
                    "atomic_payload":bool(trunk and did),
                    "credential_sent":bool(trunk.get("password")),
                    "legacy_posts":captured["legacy_posts"],
                    "duplicate_verify_posts":captured["verify_posts"],
                    "problems":problems,
                }
            except Exception as exc:
                problems.append(type(exc).__name__)
                out["viewports"][label]={"problems":problems}
            if problems:
                out["failures"].append({"viewport":label,"problems":problems})
            ctx.close()
        br.close()
    if out["failures"]:
        out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
