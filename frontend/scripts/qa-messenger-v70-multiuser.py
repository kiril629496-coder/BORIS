#!/usr/bin/env python3
from __future__ import annotations
import json,re,sys,time
from pathlib import Path
BACKEND=Path("/root/BORIS/backend");sys.path.insert(0,str(BACKEND))
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
import requests
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.user import User
from app.api.auth import create_access_token
from app.services.auth_sessions import issue_session,revoke_session
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

WEB="https://boris-ai.pro"; ACCOUNT="qa_dry_moskva"
OWNER_ID=46; EMPLOYEE_ID=1932; VIEWER_ID=1933
STAMP=str(int(time.time()))
PREFIX=f"QA-V70-{STAMP}"
GROUP_TITLE=f"QA V70 Group {STAMP}"

def dbrow(sql,params=None):
    db=SessionLocal()
    try:return db.execute(text(sql),params or {}).mappings().first()
    finally:db.close()

def issue(uid,label):
    db=SessionLocal()
    try:
        u=db.query(User).filter(User.id==uid).one()
        epoch=int(getattr(u,"security_epoch",0) or 0)
        sid,expires=issue_session(db,uid,epoch,user_agent=f"BORIS {label}",ip="127.0.0.1",device_label=label)
        db.commit()
        return create_access_token(uid,session_id=sid,security_epoch=epoch,expires_at=expires),sid,epoch
    finally:db.close()

def revoke(uid,sid):
    db=SessionLocal()
    try: revoke_session(db,uid,sid,"qa_v70_complete");db.commit()
    finally:db.close()

def legal_accept(page):
    page.goto(WEB+"/messenger/legal",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(700)
    if "/messenger/legal" in page.url:
        box=page.locator('input[type="checkbox"]')
        if box.count():
            box.check()
            page.get_by_role("button",name="Принять и продолжить").click()
            page.wait_for_url(re.compile(r"/messenger(?!/legal)"),timeout=15000)

def workspace(page,token):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('boris_messenger_token',t);localStorage.setItem('boris_currentAccount',a);localStorage.setItem('account_id',a)}""",[token,ACCOUNT])
    legal_accept(page)
    page.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1800)
    page.get_by_text("Онлайн",exact=True).first.wait_for(state="visible",timeout=10000)

def open_general(page):
    row=page.locator('button').filter(has_text="Общий чат").first
    row.wait_for(state="visible",timeout=10000);row.click();page.wait_for_timeout(250)

def composer(page):
    return page.locator('[class*="internalComposer"]').first

def send(page,body):
    c=composer(page);ta=c.locator("textarea").first
    ta.fill(body)
    c.get_by_role("button",name="Отправить",exact=True).click()
    page.get_by_text(body,exact=True).last.wait_for(state="visible",timeout=10000)

def set_viewer():
    db=SessionLocal()
    try:
        access=db.execute(text("SELECT role,can_view,can_reply,can_manage_team,can_view_reports FROM user_account_access WHERE account_id=:a AND user_id=:u"),{"a":ACCOUNT,"u":VIEWER_ID}).mappings().one()
        team=db.execute(text("SELECT role,status FROM team_memberships WHERE account_id=:a AND user_id=:u"),{"a":ACCOUNT,"u":VIEWER_ID}).mappings().one()
        db.execute(text("""UPDATE user_account_access SET role='VIEWER',can_view=TRUE,can_reply=FALSE,can_manage_team=FALSE,can_view_reports=TRUE WHERE account_id=:a AND user_id=:u"""),{"a":ACCOUNT,"u":VIEWER_ID})
        db.execute(text("UPDATE team_memberships SET role='VIEWER',status='ACTIVE' WHERE account_id=:a AND user_id=:u"),{"a":ACCOUNT,"u":VIEWER_ID})
        db.commit()
        return dict(access),dict(team)
    finally:db.close()

def restore_viewer(access,team):
    db=SessionLocal()
    try:
        db.execute(text("""UPDATE user_account_access SET role=:r,can_view=:v,can_reply=:reply,can_manage_team=:m,can_view_reports=:reports WHERE account_id=:a AND user_id=:u"""),
          {"r":access["role"],"v":access["can_view"],"reply":access["can_reply"],"m":access["can_manage_team"],"reports":access["can_view_reports"],"a":ACCOUNT,"u":VIEWER_ID})
        db.execute(text("UPDATE team_memberships SET role=:r,status=:s WHERE account_id=:a AND user_id=:u"),{"r":team["role"],"s":team["status"],"a":ACCOUNT,"u":VIEWER_ID})
        db.commit()
    finally:db.close()

def dm_ids():
    db=SessionLocal()
    try:
      return set(db.execute(text("""
        SELECT c.id FROM internal_conversations c
        WHERE c.account_id=:a AND c.kind='DM'
          AND (SELECT array_agg(user_id ORDER BY user_id) FROM internal_conversation_members m WHERE m.conversation_id=c.id)=ARRAY[:u1,:u2]::int[]
      """),{"a":ACCOUNT,"u1":min(OWNER_ID,EMPLOYEE_ID),"u2":max(OWNER_ID,EMPLOYEE_ID)}).scalars().all())
    finally:db.close()

def cleanup(before_dm):
    db=SessionLocal()
    try:
        group_ids=set(db.execute(text("SELECT id FROM internal_conversations WHERE account_id=:a AND (title=:t OR title=:renamed)"),{"a":ACCOUNT,"t":GROUP_TITLE,"renamed":GROUP_TITLE+" renamed"}).scalars().all())
        after_dm=set(db.execute(text("""
          SELECT c.id FROM internal_conversations c
          WHERE c.account_id=:a AND c.kind='DM'
            AND (SELECT array_agg(user_id ORDER BY user_id) FROM internal_conversation_members m WHERE m.conversation_id=c.id)=ARRAY[:u1,:u2]::int[]
        """),{"a":ACCOUNT,"u1":min(OWNER_ID,EMPLOYEE_ID),"u2":max(OWNER_ID,EMPLOYEE_ID)}).scalars().all())
        new_dm=after_dm-before_dm
        ids=list(db.execute(text("""SELECT m.id FROM internal_messages m JOIN internal_conversations c ON c.id=m.conversation_id WHERE c.account_id=:a AND m.body LIKE :p"""),{"a":ACCOUNT,"p":PREFIX+"%"}).scalars().all())
        if ids:
            db.execute(text("UPDATE internal_messages SET reply_to_message_id=NULL WHERE reply_to_message_id=ANY(:ids)"),{"ids":ids})
            db.execute(text("DELETE FROM internal_messenger_events WHERE account_id=:a AND (payload_json::text LIKE :p OR entity_id=ANY(:eids))"),{"a":ACCOUNT,"p":"%"+PREFIX+"%","eids":[str(x) for x in ids]})
            db.execute(text("DELETE FROM internal_messages WHERE id=ANY(:ids)"),{"ids":ids})
        all_conv=group_ids|new_dm
        if all_conv:
            db.execute(text("DELETE FROM internal_messenger_events WHERE account_id=:a AND entity_id=ANY(:ids)"),{"a":ACCOUNT,"ids":[str(x) for x in all_conv]})
            db.execute(text("DELETE FROM internal_conversations WHERE id=ANY(:ids)"),{"ids":list(all_conv)})
        db.commit()
        return {"deleted_message_ids":ids,"deleted_conversations":sorted(all_conv)}
    finally:db.close()

access0,team0=set_viewer()
before_dm=dm_ids()
tokens={}; sessions=[]
results={}; failures=[]
for uid,label in [(OWNER_ID,"Owner QA"),(EMPLOYEE_ID,"Employee QA"),(VIEWER_ID,"Viewer QA")]:
    tok,sid,epoch=issue(uid,label);tokens[uid]=tok;sessions.append((uid,sid))
try:
    # hard security: legacy JWT without sid must not enter Messenger
    legacy=create_access_token(OWNER_ID,security_epoch=0)
    legacy_resp=requests.get(WEB+"/api/legal/messenger/status",headers={"Authorization":"Bearer "+legacy},timeout=10)
    results["legacy_session_gate"]={"status":legacy_resp.status_code}
    if legacy_resp.status_code!=401: failures.append("legacy_session_gate")

    with sync_playwright() as pw:
      browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
      contexts={}
      pages={}
      for uid in (OWNER_ID,EMPLOYEE_ID,VIEWER_ID):
          ctx=browser.new_context(viewport={"width":1280,"height":800})
          page=ctx.new_page();workspace(page,tokens[uid])
          contexts[uid]=ctx;pages[uid]=page

      owner,emp,viewer=pages[OWNER_ID],pages[EMPLOYEE_ID],pages[VIEWER_ID]
      emp_name=dbrow("SELECT name FROM team_memberships WHERE account_id=:a AND user_id=:u",{"a":ACCOUNT,"u":EMPLOYEE_ID})["name"]

      # General chat and real two-way realtime
      open_general(owner);open_general(emp);open_general(viewer)
      cursor_key="boris_messenger_event_cursor:"+ACCOUNT
      m1=PREFIX+" GENERAL OWNER"
      cursor_before=emp.evaluate("(k)=>Number(localStorage.getItem(k)||0)",cursor_key)
      send(owner,m1)
      timeline=[]
      elapsed=0
      owner_to_employee=False
      for target in (1,3,6,10,15):
          emp.wait_for_timeout((target-elapsed)*1000); elapsed=target
          cursor_now=emp.evaluate("(k)=>Number(localStorage.getItem(k)||0)",cursor_key)
          visible=emp.get_by_text(m1,exact=True).count()>0
          timeline.append({"sec":target,"cursor":cursor_now,"visible":visible})
          if visible:
              owner_to_employee=True
              break
      m2=PREFIX+" GENERAL EMPLOYEE"
      send(emp,m2)
      try:
          owner.get_by_text(m2,exact=True).last.wait_for(state="visible",timeout=12000)
          employee_to_owner=True
      except PlaywrightTimeoutError:
          employee_to_owner=False
      results["general_realtime"]={"owner_to_employee":owner_to_employee,"employee_to_owner":employee_to_owner,"employee_cursor_before":cursor_before,"timeline":timeline}
      print("GENERAL_DIAG",json.dumps(results["general_realtime"],ensure_ascii=False),flush=True)
      if not owner_to_employee: failures.append("general_owner_to_employee")
      if not employee_to_owner: failures.append("general_employee_to_owner")
      try:
          owner.get_by_text(re.compile(r"Прочитано\s+\d+")).last.wait_for(state="visible",timeout=6000)
          results["read_receipt"]=True
      except PlaywrightTimeoutError:
          results["read_receipt"]=False;failures.append("read_receipt")

      # Real DM creation and two-way realtime
      plus=owner.get_by_role("button",name="Новый чат");plus.click()
      dlg=owner.get_by_role("dialog",name="Новый чат")
      cand=dlg.locator('[class*="newChatPeople"] > button').filter(has_text=emp_name).first
      cand.wait_for(state="visible",timeout=8000);cand.click()
      owner.get_by_role("button",name="Начать диалог").click();owner.wait_for_timeout(500)
      dm1=PREFIX+" DM OWNER";send(owner,dm1)
      try:
          emp.locator('button[class*="internalRow"]').filter(has_text=dm1).first.wait_for(state="visible",timeout=12000)
          dm_owner_to_employee=True
      except PlaywrightTimeoutError:
          dm_owner_to_employee=False
      db=SessionLocal()
      try:
          dm_id=int(db.execute(text("""
            SELECT c.id FROM internal_conversations c
            WHERE c.account_id=:a AND c.kind='DM'
              AND EXISTS(SELECT 1 FROM internal_conversation_members m WHERE m.conversation_id=c.id AND m.user_id=:o)
              AND EXISTS(SELECT 1 FROM internal_conversation_members m WHERE m.conversation_id=c.id AND m.user_id=:e)
            ORDER BY c.id DESC LIMIT 1
          """),{"a":ACCOUNT,"o":OWNER_ID,"e":EMPLOYEE_ID}).scalar_one())
      finally: db.close()
      if dm_owner_to_employee:
          emp.locator('button').filter(has_text=dm1).first.click()
      else:
          emp.goto(WEB+f"/messenger?account_id={ACCOUNT}&internal_conversation_id={dm_id}",wait_until="domcontentloaded",timeout=30000)
          emp.wait_for_timeout(1200)
      dm2=PREFIX+" DM EMPLOYEE";send(emp,dm2)
      try:
          owner.get_by_text(dm2,exact=True).last.wait_for(state="visible",timeout=12000)
          dm_employee_to_owner=True
      except PlaywrightTimeoutError:
          dm_employee_to_owner=False
      results["dm_realtime"]={"create":True,"owner_to_employee":dm_owner_to_employee,"employee_to_owner":dm_employee_to_owner,"conversation_id":dm_id}
      print("DM_DIAG",json.dumps(results["dm_realtime"],ensure_ascii=False),flush=True)
      if not dm_owner_to_employee: failures.append("dm_owner_to_employee")
      if not dm_employee_to_owner: failures.append("dm_employee_to_owner")

      # Real group creation and two-way realtime
      owner.get_by_role("button",name="Новый чат").click()
      dlg=owner.get_by_role("dialog",name="Новый чат")
      owner.get_by_role("button",name="Группа",exact=True).click()
      owner.get_by_role("textbox",name="Название новой группы").fill(GROUP_TITLE)
      dlg.locator('[class*="newChatPeople"] > button').filter(has_text=emp_name).first.click()
      owner.get_by_role("button",name="Создать группу").click();owner.wait_for_timeout(600)
      try:
          emp.get_by_text(GROUP_TITLE,exact=True).first.wait_for(state="visible",timeout=12000)
          group_visible=True
      except PlaywrightTimeoutError:
          group_visible=False
      db=SessionLocal()
      try:
          group_id=int(db.execute(text("SELECT id FROM internal_conversations WHERE account_id=:a AND title=:t ORDER BY id DESC LIMIT 1"),{"a":ACCOUNT,"t":GROUP_TITLE}).scalar_one())
      finally: db.close()
      g1=PREFIX+" GROUP OWNER";send(owner,g1)
      if group_visible:
          emp.locator('button').filter(has_text=GROUP_TITLE).first.click();emp.wait_for_timeout(250)
      else:
          emp.goto(WEB+f"/messenger?account_id={ACCOUNT}&internal_conversation_id={group_id}",wait_until="domcontentloaded",timeout=30000);emp.wait_for_timeout(1200)
      try:
          emp.get_by_text(g1,exact=True).last.wait_for(state="visible",timeout=10000)
          group_owner_to_employee=True
      except PlaywrightTimeoutError:
          group_owner_to_employee=False
      g2=PREFIX+" GROUP EMPLOYEE";send(emp,g2)
      try:
          owner.get_by_text(g2,exact=True).last.wait_for(state="visible",timeout=12000)
          group_employee_to_owner=True
      except PlaywrightTimeoutError:
          group_employee_to_owner=False
      results["group_realtime"]={"create":True,"visible_without_reload":group_visible,"owner_to_employee":group_owner_to_employee,"employee_to_owner":group_employee_to_owner,"conversation_id":group_id}
      print("GROUP_DIAG",json.dumps(results["group_realtime"],ensure_ascii=False),flush=True)
      if not group_visible: failures.append("group_visible_without_reload")
      if not group_owner_to_employee: failures.append("group_owner_to_employee")
      if not group_employee_to_owner: failures.append("group_employee_to_owner")

      # Viewer: read-only in UI and backend
      open_general(viewer)
      new_chat=viewer.get_by_role("button",name="Новый чат")
      viewer_comp=viewer.locator('[class*="internalComposer"] textarea').first
      ui_readonly=(new_chat.count()==1 and new_chat.is_disabled() and viewer_comp.is_disabled())
      post=requests.post(WEB+"/api/internal-messenger/conversations",headers={"Authorization":"Bearer "+tokens[VIEWER_ID],"Content-Type":"application/json"},json={"account_id":ACCOUNT,"kind":"DM","member_user_ids":[EMPLOYEE_ID]},timeout=10)
      results["viewer_readonly"]={"ui":ui_readonly,"backend_status":post.status_code}
      if not ui_readonly or post.status_code not in (403,404): failures.append("viewer_readonly")

      for ctx in contexts.values(): ctx.close()
      browser.close()
finally:
    for uid,sid in sessions:
        try:revoke(uid,sid)
        except Exception:pass
    restore_viewer(access0,team0)
    cleanup_result=cleanup(before_dm)
    results["cleanup"]=cleanup_result

print(json.dumps({"status":"PASS" if not failures else "FAIL","account":ACCOUNT,"results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(0 if not failures else 1)
