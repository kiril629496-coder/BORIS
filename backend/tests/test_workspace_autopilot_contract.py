import datetime
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from app.api import control_plane
from app.ext_api import lead, workspace_sync


class WorkspaceAutopilotContractTests(unittest.TestCase):
    def test_workspace_progress_status_never_calls_waiting_or_queued_running(self):
        self.assertEqual(
            control_plane._workspace_progress_status(
                "waiting",
                autopilot_enabled=True,
                wait_reason="WAITING_FREE_CODING_PROVIDER",
            ),
            "waiting",
        )
        self.assertEqual(
            control_plane._workspace_progress_status(
                "queued",
                autopilot_enabled=True,
            ),
            "waiting",
        )
        self.assertEqual(
            control_plane._workspace_progress_status(
                "review_requested",
                autopilot_enabled=True,
            ),
            "review",
        )
        self.assertEqual(
            control_plane._workspace_progress_status(
                "running",
                autopilot_enabled=True,
            ),
            "running",
        )
        self.assertEqual(
            control_plane._workspace_progress_status(
                "completed",
                autopilot_enabled=True,
            ),
            "verified_done",
        )
        self.assertEqual(
            workspace_sync._workspace_progress_status(
                "waiting",
                autopilot_enabled=True,
                wait_reason="WAITING_FREE_CODING_PROVIDER",
                order_status="queued",
            ),
            "waiting",
        )
        self.assertEqual(
            workspace_sync._workspace_progress_status(
                "review",
                autopilot_enabled=True,
                order_status="review_requested",
            ),
            "review",
        )
        self.assertEqual(
            control_plane._workspace_progress_status(
                "waiting",
                autopilot_enabled=True,
                blocker="provider_unavailable: review provider quota/cooling",
            ),
            "waiting",
        )
        self.assertEqual(
            workspace_sync._workspace_progress_status(
                "waiting",
                autopilot_enabled=True,
                blocked_reason="provider_unavailable: review provider quota/cooling",
                order_status="blocked_infra",
            ),
            "waiting",
        )
        # Live A2A execution is stronger truth than stale parent/provider wait.
        self.assertEqual(
            control_plane._workspace_progress_status(
                "waiting",
                autopilot_enabled=True,
                blocker="",
                wait_reason="WAITING_FREE_CODING_PROVIDER",
                order_status="running",
            ),
            "running",
        )
        self.assertEqual(
            workspace_sync._workspace_progress_status(
                "waiting",
                autopilot_enabled=True,
                wait_reason="WAITING_FREE_CODING_PROVIDER",
                order_status="running",
            ),
            "running",
        )

    def test_workspace_commands_enable_until_done_autopilot(self):
        src = inspect.getsource(control_plane._workspace_enqueue_executor)
        self.assertIn('"workspace_autopilot":True', src)
        self.assertIn('"workspace_autopilot_mode":"until_done"', src)
        self.assertIn('"autonomous_dev":True', src)
        self.assertIn('"server_owned":True', src)
        self.assertIn('scope_paths=["backend/app","backend/tests","frontend"]', src)
        self.assertIn('reviewer="claude-lead"', src)

    def test_pause_blocks_only_next_autonomous_order(self):
        self.assertIsNone(
            lead._next_order(
                {"scope": {"workspace_autopilot": False}},
                {"order_id": 1},
            )
        )

    def test_autopilot_endpoint_persists_scope_and_resumes(self):
        src = inspect.getsource(control_plane.workspace_thread_autopilot)
        self.assertIn('scope["workspace_autopilot"]=enabled', src)
        self.assertIn("resume_autopilot_job", src)
        self.assertIn('"autopilot_paused"', src)
        self.assertIn("autostart-", src)
        self.assertIn("_workspace_enqueue_executor", src)
        self.assertIn('"autopilot_start_failed"', src)

    def test_workspace_runtime_state_waits_when_free_provider_is_unavailable(self):
        self.assertEqual(
            workspace_sync._workspace_autopilot_runtime_state(
                "review",
                autopilot_enabled=True,
                provider_wait_reason="WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically",
            ),
            "waiting",
        )

    def test_provider_retry_time_uses_earliest_free_provider_and_utc(self):
        old_watch=workspace_sync._PROVIDER_WATCH_STATE
        with tempfile.TemporaryDirectory() as td:
            workspace_sync._PROVIDER_WATCH_STATE=Path(td)/"missing-watch-state.json"
            try:
                now=datetime.datetime.now(datetime.timezone.utc)
                earliest=now+datetime.timedelta(minutes=10)
                later=now+datetime.timedelta(hours=2)
                retry=workspace_sync._workspace_provider_retry_at({
                    "gemini_cli":{"retry_at":earliest},
                    "claude_code":{"retry_at":later},
                })
            finally:
                workspace_sync._PROVIDER_WATCH_STATE=old_watch
        self.assertEqual(retry,earliest.isoformat())

    def test_development_ui_shows_next_automatic_provider_attempt(self):
        page = Path("../frontend/app/dashboard/development/page.tsx").read_text(encoding="utf-8")
        self.assertIn("provider_retry_at", page)
        self.assertIn("до следующей автоматической попытки", page)
        self.assertIn("Следующая попытка ИИ:", page)
        self.assertIn("nextProviderRetryThread", page)

    def test_development_ui_hides_machine_codes_from_owner(self):
        page = Path("../frontend/app/dashboard/development/page.tsx").read_text(encoding="utf-8")
        humanizer = Path("../frontend/app/lib/humanizeBusinessText.ts").read_text(encoding="utf-8")
        self.assertNotIn("существующей OPS-задачей", page)
        self.assertNotIn("существующей OPS-задачи", page)
        self.assertIn("WORKSTREAM_SCOPE_AMBIGUOUS", humanizer)
        self.assertIn("WORKSTREAM_OWNED_OVERLAP", humanizer)
        self.assertIn("ИИ временно недоступен", humanizer)
        self.assertIn("BORIS проверяет доступность автоматически", humanizer)

    def test_guided_scenarios_ownerless_contract_is_present(self):
        detail = Path("../frontend/app/dashboard/scenarios/[id]/page.tsx").read_text(encoding="utf-8")
        catalog = Path("../frontend/app/dashboard/scenarios/page.tsx").read_text(encoding="utf-8")
        guide = Path("../frontend/app/components/ScenarioGuide.tsx").read_text(encoding="utf-8")
        scenario_source = Path("app/scenarios.py").read_text(encoding="utf-8")
        home_api = Path("app/api/home.py").read_text(encoding="utf-8")

        self.assertIn("ScenarioGuide", detail)
        self.assertIn('searchParams.get("account_id")', detail)
        self.assertIn('searchParams.get("account_id")', catalog)
        self.assertIn('data-testid="scenario-recommended"', catalog)
        self.assertIn('"recommended": recommendation', home_api)
        self.assertIn("recommend_scenario", home_api)

        self.assertIn("boris_scenario_guide_seen:", guide)
        self.assertIn("Проверить статус", guide)
        self.assertIn("Сделать сейчас", guide)
        self.assertIn("Посмотреть результат", guide)
        self.assertIn("После «Понятно» эта подсказка не повторится", guide)

        self.assertIn("SOON = []", scenario_source)
        for scenario_type in (
            "new_niche", "new_city", "find_problem", "season",
            "social", "automate_sales", "website",
        ):
            self.assertIn(f'"{scenario_type}":', scenario_source)

    def test_executor_refresh_preserves_global_provider_wait_instead_of_false_working(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from app.ext_api import db as ext_db

        row=SimpleNamespace(
            id="thread-provider-wait",
            thread_type="task",
            status="active",
            current_state_json={"dev_job_id":"dev_501","autopilot_enabled":True},
            last_event_at=None,
        )
        job={
            "id":501,"status":"review","current_phase":"ACCEPTANCE",
            "assigned_agent":"gpt-executor","wait_reason":None,"blocked_reason":None,
            "phases_json":"{}","acceptance_json":"[]","scope_json":"{}",
            "qa_status":None,"deployment_status":None,"updated_at":None,
        }
        order={
            "id":9001,"status":"review_requested","assigned_to":"claude-lead",
            "claimed_by":None,"blocked_reason":None,"iteration":1,
            "max_iterations":8,"updated_at":None,
        }
        orm=MagicMock()
        with patch.object(ext_db,"one",side_effect=[job,order]), patch.object(
            control_plane,"_workspace_free_provider_wait_snapshot",
            return_value=(
                "WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically",
                "2026-09-07T00:10:15+00:00",
            ),
        ):
            out=control_plane._workspace_refresh_executor_state(orm,row,emit_event=False)

        self.assertTrue(out["changed"])
        self.assertEqual(row.status,"waiting")
        self.assertEqual(row.current_state_json["autopilot_state"],"waiting")
        self.assertEqual(row.current_state_json["progress_status"],"waiting")
        self.assertEqual(
            row.current_state_json["executor_wait_reason"],
            "WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically",
        )
        self.assertEqual(
            row.current_state_json["provider_retry_at"],
            "2026-09-07T00:10:15+00:00",
        )

    def test_executor_refresh_does_not_hide_real_blocker_behind_global_provider_wait(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from app.ext_api import db as ext_db

        row=SimpleNamespace(
            id="thread-real-blocker",thread_type="task",status="active",
            current_state_json={"dev_job_id":"dev_502","autopilot_enabled":True},
            last_event_at=None,
        )
        job={
            "id":502,"status":"review","current_phase":"ACCEPTANCE",
            "assigned_agent":"gpt-executor","wait_reason":None,
            "blocked_reason":"filesystem read-only",
            "phases_json":"{}","acceptance_json":"[]","scope_json":"{}",
            "qa_status":None,"deployment_status":None,"updated_at":None,
        }
        order={
            "id":9002,"status":"blocked_infra","assigned_to":"claude-lead",
            "claimed_by":None,"blocked_reason":"filesystem read-only",
            "iteration":1,"max_iterations":8,"updated_at":None,
        }
        orm=MagicMock()
        with patch.object(ext_db,"one",side_effect=[job,order]), patch.object(
            control_plane,"_workspace_free_provider_wait_snapshot",
            return_value=(
                "WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically",
                "2026-09-07T00:10:15+00:00",
            ),
        ):
            control_plane._workspace_refresh_executor_state(orm,row,emit_event=False)

        self.assertEqual(row.status,"blocked")
        self.assertEqual(row.current_state_json["autopilot_state"],"blocked")
        self.assertEqual(row.current_state_json["executor_blocker"],"filesystem read-only")
        self.assertIsNone(row.current_state_json["provider_retry_at"])

    def test_workspace_capacity_wait_is_truthful_and_never_interrupts_running_order(self):
        self.assertIn(
            "безопасный слот проверки",
            workspace_sync._workspace_capacity_wait_reason(
                "review",
                order_status="review_requested",
                executor_capacity=0,
                review_capacity=0,
            ),
        )
        self.assertIsNone(
            workspace_sync._workspace_capacity_wait_reason(
                "review",
                order_status="running",
                executor_capacity=0,
                review_capacity=0,
            )
        )
        self.assertIn(
            "безопасный слот исполнителя",
            workspace_sync._workspace_capacity_wait_reason(
                "running",
                order_status="queued",
                executor_capacity=0,
                review_capacity=1,
            ),
        )

    def test_workspace_runtime_state_preserves_terminal_and_pause_truth(self):
        self.assertEqual(
            workspace_sync._workspace_autopilot_runtime_state(
                "completed",
                autopilot_enabled=True,
                provider_wait_reason="WAITING_FREE_CODING_PROVIDER",
            ),
            "done",
        )
        self.assertEqual(
            workspace_sync._workspace_autopilot_runtime_state(
                "review",
                autopilot_enabled=False,
                provider_wait_reason=None,
            ),
            "paused",
        )
        self.assertEqual(
            workspace_sync._workspace_autopilot_runtime_state(
                "blocked",
                autopilot_enabled=True,
                blocked_reason="real blocker",
                provider_wait_reason=None,
            ),
            "blocked",
        )

    def test_explicit_development_orphan_is_the_only_background_autostart_candidate(self):
        from types import SimpleNamespace

        explicit = SimpleNamespace(
            id="thread-1",
            thread_type="task",
            status="active",
            account_id=None,
            title="Задача",
            summary="Сделать и проверить",
            current_state_json={"autopilot_enabled": True},
            source_json={
                "surface": "development",
                "owner_only": True,
                "autopilot": True,
            },
        )
        self.assertTrue(
            workspace_sync._explicit_development_autostart_candidate(explicit)
        )

        recovered = SimpleNamespace(
            id="thread-2",
            thread_type="task",
            status="active",
            account_id=None,
            title="История",
            summary="Старый контекст",
            current_state_json={"autopilot_enabled": True},
            source_json={
                "surface": "development",
                "source": "recovered_context",
                "owner_only": True,
            },
        )
        self.assertFalse(
            workspace_sync._explicit_development_autostart_candidate(recovered)
        )

        paused = SimpleNamespace(
            **{**explicit.__dict__,
               "id": "thread-3",
               "current_state_json": {"autopilot_enabled": False}}
        )
        self.assertFalse(
            workspace_sync._explicit_development_autostart_candidate(paused)
        )

    def test_explicit_development_orphan_self_starts_without_owner_click(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        row = SimpleNamespace(
            id="thread-4",
            thread_type="task",
            status="active",
            account_id=None,
            title="Довести экран до production",
            summary="Исправить и проверить реальный сценарий",
            current_state_json={"autopilot_enabled": True},
            source_json={
                "surface": "development",
                "owner_only": True,
                "autopilot": True,
            },
            last_event_at=None,
        )
        db = MagicMock()
        execution = {
            "dev_job_id": "dev_1200",
            "order_id": 93001,
            "executor": "gpt-executor",
            "executor_status": "waiting",
            "order_status": "queued",
        }
        with patch.object(
            control_plane, "_workspace_enqueue_executor",
            return_value=execution,
        ) as enqueue:
            out = workspace_sync._self_start_explicit_development_thread(db, row)

        self.assertTrue(out["started"])
        self.assertFalse(out["failed"])
        self.assertEqual(row.current_state_json["dev_job_id"], "dev_1200")
        self.assertEqual(
            row.current_state_json["last_command_id"],
            "background-autostart-thread-4",
        )
        self.assertTrue(row.current_state_json["autopilot_enabled"])
        enqueue.assert_called_once()
        db.add.assert_called_once()
        event = db.add.call_args.args[0]
        self.assertEqual(event.event_type, "autopilot_self_started")
        self.assertFalse(event.payload_json["owner_action_required"])

    def test_explicit_autostart_failure_enters_existing_bounded_retry_state(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        row = SimpleNamespace(
            id="thread-5",
            thread_type="task",
            status="active",
            account_id=None,
            title="Задача",
            summary="Выполнить",
            current_state_json={"autopilot_enabled": True},
            source_json={
                "surface": "development",
                "owner_only": True,
                "autopilot": True,
            },
            last_event_at=None,
        )
        db = MagicMock()
        with patch.object(
            control_plane, "_workspace_enqueue_executor",
            side_effect=RuntimeError("transient enqueue failure"),
        ):
            out = workspace_sync._self_start_explicit_development_thread(db, row)

        self.assertTrue(out["failed"])
        self.assertEqual(row.status, "waiting")
        self.assertEqual(row.current_state_json["executor_status"], "enqueue_failed")
        self.assertEqual(row.current_state_json["execution_enqueue_attempts"], 1)
        self.assertFalse(
            workspace_sync._explicit_development_autostart_candidate(row)
        )
        event = db.add.call_args.args[0]
        self.assertEqual(event.event_type, "autopilot_self_start_failed")
        self.assertFalse(event.payload_json["owner_action_required"])

    def test_background_reconcile_includes_explicit_autostart_and_counters(self):
        src = inspect.getsource(workspace_sync.reconcile_workspace_executions)
        self.assertIn("_explicit_development_autostart_candidate", src)
        self.assertIn("_self_start_explicit_development_thread", src)
        self.assertIn('"autostart_eligible"', src)
        self.assertIn('"autostarted"', src)
        self.assertIn('"autostart_failed"', src)
        self.assertIn("Do not burn all bounded enqueue retries in one dispatcher", src)

    def test_provider_wait_text_recognizes_explicit_review_quota_blockers(self):
        self.assertTrue(
            workspace_sync._provider_wait_text(
                "provider_unavailable: review provider quota/cooling"
            )
        )
        self.assertTrue(
            workspace_sync._provider_wait_text(
                "WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically"
            )
        )
        self.assertFalse(
            workspace_sync._provider_wait_text(
                "WORKSTREAM_OWNED_OVERLAP: owner=autonomous_dev_execution"
            )
        )

    def test_provider_retry_prefers_real_watcher_probe_over_later_transport_latch(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        watcher_at = now + datetime.timedelta(minutes=20)
        transport_at = now + datetime.timedelta(minutes=50)
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "development_provider_watch.json"
            state_path.write_text(
                json.dumps({"next_auto_retry_at": watcher_at.isoformat()}),
                encoding="utf-8",
            )
            from unittest.mock import patch
            with patch.object(workspace_sync, "_PROVIDER_WATCH_STATE", state_path):
                out = workspace_sync._workspace_provider_retry_at({
                    "gemini_cli": {"retry_at": transport_at.isoformat()},
                    "claude_code": {},
                })
        self.assertEqual(
            datetime.datetime.fromisoformat(out),
            watcher_at,
        )

    def test_provider_retry_uses_gemini_daily_reset_marker_without_watch_file(self):
        reset_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
        reset_epoch = int(reset_at.timestamp())
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.json"
            from unittest.mock import patch
            with patch.object(workspace_sync, "_PROVIDER_WATCH_STATE", missing):
                out = workspace_sync._workspace_provider_retry_at({
                    "gemini_cli": {
                        "retry_at": (reset_at + datetime.timedelta(minutes=30)).isoformat(),
                        "note": (
                            "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
                            f"reset_epoch={reset_epoch} model=gemini-2.5-flash"
                        ),
                    },
                    "claude_code": {},
                })
        got = datetime.datetime.fromisoformat(out)
        self.assertEqual(got, datetime.datetime.fromtimestamp(
            reset_epoch, datetime.timezone.utc
        ) + datetime.timedelta(seconds=15))

    def test_provider_retry_falls_back_to_provider_state_when_watcher_unavailable(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        transport_at = now + datetime.timedelta(minutes=50)
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.json"
            from unittest.mock import patch
            with patch.object(workspace_sync, "_PROVIDER_WATCH_STATE", missing):
                out = workspace_sync._workspace_provider_retry_at({
                    "gemini_cli": {"retry_at": transport_at.isoformat()},
                    "claude_code": {},
                })
        self.assertEqual(
            datetime.datetime.fromisoformat(out),
            transport_at,
        )

    def test_ops_workspace_mirror_exposes_exact_owner_action_truth(self):
        src = inspect.getsource(workspace_sync.sync_ops_jobs)
        self.assertIn("o.blocked_reason AS order_blocked_reason", src)
        self.assertIn("'order_status':order_status", src)
        self.assertIn(
            "'owner_action_required':order_status=='blocked_human'",
            src,
        )
        self.assertIn("ORDER_BLOCKER_OWNER_TRUTH_V1", src)
        self.assertIn("'executor_blocker':effective_blocker", src)
        self.assertIn("effective_blocker or effective_wait", src)
        self.assertIn("blocked_reason=effective_blocker", src)
        self.assertIn("terminal=st in ('completed','cancelled')", src)
        self.assertIn("None if terminal else", src)
        self.assertIn("(not terminal) and provider_retry_at", src)
        self.assertIn("if terminal", src)

    def test_development_ui_starts_new_task_and_has_autopilot_controls(self):
        page = Path("../frontend/app/dashboard/development/page.tsx").read_text(encoding="utf-8")
        self.assertIn("/autopilot", page)
        self.assertIn("Создать и запустить", page)
        self.assertIn("autopilotText", page)
        self.assertIn("Автопилот работает — сам до результата", page)
        self.assertIn("Ждёт свободного ИИ-исполнителя", page)
        self.assertIn("Выполняются сейчас", page)
        self.assertIn("BORIS ждёт / исправляет сам", page)
        self.assertIn("Нужно ваше действие", page)
        self.assertIn("ЖДЁТ ИИ — ВКЛЮЧИТСЯ САМ", page)
        self.assertIn("ВНУТРЕННИЙ БЛОКЕР — BORIS РЕШАЕТ САМ", page)
        self.assertIn("provider_unavailable", page)
        self.assertIn("review provider quota", page)
        self.assertIn("RATE_LIMITED", page)
        self.assertIn("<span>{operationalLabel(t)}</span>", page)
        self.assertIn("· {operationalLabel(selected)}</span>", page)
        self.assertIn('order==="blocked_infra")return false', page)
        self.assertIn("ONLINE_TRUTH_REPLACE_V1", page)
        refresh = page.split("const refreshOpen=useCallback", 1)[1].split(
            "useEffect(()=>{if(!allowed)return;const timer", 1
        )[0]
        self.assertIn("setThreads(live)", refresh)
        self.assertIn("limit=2000", refresh)
        self.assertNotIn(
            "threads?status=open&projection=development",
            refresh,
        )
        self.assertIn("Для обычного «продолжай» ничего нажимать не надо", page)


if __name__ == "__main__":
    unittest.main()
