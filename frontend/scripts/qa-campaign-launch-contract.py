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
    for c in campaigns:
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
        card.wait_for(state="visible",timeout=4000)
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
    try:
        btn.wait_for(state="visible",timeout=4000)
    except PlaywrightTimeoutError:
        pass
    if not btn.count() or not btn.is_visible():
        problems.append("continue-button-missing")
    elif btn.is_disabled():
        problems.append("continue-button-disabled-before-click")
    else:
        btn.click()
        page.wait_for_timeout(1100)
        if not hits:
            problems.append("guardian-prepare-not-reached")
        if "Подготавливаю…" in page.inner_text("body"):
            problems.append("stuck-busy-after-abort")
        if btn.count() and btn.is_disabled():
            problems.append("continue-button-not-released")
    overflow=page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1")
    if overflow: problems.append("horizontal-overflow")
    page.unroute("**/api/campaign/**/guardian/prepare",block)
    return {
      "guardianAttemptBlocked":len(hits)>0,
      "businessStatus":card.inner_text() if card.count() else "",
      "continuePresent":btn.count()>0,
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
        dialog.wait_for(state="visible", timeout=3000)
    except PlaywrightTimeoutError:
        pass
    if not dialog.count() or not dialog.is_visible():
        open_btn=page.get_by_role("button",name="Уточнить и продолжить")
        if open_btn.count() and open_btn.is_visible():
            # Re-check immediately before the click to avoid racing auto-open.
            if not dialog.count() or not dialog.is_visible():
                open_btn.click()
            try:
                dialog.wait_for(state="visible", timeout=3000)
            except PlaywrightTimeoutError:
                pass
    if not dialog.count() or not dialog.is_visible():
        problems.append("intake-dialog-missing")
        return {"problems":problems}
    direction=fixture.get("direction") or ""
    if direction:
        d=dialog.locator("button").filter(has_text=direction).first
        if d.count() and d.is_visible():
            d.click(); page.wait_for_timeout(250)
    change=dialog.get_by_role("button",name="Изменить категорию",exact=True)
    if not change.count() or not change.is_visible():
        problems.append("change-category-missing")
    else:
        change.click(); page.wait_for_timeout(250)
    opts=dialog.locator(".boris-intake-category-options button")
    boxes=[]
    for i in range(opts.count()):
        b=opts.nth(i).bounding_box()
        if b:
            boxes.append({"w":round(b["width"],1),"h":round(b["height"],1)})
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
            ctx=br.new_context(viewport={"width":w,"height":h},user_agent="BORIS-QA-Campaign-Launch/1.0")
            p=ctx.new_page(); login(p)
            data=launch_case(p,launch["id"])
            out["launch"][label]=data
            if data["problems"]: out["failures"].append({"scope":"launch","viewport":label,"problems":data["problems"]})
            ctx.close()
        for label,w,h in [("phone-320",320,760),("phone-390",390,844),("desktop-1440",1440,1000)]:
            ctx=br.new_context(viewport={"width":w,"height":h},user_agent="BORIS-QA-Campaign-Launch/1.0")
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
