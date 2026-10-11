#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser persistence acceptance for the canonical Campaign QA fixture.

Mutates only Campaign #504 / testref_47784, verifies save->reload and cancel,
then restores the exact DB snapshot in finally. No publication/provider action.
"""
import copy
import json
import sys
import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, "/root/BORIS/backend")
import qa_suite
from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.api.campaigns import _ff_state_load, _ff_state_save

WEB = "https://boris-ai.pro"
ACCOUNT = "testref_47784"
CAMPAIGN_ID = 504
ITEM_ID = 5460


def api_session():
    session = requests.Session()
    response = session.post(
        WEB + "/api/auth/login",
        json={"email": qa_suite.OWNER[0], "password": qa_suite.OWNER[1]},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return session, {
        "Authorization": "Bearer " + data["access_token"],
    }, data


def fetch_item(session, headers):
    response = session.get(
        WEB + f"/api/campaign/{CAMPAIGN_ID}/items",
        headers=headers,
        params={"account_id": ACCOUNT, "limit": 100, "offset": 0},
        timeout=30,
    )
    response.raise_for_status()
    return next(
        item for item in response.json().get("items", [])
        if int(item.get("id") or 0) == ITEM_ID
    )


def main():
    db = SessionLocal()
    row = db.query(CampaignItem).filter(CampaignItem.id == ITEM_ID).first()
    campaign = db.query(Campaign).filter(Campaign.id == CAMPAIGN_ID).first()
    if not row or not campaign:
        raise RuntimeError("canonical QA fixture missing")
    snapshot = {
        "payload_json": row.payload_json,
        "status": row.status,
        "content_hash": row.content_hash,
        "hypothesis_id": row.hypothesis_id,
        "feed_identity": row.feed_identity,
    }
    _, ff = _ff_state_load(campaign)
    ff_snapshot = copy.deepcopy(ff)
    db.close()

    session, headers, auth = api_session()
    original_item = fetch_item(session, headers)
    original = original_item.get("payload") or {}
    baseline = {
        "title": str(original.get("title") or ""),
        "price": str(original.get("price") or ""),
        "template_id": str(original.get("template_id") or ""),
        "params": copy.deepcopy(original.get("params") or {}),
    }
    out = {"status": "PASS", "campaign_id": CAMPAIGN_ID, "item_id": ITEM_ID, "checks": {}, "failures": []}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(WEB + "/login", wait_until="domcontentloaded", timeout=30000)
            page.evaluate(
                """([token,email,role,account])=>{
                    localStorage.setItem('boris_token',token);
                    localStorage.setItem('boris_user_email',email);
                    localStorage.setItem('boris_user_role',role);
                    localStorage.setItem('boris_currentAccount',account);
                }""",
                [
                    auth["access_token"],
                    qa_suite.OWNER[0],
                    (auth.get("user") or {}).get("role", "owner"),
                    ACCOUNT,
                ],
            )
            page.goto(
                WEB + f"/dashboard/campaign/{CAMPAIGN_ID}?account_id={ACCOUNT}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(2200)
            card = page.locator(f"#item-{ITEM_ID}")
            card.scroll_into_view_if_needed()
            card.get_by_role("button", name="Изменить при необходимости").click()
            page.wait_for_timeout(200)

            title = card.locator('input[placeholder="Заголовок"]')
            price = card.locator('input[placeholder="Цена"]')
            category = card.locator("select")
            new_title = baseline["title"] + " · QA PERSIST"
            new_price = str(int(float(baseline["price"] or "2500")) + 2)
            options = category.locator("option").evaluate_all("els=>els.map(e=>e.value)")
            alt_template = next(value for value in options if value and value != baseline["template_id"])

            title.fill(new_title)
            price.fill(new_price)
            category.select_option(alt_template)
            page.wait_for_timeout(120)
            dirty = card.get_by_role("status").inner_text()
            out["checks"]["dirty_state"] = (
                "Есть несохранённые изменения" in dirty and "Не удалось" not in dirty
            )

            card.get_by_role("button", name="Сохранить").click()
            page.wait_for_timeout(1600)
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1800)
            card = page.locator(f"#item-{ITEM_ID}")
            card.scroll_into_view_if_needed()
            card.get_by_role("button", name="Изменить при необходимости").click()
            page.wait_for_timeout(150)

            got = {
                "title": card.locator('input[placeholder="Заголовок"]').input_value(),
                "price": card.locator('input[placeholder="Цена"]').input_value(),
                "template_id": card.locator("select").input_value(),
            }
            out["checks"]["save_reload"] = got == {
                "title": new_title,
                "price": new_price,
                "template_id": alt_template,
            }

            persisted = fetch_item(session, headers).get("payload") or {}
            out["checks"]["category_params_reset"] = (
                str(persisted.get("template_id") or "") == alt_template
                and (persisted.get("params") or {}) != baseline["params"]
            )

            card.locator('input[placeholder="Заголовок"]').fill(new_title + " DIRTY")
            page.wait_for_timeout(100)
            card.get_by_role("button", name="Отмена").click()
            page.wait_for_timeout(150)
            card.get_by_role("button", name="Изменить при необходимости").click()
            page.wait_for_timeout(100)
            out["checks"]["cancel"] = (
                card.locator('input[placeholder="Заголовок"]').input_value() == new_title
            )
            browser.close()
    finally:
        db = SessionLocal()
        row = db.query(CampaignItem).filter(CampaignItem.id == ITEM_ID).first()
        campaign = db.query(Campaign).filter(Campaign.id == CAMPAIGN_ID).first()
        row.payload_json = snapshot["payload_json"]
        row.status = snapshot["status"]
        row.content_hash = snapshot["content_hash"]
        row.hypothesis_id = snapshot["hypothesis_id"]
        row.feed_identity = snapshot["feed_identity"]
        db.commit()
        _ff_state_save(db, campaign, ff_snapshot)
        db.close()

        restored = fetch_item(session, headers).get("payload") or {}
        out["checks"]["restored"] = (
            str(restored.get("title") or "") == baseline["title"]
            and str(restored.get("price") or "") == baseline["price"]
            and str(restored.get("template_id") or "") == baseline["template_id"]
            and (restored.get("params") or {}) == baseline["params"]
        )

    out["failures"] = [name for name, passed in out["checks"].items() if not passed]
    out["status"] = "PASS" if not out["failures"] else "FAIL"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("/root/BORIS/backend/run/campaign_browser_persistence_504_final.json", "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    return 0 if out["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
