#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.api.auth import create_access_token
from app.db.session import SessionLocal
from app.models.user import User

SOURCE_LABEL={
    "telegram_reactivation":"Telegram · реактивация",
    "telegram_prospecting":"Telegram · новые лиды",
    "email_outreach":"Email · рассылка",
}


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    db=SessionLocal()
    try:
        owner=db.query(User).filter(User.role=="owner",User.is_active==True).order_by(User.id).first()
        check(bool(owner),"owner user unavailable")
        total=int(db.execute(text("SELECT count(*) FROM manager_leads WHERE manager_email=:m"),{"m":owner.email}).scalar() or 0)
        rows=db.execute(text("""SELECT lead_source,count(*) n FROM manager_leads
          WHERE manager_email=:m AND lead_source IN ('telegram_reactivation','telegram_prospecting','email_outreach')
          GROUP BY lead_source ORDER BY count(*) DESC,lead_source"""),{"m":owner.email}).mappings().all()
        choices=[(str(r["lead_source"]),int(r["n"] or 0)) for r in rows if int(r["n"] or 0)>0]
        if not choices:
            print("MANAGER_SOURCE_FILTER_UI=SKIP no_source_leads")
            return 0
        preferred=next((x for x in choices if x[0]=="telegram_reactivation"),choices[0])
        source,expected=preferred
        token=create_access_token(owner.id)
    finally:
        db.close()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        ctx=browser.new_context(viewport={"width":1440,"height":1000})

        def rewrite_api(route):
            url=route.request.url
            marker="http://127.0.0.1:3002/api/"
            if url.startswith(marker):
                route.continue_(url="http://127.0.0.1:8000/api/"+url[len(marker):])
            else:
                route.continue_()

        ctx.route("**/api/**",rewrite_api)
        page=ctx.new_page()
        api_events=[]
        api_requests=[]
        api_failed=[]
        console_errors=[]
        page.on("response",lambda resp: api_events.append((resp.status,resp.url)) if "/api/manager/leads" in resp.url else None)
        page.on("request",lambda req: api_requests.append(req.url) if "/api/manager/leads" in req.url else None)
        page.on("requestfailed",lambda req: api_failed.append((req.url,req.failure)) if "/api/manager/leads" in req.url else None)
        page.on("console",lambda msg: console_errors.append(msg.text) if msg.type=="error" else None)
        page.add_init_script("localStorage.setItem('boris_token',"+json.dumps(token)+");")
        page.goto("http://127.0.0.1:3002/manager/clients",wait_until="domcontentloaded",timeout=20000)
        try:
            page.wait_for_selector("[data-manager-source-card]",timeout=15000)
        except Exception:
            print("UI_DEBUG_INITIAL",{
                "url":page.url,
                "requests":api_requests[-12:],
                "failed":api_failed[-12:],
                "events":api_events[-12:],
                "console":console_errors[-12:],
                "body":page.locator("body").inner_text()[:1200],
            })
            raise

        initial=page.locator('[data-manager-lead-card="1"]').count()
        check(initial==total,f"initial lead count mismatch ui={initial} db={total}")

        card=page.locator(f'[data-manager-source-card="{source}"]')
        check(card.count()==1,f"source card missing: {source}")
        card.click()
        page.wait_for_timeout(1800)
        filtered=page.locator('[data-manager-lead-card="1"]').count()
        hero=page.get_by_text("Всего карточек:",exact=False).first.inner_text() if page.get_by_text("Всего карточек:",exact=False).count() else ""
        if filtered!=expected:
            print("UI_DEBUG_FILTER",{"source":source,"expected":expected,"filtered":filtered,"hero":hero,"requests":api_requests[-8:],"failed":api_failed[-8:],"events":api_events[-8:],"console":console_errors[-8:]})
        check(filtered==expected,f"filtered lead count mismatch ui={filtered} db={expected}")
        check(page.get_by_text("Показан канал:").count()>=1,"active source filter indicator missing")

        search=page.locator('[data-manager-lead-search="1"]')
        search.fill("__BORIS_QA_NO_MATCH_7F4D__")
        page.wait_for_function("() => document.querySelectorAll('[data-manager-lead-card=\"1\"]').length === 0",timeout=10000)
        search.fill("")
        page.wait_for_function("n => document.querySelectorAll('[data-manager-lead-card=\"1\"]').length === n",arg=expected,timeout=10000)

        card=page.locator(f'[data-manager-source-card="{source}"]')
        card.click()
        page.wait_for_function("n => document.querySelectorAll('[data-manager-lead-card=\"1\"]').length === n",arg=total,timeout=10000)
        restored=page.locator('[data-manager-lead-card="1"]').count()
        check(restored==total,"source filter did not restore full CRM list")

        browser.close()
    print(f"MANAGER_SOURCE_FILTER_UI=PASS source={source} label={SOURCE_LABEL.get(source,source)} initial={total} filtered={expected}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
