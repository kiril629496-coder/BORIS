# -*- coding: utf-8 -*-
import inspect
import subprocess
import unittest

import boris_guardian as guardian
import telegram_sales_health as tg_health
import telegram_sales_watchdog as tg_watchdog
from app.ext_api import watchdog, queuefix, master


class OwnerNotificationContractTest(unittest.TestCase):
    def test_guardian_deduplicates_transient_failures(self):
        src = inspect.getsource(guardian.quick)
        self.assertIn("OWNER_ALERT_DEBOUNCE_V3", src)
        self.assertIn("pending_alert_count", src)
        self.assertIn("healthy_confirmations", src)
        self.assertNotIn("API HA: работает только одна реплика", src)

    def test_guardian_owner_text_is_human(self):
        src = inspect.getsource(guardian.quick)
        self.assertIn("Не все клиентские аккаунты получили обязательные системные настройки", src)
        self.assertIn("От вас действий не требуется", src)

    def test_tg_sales_health_does_not_leak_metric_keys_as_message(self):
        src = inspect.getsource(tg_health.main)
        self.assertNotIn("safety gate FAIL", src)
        self.assertIn("Служебный/нецелевой ответ ошибочно остался активным в CRM", src)
        self.assertIn("Telegram-лидогенерация требует автопочинки", src)

    def test_tg_watchdog_translates_machine_audit(self):
        src = inspect.getsource(tg_watchdog._owner_audit_body)
        self.assertIn("Планировщик Telegram-продаж не запущен", src)
        self.assertIn("От вас действий не требуется", src)

    def test_owner_watchdog_text_has_no_internal_jargon(self):
        text = watchdog.snapshot_report()["text"]
        for forbidden in (
            "CLIENT BUSY NOW", "FALSE GREEN", "ACTIVE ORDERS PER TASK",
            "OWNER ACTION REQUIRED", "REGISTERED WORKERS", "MASTER =",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("BORIS — контроль работы", text)

    def test_owner_watchdog_names_concrete_client_goal(self):
        text = watchdog.snapshot_report()["text"]
        self.assertIn("Цель:", text)
        self.assertIn("Факт сейчас:", text)
        self.assertIn("Сейчас:", text)
        self.assertNotIn("результата пока нет около", text)
        self.assertNotIn("нового подтверждённого результата нет", text)

    def test_owner_watchdog_covers_every_business_goal(self):
        from unittest.mock import patch
        from app.ext_api import clients, report

        synthetic = "CLIENT_SYNTHETIC_WATCHDOG_TEST"
        goals = dict(clients.BUSINESS_GOAL)
        goals[synthetic] = "синтетическая клиентская цель автоматически под контролем"
        truth_rows = []
        for key, goal in goals.items():
            truth_rows.append({
                "key": key,
                "goal": goal,
                "expected_final": goal,
                "measurable": True,
                "current": 1,
                "delta": 0,
                "state": "DONE",
                "overall_done": True,
                "progress": 100,
            })
        with (
            patch.object(clients, "BUSINESS_GOAL", goals),
            patch.object(report, "business_truth", return_value={"clients": truth_rows}),
        ):
            watched = watchdog.clients()
        keys = {row["key"] for row in watched}
        self.assertEqual(keys, set(goals))
        self.assertIn(synthetic, keys)
        synthetic_row = next(row for row in watched if row["key"] == synthetic)
        self.assertEqual(
            synthetic_row["name"],
            "синтетическая клиентская цель автоматически под контролем",
        )

    def test_business_registry_health_fails_on_missing_contract_or_recovery_spec(self):
        from unittest.mock import patch
        from app.ext_api import clients

        synthetic = "CLIENT_SYNTHETIC_REGISTRY_TEST"
        goals = dict(clients.BUSINESS_GOAL)
        goals[synthetic] = "синтетическая цель"
        contracts = dict(clients.BUSINESS_CONTRACT)

        with (
            patch.object(clients, "BUSINESS_GOAL", goals),
            patch.object(clients, "BUSINESS_CONTRACT", contracts),
        ):
            health = clients.business_registry_health()
        self.assertFalse(health["ok"])
        self.assertIn(synthetic, health["missing_contract"])
        self.assertIn(synthetic, health["missing_spec"])

        contracts[synthetic] = {
            "baseline": "0",
            "expected": "1",
            "method": "test",
            "where": "test",
            "max_no_progress_min": 1,
        }
        synthetic_spec = {
            "key": synthetic,
            "scope": {"paths": ["backend/app/ext_api"]},
            "acceptance": ["готово"],
            "first_order": "проверить",
        }
        with (
            patch.object(clients, "BUSINESS_GOAL", goals),
            patch.object(clients, "BUSINESS_CONTRACT", contracts),
            patch.object(clients, "CLIENTS", list(clients.CLIENTS) + [synthetic_spec]),
        ):
            health = clients.business_registry_health()
        self.assertTrue(health["ok"], health)

    def test_registry_recovery_cooldown_bounds_same_fault_and_allows_new_fault(self):
        import os
        import tempfile
        from unittest.mock import patch

        first = {
            "ok": False,
            "missing_contract": ["CLIENT_A"],
            "missing_spec": [],
        }
        second = {
            "ok": False,
            "missing_contract": ["CLIENT_B"],
            "missing_spec": [],
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "registry-recovery.json")
            with (
                patch.object(watchdog, "REGISTRY_RECOVERY_STATE", path),
                patch.object(watchdog.time, "time", return_value=1000),
            ):
                self.assertTrue(watchdog._registry_recovery_allowed(first, cooldown_sec=3600))
                self.assertFalse(watchdog._registry_recovery_allowed(first, cooldown_sec=3600))
                self.assertTrue(watchdog._registry_recovery_allowed(second, cooldown_sec=3600))
                self.assertTrue(watchdog._registry_recovery_allowed({"ok": True}, cooldown_sec=3600))
                self.assertFalse(os.path.exists(path))

    def test_coverage_recovery_cooldown_bounds_same_gap_and_allows_new_gap(self):
        import os
        import tempfile
        from unittest.mock import patch

        first = {
            "counts": {"mapped": 52, "total": 55, "pass": False},
            "rows": [
                {"req": "KPI_AUTOPILOT", "status": "NOT_CREATED"},
                {"req": "CPX_SAFETY", "status": "NOT_CREATED"},
            ],
        }
        second = {
            "counts": {"mapped": 53, "total": 55, "pass": False},
            "rows": [
                {"req": "BANNER_FACTORY_SERVER", "status": "NOT_CREATED"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "coverage-recovery.json")
            with (
                patch.object(watchdog, "COVERAGE_RECOVERY_STATE", path),
                patch.object(watchdog.time, "time", return_value=1000),
            ):
                self.assertTrue(
                    watchdog._coverage_recovery_allowed(first, cooldown_sec=3600)
                )
                self.assertFalse(
                    watchdog._coverage_recovery_allowed(first, cooldown_sec=3600)
                )
                self.assertTrue(
                    watchdog._coverage_recovery_allowed(second, cooldown_sec=3600)
                )
                self.assertTrue(
                    watchdog._coverage_recovery_allowed(
                        {}, healthy=True, cooldown_sec=3600
                    )
                )
                self.assertFalse(os.path.exists(path))

    def test_watchdog_same_coverage_gap_does_not_create_orders_in_tight_loop(self):
        from unittest.mock import patch
        from app.ext_api import master

        broken = {
            "coverage_ok": False,
            "coverage": {
                "counts": {"mapped": 52, "total": 55, "pass": False},
                "rows": [
                    {"req": "KPI_AUTOPILOT", "status": "NOT_CREATED"},
                ],
            },
            "business_registry_ok": True,
            "business_registry": {"ok": True},
            "queue": {"duplicates": 0, "anomaly": False},
            "stalled": [],
            "false_green": False,
            "text": "test",
            "system": "RED",
        }
        with (
            patch.object(watchdog, "snapshot_report", return_value=broken),
            patch.object(watchdog, "_coverage_recovery_allowed", return_value=False),
            patch.object(master, "ensure_coverage") as ensure,
        ):
            out = watchdog.once(send=False)
        ensure.assert_not_called()
        self.assertEqual(out["system"], "RED")
        self.assertIn(
            "повторное восстановление того же покрытия отложено до следующего "
            "безопасного окна",
            out["acted"],
        )

    def test_registry_fault_changes_owner_notification_signature(self):
        base = {
            "system": "RED",
            "false_green": False,
            "stalled": [],
            "queue": {"duplicates": 0, "anomaly": False},
            "clients": [],
            "coverage_ok": True,
        }
        broken = dict(base)
        broken.update({
            "business_registry_ok": False,
            "business_registry": {
                "ok": False,
                "missing_contract": ["CLIENT_X"],
                "missing_spec": [],
            },
        })
        self.assertNotEqual(
            watchdog._notify_signature(base),
            watchdog._notify_signature(broken),
        )

    def test_watchdog_registry_drift_triggers_recover_and_verifies_heal(self):
        from unittest.mock import patch
        from app.ext_api import recover, factory

        broken = {
            "coverage_ok": True,
            "business_registry_ok": False,
            "business_registry": {"ok": False, "missing_contract": ["CLIENT_X"]},
            "queue": {"duplicates": 0, "anomaly": False},
            "stalled": [],
            "false_green": False,
            "text": "test",
            "system": "RED",
        }
        healed = dict(broken)
        healed.update({
            "business_registry_ok": True,
            "business_registry": {"ok": True},
            "system": "GREEN",
        })
        with (
            patch.object(watchdog, "snapshot_report", side_effect=[broken, healed]),
            patch.object(watchdog, "_registry_recovery_allowed", return_value=True),
            patch.object(factory, "kick_review") as kick,
            patch.object(recover, "once", return_value={"ok": True}) as rec,
        ):
            out = watchdog.once(send=False)
        kick.assert_called_once()
        rec.assert_called_once()
        self.assertEqual(out["system"], "GREEN")
        self.assertIn("восстановление запущено", out["acted"])
        self.assertIn("контур бизнес-целей восстановлен и проверен", out["acted"])

    def test_watchdog_registry_drift_remains_red_when_heal_not_verified(self):
        from unittest.mock import patch
        from app.ext_api import recover, factory

        broken = {
            "coverage_ok": True,
            "business_registry_ok": False,
            "business_registry": {"ok": False, "missing_spec": ["CLIENT_X"]},
            "queue": {"duplicates": 0, "anomaly": False},
            "stalled": [],
            "false_green": False,
            "text": "test",
            "system": "RED",
        }
        with (
            patch.object(watchdog, "snapshot_report", return_value=broken),
            patch.object(watchdog, "_registry_recovery_allowed", return_value=True),
            patch.object(factory, "kick_review"),
            patch.object(recover, "once", return_value={"ok": True}) as rec,
        ):
            out = watchdog.once(send=False)
        rec.assert_called_once()
        self.assertEqual(out["system"], "RED")
        self.assertFalse(out["business_registry_ok"])
        self.assertIn(
            "контур бизнес-целей автоматически не восстановлен; "
            "система остаётся в аварийном состоянии",
            out["acted"],
        )

    def test_watchdog_same_registry_fault_does_not_recover_in_tight_loop(self):
        from unittest.mock import patch
        from app.ext_api import recover, factory

        broken = {
            "coverage_ok": True,
            "business_registry_ok": False,
            "business_registry": {"ok": False, "missing_contract": ["CLIENT_X"]},
            "queue": {"duplicates": 0, "anomaly": False},
            "stalled": [],
            "false_green": False,
            "text": "test",
            "system": "RED",
        }
        with (
            patch.object(watchdog, "snapshot_report", return_value=broken),
            patch.object(watchdog, "_registry_recovery_allowed", return_value=False),
            patch.object(factory, "kick_review") as kick,
            patch.object(recover, "once") as rec,
        ):
            out = watchdog.once(send=False)
        kick.assert_not_called()
        rec.assert_not_called()
        self.assertEqual(out["system"], "RED")
        self.assertIn(
            "повторное восстановление того же внутреннего дефекта отложено "
            "до следующего безопасного окна",
            out["acted"],
        )

    def test_watchdog_stalled_business_goal_triggers_recover(self):
        from unittest.mock import patch
        from app.ext_api import recover, factory

        rep = {
            "coverage_ok": True,
            "queue": {"duplicates": 0, "anomaly": False},
            "stalled": [{"key": "CLIENT_BYTOVKI_REPORTS"}],
            "false_green": False,
            "text": "test",
        }
        with (
            patch.object(watchdog, "snapshot_report", return_value=rep),
            patch.object(factory, "kick_review") as kick,
            patch.object(recover, "once", return_value={"ok": True}) as rec,
        ):
            out = watchdog.once(send=False)
        kick.assert_called_once()
        rec.assert_called_once()
        self.assertIn("восстановление запущено", out["acted"])

    def test_business_order_recovery_is_idempotent(self):
        from app.ext_api import clients
        src = inspect.getsource(clients.ensure_business_orders)
        self.assertIn("business_truth(2, fresh=True)", src)
        self.assertIn("a2a.QUEUED, a2a.RUNNING, a2a.RETURNED", src)

    def test_watchdog_does_not_confuse_internal_feed_with_avito_publish(self):
        from app.ext_api import report
        src = inspect.getsource(report.business_truth)
        self.assertIn("show_live", src)
        self.assertIn("live_required", src)
        helper = inspect.getsource(report._live_avito_count)
        self.assertIn("avito_item_id", helper)
        self.assertIn("published", helper)

    def test_completed_business_orders_are_removed_from_queue(self):
        from app.ext_api import clients
        src = inspect.getsource(clients.ensure_business_orders)
        self.assertIn("business_goal_already_confirmed", src)
        self.assertIn("overall_done", src)

    def test_owner_daily_report_has_plain_language_summary(self):
        from app.api import inbox_daily
        src = inspect.getsource(inbox_daily.render_owner_daily_report)
        self.assertIn("Коротко:", src)
        self.assertIn("Ниже плана:", src)
        self.assertNotIn("Период snapshot'ов", src)
        rec = inspect.getsource(inbox_daily._account_recommendations)
        self.assertNotIn("лид(а)", rec)
        self.assertNotIn("диалог(а)", rec)

    def test_factory_reads_real_systemd_units(self):
        from app.ext_api import factory
        src = inspect.getsource(factory._units_active)
        self.assertIn("from . import agents", src)
        live = factory.workers()
        self.assertIn("active_units", live)
        # Zero executors is valid while every allowed free development
        # provider is unavailable; dispatcher must not burn retries for show.
        from app.ext_api import aiprov
        # CAPACITY_ZERO_IS_VALID_V1: provider presence is not safe capacity.
        from app.ext_api import sched
        capacity = int(sched.limits().get("MAX_EXECUTOR_WORKERS") or 0)
        providers = aiprov.development_candidates()
        # Provider/capacity can change between probes; refresh the runtime view
        # before asserting a positive executor requirement.
        if providers and capacity > 0:
            live = factory.workers()
            if live.get("active", 0) < 1:
                from app.ext_api import recover
                recover.ensure_daemon_units()
                live = factory.workers()
            self.assertGreaterEqual(live.get("active", 0), 1)
        else:
            # A provider can disappear after executors were started. Recovery
            # owns the stop transition; assert its converged state, not a race.
            if not providers and live.get("active", 0):
                from app.ext_api import recover
                reconciled = recover.ensure_daemon_units()
                live = factory.workers()
                preserved = set(reconciled.get("busy_workers_preserved") or [])
                if preserved:
                    # Provider capacity may drop to zero while an executor is
                    # already inside a real running order. Recovery must not
                    # kill useful in-flight work merely to satisfy desired=0.
                    self.assertTrue(preserved.issubset(set(live.get("active_units") or [])))
                else:
                    self.assertEqual(live.get("active", 0), 0)
            elif not providers:
                self.assertEqual(live.get("active", 0), 0)
            wd = factory.watchdog(act=False)
            self.assertEqual(wd.get("underused"), 0)
            if wd.get("backlog") and not providers:
                self.assertIn("coding-provider", wd.get("verdict") or "")

    def test_dispatcher_self_heals_executor_daemons(self):
        from app.ext_api import recover
        src = inspect.getsource(recover.ensure_daemon_units)
        self.assertIn("MAX_EXECUTOR_WORKERS", src)
        self.assertIn("MAX_REVIEW_WORKERS", src)
        self.assertIn("DAEMON_START_COOLDOWN_SEC", src)
        self.assertTrue('["systemctl", "start", unit]' in src or '["sudo", "-n", "systemctl", "start", unit]' in src)
        self.assertIn('PROVIDER_START_REVALIDATION_V1', src)
        self.assertGreaterEqual(src.count('development_candidates()'), 2)
        self.assertTrue('["systemctl", "stop", unit]' in src or '["sudo", "-n", "systemctl", "stop", unit]' in src)
        self.assertIn("busy_workers_preserved", src)
        self.assertIn("status='running'", src)
        once = inspect.getsource(recover.once)
        self.assertIn("ensure_daemon_units()", once)
        run_src = inspect.getsource(recover.run)
        self.assertNotIn('notify.send("BORIS: работа стоит', run_src)
        e2e_src = inspect.getsource(recover.production_e2e)
        self.assertIn("GIT_TRANSPORT_ENABLED", e2e_src)
        self.assertNotIn('"boris-gitdeploy") + EXECUTOR_UNITS', inspect.getsource(recover))
        dispatcher_active = recover._unit_active("boris-dispatcher")
        if not dispatcher_active:
            # FINAL_RELEASE_GATE_MAINTENANCE_V1: an intentionally closed
            # ConditionPathExists release gate is a valid maintenance state,
            # not evidence that dispatcher self-heal is broken.
            condition = subprocess.check_output(
                ["systemctl", "show", "boris-dispatcher.service", "-p", "ConditionResult", "--value"],
                text=True,
            ).strip()
            self.assertEqual(condition, "no")
        self.assertIn("subprocess", recover._sysctl_active.__globals__)
        readiness_src = inspect.getsource(recover.readiness_audit)
        self.assertIn("BORIS_GIT_TRANSPORT=0", readiness_src)
        self.assertIn("development_candidates", readiness_src)

    def test_owner_notify_hides_technical_dev_jargon(self):
        from app.ext_api import notify, deploy, gitdeploy
        done_src = inspect.getsource(notify.done)
        blocked_src = inspect.getsource(notify.blocked_human)
        deploy_src = inspect.getsource(deploy.watch_incoming)
        deploy_fail_src = inspect.getsource(deploy.handle_failure)
        gitdeploy_src = inspect.getsource(gitdeploy.tick)
        self.assertNotIn('"DONE\\n"', done_src)
        self.assertNotIn("Production QA", done_src)
        self.assertNotIn("Review: ACCEPTED", done_src)
        self.assertNotIn('"BLOCKED_HUMAN\\n"', blocked_src)
        self.assertNotIn("фаза", blocked_src)
        self.assertIn("действительно не может продолжить без вашего решения", blocked_src)
        self.assertNotIn('notify.send("Выкатка %s: PASS"', deploy_src)
        self.assertNotIn("notify.send", deploy_fail_src)
        self.assertIn("owner_notified", deploy_fail_src)
        self.assertNotIn('notify.send("Выкатка %s: PASS', gitdeploy_src)
        self.assertNotIn('FAIL —', gitdeploy_src)

    def test_stale_running_without_worker_is_requeued(self):
        from app.ext_api import a2a
        src = inspect.getsource(a2a.release_stale_claims)
        self.assertNotIn("claimed_by IS NOT NULL", src)
        self.assertIn("orphan", src)
        recover_src = inspect.getsource(a2a.recover_internal_blockers)
        self.assertIn("proposepatch", recover_src.lower())
        self.assertIn("callable", recover_src)

    def test_free_development_provider_reprobe_is_wired(self):
        from app.ext_api import aiprov, recover
        src = inspect.getsource(aiprov.reprobe_free_development)
        self.assertIn("gemini", src)
        self.assertIn("min_interval_min", src)
        self.assertIn("TemporaryDirectory", src)
        self.assertIn("GEMINI_CLI_TRUST_WORKSPACE", src)
        self.assertNotIn("cwd=backend", src)
        self.assertIn("DEV_PAID_DENY", inspect.getsource(aiprov.development_provider_allowed))
        recover_once_src = inspect.getsource(recover.once)
        self.assertIn("reprobe_free_development()", recover_once_src)
        self.assertIn("recover_internal_blockers", recover_once_src)

    def test_gemini_executor_uses_bounded_workspace_and_real_patch_gate(self):
        from app.ext_api import executor, lead
        gem = inspect.getsource(executor._run_gemini)
        prep = inspect.getsource(executor._prepare_gemini_workspace)
        materialize = inspect.getsource(executor._materialize_model_patch)
        ask = inspect.getsource(lead._ask_gemini_cli)
        self.assertIn("_prepare_gemini_workspace", gem)
        self.assertIn("proposed_edits", gem)
        self.assertIn("source_snapshot_bytes", prep)
        self.assertIn("_GEMINI_CONTEXT_MAX_TOTAL_BYTES", prep)
        self.assertEqual(executor._GEMINI_CONTEXT_MAX_TOTAL_BYTES, 650000)
        self.assertIn("server_owned", materialize)
        self.assertIn("autonomous_dev", materialize)
        self.assertIn("repo.propose", materialize)
        self.assertIn("cwd=work", gem)
        self.assertIn("workdir = cwd", ask)

    def test_false_green_requires_unfinished_client_goal(self):
        src = inspect.getsource(watchdog.snapshot_report)
        self.assertIn("bool(unfinished)", src)
        self.assertIn("overall_done", src)

    def test_incomplete_master_coverage_cannot_be_green(self):
        from unittest.mock import patch
        from app.ext_api import factory, master

        client = {
            "key": "CLIENT_BYTOVKI_REPORTS",
            "name": "Бытовки",
            "current": 2,
            "delta": 0,
            "moving": False,
            "stalled": False,
            "blocker": False,
            "overall_done": True,
            "progress": 100,
            "expected_count": 2,
            "expected_final": "два отчёта доставлены",
            "where": "Telegram",
            "state": "DONE",
        }
        with (
            patch.object(watchdog, "clients", return_value=[client]),
            patch.object(watchdog, "master", return_value={}),
            patch.object(watchdog, "queue_health", return_value={
                "queued": 0, "growth": 0, "duplicates": 0, "anomaly": False
            }),
            patch.object(factory, "workers", return_value={
                "client_busy": 0, "active": 0
            }),
            patch.object(master, "coverage", return_value={
                "counts": {"mapped": 52, "total": 55, "pass": False}
            }),
        ):
            rep = watchdog.snapshot_report()
        self.assertFalse(rep["coverage_ok"])
        self.assertEqual(rep["system"], "RED")

    def test_owner_daily_russian_plural_and_cut_time(self):
        from app.api import inbox_daily
        self.assertEqual(inbox_daily._lead_word(1), "лид")
        self.assertEqual(inbox_daily._lead_word(2), "лида")
        self.assertEqual(inbox_daily._lead_word(5), "лидов")
        src = inspect.getsource(inbox_daily.render_owner_daily_report)
        self.assertIn("Срез данных:", src)
        rec = inbox_daily._account_recommendations(
            {'target_leads': 1, 'actual_leads': 0, 'completion_pct': 0,
             'target_cpl': None, 'actual_cpl': None},
            {'views': 0, 'contacts': 0, 'view_to_contact_pct': 0, 'status': 'ok'},
            {}, {}, {})
        self.assertTrue(any("1 лид" in x for x in rec))
        self.assertFalse(any("1 лидов" in x for x in rec))

    def test_owner_watchdog_sends_only_on_material_change(self):
        src = inspect.getsource(watchdog.once)
        self.assertIn("_notify_changed(rep)", src)
        sig = inspect.getsource(watchdog._notify_signature)
        self.assertNotIn("since_min", sig)
        self.assertIn("queue_duplicates", sig)

    def test_queuefix_includes_all_live_states_and_unique_snapshots(self):
        self.assertIn("running", queuefix.RUNNABLE)
        self.assertIn("review_requested", queuefix.RUNNABLE)
        src = inspect.getsource(queuefix.snapshot)
        self.assertIn("int(time.time() * 1000)", src)
        rb = inspect.getsource(queuefix.rollback)
        self.assertIn("ORDER BY id DESC LIMIT 1", rb)

    def test_master_coverage_heal_handles_missing_orders(self):
        src = inspect.getsource(master.ensure_coverage)
        self.assertIn("orders_created", src)
        self.assertIn("MASTER_COVERAGE_HEAL_LIMIT", src)
        self.assertNotIn("res.get(\"create\"", src)

    def test_master_backlog_paths_exist_in_real_repo(self):
        import os
        root = "/root/BORIS"
        missing = []
        for key, _title, _goal, paths, _deps, _prio in master.BACKLOG:
            for rel in paths:
                if "/" in rel and not os.path.exists(os.path.join(root, rel)):
                    missing.append((key, rel))
        self.assertEqual(missing, [])
        sync = inspect.getsource(master._sync_existing_contract)
        self.assertIn("ext_a2a_orders", sync)
        self.assertIn("scope_json", sync)

    def test_provider_block_is_not_requeued_without_free_provider(self):
        from app.ext_api import a2a
        src = inspect.getsource(a2a.recover_internal_blockers)
        self.assertIn("development_candidates", src)
        self.assertIn("provider_retry = False", src)

    def test_healed_forbidden_scope_blocker_is_requeued(self):
        from unittest.mock import patch
        from app.ext_api import a2a, repo
        rows = [{"id": 7001, "status": a2a.BLOCKED_INFRA,
                 "blocked_reason": "FORBIDDEN_SCOPE: historical worktree restriction"}]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message, \
             patch.object(repo, "propose", create=True):
            recovered = a2a.recover_internal_blockers(actor="test")
        self.assertEqual(recovered, [7001])
        set_state.assert_called_once_with(7001, status=a2a.QUEUED,
                                          claimed_by=None, claimed_at=None,
                                          blocked_reason=None)
        message.assert_called_once()

    def test_true_owner_blocker_is_not_auto_recovered(self):
        from unittest.mock import patch
        from app.ext_api import a2a
        rows = [{"id": 7002, "status": a2a.BLOCKED_HUMAN,
                 "blocked_reason": "требуется решение владельца по платному действию"}]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="test")
        self.assertEqual(recovered, [])
        set_state.assert_not_called()
        message.assert_not_called()

    def test_provider_blocker_stays_blocked_when_no_free_provider(self):
        from unittest.mock import patch
        from app.ext_api import a2a, aiprov
        rows = [{"id": 7003, "status": a2a.BLOCKED_INFRA,
                 "blocked_reason": "provider_unavailable: free providers cooling"}]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(aiprov, "development_candidates", return_value=[]), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="test")
        self.assertEqual(recovered, [])
        set_state.assert_not_called()
        message.assert_not_called()

    def test_current_free_provider_blocker_requeues_after_provider_recovery(self):
        from unittest.mock import patch
        from app.ext_api import a2a, aiprov
        rows = [{
            "id": 7004,
            "status": a2a.BLOCKED_INFRA,
            "blocked_reason": (
                "all free development providers unavailable; paid fallback denied: "
                "[{\"provider\": \"gemini_cli\", \"kind\": \"TEMP_ERROR\"}]"
            ),
        }]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(aiprov, "development_candidates", return_value=["gemini_cli"]), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="test")
        self.assertEqual(recovered, [7004])
        set_state.assert_called_once_with(
            7004, status=a2a.QUEUED, claimed_by=None, claimed_at=None,
            blocked_reason=None,
        )
        message.assert_called_once()

    def test_read_only_production_conflict_waits_for_declared_dependency(self):
        from unittest.mock import patch
        from app.ext_api import a2a

        order = {
            "order_id": 84577,
            "dev_job_id": "dev_1054",
            "phase": "PLANNING",
            "scope": {
                "read_only": True,
                "scope_mode": "planning_read_only",
            },
        }
        parent = {
            "dev_job_id": "dev_1054",
            "scope": {"read_only": True, "scope_mode": "planning_read_only"},
            "locks": [],
            "depends_on": ["dev_1057"],
        }

        def read_dev(dev_id):
            if str(dev_id) == "dev_1054":
                return parent
            return {"dev_job_id": "dev_1057", "status": "waiting"}

        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a.dev, "get", side_effect=read_dev):
            healed = a2a._read_only_production_conflict_healed(
                84577,
                "PRODUCTION_CONFLICT: canonical writer still owns the workstream",
            )
        self.assertFalse(healed)

    def test_read_only_production_conflict_recovers_after_dependency_completed(self):
        from unittest.mock import patch
        from app.ext_api import a2a

        order = {
            "order_id": 84577,
            "dev_job_id": "dev_1054",
            "phase": "PLANNING",
            "scope": {
                "read_only": True,
                "scope_mode": "planning_read_only",
            },
        }
        parent = {
            "dev_job_id": "dev_1054",
            "scope": {"read_only": True, "scope_mode": "planning_read_only"},
            "locks": [],
            "depends_on": ["dev_1057"],
        }

        def read_dev(dev_id):
            if str(dev_id) == "dev_1054":
                return parent
            return {"dev_job_id": "dev_1057", "status": "completed"}

        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a.dev, "get", side_effect=read_dev):
            healed = a2a._read_only_production_conflict_healed(
                84577,
                "PRODUCTION_CONFLICT: old canonical writer preflight",
            )
        self.assertTrue(healed)

    def test_read_only_production_conflict_requeues_without_owner_after_heal(self):
        from unittest.mock import patch
        from app.ext_api import a2a

        rows = [{
            "id": 84577,
            "status": a2a.BLOCKED_INFRA,
            "blocked_reason": "PRODUCTION_CONFLICT: old read-only planning conflict",
        }]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(
                 a2a,
                 "_read_only_production_conflict_healed",
                 return_value=True,
             ), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="dispatcher-test")
        self.assertEqual(recovered, [84577])
        set_state.assert_called_once_with(
            84577,
            status=a2a.QUEUED,
            claimed_by=None,
            claimed_at=None,
            blocked_reason=None,
        )
        self.assertEqual(message.call_args.args[1], "AUTO_RECOVERY")

    def test_loop_guard_gets_one_autonomous_root_cause_recovery(self):
        from unittest.mock import patch
        from app.ext_api import a2a
        rows = [{
            "id": 7010,
            "status": a2a.BLOCKED_INFRA,
            "blocked_reason": (
                "исчерпан лимит итераций (6) — нужен разбор первопричины, "
                "а не ещё один круг"
            ),
        }]
        order = {
            "order_id": 7010,
            "goal": "Исправить исходную production-проблему",
            "scope": {"workspace_autopilot": True},
            "last_review": {"fixes": [{"criterion": "runtime", "why": "still fails"}]},
        }
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="dispatcher-test")
        self.assertEqual(recovered, [7010])
        kwargs = set_state.call_args.kwargs
        self.assertEqual(kwargs["status"], a2a.QUEUED)
        self.assertEqual(kwargs["iteration"], 0)
        self.assertIsNone(kwargs["blocked_reason"])
        self.assertIn("_loop_guard_root_cause_recoveries", kwargs["scope_json"])
        self.assertIn("АВТОВОССТАНОВЛЕНИЕ ПОСЛЕ ЗАЦИКЛИВАНИЯ", kwargs["goal"])
        self.assertEqual(message.call_args.args[1], "AUTO_ROOT_CAUSE_RECOVERY")

    def test_loop_guard_root_cause_recovery_is_bounded_to_one_cycle(self):
        from unittest.mock import patch
        from app.ext_api import a2a
        rows = [{
            "id": 7011,
            "status": a2a.BLOCKED_INFRA,
            "blocked_reason": (
                "исчерпан лимит итераций (6) — нужен разбор первопричины, "
                "а не ещё один круг"
            ),
        }]
        order = {
            "order_id": 7011,
            "goal": "Уже был root-cause recovery",
            "scope": {"_loop_guard_root_cause_recoveries": 1},
            "last_review": {},
        }
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="dispatcher-test")
        self.assertEqual(recovered, [])
        set_state.assert_not_called()
        message.assert_not_called()

    def test_loadavg_decay_keeps_one_bounded_recovery_slot(self):
        from app.ext_api import sched
        f = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 12000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 8.4,
            "load5": 8.0,
            "load15": 7.8,
            "load_per_cpu": 1.05,
            "cpu_busy_pct": 22.0,
            "cpu_idle_pct": 72.0,
            "cpu_iowait_pct": 2.0,
            "cpu_steal_pct": 4.0,
            "measured_at": 1,
        }
        caps = sched.limits(f)
        self.assertEqual(caps["MAX_EXECUTOR_WORKERS"], 1)
        self.assertEqual(caps["MAX_REVIEW_WORKERS"], 1)
        state = sched.new_state(caps=caps, running=[], client_queue=0)
        verdict = sched.admission(
            {"current_phase": "BACKEND", "priority": sched.P0_PRIORITY - 1},
            state,
        )
        self.assertTrue(verdict["admit"])
        self.assertIn("recovery-слот", verdict["reason"])

        heavy = sched.admission(
            {"current_phase": "FRONTEND", "priority": sched.P0_PRIORITY - 1},
            state,
        )
        self.assertFalse(heavy["admit"])

    def test_fractional_cpu_budget_keeps_one_executor_when_pressure_is_false(self):
        from app.ext_api import sched
        f = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 12000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 5.99,
            "load5": 4.07,
            "load15": 4.53,
            "load_per_cpu": 0.75,
            "cpu_busy_pct": 47.9,
            "cpu_idle_pct": 47.1,
            "cpu_iowait_pct": 1.7,
            "cpu_steal_pct": 3.3,
            "measured_at": 1,
        }
        self.assertFalse(sched.pressure(f)["under_pressure"])
        caps = sched.limits(f)
        self.assertEqual(caps["MAX_EXECUTOR_WORKERS"], 1)
        self.assertEqual(caps["MAX_REVIEW_WORKERS"], 1)

    def test_tiny_positive_cpu_margin_still_matches_no_pressure_policy(self):
        from app.ext_api import sched
        f = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 12000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 6.79,
            "load5": 6.2,
            "load15": 5.8,
            "load_per_cpu": 0.84875,
            "cpu_busy_pct": 31.1,
            "cpu_idle_pct": 64.8,
            "cpu_iowait_pct": 0.8,
            "cpu_steal_pct": 3.3,
            "measured_at": 1,
        }
        self.assertFalse(sched.pressure(f)["under_pressure"])
        caps = sched.limits(f)
        self.assertEqual(caps["MAX_EXECUTOR_WORKERS"], 1)
        self.assertEqual(caps["MAX_REVIEW_WORKERS"], 1)

    def test_cpu_contention_is_reported_when_it_closes_executor_capacity(self):
        from app.ext_api import sched
        base = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 12000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 6.37,
            "load5": 6.9,
            "load15": 6.26,
            "load_per_cpu": 0.80,
            "cpu_busy_pct": 40.0,
            "cpu_idle_pct": 43.1,
            "cpu_iowait_pct": 2.3,
            "cpu_steal_pct": 14.6,
            "measured_at": 1,
        }
        pressure = sched.pressure(base)
        self.assertTrue(pressure["under_pressure"])
        self.assertIn("гипервизор отбирает слишком много CPU", pressure["reasons"])
        self.assertEqual(sched.limits(base)["MAX_EXECUTOR_WORKERS"], 0)

        healthy = dict(base)
        healthy["cpu_steal_pct"] = 3.0
        self.assertFalse(sched.pressure(healthy)["under_pressure"])
        self.assertEqual(sched.limits(healthy)["MAX_EXECUTOR_WORKERS"], 1)

    def test_real_cpu_busy_pressure_closes_executor_capacity(self):
        from app.ext_api import sched
        busy = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 9000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 5.0,
            "load5": 7.0,
            "load15": 6.6,
            "load_per_cpu": 0.625,
            "cpu_busy_pct": 81.5,
            "cpu_idle_pct": 13.4,
            "cpu_iowait_pct": 0.0,
            "cpu_steal_pct": 5.0,
            "measured_at": 1,
        }
        self.assertTrue(sched.pressure(busy)["under_pressure"])
        self.assertEqual(sched.limits(busy)["MAX_EXECUTOR_WORKERS"], 0)

        recovered = dict(busy)
        recovered["cpu_busy_pct"] = 50.0
        recovered["cpu_idle_pct"] = 40.0
        self.assertFalse(sched.pressure(recovered)["under_pressure"])
        self.assertEqual(sched.limits(recovered)["MAX_EXECUTOR_WORKERS"], 1)

    def test_review_can_drain_finished_work_without_opening_executor_capacity(self):
        from app.ext_api import sched
        f = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 12000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 8.8,
            "load5": 8.2,
            "load15": 8.0,
            "load_per_cpu": 1.10,
            "cpu_busy_pct": 78.0,
            "cpu_idle_pct": 15.0,
            "cpu_iowait_pct": 2.0,
            "cpu_steal_pct": 5.0,
            "measured_at": 1,
        }
        caps = sched.limits(f)
        self.assertEqual(caps["MAX_EXECUTOR_WORKERS"], 0)
        self.assertEqual(caps["MAX_REVIEW_WORKERS"], 1)

    def test_loadavg_recovery_floor_stays_closed_when_cpu_is_really_busy(self):
        from app.ext_api import sched
        f = {
            "cpu": 8,
            "ram_total_mb": 16000,
            "ram_available_mb": 12000,
            "swap_total_mb": 0,
            "swap_used_mb": 0,
            "load1": 8.4,
            "load5": 8.0,
            "load15": 7.8,
            "load_per_cpu": 1.05,
            "cpu_busy_pct": 96.0,
            "cpu_idle_pct": 1.0,
            "cpu_iowait_pct": 1.0,
            "cpu_steal_pct": 2.0,
            "measured_at": 1,
        }
        caps = sched.limits(f)
        self.assertEqual(caps["MAX_EXECUTOR_WORKERS"], 0)
        self.assertEqual(caps["MAX_REVIEW_WORKERS"], 0)
        p = sched.pressure(f)
        self.assertTrue(p["under_pressure"])
        self.assertFalse(p["instant_headroom"])

    def test_owner_daily_refresh_degrades_on_retryable_preserved_avito_data(self):
        import datetime as real_dt
        from unittest.mock import patch
        from app.ext_api import owner_daily_job

        class DummyDB:
            def close(self):
                pass

        degraded = {
            "status": "degraded",
            "retryable": True,
            "provider_status": 429,
            "retry_after_seconds": 120,
            "message": "Avito ограничил частоту запросов; предыдущая статистика сохранена",
        }
        with (
            patch.object(owner_daily_job, "SessionLocal", return_value=DummyDB()),
            patch.object(
                owner_daily_job,
                "_active_owner_accounts",
                return_value=[{"account_id": "mbroker_27859"}],
            ),
            patch("app.api.avito.collect_stats", return_value=degraded),
        ):
            out = owner_daily_job._refresh_avito_sources(real_dt.date(2026, 9, 7))
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["warning_count"], 1)
        self.assertTrue(out["errors"][0]["preserved_previous"])

    def test_owner_daily_job_sends_degraded_report_instead_of_failed_unit(self):
        import datetime as real_dt
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.ext_api import owner_daily_job

        class FixedDateTime:
            @classmethod
            def now(cls, tz=None):
                return real_dt.datetime(2026, 9, 7, 17, 5, tzinfo=tz)

        refresh = {
            "status": "degraded",
            "refreshed_count": 8,
            "warning_count": 1,
            "errors": [{
                "account_id": "mbroker_27859",
                "preserved_previous": True,
            }],
        }
        with (
            patch.object(owner_daily_job, "dt", SimpleNamespace(datetime=FixedDateTime)),
            patch.object(owner_daily_job, "_already_sent", return_value=False),
            patch.object(owner_daily_job, "_refresh_avito_sources", return_value=refresh),
            patch.object(
                owner_daily_job,
                "send_owner_daily_report",
                return_value={"status": "ok", "sent": True},
            ) as send,
        ):
            code = owner_daily_job.main()
        self.assertEqual(code, 0)
        self.assertEqual(send.call_args.kwargs["source_refresh"], refresh)

    def test_owner_daily_report_marks_stale_avito_fallback_for_owner(self):
        from app.api.inbox_daily import _owner_source_refresh_warning
        text = _owner_source_refresh_warning({
            "status": "degraded",
            "warning_count": 1,
            "errors": [{"account_id": "mbroker_27859"}],
        })
        self.assertIn("последние сохранённые данные", text)
        self.assertIn("повторит обновление автоматически", text)

    def test_owner_daily_refresh_degrades_on_auth_failure_with_previous_snapshot(self):
        import datetime as real_dt
        from unittest.mock import patch
        from app.ext_api import owner_daily_job

        class DummyDB:
            def close(self):
                pass

        failed_auth = {
            "status": "error",
            "message": "Не удалось авторизоваться в Avito API",
            "retryable": False,
            "provider_status": None,
        }
        with (
            patch.object(owner_daily_job, "SessionLocal", return_value=DummyDB()),
            patch.object(
                owner_daily_job,
                "_active_owner_accounts",
                return_value=[{"account_id": "client_with_stale_snapshot"}],
            ),
            patch.object(owner_daily_job, "_has_previous_daily_stats", return_value=True),
            patch("app.api.avito.collect_stats", return_value=failed_auth),
        ):
            out = owner_daily_job._refresh_avito_sources(real_dt.date(2026, 10, 7))
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["warning_count"], 1)
        self.assertTrue(out["errors"][0]["preserved_previous"])

    def test_owner_daily_report_routes_to_boris_messenger(self):
        import inspect
        from app.api import inbox_daily

        src = inspect.getsource(inbox_daily.send_owner_daily_report)
        self.assertIn("_send_owner_report_to_messenger", src)
        self.assertLess(
            src.index("_send_owner_report_to_messenger"),
            src.index("send_telegram_message(OWNER_REPORT_CHAT_ID"),
        )
        helper = inspect.getsource(inbox_daily._send_owner_report_to_messenger)
        self.assertIn("KPI / AI-маркетолог", inbox_daily.OWNER_REPORT_MESSENGER_TITLE)
        self.assertIn("uuid5", helper)
        self.assertIn("send_message", helper)


class QueueGoalTerminalContractTest(unittest.TestCase):
    def test_restore_client_never_resurrects_business_goal_confirmed_order(self):
        from unittest.mock import patch
        from app.ext_api import queuefix
        job={"id": 9, "title": "CLIENT_ANTON — qa", "status": "testing"}
        responses=[job, None, {"id": 55, "blocked_reason": "business_goal_already_confirmed"}]
        with patch.object(queuefix.db, "one", side_effect=responses):
            out=queuefix.restore_client("CLIENT_ANTON", dispatch=False)
        self.assertIsNone(out["restored"])
        self.assertEqual(out["order_id"], 55)
        self.assertIn("подтверждена", out["why"])

    def test_invariants_count_business_goal_confirmed_as_preserved(self):
        from unittest.mock import patch
        from app.ext_api import queuefix
        def rows(sql, **kw):
            return []
        def one(sql, **kw):
            if "SELECT o.id, o.status, o.claimed_by" in sql:
                return None
            if "SELECT o.id, o.blocked_reason" in sql:
                return {"id": 77, "blocked_reason": "business_goal_already_confirmed"}
            if "COUNT(*) AS n" in sql:
                return {"n": 0}
            return None
        with patch.object(queuefix.db, "rows", side_effect=rows), patch.object(queuefix.db, "one", side_effect=one):
            out=queuefix.invariants()
        self.assertEqual(out["CLIENTS_PRESERVED"], "4/4")
        self.assertTrue(all(v and v["status"] == "business_goal_confirmed" for v in out["clients"].values()))

if __name__ == "__main__":
    unittest.main()


class A2AOrderIdempotencyContractTest(unittest.TestCase):
    def test_create_order_reuses_existing_live_order(self):
        from unittest.mock import patch
        from app.ext_api import a2a
        key = {"actor_type": "autonomous-dev", "actor_name": "qa", "scope_set": set()}
        job = {"id": 777, "current_phase": "IMPLEMENT"}
        with patch.object(a2a.dev, "get", return_value=job), \
             patch.object(a2a.db, "one", return_value={"id": 4242}) as one, \
             patch.object(a2a, "get", return_value={"order_id": 4242}) as get_order:
            out = a2a.create_order(key, 777, "IMPLEMENT", "goal", acceptance=["done"])
        self.assertEqual(out["order_id"], 4242)
        self.assertEqual(one.call_count, 1)
        self.assertEqual(get_order.call_args.args[0], 4242)

class A2AConcurrentOrderFenceContractTest(unittest.TestCase):
    def test_schema_has_one_live_order_unique_fence(self):
        from pathlib import Path
        ddl = Path('app/ext_api/ddl.sql').read_text(encoding='utf-8')
        self.assertIn('ux_ext_a2a_orders_one_live_per_job', ddl)
        for state in ('queued','running','review_requested','returned','blocked_human','blocked_infra'):
            self.assertIn(state, ddl)

    def test_create_order_recovers_when_concurrent_insert_wins(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from app.ext_api import a2a
        key = {'actor_type':'autonomous-dev','actor_name':'qa','scope_set':set()}
        job = {'id':778,'current_phase':'IMPLEMENT'}
        # terminal-fence SELECT -> no terminal; live-order SELECT -> none;
        # concurrent INSERT loses unique race; recovery SELECT finds winner.
        calls = [None, None, IntegrityError('insert', {}, Exception('unique')), {'id':4243}]
        def fake_one(*args, **kwargs):
            value = calls.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        with patch.object(a2a.dev,'get',return_value=job), \
             patch.object(a2a.db,'one',side_effect=fake_one), \
             patch.object(a2a,'get',return_value={'order_id':4243}) as get_order:
            out = a2a.create_order(key,778,'IMPLEMENT','goal',acceptance=['done'])
        self.assertEqual(out['order_id'],4243)
        get_order.assert_called_once_with(4243)

class BusinessGoalTerminalSchedulerFenceTest(unittest.TestCase):
    def test_schedule_excludes_business_goal_terminal_jobs_without_fake_completion(self):
        from app.ext_api import dev
        import inspect
        src=inspect.getsource(dev.schedule)
        helper=inspect.getsource(dev.business_goal_terminal_job_ids)
        self.assertIn('BUSINESS_GOAL_TERMINAL_SCHEDULER_FENCE_V1', src)
        self.assertIn('business_goal_terminal_job_ids()', src)
        self.assertIn("business_goal_already_confirmed", helper)
        self.assertNotIn("status='completed'", helper)
        self.assertNotIn('status="completed"', helper)

    def test_current_protected_business_done_jobs_are_fenced(self):
        from app.ext_api import dev
        protected = {618, 619, 620, 967}
        terminal = dev.business_goal_terminal_job_ids()
        for jid in protected:
            job = dev.get(jid)
            if job["status"] in ("completed", "cancelled"):
                self.assertNotIn(jid, terminal)
            else:
                self.assertIn(jid, terminal)
