# -*- coding: utf-8 -*-
import datetime

from app.ext_api import aiprov, lead


def test_handle_none_verdict_becomes_diagnostic_block_not_crash(monkeypatch):
    order = {"order_id": 85563}
    events = []
    monkeypatch.setattr(lead, "decide", lambda _o: None)
    monkeypatch.setattr(lead.a2a, "message", lambda *a, **k: events.append(a))
    monkeypatch.setattr(lead.a2a, "review", lambda key, oid, verdict, note, fixes, blocked_reason: {
        "order_id": oid, "status": lead.a2a.BLOCKED_INFRA,
        "verdict": verdict, "blocked_reason": blocked_reason})
    out = lead.handle(order)
    assert out["status"] == lead.a2a.BLOCKED_INFRA
    assert any(e[1] == "REVIEWER_ERROR" for e in events)


def test_weekly_limit_uses_reset_horizon_and_generic_429_does_not():
    now = datetime.datetime(2026, 8, 27, 12, 0, tzinfo=datetime.timezone.utc)
    secs = aiprov.weekly_limit_retry_after(
        "You've hit your weekly limit. reset_at=2026-09-01T12:00:00Z", now=now)
    assert secs == 5 * 24 * 60 * 60
    assert aiprov.weekly_limit_retry_after("429 rate limited, retry later", now=now) is None


def test_weekly_rate_limit_immediately_falls_through_to_next_provider(monkeypatch):
    marks, calls = [], []
    monkeypatch.setattr(aiprov, "candidates", lambda cap: ["claude_code", "gemini_cli"])
    monkeypatch.setattr(aiprov, "mark", lambda *a, **k: marks.append((a, k)))
    monkeypatch.setattr(aiprov, "_seen", lambda key: None)
    monkeypatch.setattr(aiprov, "_remember", lambda *a, **k: None)
    monkeypatch.setattr(aiprov, "why_empty", lambda cap: [])
    monkeypatch.setattr(aiprov.time, "sleep", lambda *_: None)

    def claude():
        calls.append("claude_code")
        from app.ext_api.errors import ApiError
        raise ApiError("RATE_LIMITED",
                       "You've hit your weekly limit. reset_at=2099-09-01T12:00:00Z")

    def gemini():
        calls.append("gemini_cli")
        return "ok"

    out = aiprov.call(aiprov.CODING, {"claude_code": claude, "gemini_cli": gemini})
    assert out["ok"] is True and out["provider"] == "gemini_cli"
    assert calls == ["claude_code", "gemini_cli"]
    assert marks[0][1].get("retry_after", 0) > aiprov.RETRY_AFTER[aiprov.RATE_LIMITED]
