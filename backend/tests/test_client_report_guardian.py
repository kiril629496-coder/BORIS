import inspect
import unittest
from unittest.mock import patch

import boris_guardian as guardian


CRON_LINE = (
    "30 17 * * * cd /root/BORIS/backend && set -a; . ./.env; set +a; "
    "for a in prodazha_bytovok_25677 3411770_94346; do "
    "venv/bin/python3 client_report.py --account $a --send; done"
)


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, routes):
        self.routes = dict(routes)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "SELECT DISTINCT ON (account_id)" in sql:
            rows = []
            for acc in guardian.CLIENT_REPORT_ACCOUNTS:
                if acc in self.routes:
                    chat, thread = self.routes[acc]
                    rows.append((acc, chat, thread))
            return FakeResult(rows)
        if "UPDATE tg_routes SET thread_id=0" in sql:
            acc = params["a"]
            chat = params["c"]
            current = self.routes.get(acc)
            if current and current[0] == chat and int(current[1] or 0) != 0:
                self.routes[acc] = (chat, 0)
                return FakeResult(rowcount=1)
            return FakeResult(rowcount=0)
        if "SELECT thread_id FROM tg_routes" in sql:
            acc = params["a"]
            chat = params["c"]
            current = self.routes.get(acc)
            if current and current[0] == chat:
                return FakeResult([(current[1],)])
            return FakeResult([])
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class ClientReportGuardianTests(unittest.TestCase):
    def healthy_routes(self):
        return {
            "prodazha_bytovok_25677": (guardian.CLIENT_REPORT_CHAT, 0),
            "3411770_94346": (guardian.CLIENT_REPORT_CHAT, 0),
        }

    @staticmethod
    def healthy_sh(cmd, timeout=25):
        if cmd == "crontab -l":
            return 0, CRON_LINE
        return 0, ""

    def test_healthy_scheduler_and_routes_are_green(self):
        db = FakeDB(self.healthy_routes())
        with (
            patch.object(guardian, "active", return_value=True),
            patch.object(guardian, "enabled", return_value=True),
            patch.object(guardian, "sh", side_effect=self.healthy_sh),
            patch.object(guardian, "SessionLocal", return_value=db),
        ):
            h = guardian.client_report_scheduler_health(self_heal=True)
        self.assertEqual(h["state"], "ok", h)
        self.assertEqual(h["critical"], [])
        self.assertTrue(h["cron_line_ok"])
        self.assertEqual(h["healed"], [])

    def test_cron_daemon_is_enabled_and_started_automatically(self):
        state = {"active": False, "enabled": False}
        db = FakeDB(self.healthy_routes())

        def active(_unit):
            return state["active"]

        def enabled(_unit):
            return state["enabled"]

        def sh(cmd, timeout=25):
            if cmd == "systemctl enable --now cron":
                state["active"] = True
                state["enabled"] = True
                return 0, ""
            if cmd == "crontab -l":
                return 0, CRON_LINE
            return 0, ""

        with (
            patch.object(guardian, "active", side_effect=active),
            patch.object(guardian, "enabled", side_effect=enabled),
            patch.object(guardian, "sh", side_effect=sh),
            patch.object(guardian.time, "sleep", return_value=None),
            patch.object(guardian, "SessionLocal", return_value=db),
        ):
            h = guardian.client_report_scheduler_health(self_heal=True)
        self.assertEqual(h["state"], "ok", h)
        self.assertIn("cron daemon", h["healed"])
        self.assertTrue(h["cron_active"])
        self.assertTrue(h["cron_enabled"])

    def test_thread_drift_is_self_healed_before_delivery(self):
        routes = self.healthy_routes()
        routes["prodazha_bytovok_25677"] = (guardian.CLIENT_REPORT_CHAT, 999)
        db = FakeDB(routes)
        with (
            patch.object(guardian, "active", return_value=True),
            patch.object(guardian, "enabled", return_value=True),
            patch.object(guardian, "sh", side_effect=self.healthy_sh),
            patch.object(guardian, "SessionLocal", return_value=db),
        ):
            h = guardian.client_report_scheduler_health(self_heal=True)
        self.assertEqual(h["state"], "ok", h)
        self.assertEqual(db.routes["prodazha_bytovok_25677"][1], 0)
        self.assertTrue(any("thread_id=0" in x for x in h["healed"]))
        self.assertGreaterEqual(db.commits, 1)

    def test_missing_cron_line_fails_closed(self):
        db = FakeDB(self.healthy_routes())
        with (
            patch.object(guardian, "active", return_value=True),
            patch.object(guardian, "enabled", return_value=True),
            patch.object(guardian, "sh", return_value=(0, "0 1 * * * echo noop")),
            patch.object(guardian, "SessionLocal", return_value=db),
        ):
            h = guardian.client_report_scheduler_health(self_heal=True)
        self.assertEqual(h["state"], "critical")
        self.assertFalse(h["cron_line_ok"])
        self.assertTrue(any("cron-команды" in x for x in h["critical"]))

    def test_wrong_client_chat_fails_closed_without_rewrite(self):
        routes = self.healthy_routes()
        routes["3411770_94346"] = ("-1000000000000", 0)
        db = FakeDB(routes)
        with (
            patch.object(guardian, "active", return_value=True),
            patch.object(guardian, "enabled", return_value=True),
            patch.object(guardian, "sh", side_effect=self.healthy_sh),
            patch.object(guardian, "SessionLocal", return_value=db),
        ):
            h = guardian.client_report_scheduler_health(self_heal=True)
        self.assertEqual(h["state"], "critical")
        self.assertEqual(db.routes["3411770_94346"], ("-1000000000000", 0))
        self.assertTrue(any("неверный Telegram chat" in x for x in h["critical"]))

    def test_quick_wires_client_report_scheduler_health(self):
        src = inspect.getsource(guardian.quick)
        self.assertIn("client_report_scheduler_health(self_heal=True)", src)
        self.assertIn("Клиентские отчёты:", src)


    def test_planeta_apply_in_progress_with_matching_published_observation_is_working(self):
        now = guardian.dt.datetime(2026, 9, 12, 9, 30, tzinfo=guardian.dt.timezone.utc)
        ledger = {
            "updated_at": "2026-09-12T09:29:00+00:00",
            "active_count": 1,
            "obligations": [{
                "operation_id": "kpi-ai-prepare_weak_titles-fe7abbb9dc88:apply:8417195872",
                "status": "published",
                "effect_status": "waiting_observation_window",
            }],
        }
        self.assertTrue(guardian._marketer_prepare_waits_on_fresh_prerequisite(
            {}, ledger=ledger,
            operation_id="kpi-ai-prepare_weak_titles-fe7abbb9dc88", now=now
        ))

    def test_apply_in_progress_without_downstream_lifecycle_does_not_hide_stall(self):
        now = guardian.dt.datetime(2026, 9, 12, 9, 30, tzinfo=guardian.dt.timezone.utc)
        ledger = {"updated_at": "2026-09-12T09:29:00+00:00", "active_count": 0, "obligations": []}
        self.assertFalse(guardian._marketer_prepare_waits_on_fresh_prerequisite(
            {}, ledger=ledger, operation_id="op1", now=now
        ))

    def test_stale_observation_does_not_hide_stall(self):
        now = guardian.dt.datetime(2026, 9, 12, 9, 30, tzinfo=guardian.dt.timezone.utc)
        ledger = {
            "updated_at": "2026-09-12T07:00:00+00:00",
            "active_count": 1,
            "obligations": [{
                "operation_id": "op1:apply:123",
                "status": "published",
                "effect_status": "waiting_observation_window",
            }],
        }
        self.assertFalse(guardian._marketer_prepare_waits_on_fresh_prerequisite(
            {}, ledger=ledger, operation_id="op1", now=now
        ))

    def test_other_operation_observation_cannot_make_current_queue_green(self):
        now = guardian.dt.datetime(2026, 9, 12, 9, 30, tzinfo=guardian.dt.timezone.utc)
        ledger = {
            "updated_at": "2026-09-12T09:29:00+00:00",
            "active_count": 1,
            "obligations": [{
                "operation_id": "other-op:apply:123",
                "status": "published",
                "effect_status": "waiting_observation_window",
            }],
        }
        self.assertFalse(guardian._marketer_prepare_waits_on_fresh_prerequisite(
            {}, ledger=ledger, operation_id="op1", now=now
        ))

    def test_finished_observation_returns_to_normal_stall_accounting(self):
        now = guardian.dt.datetime(2026, 9, 12, 9, 30, tzinfo=guardian.dt.timezone.utc)
        ledger = {
            "updated_at": "2026-09-12T09:29:00+00:00",
            "active_count": 1,
            "obligations": [{
                "operation_id": "op1:apply:123",
                "status": "published",
                "effect_status": "measured",
            }],
        }
        self.assertFalse(guardian._marketer_prepare_waits_on_fresh_prerequisite(
            {}, ledger=ledger, operation_id="op1", now=now
        ))

    def test_mop_pressure_counts_distinct_generation_not_quick_ticks(self):
        state = {}
        health = {"bad": [
            "main тренировка МОП без доступного AI: local_fenced_by_production_pressure",
            "replica тренировка МОП без доступного AI: local_fenced_by_production_pressure",
        ]}
        bad1, warn1 = guardian.classify_mop_training_health(
            health, False, state, hard_after=3, generation="g1"
        )
        bad2, warn2 = guardian.classify_mop_training_health(
            health, False, state, hard_after=3, generation="g1"
        )
        bad3, warn3 = guardian.classify_mop_training_health(
            health, False, state, hard_after=3, generation="g2"
        )
        self.assertEqual(state["mop_ai_pressure_only_count"], 2)
        self.assertFalse(bad1)
        self.assertFalse(bad2)
        self.assertFalse(bad3)
        self.assertTrue(warn1 and warn2 and warn3)


if __name__ == "__main__":
    unittest.main()
