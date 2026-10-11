from pathlib import Path

ROOT = Path("/root/BORIS")
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"
SETTINGS = ROOT / "frontend/app/messenger/components/SettingsPanel.tsx"
SW = ROOT / "frontend/public/sw.js"
MANIFEST = ROOT / "frontend/app/messenger/manifest.webmanifest/route.ts"
OFFLINE = ROOT / "frontend/public/messenger-offline.html"
INTERNAL = ROOT / "backend/app/api/internal_messenger.py"
PUSH = ROOT / "backend/app/services/lead_notifications.py"


def test_internal_push_targets_members_except_sender_and_deep_links_to_conversation():
    source = INTERNAL.read_text(encoding="utf-8")
    assert "SELECT user_id FROM internal_conversation_members WHERE conversation_id=:c AND user_id<>:u" in source
    assert '"target_user_ids": targets' in source
    assert 'internal_conversation_id={conversation_id}' in source
    push = PUSH.read_text(encoding="utf-8")
    assert '"target_user_ids" in payload' in push
    assert "target_user_ids = {int(value)" in push


def test_service_worker_handles_messenger_push_deep_link_and_offline_shell():
    source = SW.read_text(encoding="utf-8")
    assert 'self.addEventListener("push"' in source
    assert 'self.addEventListener("notificationclick"' in source
    assert 'self.clients.openWindow(target)' in source
    assert 'MESSENGER_OFFLINE_URL = "/messenger-offline.html"' in source
    assert 'url.pathname === "/messenger" || url.pathname.startsWith("/messenger/")' in source
    assert OFFLINE.exists()


def test_messenger_manifest_is_standalone_blue_business_communication_app():
    source = MANIFEST.read_text(encoding="utf-8")
    assert "BORIS Messenger" in source
    assert "standalone" in source
    assert "#1d4ed8" in source
    assert "communication" in source
    assert "/messenger/icon.svg" in source


def test_messenger_has_install_push_update_and_offline_draft_ux():
    source = FRONTEND.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")
    assert "beforeinstallprompt" in source
    assert "Notification.requestPermission()" in source
    assert "/api/lead-notifications/subscribe" in source
    assert "/api/lead-notifications/unsubscribe" in source
    assert "Доступна новая версия" in settings
    assert "applyPwaUpdate" in source
    assert '"boris_messenger_client_draft:"' in source
    assert 'realtimeState === "offline" ? "Нет связи"' in source


def test_assignment_and_crm_task_push_deep_link_to_exact_messenger_context():
    frontend = FRONTEND.read_text(encoding="utf-8")
    inbox = (ROOT / "backend/app/api/inbox_slots.py").read_text(encoding="utf-8")
    crm = (ROOT / "backend/app/api/crm.py").read_text(encoding="utf-8")

    assert 'params.get("client_chat_id")' in frontend
    assert 'params.get("task_id")' in frontend
    assert 'setMobilePane(requestedTaskId ? "crm" : "chat")' in frontend

    assert '"client_assigned"' in inbox
    assert '"target_user_ids": [uid] if uid else []' in inbox
    assert 'client_chat_id={req.avito_chat_id}' in inbox

    assert '"crm_task"' in crm
    assert '"target_user_ids": [uid] if uid else []' in crm
    assert 'client_chat_id={body.chat_id}&task_id={new_id}' in crm
    assert "observe_suppressed(__name__, _push_exc" in crm



def test_incoming_client_webpush_prefers_messenger_deep_link_without_changing_other_channels():
    push = PUSH.read_text(encoding="utf-8")
    assert 'messenger_url = "/messenger?account_id=%s&client_chat_id=%s"' in push
    assert '"messenger_url": messenger_url' in push
    assert 'payload.get("messenger_url") or out.get("url") or "/messages"' in push



def test_crm_task_due_and_overdue_webpush_deep_link_to_messenger_crm():
    push = PUSH.read_text(encoding="utf-8")
    assert 'f"crm-task:{r[\'id\']}:{event_key_suffix}"' in push
    assert '"messenger_url": f"/messenger?account_id={account_id}&client_chat_id={r.get(\'avito_chat_id\') or \'\'}&task_id={int(r[\'id\'])}"' in push


def test_apple_webpush_bypasses_shared_proxy_without_disabling_tls():
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.lead_notifications import _webpush_requests_session

    apple = _webpush_requests_session({"endpoint": "https://web.push.apple.com/QATEST"})
    try:
        assert apple is not None
        assert apple.trust_env is False
        assert apple.verify is True
    finally:
        if apple is not None:
            apple.close()

    non_apple = _webpush_requests_session({"endpoint": "https://fcm.googleapis.com/fcm/send/QATEST"})
    assert non_apple is None
