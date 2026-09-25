# -*- coding: utf-8 -*-
import inspect
import json
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.api import posting as api
import posting_runner as pr


class _User:
    def __init__(self, uid, role="owner"):
        self.id = uid
        self.role = role


class SocialPostingHAContractTest(unittest.TestCase):
    UID = 987654321
    ACCOUNT = "u987654321"

    def tearDown(self):
        db = SessionLocal()
        try:
            db.query(Storage).filter(Storage.account_id == self.ACCOUNT).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
        api._RUN_JOBS.clear()

    def _seed_posts_locked(self, posts):
        db = SessionLocal()
        try:
            api._load(db, self.ACCOUNT, "posting_posts", [], for_update=True)
            api._save(db, self.ACCOUNT, "posting_posts", posts)
        finally:
            db.close()

    def test_job_state_is_postgres_authoritative_and_tenant_scoped(self):
        api._save_run_job(self.ACCOUNT, "qa_job_1", {
            "done": False,
            "log": "running",
            "project_id": "ocean",
        })
        api._RUN_JOBS.clear()  # simulate request reaching another API process
        loaded = api._load_run_job(self.ACCOUNT, "qa_job_1")
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded.get("project_id"), "ocean")
        self.assertEqual(loaded.get("account_id"), self.ACCOUNT)
        active = api._active_run_job(self.ACCOUNT, "ocean")
        self.assertEqual(active.get("job_id"), "qa_job_1")

        own = api.run_status("qa_job_1", _cur=_User(self.UID))
        self.assertEqual(own.get("status"), "ok")
        foreign = api.run_status("qa_job_1", _cur=_User(self.UID + 1))
        self.assertEqual(foreign.get("status"), "unknown")

    def test_duplicate_manual_run_claim_returns_same_active_job(self):
        first, created1 = api._claim_run_job(self.ACCOUNT, "ocean", "job_first")
        second, created2 = api._claim_run_job(self.ACCOUNT, "ocean", "job_second")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.get("job_id"), "job_first")
        self.assertEqual(second.get("job_id"), "job_first")

    def test_editing_ocean_draft_rebuilds_real_delivery_preview(self):
        db = SessionLocal()
        try:
            api._save(db, self.ACCOUNT, "posting_projects", [{
                "id": "ocean",
                "name": "Океан Клиентов и Борис",
                "platforms": "both",
                "channel_tg": "@qa",
                "vk_owner_id": -1,
                "image_source": "ai",
                "footer_signature": "Если хотите проверить BORIS на своей задаче — настроим тест бесплатно. MAX / WhatsApp: 8 981 967-37-87.",
            }])
            api._load(db, self.ACCOUNT, "posting_posts", [], for_update=True)
            api._save(db, self.ACCOUNT, "posting_posts", [{
                "id": "post1",
                "project_id": "ocean",
                "project_name": "Океан Клиентов и Борис",
                "status": "draft",
                "text": "old",
                "preview": "old",
                "ids": {},
            }])
        finally:
            db.close()

        result = api.update_post(
            api.PostEditBody(post_id="post1", text="Новый короткий пост про автоматизацию продаж"),
            _cur=_User(self.UID),
        )
        self.assertEqual(result.get("status"), "ok")
        preview = result.get("preview") or ""
        self.assertIn("Новый короткий пост про автоматизацию продаж", preview)
        self.assertIn("Если хотите проверить BORIS на своей задаче — настроим тест бесплатно. MAX / WhatsApp: 8 981 967-37-87.", preview)
        self.assertNotIn("Настройка BORIS и сопровождение первого месяца — бесплатно", preview)
        self.assertIn("🤖 BORIS: https://boris-ai.pro/?utm_source=tg&utm_medium=ocean&utm_campaign=boris", preview)
        self.assertIn("💻 Разработка ПО: https://boris-ai.pro/software-dev/?utm_source=tg&utm_medium=ocean&utm_campaign=dev", preview)
        self.assertEqual(preview.count("utm_campaign=boris"), 1)
        self.assertEqual(preview.count("utm_campaign=dev"), 1)
        self.assertIn("#BORIS", preview)
        self.assertNotIn("http://", preview.lower())
        self.assertNotIn("youtu.be", preview.lower())
        self.assertNotIn("rutube.ru", preview.lower())
        self.assertLessEqual(len(preview), 1000)

    def test_unpublish_endpoint_is_real_and_delete_vk_is_direct(self):
        paths = {getattr(r, "path", "") for r in api.router.routes}
        self.assertIn("/api/posting/posts/unpublish", paths)
        src = inspect.getsource(pr.delete_vk_post)
        self.assertNotIn("proxies=", src)
        self.assertIn("wall.delete", src)

    def test_unpublish_partial_retry_only_targets_remaining_platform(self):
        db = SessionLocal()
        try:
            api._load(db, self.ACCOUNT, "posting_posts", [], for_update=True)
            api._save(db, self.ACCOUNT, "posting_posts", [{
                "id": "published1",
                "project_id": "ocean",
                "status": "published",
                "text": "test",
                "ids": {
                    "tg": {"chat_id": "@qa", "message_id": 11},
                    "vk": {"owner_id": -22, "post_id": 33},
                },
            }])
        finally:
            db.close()

        with patch.object(pr, "delete_telegram_post", return_value={"ok": True}) as dtg, \
             patch.object(pr, "delete_vk_post", return_value={"ok": False, "error": "temporary"}) as dvk:
            first = api.unpublish_post(api.PostActionBody(account_id="", post_id="published1"), _cur=_User(self.UID))
        self.assertEqual(first.get("status"), "partial")
        dtg.assert_called_once()
        dvk.assert_called_once()

        db = SessionLocal()
        try:
            rec = api._load(db, self.ACCOUNT, "posting_posts", [])[0]
        finally:
            db.close()
        self.assertEqual(rec.get("status"), "published")
        self.assertNotIn("tg", rec.get("ids") or {})
        self.assertIn("vk", rec.get("ids") or {})

        with patch.object(pr, "delete_telegram_post", return_value={"ok": True}) as dtg2, \
             patch.object(pr, "delete_vk_post", return_value={"ok": True}) as dvk2:
            second = api.unpublish_post(api.PostActionBody(account_id="", post_id="published1"), _cur=_User(self.UID))
        self.assertEqual(second.get("status"), "ok")
        dtg2.assert_not_called()
        dvk2.assert_called_once()

        db = SessionLocal()
        try:
            rec2 = api._load(db, self.ACCOUNT, "posting_posts", [])[0]
        finally:
            db.close()
        self.assertEqual(rec2.get("status"), "unpublished")
        self.assertEqual(rec2.get("ids"), {})

    def test_scheduler_passes_project_id_and_uses_project_local_day(self):
        src = inspect.getsource(pr._autopost_projects_impl)
        self.assertIn("project_id=pid", src)
        self.assertIn("_project_offset", src)
        self.assertIn("timedelta(hours=_project_offset)", src)

    def test_projects_api_reads_daily_counter_in_project_timezone(self):
        src = inspect.getsource(api.get_projects)
        self.assertIn("tz_offset", src)
        self.assertIn("timedelta(hours=int(p.get", src)
        self.assertIn("%Y%m%d", src)

    def test_manual_publish_keeps_history_record(self):
        src = inspect.getsource(api.publish_post)
        helper = inspect.getsource(pr._mark_social_delivery_published)
        self.assertIn('_mark_social_delivery_published', src)
        self.assertIn('rec["status"] = "published"', helper)
        self.assertIn('rec["preview"] = preview_text', helper)
        self.assertNotIn('posts = [p for p in posts if p.get("id") != body.post_id]', src)
        self.assertIn('fallback=pr._project_delivery_text', src)
        self.assertIn('already_published', src)
        self.assertIn('(\"draft\", \"partial\")', src)

    def test_edit_schedule_and_local_delete_lock_after_delivery(self):
        db = SessionLocal()
        try:
            api._load(db, self.ACCOUNT, "posting_posts", [], for_update=True)
            api._save(db, self.ACCOUNT, "posting_posts", [{
                "id": "delivered",
                "project_id": "ocean",
                "status": "published",
                "text": "already live",
                "preview": "already live",
                "ids": {"tg": {"chat_id": "@qa", "message_id": 77}},
            }])
        finally:
            db.close()
        user = _User(self.UID)
        upd = api.update_post(api.PostEditBody(post_id="delivered", text="changed"), _cur=user)
        self.assertEqual(upd.get("status"), "locked_after_delivery")
        sch = api.schedule_post(api.PostScheduleBody(post_id="delivered", publish_at="2026-09-03T12:00"), _cur=user)
        self.assertEqual(sch.get("status"), "locked_after_delivery")
        dele = api.delete_post(api.PostActionBody(account_id="", post_id="delivered"), _cur=user)
        self.assertEqual(dele.get("status"), "external_publication_exists")

    def test_schedule_rejects_invalid_datetime(self):
        result = api.schedule_post(api.PostScheduleBody(post_id="none", publish_at="not-a-date"), _cur=_User(self.UID))
        self.assertEqual(result.get("status"), "invalid_datetime")

    def test_pexels_source_does_not_call_paid_image_title_path(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "qa_pexels.jpg"
            fake.write_bytes(b"x" * 20000)
            with patch.object(pr, "_openai_text", return_value="Короткий тестовый пост"), \
                 patch.object(pr, "_generate_pexels_social_asset", return_value=str(fake)) as stock, \
                 patch.object(pr, "_make_title") as title:
                result = pr.generate_post(self.ACCOUNT, "автоматизация продаж", image_source="pexels")
            self.assertEqual(result.get("banner_engine"), "pexels")
            self.assertEqual(result.get("banner"), str(fake))
            stock.assert_called_once()
            title.assert_not_called()

    def test_banner_validator_rejects_corrupt_and_accepts_real_image(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.jpg"
            bad.write_bytes(b"x" * 20000)
            ok, err = pr._banner_delivery_ready({"banner": str(bad)}, "ai")
            self.assertFalse(ok)
            self.assertIn("INVALID", err)

            good = Path(td) / "good.jpg"
            Image.effect_noise((512, 512), 100).convert("RGB").save(good, "JPEG", quality=92)
            self.assertGreater(good.stat().st_size, 16 * 1024)
            ok2, err2 = pr._banner_delivery_ready({"banner": str(good)}, "ai")
            self.assertTrue(ok2)
            self.assertIsNone(err2)


if __name__ == "__main__":
    unittest.main()
