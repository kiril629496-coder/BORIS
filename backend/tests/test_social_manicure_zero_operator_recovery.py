# -*- coding: utf-8 -*-
import json

import posting_runner as pr


class _Row:
    def __init__(self, value):
        self.value = value


class _Query:
    def __init__(self, row=None):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row


class _DB:
    def __init__(self, row=None):
        self.row = row
        self.commits = 0
        self.rollbacks = 0

    def query(self, *args, **kwargs):
        return _Query(self.row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_legacy_manicure_missing_autopilot_flags_self_heals(monkeypatch):
    stored = {
        "id": "270dbf472f7f",
        "name": "Маникюр Евпатория",
        "platforms": "vk",
        "mode": "moderate",
        "times": ["9", "18"],
    }
    row = _Row(json.dumps([stored], ensure_ascii=False))
    db = _DB(row)
    monkeypatch.setattr(pr, "SessionLocal", lambda: db)

    runtime_project = dict(stored)
    changed = pr._self_heal_expected_auto_generation("u2", runtime_project)

    assert changed is True
    saved = json.loads(row.value)[0]
    assert saved["expected_auto_generation"] is True
    assert saved["paid_auto_generation"] is True
    assert runtime_project["expected_auto_generation"] is True
    assert runtime_project["paid_auto_generation"] is True
    assert db.commits == 1


def test_explicit_owner_autopilot_stop_is_not_overridden(monkeypatch):
    project = {
        "id": "270dbf472f7f",
        "name": "Маникюр Евпатория",
        "expected_auto_generation": False,
        "paid_auto_generation": False,
    }

    def should_not_open_db():
        raise AssertionError("explicit False must remain an intentional stop")

    monkeypatch.setattr(pr, "SessionLocal", should_not_open_db)
    assert pr._self_heal_expected_auto_generation("u2", project) is False
    assert project["expected_auto_generation"] is False
    assert project["paid_auto_generation"] is False


def test_vk_flood_control_stops_after_first_api_call(monkeypatch, tmp_path):
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"not-an-image")
    monkeypatch.setenv("VK_TOKEN", "test")
    monkeypatch.setattr(pr, "_social_vk_rate_backoff_remaining", lambda: 0)
    armed = []
    monkeypatch.setattr(
        pr, "_set_social_vk_rate_backoff",
        lambda seconds: armed.append(seconds) or seconds,
    )

    import requests

    calls = []

    class _Resp:
        def json(self):
            return {"error": {"error_code": 9, "error_msg": "Flood control"}}

    def fake_get(url, **kwargs):
        calls.append(url)
        return _Resp()

    def no_post(*args, **kwargs):
        raise AssertionError("file upload must not start after VK code 9")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", no_post)

    result = pr._send_vk(-176601355, "тест", str(banner), tenant_scope="qa_manicure")

    assert result["ok"] is False
    assert result["code"] == 9
    assert result["reason"] == "vk_rate_limited"
    assert result["dependency_state"] == "waiting_external"
    assert result["retryable_external"] is True
    assert len(calls) == 1
    assert armed == [3600]


def test_vk_live_rate_backoff_makes_zero_external_calls(monkeypatch):
    monkeypatch.setenv("VK_TOKEN", "test")
    monkeypatch.setattr(pr, "_social_vk_rate_backoff_remaining", lambda: 321)

    import requests

    monkeypatch.setattr(
        requests, "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("VK must not be called during live cooldown")
        ),
    )
    monkeypatch.setattr(
        requests, "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("VK must not be called during live cooldown")
        ),
    )

    result = pr._send_vk(-176601355, "тест", None, tenant_scope="qa_manicure")
    assert result["ok"] is False
    assert result["stage"] == "rate_backoff"
    assert result["retry_after_seconds"] == 321


def test_vk_maintenance_yields_to_live_delivery_even_without_stored_edit_backoff(monkeypatch):
    db = _DB(row=None)
    monkeypatch.setattr(pr, "SessionLocal", lambda: db)
    monkeypatch.setattr(pr, "_social_vk_rate_backoff_remaining", lambda: 0)
    monkeypatch.setattr(pr, "_social_vk_live_delivery_pending", lambda: True)

    assert pr._social_vk_backoff_remaining() == 900
