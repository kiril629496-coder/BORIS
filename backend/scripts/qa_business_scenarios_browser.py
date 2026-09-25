#!/root/BORIS/backend/venv/bin/python
# -*- coding: utf-8 -*-
"""Read-only browser proof for all 13 BORIS business-scenario detail surfaces.

Only the dedicated entitlement-enabled qamulti_10473 QA account is mutated:
scenario rows are started/reused and then cancelled. No scenario action button,
publication, paid generation, or external owner-approved action is clicked.
"""
from __future__ import annotations

import fcntl
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/BORIS/backend")
import qa_suite as q
import requests
from playwright.sync_api import sync_playwright

QA_ACCOUNT = "qamulti_10473"
REPORT = Path("/root/BORIS/backend/images/qa/scenario_browser_latest.json")

INPUTS = {
    "avito_start": {"niche": "QA кухни", "city": "Москва"},
    "more_leads": {"city": "Москва", "direction": "QA кухни", "target_leads": 3, "max_cpl": 500},
    "conversion_ab": {"max_bid": 150, "test_size": 30, "measurement_days": 3},
    "revive": {"limit": 3, "touch_price": "нет"},
    "new_niche": {"product": "QA кухни", "region": "Москва", "check": 100000, "model": "производство"},
    "new_city": {"city": "Москва", "direction": "QA кухни"},
    "find_problem": {"target": "заявки"},
    "season": {"period": "осень", "priority": "QA кухни"},
    "social": {"platforms": "Telegram + VK", "goal": "продажи", "frequency": "3 раза в неделю"},
    "automate_sales": {"goal": "получить телефон", "schedule": "09:00-19:00"},
    "website": {"goal": "получать заявки", "format": "лендинг"},
    "reactivation": {"priority": "всех"},
    "improve_sales": {"metric": "конверсию в продажу"},
}


_AUTH_TOKEN = None


def auth_headers():
    """Authenticate through the public production path with bounded retries.

    qa_suite.H() logs in against 127.0.0.1:8000, which is intentionally allowed
    for its local QA. This browser gate must survive a safe rolling backend
    restart, so both login and scenario requests use the same public route.
    """
    global _AUTH_TOKEN
    if _AUTH_TOKEN:
        return {"Authorization": "Bearer " + _AUTH_TOKEN}
    last = None
    for attempt in range(4):
        try:
            response = requests.post(
                q.WEB + "/api/auth/login",
                json={"email": q.OWNER[0], "password": q.OWNER[1]},
                timeout=30,
            )
            last = response
            if response.status_code == 200:
                _AUTH_TOKEN = response.json()["access_token"]
                return {"Authorization": "Bearer " + _AUTH_TOKEN}
        except requests.RequestException as exc:
            last = exc
        if attempt < 3:
            time.sleep(2)
    if isinstance(last, Exception):
        raise last
    raise RuntimeError(
        "public QA login failed: HTTP %s" % getattr(last, "status_code", "unknown")
    )


def api(method: str, path: str, **kwargs):
    """Rolling-safe public request. Four bounded retries, never an endless loop."""
    last = None
    for attempt in range(4):
        try:
            last = requests.request(
                method,
                q.WEB + path,
                headers=auth_headers(),
                timeout=60,
                **kwargs,
            )
            if last.status_code not in (502, 503, 504):
                return last
        except requests.RequestException as exc:
            last = exc
        if attempt < 3:
            time.sleep(2)
    if isinstance(last, Exception):
        raise last
    return last


def browser_login(page):
    page.goto(q.WEB + "/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(500)
    if "/login" not in page.url:
        return True
    page.locator("input[type=email]").fill(q.OWNER[0])
    page.locator("input[type=password]").fill(q.OWNER[1])
    page.get_by_text("Войти", exact=True).first.click(timeout=8000)
    page.wait_for_timeout(2200)
    return "/login" not in page.url


def wait_detail_ready(page, retry_reload=True):
    """Wait for hydrated scenario detail; one bounded reload tolerates deploy race."""
    progress = page.locator('[data-testid="scenario-progress"]')
    try:
        progress.wait_for(state="visible", timeout=7000)
        return True
    except Exception:
        if not retry_reload:
            return False
    try:
        page.reload(wait_until="domcontentloaded", timeout=30000)
        progress = page.locator('[data-testid="scenario-progress"]')
        progress.wait_for(state="visible", timeout=7000)
        return True
    except Exception:
        return False


def main():
    lock_fp = Path("/tmp/boris-scenario-browser-qa.lock").open("w")
    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"status": "FAIL", "reason": "qa_already_running"}, ensure_ascii=False))
        return 2

    results = []
    scenario_ids = []
    failures = []
    catalog_browser = {}
    try:
        # Clean only interrupted QA leftovers in the dedicated account.
        cat = api("GET", "/api/home/scenarios", params={"account_id": QA_ACCOUNT})
        if cat.status_code == 200:
            for row in cat.json().get("mine") or []:
                sid = row.get("id")
                if sid:
                    api(
                        "POST",
                        "/api/home/scenario/cancel",
                        json={"account_id": QA_ACCOUNT, "scenario_id": sid},
                    )

        catalog_json = cat.json() if cat.status_code == 200 else {}
        catalog_by_type = {
            row.get("scenario_type"): row
            for row in (catalog_json.get("available") or [])
            if row.get("scenario_type")
        }

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            if not browser_login(page):
                failures.append({"stage": "browser_login", "url": page.url})
            page.evaluate(
                "(a)=>localStorage.setItem('boris_currentAccount',a)",
                QA_ACCOUNT,
            )

            # The catalog itself must guide the owner instead of forcing a blind choice.
            page.goto(
                q.WEB + f"/dashboard/scenarios?account_id={QA_ACCOUNT}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(1100)
            catalog_body = page.locator("body").inner_text()
            catalog_cards = page.locator('[data-testid="scenarios-available"]').locator(":scope > *").count()
            recommendation_count = page.locator('[data-testid="scenario-recommended"]').count()
            soon_count = page.locator('[data-testid="scenarios-soon"]').count()
            catalog_browser.update({
                "cards": catalog_cards,
                "recommendation": recommendation_count,
                "soon_sections": soon_count,
                "recommended_text": "BORIS рекомендует начать отсюда" in catalog_body,
            })
            if not (
                catalog_cards == len(INPUTS)
                and recommendation_count == 1
                and soon_count == 0
                and catalog_browser["recommended_text"]
            ):
                failures.append({
                    "stage": "catalog_guidance_contract",
                    "evidence": dict(catalog_browser),
                })

            # Start every scenario through the real UI wizard, then prove the
            # persisted workflow, owner/automatic responsibility and guide state.
            for scenario_type, input_data in INPUTS.items():
                result = {"scenario_type": scenario_type}
                try:
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.goto(
                        q.WEB + f"/dashboard/scenarios?account_id={QA_ACCOUNT}",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    page.wait_for_timeout(800)

                    start_button = page.locator(
                        f'[data-testid="start-{scenario_type}"]'
                    )
                    try:
                        start_button.wait_for(state="visible", timeout=10000)
                    except Exception:
                        # One bounded reload handles a slow catalog fetch/hydration.
                        # Never loop: a second miss is a real QA failure.
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(1200)
                        start_button = page.locator(
                            f'[data-testid="start-{scenario_type}"]'
                        )
                    if start_button.count() != 1:
                        raise RuntimeError(
                            f"start button not found for {scenario_type}"
                        )
                    start_button.click(timeout=8000)
                    wizard = page.locator('[data-testid="wizard"]')
                    wizard.wait_for(state="visible", timeout=8000)

                    fields = (catalog_by_type.get(scenario_type) or {}).get("fields") or []
                    controls = wizard.locator("input, select")
                    if controls.count() != len(fields):
                        raise RuntimeError(
                            f"wizard controls mismatch: {controls.count()} != {len(fields)}"
                        )
                    for idx, field in enumerate(fields):
                        value = input_data.get(field.get("key"), "")
                        control = controls.nth(idx)
                        if field.get("type") == "select":
                            if str(value):
                                control.select_option(str(value))
                        else:
                            control.fill(str(value))

                    wizard.locator('[data-testid="wizard-start"]').click(timeout=8000)
                    page.wait_for_url(
                        re.compile(r".*/dashboard/scenarios/\d+.*"),
                        timeout=30000,
                    )
                    if not wait_detail_ready(page, retry_reload=True):
                        raise RuntimeError("scenario detail did not hydrate after start")
                    match = re.search(r"/dashboard/scenarios/(\d+)", page.url)
                    if not match:
                        raise RuntimeError(f"scenario id missing in url: {page.url}")
                    sid = int(match.group(1))
                    scenario_ids.append(sid)
                    result["scenario_id"] = sid
                    result["started_in_browser"] = True

                    body = page.locator("body").inner_text()
                    progress = page.locator('[data-testid="scenario-progress"]')
                    workflow = page.locator('[data-testid="scenario-business-workflow"]')
                    steps = page.locator('[data-testid="scenario-step"]')
                    total = steps.count()
                    result["total"] = total

                    # Persisted server projection is the source of truth for
                    # responsibility/blocker/result. The browser must agree
                    # with it; otherwise a pretty Guide can lie about who acts.
                    server_response = api(
                        "GET",
                        f"/api/home/scenario/{sid}",
                        params={"account_id": QA_ACCOUNT},
                    )
                    if server_response.status_code != 200:
                        raise RuntimeError(
                            f"scenario readback HTTP {server_response.status_code}"
                        )
                    server_scenario = server_response.json().get("scenario") or {}
                    server_steps = (server_scenario.get("progress") or {}).get("steps") or []
                    server_current = next(
                        (
                            x for x in server_steps
                            if x.get("status") in ("current", "attention")
                        ),
                        next(
                            (
                                x for x in server_steps
                                if str(x.get("status") or "")
                                not in ("completed", "skipped", "later")
                            ),
                            None,
                        ),
                    )
                    continuation = server_scenario.get("continuation") or {}
                    workflow_server = server_scenario.get("workflow") or {}
                    result["server_state"] = {
                        "status": server_scenario.get("status"),
                        "current_step": (server_current or {}).get("key"),
                        "current_auto": bool((server_current or {}).get("auto")),
                        "current_owner_required": bool(
                            (server_current or {}).get("owner_required")
                        ),
                        "continuation_state": continuation.get("state"),
                        "continuation_reason": continuation.get("reason"),
                        "continuation_owner": continuation.get("owner_action_required"),
                        "current_work": workflow_server.get("current_work"),
                        "owner_required": workflow_server.get("owner_required"),
                        "result": workflow_server.get("result"),
                        "next_action": workflow_server.get("next_action"),
                    }

                    result["desktop"] = {
                        "root": page.locator('[data-testid="scenario-root"]').count(),
                        "progress": progress.count(),
                        "workflow": workflow.count(),
                        "steps": total,
                        "owner_label": "Что требуется от владельца" in body,
                        "next_label": "Следующий шаг:" in body,
                        "result_label": "Текущий результат" in body,
                        "unavailable": "Сценарий недоступен" in body,
                    }
                    before = progress.inner_text() if progress.count() else ""
                    m1 = re.search(r"Выполнено\s+(\d+)\s+из\s+(\d+)", before)

                    guide = page.locator('[data-testid="scenario-guide"]')
                    guide_count = guide.count()
                    guide_key = guide.get_attribute("data-guide-key") if guide_count else None
                    guide_text = guide.inner_text() if guide_count else ""
                    primary = (
                        page.locator('[data-testid="scenario-guide-primary"]').inner_text()
                        if guide_count else ""
                    )
                    if "BORIS делает сам" in guide_text:
                        expected_primary = "Проверить статус"
                    elif "Нужно ваше действие" in guide_text:
                        expected_primary = "Сделать сейчас"
                    elif "Результат готов" in guide_text:
                        expected_primary = "Посмотреть результат"
                    else:
                        expected_primary = "Продолжить"

                    if (server_scenario.get("progress") or {}).get("done", 0) >= (
                        (server_scenario.get("progress") or {}).get("total", 0) or 1
                    ):
                        expected_guide_state = "Результат готов"
                    elif (server_current or {}).get("owner_required"):
                        expected_guide_state = "Нужно ваше действие"
                    elif (server_current or {}).get("auto"):
                        expected_guide_state = "BORIS делает сам"
                    else:
                        expected_guide_state = "Следующий шаг"
                    responsibility_ok = expected_guide_state in guide_text
                    result["responsibility_ok"] = responsibility_ok
                    result["expected_guide_state"] = expected_guide_state

                    guide_action_ok = bool(
                        guide_count == 1
                        and primary.strip() == expected_primary
                        and len(
                            page.locator('[data-testid="scenario-guide-text"]').inner_text().strip()
                        ) >= 8
                    )

                    # GUIDE_PRIMARY_RUNTIME_V1: the owner-facing primary CTA must
                    # do something real. Revive/settings is the canonical inline
                    # owner step: it has no route/tab, so «Сделать сейчас» must
                    # focus the safe inline action instead of being a no-op. We
                    # deliberately do NOT click that focused action here.
                    guide_primary_runtime_ok = True
                    if scenario_type == "revive" and primary.strip() == "Сделать сейчас":
                        page.locator('[data-testid="scenario-guide-primary"]').click(timeout=5000)
                        page.wait_for_timeout(250)
                        guide_primary_runtime_ok = bool(
                            page.evaluate(
                                """() => {
                                  const step=document.querySelector('[data-scenario-step-key="settings"]');
                                  const active=document.activeElement;
                                  return !!(step && active && step.contains(active)
                                    && active.hasAttribute('data-guide-step-action'));
                                }"""
                            )
                        )
                    result["guide_primary_runtime_ok"] = guide_primary_runtime_ok
                    if guide_count:
                        page.locator('[data-testid="scenario-guide-confirm"]').click(timeout=5000)
                        page.wait_for_timeout(150)
                    hidden_after_confirm = (
                        page.locator('[data-testid="scenario-guide"]').count() == 0
                    )

                    # Re-open the same persisted row: progress may advance, but
                    # it may never reset; the dismissed guide may not repeat on
                    # the same step/phase.
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    if not wait_detail_ready(page, retry_reload=True):
                        raise RuntimeError("scenario detail did not hydrate on resume")
                    after_locator = page.locator('[data-testid="scenario-progress"]')
                    after = after_locator.inner_text() if after_locator.count() else ""
                    m2 = re.search(r"Выполнено\s+(\d+)\s+из\s+(\d+)", after)
                    resume_ok = bool(
                        m1 and m2
                        and int(m1.group(2)) == int(m2.group(2))
                        and int(m2.group(1)) >= int(m1.group(1))
                    )
                    guide_after = page.locator('[data-testid="scenario-guide"]')
                    guide_after_key = (
                        guide_after.get_attribute("data-guide-key")
                        if guide_after.count() else None
                    )
                    guide_persist_ok = bool(
                        guide_count == 1
                        and hidden_after_confirm
                        and (
                            guide_after.count() == 0
                            or guide_after_key != guide_key
                        )
                    )
                    result["guide"] = {
                        "shown": guide_count == 1,
                        "key": guide_key,
                        "primary": primary,
                        "expected_primary": expected_primary,
                        "action_ok": guide_action_ok,
                        "hidden_after_confirm": hidden_after_confirm,
                        "after_reload_key": guide_after_key,
                        "same_step_does_not_repeat": guide_persist_ok,
                        "text": guide_text[:260],
                    }
                    result["resume"] = {
                        "before": before[:180],
                        "after": after[:180],
                        "ok": resume_ok,
                    }

                    page.set_viewport_size({"width": 380, "height": 900})
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    if not wait_detail_ready(page, retry_reload=True):
                        raise RuntimeError("mobile scenario detail did not hydrate")
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth > window.innerWidth + 4"
                    )
                    result["mobile"] = {
                        "root": page.locator('[data-testid="scenario-root"]').count(),
                        "progress": page.locator('[data-testid="scenario-progress"]').count(),
                        "overflow_x": bool(overflow),
                    }

                    d = result["desktop"]
                    if not (
                        result["started_in_browser"]
                        and d["root"] == 1
                        and d["progress"] == 1
                        and d["workflow"] == 1
                        and d["steps"] > 0
                        and d["owner_label"]
                        and d["next_label"]
                        and d["result_label"]
                        and not d["unavailable"]
                        and responsibility_ok
                        and guide_action_ok
                        and guide_primary_runtime_ok
                        and guide_persist_ok
                        and resume_ok
                        and result["mobile"]["root"] == 1
                        and result["mobile"]["progress"] == 1
                        and not result["mobile"]["overflow_x"]
                    ):
                        failures.append({
                            "scenario_type": scenario_type,
                            "stage": "browser_contract",
                            "evidence": result,
                        })
                except Exception as exc:
                    result["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
                    failures.append({
                        "scenario_type": scenario_type,
                        "stage": "browser_exception",
                        "error": result["error"],
                    })
                results.append(result)

            context.close()
            browser.close()
    finally:
        for sid in scenario_ids:
            try:
                api(
                    "POST",
                    "/api/home/scenario/cancel",
                    json={"account_id": QA_ACCOUNT, "scenario_id": sid},
                )
            except Exception:
                pass

    cleanup = api("GET", "/api/home/scenarios", params={"account_id": QA_ACCOUNT})
    active_after = cleanup.json().get("mine") or [] if cleanup.status_code == 200 else [1]
    if active_after:
        failures.append({
            "stage": "cleanup",
            "active_after": [x.get("id") for x in active_after],
        })

    payload = {
        "status": "PASS" if not failures else "FAIL",
        "account": QA_ACCOUNT,
        "catalog_count": len(INPUTS),
        "checked_count": len(results),
        "catalog_browser": catalog_browser,
        "active_after": len(active_after),
        "failures": failures,
        "results": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "catalog_count": payload["catalog_count"],
        "checked_count": payload["checked_count"],
        "active_after": payload["active_after"],
        "failure_count": len(failures),
        "report": str(REPORT),
        "failures": failures[:5],
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
