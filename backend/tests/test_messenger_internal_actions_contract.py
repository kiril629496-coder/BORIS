from pathlib import Path

from boris_ops.messenger_internal_actions import ACTION_ARGV, action_argv


def test_internal_actions_are_fixed_and_allowlisted():
    assert set(ACTION_ARGV) == {
        "messenger_migration_status",
        "messenger_migration_verify",
        "messenger_migration_060",
        "messenger_backend_tests",
    }
    for name, argv in ACTION_ARGV.items():
        assert isinstance(argv, tuple)
        assert all(isinstance(part, str) and part for part in argv)
        assert ";" not in " ".join(argv)
        assert "--from" not in argv or argv[argv.index("--from") + 1] == "060"
        assert "--to" not in argv or argv[argv.index("--to") + 1] == "060"


def test_migration_actions_use_canonical_runner():
    runner = str(Path("/root/BORIS/backend/migrations/run_all.py"))
    assert runner in action_argv("messenger_migration_status")
    assert runner in action_argv("messenger_migration_verify")
    assert runner in action_argv("messenger_migration_060")
    assert action_argv("messenger_migration_060")[-4:] == ("--from", "060", "--to", "060")


def test_backend_test_action_is_fixed_to_internal_contract():
    argv = action_argv("messenger_backend_tests")
    assert argv[:3] == ("/root/BORIS/backend/venv/bin/python", "-m", "pytest")
    assert argv[-2:] == ("/root/BORIS/backend/tests/test_internal_messenger_contract.py", "-q")
