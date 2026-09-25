from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tools import platform_onboarding_worker as worker


class PlatformOnboardingWorkerTests(unittest.TestCase):
    def test_run_budget_reserves_time_for_one_more_operation(self):
        with patch.object(worker, "monotonic", return_value=100.0):
            self.assertTrue(worker._budget_allows_start(200.0, 75))
            self.assertFalse(worker._budget_allows_start(170.0, 75))
            self.assertTrue(worker._budget_allows_start(None, 999))

    def test_static_human_checkpoint_backs_off_instead_of_burning_hourly_budget(self):
        now = datetime.now(timezone.utc)
        row = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(hours=1, minutes=5)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertFalse(worker._verification_due(row, now))
            row["updated_at"] = (now - timedelta(hours=12, minutes=5)).isoformat()
            self.assertTrue(worker._verification_due(row, now))

    def test_async_verification_checkpoint_remains_hourly(self):
        now = datetime.now(timezone.utc)
        row = {
            "status": "verification_required",
            "checkpoint": "email_verification_required",
            "updated_at": (now - timedelta(hours=1, minutes=5)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertTrue(worker._verification_due(row, now))

    def test_manual_browser_checkpoint_uses_middle_backoff(self):
        now = datetime.now(timezone.utc)
        row = {
            "status": "verification_required",
            "checkpoint": "manual_verification",
            "updated_at": (now - timedelta(hours=5)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertFalse(worker._verification_due(row, now))
            row["updated_at"] = (now - timedelta(hours=6, minutes=5)).isoformat()
            self.assertTrue(worker._verification_due(row, now))

    def test_fresh_human_checkpoint_is_not_reprobed(self):
        now = datetime.now(timezone.utc)
        row = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(minutes=30)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertFalse(worker._verification_due(row, now))

    def test_terminal_or_nonhuman_checkpoint_is_not_probed(self):
        now = datetime.now(timezone.utc)
        terminal = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(hours=3)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=True):
            self.assertFalse(worker._verification_due(terminal, now))

        nonhuman = {
            "status": "verification_required",
            "checkpoint": "account_creation_unverified",
            "updated_at": (now - timedelta(hours=3)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertFalse(worker._verification_due(nonhuman, now))

    def test_active_client_demand_sorts_first(self):
        reg = {
            "urgent": {"updated_at": "2026-09-01T00:00:00+00:00"},
            "later": {"updated_at": "2026-08-01T00:00:00+00:00"},
        }
        priority = {
            "urgent": {"urgent_for_active_projects": True, "relevant_projects": 1, "potential_slots": 1},
            "later": {"urgent_for_active_projects": False, "relevant_projects": 0, "potential_slots": 0},
        }
        items = [{"platform": "later", "priority": 1}, {"platform": "urgent", "priority": 99}]
        items.sort(key=lambda x: worker._priority_key(x, priority, reg))
        self.assertEqual([x["platform"] for x in items], ["urgent", "later"])

    def test_inventory_soft_priority_sorts_relevant_before_unrelated_without_scoping(self):
        reg = {
            "inventory_relevant": {"updated_at": "2026-09-01T00:00:00+00:00"},
            "unrelated": {"updated_at": "2026-08-01T00:00:00+00:00"},
        }
        priority = {
            "inventory_relevant": {
                "urgent_for_active_projects": False,
                "relevant_projects": 1,
                "potential_slots": 1,
            }
        }
        items = [
            {"platform": "unrelated", "priority": 1},
            {"platform": "inventory_relevant", "priority": 99},
        ]
        items.sort(key=lambda x: worker._priority_key(x, priority, reg))
        self.assertEqual([x["platform"] for x in items], ["inventory_relevant", "unrelated"])
        self.assertEqual(worker._scope_candidates_to_active_projects(items, {}), items)

    def test_effective_priority_hard_scopes_only_contract_urgent_rows(self):
        priority = {
            "client_forum": {"urgent_for_active_projects": True},
            "inventory_forum": {"urgent_for_active_projects": False},
        }
        contract = [{
            "id": "contract", "plan_mode": "contract", "status": "approved_for_placement",
            "content_status": "approved", "target_count": 1, "bonus_count": 0,
        }]
        with (
            patch.object(worker.crowd_seo, "project_is_active", return_value=True),
            patch.object(worker.crowd_seo, "_project_required_count", return_value=1),
        ):
            effective = worker._effective_bootstrap_priority(priority, contract)
        self.assertEqual(set(effective), {"client_forum"})

    def test_active_project_scope_excludes_reserve_until_client_onboarding_is_clear(self):
        candidates = [
            {"platform": "client_forum"},
            {"platform": "reserve_forum"},
        ]
        priority = {
            "client_forum": {
                "urgent_for_active_projects": True,
                "relevant_projects": 1,
                "potential_slots": 1,
            }
        }
        scoped = worker._scope_candidates_to_active_projects(candidates, priority)
        self.assertEqual([x["platform"] for x in scoped], ["client_forum"])
        self.assertEqual(worker._scope_candidates_to_active_projects(candidates, {}), candidates)

    def test_worker_verify_command_never_submits(self):
        class Proc:
            returncode = 0
            stdout = '{"ok": true, "status": "verification_required", "checkpoint": "captcha_required"}'
            stderr = ""

        with patch.object(worker.subprocess, "run", return_value=Proc()) as run:
            result = worker._verify("example")

        argv = run.call_args.args[0]
        self.assertIn("verify-registration", argv)
        self.assertNotIn("--submit", argv)
        self.assertFalse(result["submitted"])


    def test_playwright_fill_values_are_redacted_from_diagnostics(self):
        raw = 'Locator.fill timeout\n - fill("ExampleSecret123!")\n password=ExampleSecret123!'
        cleaned = worker.marketplace.redact_sensitive_text(raw)
        self.assertNotIn("ExampleSecret123!", cleaned)
        self.assertIn("[REDACTED_SECRET]", cleaned)

    def test_worker_transport_error_never_persists_fill_secret(self):
        class Proc:
            returncode = 1
            stdout = ""
            stderr = 'TimeoutError: Locator.fill\n - fill("ExampleSecret123!")'

        current = {
            "platform": "captcha_forum",
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "email": "worker@example.com",
            "account_url": "https://forum.example/register",
        }
        with (
            patch.object(worker.subprocess, "run", return_value=Proc()),
            patch.object(worker.marketplace, "registration_plan", return_value=[current]),
            patch.object(worker.marketplace, "upsert_registration") as upsert,
            patch.object(worker.marketplace, "record_attempt") as record,
        ):
            result = worker._verify("captcha_forum")

        self.assertNotIn("ExampleSecret123!", result["error"])
        self.assertIn("[REDACTED_SECRET]", result["error"])
        self.assertNotIn("ExampleSecret123!", upsert.call_args.kwargs["last_error"])
        self.assertNotIn("ExampleSecret123!", record.call_args.kwargs["error_detail"])

    def test_transport_failure_preserves_human_checkpoint_and_refreshes_timestamp(self):
        class Proc:
            returncode = 1
            stdout = ""
            stderr = "Page.goto: Timeout 45000ms exceeded"

        current = {
            "platform": "captcha_forum",
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "email": "worker@example.com",
            "account_url": "https://forum.example/register",
        }
        with (
            patch.object(worker.subprocess, "run", return_value=Proc()),
            patch.object(worker.marketplace, "registration_plan", return_value=[current]),
            patch.object(worker.marketplace, "upsert_registration") as upsert,
            patch.object(worker.marketplace, "record_attempt") as record,
        ):
            result = worker._verify("captcha_forum")

        self.assertEqual(result["returncode"], 1)
        self.assertIn("Timeout", result["error"])
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[:2], ("captcha_forum", "verification_required"))
        self.assertEqual(upsert.call_args.kwargs["checkpoint"], "captcha_required")
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["error_code"], "verification_transport_error")

    def test_timeout_refreshes_checkpoint_instead_of_monopolising_queue(self):
        current = {
            "platform": "slow_forum",
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "email": "worker@example.com",
            "account_url": "https://forum.example/register",
        }
        with (
            patch.object(worker.subprocess, "run", side_effect=worker.subprocess.TimeoutExpired(cmd=["verify"], timeout=70)),
            patch.object(worker.marketplace, "registration_plan", return_value=[current]),
            patch.object(worker.marketplace, "upsert_registration") as upsert,
            patch.object(worker.marketplace, "record_attempt") as record,
        ):
            result = worker._verify("slow_forum")

        self.assertEqual(result["returncode"], 124)
        self.assertEqual(result["status"], "verification_required")
        self.assertEqual(result["checkpoint"], "captcha_required")
        self.assertEqual(result["error"], "verification_timeout")
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["checkpoint"], "captcha_required")
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["error_code"], "verification_transport_error")

    def test_transient_rule_audit_due_uses_short_guardian_interval(self):
        now = datetime.now(timezone.utc)
        row = {
            "checked_at": (now - worker.guardian.TRANSIENT_AUDIT_RETRY_INTERVAL - timedelta(minutes=1)).isoformat(),
            "decision": "review",
            "errors": [{"error": "Temporary failure in name resolution"}],
        }
        with (
            patch.object(worker.platform_rules, "latest", return_value=row),
            patch.object(worker.guardian, "_audit_has_transient_transport_error", return_value=True),
        ):
            self.assertTrue(worker._transient_rule_audit_due("forum", now))

    def test_worker_retries_transient_rule_audit_and_marks_recovery(self):
        now = datetime.now(timezone.utc)
        previous = {
            "checked_at": (now - timedelta(hours=3)).isoformat(),
            "decision": "review",
            "errors": [{"error": "Page.goto: net::ERR_NAME_NOT_RESOLVED"}],
        }
        recovered = {
            "checked_at": now.isoformat(),
            "decision": "allowed",
            "errors": [],
        }
        with (
            patch.object(worker.marketplace, "list_platforms", return_value=[{"key": "forum"}]),
            patch.object(worker.platform_rules, "latest", return_value=previous),
            patch.object(worker.platform_rules, "inspect_platform", return_value=recovered) as inspect,
            patch.object(worker.guardian, "_audit_has_transient_transport_error", side_effect=lambda row: bool(row.get("errors"))),
        ):
            result = worker._retry_transient_rule_audits(now)

        inspect.assert_called_once_with("forum")
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["progressed"], ["forum"])
        self.assertFalse(result["attempts"][0]["transient_error"])

    def test_guest_publication_verifier_requires_exact_title_and_target(self):
        class Response:
            status_code = 200
            url = "https://board.example/services"
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            text = (
                '<html><body><strong>Разработка веб-приложений</strong>'
                '<a href="https://boris-ai.pro/software-dev/services/web-app-development">Подробнее</a>'
                '</body></html>'
            )

        row = {
            "platform": "guest_test",
            "submitted": True,
            "verified": False,
            "title": "Разработка веб-приложений",
            "target_url": "https://boris-ai.pro/software-dev/services/web-app-development",
            "verification_urls": ["https://board.example/services"],
        }
        with patch.object(worker.requests, "get", return_value=Response()) as get:
            result = worker._verify_guest_publication(row)
        self.assertTrue(result["verified"])
        self.assertEqual(result["public_url"], Response.url)
        get.assert_called_once()

    def test_guest_publication_verifier_rejects_plain_text_target(self):
        class Response:
            status_code = 200
            url = "https://board.example/services"
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            text = (
                '<html><body><strong>Разработка веб-приложений</strong>'
                '<span>https://boris-ai.pro/software-dev/services/web-app-development</span>'
                '</body></html>'
            )

        row = {
            "platform": "guest_test",
            "submitted": True,
            "verified": False,
            "title": "Разработка веб-приложений",
            "target_url": "https://boris-ai.pro/software-dev/services/web-app-development",
            "probes": [{"url": "https://board.example/services"}],
        }
        with patch.object(worker.requests, "get", return_value=Response()):
            result = worker._verify_guest_publication(row)
        self.assertFalse(result["verified"])
        self.assertTrue(result["probes"][0]["target_present"])
        self.assertFalse(result["probes"][0]["exact_href"])

    def test_guest_publication_verifier_rejects_noindex_page(self):
        class Response:
            status_code = 200
            url = "https://board.example/services"
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            text = (
                '<html><head><meta name="robots" content="noindex,follow"></head><body>'
                '<strong>Разработка веб-приложений</strong>'
                '<a href="https://boris-ai.pro/software-dev/services/web-app-development">Подробнее</a>'
                '</body></html>'
            )

        row = {
            "platform": "guest_test",
            "submitted": True,
            "verified": False,
            "title": "Разработка веб-приложений",
            "target_url": "https://boris-ai.pro/software-dev/services/web-app-development",
            "verification_urls": ["https://board.example/services"],
        }
        with patch.object(worker.requests, "get", return_value=Response()):
            result = worker._verify_guest_publication(row)
        self.assertFalse(result["verified"])
        self.assertTrue(result["probes"][0]["exact_href"])
        self.assertFalse(result["probes"][0]["indexable"])

    def test_same_form_guest_submit_without_confirmation_is_not_evidence(self):
        false_positive = {
            "submitted": True,
            "verified": False,
            "source_form": "https://catalog.example/add/",
            "final_url": "https://catalog.example/add/",
            "response_excerpt": "Опубликованное ранее содержание не будет принято.",
        }
        moderation = {
            "submitted": True,
            "verified": False,
            "source_form": "https://board.example/add",
            "final_url": "https://board.example/thanks?id=1",
            "response_excerpt": "Объявление поступило на модерацию.",
        }
        same_form_confirmed = {
            "submitted": True,
            "verified": False,
            "source_form": "https://board.example/add",
            "final_url": "https://board.example/add/",
            "response_excerpt": "Спасибо, ваше объявление отправлено на модерацию.",
        }
        self.assertFalse(worker._guest_submission_evidence_valid(false_positive))
        self.assertTrue(worker._guest_submission_evidence_valid(moderation))
        self.assertTrue(worker._guest_submission_evidence_valid(same_form_confirmed))

    def test_pending_guest_verifier_demotes_unconfirmed_same_form_submit(self):
        now = datetime(2026, 9, 11, 23, 45, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            path = directory / "guest_publications_20260911.json"
            path.write_text(json.dumps([{
                "platform": "guest_false_positive",
                "submitted": True,
                "verified": False,
                "source_form": "https://catalog.example/add/",
                "final_url": "https://catalog.example/add/",
                "response_excerpt": "Опубликованное ранее содержание не будет принято.",
            }], ensure_ascii=False), encoding="utf-8")
            with (
                patch.object(worker, "GUEST_PUBLICATIONS_DIR", directory),
                patch.object(worker, "_verify_guest_publication") as verify,
                patch.object(worker.marketplace, "record_attempt") as record,
            ):
                result = worker._verify_pending_guest_publications(now)
            saved = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(result["demoted_unconfirmed"], ["guest_false_positive"])
            self.assertFalse(saved["submitted"])
            self.assertEqual(saved["checkpoint"], "unconfirmed_submit_response")
            self.assertTrue(saved["submission_evidence_invalid"])
            verify.assert_not_called()
            record.assert_called_once()
            self.assertEqual(record.call_args.kwargs["error_code"], "unconfirmed_submit_response")

    def test_pending_guest_verification_updates_evidence_without_resubmit(self):
        now = datetime(2026, 9, 10, 22, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            path = directory / "guest_publications_20260910.json"
            path.write_text(json.dumps([{
                "platform": "guest_forma_spb",
                "submitted": True,
                "verified": False,
                "title": "Разработка веб-приложений",
                "target_url": "https://boris-ai.pro/software-dev/services/web-app-development",
            }], ensure_ascii=False), encoding="utf-8")
            proof = {
                "ok": True,
                "verified": True,
                "platform": "guest_forma_spb",
                "public_url": "https://www.forma.spb.ru/active/adv/main.shtml",
                "error": None,
            }
            with (
                patch.object(worker, "GUEST_PUBLICATIONS_DIR", directory),
                patch.object(worker, "_verify_guest_publication", return_value=proof) as verify,
                patch.object(worker.marketplace, "upsert_verified_external_publication") as upsert,
                patch.object(worker.marketplace, "record_attempt") as record,
            ):
                result = worker._verify_pending_guest_publications(now)

            saved = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(result["resubmissions"], 0)
            self.assertEqual(result["verified"], ["guest_forma_spb"])
            self.assertTrue(saved["verified"])
            self.assertEqual(saved["public_url"], proof["public_url"])
            self.assertEqual(saved["verification_checks"], 1)
            verify.assert_called_once()
            upsert.assert_called_once()
            self.assertEqual(upsert.call_args.kwargs["url"], proof["public_url"])
            self.assertEqual(upsert.call_args.kwargs["target_url"], "https://boris-ai.pro/software-dev/services/web-app-development")
            record.assert_called_once()
            self.assertEqual(record.call_args.kwargs["action"], "verify_publication")
            self.assertEqual(record.call_args.kwargs["status_value"], "verified")

    def test_confirmed_guest_posts_sync_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            confirmed = directory / "confirmed_posts_20260911.json"
            confirmed.write_text(json.dumps([{
                "platform": "sdelkino.com",
                "status": "posted_verified",
                "title": "BORIS Development",
                "target_url": "https://boris-ai.pro/software-dev/",
                "listing_url": "https://example.test/list",
                "publication_url": "https://example.test/post/1",
                "verified_at": "2026-09-11T20:44:57+00:00",
                "verification": {"http_status": 200},
            }], ensure_ascii=False), encoding="utf-8")
            saved = {"created": True}
            with (
                patch.object(worker, "GUEST_CONFIRMED_POSTS_DIR", directory),
                patch.object(worker.marketplace, "upsert_verified_external_publication", return_value=saved) as upsert,
            ):
                result = worker._sync_confirmed_guest_publications()

            self.assertEqual(result["scanned"], 1)
            self.assertEqual(result["created"], ["sdelkino.com"])
            self.assertEqual(result["errors"], [])
            upsert.assert_called_once()
            self.assertEqual(upsert.call_args.kwargs["url"], "https://example.test/post/1")
            self.assertEqual(upsert.call_args.kwargs["source"], "confirmed_guest_post")

    def test_confirmed_guest_sync_does_not_resurrect_canonical_stripped_link(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            confirmed = directory / "confirmed_posts_20260911.json"
            confirmed.write_text(json.dumps([{
                "platform": "sdelkino.com",
                "status": "posted_verified",
                "title": "BORIS Development",
                "target_url": "https://boris-ai.pro/software-dev/",
                "publication_url": "https://example.test/post/1",
                "verified_at": "2026-09-11T20:44:57+00:00",
            }], ensure_ascii=False), encoding="utf-8")
            canonical = [{
                "platform": "sdelkino.com",
                "url": "https://example.test/post/1",
                "verified": False,
                "link_present": False,
                "verification_error": "target_link_missing",
            }]
            with (
                patch.object(worker, "GUEST_CONFIRMED_POSTS_DIR", directory),
                patch.object(worker.marketplace, "list_publications", return_value=canonical),
                patch.object(worker.marketplace, "upsert_verified_external_publication") as upsert,
            ):
                result = worker._sync_confirmed_guest_publications()

            self.assertEqual(result["existing"], ["sdelkino.com"])
            self.assertEqual(result["created"], [])
            upsert.assert_not_called()

    def test_external_publication_recheck_removes_stripped_link(self):
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        row = {
            "platform": "sdelkino.com",
            "source": "confirmed_guest_post",
            "url": "https://example.test/post/1",
            "site_url": "https://boris-ai.pro/software-dev/",
            "title": "BORIS Development",
        }
        proof = {
            "ok": True,
            "verified": False,
            "link_present": False,
            "error": "target_link_missing",
        }
        with (
            patch.object(worker.marketplace, "list_publications", return_value=[row]),
            patch.object(worker, "_verify_external_publication", return_value=proof),
            patch.object(worker.marketplace, "update_external_publication_verification") as update,
        ):
            result = worker._recheck_external_publications(now)
        self.assertEqual(result["removed_or_stripped"], ["sdelkino.com"])
        update.assert_called_once()
        self.assertFalse(update.call_args.kwargs["verified"])
        self.assertFalse(update.call_args.kwargs["link_present"])

    def test_external_publication_plain_text_url_is_not_verified(self):
        row = {
            "platform": "guest",
            "url": "https://example.test/post/1",
            "site_url": "https://boris-ai.pro/software-dev",
            "title": "Разработка ПО",
        }
        class Response:
            status_code = 200
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            url = "https://example.test/post/1"
            text = "<html><body><h1>Разработка ПО</h1><p>https://boris-ai.pro/software-dev</p></body></html>"
        with patch.object(worker.requests, "get", return_value=Response()):
            proof = worker._verify_external_publication(row)
        self.assertFalse(proof["verified"])
        self.assertFalse(proof["link_present"])
        self.assertTrue(proof["target_text_present"])

    def test_external_publication_exact_clickable_href_is_verified(self):
        row = {
            "platform": "guest",
            "url": "https://example.test/post/1",
            "site_url": "https://boris-ai.pro/software-dev",
            "title": "Разработка ПО",
        }
        class Response:
            status_code = 200
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            url = "https://example.test/post/1"
            text = '<html><body><h1>Разработка ПО</h1><a href="https://boris-ai.pro/software-dev/">Подробнее</a></body></html>'
        with patch.object(worker.requests, "get", return_value=Response()):
            proof = worker._verify_external_publication(row)
        self.assertTrue(proof["verified"])
        self.assertTrue(proof["link_present"])

    def test_legacy_external_publication_without_source_is_rechecked(self):
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        row = {
            "draft_id": "external_otsiv_boris_ai_20260910",
            "platform": "otsiv_ru",
            "url": "https://example.test/post/1",
            "site_url": "https://boris-ai.pro/software-dev/",
            "verified": True,
        }
        self.assertTrue(worker._external_publication_due(row, now))

    def test_external_publication_transport_error_preserves_previous_credit(self):
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        row = {
            "platform": "guest",
            "source": "confirmed_guest_post",
            "url": "https://example.test/post/1",
            "site_url": "https://boris-ai.pro/software-dev/",
        }
        proof = {"ok": False, "verified": None, "link_present": None, "error": "TimeoutError"}
        with (
            patch.object(worker.marketplace, "list_publications", return_value=[row]),
            patch.object(worker, "_verify_external_publication", return_value=proof),
            patch.object(worker.marketplace, "update_external_publication_verification") as update,
        ):
            result = worker._recheck_external_publications(now)
        self.assertEqual(result["transient"], ["guest"])
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
