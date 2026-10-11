#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
import requests

BACKEND=Path("/root/BORIS/backend")
sys.path.insert(0,str(BACKEND))
import qa_suite
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT=""
OUT=BACKEND/"images/qa"
OUT.mkdir(parents=True,exist_ok=True)
VIEWPORTS=[
    (360,800,"phone-360"),
    (390,844,"phone-390"),
    (768,1024,"tablet-768"),
    (1024,768,"tablet-1024"),
    (1280,800,"desktop-1280"),
    (1440,900,"desktop-1440"),
]

def session():
    email,password=qa_suite.CLIENT
    try:
        r=requests.post(WEB+"/api/auth/login",json={"email":email,"password":password},timeout=20)
        if r.status_code==200:
            d=r.json()
            if d.get("access_token"):
                return d["access_token"],email,d.get("user") or {}
    except Exception:
        pass
    local=qa_suite.login(qa_suite.CLIENT)
    if not local:
        raise RuntimeError("QA owner auth unavailable")
    token,role,account=local
    return token,email,{"role":role,"account_id":account}

def seed(page,token,email,user):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([token,email,role,account])=>{
      localStorage.setItem('boris_token',token);
      localStorage.setItem('boris_user_email',email);
      localStorage.setItem('boris_user_role',role||'owner');
      localStorage.setItem('boris_currentAccount',account||'');
    }""",[token,email,user.get("role"),user.get("account_id")])

def metrics(page):
    return page.evaluate("""()=>({
      width:innerWidth,
      scrollWidth:document.documentElement.scrollWidth,
      overflowX:document.documentElement.scrollWidth>innerWidth+1,
      body:(document.body.innerText||'').slice(0,30000),
      nav:!!document.querySelector('nav'),
      textareas:document.querySelectorAll('textarea').length,
      buttons:document.querySelectorAll('button').length,
    })""")

token,email,user=session()
ACCOUNT=str(user.get('account_id') or '').strip()
if not ACCOUNT:
    raise RuntimeError('QA client account missing')
results={}
failures=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for w,h,label in VIEWPORTS:
        page=browser.new_page(viewport={"width":w,"height":h})
        seed(page,token,email,user)
        page.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(2500)
        m=metrics(page)
        body=m["body"]
        problems=[]
        if m["overflowX"]: problems.append("horizontal_overflow")
        if "Требуется team.manage" in body: problems.append("raw_team_manage")
        if "Тариф этого аккаунта закончился" in body: problems.append("false_expired")
        if ACCOUNT not in page.url and not body: problems.append("account_not_loaded")
        chat=page.get_by_role("button",name="Чат",exact=True)
        if chat.count():
            chat.click()
            page.wait_for_timeout(900)
        chat_body=(page.locator("body").inner_text() or "")
        if "Общий чат" not in chat_body: problems.append("general_chat_not_visible")
        if w>=1025:
            rail=page.locator("nav").bounding_box()
            if not rail or rail["width"]>100: problems.append("desktop_rail_not_compact")
        team=page.get_by_role("button",name="Команда",exact=True)
        team_drawer=False
        if team.count():
            team.click(); page.wait_for_timeout(700)
            team_body=page.locator("body").inner_text() or ""
            if "team.manage" in team_body: problems.append("team_technical_error")
            if "Создать приглашение" in team_body and "Пригласить" not in team_body:
                problems.append("old_giant_invite_form")
            invite=page.get_by_role("button",name="＋ Пригласить",exact=True)
            if invite.count():
                invite.click(); page.wait_for_timeout(300)
                team_drawer=page.get_by_role("dialog",name="Пригласить сотрудника").count()>0
                if not team_drawer: problems.append("invite_drawer_missing")
        shot=OUT/f"messenger-v4-{label}.png"
        page.screenshot(path=str(shot),full_page=False)
        results[label]={
            "overflowX":m["overflowX"],
            "buttons":m["buttons"],
            "account":ACCOUNT,
            "generalChat":("Общий чат" in chat_body),
            "teamDrawer":team_drawer,
            "problems":problems,
            "screenshot":str(shot),
        }
        failures.extend(label+":"+p for p in problems)
        page.close()
    browser.close()
print(json.dumps({"status":"PASS" if not failures else "FAIL","account":ACCOUNT,"results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(0 if not failures else 1)
