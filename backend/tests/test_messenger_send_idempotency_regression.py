from pathlib import Path

INBOX = Path(__file__).parents[1] / "app" / "api" / "inbox_slots.py"


def test_inbox_send_idempotency_finish_preserves_payload_hash():
    source = INBOX.read_text(encoding="utf-8")
    start = source.index("def _send_idempotency_finish")
    end = source.index('@router.post("/send")', start)
    block = source[start:end]
    assert "payload_hash" in block
    assert 'saved.get("payload_hash")' in block
    assert 'value["payload_hash"] = payload_hash' in block


def test_employee_reply_author_respects_db_length_contract():
    source = INBOX.read_text(encoding="utf-8")
    assert '(getattr(user, "email", None) or "")[:24]' in source
