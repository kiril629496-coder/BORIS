#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production contract for Ocean Clients social autoposting."""
import inspect
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models.storage import Storage
import posting_runner as pr
import boris_background_worker as bw


def fail(msg):
    raise AssertionError(msg)


def main():
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == "u2", Storage.key == "posting_projects").first()
        if not row:
            fail("posting_projects missing for u2")
        projects = json.loads(row.value or "[]")
        ocean = next((p for p in projects if "Океан Клиент" in str(p.get("name") or "")), None)
        if not ocean:
            fail("Ocean Clients project missing")

        checks = {
            "platforms_both": ocean.get("platforms") == "both",
            "telegram_target": bool(ocean.get("channel_tg")),
            "vk_target": bool(ocean.get("vk_owner_id")),
            "banner_required": str(ocean.get("image_source") or "").lower() == "ai",
            "paid_auto_generation_optin": ocean.get("paid_auto_generation") is True,
            "auto_publish_delay": int(ocean.get("auto_publish_hours") or 0) > 0,
            "mobile_banner_rule": "МОБИЛЬНАЯ ЧИТАЕМОСТЬ ОБЯЗАТЕЛЬНА" in str(ocean.get("banner_prompt") or ""),
            "every_post_rule": "ЖЕЛЕЗНОЕ ПРАВИЛО ДЛЯ КАЖДОГО НОВОГО ПОСТА" in str(ocean.get("text_prompt") or ""),
        }
        for key, ok in checks.items():
            if not ok:
                fail("config check failed: " + key)

        sample = ("Почему владелец бизнеса не должен вручную собирать статистику из пяти сервисов. " * 40).strip()
        final = pr._project_delivery_text(sample, ocean, "tg")
        if len(final) > 1000:
            fail("telegram media caption over 1000 chars: %s" % len(final))
        if final.count("Бесплатный тест софта BORIS — 3 дня") != 1:
            fail("mandatory 3-day trial phrase must exist exactly once")
        if final.count("https://boris-ai.pro/login") != 1:
            fail("login footer must exist exactly once")
        if final.count("8 981 967-37-87") != 1:
            fail("phone footer must exist exactly once")
        if sum(1 for x in final.split() if x.startswith("#")) < 5:
            fail("minimum five thematic hashtags missing")
        if "AI-Авитолог" not in final or "коллтрекинг" not in final or "сквозная аналитика" not in final:
            fail("module summary missing")
        if "youtu.be/HqufxtLVOBU" not in final and "rutube.ru/video/df9cd0774a0a73bb1fb88bc1d3d3363e" not in final:
            fail("video link missing")

        req = pr._required_delivery_platforms(ocean)
        if req != ["tg", "vk"]:
            fail("required delivery platforms incorrect: %r" % (req,))
        if pr._all_required_delivery_ok(ocean, {"tg": {"ok": True}, "vk": {"ok": False}}):
            fail("partial delivery incorrectly accepted")
        if not pr._all_required_delivery_ok(ocean, {"tg": {"ok": True}, "vk": {"ok": True}}):
            fail("complete delivery rejected")
        merged = pr._merge_delivery_ids({}, ocean, {
            "tg": {"ok": True, "result": {"message_id": 999}},
            "vk": {"ok": True, "post_id": 888},
        })
        if merged.get("tg", {}).get("message_id") != 999 or merged.get("vk", {}).get("post_id") != 888:
            fail("delivery id merge failed")

        due_src = inspect.getsource(pr.due_posts_all)
        if 'not in ("draft", "partial")' not in due_src or "skipped_existing" not in due_src:
            fail("partial retry / no-duplicate contract missing in due_posts_all")
        auto_src = inspect.getsource(pr.autopost_projects)
        if "_all_required_delivery_ok" not in auto_src or "PARTIAL_DELIVERY" not in auto_src:
            fail("immediate publish all-platform contract missing")
        worker_src = inspect.getsource(bw._social_posting_loop)
        if "autopost_all(respect_time=True)" not in worker_src or "due_posts_all(dry=False)" not in worker_src:
            fail("background social scheduler missing")
        vk_src = inspect.getsource(pr._send_vk)
        if "proxies=" in vk_src:
            fail("VK media transport still forces proxy")
        from app.api import posting as posting_api
        api_src = inspect.getsource(posting_api._enforce_project_content_contract)
        if "ЖЕЛЕЗНОЕ ПРАВИЛО ДЛЯ КАЖДОГО НОВОГО ПОСТА" not in api_src or "МОБИЛЬНАЯ ЧИТАЕМОСТЬ ОБЯЗАТЕЛЬНА" not in api_src:
            fail("UI-save content contract missing")
        restored = posting_api._enforce_project_content_contract({"name": "Океан Клиентов и Борис", "text_prompt": "", "banner_prompt": ""})
        if "ЖЕЛЕЗНОЕ ПРАВИЛО ДЛЯ КАЖДОГО НОВОГО ПОСТА" not in restored.get("text_prompt", ""):
            fail("UI-save does not restore mandatory post rule")
        if "МОБИЛЬНАЯ ЧИТАЕМОСТЬ ОБЯЗАТЕЛЬНА" not in restored.get("banner_prompt", ""):
            fail("UI-save does not restore mobile banner rule")

        posts_row = db.query(Storage).filter(Storage.account_id == "u2", Storage.key == "posting_posts").first()
        posts = json.loads(posts_row.value or "[]") if posts_row else []
        ocean_published = [x for x in posts if x.get("project_name") == ocean.get("name") and x.get("status") == "published"]
        current = ocean_published[0] if ocean_published else None
        if not current:
            fail("no published Ocean post found")
        ids = current.get("ids") or {}
        tg_id = ids.get("tg", {}).get("message_id")
        vk_id = ids.get("vk", {}).get("post_id")
        if not tg_id or not vk_id:
            fail("latest Ocean post is not completely delivered to TG+VK")
        banner = str(current.get("banner") or "")
        path = Path(pr.BANNER_DIR) / banner
        if not banner or not path.exists() or path.stat().st_size <= 0:
            fail("latest Ocean banner missing")
        live_text = pr._project_delivery_text(current.get("text") or "", ocean, "tg")
        required_live = (
            "Бесплатный тест софта BORIS — 3 дня",
            "https://boris-ai.pro/login",
            "8 981 967-37-87",
            "AI-Авитолог",
            "анализ звонков",
            "Command Center",
        )
        if len(live_text) > 1000 or not all(x in live_text for x in required_live):
            fail("latest Ocean content contract failed")

        print("OCEAN_AUTOPOST_QA=PASS")
        print("caption_chars=", len(final))
        print("required_platforms=", ",".join(req))
        print("scheduler=singleton_worker")
        print("current_ids=tg:%s,vk:%s" % (tg_id, vk_id))
    finally:
        db.close()


if __name__ == "__main__":
    main()
