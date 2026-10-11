from unittest.mock import patch

from app.api import messenger


class _Mapped:
    def __init__(self, row):
        self.row = row
    def mappings(self):
        return self
    def first(self):
        return self.row


class _Db:
    def __init__(self, row):
        self.row = row
    def execute(self, *args, **kwargs):
        return _Mapped(self.row)
    def close(self):
        pass


def test_local_durable_context_recovers_latest_inbound_metadata():
    row = {
        "avito_message_id": "mid-1",
        "text": "8-905-535-11-69",
        "avito_created_at": 200,
        "item_id": "4820058777",
        "item_title": "Volvo XC60",
        "direction": "in",
    }
    with patch("app.db.session.SessionLocal", return_value=_Db(row)):
        out = messenger._mop_local_durable_chat_context(
            "7473337_04122", "chat-1", owner_user_id="owner-1"
        )
    assert out["id"] == "chat-1"
    assert out["last_message"]["id"] == "mid-1"
    assert out["last_message"]["direction"] == "in"
    assert out["context"]["value"]["id"] == "4820058777"
    assert out["_boris_local_durable_context"] is True


def test_local_durable_context_never_reopens_chat_if_latest_is_outgoing():
    row = {
        "avito_message_id": "mid-out",
        "text": "уже ответили",
        "avito_created_at": 201,
        "item_id": "1",
        "item_title": "item",
        "direction": "out",
    }
    with patch("app.db.session.SessionLocal", return_value=_Db(row)):
        out = messenger._mop_local_durable_chat_context(
            "acc", "chat", owner_user_id="owner"
        )
    assert out is None
