import unittest
from unittest.mock import patch

from app import mop_core


class StructuredHandoffStateTests(unittest.TestCase):
    def test_manual_mode_enters_human_required(self):
        calls = []
        def fake_set_status(db, draft_id, target, allowed_from, event, **kwargs):
            calls.append((target, tuple(allowed_from), event, kwargs))
            return ({"id": draft_id}, "analyzing")
        with patch.object(mop_core, "set_status", side_effect=fake_set_status),              patch.object(mop_core, "push_card", return_value="101"):
            result = mop_core.finish_incoming(
                object(), 7, "Передам вопрос менеджеру.",
                usage={"model": "test"},
                analysis={"human_handoff": True, "handoff_reason": "нужен расчет"},
                notify_manager=True,
            )
        self.assertEqual(result, "human_required")
        self.assertEqual(calls[0][0], "human_required")
        self.assertEqual(calls[0][2], "handed_to_human")

    def test_autopilot_keeps_sendable_state_before_safe_reply(self):
        calls = []
        def fake_set_status(db, draft_id, target, allowed_from, event, **kwargs):
            calls.append((target, tuple(allowed_from), event, kwargs))
            return ({"id": draft_id}, "analyzing")
        with patch.object(mop_core, "set_status", side_effect=fake_set_status):
            result = mop_core.finish_incoming(
                object(), 8, "Передам вопрос менеджеру.",
                usage={"model": "test"},
                analysis={"human_handoff": True, "handoff_reason": "нет актуальной цены"},
                notify_manager=False,
            )
        self.assertEqual(result, "draft_ready")
        self.assertEqual(calls[0][0], "draft_ready")


class ChatHandoffLockTests(unittest.TestCase):
    class _Result:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = rows or []
        def mappings(self):
            return self
        def first(self):
            return self._first
        def fetchall(self):
            return self._rows

    class _Db:
        def __init__(self, results):
            self._results = iter(results)
        def execute(self, *args, **kwargs):
            return next(self._results)

    def test_later_mop_reply_does_not_resolve_handoff(self):
        from datetime import datetime, timezone
        from app.api import messenger
        body = "Специалист подготовит подборку и пришлет сюда."
        db = self._Db([
            self._Result(first={"id": 10, "sent_at": datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc), "updated_at": None}),
            self._Result(rows=[(mop_core.text_hash(body),)]),
            self._Result(rows=[(body, 1788879700)]),
        ])
        state = messenger._mop_unresolved_chat_handoff(db, "acc", "chat")
        self.assertIsNotNone(state)
        self.assertTrue(state["pending"])

    def test_real_human_outgoing_resolves_handoff(self):
        from datetime import datetime, timezone
        from app.api import messenger
        mop_body = "Передаю специалисту."
        human_body = "Мария: посмотрела варианты, отправляю подборку."
        db = self._Db([
            self._Result(first={"id": 10, "sent_at": datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc), "updated_at": None}),
            self._Result(rows=[(mop_core.text_hash(mop_body),)]),
            self._Result(rows=[(human_body, 1788879800)]),
        ])
        state = messenger._mop_unresolved_chat_handoff(db, "acc", "chat")
        self.assertIsNone(state)


if __name__ == "__main__":
    unittest.main()
