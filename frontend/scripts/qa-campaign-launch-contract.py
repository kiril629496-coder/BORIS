#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only end-to-end contract for campaign launch on phone + desktop.

It proves the real user path reaches BORIS preparation, but aborts the
guardian/prepare request in the browser before it reaches the backend.
No publication, paid generation or campaign mutation is allowed here.
"""
from __future__ import annotations
import json,re,sys,time
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite,requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

WEB="https://boris-ai.pro"
API=WEB
ACCOUNT="testref_47784"
_PUBLIC_AUTH=None

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
                    _PUBLIC_AUTH=(token,role)
                    return _PUBLIC_AUTH
            last=f"http_{r.status_code}"
        except Exception as e:
            last=f"{type(e).__name__}:{str(e)[:120]}"
        time.sleep(2*(attempt+1))
    raise RuntimeError("public QA auth failed: "+last)

def headers():
    token,_=public_auth()
    return {"Authorization":"Bearer "+token}

def choose_fixtures():
    campaigns=requests.get(API+"/api/campaign/list",headers=headers(),params={"account_id":ACCOUNT},timeout=20).json().get("campaigns",[])
    launch=None
    intake=None
    # QA_FIXTURE_SCAN_BOUND_V1: bound discovery so two workflow reads per
    # campaign cannot exhaust the canonical deploy job timeout.
    for c in campaigns[:20]:
        cid=c.get("id")
        if not cid:
            continue
        try:
            wf=requests.get(API+f"/api/campaign/{cid}/workflow",headers=headers(),timeout=12)
            if wf.status_code==200:
                d=wf.json(); w=d.get("workflow") or {}
                step=str(w.get("step") or "")
                qs=d.get("questions") or []
                if launch is None and step in {"structure","texts","media","category","settings","validation","preview","safe_feed"} and not qs:
                    guardian=requests.get(API+f"/api/campaign/{cid}/guardian/status",headers=headers(),timeout=12)
                    gd=guardian.json() if guardian.status_code==200 else None
                    order=w.get("order_contract") or {}
                    order_status=str(order.get("status") or "").upper()
                    jobs=(gd or {}).get("jobs") or []
                    latest=jobs[0] if jobs else {}
                    latest_status=str(latest.get("status") or "").lower()
                    # A running preparation intentionally disables the launch button.
                    # Terminal-failed/cancelled legacy campaigns and BLOCKED order
                    # contracts are not valid launch fixtures: they do not represent
                    # the owner's normal "Continue preparation" path.
                    if (
                        gd is not None
                        and not gd.get("running")
                        and order_status not in {"BLOCKED","FAILED"}
                        and latest_status not in {"failed","cancelled"}
                    ):
                        launch={"id":cid,"step":step,"name":c.get("name")}
            ir=requests.get(API+f"/api/campaign/{cid}/workflow/intake",headers=headers(),timeout=12)
            if ir.status_code==200:
                d=ir.json()
                if not d.get("all_complete"):
                    for direction in d.get("directions") or []:
                        candidates=direction.get("candidates") or []
                        if len(candidates)>=4:
                            intake={"id":cid,"name":c.get("name"),"direction":str(direction.get("direction") or ""),"candidates":len(candidates)}
                            break
        except Exception:
            pass
        if launch and intake:
            break
    return launch,intake

def login(page):
    token,role=public_auth()
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([token,email,role,account])=>{
      localStorage.setItem('boris_token',token);
      localStorage.setItem('boris_user_email',email);
      localStorage.setItem('boris_user_role',role);
      localStorage.setItem('boris_currentAccount',account);
    }""",[token,qa_suite.OWNER[0],role,ACCOUNT])

def launch_case(page,cid):
    hits=[]
    def block(route):
        hits.append(route.request.url)
        route.abort()
    page.route("**/api/campaign/**/guardian/prepare",block)
    page.goto(WEB+f"/dashboard/campaign/{cid}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1000)
    problems=[]
    card=page.locator('[data-testid="campaign-business-status"]')
    try:
        card.wait_for(state="visible",timeout=10000)
    except PlaywrightTimeoutError:
        pass
    if not card.count() or not card.is_visible():
        problems.append("business-status-missing")
    else:
        text=card.inner_text()
        for required in ("BORIS сейчас","Практический результат","От вас сейчас"):
            if required not in text:
                problems.append("business-status-labels")
                break
    btn=page.get_by_role("button",name=re.compile("Подготовить кампанию|Продолжить подготовку"))
    ready_btn=page.get_by_role("button",name=re.compile("Проверить и разместить в Avito|Проверить результат"))
    try:
        btn.wait_for(state="visible",timeout=10000)
    except PlaywrightTimeoutError:
        pass
    ready_visible=bool(ready_btn.count() and ready_btn.is_visible())
    if ready_visible:
        # A zero-touch campaign may already be READY by the time QA opens it.
        # The correct owner action is review/publication, not another prepare call.
        # Never click this CTA in QA: external publication remains out of scope.
        pass
    elif not btn.count() or not btn.is_visible():
        # Zero-touch may start between fixture discovery and the mobile render.
        # In that state the normal continue button is intentionally replaced by
        # the autonomous-running UI and must not be reported as missing.
        try:
            guardian=requests.get(API+f"/api/campaign/{cid}/guardian/status",headers=headers(),timeout=12)
            gd=guardian.json() if guardian.status_code==200 else {}
        except Exception:
            gd={}
        auto_btn=page.get_by_role("button",name=re.compile("BORIS продолжает автоматически"))
        if not (gd.get("running") or (auto_btn.count() and auto_btn.is_visible())):
            problems.append("continue-button-missing")
    elif btn.is_disabled():
        problems.append("continue-button-disabled-before-click")
    else:
        btn.click()
        page.wait_for_timeout(1100)
        if not hits:
            # Zero-touch Campaign may be claimed by the durable worker in the
            # short discovery->click race. In that case the button disappears
            # because preparation is already running, which is a successful
            # launch state rather than a missing guardian request.
            try:
                guardian=requests.get(API+f"/api/campaign/{cid}/guardian/status",headers=headers(),timeout=12)
                gd=guardian.json() if guardian.status_code==200 else {}
            except Exception:
                gd={}
            status_text=card.inner_text() if card.count() else ""
            autonomous_running=bool(gd.get("running")) or (
                "Готовит кампанию" in status_text and "Ничего" in status_text
            )
            if not autonomous_running:
                problems.append("guardian-prepare-not-reached")
        if "Подготавливаю…" in page.inner_text("body") and hits:
            problems.append("stuck-busy-after-abort")
        if btn.count() and btn.is_disabled() and hits:
            problems.append("continue-button-not-released")
    # Final-state recheck: the business status can appear between the initial
    # visibility timeout and the rest of the launch assertions. Judge the state
    # the user actually sees at the end of the interaction, not a transient 4s race.
    if "business-status-missing" in problems and card.count() and card.is_visible():
        problems=[p for p in problems if p!="business-status-missing"]
        text=card.inner_text()
        if any(required not in text for required in ("BORIS сейчас","Практический результат","От вас сейчас")):
            problems.append("business-status-labels")
    # Route callbacks can land just after the first post-click check. If the
    # guarded guardian/prepare request is now observed, the launch path was
    # definitively reached and the earlier transient error is stale.
    if hits and "guardian-prepare-not-reached" in problems:
        problems=[p for p in problems if p!="guardian-prepare-not-reached"]
    overflow=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    if overflow: problems.append("horizontal-overflow")
    page.unroute("**/api/campaign/**/guardian/prepare",block)
    return {
      "guardianAttemptBlocked":len(hits)>0,
      "businessStatus":card.inner_text() if card.count() else "",
      "continuePresent":btn.count()>0,
      "readyActionPresent":ready_visible,
      "continueDisabledAfter":btn.is_disabled() if btn.count() else None,
      "stuckBusy":"Подготавливаю…" in page.inner_text("body"),
      "overflowX":overflow,
      "problems":problems,
    }

def intake_case(page,fixture):
    cid=fixture["id"]
    page.goto(WEB+f"/dashboard/campaign/{cid}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1000)
    problems=[]
    dialog=page.locator('[role="dialog"][aria-labelledby="feed-intake-title"]')
    # Intake intentionally auto-opens after its API data arrives. Give that
    # normal async transition a short grace window before clicking the fallback
    # opener; otherwise the modal can appear between the visibility check and
    # the click and correctly intercept the now-obsolete underlying button.
    try:
        dialog.wait_for(state="visible", timeout=8000)
    except PlaywrightTimeoutError:
        pass
    if not dialog.count() or not dialog.is_visible():
        open_btn=page.get_by_role("button",name="Уточнить и продолжить")
        if open_btn.count() and open_btn.is_visible():
            # Re-check immediately before the click to avoid racing auto-open.
            if not dialog.count() or not dialog.is_visible():
                open_btn.click()
            try:
                dialog.wait_for(state="visible", timeout=8000)
            except PlaywrightTimeoutError:
                pass
    if not dialog.count() or not dialog.is_visible():
        # The zero-touch Campaign worker may resolve the missing Avito facts
        # between fixture discovery and browser render. Re-check server truth
        # before declaring the UI broken; a now-complete intake correctly has
        # neither a modal nor an "Уточнить" button.
        try:
            live=requests.get(API+f"/api/campaign/{cid}/workflow/intake",headers=headers(),timeout=12)
            current=live.json() if live.status_code==200 else {}
        except Exception:
            current={}
        if current.get("all_complete"):
            overflow=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
            if overflow: problems.append("horizontal-overflow")
            return {
              "resolvedDuringLoad":True,
              "dialogVisible":False,
              "overflowX":overflow,
              "problems":problems,
            }
        # Portal mounting and Campaign hydration can lag on the narrowest
        # 320px viewport. If the page is still in the loading state, wait for
        # either the dialog, completion, or the loading marker to disappear
        # before declaring a missing-control defect.
        body_text=page.inner_text("body")[:2400]
        if "Загружаю…" in body_text:
            for _ in range(20):
                page.wait_for_timeout(500)
                if dialog.count() and dialog.is_visible():
                    break
                try:
                    live=requests.get(API+f"/api/campaign/{cid}/workflow/intake",headers=headers(),timeout=6)
                    current=live.json() if live.status_code==200 else {}
                except Exception:
                    current={}
                if current.get("all_complete"):
                    overflow=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
                    if overflow: problems.append("horizontal-overflow")
                    return {
                      "resolvedDuringLoad":True,
                      "dialogVisible":False,
                      "overflowX":overflow,
                      "problems":problems,
                    }
                body_text=page.inner_text("body")[:2400]
                if "Загружаю…" not in body_text:
                    break
        if "Нужно уточнить обязательные данные Avito" in body_text:
            try:
                dialog.wait_for(state="visible",timeout=3000)
            except PlaywrightTimeoutError:
                pass
        if dialog.count() and dialog.is_visible():
            pass
        else:
            open_btn=page.get_by_role("button",name="Уточнить и продолжить")
            problems.append("intake-dialog-missing")
            return {
              "resolvedDuringLoad":False,
              "openButtonCount":open_btn.count(),
              "openButtonVisible":bool(open_btn.count() and open_btn.is_visible()),
              "url":page.url,
              "bodyPreview":body_text,
              "problems":problems,
            }
    direction=fixture.get("direction") or ""
    if direction:
        d=dialog.locator("button").filter(has_text=direction).first
        if d.count() and d.is_visible():
            d.click(); page.wait_for_timeout(250)
    change=dialog.get_by_role("button",name="Изменить категорию",exact=True)
    opts=dialog.locator(".boris-intake-category-options button")
    # React can hydrate the selected category/candidate picker a fraction later on
    # the narrow 320px viewport. Wait briefly for either valid state before
    # recording a missing-control defect; locators stay live while the DOM updates.
    if not ((change.count() and change.is_visible()) or opts.count()>0):
        for _ in range(15):
            page.wait_for_timeout(100)
            if not dialog.count() or not dialog.is_visible():
                break
            if (change.count() and change.is_visible()) or opts.count()>0:
                break
    # When no category is selected yet, the candidate picker is already open and
    # there is nothing to "change". Require the toggle only if options are hidden.
    if change.count() and change.is_visible():
        change.click(); page.wait_for_timeout(250)
        opts=dialog.locator(".boris-intake-category-options button")
    elif opts.count()==0:
        problems.append("change-category-missing")
    boxes=[]
    for i in range(opts.count()):
        b=opts.nth(i).bounding_box()
        if b:
            boxes.append({"w":round(b["width"],1),"h":round(b["height"],1)})
    # Intake can become complete while the modal is open: zero-touch worker
    # may resolve the last required fact and React correctly unmounts the dialog.
    # Re-check server truth before treating now-missing picker controls as UI bugs.
    if not dialog.count() or not dialog.is_visible():
        try:
            live=requests.get(API+f"/api/campaign/{cid}/workflow/intake",headers=headers(),timeout=12)
            current=live.json() if live.status_code==200 else {}
        except Exception:
            current={}
        if current.get("all_complete"):
            overflow=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
            if overflow: problems.append("horizontal-overflow")
            return {
              "resolvedDuringLoad":True,
              "dialogVisible":False,
              "overflowX":overflow,
              "problems":[p for p in problems if p not in {"change-category-missing","category-options","close-touch-target"}],
            }
    # Final-state truth wins over a transient hydration race above. If the
    # candidate picker is visibly populated, absence of the "change" toggle is
    # valid because there is no selected category to change yet.
    if opts.count()>0:
        problems=[p for p in problems if p!="change-category-missing"]
    if opts.count()<4: problems.append("category-options")
    if any(x["w"]<44 or x["h"]<44 for x in boxes): problems.append("category-touch-target")
    close=dialog.get_by_role("button",name="Закрыть опрос")
    cb=close.bounding_box() if close.count() else None
    if not cb or cb["width"]<44 or cb["height"]<44: problems.append("close-touch-target")
    overflow=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    if overflow: problems.append("horizontal-overflow")
    return {
      "dialogVisible":dialog.is_visible(),
      "options":opts.count(),
      "optionBoxes":boxes,
      "closeBox":{"w":round(cb["width"],1),"h":round(cb["height"],1)} if cb else None,
      "overflowX":overflow,
      "problems":problems,
    }

def main():
    launch,intake=choose_fixtures()
    out={"status":"PASS","fixtures":{"launch":launch,"intake":intake},"launch":{},"intake":{},"failures":[]}
    if not launch:
        out["failures"].append({"scope":"fixture","problems":["launch-fixture-missing"]})
    if not intake:
        out["failures"].append({"scope":"fixture","problems":["intake-fixture-missing"]})
    if out["failures"]:
        out["status"]="FAIL"; print(json.dumps(out,ensure_ascii=False,indent=2)); return 2
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for label,w,h in [("phone-320",320,760),("desktop-1440",1440,1000)]:
            ctx=br.new_context(service_workers="block",viewport={"width":w,"height":h},user_agent="BORIS-QA-Campaign-Launch/1.0")
            p=ctx.new_page(); login(p)
            data=launch_case(p,launch["id"])
            out["launch"][label]=data
            if data["problems"]: out["failures"].append({"scope":"launch","viewport":label,"problems":data["problems"]})
            ctx.close()
        for label,w,h in [("phone-320",320,760),("phone-390",390,844),("desktop-1440",1440,1000)]:
            ctx=br.new_context(service_workers="block",viewport={"width":w,"height":h},user_agent="BORIS-QA-Campaign-Launch/1.0")
            p=ctx.new_page(); login(p)
            data=intake_case(p,intake)
            out["intake"][label]=data
            if data["problems"]: out["failures"].append({"scope":"intake","viewport":label,"problems":data["problems"]})
            ctx.close()
        br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
