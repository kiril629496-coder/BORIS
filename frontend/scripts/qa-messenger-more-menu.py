#!/usr/bin/env python3
from __future__ import annotations
import atexit,json,sys
from pathlib import Path
BACKEND=Path("/root/BORIS/backend");sys.path.insert(0,str(BACKEND))
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from app.db.session import SessionLocal
from app.models.user import User
from app.services.platform_roles import is_private_platform_owner
from app.services.messenger_legal import acceptance_status
from app.api.auth import create_access_token
from app.services.auth_sessions import issue_session, revoke_session
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro";ACCOUNT="planetazayavki_65985"

def auth():
    db=SessionLocal()
    try:
        owner=next(u for u in db.query(User).all() if is_private_platform_owner(u))
        if not acceptance_status(db,int(owner.id)).get("accepted"):
            raise RuntimeError("legal acceptance missing")
        epoch=int(getattr(owner,"security_epoch",0) or 0)
        sid,expires=issue_session(db,int(owner.id),epoch,user_agent="BORIS QA More Menu",ip="127.0.0.1",device_label="QA More Menu")
        db.commit()
        token=create_access_token(int(owner.id),session_id=sid,security_epoch=epoch,expires_at=expires)
        return token,int(owner.id),sid
    finally: db.close()

def cleanup(user_id:int,sid:str):
    db=SessionLocal()
    try:
        revoke_session(db,user_id,sid,"qa_more_menu_complete")
        db.commit()
    finally: db.close()

def seed(page,token):
    payload=json.dumps([token,ACCOUNT])
    page.add_init_script("(()=>{const [t,a]="+payload+";localStorage.setItem('boris_token',t);localStorage.setItem('boris_messenger_token',t);localStorage.setItem('boris_currentAccount',a);localStorage.setItem('account_id',a);})()")

checks=[
 ("Задачи и напоминания","Срочные задачи аккаунта"),
 ("Отчёты и сводки","Спросить BORIS"),
 ("Команда и доступы","Пригласить"),
 ("Уведомления и приложение","Внешний вид"),
 ("Безопасность и документы","Устройства и сеансы"),
]
token,qa_user_id,qa_sid=auth(); atexit.register(cleanup,qa_user_id,qa_sid); results={}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    page=browser.new_page(viewport={"width":1440,"height":900})
    seed(page,token)
    page.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(4500)
    for name,expected in checks:
        more=page.get_by_role("button",name="Ещё",exact=True)
        if more.count()!=1:
            results[name]={"ok":False,"reason":"more_missing"};continue
        more.click();page.wait_for_timeout(120)
        dlg=page.get_by_role("dialog",name="Ещё")
        btn=dlg.get_by_role("button",name=name,exact=False)
        if btn.count()!=1 and name=="Команда и доступы":
            try:
                page.wait_for_function("""()=>{const d=document.querySelector('[role=\"dialog\"][aria-label=\"Ещё\"]'); if(!d)return false; const b=[...d.querySelectorAll('button')].find(x=>x.textContent?.includes('Команда и доступы')); return Boolean(b && !b.disabled);}""",timeout=5000)
                btn=dlg.get_by_role("button",name=name,exact=False)
            except Exception:
                results[name]={"ok":False,"reason":"team_permission_loading_timeout"};page.keyboard.press("Escape");continue
        if btn.count()!=1:
            results[name]={"ok":False,"reason":"item_missing"};page.keyboard.press("Escape");continue
        previous_url=page.url
        btn.click();page.wait_for_timeout(900)
        body=page.locator("body").inner_text()
        section_value={"Задачи и напоминания":"crm","Отчёты и сводки":"reports","Команда и доступы":"team","Уведомления и приложение":"settings","Безопасность и документы":"security"}[name]
        url_ok=("section="+section_value) in page.url
        first_ok=expected in body
        target_url=page.url
        page.go_back(wait_until="domcontentloaded");page.wait_for_timeout(400)
        back_ok=page.url==previous_url
        page.go_forward(wait_until="domcontentloaded")
        page.get_by_text(expected,exact=False).first.wait_for(state="visible",timeout=10000)
        forward_ok=page.url==target_url
        results[name+" Back/Forward"]={"ok":back_ok and forward_ok,"back":back_ok,"forward":forward_ok}
        page.reload(wait_until="domcontentloaded",timeout=30000)
        try:
            page.get_by_text(expected, exact=False).first.wait_for(state="visible", timeout=5000)
            reload_ok=True
        except Exception:
            reload_ok=expected in page.locator("body").inner_text()
        results[name]={"ok":first_ok and url_ok and reload_ok,"expected":expected,"url":page.url,"url_ok":url_ok,"reload_ok":reload_ok}

    # Visual/type-scale acceptance on desktop and mobile.
    for width,height,label in [(320,568,"Typography mobile 320"),(360,800,"Typography mobile 360"),(390,844,"Typography mobile 390"),(430,932,"Typography mobile 430"),(1024,768,"Typography desktop 1024"),(1280,800,"Typography desktop 1280"),(1440,900,"Typography desktop 1440")]:
        page=browser.new_page(viewport={"width":width,"height":height})
        seed(page,token)
        page.goto(WEB+f"/messenger?account_id={ACCOUNT}&section=settings",wait_until="domcontentloaded",timeout=30000)
        page.get_by_role("heading",name="Настройки Messenger",exact=True).wait_for(state="visible",timeout=10000)
        problems=[]
        def font(locator):
            return float(locator.evaluate("el=>parseFloat(getComputedStyle(el).fontSize)"))
        title=page.get_by_role("heading",name="Настройки Messenger",exact=True)
        card_title=page.get_by_text("Внешний вид",exact=True)
        card_meta=page.get_by_text("Тема и акцент Messenger",exact=True)
        segment=page.get_by_role("button",name="Светлая",exact=True)
        preview_body=page.get_by_text("Добрый день! Проверил задачу.",exact=True)
        preview_meta=page.locator('[class*="themePreview"] span').filter(has_text="Сотрудник").first
        fonts={"title":font(title),"card_title":font(card_title),"meta":font(card_meta),"control":font(segment),"body":font(preview_body),"caption":font(preview_meta)}
        if fonts["title"]<20: problems.append("title_lt20")
        if fonts["card_title"]<17: problems.append("card_title_lt17")
        if fonts["meta"]<13 or fonts["caption"]<13: problems.append("secondary_lt13")
        if width<=700:
            if fonts["control"]<16 or fonts["body"]<16: problems.append("mobile_working_text_lt16")
        else:
            if fonts["control"]<15 or fonts["body"]<15: problems.append("desktop_working_text_lt15")
        cards=page.locator('[class*="settingsCard"]')
        appearance=cards.filter(has_text="Внешний вид").first
        push=cards.filter(has_text="Push-уведомления").first
        app=cards.filter(has_text="Приложение BORIS Messenger").first
        access=cards.filter(has_text="Доступ к аккаунтам").first
        boxes={k:v.bounding_box() for k,v in {"appearance":appearance,"push":push,"app":app,"access":access}.items()}
        if any(v is None for v in boxes.values()):
            problems.append("settings_box_missing")
        elif width>900:
            if not (boxes["push"]["x"]>boxes["appearance"]["x"]+100): problems.append("desktop_columns_not_split")
            if boxes["push"]["height"]>=280: problems.append("push_card_too_tall")
            if abs(boxes["push"]["height"]-boxes["appearance"]["height"])<20: problems.append("settings_cards_equal_height")
        else:
            xs=[boxes[k]["x"] for k in ("appearance","push","app","access")]
            if max(xs)-min(xs)>5: problems.append("mobile_not_stacked")
        if page.locator("select").count()!=0: problems.append("native_select_present")
        if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"): problems.append("horizontal_overflow")
        results[label]={"ok":not problems,"fonts":fonts,"boxes":boxes,"native_selects":page.locator("select").count(),"problems":problems}
        page.close()

    # Report destination uses the custom picker, never the system grey select.
    page=browser.new_page(viewport={"width":1440,"height":900})
    seed(page,token)
    page.goto(WEB+f"/messenger?account_id={ACCOUNT}&section=reports",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(2400)
    picker=page.locator('[class*="reportChatSelectButton"]').first
    picker.wait_for(state="visible",timeout=30000)
    picker_ok=picker.count()==1
    option_count=0
    if picker_ok:
        picker.click()
        listbox=page.get_by_role("listbox",name="Выберите чат")
        try:
            listbox.wait_for(state="visible",timeout=3000)
            page.wait_for_function("""()=>document.querySelectorAll('[role=\"listbox\"][aria-label=\"Выберите чат\"] [role=\"option\"]').length>0""",timeout=5000)
        except Exception:
            pass
        listbox.get_by_role("option").first.wait_for(state="visible", timeout=15000)
        option_count=listbox.get_by_role("option").count()
    results["Кастомный список чатов"]={"ok":picker_ok and option_count>0 and page.locator("select").count()==0,"option_count":option_count,"native_selects":page.locator("select").count()}
    page.close()

    page=browser.new_page(viewport={"width":1440,"height":900})
    seed(page,token)
    page.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1200)
    page.get_by_role("button",name="Ещё",exact=True).click()
    page.wait_for_timeout(100)
    btn=page.get_by_role("dialog",name="Ещё").get_by_role("button",name="Открыть основной BORIS",exact=False)
    btn.click();page.wait_for_timeout(900)
    results["Открыть основной BORIS"]={"ok":"/dashboard" in page.url,"url":page.url}
    page.close()
    browser.close()
ok=all(x["ok"] for x in results.values())
cleanup(qa_user_id,qa_sid)
print(json.dumps({"status":"PASS" if ok else "FAIL","results":results},ensure_ascii=False,indent=2))
raise SystemExit(0 if ok else 1)
