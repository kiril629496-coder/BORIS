# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_LAUNCHER = ROOT / "scripts/boris_backend_release_launcher.sh"
SYSTEM_LAUNCHER = Path("/usr/local/sbin/boris-backend-release-launcher")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit(name: str) -> str:
    return subprocess.check_output(["systemctl", "cat", name], text=True)


def test_canonical_launcher_owns_all_long_lived_runtime_roles():
    src = REPO_LAUNCHER.read_text(encoding="utf-8")
    for token in (
        "--worker",
        "--telephony",
        "--crm",
        "BORIS_RUNTIME_BACKEND_ROOT",
        "BORIS_RELEASE_BUILD_ID",
        'export PYTHONPATH="$rel"',
        "releases/backend/current/backend",
    ):
        assert token in src, token


def test_system_launcher_is_exact_canonical_copy():
    assert SYSTEM_LAUNCHER.is_file()
    assert _sha(SYSTEM_LAUNCHER) == _sha(REPO_LAUNCHER)


def test_background_worker_uses_immutable_release_launcher():
    src = _unit("boris-background-worker.service")
    assert "IMMUTABLE_LONG_LIVED_RUNTIME_V1" in src
    assert "boris-backend-release-launcher --preflight worker" in src
    assert "boris-backend-release-launcher --worker" in src


def test_crm_sync_uses_immutable_release_launcher():
    src = _unit("boris-crm-sync.service")
    assert "IMMUTABLE_LONG_LIVED_RUNTIME_V1" in src
    assert "boris-backend-release-launcher --preflight crm" in src
    assert "boris-backend-release-launcher --crm" in src


def test_telephony_uses_immutable_release_launcher():
    src = _unit("boris-telephony-runtime.service")
    assert "IMMUTABLE_LONG_LIVED_RUNTIME_V1" in src
    assert "boris-backend-release-launcher --preflight telephony" in src
    assert "boris-backend-release-launcher --telephony" in src


def test_generation_contract_tracks_active_release_for_long_lived_runtimes():
    src = (ROOT / "production_contract_runner.py").read_text(encoding="utf-8")
    assert "BACKEND_ACTIVE_ROOT/'boris_background_worker.py'" in src
    assert 'BACKEND_ACTIVE_ROOT/"app/crm/bridge.py"' in src
    assert 'BACKEND_ACTIVE_ROOT/"app/services/telephony_core.py"' in src
