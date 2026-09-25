import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_store as store
from app.services.command_contract import CommandInput


class CommandCenterExactlyOnceTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        row = self.db.execute(text(
            "select account_id, owner_user_id from accounts "
            "where owner_user_id is not null and account_id is not null order by id limit 1"
        )).fetchone()
        if not row:
            self.skipTest("no account with owner")
        self.account_id = str(row[0])
        self.uid = int(row[1])
        self.user = SimpleNamespace(id=self.uid, role="owner")
        self.ids = []

    def tearDown(self):
        try:
            for cid in self.ids:
                # Audit rows reference the command UUID on installations where the
                # FK is not cascading, so delete them first when the table exists.
                try:
                    self.db.execute(text("delete from boris_command_events where command_id=cast(:c as uuid)"), {"c": cid})
                except Exception:
                    self.db.rollback()
                self.db.execute(text("delete from boris_commands where command_id=cast(:c as uuid)"), {"c": cid})
            self.db.commit()
        finally:
            self.db.close()

    def _new(self):
        cid = str(uuid.uuid4())
        self.ids.append(cid)
        return cid

    def _create(self, cid, status="parsed", operation="chat.smalltalk", units=0):
        created = store.create(self.db, cid, self.uid, "user", self.uid,
                               self.account_id, "text", raw_text="qa")
        self.assertTrue(created)
        self.db.execute(text(
            "update boris_commands set status=:s, operation=:o, risk_level='green',"
            " permission=null, units=:u where command_id=cast(:c as uuid)"
        ), {"s": status, "o": operation, "u": units, "c": cid})
        self.db.commit()

    def test_same_command_id_replays_without_second_parser_or_executor(self):
        cid = self._new()
        inp = CommandInput(command_id=cid, source="text", text="привет", account_id=self.account_id)
        parsed = {"intent": "chit_chat", "confidence": 1.0, "entities": {},
                  "summary": "Приветствие", "clarify_question": None}
        snap = {"operation": "chat.smalltalk", "risk_level": "green",
                "permission": None, "units": 0}
        with patch.object(flow.policy, "precheck", return_value=(("user", self.uid), None)), \
             patch.object(flow.parser, "parse_command", return_value=parsed) as parse_mock, \
             patch.object(flow.registry, "resolve_operation", return_value="chat.smalltalk"), \
             patch.object(flow.policy, "authorize_operation", return_value=(snap, None)), \
             patch.object(flow.registry, "needs_confirmation", return_value=False), \
             patch.object(flow.executor, "execute", return_value={"status": "ok", "answer": "Здравствуйте", "route": "/"}) as exec_mock:
            first = flow.start(self.db, self.user, inp)
            second = flow.start(self.db, self.user, inp)
        self.assertEqual(first["status"], "done")
        self.assertEqual(second["status"], "done")
        self.assertEqual(parse_mock.call_count, 1)
        self.assertEqual(exec_mock.call_count, 1)
        row = store.load(self.db, cid)
        self.assertEqual(row["status"], "done")

    def test_finish_claim_allows_only_one_executor_call(self):
        cid = self._new()
        self._create(cid, status="parsed")
        row = store.load(self.db, cid)
        with patch.object(flow.executor, "execute", return_value={"status": "ok", "answer": "ok", "route": "/"}) as exec_mock:
            first = flow._finish(self.db, self.user, row, cid)
            second = flow._finish(self.db, self.user, store.load(self.db, cid), cid)
        self.assertEqual(first["status"], "done")
        self.assertEqual(second["status"], "done")
        self.assertEqual(exec_mock.call_count, 1)

    def test_confirm_during_execution_never_expires_command(self):
        cid = self._new()
        self._create(cid, status="executing")
        snap = {"operation": "chat.smalltalk", "risk_level": "green",
                "permission": None, "units": 0}
        with patch.object(flow.policy, "authorize_operation", return_value=(snap, None)):
            out = flow.confirm(self.db, self.user, cid)
        self.assertEqual(out["status"], "executing")
        self.assertEqual(store.load(self.db, cid)["status"], "executing")

    def test_cancel_cannot_rewrite_done_or_executing(self):
        for st in ("done", "executing", "failed", "expired"):
            cid = self._new()
            self._create(cid, status=st)
            out = flow.cancel(self.db, self.user, cid)
            self.assertEqual(out["status"], st)
            self.assertEqual(store.load(self.db, cid)["status"], st)


    def test_replay_returns_original_answer_and_data(self):
        cid = self._new()
        inp = CommandInput(command_id=cid, source="text", text="привет", account_id=self.account_id)
        parsed = {"intent": "chit_chat", "confidence": 1.0, "entities": {},
                  "summary": "Приветствие", "clarify_question": None}
        snap = {"operation": "chat.smalltalk", "risk_level": "green",
                "permission": None, "units": 0}
        with patch.object(flow.policy, "precheck", return_value=(("user", self.uid), None)), \
             patch.object(flow.parser, "parse_command", return_value=parsed), \
             patch.object(flow.registry, "resolve_operation", return_value="chat.smalltalk"), \
             patch.object(flow.policy, "authorize_operation", return_value=(snap, None)), \
             patch.object(flow.registry, "needs_confirmation", return_value=False), \
             patch.object(flow.executor, "execute", return_value={"status": "ok", "answer": "Здравствуйте", "route": "/messages"}):
            first = flow.start(self.db, self.user, inp)
            second = flow.start(self.db, self.user, inp)
        self.assertEqual(first["answer"], "Здравствуйте")
        self.assertEqual(second["answer"], "Здравствуйте")
        self.assertEqual(second["data"]["route"], "/messages")

    def test_same_command_id_with_different_text_is_conflict(self):
        cid = self._new()
        base = CommandInput(command_id=cid, source="text", text="первая команда", account_id=self.account_id)
        other = CommandInput(command_id=cid, source="text", text="другая команда", account_id=self.account_id)
        parsed = {"intent": "chit_chat", "confidence": 1.0, "entities": {},
                  "summary": "Приветствие", "clarify_question": None}
        snap = {"operation": "chat.smalltalk", "risk_level": "green",
                "permission": None, "units": 0}
        with patch.object(flow.policy, "precheck", return_value=(("user", self.uid), None)), \
             patch.object(flow.parser, "parse_command", return_value=parsed), \
             patch.object(flow.registry, "resolve_operation", return_value="chat.smalltalk"), \
             patch.object(flow.policy, "authorize_operation", return_value=(snap, None)), \
             patch.object(flow.registry, "needs_confirmation", return_value=False), \
             patch.object(flow.executor, "execute", return_value={"status": "ok", "answer": "ok"}) as exec_mock:
            self.assertEqual(flow.start(self.db, self.user, base)["status"], "done")
            conflict = flow.start(self.db, self.user, other)
        self.assertEqual(conflict["status"], "failed")
        self.assertEqual(conflict["error_code"], "idempotency_conflict")
        self.assertEqual(exec_mock.call_count, 1)

    def test_store_create_conflict_is_non_destructive(self):
        cid = self._new()
        self.assertTrue(store.create(self.db, cid, self.uid, "user", self.uid,
                                     self.account_id, "text", raw_text="first"))
        self.assertFalse(store.create(self.db, cid, self.uid, "user", self.uid,
                                      self.account_id, "text", raw_text="second"))
        row = self.db.execute(text(
            "select raw_text from boris_commands where command_id=cast(:c as uuid)"), {"c": cid}).fetchone()
        self.assertEqual(row[0], "first")


if __name__ == "__main__":
    unittest.main()
