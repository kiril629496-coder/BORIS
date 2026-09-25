# -*- coding: utf-8 -*-
import importlib.util
import os
from pathlib import Path


def _load_posting_runner():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("posting_runner", root / "posting_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_guarded_banner_persists_successful_image_bytes(monkeypatch, tmp_path):
    from app.services import image_service

    out = tmp_path / "banner.png"
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (256, 256)).save(buf, format="PNG")
    payload = buf.getvalue()

    monkeypatch.setattr(image_service, "budget_reserve", lambda *a, **k: {"status": "reserved"})
    monkeypatch.setattr(image_service, "budget_allow", lambda *a, **k: True)
    monkeypatch.setattr(image_service, "budget_commit", lambda *a, **k: True)
    monkeypatch.setattr(image_service, "budget_release", lambda *a, **k: True)
    from app import usage as usage_module
    monkeypatch.setattr(usage_module, "log_usage", lambda *a, **k: 0.0)

    from app.services import openai_image_transport
    monkeypatch.setattr(openai_image_transport, "generate_image_http", lambda **kw: {
        "ok": True,
        "request_id": "req-test-banner",
        "path_data": payload,
        "size": "1024x1024",
        "quality": "medium",
        "usage": {},
    })

    result = image_service.generate_banner_financially_guarded(
        db=object(), account_id="existing-configured-account",
        prompt="safe test banner", save_path=str(out),
        model="gpt-image-2", quality="medium", operation="banner_social_post_test")

    assert result["ok"] is True
    assert result["path"] == str(out)
    assert out.exists() and out.read_bytes() == payload


def test_social_publishers_receive_existing_banner_without_network_send(monkeypatch, tmp_path):
    pr = _load_posting_runner()
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"not-a-real-image-but-existing")

    calls = {"tg": [], "vk": []}

    class _TG:
        BOT_TOKEN = "test"
        API_BASE = "https://example.invalid/bot"
        @staticmethod
        def send_telegram_message(chat_id, text):
            calls["tg"].append((chat_id, text, None))
            return {"ok": True}

    monkeypatch.setattr("app.telegram_bot.BOT_TOKEN", "test", raising=False)
    monkeypatch.setattr("app.telegram_bot.API_BASE", "https://example.invalid/bot", raising=False)

    import requests
    class _Resp:
        def json(self):
            return {"ok": True, "result": {"message_id": 1}}
    def fake_post(url, data=None, files=None, timeout=None, **kwargs):
        calls["tg"].append((url, bool(files and files.get("photo")), data))
        return _Resp()
    monkeypatch.setattr(requests, "post", fake_post)

    tg = pr._send_telegram("@test", "hello", str(banner), tenant_scope="qa_social_banner")
    assert tg.get("ok") is True
    assert any(item[1] is True for item in calls["tg"] if isinstance(item, tuple) and len(item) > 1)

    monkeypatch.setenv("VK_TOKEN", "test")
    class _GetResp:
        def __init__(self, body): self._body = body
        def json(self): return self._body
    def fake_get(url, params=None, **kwargs):
        if "photos.getWallUploadServer" in url:
            calls["vk"].append(("upload_server", params))
            return _GetResp({"response": {"upload_url": "https://upload.invalid"}})
        if "photos.saveWallPhoto" in url:
            calls["vk"].append(("save_photo", params))
            return _GetResp({"response": [{"owner_id": -1, "id": 2}]})
        if "wall.post" in url:
            calls["vk"].append(("wall_post", params))
            return _GetResp({"response": {"post_id": 3}})
        raise AssertionError(url)
    def fake_vk_post(url, files=None, **kwargs):
        calls["vk"].append(("upload_file", bool(files and files.get("photo"))))
        return _GetResp({"photo": "x", "server": 1, "hash": "h"})
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_vk_post)

    monkeypatch.setattr(pr, "_social_vk_rate_backoff_remaining", lambda: 0)
    vk = pr._send_vk(-1, "hello", str(banner), tenant_scope="qa_social_banner")
    assert vk.get("ok") is True
    wall = [x for x in calls["vk"] if x[0] == "wall_post"][0]
    assert wall[1].get("attachments") == "photo-1_2"


def test_scheduled_social_slot_is_persisted_before_paid_generation_and_deduped():
    pr = _load_posting_runner()
    import inspect
    src = inspect.getsource(pr._autopost_projects_impl)
    plan_src = inspect.getsource(pr._scheduled_generation_plan)
    save_src = inspect.getsource(pr._save_post_record)

    assert "_scheduled_generation_plan" in src
    assert "_posting_generation_exists" in src
    assert "SLOT_ALREADY_MATERIALIZED" in src
    assert "generation_key=_generation_key" in src
    assert "pg_advisory_xact_lock" in plan_src
    assert "posting_generation_plan:" in plan_src
    assert "generation_key" in save_src
