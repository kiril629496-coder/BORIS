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
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro";ACCOUNT="planetazayavki_65985"
VPS=[(360,800,"mobile-360"),(390,844,"mobile-390"),(1024,768,"desktop-1024"),(1280,800,"desktop-1280"),(1440,900,"desktop-1440")]

def auth():
    db=SessionLocal()
    try:
        owner=next((u for u in db.query(User).all() if is_private_platform_owner(u)),None)
        if owner is None or not acceptance_status(db,int(owner.id)).get("accepted"): raise RuntimeError("owner/legal unavailable")
        return create_access_token(int(owner.id),security_epoch=int(getattr(owner,"security_epoch",0) or 0))
    finally: db.close()

def seed(page,token):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('boris_messenger_token',t);localStorage.setItem('boris_currentAccount',a);localStorage.setItem('account_id',a)}""",[token,ACCOUNT])

token=auth();results={};fail=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
    for w,h,label in VPS:
        page=browser.new_page(viewport={"width":w,"height":h})
        report_network=[]
        page.on("response",lambda r: report_network.append({"status":r.status,"path":r.url.split("?",1)[0]}) if "/api/messenger-reports/" in r.url else None)
        seed(page,token)
        page.goto(WEB+"/messenger?account_id="+ACCOUNT,wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(4500)
        problems=[]
        connection=""
        labels=page.locator('[class*="connectionLabel"]')
        if labels.count(): connection=labels.first.inner_text()
        if connection!="Онлайн": problems.append("realtime_not_online:"+connection)
        more=page.get_by_role("button",name="Ещё",exact=True)
        if more.count()!=1: problems.append("more_missing")
        else:
            more.click();page.wait_for_timeout(150)
            report_btn=page.get_by_role("button",name="Отчёты и сводки")
            if report_btn.count()!=1: problems.append("reports_nav_missing")
            else:
                report_btn.click();page.wait_for_timeout(2500)
        body=page.locator("body").inner_text()
        for txt in ("Отчёты и сводки","Спросить BORIS","Утренний отчёт","Деньги","Реклама","Реактивация","A/B-тесты"):
            if txt not in body: problems.append("reports_missing:"+txt)
        if not any(x["status"]==200 and x["path"].endswith("/api/messenger-reports/summary") for x in report_network):
            problems.append("summary_http_200_missing")
        finance=page.locator('button').filter(has_text="Баланс, аванс и расходы").first
        if finance.count():
            finance.click()
            try:
                page.get_by_text("Финансы аккаунта", exact=True).wait_for(state="visible", timeout=5000)
                page.get_by_text("Не раскрывается API", exact=True).wait_for(state="visible", timeout=5000)
            except Exception:
                finance_body=page.locator("body").inner_text()
                if "Финансы аккаунта" not in finance_body: problems.append("finance_title_missing")
                if "Не раскрывается API" not in finance_body: problems.append("advance_truth_missing")
        else: problems.append("finance_button_missing")
        ab_card=page.locator('button').filter(has_text="Гипотезы и результаты").first
        if ab_card.count():
            ab_card.click()
            try:
                page.get_by_text("A/B-тесты объявлений", exact=True).wait_for(state="visible", timeout=5000)
                p60=page.get_by_role("button",name="60 дней",exact=True)
                if p60.count():
                    p60.click()
                    page.get_by_text("Подготовлено · ещё не измерялось", exact=False).first.wait_for(state="visible", timeout=5000)
                else:
                    problems.append("ab_period_60_missing")
            except Exception:
                problems.append("ab_prepared_hypotheses_missing")
        else:
            problems.append("ab_card_missing")
        more.click();page.wait_for_timeout(120)
        settings_btn=page.get_by_role("button",name="Уведомления и приложение")
        if settings_btn.count()!=1: problems.append("settings_nav_missing")
        else:
            settings_btn.click();page.wait_for_timeout(500)
        settings_body=page.locator("body").inner_text()
        for txt in ("Настройки Messenger","Внешний вид","Push-уведомления","Приложение BORIS Messenger","Доступ к аккаунтам"):
            if txt not in settings_body: problems.append("settings_missing:"+txt)
        settings_cards=page.locator('[class*="settingsCard"]').count()
        if settings_cards<4: problems.append("settings_cards_lt4")
        access=page.locator('[class*="settingsLinkRow"]').filter(has_text="Планета Мебели").first
        access_open=False
        if access.count():
            access.click();page.wait_for_timeout(150)
            access_open=page.get_by_role("dialog",name="Доступ к аккаунтам").count()==1
            if not access_open: problems.append("access_drawer_missing")
            elif "Можно смотреть отчёты" not in page.locator("body").inner_text(): problems.append("access_report_permission_missing")
        else: problems.append("access_button_missing")
        overflow=page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        if overflow: problems.append("horizontal_overflow")
        results[label]={"connection":connection,"report_network":report_network,"settings_cards":settings_cards,"access_open":access_open,"overflow_x":overflow,"problems":problems}
        fail.extend(label+":"+p for p in problems)
        page.close()
    browser.close()
print(json.dumps({"status":"PASS" if not fail else "FAIL","account":ACCOUNT,"results":results,"failures":fail},ensure_ascii=False,indent=2))
raise SystemExit(0 if not fail else 1)
