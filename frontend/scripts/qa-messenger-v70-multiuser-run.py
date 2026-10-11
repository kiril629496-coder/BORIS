#!/usr/bin/env python3
from __future__ import annotations
import json, sys, time, traceback
from pathlib import Path
BACKEND=Path("/root/BORIS/backend"); sys.path.insert(0,str(BACKEND))
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.user import User
from app.services.auth_sessions import issue_session, revoke_session
from app.api.auth import create_access_token
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"; ACCOUNT="qa_dry_moskva"; STAMP=str(int(time.time()))
EMP_EMAIL="qa.v70.emp."+STAMP+"@boris.invalid"; VIEW_EMAIL="qa.v70.viewer."+STAMP+"@boris.invalid"
EMP_NAME="QA V70 Employee "+STAMP[-5:]; VIEW_NAME="QA V70 Viewer "+STAMP[-5:]
EMP_PHONE="+7999"+STAMP[-7:]; VIEW_PHONE="+7888"+STAMP[-7:]
EMP_PASS="QaEmp2026"+STAMP[-3:]+"A"; VIEW_PASS="QaView2026"+STAMP[-3:]+"B"
GROUP_TITLE="QA V70 Group "+STAMP[-5:]; OWNER_MSG="QA V70 OWNER "+STAMP; EMP_MSG="QA V70 EMPLOYEE "+STAMP

def issue_owner():
    db=SessionLocal()
    try:
        oid=int(db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a"),{"a":ACCOUNT}).scalar_one())
        u=db.query(User).filter(User.id==oid).one()
        sid,exp=issue_session(db,oid,int(getattr(u,"security_epoch",0) or 0),user_agent="BORIS-V70-MULTIUSER-QA",ip="127.0.0.1",device_label="V7 QA Owner")
        db.commit()
        return oid,sid,create_access_token(oid,session_id=sid,security_epoch=int(getattr(u,"security_epoch",0) or 0),expires_at=exp)
    finally: db.close()

def seed(page,token):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('boris_messenger_token',t);localStorage.setItem('boris_currentAccount',a);localStorage.setItem('account_id',a)}""",[token,ACCOUNT])

def legal(page):
    page.goto(WEB+"/messenger?account_id="+ACCOUNT,wait_until="domcontentloaded",timeout=30000); page.wait_for_timeout(900)
    if "/messenger/legal" in page.url:
        page.get_by_role("checkbox").check(); page.get_by_role("button",name="Принять и продолжить").click()
        page.wait_for_url("**/messenger**",timeout=15000)
    page.wait_for_timeout(1800)

def more(page):
    page.get_by_role("button",name="Ещё",exact=True).click()
    page.get_by_role("dialog",name="Ещё").wait_for(state="visible",timeout=5000)

def invite(page,name,email,phone,role):
    more(page); page.get_by_role("dialog",name="Ещё").locator("button").filter(has_text="Команда и доступы").first.click()
    page.get_by_role("button",name="＋ Пригласить").first.click()
    d=page.get_by_role("dialog",name="Пригласить сотрудника"); d.wait_for(state="visible",timeout=5000)
    inputs=d.locator("input"); inputs.nth(0).fill(name); inputs.nth(1).fill(phone); inputs.nth(2).fill(email)
    role_index={"Старший менеджер":0,"Менеджер":1,"Наблюдатель":2}[role]; d.locator('button[class*="roleCard"]').nth(role_index).click(); d.get_by_role("button",name="Создать приглашение").click()
    c=d.locator("code"); c.wait_for(state="visible",timeout=7000); url=c.inner_text().strip()
    d.get_by_role("button",name="Закрыть").click(); return url

def accept_login(ctx,url,email,password):
    p=ctx.new_page(); p.goto(url,wait_until="domcontentloaded",timeout=30000)
    p.get_by_label("Email").fill(email); p.get_by_label("Пароль").fill(password); p.get_by_role("button",name="Принять приглашение").click()
    p.wait_for_url("**/login",timeout=15000); p.locator('input[type="email"]').fill(email); p.locator('input[type="password"]').fill(password)
    login_status=[]
    p.on("response",lambda r: login_status.append(r.status) if "/api/auth/login" in r.url else None)
    p.get_by_role("button",name="Войти",exact=True).click()
    p.wait_for_timeout(1400)
    if "/login" in p.url:
        visible=" ".join(p.locator("body").inner_text().split())[-500:]
        raise RuntimeError("employee login did not leave login page; status="+str(login_status[-1] if login_status else "none")+"; ui="+visible)
    if "/messenger" not in p.url:
        p.goto(WEB+"/messenger?account_id="+ACCOUNT,wait_until="domcontentloaded",timeout=30000)
    elif "/messenger/legal" not in p.url:
        p.goto(WEB+"/messenger?account_id="+ACCOUNT,wait_until="domcontentloaded",timeout=30000)
    p.wait_for_timeout(600)
    if "/messenger/legal" in p.url:
        p.get_by_role("checkbox").check(); p.get_by_role("button",name="Принять и продолжить").click(); p.wait_for_url("**/messenger**",timeout=15000)
    p.wait_for_timeout(1800); return p

def open_general(p):
    p.get_by_role("button",name="Чаты",exact=True).click(); p.wait_for_timeout(350)
    b=p.locator("button").filter(has_text="Общий чат").first; b.wait_for(state="visible",timeout=7000); b.click(); p.wait_for_timeout(250)

def send(p,msg):
    t=p.locator('textarea[placeholder*="Написать"]').first; t.fill(msg); p.get_by_role("button",name="Отправить",exact=True).last.click()

oid,sid,token=issue_owner(); results={}; failures=[]
with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    try:
        oc=b.new_context(viewport={"width":1440,"height":900}); owner=oc.new_page(); seed(owner,token); legal(owner); results["owner_legal"]=True
        ei=invite(owner,EMP_NAME,EMP_EMAIL,EMP_PHONE,"Менеджер"); vi=invite(owner,VIEW_NAME,VIEW_EMAIL,VIEW_PHONE,"Наблюдатель"); results["invites"]=True
        ec=b.new_context(viewport={"width":1280,"height":800}); emp=accept_login(ec,ei,EMP_EMAIL,EMP_PASS); results["employee_login_legal"]=True
        vc=b.new_context(viewport={"width":1280,"height":800}); viewer=accept_login(vc,vi,VIEW_EMAIL,VIEW_PASS); results["viewer_login_legal"]=True
        open_general(owner); open_general(emp); send(owner,OWNER_MSG); emp.get_by_text(OWNER_MSG,exact=True).wait_for(state="visible",timeout=8000)
        send(emp,EMP_MSG); owner.get_by_text(EMP_MSG,exact=True).wait_for(state="visible",timeout=8000); results["general_realtime"]=True
        owner.get_by_role("button",name="Новый чат").click(); d=owner.get_by_role("dialog",name="Новый чат"); d.get_by_role("textbox",name="Поиск сотрудника").fill(EMP_NAME)
        d.locator('[class*="newChatPeople"] > button').filter(has_text=EMP_NAME).first.click(); d.get_by_role("button",name="Начать диалог").click(); d.wait_for(state="hidden",timeout=5000)
        emp.locator("button").filter(has_text=EMP_NAME).first.wait_for(state="visible",timeout=8000); results["dm_realtime"]=True
        owner.get_by_role("button",name="Новый чат").click(); d=owner.get_by_role("dialog",name="Новый чат"); d.get_by_role("button",name="Группа",exact=True).click()
        d.get_by_role("textbox",name="Название новой группы").fill(GROUP_TITLE); d.locator('[class*="newChatPeople"] > button').filter(has_text=EMP_NAME).first.click()
        d.get_by_role("button",name="Создать группу").click(); d.wait_for(state="hidden",timeout=5000); emp.locator("button").filter(has_text=GROUP_TITLE).first.wait_for(state="visible",timeout=8000); results["group_realtime"]=True
        open_general(viewer); results["viewer_readonly_ui"]=viewer.locator('textarea[disabled]').count()>0
        deny=viewer.evaluate("""async(a)=>{const t=localStorage.getItem('boris_token')||localStorage.getItem('boris_messenger_token');const r=await fetch('/api/internal-messenger/conversations',{method:'POST',headers:{Authorization:'Bearer '+t,'Content-Type':'application/json'},body:JSON.stringify({account_id:a,kind:'GROUP',title:'QA BLOCK',member_user_ids:[]})});return r.status;}""",ACCOUNT)
        results["viewer_backend_denied"]=deny in (403,404)
        more(owner); owner.get_by_role("dialog",name="Ещё").locator("button").filter(has_text="Команда и доступы").first.click(); owner.wait_for_timeout(300)
        row=owner.locator('[class*="teamMemberRow"]').filter(has_text=EMP_NAME).first; row.get_by_role("button",name="Отозвать").click(); owner.wait_for_timeout(600)
        revoked=emp.evaluate("""async(a)=>{const t=localStorage.getItem('boris_token')||localStorage.getItem('boris_messenger_token');const r=await fetch('/api/internal-messenger/conversations?account_id='+encodeURIComponent(a),{headers:{Authorization:'Bearer '+t}});return r.status;}""",ACCOUNT)
        results["employee_revoke_status"]=revoked; results["employee_revoke_immediate"]=revoked in (401,403)
        owner.get_by_role("button",name="Чаты",exact=True).click(); owner.wait_for_timeout(300); open_general(owner)
        owner.get_by_role("button",name="Добавить").click(); owner.get_by_text("Сводка BORIS",exact=True).click(); owner.locator("button").filter(has_text="Финансы").last.click()
        owner.get_by_text("Сводка BORIS",exact=True).wait_for(state="visible",timeout=6000); owner.get_by_role("button",name="Закрыть сводку").click()
        ta=owner.locator('textarea[placeholder*="Написать"]').first; ta.fill("/баланс"); ta.press("Enter"); owner.get_by_text("Сводка BORIS",exact=True).wait_for(state="visible",timeout=6000)
        results["composer_reports_slash"]=True
    except Exception as e:
        failures.append(type(e).__name__+":"+str(e)); traceback.print_exc()
    finally:
        b.close()
print(json.dumps({"status":"PASS" if not failures and all(v is not False for v in results.values()) else "FAIL","results":results,"failures":failures,
"cleanup_manifest":{"account":ACCOUNT,"owner_id":oid,"owner_session_id":sid,"employee_email":EMP_EMAIL,"viewer_email":VIEW_EMAIL,"stamp":STAMP,"group_title":GROUP_TITLE}},ensure_ascii=False,indent=2))


db=SessionLocal()
try:
    revoke_session(db,oid,sid,"qa_v70_multiuser_complete")
    db.commit()
finally:
    db.close()
raise SystemExit(0 if not failures and all(v is not False for v in results.values()) else 1)
