import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import email_queue
from app.services import email_tracking
from app.api import prospecting_scale


class EmailOpenTrackingContractTest(unittest.TestCase):
    def test_pixel_is_transparent_gif_and_uses_public_first_party_url(self):
        self.assertTrue(email_tracking.PIXEL_GIF.startswith(b"GIF89a"))
        tag = email_tracking._pixel_tag("x" * 32)
        self.assertIn('data-boris-open-tracking="1"', tag)
        self.assertIn("/api/prospecting/scale/open/", tag)

    def test_proxy_hint_is_explicit_not_all_mail_clients(self):
        self.assertTrue(email_tracking._proxy_hint("GoogleImageProxy"))
        self.assertTrue(email_tracking._proxy_hint("Mozilla/5.0 (compatible; YandexImageResizer/2.0)"))
        self.assertFalse(email_tracking._proxy_hint("Mozilla/5.0 Thunderbird"))

    def test_prospect_enqueue_injects_tracking_fail_closed(self):
        src = inspect.getsource(email_queue.enqueue_email)
        self.assertIn('source == "prospect_campaign"', src)
        self.assertIn("ensure_tracker_and_inject", src)
        deliver = inspect.getsource(email_queue._deliver)
        self.assertIn("tracking_ready", deliver)
        self.assertIn("ensure_tracker_and_inject", deliver)
        self.assertIn("open_tracking_self_healed", deliver)
        self.assertIn("OPEN_TRACKING_REQUIRED", deliver)

    def test_tracking_workstream_is_single_writer_and_shared_core_is_foreign(self):
        from app.services.workstream_scope import load_registry, registry_overlaps, writable_owners
        registry = load_registry()
        self.assertEqual(registry_overlaps(registry), [])
        self.assertEqual(writable_owners("app/services/email_tracking.py", registry), ["email_tracking_analytics"])
        self.assertEqual(writable_owners("migrations/040_email_open_tracking.sql", registry), ["email_tracking_analytics"])
        self.assertEqual(writable_owners("tests/test_email_open_tracking_contract.py", registry), ["email_tracking_analytics"])
        self.assertEqual(writable_owners("app/services/email_queue.py", registry), ["email_outreach_core"])
        self.assertEqual(writable_owners("app/services/prospect_campaigns.py", registry), ["email_outreach_core"])
        self.assertEqual(writable_owners("../frontend/app/email-outreach/page.tsx", registry), ["frontend_mobile_ui"])

    def test_public_pixel_endpoint_and_campaign_funnel_are_wired(self):
        route = inspect.getsource(prospecting_scale.open_pixel)
        self.assertIn("record_open", route)
        self.assertIn("no-store", route)
        campaigns = inspect.getsource(prospecting_scale.campaigns)
        self.assertIn("campaign_tracking_stats", campaigns)
        details = inspect.getsource(prospecting_scale.stats)
        self.assertIn("include_subjects=True", details)

    def test_owner_contract_allows_only_exact_tracking_pixel_url(self):
        from app.services import prospect_campaigns
        body = prospect_campaigns._render(
            prospect_campaigns.DEFAULT_OWNER_OUTREACH_BODY if hasattr(prospect_campaigns, "DEFAULT_OWNER_OUTREACH_BODY") else
            """Мы собираем базу целевых контактов.
Проверяем и очищаем её.
Формируем предложения и письма.
Запускаем email-рассылку.
Контролируем ответы.
От поиска контактов до первых диалогов и заявок.
BORIS AI""",
            {},
        )
        # Use a known-good body from the production campaign instead of
        # weakening the owner content contract in this unit.
        from sqlalchemy import text
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            body = db.execute(text("SELECT body_template FROM prospect_campaigns WHERE id=9")).scalar_one()
        finally:
            db.close()
        token = "x" * 32
        tracked = "<html><body>" + body + email_tracking._pixel_tag(token) + "</body></html>"
        errors = prospect_campaigns._owner_outreach_contract(
            "Сколько стоит 1 пропущенная заявка?", body, tracked, []
        )
        self.assertNotIn("url_forbidden", errors)
        other = tracked.replace("</body>", '<a href="https://example.com">x</a></body>')
        errors2 = prospect_campaigns._owner_outreach_contract(
            "Сколько стоит 1 пропущенная заявка?", body, other, []
        )
        self.assertIn("url_forbidden", errors2)

    def test_record_open_dedupes_same_user_agent_burst(self):
        class FakeResult:
            def __init__(self, row=None):
                self.row = row
            def first(self):
                return self.row

        class FakeDB:
            def __init__(self):
                self.writes = []
                self.commits = 0
                self.closed = False
            def execute(self, stmt, params=None):
                sql = str(stmt)
                if "SELECT id FROM email_open_trackers" in sql:
                    return FakeResult((7,))
                if "SELECT 1 FROM email_open_events" in sql:
                    return FakeResult((1,))
                if "INSERT INTO email_open_events" in sql or "UPDATE email_open_trackers" in sql:
                    self.writes.append((sql, params))
                return FakeResult()
            def commit(self):
                self.commits += 1
            def close(self):
                self.closed = True

        db = FakeDB()
        with patch.object(email_tracking, "ensure_schema", lambda: None),              patch.object(email_tracking, "SessionLocal", return_value=db):
            self.assertTrue(email_tracking.record_open("x" * 32, "Mozilla/5.0"))
        self.assertEqual(db.writes, [])
        self.assertEqual(db.commits, 0)
        self.assertTrue(db.closed)

    def test_likely_human_latency_ignores_known_proxy_first_fetch(self):
        src = inspect.getsource(email_tracking.campaign_tracking_stats)
        self.assertIn("first_likely_human_open_at", src)
        self.assertIn("LIKELY_HUMAN_MIN_DELAY_SECONDS", src)
        self.assertIn("likely_human_repeat_openers", src)
        self.assertIn("fast_nonproxy_only_opened_unique", src)
        self.assertNotIn(
            "t.first_opened_at - (q.sent_at AT TIME ZONE 'UTC')",
            src,
        )

    def test_stats_exclude_noncanonical_sent_rows_and_surface_integrity_warning(self):
        src = inspect.getsource(email_tracking.campaign_tracking_stats)
        self.assertIn("orphan_sent_without_queue", src)
        self.assertIn("q.id IS NOT NULL", src)
        self.assertIn("data_integrity_warning", src)

    def test_reply_latency_index_is_declared_in_runtime_and_migration(self):
        ensure_src = inspect.getsource(email_tracking.ensure_schema)
        self.assertIn("ix_prospect_replies_member_human_received", ensure_src)
        migration = Path(
            "/root/BORIS/backend/migrations/040_email_open_tracking.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ix_prospect_replies_member_human_received", migration)

    def test_basic_html_gets_one_marker(self):
        class FakeResult:
            def __init__(self, row=None):
                self.row = row
            def first(self):
                return self.row

        class FakeDB:
            def __init__(self):
                self.token = None
            def execute(self, stmt, params=None):
                sql = str(stmt)
                if "SELECT token FROM email_open_trackers" in sql:
                    return FakeResult((self.token,) if self.token else None)
                if "INSERT INTO email_open_trackers" in sql:
                    self.token = params["t"]
                    return FakeResult()
                return FakeResult()

        old = email_tracking.ensure_schema
        email_tracking.ensure_schema = lambda: None
        try:
            db = FakeDB()
            html = email_tracking.ensure_tracker_and_inject(db, 1, "<html><body>Hi</body></html>", "Hi")
            self.assertEqual(html.count(email_tracking.TRACKING_MARKER), 1)
            html2 = email_tracking.ensure_tracker_and_inject(db, 1, html, "Hi")
            self.assertEqual(html2.count(email_tracking.TRACKING_MARKER), 1)
            stale = (
                '<html><body>Hi'
                '<img src="https://boris-ai.pro/api/prospecting/scale/open/'
                + ('z' * 32)
                + '.gif" data-boris-open-tracking="1">'
                '</body></html>'
            )
            healed = email_tracking.ensure_tracker_and_inject(db, 1, stale, "Hi")
            self.assertEqual(healed.count(email_tracking.TRACKING_MARKER), 1)
            self.assertIn(str(db.token), healed)
            self.assertNotIn('open/' + ('z' * 32) + '.gif', healed)
        finally:
            email_tracking.ensure_schema = old


if __name__ == "__main__":
    unittest.main()
