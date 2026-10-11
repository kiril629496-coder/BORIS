# -*- coding: utf-8 -*-
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _text(rel):
    return (BACKEND / rel).read_text(encoding="utf-8", errors="ignore")


def test_runtime_imports_do_not_force_mutable_backend_master():
    files = [
        "ads_autopilot.py",
        "factory_a.py",
        "factory_b.py",
        "marketer_rollout_runner.py",
        "app/services/intraday.py",
    ]
    forbidden = (
        'sys.path.insert(0, "/root/BORIS/backend")',
        "sys.path.insert(0, '/root/BORIS/backend')",
    )
    for rel in files:
        src = _text(rel)
        assert "BORIS_RUNTIME_BACKEND_ROOT" in src, rel
        assert not any(token in src for token in forbidden), rel


def test_runtime_spawned_python_entrypoints_use_release_root():
    marketer = _text("marketer_rollout_runner.py")
    factory = _text("app/api/factory_confirmations.py")
    qa = _text("app/api/qa.py")
    assert "os.path.join(RUNTIME_BACKEND,'cpx_advisor_runner.py')" in marketer
    assert 'os.path.join(runtime_backend, "factory_h_runner.py")' in factory
    assert 'os.path.join(RUNTIME_BACKEND, "qa_tester.py")' in qa
    assert 'os.path.join(RUNTIME_BACKEND,"ux_client_tester.py")' in qa


def test_release_builder_is_fail_closed_on_source_race_and_seals_artifact():
    src = _text("scripts/build_backend_release.py")
    assert "SOURCE_CHANGED_DURING_BUILD" in src
    assert "chattr" in src and '"+i"' in src
    assert "_clean_root_python_files" in src
    assert 'SOURCE / "client_aliases.local.json.example"' in src
    assert "--activate" in src


def test_deploy_requires_release_build_before_runtime_contract():
    src = _text("app/ext_api/deploy.py")
    release_pos = src.index('res["backend_release"] = _backend_release_build_activate()')
    contract_pos = src.index('res["production_contract"] =')
    assert release_pos < contract_pos
    assert 'res["deploy_blocked"] = "backend_release_build_failed"' in src


def test_generation_truth_points_at_immutable_release():
    src = _text("production_contract_runner.py")
    assert "releases/backend/current/backend" in src
    assert "BACKEND_ACTIVE_ROOT" in src
    assert "BACKEND_RUNTIME_PATHS=[p for p in (BACKEND_ACTIVE_ROOT/'app')" in src


def test_rolling_preflight_imports_active_release_not_mutable_master():
    src = _text("rolling_restart_backend.sh")
    assert "active_backend_root" in src
    assert 'BORIS_RUNTIME_BACKEND_ROOT="$rel" PYTHONPATH="$rel"' in src
    assert "DEPLOY_SOURCE_IMPORT_FENCE_V2" in src


def test_workspace_bytes_are_not_automatic_production_authority():
    src = _text("app/ext_api/deploy.py")
    assert "WORKSHOP_NOT_PRODUCTION_V1" in src
    assert "_backend_release_reasons" in src
    assert 'patches.get("applied")' in src
    assert 'item.get("ok") is True and item.get("archived")' in src
    assert '"skipped": "no canonical accepted backend source change"' in src
