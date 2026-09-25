# -*- coding: utf-8 -*-
from unittest.mock import patch

from app.ext_api import deploy


def test_provenance_rejects_unreviewed_runtime_hitchhiker():
    patches = {"applied": [{"patch_id": 1, "files": ["backend/app/api/a.py"]}]}
    with patch.object(deploy, "_runtime_workspace_changed_paths", return_value=[
        "app/api/a.py", "production_contract_runner.py"
    ]):
        result = deploy._backend_release_provenance_gate(patches, [])
    assert result["ok"] is False
    assert result["reason"] == "unreviewed_workspace_runtime_drift"
    assert result["unexpected"] == ["production_contract_runner.py"]


def test_provenance_allows_only_exact_applied_runtime_paths():
    patches = {"applied": [{"patch_id": 1, "files": [
        "backend/app/api/a.py", "backend/production_contract_runner.py"
    ]}]}
    with patch.object(deploy, "_runtime_workspace_changed_paths", return_value=[
        "app/api/a.py", "production_contract_runner.py"
    ]):
        result = deploy._backend_release_provenance_gate(patches, [])
    assert result["ok"] is True
    assert result["unexpected"] == []


def test_legacy_bundle_without_file_provenance_is_fail_closed():
    bundles = [{"ok": True, "archived": "/tmp/bundle.py", "bundle": "bundle.py"}]
    with patch.object(deploy, "_runtime_workspace_changed_paths", return_value=["app/ext_api/deploy.py"]):
        result = deploy._backend_release_provenance_gate({"applied": []}, bundles)
    assert result["ok"] is False
    assert result["reason"] == "accepted_bundle_missing_runtime_file_provenance"


def test_bootstrap_without_active_artifact_is_explicitly_allowed():
    with patch.object(deploy, "_runtime_workspace_changed_paths", return_value=None):
        result = deploy._backend_release_provenance_gate({"applied": []}, [])
    assert result["ok"] is True
    assert result["bootstrap"] is True


def test_tick_checks_provenance_before_release_activation():
    import inspect
    source = inspect.getsource(deploy.tick)
    provenance = source.index("_backend_release_provenance_gate")
    activation = source.index("_backend_release_build_activate")
    assert provenance < activation
    assert "backend_release_provenance_failed" in source
