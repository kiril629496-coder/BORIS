from __future__ import annotations
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
from app.api import inbox_daily as report
from app.api import internal_messenger
from app.models.user import User
from app.services import messenger_idempotency
from app.services.platform_roles import PLATFORM_OWNER_ID

DAY = dt.date(2026, 10, 8)
CID = 88
OWNER = int(PLATFORM_OWNER_ID)


class Rows:
    def __init__(self, rows=()): self.rows = list(rows)
    def scalar(self): return self.rows[0] if self.rows else None
    def mappings(self): return self
    def scalars(self): return self
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows


class Query:
    def __init__(self, state): self.state = state
    def filter(self, *args): return self
    def first(self): return self.state.owner


class State:
    def __init__(self):
        self.owner = User(id=OWNER, email="owner@example.invalid")
        self.members = [OWNER]
        self.conversation_id = CID
        self.storage = {}
        self.receipts = {}
        self.messages = []
        self.attempts = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_before = None
        self.fail_after = None
        self.response_status = None
        self.receipt_response = None
        self.add_member_after_first = False
        self.databases = []

    def factory(self):
        db = FakeDB(self)
        self.databases.append(db)
        return db

    def send(self, conversation_id, body, user):
        # Delivery is offline; use the real schema and canonical receipt code.
        # This also proves the frozen plan committed before any send call.
        assert isinstance(user, User) and user is self.owner
        assert self.storage, "first send preceded durable frozen plan"
        assert self.members == [OWNER], "sender invoked for a non-private group"
        key = str(body.client_message_id)
        self.attempts.append({"id": key, "body": body.body, "conversation_id": conversation_id, "user_id": user.id})
        if self.fail_before == len(self.messages) + 1:
            self.fail_before = None
            raise RuntimeError("simulated transport interruption before commit")
        if self.response_status is not None:
            return {"status": self.response_status}
        db = self.factory()
        receipt, replay = messenger_idempotency.begin(db, conversation_id, user.id, body)
        if replay is not None:
            return replay
        message_id = 100 + len(self.messages)
        message = {
            "id": message_id, "conversation_id": conversation_id,
            "sender_user_id": user.id, "body": body.body.strip(), "delivery_status": "DELIVERED",
        }
        result = {"status": "ok", "message": message}
        if self.receipt_response:
            result = copy.deepcopy(self.receipt_response)
        self.messages.append(message)
        messenger_idempotency.finish(db, receipt, result)
        db.commit()
        if self.add_member_after_first and len(self.messages) == 1:
            self.members.append(999)
        if self.fail_after == len(self.messages):
            self.fail_after = None
            raise RuntimeError("simulated connection loss after canonical message+receipt commit")
        return result


class FakeDB:
    def __init__(self, state):
        self.state = state
        self.pending_storage = {}
        self.pending_receipts = {}
        self.closed = False
    def query(self, model):
        assert model is User
        return Query(self.state)
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        s = self.state
        if "pg_advisory_xact_lock" in sql:
            return Rows([None])
        if "SELECT c.id FROM internal_conversations" in sql:
            return Rows([s.conversation_id] if s.conversation_id and OWNER in s.members else [])
        if "SELECT user_id FROM internal_conversation_members" in sql:
            return Rows(sorted(s.members))
        if "SELECT id FROM internal_messages" in sql:
            prefix = params["prefix"][:-1]
            ids = [m["id"] for m in s.messages if m["conversation_id"] == params["c"]
                   and m["sender_user_id"] == params["u"] and m["body"].startswith(prefix)]
            return Rows([max(ids)] if ids else [])
        if "SELECT value FROM storage" in sql:
            value = s.storage.get((params["a"], params["k"]))
            return Rows([value] if value is not None else [])
        if "INSERT INTO storage" in sql:
            self.pending_storage[(params["a"], params["k"])] = params["v"]
            return Rows()
        if "SELECT request_hash,response_json FROM internal_message_receipts" in sql:
            value = s.receipts.get((params["c"], params["u"], params["k"]))
            return Rows([copy.deepcopy(value)] if value else [])
        if "INSERT INTO internal_message_receipts" in sql:
            self.pending_receipts[(params["c"], params["u"], params["k"])] = {
                "request_hash": params["h"], "response_json": json.loads(params["response"]),
            }
            return Rows()
        raise AssertionError("Unexpected SQL: " + sql)
    def commit(self):
        self.state.storage.update(self.pending_storage)
        self.state.receipts.update(self.pending_receipts)
        self.pending_storage.clear()
        self.pending_receipts.clear()
        self.state.commits += 1
    def rollback(self):
        self.pending_storage.clear()
        self.pending_receipts.clear()
        self.state.rollbacks += 1
    def close(self):
        self.rollback()
        self.closed = True


def sender(state, body, slot="daily"):
    with patch.object(report, "SessionLocal", side_effect=state.factory), patch.object(internal_messenger, "send_message", side_effect=state.send):
        return report._send_owner_report_to_messenger(body, DAY, slot)


class ChunkPlanTests(unittest.TestCase):
    def test_short_report_preserves_legacy_envelope(self):
        plan = report._owner_report_chunk_plan("<b>Клиент</b><br>Факт &amp; цель", DAY, "daily", CID, OWNER)
        report._owner_report_validate_chunk_plan(plan, DAY, "daily", CID, OWNER)
        self.assertEqual(len(plan["parts"]), 1)
        self.assertEqual(plan["parts"][0]["content"], "Клиент\nФакт & цель")
        self.assertEqual(plan["parts"][0]["footer"], "")
        self.assertEqual(plan["parts"][0]["body"], plan["report_prefix"] + "\nТолько подтверждённый факт; queued/prepared не считаются выполнением.\n\nКлиент\nФакт & цель")

    def test_realistic_oversize_report_preserves_every_character(self):
        text = "\n\n".join(f"Клиент {i}\n" + ("Показатели: факт 2 · план 7. " * 50) for i in range(100))
        plan = report._owner_report_chunk_plan(text, DAY, "evening", CID, OWNER)
        self.assertGreater(len(plan["parts"]), 2)
        self.assertEqual("".join(p["content"] for p in plan["parts"]), text.strip())
        report._owner_report_validate_chunk_plan(plan, DAY, "evening", CID, OWNER)
        for part in plan["parts"]:
            schema = internal_messenger.MessageCreate(body=part["body"])
            self.assertLessEqual(len(schema.body), 18000)
            self.assertEqual(schema.body, schema.body.strip())

    def test_long_unbroken_unicode_line_has_no_loss(self):
        text = ("🙂中文е\u0301🚀" * 13000) + "конец"
        plan = report._owner_report_chunk_plan(text, DAY, "daily", CID, OWNER)
        self.assertGreater(len(plan["parts"]), 2)
        self.assertEqual("".join(p["content"] for p in plan["parts"]), text)
        for part in plan["parts"]:
            self.assertLessEqual(len(part["body"]), 18000)
            part["body"].encode("utf-8")

    def test_whitespace_at_all_split_boundaries_survives_canonical_strip(self):
        text = "Начало" + (" " * 41000) + "\n\n\t" + ("данные\n\n" * 4000) + "конец"
        plan = report._owner_report_chunk_plan(text, DAY, "daily", CID, OWNER)
        delivered_content = []
        for part in plan["parts"]:
            actual = part["body"].strip()
            self.assertEqual(actual, part["body"])
            end = len(actual) - len(part["footer"]) if part["footer"] else len(actual)
            delivered_content.append(actual[len(part["header"]):end])
        self.assertEqual("".join(delivered_content), text)

    def test_multi_part_cannot_match_legacy_broad_already_sent_prefix(self):
        plan = report._owner_report_chunk_plan("x" * 40000, DAY, "daily", CID, OWNER)
        for part in plan["parts"]:
            self.assertFalse(part["body"].startswith(plan["report_prefix"]))
        state = State(); state.fail_before = 2
        partial = sender(state, "x" * 40000)
        self.assertEqual(partial["status"], "error")
        old_lookup = state.factory().execute("SELECT id FROM internal_messages", {
            "c": CID, "u": OWNER, "prefix": plan["report_prefix"] + "%",
        }).scalar()
        self.assertIsNone(old_lookup)

    def test_first_part_keeps_existing_message_identity(self):
        from uuid import uuid5, NAMESPACE_URL
        expected = str(uuid5(NAMESPACE_URL, f"boris-owner-report:auto:v1:{DAY.isoformat()}:daily:conversation:{CID}"))
        self.assertEqual(report._owner_report_part_message_id(DAY, "daily", CID, 1), expected)

    def test_corrupt_or_incomplete_frozen_plan_rejected(self):
        original = report._owner_report_chunk_plan("x" * 40000, DAY, "daily", CID, OWNER)
        for mutate in [
            lambda p: p.update(slot="morning"),
            lambda p: p.update(body_sha256="wrong"),
            lambda p: p["parts"].pop(),
            lambda p: p["parts"][0].update(body="wrong"),
            lambda p: p["parts"][0].update(client_message_id=p["parts"][1]["client_message_id"]),
        ]:
            bad = copy.deepcopy(original); mutate(bad)
            with self.assertRaises(ValueError):
                report._owner_report_validate_chunk_plan(bad, DAY, "daily", CID, OWNER)


class FrozenDeliveryTests(unittest.TestCase):
    def test_all_parts_commit_and_repeat_does_not_send_duplicates(self):
        state = State()
        result = sender(state, "Данные\n" * 11000)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["parts_total"], 2)
        self.assertEqual(result["parts_delivered"], result["parts_total"])
        self.assertEqual(len(state.messages), result["parts_total"])
        self.assertFalse(result["already_sent"])
        old_attempts = copy.deepcopy(state.attempts)
        again = sender(state, "Данные\n" * 11000)
        self.assertEqual(again["status"], "ok")
        self.assertTrue(again["already_sent"])
        self.assertEqual(again["message_ids"], result["message_ids"])
        self.assertEqual(state.attempts, old_attempts)

    def test_partial_failure_new_process_resumes_frozen_plan_with_same_ids(self):
        state = State(); state.fail_before = 2
        original = "Старые подтверждённые данные\n" * 4000
        first = sender(state, original)
        self.assertEqual(first["status"], "error")
        self.assertTrue(first["partial"])
        self.assertEqual(first["parts_delivered"], 1)
        storage_before = copy.deepcopy(state.storage)
        stored = json.loads(next(iter(state.storage.values())))
        ids = [p["client_message_id"] for p in stored["parts"]]
        # New sessions, no retained request/process state, radically shorter new report.
        second = sender(state, "Совсем другие показатели после обновления")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["parts_total"], len(ids))
        self.assertEqual(state.storage, storage_before)
        self.assertEqual([m["body"] for m in state.messages], [p["body"] for p in stored["parts"]])
        self.assertEqual("".join(p["content"] for p in stored["parts"]), original.strip())
        self.assertEqual(state.attempts[0]["id"], ids[0])
        self.assertEqual(state.attempts[1]["id"], state.attempts[2]["id"])
        self.assertEqual(len(state.messages), len(ids))

    def test_committed_send_with_lost_response_resumes_from_atomic_receipt(self):
        state = State(); state.fail_after = 1
        first = sender(state, "x" * 40000)
        self.assertEqual(first["status"], "error")
        self.assertEqual(len(state.messages), 1)
        second = sender(state, "changed")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(len(state.messages), second["parts_total"])
        self.assertEqual(len(state.attempts), second["parts_total"])

    def test_legacy_unchunked_delivery_is_still_already_sent(self):
        state = State()
        old = report._owner_report_chunk_plan("исторический отчёт", DAY, "daily", CID, OWNER)
        state.messages.append({"id": 41, "conversation_id": CID, "sender_user_id": OWNER,
                               "body": old["parts"][0]["body"], "delivery_status": "DELIVERED"})
        result = sender(state, "x" * 80000)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["already_sent"])
        self.assertTrue(result["legacy_unchunked"])
        self.assertEqual(result["message_id"], 41)
        self.assertFalse(state.attempts)
        self.assertFalse(state.storage)

    def test_other_member_blocks_before_storage_and_send(self):
        state = State(); state.members.append(999)
        result = sender(state, "x" * 40000)
        self.assertEqual(result["code"], "owner_messenger_channel_not_private")
        self.assertFalse(state.attempts)
        self.assertFalse(state.storage)

    def test_member_added_during_delivery_stops_remaining_parts(self):
        state = State(); state.add_member_after_first = True
        result = sender(state, "x" * 40000)
        self.assertEqual(result["code"], "owner_messenger_channel_not_private")
        self.assertEqual(result["parts_delivered"], 1)
        self.assertEqual(len(state.messages), 1)
        self.assertTrue(result["partial"])

    def test_missing_actual_owner_object_blocks(self):
        state = State(); state.owner = None
        result = sender(state, "x" * 40000)
        self.assertEqual(result["code"], "owner_messenger_channel_missing")
        self.assertFalse(state.attempts)

    def test_wrong_owner_object_blocks(self):
        state = State(); state.owner = User(id=999, email="other@example.invalid")
        result = sender(state, "x" * 40000)
        self.assertEqual(result["code"], "owner_messenger_channel_missing")
        self.assertFalse(state.attempts)

    def test_corrupted_existing_plan_not_replaced_or_sent(self):
        state = State()
        key = f"owner_report_messenger_parts:v1:{DAY.isoformat()}:daily:conversation:{CID}"
        state.storage[(report.OWNER_REPORT_MESSENGER_ACCOUNT_ID, key)] = "{not json"
        previous = copy.deepcopy(state.storage)
        result = sender(state, "x" * 40000)
        self.assertEqual(result["code"], "owner_messenger_frozen_plan_invalid")
        self.assertEqual(state.storage, previous)
        self.assertFalse(state.attempts)

    def test_failed_status_never_marks_complete(self):
        state = State(); state.response_status = "error"
        result = sender(state, "x" * 40000)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["parts_delivered"], 0)

    def test_incomplete_or_wrong_conversation_receipt_never_marks_complete(self):
        for response in [
            {"status": "in_progress"},
            {"status": "error", "message": {"id": 5}},
            {"status": "ok", "message": {"id": 5, "conversation_id": CID, "delivery_status": "QUEUED"}},
            {"status": "ok", "message": {"id": 5, "conversation_id": CID + 1, "delivery_status": "DELIVERED"}},
            {"status": "ok", "message": {"id": 5, "conversation_id": CID, "sender_user_id": OWNER + 1, "delivery_status": "DELIVERED"}},
        ]:
            state = State(); state.receipt_response = response
            result = sender(state, "x" * 40000)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["parts_delivered"], 0)
            self.assertEqual(len(state.messages), 1)

    def test_receipt_scope_does_not_accept_another_user_or_conversation(self):
        state = State()
        plan = report._owner_report_chunk_plan("x" * 40000, DAY, "daily", CID, OWNER)
        first = plan["parts"][0]
        request = internal_messenger.MessageCreate(client_message_id=first["client_message_id"], body=first["body"])
        for other_c, other_u in ((CID + 1, OWNER), (CID, OWNER + 1)):
            db = state.factory()
            receipt, _ = messenger_idempotency.begin(db, other_c, other_u, request)
            messenger_idempotency.finish(db, receipt, {"status": "ok", "message": {"id": 5, "conversation_id": other_c, "delivery_status": "DELIVERED"}})
            db.commit()
        result = sender(state, "x" * 40000)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(state.messages), result["parts_total"])
        self.assertNotIn(5, result["message_ids"])

    def test_different_slots_use_different_frozen_plans_and_ids(self):
        state = State()
        first = sender(state, "x" * 40000, slot="morning")
        second = sender(state, "y" * 40000, slot="evening")
        self.assertEqual(first["status"], "ok"); self.assertEqual(second["status"], "ok")
        self.assertNotEqual(first["frozen_plan_key"], second["frozen_plan_key"])
        self.assertEqual(len(state.storage), 2)
        self.assertEqual(len({a["id"] for a in state.attempts}), len(state.attempts))


if __name__ == "__main__":
    unittest.main()
