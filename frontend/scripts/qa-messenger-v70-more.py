#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
BACKEND=Path("/root/BORIS/backend"); sys.path.insert(0,str(BACKEND))
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.user import User
from app.services.platform_roles import is_private_platform_owner
from app.services.messenger_legal import acceptance_status
from app.services.auth_sessions import issue_session
from app.api.auth import create_access_token
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"; ACCOUNT="planetazayavki_65985"
VIEWPORTS=[(390,844,"mobile-390"),(1440,900,"desktop-1440")]

def issue_token():
    db=SessionLocal()
    try:
        owner=next((u for u in db.query(User).all() if is_private_platform_owner(u)),None)
        if owner is None or not acceptance_status(db,int(owner.id)).get("accepted"):
            raise RuntimeError("platform owner/legal unavailable")
        sid,expires=issue_session(db,int(owner.id),int(getattr(owner,"security_epoch",0) or 0),user_agent="BORIS-V70-MORE-QA",ip="127.0.0.1",device_label="V7 More QA")
        db.commit()
        token=create_access_token(int(owner.id),session_id=sid,security_epoch=int(getattr(owner,"security_epoch",0) or 0),expires_at=expires)
        return token,int(owner.id),sid
    finally:
        db.close()

def cleanup(uid,sid):
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM auth_user_sessions WHERE user_id=:u AND session_id=:s"),{"u":uid,"s":sid}); db.commit()
    finally: db.close()

def seed(page,token):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('boris_messenger_token',t);localStorage.setItem('boris_currentAccount',a);localStorage.setItem('account_id',a)}""",[token,ACCOUNT])

token,uid,sid=issue_token(); results={}; failures=[]
try:
  with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for w,h,label in VIEWPORTS:
      page=browser.new_page(viewport={"width":w,"height":h})
      seed(page,token)
      page.goto(WEB+"/messenger?account_id="+ACCOUNT,wait_until="domcontentloaded",timeout=30000)
      page.wait_for_timeout(4500)
      probs=[]
      def more_open():
        btn=page.get_by_role("button",name="Ещё",exact=True)
        if btn.count()!=1: probs.append("more_button_missing"); return False
        btn.click(); page.wait_for_timeout(150)
        return page.get_by_role("dialog",name="Ещё").count()==1
      # Tasks
      if more_open():
        b=page.get_by_role("button",name="Задачи и напоминания")
        if b.count()!=1: probs.append("tasks_item_missing")
        else:
          b.click(); page.wait_for_timeout(300)
          if page.get_by_text("Задачи и напоминания",exact=True).count()<1: probs.append("tasks_section_missing")
          open_clients=page.get_by_role("button",name="Открыть клиентские диалоги")
          if open_clients.count():
            open_clients.click(); page.wait_for_timeout(200)
            if page.get_by_text("Диалоги",exact=True).count()<1: probs.append("tasks_open_clients_broken")
      # Reports + custom picker
      if more_open():
        b=page.get_by_role("button",name="Отчёты и сводки")
        if b.count()!=1: probs.append("reports_item_missing")
        else:
          b.click(); page.wait_for_timeout(1700)
          if page.get_by_text("Отчёты и сводки",exact=True).count()<1: probs.append("reports_section_missing")
          native=page.locator('[class*="reportShareBar"] select').count()
          custom=page.locator('[class*="reportChatSelectButton"]').count()
          if native: probs.append("native_report_select_still_present")
          if custom!=1: probs.append("custom_report_picker_missing")
          if custom==1:
            page.locator('[class*="reportChatSelectButton"]').click(); page.wait_for_timeout(100)
            if page.get_by_role("listbox",name="Выберите чат").count()!=1: probs.append("report_picker_list_missing")
      # Team
      if more_open():
        b=page.get_by_role("button",name="Команда и доступы")
        if b.count()!=1: probs.append("team_item_missing")
        else:
          b.click(); page.wait_for_timeout(500)
          if page.get_by_role("heading",name="Команда",exact=True).count()!=1: probs.append("team_section_missing")
          invite=page.get_by_role("button",name="＋ Пригласить")
          if invite.count()<1: probs.append("team_invite_missing")
          else:
            invite.first.click(); page.wait_for_timeout(100)
            dlg=page.get_by_role("dialog",name="Пригласить сотрудника")
            if dlg.count()!=1: probs.append("invite_drawer_broken")
            else: dlg.get_by_role("button",name="Закрыть").click()
      # Settings
      if more_open():
        b=page.get_by_role("button",name="Уведомления и приложение")
        if b.count()!=1: probs.append("settings_item_missing")
        else:
          b.click(); page.wait_for_timeout(350)
          if page.get_by_text("Настройки Messenger",exact=True).count()<1: probs.append("settings_section_missing")
          access=page.locator('[class*="settingsLinkRow"]').filter(has_text="Планета Мебели").first
          if access.count()!=1: probs.append("settings_access_button_missing")
          else:
            access.click(); page.wait_for_timeout(100)
            dlg=page.get_by_role("dialog",name="Доступ к аккаунтам")
            if dlg.count()!=1: probs.append("settings_access_drawer_broken")
            else: dlg.get_by_role("button",name="Закрыть").click()
      # Security
      if more_open():
        b=page.get_by_role("dialog",name="Ещё").locator("button").filter(has_text="Безопасность и документы").first
        if b.count()!=1: probs.append("security_item_missing")
        else:
          b.click(); page.wait_for_timeout(650)
          if page.get_by_role("heading",name="Безопасность",exact=True).count()<1: probs.append("security_section_missing")
          refresh=page.get_by_role("button",name="Обновить",exact=True)
          if refresh.count()<1: probs.append("security_refresh_missing")
          else:
            refresh.first.click(); page.wait_for_timeout(350)
            if page.get_by_text("Устройства и сеансы",exact=True).count()!=1: probs.append("security_refresh_broken")
      # Dashboard link exists and points by behavior.
      if more_open():
        b=page.get_by_role("button",name="Открыть основной BORIS")
        if b.count()!=1: probs.append("dashboard_item_missing")
      overflow=page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
      if overflow: probs.append("horizontal_overflow")
      connection=""
      labels=page.locator('[class*="connectionLabel"]')
      if labels.count(): connection=labels.first.inner_text()
      if connection!="Онлайн": probs.append("realtime_not_online:"+connection)
      results[label]={"connection":connection,"problems":probs,"overflow_x":overflow}
      failures.extend(label+":"+x for x in probs)
      page.close()
    browser.close()
finally:
  cleanup(uid,sid)
print(json.dumps({"status":"PASS" if not failures else "FAIL","results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(0 if not failures else 1)
