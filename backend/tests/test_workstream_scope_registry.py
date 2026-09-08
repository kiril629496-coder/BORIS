import importlib.util
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from app.services.workstream_scope import (
    ScopeError,
    assert_scope_paths,
    classify_workstream_paths,
    load_registry,
    neighbor_chat_map_health,
    normalize_scope_path,
    owned_scope_conflicts,
    scope_claims_overlap,
    unowned_scope_conflicts,
    scope_path_overlaps_pattern,
    registry_overlaps,
    single_writer_scope_health,
    social_reliability_scope_health,
    writable_owners,
)


class WorkstreamScopeRegistryTest(unittest.TestCase):
    def test_registry_has_no_overlapping_writers(self):
        self.assertEqual(registry_overlaps(load_registry()), [])

    def test_social_core_owns_shared_social_runtime(self):
        for path in (
            "posting_runner.py",
            "boris_background_worker.py",
            "app/services/social_owner_policy_v3.py",
            "app/services/incident_reconciler.py",
            "tests/test_social_due_rescue_generation.py",
            "run/social_vk_replacement_watch_v1.py",
        ):
            self.assertEqual(writable_owners(path), ["social_reliability_core"], path)

    def test_social_core_rejects_avito_email_telephony_and_frontend(self):
        for path in (
            "app/api/avito.py",
            "app/services/prospect_campaigns.py",
            "app/services/telephony_core.py",
            "../frontend/src/App.tsx",
        ):
            with self.assertRaises(ScopeError, msg=path):
                assert_scope_paths("social_reliability_core", [path])

    def test_client_workstream_cannot_claim_social_core(self):
        with self.assertRaises(ScopeError):
            assert_scope_paths("planb_client_operations", ["posting_runner.py"])

    def test_email_shared_paths_have_one_canonical_writer(self):
        expected = {
            "email_queue_runner.py": "email_outreach_core",
            "app/services/contact_outreach.py": "email_outreach_core",
            "app/services/email_queue.py": "email_outreach_core",
            "app/services/email_service.py": "email_outreach_core",
            "app/services/client_mailboxes.py": "email_outreach_core",
            "app/services/prospect_campaigns.py": "email_outreach_core",
            "app/services/prospect_reporting.py": "email_outreach_core",
            "app/services/owner_outreach_policy.py": "email_outreach_core",
            "app/services/owner_outreach_volume_safety.py": "email_outreach_core",
            "app/api/prospecting_scale.py": "email_outreach_core",
            "app/services/contact_prospector.py": "prospecting_discovery_core",
            "app/services/prospect_discovery.py": "prospecting_discovery_core",
            "app/services/prospecting.py": "prospecting_discovery_core",
            "app/services/prospect_replenisher.py": "prospecting_discovery_core",
            "app/api/prospecting.py": "prospecting_discovery_core",
            "app/services/email_tracking.py": "email_tracking_analytics",
            "app/services/email_outreach_cost_policy.py": "email_outreach_economics",
            "../frontend/app/email-outreach/page.tsx": "frontend_mobile_ui",
            "app/usage.py": "social_cost_accounting",
        }
        for path, owner in expected.items():
            self.assertEqual(writable_owners(path), [owner], path)

    def test_repo_path_aliases_cannot_bypass_scope_ownership(self):
        variants = (
            "app/services/prospect_campaigns.py",
            "backend/app/services/prospect_campaigns.py",
            "/root/BORIS/backend/app/services/prospect_campaigns.py",
        )
        for path in variants:
            self.assertEqual(writable_owners(path), ["email_outreach_core"], path)
        self.assertEqual(
            writable_owners("frontend/app/dashboard/prospecting/page.tsx"),
            ["frontend_mobile_ui"],
        )
        self.assertEqual(
            writable_owners("/root/BORIS/clients/boris-phone/android/MainActivity.kt"),
            ["telephony_core"],
        )
        self.assertEqual(
            normalize_scope_path("backend/app/services/email_queue.py"),
            "app/services/email_queue.py",
        )

    def test_avito_money_and_content_are_distinct_single_writers(self):
        money_paths = (
            "ai_marketer_hourly.sh",
            "app/api/cpx_advisor.py",
            "cpx_budget_brake.py",
            "cpx_cap_reconciler.py",
            "kpi_goal_runner.py",
            "app/services/marketing_experiment_journal.py",
            "app/services/marketing_money_policy.py",
        )
        for path in money_paths:
            self.assertEqual(writable_owners(path), ["avito_money_core"], path)
        for path in ("app/api/avito.py", "app/services/avito_account_throttle.py"):
            self.assertEqual(writable_owners(path), ["avito_integration_core"], path)
        with self.assertRaises(ScopeError):
            assert_scope_paths("avito_money_core", ["app/api/avito.py"])
        with self.assertRaises(ScopeError):
            assert_scope_paths("avito_marketing_core", ["app/api/cpx_advisor.py"])

    def test_avito_shared_adapter_has_neutral_owner(self):
        self.assertEqual(writable_owners("app/api/avito.py"), ["avito_integration_core"])
        self.assertEqual(writable_owners("app/services/avito_account_throttle.py"), ["avito_integration_core"])
        self.assertEqual(writable_owners("app/api/avito_categories.py"), ["avito_marketing_core"])
        with self.assertRaises(ScopeError):
            assert_scope_paths("avito_money_core", ["app/api/avito.py"])
        with self.assertRaises(ScopeError):
            assert_scope_paths("avito_marketing_core", ["app/api/avito.py"])
        with self.assertRaises(ScopeError):
            assert_scope_paths("avito_integration_core", ["app/api/cpx_advisor.py"])

    def test_dev_scope_classifier_detects_cross_workstream_parent_claims(self):
        broad = classify_workstream_paths(["backend/app/services"])
        self.assertTrue(broad["ambiguous"], broad)
        self.assertIn("email_outreach_core", broad["owners"])
        self.assertIn("email_tracking_analytics", broad["owners"])
        self.assertIn("prospecting_discovery_core", broad["owners"])

        exact = classify_workstream_paths(["backend/app/services/email_tracking.py"])
        self.assertFalse(exact["ambiguous"], exact)
        self.assertEqual(exact["owners"], ["email_tracking_analytics"])

        split = classify_workstream_paths([
            "backend/app/services/email_queue.py",
            "backend/app/services/prospect_discovery.py",
        ])
        self.assertTrue(split["ambiguous"], split)
        self.assertEqual(
            set(split["owners"]),
            {"email_outreach_core", "prospecting_discovery_core"},
        )

    def test_scope_path_overlap_understands_parent_directory_claim(self):
        self.assertTrue(
            scope_path_overlaps_pattern(
                "backend/app/services",
                "app/services/email_tracking.py",
            )
        )
        self.assertTrue(
            scope_path_overlaps_pattern(
                "backend/app/services",
                "app/services/social_*",
            )
        )
        self.assertFalse(
            scope_path_overlaps_pattern(
                "backend/app/services/email_tracking.py",
                "app/services/prospect_discovery.py",
            )
        )

    def test_current_coordination_chat_cannot_write_neighbor_product_code(self):
        foreign_paths = (
            "app/services/email_tracking.py",
            "app/services/email_queue.py",
            "app/services/prospect_discovery.py",
            "app/services/email_outreach_cost_policy.py",
            "../frontend/app/email-outreach/page.tsx",
        )
        for path in foreign_paths:
            with self.assertRaises(ScopeError, msg=path):
                assert_scope_paths("workstream_coordination", [path])

        own_paths = (
            "config/workstream_scope_registry.json",
            "app/services/workstream_scope.py",
            "run/workstream_scope_guard.py",
            ".boris_ops/chat_coordination/workstreams.json",
            ".boris_ops/chat_coordination/audit_workstreams.py",
        )
        self.assertTrue(
            assert_scope_paths("workstream_coordination", list(own_paths))["ok"]
        )

    def test_unowned_filesystem_conflicts_are_detected_without_guessing_owner(self):
        jobs = [
            {
                "id": 1,
                "title": "unowned broad",
                "status": "waiting",
                "paths": ["misc/unowned_scope"],
            },
            {
                "id": 2,
                "title": "unowned exact",
                "status": "waiting",
                "paths": ["misc/unowned_scope/a.py"],
            },
            {
                "id": 3,
                "title": "logical only",
                "status": "waiting",
                "paths": ["coordination:production_ai_restore"],
            },
        ]
        out = unowned_scope_conflicts(jobs)
        self.assertEqual(out["pair_count"], 1, out)
        self.assertEqual(out["conflicted_jobs"], {1: [2], 2: [1]})

    def test_periodic_guard_wires_unowned_overlap_quarantine(self):
        guard = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        ).read_text(encoding="utf-8")
        self.assertIn('DEV_UNOWNED_BLOCK_PREFIX="WORKSTREAM_UNOWNED_OVERLAP"', guard)
        self.assertIn("def _unowned_scope_guard(data:dict)->dict:", guard)
        self.assertIn("unowned_scope=_unowned_scope_guard(data)", guard)
        self.assertIn('details["reason"]="running_unowned_dev_scope_overlap"', guard)

    def test_periodic_guard_refreshes_scope_blocked_child_truth(self):
        guard = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        ).read_text(encoding="utf-8")
        self.assertIn("WORKSTREAM_SCOPE_BLOCKED_ORDER_TRUTH_V1", guard)
        self.assertIn("refreshed_scope_orders", guard)
        self.assertIn(
            '"blocked_prefix":DEV_SCOPE_BLOCK_PREFIX+":%"',
            guard,
        )
        self.assertIn("AND COALESCE(blocked_reason,'')<>:reason", guard)

    def test_periodic_guard_wires_owned_overlap_quarantine(self):
        guard = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        ).read_text(encoding="utf-8")
        self.assertIn('DEV_OWNED_BLOCK_PREFIX="WORKSTREAM_OWNED_OVERLAP"', guard)
        self.assertIn("def _owned_scope_guard(data:dict)->dict:", guard)
        self.assertIn("owned_scope=_owned_scope_guard(data)", guard)
        self.assertIn('details["reason"]="running_owned_single_writer_overlap"', guard)
        self.assertIn("WORKSTREAM_BLOCKED_ORDER_TRUTH_V1", guard)
        self.assertIn("refreshed_blocked_orders", guard)
        self.assertIn("AND status='blocked_infra'", guard)
        self.assertIn("AND COALESCE(blocked_reason,'')<>:reason", guard)

    def test_guard_readonly_planning_phrase_contract(self):
        guard_path = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        )
        spec = importlib.util.spec_from_file_location("scope_guard_readonly_test", guard_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for goal in (
            "Ничего не меняй на этом шаге.",
            "Ничего не переписывай, только proposePatch.",
            "Ничего не выкатывай на этом шаге.",
            "Не вноси изменений, сначала собери факты.",
            "Работай без изменения исходников.",
        ):
            self.assertTrue(module._planning_goal_is_read_only(goal), goal)
        for goal in (
            "Сначала измерь, затем исправь.",
            "Внеси изменения и запусти тесты.",
            "",
        ):
            self.assertFalse(module._planning_goal_is_read_only(goal), goal)

    def test_guard_blanket_tests_become_read_only_only_for_single_owner_product_scope(self):
        service = subprocess.run(
            ["systemctl", "cat", "boris-workstream-scope-guard.service"],
            check=True, text=True, capture_output=True,
        ).stdout
        runtime_lines = [
            line.strip() for line in service.splitlines()
            if line.strip().startswith("ExecStart=")
            and "workstream-scopes/objects/guard-" in line
        ]
        self.assertTrue(runtime_lines, service)
        guard_path = Path(runtime_lines[-1].split()[-1])
        self.assertTrue(guard_path.is_file(), guard_path)
        spec = importlib.util.spec_from_file_location("scope_guard_test_scope", guard_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry = load_registry()

        money = module._blanket_test_scope_plan(
            registry,
            ["backend/app/api/cpx_advisor.py", "backend/tests"],
        )
        self.assertEqual(money["owner"], "avito_money_core")
        self.assertEqual(money["write_paths"], ["app/api/cpx_advisor.py"])
        self.assertEqual(money["read_only_test_paths"], ["tests"])

        integration = module._blanket_test_scope_plan(
            registry,
            ["backend/app/api/avito.py", "backend/tests"],
        )
        self.assertEqual(integration["owner"], "avito_integration_core")
        self.assertEqual(integration["write_paths"], ["app/api/avito.py"])

        self.assertIsNone(module._blanket_test_scope_plan(
            registry,
            ["backend/app/ext_api", "backend/tests"],
        ))
        self.assertIsNone(module._blanket_test_scope_plan(
            registry,
            ["backend/tests"],
        ))

    def test_guard_source_drift_is_backed_up_and_self_healed(self):
        guard_path = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        )
        spec = importlib.util.spec_from_file_location("scope_guard_drift_test", guard_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "objects"
            runtime_dir.mkdir(parents=True)
            runtime = runtime_dir / "guard-runtime.py"
            source = root / "run" / "workstream_scope_guard.py"
            source.parent.mkdir(parents=True)
            backup_dir = root / "backups"

            runtime.write_bytes(b"canonical immutable guard\n")
            source.write_bytes(b"unpromoted neighbor edit\n")
            source.chmod(0o555)
            source_stat_before = source.stat()

            module.__file__ = str(runtime)
            module.CANON_DIR = runtime_dir
            module.GUARD_SOURCE = source
            module.GUARD_DRIFT_BACKUP_DIR = backup_dir

            out = module._guard_source_state(self_heal=True)

            self.assertTrue(out["immutable_runtime"], out)
            self.assertTrue(out["drift_detected"], out)
            self.assertTrue(out["self_healed"], out)
            self.assertIsNone(out["error"], out)
            self.assertEqual(source.read_bytes(), runtime.read_bytes())
            source_stat_after = source.stat()
            self.assertEqual(source_stat_after.st_mode & 0o7777, 0o555)
            self.assertEqual(source_stat_after.st_uid, source_stat_before.st_uid)
            self.assertEqual(source_stat_after.st_gid, source_stat_before.st_gid)
            backup = Path(out["backup_path"])
            self.assertTrue(backup.is_file(), out)
            self.assertEqual(backup.read_bytes(), b"unpromoted neighbor edit\n")

    def test_timer_self_protection_restores_enabled_and_active(self):
        guard_path = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        )
        spec = importlib.util.spec_from_file_location("scope_guard_timer_test", guard_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        state = {"active": False, "enabled": False}

        def fake_systemctl(command):
            action = command[1]
            if action == "is-active":
                return state["active"], "active" if state["active"] else "inactive"
            if action == "is-enabled":
                return state["enabled"], "enabled" if state["enabled"] else "disabled"
            if action == "enable":
                state["enabled"] = True
                return True, ""
            if action == "start":
                state["active"] = True
                return True, ""
            raise AssertionError(command)

        module._systemctl_flag = fake_systemctl
        out = module._timer_self_protection()

        self.assertTrue(out["ok"], out)
        self.assertTrue(out["self_healed"], out)
        self.assertTrue(out["active_after"], out)
        self.assertTrue(out["enabled_after"], out)
        self.assertEqual(out["actions"], ["enable", "start"], out)

    def test_scope_timer_refuses_manual_stop(self):
        timer = Path("/etc/systemd/system/boris-workstream-scope-guard.timer")
        self.assertTrue(timer.is_file(), timer)
        unit = timer.read_text(encoding="utf-8")
        self.assertIn("RefuseManualStop=yes", unit)
        self.assertIn("OnCalendar=*-*-* *:0/5:00", unit)
        self.assertNotIn("OnUnitActiveSec=", unit)

    def test_owned_scope_conflicts_serialize_same_canonical_writer(self):
        jobs = [
            {
                "id": 21,
                "title": "email queue writer A",
                "status": "waiting",
                "paths": ["backend/app/services/email_queue.py"],
            },
            {
                "id": 22,
                "title": "email queue writer B",
                "status": "waiting",
                "paths": ["backend/app/services/email_queue.py"],
            },
            {
                "id": 23,
                "title": "tracking writer",
                "status": "waiting",
                "paths": ["backend/app/services/email_tracking.py"],
            },
            {
                "id": 24,
                "title": "different email runtime file",
                "status": "waiting",
                "paths": ["backend/app/services/email_service.py"],
            },
        ]
        out = owned_scope_conflicts(jobs)
        self.assertEqual(out["pair_count"], 1, out)
        self.assertEqual(out["conflicted_jobs"], {21: [22], 22: [21]}, out)
        self.assertEqual(out["owners_by_job"][21], "email_outreach_core")
        self.assertEqual(out["owners_by_job"][22], "email_outreach_core")
        self.assertEqual(out["owners_by_job"][23], "email_tracking_analytics")

    def test_unowned_scope_conflicts_do_not_mix_canonical_email_owners(self):
        jobs = [
            {
                "id": 10,
                "title": "email runtime",
                "status": "waiting",
                "paths": ["backend/app/services/email_queue.py"],
            },
            {
                "id": 11,
                "title": "tracking",
                "status": "waiting",
                "paths": ["backend/app/services/email_tracking.py"],
            },
        ]
        out = unowned_scope_conflicts(jobs)
        self.assertEqual(out["candidate_count"], 0, out)
        self.assertEqual(out["pair_count"], 0, out)

    def test_scope_claim_overlap_ignores_logical_locks(self):
        self.assertTrue(
            scope_claims_overlap("app/ext_api", "app/ext_api/a2a.py")
        )
        self.assertTrue(
            scope_claims_overlap(
                "app/services/category_resolver.py",
                "app/services/category_resolver.py",
            )
        )
        self.assertFalse(
            scope_claims_overlap("proxy", "proxy")
        )
        self.assertFalse(
            scope_claims_overlap(
                "coordination:production_ai_restore",
                "coordination:production_ai_restore",
            )
        )

    def test_neighboring_email_chats_have_one_canonical_owner(self):
        health = neighbor_chat_map_health()
        self.assertTrue(health["ok"], health)
        expected = {
            "Текущий чат — пересечения задач": "workstream_coordination",
            "Исправление темы рассылки": "email_copy_review",
            "Исправление текста письма": "email_copy_review",
            "PROSPECTING — Поиск клиентов": "prospecting_discovery_core",
            "EMAIL_OUTREACH — Почтовые касания": "email_outreach_core",
            "Статистика открытий/ответов email": "email_tracking_analytics",
            "Себестоимость 5–10 клиентов": "email_outreach_economics",
            "Дашборд BORIS AI для рассылок / промо рассылок": "frontend_mobile_ui",
            "BORIS Production Watch / Production Buildout": "production_watch_global",
        }
        for chat, owner in expected.items():
            self.assertEqual(health["mapping"].get(chat), owner, chat)

    def test_social_neighbor_chats_have_distinct_non_overlapping_modes(self):
        health = neighbor_chat_map_health()
        self.assertTrue(health["ok"], health)
        mapping = health["mapping"]
        self.assertEqual(mapping.get("Автопостинг BORIS"), "social_reliability_core")
        self.assertEqual(
            mapping.get("Обновление автопостинга BORIS"),
            "social_legacy_autopost_chat",
        )
        self.assertEqual(
            mapping.get("Публикации без видео"),
            "social_editorial_client_config",
        )
        self.assertEqual(
            mapping.get("Проверка VK Океан"),
            "social_editorial_client_config",
        )
        registry = load_registry()
        rows = registry["workstreams"]["workstream_coordination"]["neighbor_chat_map"]
        by_chat = {row["chat"]: row for row in rows}
        self.assertEqual(by_chat["Автопостинг BORIS"]["mode"], "social_runtime_only")
        self.assertEqual(
            by_chat["Обновление автопостинга BORIS"]["mode"],
            "read_only_alias",
        )
        self.assertEqual(
            by_chat["Публикации без видео"]["mode"],
            "client_editorial_only",
        )
        legacy = registry["workstreams"]["social_legacy_autopost_chat"]
        self.assertEqual(legacy.get("writable_paths"), [])
        self.assertEqual(legacy.get("status"), "read_only_alias")

    def test_neighbor_chat_map_rejects_duplicate_assignment(self):
        data = deepcopy(load_registry())
        rows = data["workstreams"]["workstream_coordination"]["neighbor_chat_map"]
        rows.append(dict(rows[0]))
        health = neighbor_chat_map_health(data)
        self.assertFalse(health["ok"], health)
        self.assertTrue(
            any(x.get("reason") == "chat_mapped_more_than_once"
                for x in health["violations"]),
            health,
        )

    def test_neighbor_chat_map_rejects_owner_mode_mismatch(self):
        data = deepcopy(load_registry())
        rows = data["workstreams"]["workstream_coordination"]["neighbor_chat_map"]
        target = next(x for x in rows if x["chat"] == "Себестоимость 5–10 клиентов")
        target["mode"] = "runtime_only"
        health = neighbor_chat_map_health(data)
        self.assertFalse(health["ok"], health)
        self.assertTrue(
            any(
                x.get("reason") == "owner_mode_mismatch"
                and x.get("chat") == "Себестоимость 5–10 клиентов"
                and x.get("expected_mode") == "economics_only"
                for x in health["violations"]
            ),
            health,
        )

    def test_registry_required_writer_contract_is_authoritative(self):
        data = deepcopy(load_registry())
        data["workstreams"]["workstream_coordination"]["required_single_writers"] = {
            "app/services/email_queue.py": "email_outreach_core",
        }
        health = single_writer_scope_health(data)
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["required_count"], 1, health)

    def test_registry_chat_mode_contract_is_authoritative(self):
        data = deepcopy(load_registry())
        coordination = data["workstreams"]["workstream_coordination"]
        coordination["neighbor_chat_owner_modes"] = {
            "email_outreach_economics": "runtime_only",
        }
        rows = coordination["neighbor_chat_map"]
        target = next(x for x in rows if x["chat"] == "Себестоимость 5–10 клиентов")
        target["mode"] = "runtime_only"
        health = neighbor_chat_map_health(data)
        self.assertTrue(health["ok"], health)

    def test_avito_auxiliary_files_have_explicit_owners(self):
        expected = {
            "daily_stats_collector.py": "avito_integration_core",
            "portfolio_bootstrap_runner.py": "avito_marketing_core",
            "app/services/initial_portfolio.py": "avito_marketing_core",
            "app/services/marketing_signal_guard.py": "avito_money_core",
            "app/services/marketing_clock.py": "avito_money_core",
        }
        for path, owner in expected.items():
            self.assertEqual(writable_owners(path), [owner], path)

    def test_control_plane_core_has_one_backend_writer(self):
        expected = {
            "app/api/control_plane.py": "control_plane_core",
            "app/services/control_plane.py": "control_plane_core",
            "app/services/control_plane_adapters.py": "control_plane_core",
            "app/services/control_plane_adapters_ext.py": "control_plane_core",
            "app/services/client_supervisor.py": "control_plane_core",
            "app/services/brain_world_state.py": "control_plane_core",
            "app/services/brain_acceptance.py": "control_plane_core",
            "app/models/control_plane.py": "control_plane_core",
            "app/ext_api/workspace_sync.py": "control_plane_core",
        }
        for path, owner in expected.items():
            self.assertEqual(writable_owners(path), [owner], path)
        data = load_registry()["workstreams"]
        for surface in (
            "brain_control_plane_surface",
            "clients_control_surface",
            "development_control_surface",
        ):
            self.assertEqual(data[surface].get("writable_paths"), [], surface)
            self.assertIn(
                "control_plane_core",
                data[surface].get("delegates_edits_to") or [],
                surface,
            )

    def test_exact_execution_paths_have_canonical_owners(self):
        expected = {
            "app/telegram_bot.py": "telegram_command_core",
            "app/services/category_resolver.py": "avito_category_pipeline",
            "app/services/feed_param_normalizer.py": "avito_category_pipeline",
            "app/api/client_launch_control.py": "client_launch_core",
            "app/services/onboarding_factory_sync.py": "client_launch_core",
            "app/config.py": "client_config_core",
            "app/api/admin_clients.py": "client_config_core",
            "app/api/inbox_daily.py": "sales_conversation_pipeline",
            "daily_inbox_report.py": "sales_conversation_pipeline",
            "app/api/messenger.py": "sales_conversation_pipeline",
            "app/models/messenger_message.py": "sales_conversation_pipeline",
            "app/mop_core.py": "sales_conversation_pipeline",
            "app/api/mop_training.py": "sales_conversation_pipeline",
            "app/api/mop_combat_training.py": "sales_conversation_pipeline",
            "rop_auto.py": "sales_conversation_pipeline",
            "rop_digest.py": "sales_conversation_pipeline",
            "rop_batch.py": "sales_conversation_pipeline",
            "app/services/jobs.py": "content_engine_core",
            "app/services/campaign_service.py": "content_engine_core",
            "app/services/crm_service.py": "crm_integration_core",
            "app/api/crm.py": "crm_integration_core",
            "app/services/command_flow.py": "command_center_core",
            "app/services/command_executor.py": "command_center_core",
            "app/services/command_policy.py": "command_center_core",
            "app/services/command_audit.py": "command_center_core",
            "app/services/command_store.py": "command_center_core",
            "app/api/calltracking.py": "telephony_core",
            "app/services/ai_cost_alerts.py": "social_cost_accounting",
            "app/services/ai_guard_audit.py": "social_cost_accounting",
            "dev/v8_autonomous_canary.txt": "autonomous_dev_execution",
            "app/ext_api/aiprov.py": "autonomous_dev_execution",
            "app/ext_api/ddl.sql": "autonomous_dev_execution",
            "app/ext_api/mcp_server.py": "autonomous_dev_execution",
            "app/ext_api/router.py": "autonomous_dev_execution",
            "app/ext_api/schema.py": "autonomous_dev_execution",
            "app/ext_api/selftest.py": "autonomous_dev_execution",
        }
        for path, owner in expected.items():
            self.assertEqual(writable_owners(path), [owner], path)

    def test_copy_review_and_global_watch_are_non_writers(self):
        data = load_registry()["workstreams"]
        self.assertEqual(data["email_copy_review"].get("writable_paths"), [])
        self.assertEqual(data["production_watch_global"].get("writable_paths"), [])
        with self.assertRaises(ScopeError):
            assert_scope_paths(
                "email_copy_review",
                ["backend/app/services/prospect_campaigns.py"],
            )

    def test_order_scope_guard_wires_parent_order_reconciliation(self):
        guard_path = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        )
        guard = guard_path.read_text(encoding="utf-8")
        self.assertIn(
            'ORDER_SCOPE_BLOCK_PREFIX="WORKSTREAM_ORDER_SCOPE_STALE"',
            guard,
        )
        self.assertIn("def _reconcile_order_scopes(data:dict)->dict:", guard)
        self.assertIn("order_scope=_reconcile_order_scopes(data)", guard)
        self.assertIn("repairable_missing_write=bool(", guard)
        self.assertIn('parent_class.get("unowned_paths")', guard)
        self.assertIn('"scope_mode"]="canonical_writer"', guard)
        self.assertIn(
            'details["reason"]="running_stale_a2a_order_scope"',
            guard,
        )

    def test_order_scope_helper_rejects_broader_or_foreign_child_claim(self):
        guard_path = (
            Path(__file__).resolve().parents[1]
            / "run"
            / "workstream_scope_guard.py"
        )
        spec = importlib.util.spec_from_file_location(
            "scope_guard_order_scope_test",
            guard_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertTrue(module._path_within_parent_claim(
            "app/api/calltracking.py",
            ["app/api"],
        ))
        self.assertFalse(module._path_within_parent_claim(
            "app/api",
            ["app/api/calltracking.py"],
        ))
        self.assertTrue(module._path_within_parent_claim(
            "app/services/jobs.py",
            ["app/services/**"],
        ))
        self.assertFalse(module._path_within_parent_claim(
            "app/services/media_service.py",
            [
                "app/services/jobs.py",
                "app/services/campaign_service.py",
            ],
        ))
        goal = module._canonical_planning_goal(
            "DEV_CALLS_REPORT_FIX",
            "Проверить реальные звонки и рекомендации",
        )
        self.assertIn("DEV_CALLS_REPORT_FIX", goal)
        self.assertIn("Проверить реальные звонки и рекомендации", goal)
        self.assertIn("Ничего не переписывай", goal)

    def test_coordination_audit_explains_practical_business_value(self):
        audit_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "audit_workstreams.py"
        )
        spec = importlib.util.spec_from_file_location(
            "coordination_business_value_test",
            audit_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module._practical_impact(
            writer_health={
                "ok": True,
                "required_count": 10,
                "wrong_or_missing_single_writers": [],
                "overlaps": [],
            },
            chat_health={"ok": True, "count": 4, "violations": []},
            lock_health={
                "state": "ok",
                "active_jobs": 5,
                "running_conflicts": [],
                "potential_overlap_pairs": 0,
            },
            order_health={
                "ok": True,
                "executable_count": 3,
                "read_only_count": 2,
                "writable_count": 1,
                "writable_overlap_count": 0,
                "stale_scope_blocked_count": 0,
            },
            timer_health={
                "ok": True,
                "active": True,
                "enabled": True,
                "fixed_calendar": True,
            },
            failure_escalation={
                "ok": True,
                "notify_ready": True,
                "handler_wired": True,
            },
            required_path_truth={
                "required_count": 10,
                "existing_count": 9,
                "missing_count": 1,
            },
            throughput_health={
                "ok": True,
                "active_jobs": 5,
                "running_jobs": 1,
                "provider_wait_jobs": 0,
                "free_provider_available": True,
                "free_provider_states": {"gemini_cli": "AVAILABLE"},
                "human_only_blocker": False,
                "client_effect": "Очередь разработки движется.",
            },
        )
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["problem_count"], 0)
        self.assertEqual(len(out["actions"]), 8)
        self.assertIn("сам не создаёт лиды", out["direct_business_result"])
        self.assertTrue(all(x.get("client") for x in out["actions"]))
        self.assertTrue(all(x.get("boris") for x in out["actions"]))

    def test_coordination_audit_reports_internal_review_blocker_without_owner_action(self):
        audit_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "audit_workstreams.py"
        )
        spec = importlib.util.spec_from_file_location(
            "coordination_internal_blocker_test",
            audit_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        effect = (
            "Разработка движется, но 4 наряда заблокированы неполным "
            "контекстом чтения. Это внутренняя автопочинка BORIS."
        )
        out = module._practical_impact(
            writer_health={
                "ok": True,
                "required_count": 10,
                "wrong_or_missing_single_writers": [],
                "overlaps": [],
            },
            chat_health={"ok": True, "count": 4, "violations": []},
            lock_health={
                "state": "ok",
                "active_jobs": 5,
                "running_conflicts": [],
                "potential_overlap_pairs": 0,
            },
            order_health={
                "ok": True,
                "executable_count": 3,
                "read_only_count": 2,
                "writable_count": 1,
                "writable_overlap_count": 0,
                "stale_scope_blocked_count": 0,
            },
            timer_health={
                "ok": True,
                "active": True,
                "enabled": True,
                "fixed_calendar": True,
            },
            failure_escalation={
                "ok": True,
                "notify_ready": True,
                "handler_wired": True,
            },
            required_path_truth={
                "required_count": 10,
                "existing_count": 10,
                "missing_count": 0,
            },
            throughput_health={
                "ok": False,
                "active_jobs": 12,
                "running_jobs": 3,
                "review_orders": 9,
                "queued_orders": 5,
                "returned_orders": 0,
                "workspace_review_blocker_count_15m": 4,
                "workspace_review_blocker_examples": [
                    {"order_id": 43400},
                    {"order_id": 43510},
                    {"order_id": 45022},
                    {"order_id": 53240},
                ],
                "dev_running_rows": 3,
                "actionable_state_mismatch": 0,
                "provider_wait_jobs": 0,
                "free_provider_available": True,
                "free_provider_states": {"gemini_cli": "AVAILABLE"},
                "human_only_blocker": False,
                "client_effect": effect,
                "owner_action": None,
            },
        )
        self.assertEqual(out["state"], "attention")
        self.assertEqual(out["direct_business_result"], effect)
        self.assertFalse(out["owner_action_required"])
        evidence = out["actions"][0]["evidence"]
        self.assertEqual(evidence["workspace_review_blocker_count_15m"], 4)
        self.assertEqual(
            evidence["workspace_review_blocker_order_ids"],
            [43400, 43510, 45022, 53240],
        )

    def test_provider_watch_does_not_reprobe_terminal_or_pressured_state(self):
        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertFalse(module._provider_probe_due({
            "provider_blocked": True,
            "under_pressure": True,
            "active_jobs": 1,
            "gemini_reprobe": {"due": True},
            "providers": {
                "gemini_cli": {
                    "state": "TEMP_ERROR",
                    "retry_at": None,
                },
            },
        }))
        self.assertFalse(module._provider_probe_due({
            "provider_blocked": True,
            "under_pressure": False,
            "active_jobs": 1,
            "providers": {
                "gemini_cli": {
                    "state": "AUTH_ERROR",
                    "retry_at": "2099-01-01T00:00:00+00:00",
                },
            },
        }))
        # Generic transient recovery still needs active development work.
        self.assertFalse(module._provider_probe_due({
            "provider_blocked": True,
            "under_pressure": False,
            "active_jobs": 0,
            "gemini_reprobe": {
                "due": True,
                "source": "recover.reprobe_free_development",
            },
            "providers": {
                "gemini_cli": {
                    "state": "TEMP_ERROR",
                    "retry_at": None,
                },
            },
        }))
        # GEMINI_DAILY_RESET_PROACTIVE_PROBE_V1: after a proven daily reset,
        # sales/MOP/ROP recovery is allowed even without development jobs.
        self.assertTrue(module._provider_probe_due({
            "provider_blocked": True,
            "under_pressure": False,
            "active_jobs": 0,
            "gemini_reprobe": {
                "due": True,
                "source": "daily_model_quota_reset",
            },
            "providers": {
                "gemini_cli": {
                    "state": "RATE_LIMITED",
                    "retry_at": None,
                },
            },
        }))
        self.assertTrue(module._provider_probe_due({
            "provider_blocked": True,
            "under_pressure": False,
            "active_jobs": 1,
            "gemini_reprobe": {"due": True},
            "providers": {
                "gemini_cli": {
                    "state": "TEMP_ERROR",
                    "retry_at": None,
                },
            },
        }))

    def test_provider_watch_reprobe_uses_canonical_daemon_environment(self):
        from unittest.mock import patch
        import os

        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_env_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as td:
            root_home = Path(td) / "root"
            (root_home / ".gemini").mkdir(parents=True)
            (root_home / ".gemini" / "settings.json").write_text(
                '{"security":{"auth":{"selectedType":"oauth-personal"}}}\n',
                encoding="utf-8",
            )
            proxy_env = Path(td) / "proxy.env"
            proxy_env.write_text(
                "HTTPS_PROXY=http://127.0.0.1:9999\n"
                "https_proxy=http://127.0.0.1:9999\n",
                encoding="utf-8",
            )

            previous_home = os.environ.get("HOME")
            previous_path = os.environ.get("PATH")
            previous_https = os.environ.get("HTTPS_PROXY")
            captured = {}

            def fake_probe(min_interval_min):
                captured.update({
                    "interval": min_interval_min,
                    "home": os.environ.get("HOME"),
                    "path": os.environ.get("PATH"),
                    "trust": os.environ.get("GEMINI_CLI_TRUST_WORKSPACE"),
                    "fallback": os.environ.get("BORIS_GEMINI_FALLBACK_MODEL"),
                    "proxy": os.environ.get("HTTPS_PROXY"),
                })
                return {"provider": "gemini_cli", "status": "AVAILABLE"}

            with (
                patch.object(module, "CANONICAL_CLI_HOME", str(root_home)),
                patch.object(module, "CANONICAL_CLI_PROXY_ENV", proxy_env),
                patch.object(module.os, "geteuid", return_value=0),
                patch.object(
                    module.aiprov,
                    "reprobe_free_development",
                    side_effect=fake_probe,
                ),
            ):
                out = module._reprobe_with_canonical_cli_env(5)

            self.assertEqual(out["status"], "AVAILABLE")
            self.assertEqual(captured["interval"], 5)
            self.assertEqual(captured["home"], str(root_home))
            self.assertTrue(
                captured["path"].split(":")[0] == module.CANONICAL_CLI_BIN
                or module.CANONICAL_CLI_BIN in captured["path"].split(":")
            )
            self.assertEqual(captured["trust"], "true")
            self.assertEqual(
                captured["fallback"],
                module.CANONICAL_GEMINI_FALLBACK_MODEL,
            )
            self.assertEqual(
                module.CANONICAL_GEMINI_FALLBACK_MODEL,
                "gemini-2.5-flash",
            )
            self.assertEqual(captured["proxy"], "http://127.0.0.1:9999")
            self.assertEqual(os.environ.get("HOME"), previous_home)
            self.assertEqual(os.environ.get("PATH"), previous_path)
            self.assertEqual(os.environ.get("HTTPS_PROXY"), previous_https)

    def test_provider_watch_non_root_probe_fails_closed_before_shared_state_write(self):
        from unittest.mock import patch

        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_nonroot_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with (
            patch.object(module.os, "geteuid", return_value=1000),
            patch.object(module.aiprov, "reprobe_free_development") as probe,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "canonical_cli_probe_requires_root",
            ):
                module._reprobe_with_canonical_cli_env(5)
        probe.assert_not_called()

    def test_provider_watch_historical_loadavg_does_not_delay_safe_recovery(self):
        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_historical_load_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        facts = {
            "under_pressure": True,
            "historical_load_only": True,
            "active_jobs": 1,
            "development_candidates": ["gemini_cli"],
            "executor_capacity": 1,
            "review_capacity": 1,
            "providers": {"gemini_cli": {"state": "RATE_LIMITED"}},
            "gemini_reprobe": {"due": True},
        }
        self.assertTrue(module._provider_probe_due(facts))
        self.assertTrue(
            module._provider_recovery_gate(
                facts,
                {
                    "ok": True,
                    "running_conflicts": [],
                    "potential_overlap_pairs": 0,
                },
                {"block_new_writes": False},
            )["allowed"]
        )
        self.assertTrue(module._review_provider_recovery_gate(facts)["allowed"])

        real_pressure = dict(facts)
        real_pressure["historical_load_only"] = False
        self.assertFalse(module._provider_probe_due(real_pressure))
        self.assertIn(
            "server_under_pressure",
            module._provider_recovery_gate(
                real_pressure,
                {
                    "ok": True,
                    "running_conflicts": [],
                    "potential_overlap_pairs": 0,
                },
                {"block_new_writes": False},
            )["reasons"],
        )

    def test_provider_watch_effective_retry_respects_hard_cooldown(self):
        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_retry_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            module._effective_retry_at(
                "2026-09-07T12:59:59+00:00",
                "2026-09-08T00:10:14+00:00",
            ),
            "2026-09-08T00:10:14+00:00",
        )
        self.assertEqual(
            module._effective_retry_at(
                "2026-09-08T00:30:00+00:00",
                "2026-09-08T00:10:14+00:00",
            ),
            "2026-09-08T00:30:00+00:00",
        )
        self.assertIsNone(module._effective_retry_at(None, None))

    def test_provider_watch_reprobe_respects_provider_retry_fence(self):
        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_probe_fence_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        now = module.datetime(2026, 9, 7, 14, 0, tzinfo=module.timezone.utc)
        out = module._gemini_reprobe_plan(
            {
                "gemini_cli": {
                    "state": "RATE_LIMITED",
                    "updated_at": "2026-09-07T12:29:59+00:00",
                    "retry_at": "2026-09-08T00:10:14+00:00",
                },
            },
            now=now,
        )
        self.assertFalse(out["due"])
        self.assertEqual(out["source"], "provider_retry_fence")
        self.assertEqual(out["next_probe_at"], "2026-09-08T00:10:14+00:00")

        daily = module._probe_cooldown_alignment_plan(
            {
                "providers": {
                    "gemini_cli": {
                        "state": "RATE_LIMITED",
                        "retry_at": "2026-09-08T00:10:14+00:00",
                        "note": (
                            "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED "
                            "reset_epoch=1788826214"
                        ),
                    },
                },
                "gemini_reprobe": out,
                "development_candidates": [],
                "under_pressure": False,
            },
            now=now,
        )
        self.assertFalse(daily["align"])
        self.assertEqual(daily["target_retry_at"], "2026-09-08T00:10:14+00:00")

        reconcile = module._daily_quota_retry_reconcile_plan(
            {
                "state": "RATE_LIMITED",
                "retry_at": "2026-09-08T00:40:14+00:00",
                "note": (
                    "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED "
                    "reset_epoch=1788826200"
                ),
            },
            now=now,
        )
        self.assertTrue(reconcile["drift"])
        self.assertEqual(
            reconcile["target_retry_at"],
            "2026-09-08T00:10:15+00:00",
        )

        transient = module._probe_cooldown_alignment_plan(
            {
                "providers": {
                    "gemini_cli": {
                        "state": "TEMP_ERROR",
                        "retry_at": "2026-09-07T14:05:00+00:00",
                        "note": "temporary transport error",
                    },
                },
                "gemini_reprobe": {
                    "due": False,
                    "next_probe_at": "2026-09-07T14:10:00+00:00",
                    "source": "recover.reprobe_free_development",
                },
                "development_candidates": [],
                "under_pressure": False,
            },
            now=now,
        )
        self.assertTrue(transient["align"])
        self.assertEqual(transient["target_retry_at"], "2026-09-07T14:40:00+00:00")

    def test_provider_watch_dedup_ignores_queue_and_load_churn(self):
        watch_path = (
            Path(__file__).resolve().parents[1]
            / ".boris_ops"
            / "chat_coordination"
            / "development_provider_watch.py"
        )
        spec = importlib.util.spec_from_file_location(
            "development_provider_watch_dedup_test",
            watch_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        base = {
            "blocked": True,
            "human_only": False,
            "provider_wait_jobs": 25,
            "free_provider_states": {
                "gemini_cli": "UNAVAILABLE_BILLING",
                "claude_code": "AUTH_ERROR",
            },
            "development_candidates": [],
            "paid_development_allowed": False,
            "provider_blocked": True,
            "capacity_blocked": False,
            "executor_capacity": 0,
            "under_pressure": False,
            "load1": 5.2,
            "next_auto_retry_at": "2026-09-06T00:02:03",
        }
        changed = dict(base)
        changed.update({
            "provider_wait_jobs": 31,
            "executor_capacity": 2,
            "under_pressure": True,
            "load1": 9.8,
        })
        self.assertEqual(
            module._fingerprint(base),
            module._fingerprint(changed),
        )
        msg = module._message({
            **base,
            "active_jobs": 43,
            "running_jobs": 0,
        })
        self.assertIn("Владелец сейчас НЕ нужен", msg)
        self.assertIn("health-probe запланирован на", msg)

    def test_single_writer_health_is_green(self):
        out = single_writer_scope_health()
        self.assertTrue(out["ok"], out)

    def test_health_is_green(self):
        self.assertTrue(social_reliability_scope_health()["ok"])


if __name__ == "__main__":
    unittest.main()

class WorkstreamAuthorityContractSourceTest(unittest.TestCase):
    def test_scope_guard_validates_authority_contract_reference(self):
        guard=(Path(__file__).resolve().parents[1]/"run"/"workstream_scope_guard.py").read_text(encoding="utf-8")
        self.assertIn("def _authority_contract_violations(data:dict)->list[dict]:",guard)
        self.assertIn('"authority_contract_violations":authority_contract_violations',guard)
        self.assertIn('not authority_contract_violations',guard)


class SingleWriterGuardianRuntimeContractTest(unittest.TestCase):
    def test_waiting_overlap_is_serialized_not_invariant_failure(self):
        src=(Path(__file__).resolve().parents[1]/"control_plane_guardian_runner.py").read_text(encoding="utf-8")
        self.assertIn("OWNER_SINGLE_WRITER_RUNTIME_VERIFY_V2",src)
        block=src.split("OWNER_SINGLE_WRITER_RUNTIME_VERIFY_V2",1)[1].split("elif key.startswith",1)[0]
        self.assertIn('not _running_conflicts',block)
        self.assertNotIn('and _potential_pairs==0',block)
        self.assertIn('scheduler serializes them',block)
