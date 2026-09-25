# -*- coding: utf-8 -*-
from app.ext_api import a2a


def test_recoverable_executor_error_codes_are_narrow():
    assert set(a2a.RECOVERABLE_EXECUTOR_ERRORS) == {
        "MISSING_INPUT",
        "VALIDATION_ERROR",
        "POSTFLIGHT_UNVERIFIED",
    }


def test_recovery_keeps_alias_pool_semantics():
    assert "boris-executor" in a2a._assignment_names("gpt-executor")
    assert "gpt-executor" in a2a._assignment_names("boris-executor")


def test_recovery_function_is_present_for_daemon_startup():
    assert callable(a2a.recover_stranded_executor_orders)
