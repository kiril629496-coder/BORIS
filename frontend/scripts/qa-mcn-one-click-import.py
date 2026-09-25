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
            captured={"onboards":[],"legacy_posts":0,"verify_posts":0}
            try:
                login(page)
                page.goto(WEB+"/dashboard/phone?tab="+urllib.parse.quote("Подключение"),wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(1200)
                card=page.locator("button.bp-provider-card").filter(has_text="MCN").first
                if card.count():
                    card.click()
                    page.wait_for_timeout(700)

                def onboard_handler(route,request):
                    body=request.post_data_json
                    captured["onboards"].append(body)
                    route.fulfill(status=200,content_type="application/json",body=json.dumps({
                        "status":"ok",
                        "atomic":True,
                        "trunk":{"id":987654,"status":"configured_unverified"},
                        "did":{"id":876543,"number_e164":"+74952223344","trunk_id":987654},
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

                ta=page.get_by_label("Вставить настройки MCN целиком")
                mode=page.get_by_label("Тип подключения")
                mode.select_option("ip")
                page.get_by_label("Outbound proxy, если указан MCN").fill("stale.example.invalid:5099")
                page.locator("label").filter(has_text="IP-адреса MCN").locator("input").fill("192.0.2.77/32")
                ta.fill("Number: 74951234567\nUser ID / Account: qa-user-99\nPassword: qa-value-99;\nProxy Server IP / Domain: sip.mcn.ru\nНомер MCN: +7 495 222-33-44")
                page.get_by_role("button",name="Распознать, сохранить и проверить",exact=True).click()
                page.wait_for_timeout(1800)

                body=(captured["onboards"] or [{}])[0]
                trunk=body.get("trunk") or {}
                did=body.get("did") or {}
                if len(captured["onboards"])!=1:
                    problems.append("onboard-post-count-"+str(len(captured["onboards"])))
                if trunk.get("registrar")!="sip.mcn.ru":
                    problems.append("bad-registrar")
                if trunk.get("auth_mode")!="registration":
                    problems.append("ip-to-registration-mode-not-switched")
                if trunk.get("outbound_proxy") not in ("",None):
                    problems.append("stale-proxy-leaked-into-registration")
                if trunk.get("source_ips") not in ([],None):
                    problems.append("stale-source-ip-leaked-into-registration")
                if trunk.get("username")!="qa-user-99":
                    problems.append("bad-login")
                if not trunk.get("password"):
                    problems.append("password-not-sent")
                if trunk.get("password")!="qa-value-99;":
                    problems.append("password-special-chars-changed")
                if trunk.get("allowed_cli")!=["+74952223344"]:
                    problems.append("bad-allowed-cli")
                if did.get("number")!="+74952223344":
                    problems.append("bad-did")
                if captured["legacy_posts"]:
                    problems.append("used-legacy-multi-post")
                if captured["verify_posts"]:
                    problems.append("duplicated-frontend-verify")
                if ta.input_value()!="":
                    problems.append("textarea-not-cleared")

                metrics=page.evaluate("""() => ({
                  overflowX: document.documentElement.scrollWidth > innerWidth + 1,
                  oneClickVisible: [...document.querySelectorAll('button')].some(x => x.textContent?.includes('Распознать, сохранить и проверить') && x.offsetParent !== null)
                })""")
                if metrics["overflowX"]:
                    problems.append("horizontal-overflow")
                if not metrics["oneClickVisible"]:
                    problems.append("one-click-button-not-visible")

                # Reverse mode switch: a current IP-auth settings paste must
                # override stale registration credentials and stale proxy.
                mode.select_option("registration")
                page.get_by_label("Outbound proxy, если указан MCN").fill("stale2.example.invalid:5098")
                page.get_by_label("SIP login").fill("stale-registration-user")
                page.get_by_label("SIP password").fill("stale-registration-secret")
                before_ip=len(captured["onboards"])
                ta.fill("SIP server: sip.mcn.ru\nIP-адреса MCN: 192.0.2.88/32\nНомер MCN: +7 495 777-88-99")
                page.get_by_role("button",name="Распознать, сохранить и проверить",exact=True).click()
                page.wait_for_timeout(1100)
                if len(captured["onboards"])!=before_ip+1:
                    problems.append("registration-to-ip-onboard-missing")
                else:
                    ip_body=captured["onboards"][-1]
                    ip_trunk=ip_body.get("trunk") or {}
                    if ip_trunk.get("auth_mode")!="ip":
                        problems.append("registration-to-ip-mode-not-switched")
                    if ip_trunk.get("source_ips")!=["192.0.2.88/32"]:
                        problems.append("bad-ip-source")
                    if "username" in ip_trunk or "password" in ip_trunk:
                        problems.append("stale-registration-credentials-leaked-into-ip")
                    if ip_trunk.get("outbound_proxy") not in ("",None):
                        problems.append("stale-proxy-leaked-into-ip")

                # Stale form values must never authorize a second atomic save.
                before=len(captured["onboards"])
                page.get_by_label("Ваш номер MCN").fill("+74950000000")
                ta.fill("Number: 74951234567\nUser ID / Account: 74951234567\nPassword: qa-value-no-did\nProxy Server IP / Domain: sip.mcn.ru")
                page.get_by_role("button",name="Распознать, сохранить и проверить",exact=True).click()
                page.wait_for_timeout(350)
                if len(captured["onboards"])!=before:
                    problems.append("stale-number-authorized-auto-save")
                note_text=page.locator(".bp-note").inner_text() if page.locator(".bp-note").count() else ""
                if "в текущей вставке не распознано: номер" not in note_text:
                    problems.append("missing-number-note-not-specific")

                page.get_by_label("SIP login").fill("old-user")
                page.get_by_label("SIP password").fill("old-secret")
                ta.fill("Сервер SIP: sip.mcn.ru\nНомер MCN: +7 495 666-77-88")
                page.get_by_role("button",name="Распознать, сохранить и проверить",exact=True).click()
                page.wait_for_timeout(350)
                if len(captured["onboards"])!=before:
                    problems.append("stale-credentials-authorized-auto-save")
                note_text_2=page.locator(".bp-note").inner_text() if page.locator(".bp-note").count() else ""
                if "SIP логин" not in note_text_2 or "SIP пароль" not in note_text_2:
                    problems.append("missing-credentials-note-not-specific")

                out["viewports"][label]={
                    "onboard_posts":len(captured["onboards"]),
                    "atomic_payload":bool(trunk and did),
                    "credential_sent":bool(trunk.get("password")),
                    "textarea_cleared":ta.input_value()=="",
                    "legacy_posts":captured["legacy_posts"],
                    "duplicate_verify_posts":captured["verify_posts"],
                    "stale_values_blocked":len(captured["onboards"])==before,
                    "metrics":metrics,
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
