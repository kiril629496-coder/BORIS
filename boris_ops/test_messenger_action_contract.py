from pathlib import Path


CANDIDATE = Path(__file__).parent / "releases" / "agent-1.4.4-messenger-p0-candidate.py"
SOURCE = CANDIDATE.read_text(encoding="utf-8")


def test_messenger_action_is_registered_and_fixed():
    assert '"messenger_p0_selftest": ([], "/root/BORIS/releases/backend/current/backend", 1800)' in SOURCE
    assert '"messenger_p0_selftest"' in SOURCE
    assert '"--selector", "messenger_p0", "--account", "synthetic"' in SOURCE
    assert '"BORIS_MESSENGER_TEST_TRANSPORT"] = "stub"' in SOURCE
    assert '"EXT_API_ALLOW_TEST_HOOKS"] = "1"' in SOURCE
    assert '"/root/BORIS/backend/venv/bin/python", "-m", "app.ext_api.selftest"' in SOURCE


def test_messenger_action_ignores_caller_execution_inputs():
    fixed = SOURCE.split('if action == "messenger_p0_selftest":', 1)[1].split('elif action == "frontend_deploy":', 1)[0]
    assert "a.get(\"artifact\")" not in fixed
    assert "a.get(\"background\")" not in fixed
    assert "a.get(\"command\")" not in fixed
    assert "a.get(\"selector\")" not in fixed
    assert "a.get(\"account\")" not in fixed
    assert "a.get(\"cwd\")" not in fixed
    assert "a.get(\"env\")" not in fixed
    assert "script_run" not in fixed
    assert "sql" not in fixed.lower()
    assert "migration" not in fixed.lower()


def test_existing_action_registry_and_self_update_boundaries_remain():
    assert '"frontend_deploy":' in SOURCE
    assert '"backend_scoped_deploy":' in SOURCE
    assert 'SELF_UPDATE_GATE' in SOURCE
    assert 'RELEASE_DIR' in SOURCE
    assert 'candidate_outside_release_dir' in SOURCE
    assert 'candidate_sha256_mismatch' in SOURCE
