#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production browser acceptance for the BORIS first-run journey.

Loads the real public frontend. Only onboarding/factory test state is fulfilled
inside the browser so QA never mutates a real client's onboarding or launches.
"""
from __future__ import annotations
import json
import sys
from urllib.parse import urlparse

sys.path.insert(0, "/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB = "https://boris-ai.pro"
QA_ACCOUNT = "testref_47784"

ANALYSIS = {
    "status": "matched",
    "profile": {
        "company_name": "QA Онбординг",
        "company_niche": "аренда бытовок",
        "city": "Санкт-Петербург",
        "channels": [{"key": "avito", "title": "Авито"}],
    },
    "niche": {"name": "Аренда бытовок", "summary": "Проверочный профиль для UI QA."},
    "buyer_questions": {"intro": "Что важно клиенту", "items": []},
    "channels_compare": {
        "have": [{"key": "avito", "title": "Авито"}],
        "can_add_later": [],
    },
    "problems": [],
    "capabilities_now": [],
    "capabilities_paid": [],
    "materials_missing": [],
    "plan": [{
        "key": "first",
        "order": 1,
        "title": "Подключить рабочий аккаунт",
        "description": "BORIS проверит состояние и подготовит первый рабочий сценарий.",
        "status": "works",
        "requirements": [{"kind": "connection"}],
        "result": {"title": "Рабочий сценарий готов"},
    }],
}

def payload_status(state):
    status = "completed" if state["completed"] else ("in_progress" if state["step"] else "not_started")
    return {
        "status": status,
        "current_step": state["step"],
        "form_version": 1,
        "required_steps": 2,
        "skipped_blocks": [],
        "form_data": dict(state["form"]),
        "completed_at": "2026-09-06T12:00:00" if state["completed"] else None,
    }

def readiness(ready: bool):
    return {
        "status": "ok",
        "all_ready": ready,
        "readiness_pct": 100 if ready else 50,
        "accounts": [],
    }

def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

def install_api_mock(page, state):
    def handler(route, request):
        path = urlparse(request.url).path
        method = request.method.upper()

        if path == "/api/onboarding/status":
            if state["mode"] == "onboarding_error" and state["status_failures"] > 0:
                state["status_failures"] -= 1
                return fulfill(route, {"detail": "qa temporary failure"}, 503)
            return fulfill(route, payload_status(state))

        if path == "/api/onboarding/step" and method in ("PATCH", "POST"):
            body = request.post_data_json or {}
            step = int(body.get("step") or 0)
            data = body.get("data") or {}
            state["form"].update(data)
            state["step"] = max(state["step"], step)
            return fulfill(route, payload_status(state))

        if path == "/api/onboarding/complete" and method == "POST":
            state["step"] = 2
            state["completed"] = True
            return fulfill(route, payload_status(state))

        if path == "/api/onboarding/factory-sync":
            return fulfill(route, {"status": "ok"})

        if path == "/api/onboarding/factory-readiness":
            return fulfill(route, readiness(bool(state["ready"])))

        if path == "/api/onboarding/analysis":
            if state["mode"] == "analysis_error" and state["analysis_failures"] > 0:
                state["analysis_failures"] -= 1
                return fulfill(route, {"detail": {"code": "qa_analysis_unavailable"}}, 503)
            return fulfill(route, ANALYSIS)

        if path == "/api/onboarding/event":
            return fulfill(route, {"status": "ok"})

        if path == "/api/factory/launches":
            if state["mode"] == "launch_error" and state["launch_failures"] > 0:
                state["launch_failures"] -= 1
                return fulfill(route, {"detail": "qa launch state unavailable"}, 503)
            return fulfill(route, {
                "status": "ok",
                "summary": {"total": 0, "launched": 0, "needs_decision": 0, "blocked": 0, "applying": 0},
                "items": [],
            })

        route.continue_()

    page.route("**/api/**", handler)

def login(page):
    page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
    page.locator("input[type=email]").fill(qa_suite.OWNER[0])
    page.locator("input[type=password]").fill(qa_suite.OWNER[1])
    page.get_by_text("Войти", exact=True).first.click()
    page.wait_for_timeout(1800)
    if "/login" in page.url:
        raise RuntimeError("QA login failed")
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)", QA_ACCOUNT)

def metric(page):
    return page.evaluate("""() => ({
      path: location.pathname,
      width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      overflowX: document.documentElement.scrollWidth > innerWidth + 1,
      bodyText: (document.body.innerText || '').trim().length,
      buttons: [...document.querySelectorAll('button')].filter(b => {
        const r=b.getBoundingClientRect(), s=getComputedStyle(b);
        return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
      }).map(b => ({text:(b.innerText||'').trim(), w:Math.round(b.getBoundingClientRect().width), h:Math.round(b.getBoundingClientRect().height)}))
    })""")

def must(body: str, *parts: str):
    missing=[x for x in parts if x not in body]
    if missing:
        raise AssertionError("missing text: " + " | ".join(missing))

def main():
    out={"status":"PASS","journey":{},"error_recovery":{},"ready_result":{},"failures":[]}
    state={
        "mode":"journey","step":0,"completed":False,"form":{},"ready":False,
        "status_failures":0,"analysis_failures":0,"launch_failures":0,
    }
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
            ctx=browser.new_context(viewport={"width":320,"height":760}, service_workers="block")
            page=ctx.new_page()
            install_api_mock(page,state)

            # Real login + real production routing, synthetic onboarding state only.
            login(page)
            page.wait_for_url("**/onboarding", timeout=12000)
            page.wait_for_timeout(500)
            body=page.locator("body").inner_text()
            must(body,"Шаг 1 из 2","Расскажите о компании","Далее")
            m1=metric(page)
            if m1["overflowX"]: raise AssertionError("step1 horizontal overflow")

            page.get_by_placeholder("Например: Бытовки План Б").fill("QA Онбординг")
            page.get_by_placeholder("Например: аренда бытовок").fill("аренда бытовок")
            page.get_by_placeholder("Например: Санкт-Петербург").fill("Санкт-Петербург")
            page.get_by_role("button",name="Далее",exact=True).click()
            page.get_by_text("Шаг 2 из 2",exact=True).wait_for(timeout=7000)
            body=page.locator("body").inner_text()
            must(body,"Где вы уже продаёте","Назад","Готово")
            m2=metric(page)
            if m2["overflowX"]: raise AssertionError("step2 horizontal overflow")

            page.get_by_text("Авито", exact=True).click()
            page.get_by_role("button",name="Готово",exact=True).click()
            page.wait_for_url("**/onboarding/analysis", timeout=10000)
            page.wait_for_timeout(700)
            body=page.locator("body").inner_text()
            must(
                body,
                "Персональный план запуска BORIS",
                "Готовность BORIS",
                "Осталось подключить Авито",
                "От вас сейчас нужно только подключить Авито",
                "После подключения BORIS сам",
                "Перейти к бизнес-сценариям",
            )
            ma=metric(page)
            if ma["overflowX"]: raise AssertionError("analysis horizontal overflow")
            out["journey"]={"step1":m1,"step2":m2,"analysis":ma,"path":page.url}

            page.get_by_role("button",name="Перейти к бизнес-сценариям",exact=True).click()
            page.wait_for_url("**/dashboard/scenarios",timeout=10000)
            out["journey"]["result_route"]=urlparse(page.url).path

            auth_state=ctx.storage_state()
            ctx.close()

            # Opening master failure must recover in one click, in an isolated
            # context so no prior SPA/module cache can mask the synthetic 503.
            s1={
                "mode":"onboarding_error","step":0,"completed":False,"form":{},
                "ready":False,"status_failures":1,"analysis_failures":0,"launch_failures":0,
            }
            c1=browser.new_context(storage_state=auth_state,viewport={"width":320,"height":760},service_workers="block")
            p1=c1.new_page(); install_api_mock(p1,s1)
            p1.goto(WEB+"/onboarding",wait_until="domcontentloaded",timeout=30000)
            p1.get_by_text("Не удалось открыть мастер",exact=True).wait_for(timeout=7000)
            p1.get_by_role("button",name="Попробовать снова",exact=True).click()
            p1.get_by_text("Шаг 1 из 2",exact=True).wait_for(timeout=7000)
            out["error_recovery"]["master"]="PASS"
            c1.close()

            # Analysis failure must recover in one click.
            s2={
                "mode":"analysis_error","step":2,"completed":True,"form":dict(state["form"]),
                "ready":False,"status_failures":0,"analysis_failures":1,"launch_failures":0,
            }
            c2=browser.new_context(storage_state=auth_state,viewport={"width":320,"height":760},service_workers="block")
            p2=c2.new_page(); install_api_mock(p2,s2)
            p2.goto(WEB+"/onboarding/analysis",wait_until="domcontentloaded",timeout=30000)
            p2.get_by_text("Не получилось собрать анализ",exact=True).wait_for(timeout=7000)
            p2.get_by_role("button",name="Попробовать снова",exact=True).click()
            p2.get_by_text("Готовность BORIS",exact=True).wait_for(timeout=7000)
            out["error_recovery"]["analysis"]="PASS"
            c2.close()

            # Launch status: transient failure -> retry -> useful empty state.
            s3={
                "mode":"launch_error","step":2,"completed":True,"form":dict(state["form"]),
                "ready":False,"status_failures":0,"analysis_failures":0,"launch_failures":1,
            }
            c3=browser.new_context(storage_state=auth_state,viewport={"width":320,"height":760},service_workers="block")
            p3=c3.new_page(); install_api_mock(p3,s3)
            p3.goto(WEB+"/dashboard/launch-status",wait_until="domcontentloaded",timeout=30000)
            p3.get_by_role("button",name="Повторить проверку",exact=True).wait_for(timeout=10000)
            p3.get_by_role("button",name="Повторить проверку",exact=True).click()
            p3.get_by_text("Клиентов для запуска пока нет",exact=True).wait_for(timeout=7000)
            add=p3.get_by_role("link",name="Добавить клиента",exact=True)
            if add.get_attribute("href") != "/dashboard/launch-client":
                raise AssertionError("launch empty action target")
            out["error_recovery"]["launch_status"]="PASS"
            c3.close()

            # Ready result state explicitly says what is ready and where to work next.
            s4={
                "mode":"ready","step":2,"completed":True,"form":dict(state["form"]),
                "ready":True,"status_failures":0,"analysis_failures":0,"launch_failures":0,
            }
            c4=browser.new_context(storage_state=auth_state,viewport={"width":320,"height":760},service_workers="block")
            p4=c4.new_page(); install_api_mock(p4,s4)
            p4.goto(WEB+"/onboarding/analysis",wait_until="domcontentloaded",timeout=30000)
            p4.get_by_text("BORIS готов к работе",exact=True).wait_for(timeout=7000)
            body=p4.locator("body").inner_text()
            must(
                body,
                "Результат готов.",
                "рабочие сценарии и следующие действия находятся в разделе «Бизнес-сценарии»",
                "От вас сейчас ничего не требуется.",
                "Открыть бизнес-сценарии",
            )
            mr=metric(p4)
            if mr["overflowX"]: raise AssertionError("ready analysis horizontal overflow")
            out["ready_result"]["phone320"]=mr

            p4.set_viewport_size({"width":1440,"height":900})
            p4.reload(wait_until="domcontentloaded")
            p4.get_by_text("BORIS готов к работе",exact=True).wait_for(timeout=7000)
            md=metric(p4)
            if md["overflowX"]: raise AssertionError("desktop analysis horizontal overflow")
            out["ready_result"]["desktop1440"]=md
            c4.close()

            browser.close()

    except Exception as exc:
        out["status"]="FAIL"
        out["failures"].append(type(exc).__name__ + ": " + str(exc)[:500])

    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
