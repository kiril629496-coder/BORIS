#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only browser QA for the mobile Avito category picker.

Uses existing QA campaign 284. It only opens the intake modal, switches between
directions and expands the category list. It never selects/saves/publishes.
"""
import json,sys
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
CAMPAIGN=284
DIRECTION="QA mobile UI: тестовая уборка в Москве"

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(400)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(2000)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def run(page):
    page.goto(WEB+f"/dashboard/campaign/{CAMPAIGN}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1300)
    dialog=page.locator('[role="dialog"][aria-labelledby="feed-intake-title"]')
    # Incomplete intake may auto-open on page load. Do not click the covered
    # underlying action in that state; a real user already has the dialog.
    if not dialog.count() or not dialog.is_visible():
        open_btn=page.get_by_role("button",name="Уточнить и продолжить")
        if not open_btn.count():
            return {"error":"open-intake-button-missing"}
        open_btn.click()
        page.wait_for_timeout(300)
    if not dialog.count() or not dialog.is_visible():
        return {"error":"intake-dialog-missing"}
    direction=dialog.locator("button").filter(has_text=DIRECTION).first
    if direction.count():
        direction.click()
        page.wait_for_timeout(300)
    change=dialog.get_by_role("button",name="Изменить категорию",exact=True)
    if not change.count():
        return {"error":"change-category-button-missing"}
    change.click()
    page.wait_for_timeout(250)
    options=dialog.locator(".boris-intake-category-options button")
    boxes=[]
    for i in range(options.count()):
        b=options.nth(i).bounding_box()
        if b:
            boxes.append({"w":round(b["width"],1),"h":round(b["height"],1),"text":options.nth(i).inner_text()[:80]})
    close=dialog.get_by_role("button",name="Закрыть опрос")
    cb=close.bounding_box() if close.count() else None
    return {
        "dialogVisible":dialog.is_visible(),
        "options":options.count(),
        "optionBoxes":boxes,
        "closeBox":{"w":round(cb["width"],1),"h":round(cb["height"],1)} if cb else None,
        "overflowX":page.evaluate("document.documentElement.scrollWidth>window.innerWidth+1"),
        "dialogWidth":round(dialog.bounding_box()["width"],1) if dialog.bounding_box() else None,
        "viewportWidth":page.viewport_size["width"],
    }

def main():
    out={"status":"PASS","viewports":{},"failures":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for width,height,label in [(320,760,"phone-320"),(390,844,"phone-390"),(1440,1000,"desktop-1440")]:
            ctx=br.new_context(viewport={"width":width,"height":height})
            p=ctx.new_page(); login(p)
            data=run(p); problems=[]
            if data.get("error"): problems.append(data["error"])
            if data.get("options",0)<4: problems.append("category-options")
            if data.get("overflowX"): problems.append("horizontal-overflow")
            if any(x["w"]<44 or x["h"]<44 for x in data.get("optionBoxes",[])): problems.append("option-touch-target")
            cb=data.get("closeBox")
            if cb and (cb["w"]<44 or cb["h"]<44): problems.append("close-touch-target")
            out["viewports"][label]={"metrics":data,"problems":problems}
            if problems: out["failures"].append({"viewport":label,"problems":problems})
            ctx.close()
        br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
