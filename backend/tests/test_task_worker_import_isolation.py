# -*- coding: utf-8 -*-
import os


def test_pytest_disables_import_time_task_workers():
    assert os.environ.get("BORIS_TASK_WORKERS_ENABLED") == "0"
    from app.api import tasks
    assert tasks.TASK_WORKERS_ENABLED is False
    assert tasks._worker_threads == []


def test_production_default_remains_enabled_in_source():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "tasks.py").read_text(encoding="utf-8")
    assert 'os.getenv("BORIS_TASK_WORKERS_ENABLED", "1")' in source
    assert "TASK_WORKER_IMPORT_SIDE_EFFECT_GUARD_V1" in source
