import importlib.util
from pathlib import Path
from unittest.mock import patch

from app.services import crowd_seo, service_marketplace


def test_paused_project_preserves_history_and_can_resume(monkeypatch):
    rows = [{"id": "p", "status": "approved_for_placement",
             "content_status": "approved", "placements": [{"id": "keep"}]}]
    monkeypatch.setattr(crowd_seo, "_load", lambda: rows)
    monkeypatch.setattr(crowd_seo, "_save", lambda rows: None)
    monkeypatch.setattr(crowd_seo, "get_project", lambda key: rows[0])
    monkeypatch.setattr(crowd_seo, "_operational_status", lambda row: "approved_for_placement")
    result = crowd_seo.decide_project("p", "paused")
    assert not crowd_seo.project_is_active(result)
    assert result["content_status"] == "approved"
    assert result["placements"] == [{"id": "keep"}]
    crowd_seo.decide_project("p", "resume")
    assert crowd_seo.project_is_active(rows[0])


def test_pending_moderation_keeps_original_draft(monkeypatch):
    rows = [{"id": "p", "content_status": "approved", "placements": [
        {"status": "publication_pending_verification", "marketplace_draft_id": "d"}
    ]}]
    original = {"id": "d", "crowd_project_id": "p", "status": "approved", "text": "original"}
    monkeypatch.setattr(crowd_seo, "_load", lambda: rows)
    monkeypatch.setattr(crowd_seo, "_save", lambda rows: None)
    monkeypatch.setattr(service_marketplace, "list_drafts", lambda: [original])
    with patch.object(service_marketplace, "set_draft_status") as reject, patch.object(
        service_marketplace, "save_drafts"
    ):
        crowd_seo.sync_project_drafts("p")
        reject.assert_not_called()
    assert original["text"] == "original"


def test_pdfcatalog_fills_both_steps_without_false_publication(monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import parse_qs
    spec = importlib.util.spec_from_file_location("seo_browser_regression",
        Path(__file__).resolve().parents[1] / "tools/marketplace_browser_assistant.py")
    browser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser)
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.reply('<form method="post" action="/addsite1">'
                '<input name="siteadd" required><input name="name" required>'
                '<input name="email" required><input name="keyw" required>'
                '<textarea name="text"></textarea><input id="submit_r" type="submit" value="Далее"></form>')
        def reply(self, text):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode())
        def do_POST(self):
            fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            received.append(fields)
            if self.path == "/addsite1":
                assert all(fields.get(k) for k in ("siteadd", "name", "email", "keyw", "text"))
                self.reply('<form method="post" action="/receipt">'
                    '<select name="rubr"><option>Авто Мото</option>'
                    '<option>Компьютеры - Софт</option></select>'
                    '<input type="submit" name="submit" value="Отправить"></form>')
            else:
                self.reply("Сайт добавлен. https://example.com/ Test software")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(browser, "RUNS", tmp_path)
    monkeypatch.setattr(browser, "_platform_start_url", lambda platform: f"http://127.0.0.1:{server.server_port}/addsite")
    monkeypatch.setattr(browser, "_default_email", lambda: "owner@example.com")
    draft = {"id": "d", "platform": "pubdisc_pdfcatalog_ru", "status": "approved",
        "title": "Test software", "text": "Software service", "target_url": "https://example.com/",
        "keyword_phrases": [f"keyword {i}" for i in range(15)]}
    monkeypatch.setattr(browser, "_find_draft", lambda key: draft)
    monkeypatch.setattr(browser.platform_rules, "inspect_platform", lambda platform: {"decision": "allowed"})
    monkeypatch.setattr(browser.platform_rules, "publish_gate", lambda *a, **kw: {"allowed": True})
    monkeypatch.setattr(browser.marketplace_svc, "get_platform", lambda platform: None)
    monkeypatch.setattr(browser.marketplace_svc, "list_platforms", lambda: [])
    monkeypatch.setattr(browser.marketplace_svc, "registration_plan", lambda: [{"platform": "pubdisc_pdfcatalog_ru", "status": "ready"}])
    monkeypatch.setattr(browser.marketplace_svc, "validate_publication_content", lambda d: {"ok": True})
    monkeypatch.setattr(browser.marketplace_svc, "record_attempt", lambda **kw: None)
    monkeypatch.setattr(browser, "_context", lambda pw, platform, headless: pw.chromium.launch_persistent_context(
        str(tmp_path / "profile"), headless=True))
    try:
        with patch.object(browser.marketplace_svc, "mark_posted") as posted:
            result = browser.prepare_post("pubdisc_pdfcatalog_ru", "d", headless=True, publish=True)
            posted.assert_not_called()
        assert len(received) == 2
        assert received[1]["rubr"] == ["Компьютеры - Софт"]
        assert result["publish_clicked"]
        assert not result["publication_verified"]
        assert result["checkpoint"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_duplicate_receipt_is_not_accepted():
    spec = importlib.util.spec_from_file_location("seo_receipt_regression",
        Path(__file__).resolve().parents[1] / "tools/marketplace_browser_assistant.py")
    browser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser)
    assert not browser._pdfcatalog_receipt_accepted("Такой сайт уже есть! Благодарим за размещение объявления!")
    assert not browser._pdfcatalog_receipt_accepted("Описание сайта. Далее.")
    assert browser._pdfcatalog_receipt_accepted("Сайт добавлен")


def test_product_free_pricing_does_not_unlock_paid_submission(monkeypatch):
    from app.services import platform_rules
    monkeypatch.setattr(platform_rules, "_publication_discovery_authoring_evidence", lambda *args: {"found": True})
    inspected = [{"text_excerpt": "Pricing Free Freemium Paid Free Trial. Submit for Review · $39",
                  "form_profile": {"forms": 1, "has_website": True}, "evidence": {}}]
    result, requirements = platform_rules._direct_zero_cost_submission_override("pubdisc_paid_test", inspected, "review", [])
    assert result == "blocked"
    assert "paid_submission_required" in requirements
