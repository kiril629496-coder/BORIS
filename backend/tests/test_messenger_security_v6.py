import os
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = Path(os.environ.get("BORIS_FRONTEND_ROOT", "/root/BORIS/frontend")).resolve()
INTERNAL = (BACKEND_ROOT / "app/api/internal_messenger.py").read_text(encoding="utf-8")
TEAM = (BACKEND_ROOT / "app/api/team.py").read_text(encoding="utf-8")
LEGAL = (BACKEND_ROOT / "app/api/legal.py").read_text(encoding="utf-8")
LEGAL_SERVICE = (BACKEND_ROOT / "app/services/messenger_legal.py").read_text(encoding="utf-8")
AUTH = (BACKEND_ROOT / "app/api/auth.py").read_text(encoding="utf-8")
SESSIONS = (BACKEND_ROOT / "app/services/auth_sessions.py").read_text(encoding="utf-8")
MAIN = (BACKEND_ROOT / "app/main.py").read_text(encoding="utf-8")
NEXT = (FRONTEND_ROOT / "next.config.ts").read_text(encoding="utf-8")
MESSENGER = (FRONTEND_ROOT / "app/messenger/page.tsx").read_text(encoding="utf-8")
MIGRATION_PATH = BACKEND_ROOT / "migrations/062_messenger_security_legal.sql"
if not MIGRATION_PATH.is_file():
    MIGRATION_PATH = Path("/root/BORIS/backend/migrations/062_messenger_security_legal.sql")
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")


def test_legal_acceptance_is_versioned_hashed_snapshotted_and_unique():
    for marker in (
        "document_version",
        "document_hash",
        "document_snapshot",
        "accepted_at",
        "ip_hash",
        "user_agent",
        "session_id",
        "UNIQUE(user_id, document_type, document_version, document_hash)",
    ):
        assert marker in MIGRATION
    assert "hashlib.sha256(body.encode" in LEGAL_SERVICE
    assert "ON CONFLICT(user_id,document_type,document_version,document_hash)" in LEGAL_SERVICE
    assert "document_snapshot" in LEGAL_SERVICE


def test_legal_acceptance_is_explicit_and_server_enforced_for_messenger_native_apis():
    assert "accepted is not True" in LEGAL
    assert "submitted != expected" in LEGAL
    assert "require_messenger_legal_user" in LEGAL
    assert "dependencies=[Depends(require_messenger_legal_user)]" in INTERNAL
    assert "Depends(require_messenger_legal_user)" in TEAM


def test_revocable_sessions_and_security_epoch_are_server_side():
    assert "auth_user_sessions" in MIGRATION
    assert "security_epoch" in MIGRATION
    assert "validate_session(" in AUTH
    assert "token_epoch != current_epoch" in AUTH
    assert "revoke_all_and_advance_epoch" in AUTH
    assert "revoked_at IS NULL AND expires_at>now()" in SESSIONS


def test_team_revoke_kills_push_and_last_access_sessions():
    assert "UPDATE lead_push_subscriptions SET active=FALSE" in TEAM
    assert 'revoke_all_and_advance_epoch(db, int(emp.id), "team_access_revoked")' in TEAM
    assert "ensure_general_conversation_membership(db, acc)" in TEAM


def test_security_headers_cors_and_frontend_csp_are_fail_closed():
    assert '_cors_origins = ["https://boris-ai.pro"]' in MAIN
    assert 'allow_credentials=True' in MAIN
    assert 'Strict-Transport-Security' in MAIN
    assert 'X-Content-Type-Options' in MAIN
    assert 'X-Frame-Options' in MAIN
    assert 'Referrer-Policy' in MAIN
    assert 'Permissions-Policy' in MAIN

    assert "Content-Security-Policy" in NEXT
    assert "frame-ancestors 'none'" in NEXT
    assert "object-src 'none'" in NEXT
    assert "base-uri 'self'" in NEXT


def test_messenger_has_no_raw_html_rendering_and_has_abuse_limits():
    assert "dangerouslySetInnerHTML" not in MESSENGER
    assert 'enforce_messenger_rate("messenger_search"' in INTERNAL
    assert 'enforce_messenger_rate("messenger_send"' in INTERNAL
    assert 'enforce_messenger_rate("messenger_upload"' in INTERNAL
    assert 'enforce_messenger_rate("messenger_invite"' in TEAM


def test_security_sensitive_actions_are_audited_without_secret_payloads():
    security = (BACKEND_ROOT / "app/services/messenger_security.py").read_text(encoding="utf-8")
    assert 'source="messenger_security"' in security
    assert "Принял документы BORIS Messenger" in LEGAL
    assert "Завершил сеанс" in AUTH
    assert "Завершил все остальные сеансы" in AUTH
    assert "Вышел из BORIS" in AUTH
    for marker in (
        "Создал приглашение сотрудника",
        "Принял приглашение в команду",
        "Изменил роль сотрудника",
        "Отозвал доступ сотрудника",
        "Отозвал приглашение",
    ):
        assert marker in TEAM
    for marker in (
        "Удалил сообщение",
        "Добавил участника в группу",
        "Удалил участника из группы",
        "Передал владение группой",
        "Покинул группу",
    ):
        assert marker in INTERNAL
    assert "password" not in security.lower()
    assert "access_token" not in security.lower()


def test_attachment_magic_validator_accepts_real_signatures_and_rejects_spoof():
    from app.api.internal_messenger import _validate_attachment_magic

    _validate_attachment_magic("image/jpeg", "photo.jpg", b"\xff\xd8\xff\xe0" + b"x" * 20)
    _validate_attachment_magic("image/png", "photo.png", b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    _validate_attachment_magic("application/pdf", "doc.pdf", b"%PDF-1.7\n" + b"x" * 20)
    _validate_attachment_magic("application/zip", "archive.zip", b"PK\x03\x04" + b"x" * 20)
    _validate_attachment_magic("text/plain", "note.txt", "безопасный текст".encode("utf-8"))

    with pytest.raises(HTTPException) as jpg_spoof:
        _validate_attachment_magic("image/jpeg", "photo.jpg", b"<script>alert(1)</script>")
    assert jpg_spoof.value.status_code == 415

    with pytest.raises(HTTPException) as pdf_spoof:
        _validate_attachment_magic("application/pdf", "doc.pdf", b"MZ" + b"x" * 30)
    assert pdf_spoof.value.status_code == 415

    with pytest.raises(HTTPException) as svg:
        _validate_attachment_magic("image/png", "evil.svg", b"<svg onload='x'/>")
    assert svg.value.status_code == 415


def test_attachment_download_is_authorized_and_never_inline_cached():
    assert "_require_member(db, int(row[\"conversation_id\"]), user)" in INTERNAL
    assert 'content_disposition_type="attachment"' in INTERNAL
    assert '"X-Content-Type-Options": "nosniff"' in INTERNAL
    assert '"Cache-Control": "private, no-store"' in INTERNAL


def test_invite_accept_never_returns_plaintext_password_and_verifies_existing_user():
    team = (BACKEND_ROOT / "app/api/team.py").read_text(encoding="utf-8")
    assert "verify_password(password, existing.password_hash)" in team
    assert 'Email не совпадает с приглашением' in team
    assert 'Пароль должен быть не короче 8 символов' in team
    accept_block = team.split('def accept_invite(', 1)[1].split('@router.get("/memberships")', 1)[0]
    assert '"password":password' not in accept_block
    assert '"password": password' not in accept_block


def test_security_evidence_secrets_fail_closed_without_config(monkeypatch):
    from app.services import auth_sessions, messenger_legal

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("FERNET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MESSENGER_SESSION_SECRET_MISSING"):
        auth_sessions._secret()
    with pytest.raises(RuntimeError, match="MESSENGER_LEGAL_EVIDENCE_SECRET_MISSING"):
        messenger_legal._evidence_hash("ip:", "127.0.0.1")


def test_messenger_legacy_tokens_must_upgrade_to_server_side_session():
    from app.api.legal import _require_messenger_server_session

    class LegacyUser:
        _auth_session_id = None

    class SessionUser:
        _auth_session_id = "qa-session-id"

    with pytest.raises(HTTPException) as legacy:
        _require_messenger_server_session(LegacyUser())
    assert legacy.value.status_code == 401
    assert isinstance(legacy.value.detail, dict)
    assert legacy.value.detail.get("code") == "messenger_session_upgrade_required"
    assert _require_messenger_server_session(SessionUser()) == "qa-session-id"


def test_messenger_webpush_payload_is_privacy_safe_by_default():
    notifications = (BACKEND_ROOT / "app/services/lead_notifications.py").read_text(encoding="utf-8")
    assert 'messenger_private = str(out.get("event_type") or "")' in notifications
    assert 'safe_body = "Откройте BORIS Messenger, чтобы посмотреть обновление."' in notifications
    assert '"body": safe_body' in notifications
    assert '"privacy_mode": "private" if messenger_private else "standard"' in notifications


def test_messenger_rejects_legacy_jwt_without_revocable_server_session():
    from types import SimpleNamespace
    from app.api.legal import _require_messenger_server_session

    with pytest.raises(HTTPException) as legacy:
        _require_messenger_server_session(SimpleNamespace(_auth_session_id=None))
    assert legacy.value.status_code == 401
    assert legacy.value.detail["code"] == "messenger_session_upgrade_required"

    sid = _require_messenger_server_session(SimpleNamespace(_auth_session_id="session-123"))
    assert sid == "session-123"
