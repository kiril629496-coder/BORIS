from types import SimpleNamespace
from unittest.mock import patch

from gigachat.models import Messages, MessagesRole

import gigachat_pool as G
from app.ext_api import lead, mcp_server


def test_primary_gigachat_receives_only_tokenized_client_data():
    captured = {}

    class Client:
        def chat(self, chat):
            captured["messages"] = [m.content for m in chat.messages]
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content="Перезвоню [[BORIS_PERSON_1]] по [[BORIS_PHONE_1]]"
                    )
                )],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

    class CM:
        def __enter__(self):
            return Client()
        def __exit__(self, *args):
            return False

    messages = [
        Messages(
            role=MessagesRole.USER,
            content="Клиент: Иван Иванов; телефон +7 999 123-45-67; ivan@example.com",
        )
    ]
    with patch.object(G, "_giga_breaker_is_open", return_value=False),          patch.object(G, "_outage_should_skip", return_value=False),          patch.object(G, "_outage_note_success", return_value=None),          patch.object(G, "RateLimitedGigaChat", return_value=CM()),          patch("app.usage.log_usage", return_value=0):
        out = G.chat_with_fallback(
            messages,
            credentials="test",
            account_id="qa",
            operation="privacy-test",
        )

    outbound = "\n".join(captured["messages"])
    assert "Иван Иванов" not in outbound
    assert "999" not in outbound
    assert "ivan@example.com" not in outbound
    assert "[[BORIS_PERSON_1]]" in outbound
    assert "[[BORIS_PHONE_1]]" in outbound
    assert "[[BORIS_EMAIL_1]]" in outbound
    assert "Иван Иванов" in out
    assert "+7 999 123-45-67" in out


def test_mcp_tool_output_is_sanitized_before_claude_sees_it():
    out = mcp_server._text({
        "client_name": "Иван Иванов",
        "phone": "+7 999 123-45-67",
        "email": "ivan@example.com",
    })
    assert "Иван Иванов" not in out
    assert "999" not in out
    assert "ivan@example.com" not in out
    assert "[[BORIS_" in out


def test_claude_cli_prompt_is_sanitized_and_builtin_file_tools_are_not_allowed():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"{\\\"status\\\":\\\"ok\\\",\\\"reply\\\":\\\"Позвоню [[BORIS_PHONE_1]]\\\"}"}',
            stderr="",
        )

    with patch.object(lead, "claude_cli", return_value="/fake/claude"),          patch.object(lead, "oauth_token", return_value="token"),          patch.object(lead, "_env_all", return_value={}),          patch.object(lead.subprocess, "run", side_effect=fake_run):
        out = lead._ask_claude_code(
            "Клиент: Иван Иванов; телефон +7 999 123-45-67",
            tools=True,
        )

    argv = captured["argv"]
    sent_prompt = argv[argv.index("-p") + 1]
    assert "Иван Иванов" not in sent_prompt
    assert "999" not in sent_prompt
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Read" not in allowed
    assert "Grep" not in allowed
    assert "Glob" not in allowed
    assert "mcp__boris__repo_read" in allowed
    assert out["reply"] == "Позвоню +7 999 123-45-67"


def test_gemini_cli_prompt_is_sanitized_and_output_restored():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return SimpleNamespace(
            returncode=0,
            stdout='{"response":"{\\\"status\\\":\\\"ok\\\",\\\"reply\\\":\\\"[[BORIS_PERSON_1]] [[BORIS_EMAIL_1]]\\\"}"}',
            stderr="",
        )

    with patch.object(lead.subprocess, "run", side_effect=fake_run):
        out = lead._ask_gemini_cli(
            "Клиент: Иван Иванов; email ivan@example.com",
            cwd="/tmp",
        )

    argv = captured["argv"]
    sent_prompt = argv[argv.index("-p") + 1]
    assert "Иван Иванов" not in sent_prompt
    assert "ivan@example.com" not in sent_prompt
    assert out["reply"] == "Иван Иванов ivan@example.com"


def test_gemini_bounded_workspace_contains_only_sanitized_source(tmp_path, monkeypatch):
    from app.ext_api import executor

    source = tmp_path / "source.py"
    source.write_text(
        'CLIENT = "Иван Иванов"\nPHONE = "+7 999 123-45-67"\nEMAIL = "ivan@example.com"\n',
        encoding="utf-8",
    )
    work_root = tmp_path / "workers"
    monkeypatch.setattr(executor, "WORKSPACE", str(work_root))
    monkeypatch.setattr(
        executor, "_gemini_direct_scope_files",
        lambda order: ["backend/source.py"],
    )
    monkeypatch.setattr(
        executor, "_gemini_context_candidates",
        lambda order: ["backend/source.py"],
    )
    monkeypatch.setattr(executor.repo, "safe_path", lambda rel: str(source))

    work, ctx = executor._prepare_gemini_workspace({
        "order_id": 7001,
        "dev_job_id": 99,
        "phase": "qa",
        "scope": {"note": "клиент Иван Иванов, +7 999 123-45-67"},
    })
    external_copy = (
        __import__("pathlib").Path(work) / "source" / "backend" / "source.py"
    ).read_text(encoding="utf-8")
    external_context = (
        __import__("pathlib").Path(work) / "BORIS_CONTEXT.json"
    ).read_text(encoding="utf-8")

    for value in ("Иван Иванов", "999", "ivan@example.com"):
        assert value not in external_copy
    assert "Иван Иванов" not in external_context
    assert "999" not in external_context
    assert "[[BORIS_" in external_copy
    assert ctx.get("_privacy_vault") is not None
    assert "Иван Иванов" not in external_context


def test_all_client_reference_vision_routes_use_sanitized_pixels():
    from pathlib import Path

    root = Path("/root/BORIS/backend")
    banners = (root / "app/api/banners.py").read_text(encoding="utf-8")
    campaigns = (root / "app/api/campaigns.py").read_text(encoding="utf-8")
    image_service = (root / "app/services/image_service.py").read_text(encoding="utf-8")
    media_validator = (root / "app/services/media_semantic_validator.py").read_text(encoding="utf-8")
    video_qa = (root / "app/video_factory/visual_qa.py").read_text(encoding="utf-8")

    assert banners.count("sanitize_external_reference_image_bytes") >= 3
    assert "safe_images.append(" in banners
    assert "Never give an external model a private/signed/arbitrary URL" in banners
    assert '"data:image/jpeg;base64," + raw' not in banners
    assert 'content.extend({"type": "input_image", "image_url": u} for u in urls)' not in banners

    ref_start = campaigns.index("def ff_workflow_references_save")
    ref_end = campaigns.index("def _ff_clean_title_for_avito", ref_start)
    ref_block = campaigns[ref_start:ref_end]
    assert "sanitize_external_reference_image_bytes" in ref_block
    assert "thumbnail((1600,1600))" not in ref_block
    assert "Не пытайся читать, восстанавливать или угадывать" in ref_block

    assert "sanitize_external_reference_image_bytes" in image_service
    assert "sanitize_external_reference_image_bytes" in media_validator
    assert "sanitize_external_reference_image_bytes" in video_qa


def test_production_contract_rejects_unprotected_external_ai_boundaries():
    import production_contract_runner as contract
    checks = contract._source_checks()
    assert checks["external_ai_boundary_dynamic_scan"] is True
