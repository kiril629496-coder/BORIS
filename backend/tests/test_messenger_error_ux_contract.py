from pathlib import Path

ROOT = Path('/root/BORIS')
FRONTEND = ROOT / 'frontend/app/messenger/page.tsx'

def test_api_errors_are_human_readable_and_not_raw_http_only():
    source = FRONTEND.read_text(encoding='utf-8')
    assert 'response.status === 402' in source
    assert 'response.status === 403' in source
    assert 'response.status === 409' in source
    assert 'response.status === 429' in source
    assert 'response.status >= 500' in source
    assert 'Тариф этого аккаунта закончился' in source
    assert 'Слишком много действий' in source
    assert 'BORIS временно недоступен' in source

def test_operational_feedback_uses_toast_with_close_control():
    source = FRONTEND.read_text(encoding='utf-8')
    assert 'const [toast, setToast]' in source
    assert 'role="status"' in source
    assert 'aria-label="Закрыть уведомление"' in source
    assert 'setToast(null)' in source

def test_internal_reactions_offer_compact_picker():
    source = FRONTEND.read_text(encoding='utf-8')
    assert 'reactionMenuMessageId' in source
    assert '["👍","✅","❤️","😂"]' in source
    assert 'toggleInternalReaction(message, reaction)' in source


def test_background_network_failures_do_not_surface_raw_failed_to_fetch_toast():
    source = FRONTEND.read_text(encoding='utf-8')
    assert 'function isTransientNetworkError(' in source
    assert 'failed to fetch' in source.lower()
    assert 'Внутренние чаты недоступны:' not in source
    assert 'Контекст диалога недоступен:' not in source
    assert 'CRM задачи аккаунта недоступны:' not in source
    assert "REALTIME_POLLING_FALLBACK_V1" in source
    assert '/api/internal-messenger/events?account_id=' in source
    assert 'setRealtimeState("online")' in source


def test_background_fetches_cannot_overwrite_realtime_transport_state():
    source = FRONTEND.read_text(encoding='utf-8')
    assert 'setRealtimeState(navigator.onLine ? "reconnecting" : "offline")' not in source
    assert 'REALTIME_POLLING_FALLBACK_V1' in source
    assert 'setRealtimeState("online")' in source
