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

def setup_payload(ready):
    return {
        "status":"ok",
        "trunks":[{
            "id":991001,"name":"MCN poll QA","auth_mode":"registration",
            "registrar":"sip.mcn.ru","outbound_proxy":None,
            "source_ips":[],"status":"connected" if ready else "configured_unverified",
            "has_username":True,"has_password":True
        }],
        "dids":[{
            "id":991002,"number_e164":"+74950001122","region":"Москва",
            "outbound_cli_enabled":True,"status":"active"
        }],
        "transport":{
            "ready":ready,
            "trunks":[{
                "trunk_id":991001,
                "live_status":"registration_registered" if ready else "registration_pending"
            }]
        },
        "readiness":{
            "ready":ready,
            "steps_completed":3 if ready else 2,
            "steps_total":3,
            "blockers":[] if ready else ["mcn_trunk_not_verified"],
            "primary_next_action":{
                "code":"mcn_ready" if ready else "mcn_registration_self_heal",
                "text":"MCN готов" if ready else "BORIS проверяет регистрацию автоматически"
            }
        }
    }

def main():
    result={"status":"PASS","viewports":{},"failures":[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for w,h,label in [(320,760,"phone-320"),(390,844,"phone-390")]:
            ctx=browser.new_context(viewport={"width":w,"height":h})
            page=ctx.new_page()
            problems=[]
            state={"setup_calls":0,"verify_posts":0}
            try:
                login(page)
                def setup_handler(route,request):
                    state["setup_calls"]+=1
                    ready=state["setup_calls"]>=2
                    route.fulfill(status=200,content_type="application/json",
                                  body=json.dumps(setup_payload(ready),ensure_ascii=False))
                def verify_handler(route,request):
                    state["verify_posts"]+=1
                    route.abort()
                page.route("**/api/mcn/setup?account_id=*",setup_handler)
                page.route("**/api/telephony/provider-config/verify",verify_handler)
                page.goto(WEB+"/dashboard/phone?tab="+urllib.parse.quote("Подключение"),
                          wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(900)
                card=page.locator("button.bp-provider-card").filter(has_text="MCN").first
                if card.count():
                    card.click()
                page.wait_for_timeout(700)
                text_before=page.locator("body").inner_text()
                if "регистрация выполняется" not in text_before and "BORIS проверяет" not in text_before:
                    problems.append("pending-state-not-visible")
                # No user click: wait for the built-in 5s MCN polling cycle.
                page.wait_for_timeout(5600)
                text_after=page.locator("body").inner_text()
                if state["setup_calls"]<2:
                    problems.append("mcn-setup-not-polled")
                if "онлайн · SIP подтверждён" not in text_after:
                    problems.append("registered-state-not-rendered")
                if "готово" not in text_after:
                    problems.append("ready-state-not-rendered")
                if state["verify_posts"]!=0:
                    problems.append("manual-verify-required-by-ui")
                result["viewports"][label]={
                    "setup_calls":state["setup_calls"],
                    "verify_posts":state["verify_posts"],
                    "auto_registered_visible":"онлайн · SIP подтверждён" in text_after,
                    "auto_ready_visible":"готово" in text_after,
                    "problems":problems,
                }
            except Exception as exc:
                problems.append(type(exc).__name__)
                result["viewports"][label]={"problems":problems}
            if problems:
                result["failures"].append({"viewport":label,"problems":problems})
            ctx.close()
        browser.close()
    if result["failures"]:
        result["status"]="FAIL"
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
