import unittest
from unittest.mock import patch

from app.api import browser_gateway as bg


class BrowserGatewayIsolationTests(unittest.TestCase):
    def test_first_pair_dispatches_only_identity_probe(self):
        rec = {
            "gateway_id": "gw_test",
            "account_id": "acc_test",
            "identity_verified": None,
            "identity_mismatch": False,
            "token": "tok",
        }
        calls = []
        with patch.object(bg, "_auth", return_value=rec), \
             patch.object(bg, "_recover_browser_jobs", return_value={}), \
             patch.object(bg, "_maybe_queue_identity_watchdog", side_effect=lambda *a, **k: calls.append("watchdog_queue") or False), \
             patch.object(bg, "_claim_identity_watchdog_job", side_effect=lambda *a, **k: calls.append("watchdog_claim") or None), \
             patch.object(bg, "_next_waiting_job", side_effect=lambda *a, **k: calls.append("mass_job") or None), \
             patch.object(bg, "_claim_oldest_queued_job", side_effect=lambda *a, **k: calls.append("mass_claim") or None), \
             patch.object(bg, "_next_browser_job", side_effect=lambda *a, **k: calls.append("browser_job") or None), \
             patch.object(bg, "_claim_oldest_browser_job", side_effect=lambda *a, **k: calls.append("browser_claim") or None):
            out = bg.next_job("Bearer x")
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "identity_verification_pending")
        self.assertEqual(calls, ["watchdog_queue", "watchdog_claim"])

    def test_unknown_browser_identity_cannot_be_used_as_fallback(self):
        with patch.object(bg, "_account_gateway_state", return_value={
            "online": True,
            "identity_verified": None,
            "identity_mismatch": False,
            "requires_login": False,
        }), patch.object(bg, "_avito_api_state", return_value={"connected": False, "avito_user_id": ""}):
            out = bg._execution_route("acc", "mass_edit_characteristics", "WRITE")
        self.assertEqual(out["executor"], "blocked")
        self.assertEqual(out["reason"], "browser_identity_verification_pending")

    def test_mismatched_browser_identity_is_blocked(self):
        with patch.object(bg, "_account_gateway_state", return_value={
            "online": True,
            "identity_verified": False,
            "identity_mismatch": True,
            "requires_login": False,
        }), patch.object(bg, "_avito_api_state", return_value={"connected": False, "avito_user_id": ""}):
            out = bg._execution_route("acc", "mass_edit_characteristics", "WRITE")
        self.assertEqual(out["executor"], "blocked")
        self.assertEqual(out["reason"], "browser_identity_mismatch")

    def test_profile_name_parser_removes_glued_rating(self):
        cases = {
            "Сообщения Антон4.537 отзывов Реквизиты проверены Кошелёк": "Антон",
            "Сообщения МАРИЯ КАРГАЛЬЦЕВА - ИПОТЕЧНЫЙ БРОКЕР 4,9 · 120 отзывов Реквизиты проверены": "МАРИЯ КАРГАЛЬЦЕВА - ИПОТЕЧНЫЙ БРОКЕР",
            "Сообщения Стекло24 5,0 · 18 отзывов Кошелёк": "Стекло24",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(bg._extract_avito_profile_name(raw), expected)

    def test_mass_write_gateway_version_floor(self):
        required = bg.MIN_AVITO_MASS_WRITE_GATEWAY_VERSION
        parts = [int(x) for x in required.split(".")]
        older = parts[:]
        older[-1] = max(0, older[-1] - 1)
        older = ".".join(str(x) for x in older)
        self.assertFalse(bg._gateway_version_at_least(older))
        self.assertTrue(bg._gateway_version_at_least(required))
        self.assertTrue(bg._gateway_version_at_least("99.0.0"))
        self.assertFalse(bg._gateway_version_at_least(None))

    def test_old_gateway_reports_upgrade_instead_of_dispatching_mass_write(self):
        rec = {
            "gateway_id": "gw_test",
            "account_id": "acc_test",
            "extension_version": "2.1.4",
            "identity_verified": True,
            "identity_mismatch": False,
            "token": "tok",
        }
        with patch.object(bg, "_auth", return_value=rec), \
             patch.object(bg, "_recover_browser_jobs", return_value={}), \
             patch.object(bg, "_next_waiting_job", side_effect=AssertionError("old gateway must not receive mass write")), \
             patch.object(bg, "_claim_oldest_queued_job", side_effect=AssertionError("old gateway must not claim mass write")), \
             patch.object(bg, "_next_browser_job", return_value=None), \
             patch.object(bg, "_claim_oldest_browser_job", return_value=None), \
             patch.object(bg, "_maybe_queue_identity_watchdog", return_value=False), \
             patch.object(bg, "_pending_mass_write_exists", return_value=True):
            out = bg.next_job("Bearer x")
        self.assertEqual(out["status"], "idle")
        self.assertEqual(out["mass_write"]["reason"], "gateway_upgrade_required")
        self.assertEqual(out["mass_write"]["current_version"], "2.1.4")
        self.assertEqual(out["mass_write"]["required_version"], bg.MIN_AVITO_MASS_WRITE_GATEWAY_VERSION)

    def test_crowd_gateway_can_claim_browser_job_without_avito_identity_probe(self):
        rec = {
            "gateway_id": "gw_crowd",
            "account_id": bg.CROWD_GATEWAY_ACCOUNT,
            "extension_version": "2.2.0",
            "identity_verified": None,
            "identity_mismatch": False,
            "token": "tok",
        }
        crowd_job = (bg.CROWD_GATEWAY_ACCOUNT, "browser_job:crowd_1", {
            "job_id": "crowd_1", "status": "waiting_gateway", "mode": "register",
            "domain": "forum-seo.net", "capability": "crowd_registration",
            "risk_level": "WRITE", "confirmed": True, "steps": [],
            "gateway_id": "gw_crowd", "metadata": {"platform": "forum_seo_net"},
        })
        with patch.object(bg, "_auth", return_value=rec), \
             patch.object(bg, "_recover_browser_jobs", return_value={}), \
             patch.object(bg, "_next_waiting_job", return_value=None), \
             patch.object(bg, "_claim_oldest_queued_job", return_value=None), \
             patch.object(bg, "_next_browser_job", return_value=None), \
             patch.object(bg, "_claim_oldest_browser_job", return_value=crowd_job), \
             patch.object(bg, "_maybe_queue_identity_watchdog", side_effect=AssertionError("crowd session must not run Avito identity watchdog")), \
             patch.object(bg, "_db_put"):
            out = bg.next_job("Bearer x")
        self.assertEqual(out["status"], "job")
        self.assertEqual(out["job"]["kind"], "browser")
        self.assertEqual(out["job"]["job_id"], "crowd_1")
        self.assertEqual(out["job"]["metadata"]["platform"], "forum_seo_net")

    def test_human_checkpoint_pauses_crowd_job_without_completing_it(self):
        rec = {"gateway_id": "gw_crowd", "account_id": bg.CROWD_GATEWAY_ACCOUNT, "token": "tok"}
        job = {
            "job_id": "crowd_2", "status": "running_gateway", "gateway_id": "gw_crowd",
            "capability": "crowd_registration", "metadata": {"platform": "forum_seo_net"},
        }
        saved = []
        req = bg.BrowserProgressRequest(
            job_id="crowd_2", account_id=bg.CROWD_GATEWAY_ACCOUNT,
            event="human_checkpoint_ready", step_index=2,
            payload={"platform": "forum_seo_net", "url": "https://forum-seo.net/register/"},
        )
        with patch.object(bg, "_auth", return_value=rec), \
             patch.object(bg, "_db_get", return_value=job), \
             patch.object(bg, "_db_put", side_effect=lambda a, k, v: saved.append((a, k, dict(v)))):
            out = bg.browser_progress(req, "Bearer x")
        self.assertEqual(out["job_status"], "waiting_human")
        self.assertEqual(job["status"], "waiting_human")
        self.assertEqual(job["current_step"], 2)
        self.assertEqual(job["human_checkpoint"]["url"], "https://forum-seo.net/register/")
        self.assertTrue(saved)

    def test_new_gateway_can_claim_mass_write(self):
        rec = {
            "gateway_id": "gw_test",
            "account_id": "acc_test",
            "extension_version": bg.MIN_AVITO_MASS_WRITE_GATEWAY_VERSION,
            "identity_verified": True,
            "identity_mismatch": False,
            "token": "tok",
        }
        mass_job = ("acc_test", "mass_editor_job:j1", {
            "job_id": "j1", "status": "waiting_gateway", "mode": "edit",
            "items": [], "rules": {}, "gateway_id": "gw_test",
        })
        with patch.object(bg, "_auth", return_value=rec), \
             patch.object(bg, "_recover_browser_jobs", return_value={}), \
             patch.object(bg, "_next_waiting_job", return_value=mass_job), \
             patch.object(bg, "_db_put"), \
             patch.object(bg, "_next_browser_job", return_value=None), \
             patch.object(bg, "_claim_oldest_browser_job", return_value=None):
            out = bg.next_job("Bearer x")
        self.assertEqual(out["status"], "job")
        self.assertEqual(out["job"]["kind"], "avito")
        self.assertEqual(out["job"]["job_id"], "j1")


if __name__ == "__main__":
    unittest.main()
