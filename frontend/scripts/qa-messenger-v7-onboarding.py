#!/usr/bin/env python3
from pathlib import Path
ns={}
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0],ns)
import json,re,secrets,time,requests
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright
web=ns["WEB"];account=ns["ACCOUNT"];uid=ns["OWNER_ID"]
stamp=str(int(time.time()));name="QA V7 Onboarding "+stamp
email="qa.v7."+stamp+"@example.com";password=secrets.token_urlsafe(24)+"A9"
jwt,sid,epoch=ns["issue"](uid,"V7 onboarding owner")
employee_id=None;employee_token=None;checks={};membership=None
def check(name,ok,details=None):checks[name]={"ok":bool(ok),"details":details}
def api(method,path,body=None,token=jwt):
 return requests.request(method,web+path,headers={"Authorization":"Bearer "+token},json=body,timeout=20)
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  owner=browser.new_page(viewport={"width":1440,"height":900})
  owner.goto(web+"/login",wait_until="commit",timeout=60000)
  owner.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[jwt,account])
  owner.goto(web+"/messenger?account_id="+account+"&section=team",wait_until="commit",timeout=60000)
  owner.get_by_role("button",name="＋ Пригласить",exact=True).first.wait_for(state="visible",timeout=30000)
  owner.get_by_role("button",name="＋ Пригласить",exact=True).first.click()
  dialog=owner.get_by_role("dialog",name="Пригласить сотрудника")
  dialog.get_by_placeholder("Например, Анна").fill(name)
  dialog.get_by_placeholder("+7 900 000-00-00").fill("+700000"+stamp[-5:])
  dialog.get_by_placeholder("name@company.ru").fill(email)
  dialog.get_by_role("button",name=re.compile("^Менеджер")).click()
  dialog.get_by_role("button",name="Создать приглашение",exact=True).click()
  link=dialog.locator("code");link.wait_for(timeout=15000);url=link.inner_text()
  invite_token=parse_qs(urlparse(url).query)["token"][0]
  membership=ns["dbrow"]("SELECT id,role,status,account_id,invite_expires_at>now() AS valid_expiry,invite_token_hash IS NOT NULL AS hashed FROM team_memberships WHERE account_id=:a AND email=:e ORDER BY id DESC LIMIT 1",{"a":account,"e":email})
  check("EMPLOYEE_INVITE",membership["status"]=="INVITED" and membership["hashed"] and membership["valid_expiry"])
  check("INVITE_BINDING",membership["account_id"]==account and membership["role"]=="MANAGER")
  wrong=api("POST","/api/team/invite/accept",{"token":invite_token,"email":"wrong."+email,"password":password})
  check("INVITE_EMAIL_BINDING_DENIAL",wrong.status_code==400,wrong.status_code)
  emp=browser.new_page(viewport={"width":1280,"height":800})
  emp.goto(url,wait_until="commit",timeout=60000)
  emp.get_by_label("Email",exact=True).wait_for(state="visible",timeout=30000)
  emp.get_by_label("Email",exact=True).fill(email)
  emp.get_by_label("Пароль",exact=True).fill(password)
  emp.get_by_role("button",name="Принять приглашение",exact=True).click()
  emp.wait_for_url("**/login",timeout=20000)
  employee_id=int(ns["dbrow"]("SELECT id FROM users WHERE email=:e",{"e":email})["id"])
  check("EMPLOYEE_REGISTRATION",employee_id>0)
  emp.locator('input[type="email"]').fill(email)
  emp.locator('input[type="password"]').fill(password)
  with emp.expect_response(lambda r:"/api/auth/login" in r.url,timeout=15000) as login_response:
   emp.get_by_role("button",name="Войти",exact=True).click()
  login_data=login_response.value.json()
  print("LOGIN_DIAG",json.dumps({"http":login_response.value.status,"status":login_data.get("status"),"message":login_data.get("message"),"detail":login_data.get("detail"),"has_token":bool(login_data.get("access_token"))},ensure_ascii=False),flush=True)
  emp.wait_for_url(re.compile("/messenger"),timeout=20000)
  employee_token=emp.evaluate("localStorage.getItem('boris_token') || localStorage.getItem('boris_messenger_token')")
  check("EMPLOYEE_LOGIN",bool(employee_token))
  denied=api("GET","/api/internal-messenger/conversations?account_id="+account,token=employee_token)
  check("EMPLOYEE_LEGAL_GATE",denied.status_code==428,denied.status_code)
  emp.wait_for_url("**/messenger/legal",timeout=15000)
  emp.locator('input[type="checkbox"]').check()
  emp.get_by_role("button",name="Принять и продолжить",exact=True).click()
  emp.wait_for_url(re.compile("/messenger(?!/legal)"),timeout=15000)
  r=api("GET","/api/internal-messenger/conversations?account_id="+account,token=employee_token)
  check("EMPLOYEE_LEGAL_ACCEPTANCE",r.status_code==200,r.status_code)
  check("GENERAL_AUTO_JOIN",any(x.get("is_general") for x in r.json().get("conversations",[])))
  check("TEAM_STATUS_ACTIVE",ns["dbrow"]("SELECT status FROM team_memberships WHERE id=:id",{"id":membership["id"]})["status"]=="ACTIVE")
  owner.get_by_role("button",name="Закрыть",exact=True).click()
  row_ui=owner.locator('[class*="teamMemberRow"]').filter(has_text=email)
  row_ui.wait_for(timeout=15000)
  check("TEAM_DEVICE_STATUS_UI","Push" in row_ui.inner_text() and "PWA" in row_ui.inner_text())
  for role,label in [("MANAGER_LEAD","Старший менеджер"),("VIEWER","Наблюдатель"),("MANAGER","Менеджер")]:
   row_ui.get_by_role("button",name="Роль "+name,exact=True).click()
   with owner.expect_response(lambda r:r.request.method=="PATCH" and "/api/team/memberships/" in r.url,timeout=15000) as role_response:
    owner.get_by_role("option").filter(has_text=label).first.click()
   check("ROLE_HTTP_"+role,role_response.value.status==200,role_response.value.status)
   current=ns["dbrow"]("SELECT role FROM user_account_access WHERE account_id=:a AND user_id=:u",{"a":account,"u":employee_id})
   check("TEAM_ROLE_"+role,current["role"]==role)
  owner.once("dialog",lambda d:d.accept())
  row_ui.get_by_role("button",name="Отозвать",exact=True).click()
  owner.wait_for_timeout(1000)
  denied=api("GET","/api/internal-messenger/conversations?account_id="+account,token=employee_token)
  check("EMPLOYEE_REVOKE_API",denied.status_code in (401,403),denied.status_code)
  emp.wait_for_url(re.compile("/login|/messenger/legal"),timeout=20000)
  check("EMPLOYEE_REVOKE_UI",account not in emp.locator("body").inner_text() and "/login" in emp.url)
  row=ns["dbrow"]("SELECT count(*) AS n FROM auth_user_sessions WHERE user_id=:u AND revoked_at IS NULL",{"u":employee_id})
  check("EMPLOYEE_SESSIONS_REVOKED",row["n"]==0)
  browser.close()
except Exception as exc:
 check("UNEXPECTED",False,type(exc).__name__+":"+str(exc))
finally:
 if employee_id:
  r=api("POST","/api/team/revoke",{"user_id":employee_id,"account_id":account})
  check("QA_EMPLOYEE_ACCESS_CLOSED",r.status_code==200 or not ns["dbrow"]("SELECT 1 FROM user_account_access WHERE account_id=:a AND user_id=:u",{"a":account,"u":employee_id}))
 elif membership:
  api("POST",f"/api/team/memberships/{membership['id']}/revoke-invite")
 ns["revoke"](uid,sid)
print(json.dumps({"status":"PASS" if checks and all(x["ok"] for x in checks.values()) else "FAIL","employee_id":employee_id,"membership_id":membership["id"] if membership else None,"checks":checks},ensure_ascii=False,indent=2))
raise SystemExit(0 if checks and all(x["ok"] for x in checks.values()) else 1)
