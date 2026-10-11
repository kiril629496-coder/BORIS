from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_plaintext_click_tracking_is_render_only_after_copy_guard():
    tracking=(ROOT/'app/services/email_tracking.py').read_text()
    queue=(ROOT/'app/services/email_queue.py').read_text()
    assert 'def render_text_click_tracking' in tracking
    assert 'return raw.replace(target,click_url)' in tracking
    guard_pos=queue.index('owner_queue_pre_send_guard')
    render_pos=queue.index('body = _tracking.render_text_click_tracking')
    smtp_pos=queue.index('send_outbound(',render_pos)
    assert guard_pos < render_pos < smtp_pos
    # The rendered click URL is kept in the local MIME body. Queue copy truth
    # remains guarded/stored separately and is not rewritten by this hook.
    hook=queue[render_pos:smtp_pos]
    assert 'UPDATE email_queue SET text_body' not in hook
