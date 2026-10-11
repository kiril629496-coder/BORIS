#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
BACKEND=Path("/root/BORIS/backend");sys.path.insert(0,str(BACKEND))
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from app.db.session import SessionLocal
from app.models.user import User
from app.services.platform_roles import is_private_platform_owner
from app.services.messenger_legal import acceptance_status
from app.api.auth import create_access_token
from app.services.auth_sessions import issue_session,revoke_session
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro";ACCOUNT="planetazayavki_65985"

def auth():
    db=SessionLocal()
    try:
        owner=next(u for u in db.query(User).all() if is_private_platform_owner(u))
        assert acceptance_status(db,int(owner.id)).get("accepted")
        epoch=int(getattr(owner,"security_epoch",0) or 0)
        sid,expires=issue_session(db,int(owner.id),epoch,user_agent="BORIS QA Typography",ip="127.0.0.1",device_label="QA Typography")
        db.commit()
        return create_access_token(int(owner.id),session_id=sid,security_epoch=epoch,expires_at=expires),int(owner.id),sid
    finally: db.close()

def cleanup(uid,sid):
    db=SessionLocal()
    try: revoke_session(db,uid,sid,"qa_typography_complete"); db.commit()
    finally: db.close()

def px(page,selector):
    return float(page.eval_on_selector(selector,"el=>parseFloat(getComputedStyle(el).fontSize)"))

token,uid,sid=auth()
results={}; failures=[]
try:
  with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for w,h,label in [(1440,900,"desktop-1440"),(390,844,"mobile-390"),(320,568,"mobile-320")]:
      p=b.new_page(viewport={"width":w,"height":h})
      p.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
      p.evaluate("""([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('boris_messenger_token',t);localStorage.setItem('boris_currentAccount',a);localStorage.setItem('account_id',a)}""",[token,ACCOUNT])
      p.goto(WEB+f"/messenger?account_id={ACCOUNT}&section=settings",wait_until="domcontentloaded",timeout=30000)
      p.wait_for_timeout(1600)
      vals={
        "page_title":px(p,'[class*="surfaceHead"] h1'),
        "card_title":px(p,'[class*="settingsCardHead"] strong'),
        "card_meta":px(p,'[class*="settingsCardHead"] small'),
        "segmented":px(p,'[class*="settingsSegmented"] button'),
        "link_title":px(p,'[class*="settingsLinkRow"] strong'),
        "link_meta":px(p,'[class*="settingsLinkRow"] small'),
      }
      overflow=bool(p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
      cols=p.eval_on_selector('[class*="settingsColumns"]',"el=>getComputedStyle(el).gridTemplateColumns")
      problems=[]
      if vals["page_title"] < 20: problems.append("page_title_too_small")
      if vals["card_title"] < 17: problems.append("card_title_too_small")
      if vals["card_meta"] < 13: problems.append("card_meta_too_small")
      if vals["link_meta"] < 13: problems.append("link_meta_too_small")
      if w<=700 and vals["segmented"] < 15: problems.append("mobile_control_too_small")
      if w>700 and vals["segmented"] < 14: problems.append("desktop_control_too_small")
      if overflow: problems.append("horizontal_overflow")
      if w>900 and len(cols.split())<2: problems.append("desktop_columns_missing")
      results[label]={"font_px":vals,"grid_template_columns":cols,"overflow_x":overflow,"problems":problems}
      failures.extend(label+":"+x for x in problems)
      p.close()
    b.close()
finally:
  cleanup(uid,sid)
print(json.dumps({"status":"PASS" if not failures else "FAIL","results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(0 if not failures else 1)
