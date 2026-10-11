#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT/'app/messenger/page.tsx').read_text(encoding='utf-8')
SW = (ROOT/'public/sw.js').read_text(encoding='utf-8')
MANIFEST = (ROOT/'app/messenger/manifest.webmanifest/route.ts').read_text(encoding='utf-8')
NEXT_CONFIG = (ROOT/'next.config.ts').read_text(encoding='utf-8')
BACKEND = ROOT.parent/'backend'
if not (BACKEND/'app/api/internal_messenger.py').is_file():
    BACKEND = Path('/root/BORIS/backend')
if not (BACKEND/'app/api/internal_messenger.py').is_file():
    raise SystemExit(f"MESSENGER_PWA_PUSH_AUDIT_BACKEND_MISSING={BACKEND}")
INTERNAL = (BACKEND/'app/api/internal_messenger.py').read_text(encoding='utf-8')
INBOX = (BACKEND/'app/api/inbox_slots.py').read_text(encoding='utf-8')
CRM = (BACKEND/'app/api/crm.py').read_text(encoding='utf-8')
NOTIFY = (BACKEND/'app/services/lead_notifications.py').read_text(encoding='utf-8')
checks = {
 'MANIFEST_STANDALONE': 'display: "standalone"' in MANIFEST,
 'MANIFEST_SCOPE': 'scope: "/messenger"' in MANIFEST,
 'MESSENGER_ICON': '"/messenger/icon.svg"' in MANIFEST,
 'OFFLINE_SHELL': 'MESSENGER_OFFLINE_URL = "/messenger-offline.html"' in SW,
 'SW_PUSH': 'self.addEventListener("push"' in SW,
 'SW_DEEPLINK': 'self.addEventListener("notificationclick"' in SW and 'client.navigate(target)' in SW,
 'SW_UPDATE': 'BORIS_SKIP_WAITING' in SW and 'boris-pwa-update-ready' in PAGE,
 'MEDIA_PERMISSION_POLICY': 'camera=(self), microphone=(self)' in NEXT_CONFIG,
 'PWA_UPDATE_MEDIA_CALL_GUARD': (not ('RTCPeerConnection' in PAGE or 'videoState' in PAGE)) or ('voiceState !== \"idle\" || videoState !== \"idle\"' in PAGE and 'if (activeCall)' in PAGE),
 'INSTALL_PROMPT': 'beforeinstallprompt' in PAGE and 'installMessenger' in PAGE,
 'PUSH_PERMISSION_USER_ACTION': 'Notification.requestPermission()' in PAGE,
 'PUSH_SUBSCRIBE': '/api/lead-notifications/subscribe' in PAGE,
 'PUSH_UNSUBSCRIBE': '/api/lead-notifications/unsubscribe' in PAGE,
 'INTERNAL_PUSH_TARGETS': '"target_user_ids": targets' in INTERNAL,
 'PUSH_TARGET_FILTER': '"target_user_ids" in payload' in NOTIFY,
 'INTERNAL_DEEPLINK': 'internal_conversation_id=' in INTERNAL,
 'CALL_PUSH_TARGETS': (
     '\"internal_call\"' in INTERNAL
     and '\"target_user_ids\": list(call.get(\"target_user_ids\")' in INTERNAL
     and '[call[\"peer_user_id\"]]' in INTERNAL
     and 'if call.get(\"peer_user_id\") else []' in INTERNAL
 ),
 'CALL_PUSH_DEEPLINK': "&call_id={call['call_id']}" in INTERNAL and "internal_conversation_id={call['conversation_id']}" in INTERNAL,
 'CLIENT_ASSIGNMENT_PUSH': '"client_assigned"' in INBOX and 'client_chat_id=' in INBOX,
 'CRM_TASK_PUSH': '"crm_task"' in CRM and 'task_id=' in CRM,
 'CLIENT_DEEPLINK_UI': 'params.get("client_chat_id")' in PAGE and 'params.get("task_id")' in PAGE,
 'INCOMING_CLIENT_WEBPUSH_DEEPLINK': 'messenger_url = "/messenger?account_id=%s&client_chat_id=%s"' in NOTIFY and 'payload.get("messenger_url") or out.get("url") or "/messages"' in NOTIFY,
 'CRM_TASK_REMINDER_DEEPLINK': 'task_id={int(r[\'id\'])}' in NOTIFY and 'client_chat_id={r.get(\'avito_chat_id\') or \'\'}' in NOTIFY,
}
failed=[k for k,v in checks.items() if not v]
print('MESSENGER_PWA_PUSH_AUDIT')
for k,v in checks.items(): print(k+'='+('PASS' if v else 'FAIL'))
print('FAILURES='+str(len(failed)))
if failed: sys.exit(1)
print('PWA_STATIC_CONTRACT=PASS')
print('PUSH_STATIC_CONTRACT=PASS')
