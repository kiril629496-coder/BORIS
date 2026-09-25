#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production browser gate for ownerless BORIS onboarding.

Loads real https://boris-ai.pro assets, but intercepts every onboarding write and
state API so the audit never creates users, accounts or client data. It verifies
the post-verification route, two-step wizard, result/readiness screen, retry
states and launch-status empty/error recovery on phone and desktop.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, "/root/BORIS/backend")
import qa_suite

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

WEB = "https://boris-ai.pro"
VIEWPORTS = (
    ("phone-320", {"width": 320, "height": 760}),
    ("desktop-1440", {"width": 1440, "height": 900}),
)


def fulfill(route, body: Any, status: int = 200):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(body, ensure_ascii=False, default=str),
    )


def metrics(page):
    return page.evaluate(
        """() => {
          const root=document.documentElement;
          const visible=(el)=>{
            const s=getComputedStyle(el), r=el.getBoundingClientRect();
            if(s.display==='none' || s.visibility==='hidden' ||
               Number(s.opacity)===0 || r.width<=0 || r.height<=0) return false;
            // Closed mobile sidebar is intentionally translated fully outside
            // the canvas. It is not a horizontal-layout defect.
            if(r.right<=0 || r.left>=innerWidth) return false;
            return true;
          };
          const offscreen=[];
          for(const el of document.querySelectorAll('button,a,input,select,textarea')){
            if(!visible(el)) continue;
            const r=el.getBoundingClientRect();
            if(r.right>innerWidth+2 || r.left<-2){
              offscreen.push({
                tag:el.tagName,
                text:(el.textContent||el.getAttribute('placeholder')||'').trim().slice(0,60),
                left:Math.round(r.left), right:Math.round(r.right), width:Math.round(r.width)
              });
              if(offscreen.length>=8) break;
            }
          }
          return {
            path:location.pathname,
            width:innerWidth,
            scrollWidth:root.scrollWidth,
            overflowX:root.scrollWidth>innerWidth+1,
            bodyText:(document.body.innerText||'').trim().length,
            offscreen:offscreen
          };
        }"""
    )


def assert_layout(out, label, stage, page):
    m = metrics(page)
    out["checks"].append({"viewport": label, "stage": stage, "metrics": m})
    if m["overflowX"]:
        out["failures"].append(
            {"viewport": label, "stage": stage, "problem": f"overflow:{m['scrollWidth']}>{m['width']}"}
        )
    if m["offscreen"]:
        out["failures"].append(
            {"viewport": label, "stage": stage, "problem": "offscreen-controls", "items": m["offscreen"]}
        )
    if m["bodyText"] < 40:
        out["failures"].append(
            {"viewport": label, "stage": stage, "problem": f"empty:{m['bodyText']}"}
        )


def text_required(out, label, stage, page, text):
    count = page.get_by_text(text, exact=False).count()
    if count < 1:
        out["failures"].append(
            {"viewport": label, "stage": stage, "problem": "text-missing", "text": text}
        )


def run_viewport(browser, label, viewport, out):
    print(f"QA_STAGE {label} START", flush=True)
    ctx = browser.new_context(viewport=viewport)
    page = ctx.new_page()
    page.set_default_timeout(6000)
    page.set_default_navigation_timeout(15000)

    state = {
        "onboarding_status": "normal",
        "analysis_error": False,
        "factory_ready": False,
        "launch_mode": "empty",
    }

    # Fake post-registration verification response. No user is created.
    page.route(
        "**/api/auth/verify-email",
        lambda route: fulfill(
            route,
            {
                "status": "ok",
                "access_token": "qa-ownerless-onboarding-token",
                "user": {
                    "id": 999999,
                    "email": "qa-onboarding@example.invalid",
                    "role": "owner",
                    "status": "active",
                    "account_id": None,
                },
            },
        ),
    )

    def status_handler(route):
        mode = state["onboarding_status"]
        if mode == "fail":
            fulfill(route, {"detail": "qa forced status failure"}, 503)
            return
        if mode == "completed":
            fulfill(
                route,
                {
                    "status": "completed",
                    "current_step": 2,
                    "form_version": 1,
                    "required_steps": 2,
                    "skipped_blocks": [],
                    "form_data": {},
                    "completed_at": "2026-09-06T00:00:00",
                },
            )
            return
        fulfill(
            route,
            {
                "status": "not_started",
                "current_step": 0,
                "form_version": 1,
                "required_steps": 2,
                "skipped_blocks": [],
                "form_data": {},
                "completed_at": None,
            },
        )

    page.route("**/api/onboarding/status", status_handler)

    def step_handler(route):
        payload = json.loads(route.request.post_data or "{}")
        step = int(payload.get("step") or 0)
        fulfill(
            route,
            {
                "status": "in_progress",
                "current_step": step,
                "form_version": 1,
                "required_steps": 2,
                "skipped_blocks": [],
                "form_data": payload.get("data") or {},
                "completed_at": None,
            },
        )

    page.route("**/api/onboarding/step", step_handler)
    page.route(
        "**/api/onboarding/complete",
        lambda route: fulfill(
            route,
            {
                "status": "completed",
                "current_step": 2,
                "form_version": 1,
                "required_steps": 2,
                "skipped_blocks": [],
                "form_data": {},
                "completed_at": "2026-09-06T00:00:00",
            },
        ),
    )
    page.route("**/api/onboarding/factory-sync?**", lambda route: fulfill(route, {"status": "ok"}))
    page.route("**/api/onboarding/event", lambda route: fulfill(route, {"status": "ok"}))

    def analysis_handler(route):
        if state["analysis_error"]:
            fulfill(route, {"detail": {"code": "qa_analysis_failure"}}, 503)
            return
        fulfill(
            route,
            {
                "status": "matched",
                "profile": {
                    "company_name": "QA Компания",
                    "company_niche": "ремонт",
                    "city": "Москва",
                    "website": "",
                    "channels": [{"key": "avito", "title": "Авито"}],
                },
                "niche": {"name": "Ремонт", "summary": "Проверенный профиль ниши"},
                "buyer_questions": {"intro": "", "items": []},
                "channels_compare": {
                    "have": [{"key": "avito", "title": "Авито"}],
                    "can_add_later": [],
                },
                "problems": [],
                "capabilities_now": [
                    {"key": "analysis", "title": "Анализ", "description": "BORIS анализирует данные", "status": "works"}
                ],
                "plan": [
                    {
                        "key": "start",
                        "order": 1,
                        "title": "Запустить сценарий",
                        "description": "Первое рабочее действие",
                        "status": "works",
                        "requirements": [],
                        "result": {"title": "Рабочий сценарий"},
                    }
                ],
                "capabilities_paid": [],
                "materials_missing": [],
            },
        )

    page.route("**/api/onboarding/analysis", analysis_handler)

    def readiness_handler(route):
        ready = bool(state["factory_ready"])
        fulfill(
            route,
            {
                "status": "ok",
                "accounts": [] if not ready else [
                    {
                        "account_id": "demo_1",
                        "state": "ready",
                        "launch_status": "LAUNCHED",
                        "readiness_pct": 100,
                        "human_questions": 0,
                    }
                ],
                "total": 0 if not ready else 1,
                "ready": 0 if not ready else 1,
                "blocked": 0,
                "all_ready": ready,
            },
        )

    page.route("**/api/onboarding/factory-readiness", readiness_handler)

    # Dashboard shell dependencies for launch-status error/empty state QA.
    page.route(
        "**/api/auth/me",
        lambda route: fulfill(
            route,
            {
                "status": "ok",
                "user": {
                    "id": 999999,
                    "email": "qa-onboarding@example.invalid",
                    "role": "owner",
                    "status": "active",
                    "account_id": "demo_1",
                },
            },
        ),
    )
    page.route(
        "**/api/accounts/list",
        lambda route: fulfill(
            route,
            {"accounts": [{"account_id": "demo_1", "name": "QA клиент", "role": "owner"}]},
        ),
    )

    def launches_handler(route):
        if state["launch_mode"] == "fail":
            fulfill(route, {"detail": "qa forced launch failure"}, 503)
            return
        if state["launch_mode"] == "mixed":
            fulfill(
                route,
                {
                    "status": "ok",
                    "summary": {
                        "total": 2,
                        "launched": 1,
                        "needs_decision": 0,
                        "blocked": 1,
                        "applying": 0,
                    },
                    "items": [
                        {
                            "account_id": "testref_47784",
                            "launch_status": "LAUNCHED",
                            "launch_ready": True,
                            "readiness_pct": 100,
                            "pending_decisions": 0,
                            "approved_decisions": 1,
                            "rejected_decisions": 0,
                            "required_schedules": 1,
                            "active_schedules": 1,
                            "derived_values": 5,
                            "health": {
                                "automation": {
                                    "required": 1,
                                    "active": 1,
                                    "items": [
                                        {
                                            "field": "schedule.autopilot",
                                            "job_kind": "autopilot",
                                            "state": "active",
                                            "active": True,
                                            "cron_expr": "*/5 * * * *",
                                        }
                                    ],
                                }
                            },
                            "blockers": [],
                        },
                        {
                            "account_id": "missing_demo",
                            "launch_status": "NOT_ZERO_TOUCH_READY",
                            "launch_ready": False,
                            "readiness_pct": 55,
                            "pending_decisions": 0,
                            "approved_decisions": 0,
                            "rejected_decisions": 0,
                            "required_schedules": 1,
                            "active_schedules": 0,
                            "derived_values": 2,
                            "health": {"automation": {"required": 1, "active": 0, "items": []}},
                            "blockers": [
                                {
                                    "code": "NEED_AVITO",
                                    "field": "avito",
                                    "title": "Подключите Авито",
                                    "action": "Добавьте доступ один раз",
                                    "human_required": True,
                                },
                                {
                                    "code": "AUTO_CHECK",
                                    "field": "health",
                                    "title": "BORIS проверяет рабочий контур",
                                    "action": "Проверка выполняется автоматически",
                                    "human_required": False,
                                },
                            ],
                        },
                    ],
                },
            )
            return
        fulfill(
            route,
            {
                "status": "ok",
                "summary": {
                    "total": 0,
                    "launched": 0,
                    "needs_decision": 0,
                    "blocked": 0,
                    "applying": 0,
                },
                "items": [],
            },
        )

    page.route("**/api/factory/launches", launches_handler)

    # Post-registration path: verify-email -> resolve onboarding -> /onboarding.
    page.add_init_script(
        """localStorage.setItem('boris_verification_id','qa-verification-id');
localStorage.setItem('boris_verification_email','q***@example.invalid');"""
    )
    print(f"QA_STAGE {label} VERIFY", flush=True)
    page.goto(WEB + "/verify", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(400)
    cells = page.locator("input.boris-code-cell")
    if cells.count() != 6:
        out["failures"].append(
            {"viewport": label, "stage": "verify", "problem": f"code-cells:{cells.count()}"}
        )
    else:
        for i, digit in enumerate("123456"):
            cells.nth(i).fill(digit)
    assert_layout(out, label, "verify", page)
    page.get_by_text("Подтвердить", exact=True).click()
    try:
        page.wait_for_url("**/onboarding", timeout=8000)
    except PlaywrightTimeoutError:
        body = page.locator("body").inner_text(timeout=2000)
        out["failures"].append(
            {
                "viewport": label,
                "stage": "post-verify",
                "problem": "did-not-route-to-onboarding",
                "url": page.url,
                "body": body[:700],
            }
        )
    page.get_by_text("Шаг 1 из 2", exact=False).wait_for(timeout=6000)
    text_required(out, label, "step1", page, "Шаг 1 из 2")
    assert_layout(out, label, "step1", page)

    # Step 1 has one forward CTA.
    if page.get_by_text("Далее", exact=True).count() != 1:
        out["failures"].append({"viewport": label, "stage": "step1", "problem": "main-cta-next"})
    page.locator('input[placeholder="Например: Бытовки План Б"]').fill("QA Компания")
    page.locator('input[placeholder="Например: аренда бытовок"]').fill("Ремонт квартир")
    page.locator('input[placeholder="Например: Санкт-Петербург"]').fill("Москва")
    page.get_by_text("Далее", exact=True).click()
    page.get_by_text("Шаг 2 из 2", exact=False).wait_for(timeout=5000)
    assert_layout(out, label, "step2", page)
    if page.get_by_text("Готово", exact=True).count() != 1:
        out["failures"].append({"viewport": label, "stage": "step2", "problem": "main-cta-done"})
    avito = page.locator("label").filter(has_text="Авито").locator('input[type="checkbox"]')
    if avito.count() == 1:
        avito.check()
    else:
        out["failures"].append({"viewport": label, "stage": "step2", "problem": "avito-checkbox"})
    page.get_by_text("Готово", exact=True).click()
    try:
        page.wait_for_url("**/onboarding/analysis", timeout=8000)
    except PlaywrightTimeoutError:
        out["failures"].append(
            {"viewport": label, "stage": "complete", "problem": "analysis-route-timeout", "url": page.url}
        )

    # Not-ready result: show connected/missing and BORIS/client responsibility.
    page.get_by_text("Готовность BORIS", exact=False).wait_for(timeout=6000)
    page.get_by_text("Осталось подключить Авито", exact=False).wait_for(timeout=6000)
    text_required(out, label, "analysis-not-ready", page, "Осталось подключить Авито")
    text_required(out, label, "analysis-not-ready", page, "Нужно подключить аккаунт")
    text_required(out, label, "analysis-not-ready", page, "После подключения BORIS сам:")
    text_required(out, label, "analysis-not-ready", page, "Перейти к бизнес-сценариям")
    text_required(out, label, "analysis-not-ready", page, "статус обновится автоматически")
    assert_layout(out, label, "analysis-not-ready", page)

    # Ready result: explicitly say result is ready and where to open it.
    print(f"QA_STAGE {label} ANALYSIS_READY", flush=True)
    state["factory_ready"] = True
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.get_by_text("BORIS готов к работе", exact=False).wait_for(timeout=5000)
    text_required(out, label, "analysis-ready", page, "Результат готов")
    text_required(out, label, "analysis-ready", page, "Открыть бизнес-сценарии")
    text_required(out, label, "analysis-ready", page, "Подключено")
    text_required(out, label, "analysis-ready", page, "Готов к работе")
    assert_layout(out, label, "analysis-ready", page)

    # Error state must contain a working retry action.
    print(f"QA_STAGE {label} ONBOARDING_ERROR", flush=True)
    state["onboarding_status"] = "fail"
    page.goto(WEB + "/onboarding?qa_error=1", wait_until="domcontentloaded", timeout=15000)
    page.get_by_text("Не удалось открыть мастер", exact=False).wait_for(timeout=5000)
    text_required(out, label, "onboarding-error", page, "Попробовать снова")
    assert_layout(out, label, "onboarding-error", page)
    state["onboarding_status"] = "normal"
    page.get_by_text("Попробовать снова", exact=True).click()
    page.get_by_text("Шаг 1 из 2", exact=False).wait_for(timeout=5000)

    # Analysis error must also recover without owner intervention.
    print(f"QA_STAGE {label} ANALYSIS_ERROR", flush=True)
    state["analysis_error"] = True
    state["factory_ready"] = False
    page.goto(WEB + "/onboarding/analysis?qa_error=1", wait_until="domcontentloaded", timeout=15000)
    page.get_by_text("Не получилось собрать анализ", exact=False).wait_for(timeout=5000)
    text_required(out, label, "analysis-error", page, "Попробовать снова")
    state["analysis_error"] = False
    page.get_by_text("Попробовать снова", exact=True).click()
    page.get_by_text("Готовность BORIS", exact=False).wait_for(timeout=5000)

    # Launch status lives under the authenticated dashboard. Use the existing
    # QA owner login here (read-only), while keeping the launch API itself mocked.
    print(f"QA_STAGE {label} LAUNCH_STATES", flush=True)
    ctx.close()
    dash_ctx = browser.new_context(viewport=viewport)
    dash = dash_ctx.new_page()
    dash.set_default_timeout(6000)
    dash.set_default_navigation_timeout(15000)
    dash.goto(WEB + "/login", wait_until="domcontentloaded", timeout=15000)
    if "/login" in dash.url:
        dash.locator("input[type=email]").fill(qa_suite.OWNER[0])
        dash.locator("input[type=password]").fill(qa_suite.OWNER[1])
        dash.get_by_text("Войти", exact=True).first.click()
        dash.wait_for_timeout(1800)
    if "/login" in dash.url:
        out["failures"].append({
            "viewport": label,
            "stage": "launch-auth",
            "problem": "qa-login-failed",
        })
        dash_ctx.close()
        return
    dash.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)", "testref_47784")
    dash.route("**/api/onboarding/status", lambda route: fulfill(route, {
        "status": "completed",
        "current_step": 2,
        "form_version": 1,
        "required_steps": 2,
        "skipped_blocks": [],
        "form_data": {},
        "completed_at": "2026-09-06T00:00:00",
    }))
    dash.route("**/api/factory/launches", launches_handler)

    state["launch_mode"] = "fail"
    dash.goto(WEB + "/dashboard/launch-status", wait_until="domcontentloaded", timeout=15000)
    try:
        dash.get_by_text("Повторить проверку", exact=False).wait_for(timeout=8000)
    except PlaywrightTimeoutError:
        body = dash.locator("body").inner_text(timeout=2000)
        out["failures"].append({
            "viewport": label,
            "stage": "launch-error",
            "problem": "retry-not-visible",
            "url": dash.url,
            "body": body[:900],
        })
        dash_ctx.close()
        return
    assert_layout(out, label, "launch-error", dash)
    state["launch_mode"] = "empty"
    dash.get_by_text("Повторить проверку", exact=True).click()
    dash.get_by_text("Клиентов для запуска пока нет", exact=False).wait_for(timeout=5000)
    text_required(out, label, "launch-empty", dash, "Добавить клиента")
    link = dash.get_by_text("Добавить клиента", exact=True)
    if link.count() != 1 or link.get_attribute("href") != "/dashboard/launch-client":
        out["failures"].append(
            {"viewport": label, "stage": "launch-empty", "problem": "add-client-action"}
        )
    assert_layout(out, label, "launch-empty", dash)

    # Normal dashboard state must separate what is connected, what is missing,
    # and who acts next: the client or BORIS.
    state["launch_mode"] = "mixed"
    dash.get_by_text("Обновить", exact=True).click()
    dash.get_by_text("Подключите Авито", exact=False).wait_for(timeout=5000)
    text_required(out, label, "launch-mixed", dash, "✓ BORIS обслуживает аккаунт автоматически")
    text_required(out, label, "launch-mixed", dash, "Подключите Авито")
    text_required(out, label, "launch-mixed", dash, "Нужно от вас")
    text_required(out, label, "launch-mixed", dash, "BORIS проверяет рабочий контур")
    text_required(out, label, "launch-mixed", dash, "Проверка выполняется автоматически")
    assert_layout(out, label, "launch-mixed", dash)
    dash_ctx.close()


def main():
    out = {"status": "PASS", "checks": [], "failures": []}
    requested = str(os.getenv("QA_VIEWPORT") or "").strip()
    viewports = [x for x in VIEWPORTS if not requested or x[0] == requested]
    if not viewports:
        raise SystemExit("Unknown QA_VIEWPORT=" + requested)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label, viewport in viewports:
            try:
                run_viewport(browser, label, viewport, out)
            except Exception as exc:
                out["failures"].append(
                    {
                        "viewport": label,
                        "stage": "unhandled",
                        "problem": f"{type(exc).__name__}:{str(exc)[:300]}",
                    }
                )
        browser.close()
    if out["failures"]:
        out["status"] = "FAIL"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
