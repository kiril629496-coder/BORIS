#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path

BACKEND=Path("/root/BORIS/backend")
sys.path.insert(0,str(BACKEND))
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env", override=True)
import os
for _proxy_key in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
    os.environ.pop(_proxy_key, None)
os.environ["NO_PROXY"]="boris-ai.pro,127.0.0.1,localhost,::1"
os.environ["no_proxy"]=os.environ["NO_PROXY"]
from app.db.session import SessionLocal
from app.models.user import User
from app.services.platform_roles import is_private_platform_owner
from app.services.messenger_legal import acceptance_status
from app.api.auth import create_access_token
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="planetazayavki_65985"
# MESSENGER_UI_SCREENSHOT_V10
SHOT_DIR=Path("/root/BORIS/outputs/messenger-ui-v10-20261005")
SHOT_DIR.mkdir(parents=True,exist_ok=True)
VIEWPORTS=[
    (390,844,"mobile-390"),
    (1024,768,"desktop-1024"),
    (1280,800,"desktop-1280"),
    (1440,900,"desktop-1440"),
]

def auth():
    db=SessionLocal()
    try:
        owner=None
        for user in db.query(User).all():
            if is_private_platform_owner(user):
                owner=user
                break
        if owner is None:
            raise RuntimeError("private platform owner unavailable")
        if not acceptance_status(db,int(owner.id)).get("accepted"):
            raise RuntimeError("platform owner messenger legal acceptance missing")
        from app.services.auth_sessions import issue_session
        epoch=int(getattr(owner,"security_epoch",0) or 0)
        sid,expires=issue_session(
            db,int(owner.id),epoch,
            user_agent="BORIS Messenger runtime QA",
            ip="127.0.0.1",
        )
        db.commit()
        token=create_access_token(
            int(owner.id),
            session_id=sid,
            security_epoch=epoch,
            expires_at=expires,
        )
        return token,str(owner.role or "owner"),str(owner.account_id or "")
    finally:
        db.close()

def seed(page, token, role, account):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([token,role,account,target])=>{
      localStorage.setItem('boris_token',token);
      localStorage.setItem('boris_messenger_token',token);
      localStorage.setItem('boris_user_role',role||'owner');
      localStorage.setItem('boris_currentAccount',target);
      localStorage.setItem('account_id',target);
    }""",[token,role,account,ACCOUNT])

token,role,account=auth()
results={}
failures=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for w,h,label in VIEWPORTS:
        page=browser.new_page(viewport={"width":w,"height":h})
        realtime_network=[]
        def on_response(response):
            if "/api/internal-messenger/events" in response.url:
                realtime_network.append({"type":"response","status":response.status,"url":response.url.split("?",1)[0]})
        def on_failed(request):
            if "/api/internal-messenger/events" in request.url:
                realtime_network.append({"type":"failed","error":request.failure,"url":request.url.split("?",1)[0]})
        page.on("response",on_response)
        page.on("requestfailed",on_failed)
        seed(page,token,role,account)
        page.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(7000)
        body=page.locator("body").inner_text()
        problems=[]
        if "/login" in page.url:
            problems.append("redirected_to_login")
        if "/messenger/legal" in page.url:
            problems.append("redirected_to_legal")
        nav=page.locator("nav")
        if nav.count()!=1:
            problems.append("nav_missing")
        connection=""
        labels=page.locator('[class*="connectionLabel"]')
        if labels.count():
            connection=labels.first.inner_text()
        page.screenshot(path=str(SHOT_DIR / f"{label}-base.png"), full_page=False)
        plus=page.get_by_role("button",name="Новый чат")
        if plus.count()!=1:
            problems.append("new_chat_button_missing")
        else:
            plus.click()
            page.wait_for_timeout(250)
            page.screenshot(path=str(SHOT_DIR / f"{label}-new-chat.png"), full_page=False)
        dialog=page.get_by_role("dialog",name="Новый чат")
        if dialog.count()!=1:
            problems.append("new_chat_dialog_missing")
            candidate_count=0
            dm_enabled=False
            group_enabled=False
        else:
            candidates_locator=dialog.locator('[class*="newChatPeople"] > button')
            try:
                candidates_locator.first.wait_for(state="visible",timeout=5000)
            except Exception:
                pass
            candidate_count=candidates_locator.count()
            dm_enabled=False
            group_enabled=False
            if page.get_by_role("textbox",name="Поиск сотрудника").count()!=1:
                problems.append("employee_search_missing")
            if candidate_count<1:
                problems.append("employee_candidates_missing")
            else:
                candidate=dialog.locator('[class*="newChatPeople"] > button').first
                candidate.click()
                page.wait_for_timeout(100)
                start=page.get_by_role("button",name="Начать диалог")
                dm_enabled=start.count()==1 and start.is_enabled()
                if not dm_enabled:
                    problems.append("dm_submit_disabled_after_selection")
                page.get_by_role("button",name="Группа",exact=True).click()
                page.get_by_role("textbox",name="Название новой группы").fill("QA only — not submitted")
                dialog.locator('[class*="newChatPeople"] > button').first.click()
                page.wait_for_timeout(100)
                create=page.get_by_role("button",name="Создать группу")
                group_enabled=create.count()==1 and create.is_enabled()
                if not group_enabled:
                    problems.append("group_submit_disabled_after_selection")
        page.keyboard.press("Escape")
        more=page.get_by_role("button",name="Ещё",exact=True)
        if more.count()!=1:
            problems.append("more_button_missing")
            more_visible=False
        else:
            more.click()
            page.wait_for_timeout(150)
            more_visible=page.get_by_role("dialog",name="Ещё").count()==1
            if not more_visible:
                problems.append("more_dialog_missing")
        body2=page.locator("body").inner_text()
        for text in ("Задачи и напоминания","Уведомления и приложение","Безопасность и документы","Открыть основной BORIS"):
            if text not in body2:
                problems.append("more_item_missing:"+text)
        page.wait_for_function("() => !!document.documentElement && !!document.body",timeout=5000)
        overflow=page.evaluate("() => document.documentElement ? document.documentElement.scrollWidth > window.innerWidth + 1 : false")
        if overflow:
            problems.append("horizontal_overflow")
        results[label]={
            "url":page.url,
            "connection":connection,
            "realtime_network":realtime_network,
            "candidate_count":candidate_count,
            "dm_enabled":dm_enabled,
            "group_enabled":group_enabled,
            "more_visible":more_visible,
            "overflow_x":overflow,
            "problems":problems,
        }
        failures.extend(label+":"+p for p in problems)
        page.close()

    # Frontend execution contract: submit the real UI but intercept only the
    # create-conversation POST so production data is never mutated.
    for mode,label,conversation_id in (("dm","dm-submit",990001),("group","group-submit",990002)):
        page=browser.new_page(viewport={"width":1280,"height":800})
        seed(page,token,role,account)
        def create_route(route, request, cid=conversation_id):
            if request.method == "POST" and request.url.split("?",1)[0].endswith("/api/internal-messenger/conversations"):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status":"ok","conversation_id":cid,"deduplicated":False}),
                )
            else:
                route.continue_()
        page.route("**/api/internal-messenger/conversations", create_route)
        page.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(4000)
        plus=page.get_by_role("button",name="Новый чат")
        if plus.count()!=1:
            failures.append(label+":new_chat_button_missing")
            page.close()
            continue
        plus.click()
        dialog=page.get_by_role("dialog",name="Новый чат")
        dialog.wait_for(state="visible",timeout=5000)
        candidates=dialog.locator('[class*="newChatPeople"] > button')
        try:
            candidates.first.wait_for(state="visible",timeout=5000)
        except Exception:
            pass
        if candidates.count()<1:
            failures.append(label+":employee_candidates_missing")
            page.close()
            continue
        if mode=="dm":
            candidates.first.click()
            submit=page.get_by_role("button",name="Начать диалог")
        else:
            page.get_by_role("button",name="Группа",exact=True).click()
            page.get_by_role("textbox",name="Название новой группы").fill("QA UI synthetic group")
            dialog.locator('[class*="newChatPeople"] > button').first.click()
            submit=page.get_by_role("button",name="Создать группу")
        if not submit.is_enabled():
            failures.append(label+":submit_disabled")
        else:
            submit.click()
            try:
                dialog.wait_for(state="hidden",timeout=3000)
            except PlaywrightTimeoutError:
                failures.append(label+":dialog_did_not_close")
            composer=page.locator('textarea[placeholder*="Написать"]').first
            if composer.count()!=1:
                failures.append(label+":composer_not_opened")
        results[label]={
            "intercepted_post":True,
            "dialog_closed":dialog.count()==0 or not dialog.is_visible(),
            "composer_open":page.locator('textarea[placeholder*="Написать"]').count()>0,
        }
        page.close()
    browser.close()

print(json.dumps({"status":"PASS" if not failures else "FAIL","account":ACCOUNT,"results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(0 if not failures else 1)
