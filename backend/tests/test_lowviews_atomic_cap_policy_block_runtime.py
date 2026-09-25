def test_lowviews_atomic_cap_is_blocked_not_raised(monkeypatch):
    import pathlib
    import app.api.cpx_advisor as mod
    import app.db.session as db_session
    import app.services.marketing_money_policy as money_policy
    import app.services.marketing_signal_guard as signal_guard

    class FakeDB:
        def close(self):
            pass

    monkeypatch.setenv("BORIS_MARKETER_MONEY_LANE", "controlled_lowviews_v2")
    orig_read_text = pathlib.Path.read_text
    def fake_read_text(self, *args, **kwargs):
        if str(self) == "/proc/self/cgroup":
            return "0::/system.slice/boris-ai-marketer.service"
        return orig_read_text(self, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    monkeypatch.setattr(db_session, "SessionLocal", lambda: FakeDB())
    monkeypatch.setattr(money_policy, "effective_hard_bid_cap", lambda db, account_id: 150.0)
    monkeypatch.setattr(signal_guard, "money_stats_snapshot_eligible", lambda db, account_id, max_age_seconds=900: (True, {"ok": True}))
    monkeypatch.setattr(mod, "ensure_paid_tariff_raise_mandate", lambda account_id: {"status": "ok"})
    monkeypatch.setattr(mod, "marketing_today_iso", lambda: "2026-09-10")

    saved = {}
    def fake_load(db, account_id, key):
        if key == "daily_stats:2026-09-10":
            return {"stats_date": "2026-09-10", "items": [{"id": 12345, "status": "active", "views": 5, "contacts": 0}]}
        return {}
    def fake_save(db, account_id, key, value):
        saved[key] = value
    monkeypatch.setattr(mod, "_load_json", fake_load)
    monkeypatch.setattr(mod, "_save_json", fake_save)

    def blocked_apply(_body):
        raise RuntimeError("blocked_first_bid_cross_source_hourly_atomic count=1 limit=1")
    monkeypatch.setattr(mod, "apply_one", blocked_apply)

    result = mod.hourly_account_low_views_ramp("acct", max_items=1)
    assert result["status"] == "blocked"
    assert result["applied"] == []
    assert result["failed"] == []
    assert result["blocked"][0]["item_id"] == 12345
    assert result["blocked"][0]["reason"] == "first_bid_cross_source_hourly_limit"
    assert result["blocked"][0]["retry_after_minutes"] >= 1
    assert saved["cpx_account_low_views_ramp"]["items"]["12345"]["status"] == "blocked"
