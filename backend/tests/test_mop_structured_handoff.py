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

    def test_real_human_outgoing_no_longer_reenables_mop(self):
        from datetime import datetime, timezone
        from app.api import messenger
        mop_body = "Передаю специалисту."
        human_body = "Мария: посмотрела варианты, отправляю подборку."
        db = self._Db([
            self._Result(first={"id": 10, "sent_at": datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc), "updated_at": None}),
            self._Result(rows=[(mop_core.text_hash(mop_body),)]),
            self._Result(rows=[(human_body, 1788879800)]),
        ])
        # Legacy handoff helper may consider the original MOP->human transition
        # complete, but chat-level MOP_HUMAN_TAKEOVER_V1 keeps the employee as
        # owner until an explicit returned_to_ai action. This regression test
        # documents that a human outgoing is never a signal to auto-resume MOP.
        state = messenger._mop_unresolved_chat_handoff(db, "acc", "chat")
        self.assertIsNone(state)


class HumanTakeoverPolicyTests(unittest.TestCase):
    def test_do_send_blocks_when_employee_owns_chat(self):
        from unittest.mock import patch
        current = {
            "id": 77,
            "account_id": "acc",
            "avito_chat_id": "chat",
            "status": "draft_ready",
            "reply_text": "Автоматический ответ",
            "avito_message_id": "in-1",
        }
        with patch.object(mop_core, "_draft", return_value=current), \
             patch("app.services.reliability.module_blocked", return_value=(False, None)), \
             patch.object(mop_core, "human_takeover_state", return_value={"active": True, "reason": "employee_outgoing"}), \
             patch.object(mop_core, "set_status") as set_status, \
             patch.object(mop_core, "claim_for_send") as claim:
            sent, note = mop_core.do_send(object(), 77, "account_autopilot", channel="mop_autopilot")
        self.assertFalse(sent)
        self.assertIn("human_takeover_active", note)
        claim.assert_not_called()
        self.assertTrue(set_status.called)


class HumanTakeoverStateUnitTests(unittest.TestCase):
    class _Result:
        def __init__(self, first=None, rows=None):
            self._first = first
            self._rows = rows or []
        def mappings(self): return self
        def first(self): return self._first
        def fetchall(self): return self._rows
    class _Db:
        def __init__(self, results): self._results = iter(results)
        def execute(self, *args, **kwargs): return next(self._results)

    def test_employee_outgoing_after_return_locks_chat(self):
        from datetime import datetime, timezone
        returned = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
        human = datetime(2026, 9, 10, 9, 5, tzinfo=timezone.utc)
        db = self._Db([
            self._Result(first={"at": returned, "actor_type": "user", "actor_id": "7"}),
            self._Result(first={"sent_at": human, "user_id": 7, "text": "Отвечаю сам"}),
        ])
        state = mop_core.human_takeover_state(db, "acc", "chat")
        self.assertTrue(state["active"])
        self.assertEqual(state["reason"], "employee_outgoing")

    def test_explicit_return_after_employee_reply_clears_lock(self):
        from datetime import datetime, timezone
        returned = datetime(2026, 9, 10, 9, 10, tzinfo=timezone.utc)
        human = datetime(2026, 9, 10, 9, 5, tzinfo=timezone.utc)
        db = self._Db([
            self._Result(first={"at": returned, "actor_type": "user", "actor_id": "7"}),
            self._Result(first={"sent_at": human, "user_id": 7, "text": "Отвечаю сам"}),
            self._Result(rows=[]),
            self._Result(rows=[]),
            self._Result(first=None),
            self._Result(rows=[]),
        ])
        state = mop_core.human_takeover_state(db, "acc", "chat")
        self.assertFalse(state["active"])

    def test_system_returned_to_ai_cannot_clear_employee_lock(self):
        from datetime import datetime, timezone
        human = datetime(2026, 9, 10, 9, 5, tzinfo=timezone.utc)
        db = self._Db([
            # The canonical SQL filters actor_type to user/telegram_user,
            # so a system returned_to_ai is intentionally invisible here.
            self._Result(first=None),
            self._Result(first={"sent_at": human, "user_id": 7, "text": "Отвечаю сам"}),
        ])
        state = mop_core.human_takeover_state(db, "acc", "chat")
        self.assertTrue(state["active"])
        self.assertEqual(state["reason"], "employee_outgoing")

    def test_telegram_user_return_clears_employee_lock(self):
        from datetime import datetime, timezone
        returned = datetime(2026, 9, 10, 9, 10, tzinfo=timezone.utc)
        human = datetime(2026, 9, 10, 9, 5, tzinfo=timezone.utc)
        db = self._Db([
            self._Result(first={"at": returned, "actor_type": "telegram_user", "actor_id": "7"}),
            self._Result(first={"sent_at": human, "user_id": 7, "text": "Отвечаю сам"}),
            self._Result(rows=[]),
            self._Result(rows=[]),
            self._Result(first=None),
            self._Result(rows=[]),
        ])
        state = mop_core.human_takeover_state(db, "acc", "chat")
        self.assertFalse(state["active"])

    def test_direct_avito_old_human_reply_is_cleared_by_new_client_inbound(self):
        db = self._Db([
            self._Result(first=None),  # returned_to_ai
            self._Result(first=None),  # attributed Unified Inbox outgoing
            self._Result(first=None),  # explicit TAKE
            self._Result(rows=[]),     # MOP sent hashes
            self._Result(first=(200,)),  # newest real client inbound
            self._Result(rows=[("старый ручной ответ", 100)]),
        ])
        state = mop_core.human_takeover_state(db, "acc", "chat")
        self.assertFalse(state["active"])
        self.assertEqual(state["reason"], "no_human_takeover")

    def test_direct_avito_human_reply_after_latest_inbound_blocks_mop(self):
        db = self._Db([
            self._Result(first=None),
            self._Result(first=None),
            self._Result(first=None),
            self._Result(rows=[]),
            self._Result(first=(100,)),
            self._Result(rows=[("новый ручной ответ", 200)]),
        ])
        state = mop_core.human_takeover_state(db, "acc", "chat")
        self.assertTrue(state["active"])
        self.assertEqual(state["reason"], "external_human_outgoing")
        self.assertEqual(state["latest_client_inbound_epoch"], 100)


if __name__ == "__main__":
    unittest.main()


def test_begin_incoming_human_takeover_never_starts_ai():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = ("new",)
    with patch.object(mop_core, "human_takeover_state", return_value={"active": True, "reason": "employee_outgoing"}), \
         patch.object(mop_core, "create_draft", return_value=(901, True)), \
         patch.object(mop_core, "set_status", return_value=({"id": 901}, "new")) as set_status:
        out = mop_core.begin_incoming(
            db, "acc", "chat", "msg-1", "клиент написал снова", ai_enabled=True
        )
    assert out["status"] == "human_required"
    assert out["need_generate"] is False
    assert out["no_reply_reason"] == "human_takeover_active"
    assert set_status.call_args.args[2] == "human_required"


def test_human_takeover_return_query_allows_only_real_user_action():
    import inspect
    src = inspect.getsource(mop_core.human_takeover_state)
    assert "e.event='returned_to_ai'" in src
    assert "COALESCE(e.actor_type,'') IN ('user','telegram_user')" in src
    assert "provider_wait_retry" not in src
