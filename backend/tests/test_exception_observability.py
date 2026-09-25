# -*- coding: utf-8 -*-
import logging

from app.services import exception_observability as obs


def test_observe_suppressed_never_logs_raw_exception_text(caplog):
    obs._LAST.clear()
    secret = "customer-phone-79991234567-token-SECRET"
    with caplog.at_level(logging.WARNING, logger="boris.suppressed"):
        obs.observe_suppressed("tests.secret", RuntimeError(secret), line=77,
                               min_interval_seconds=0)
    text = caplog.text
    assert secret not in text
    assert "RuntimeError" in text
    assert "tests.secret" in text
    assert "line=77" in text
    assert "fingerprint=" in text


def test_observe_suppressed_rate_limits_identical_failure(caplog):
    obs._LAST.clear()
    with caplog.at_level(logging.WARNING, logger="boris.suppressed"):
        obs.observe_suppressed("tests.rate", ValueError("same"), line=9,
                               min_interval_seconds=300)
        obs.observe_suppressed("tests.rate", ValueError("same"), line=9,
                               min_interval_seconds=300)
    messages = [r.message for r in caplog.records if "legacy_suppressed_exception" in r.message]
    assert len(messages) == 1
