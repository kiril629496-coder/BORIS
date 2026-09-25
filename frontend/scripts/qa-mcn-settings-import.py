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
            try:
                login(page)
                page.goto(WEB+"/dashboard/phone?tab="+urllib.parse.quote("Подключение"),wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(1200)
                card=page.locator("button.bp-provider-card").filter(has_text="MCN").first
                if card.count():
                    card.click()
                    page.wait_for_timeout(700)
                ta=page.get_by_label("Вставить настройки MCN целиком")
                if not ta.count():
                    problems.append("mcn-import-textarea-missing")
                else:
                    ta.fill("Registrar: sip.mcn.ru\nOutbound proxy: edge.mcn.ru:5080\nSIP login: qa-user-42\nSIP password: qa-value-42\nНомер MCN: +7 495 222-33-44")
                    page.get_by_role("button",name="Распознать и заполнить",exact=True).click()
                    page.wait_for_timeout(250)
                    values={
                      "registrar":page.get_by_label("Сервер SIP / registrar").input_value(),
                      "proxy":page.get_by_label("Outbound proxy, если указан MCN").input_value(),
                      "login":page.get_by_label("SIP login").input_value(),
                      "credential":page.get_by_label("SIP password").input_value(),
                      "number":page.get_by_label("Ваш номер MCN").input_value(),
                      "textarea":ta.input_value(),
                    }
                    expected={
                      "registrar":"sip.mcn.ru",
                      "proxy":"edge.mcn.ru:5080",
                      "login":"qa-user-42",
                      "credential":"qa-value-42",
                      "number":"+74952223344",
                      "textarea":"",
                    }
                    for k,v in expected.items():
                        if values.get(k)!=v:
                            problems.append("bad-"+k)
                    metrics=page.evaluate("""() => ({
                      overflowX: document.documentElement.scrollWidth > innerWidth + 1,
                      bodyText: document.body.innerText.length,
                      textareaVisible: !![...document.querySelectorAll('textarea')].find(x => x.offsetParent !== null),
                      buttonVisible: [...document.querySelectorAll('button')].some(x => x.textContent?.includes('Распознать и заполнить') && x.offsetParent !== null)
                    })""")
                    if metrics["overflowX"]:
                        problems.append("horizontal-overflow")
                    if not metrics["textareaVisible"]:
                        problems.append("textarea-not-visible")
                    if not metrics["buttonVisible"]:
                        problems.append("parse-button-not-visible")

                    # Numeric SIP logins often look like Russian phone numbers.
                    # Without an explicit DID/number line the importer must not
                    # silently turn the login into the client's Caller ID.
                    number_input=page.get_by_label("Ваш номер MCN")
                    number_input.fill("")
                    ta.fill("Сервер SIP: sip.mcn.ru\nSIP login: 74951234567\nSIP password: qa-value-43")
                    page.get_by_role("button",name="Распознать и заполнить",exact=True).click()
                    page.wait_for_timeout(200)
                    numeric_login_number=number_input.input_value()
                    if numeric_login_number:
                        problems.append("numeric-login-misdetected-as-did")

                    out["viewports"][label]={
                      "values":{
                        "registrar":values["registrar"],
                        "proxy":values["proxy"],
                        "login":values["login"],
                        "credential_present":bool(values["credential"]),
                        "number":values["number"],
                        "textarea_cleared":values["textarea"]=="",
                      },
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
