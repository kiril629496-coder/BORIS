"""Offline receipt and real-due failure cases; no database, HTTP or paid AI."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ast
import contextlib
import importlib.util
import io
import json
import sys
import types
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND / "posting_runner.py"
SPEC = importlib.util.spec_from_file_location(
    "social_vk_pacing_receipts_under_test", BACKEND / "app/services/social_delivery_receipts.py")
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


def new_intent(rid="new", protocol="SOCIAL_RECEIPT_V1"):
    return m.DeliveryIntent("u2", "vk", "-42", "wall_post", rid + " text", "asset", {
        "post_id": rid, "project_id": "dushi", "generation_key": "slot:" + rid,
        "created_at": "2026-10-08T06:00:00+00:00", "protocol": protocol,
    })


def admission_ok():
    return {"ok": True, "local_day": datetime.now(timezone.utc).date().isoformat(),
            "timezone_offset_hours": 0}


def admission_block():
    return {"ok": False, "reason": "SOCIAL_DAY_EVIDENCE_REQUIRED"}


class MemoryJournal:
    def __init__(self, check=None):
        self.rows = {}
        self.events = []
        self.check = check or (lambda intent, number: admission_ok())
        self.on_save = None

    def __call__(self, intent):
        owner = self

        class Journal:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def load(self):
                return deepcopy(owner.rows.get(intent.key))

            def save(self, row):
                if owner.on_save:
                    owner.on_save(row)
                owner.rows[intent.key] = deepcopy(row)

            def destination_admission(self):
                class Capacity:
                    number = 0

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return None

                    def check(self, now=None):
                        self.number += 1
                        owner.events.append((intent.context["post_id"], "capacity", self.number))
                        return owner.check(intent, self.number)

                return Capacity()

        return Journal()


def receipt_row(intent, status="uncertain", origin="legacy"):
    return {"protocol": m.PROTOCOL, "identity": intent.identity,
            "payload_hash": intent.payload_hash, "asset_sha256": intent.asset_sha256,
            "context": deepcopy(intent.context), "status": status, "origin": origin,
            "created_at": "2026-10-08T06:00:00+00:00", "prepared": {}}


class ReceiptInvocationProofTests(unittest.TestCase):
    def test_first_capacity_refusal_is_proven_zero_io(self):
        journal, calls = MemoryJournal(lambda *_: admission_block()), []
        out = m.guarded_delivery(new_intent(), lambda _: calls.append("send"),
            prepare=lambda _: calls.append("prepare"), reconcile=lambda *_: calls.append("reconcile"),
            journal_factory=journal)
        self.assertIs(out["provider_io_started"], False)
        self.assertEqual(out["receipt_status"], "planned")
        self.assertIs(out["delivery_outcome_uncertain"], False)
        self.assertEqual(calls, [])
        self.assertEqual(journal.rows[new_intent().key]["status"], "planned")

    def test_second_capacity_refusal_after_prepare_is_not_zero_io(self):
        journal = MemoryJournal(lambda _, n: admission_ok() if n == 1 else admission_block())
        calls = []

        def prepare(_):
            calls.append("prepare")
            return True, {"attachment": "already-uploaded"}

        out = m.guarded_delivery(new_intent(), lambda _: calls.append("send"),
                                 prepare=prepare, journal_factory=journal)
        self.assertEqual(out["stage"], "destination_capacity")
        self.assertIs(out["provider_io_started"], True)
        self.assertEqual(calls, ["prepare"])
        self.assertNotIn("attempted_at", journal.rows[new_intent().key])

    def test_released_legacy_receipt_reconciliation_counts_before_first_capacity(self):
        journal, calls = MemoryJournal(lambda *_: admission_block()), []
        intent = new_intent()
        journal.rows[intent.key] = receipt_row(intent)

        def reconcile(*_):
            calls.append("reconcile")
            return {"status": "absent", "scan_complete": True,
                    "pre_send_rejection_proven": True}

        out = m.guarded_delivery(intent, lambda _: calls.append("send"),
                                 reconcile=reconcile, journal_factory=journal)
        self.assertEqual(out["reason"], "SOCIAL_DAY_EVIDENCE_REQUIRED")
        self.assertEqual(out["receipt_status"], "planned")
        self.assertIs(out["provider_io_started"], True)
        self.assertEqual(calls, ["reconcile"])

    def test_prepare_error_counts_callback_without_claiming_public_write(self):
        journal = MemoryJournal()

        def broken(_):
            raise OSError("simulated upload connection error")

        out = m.guarded_delivery(new_intent(), lambda _: None,
                                 prepare=broken, journal_factory=journal)
        self.assertIs(out["provider_io_started"], True)
        self.assertIs(out["delivery_outcome_uncertain"], False)
        self.assertEqual(journal.rows[new_intent().key]["status"], "planned")

    def test_callback_busy_exceptions_do_not_reset_invocation_proof(self):
        for error in (m.DestinationBusy, m.ReceiptBusy):
            with self.subTest(error=error.__name__):
                def broken(_):
                    raise error()
                out = m.guarded_delivery(new_intent(), lambda _: None,
                                         prepare=broken, journal_factory=MemoryJournal())
                self.assertIs(out["provider_io_started"], True)

    def test_reconcile_error_counts_callback_and_keeps_uncertain(self):
        journal = MemoryJournal()
        intent = new_intent()
        journal.rows[intent.key] = receipt_row(intent)

        def broken(*_):
            raise TimeoutError("simulated history timeout")

        out = m.guarded_delivery(intent, lambda _: None, reconcile=broken, journal_factory=journal)
        self.assertIs(out["provider_io_started"], True)
        self.assertIs(out["delivery_outcome_uncertain"], True)
        self.assertEqual(journal.rows[intent.key]["status"], "uncertain")

    def test_provider_cannot_spoof_zero_io_in_compatible_capacity_result(self):
        journal = MemoryJournal()
        spoofed = m._capacity_rejection(new_intent(), admission_block())
        spoofed["provider_io_started"] = False
        out = m.guarded_delivery(new_intent(), lambda _: ("rejected", spoofed), journal_factory=journal)
        self.assertEqual(out["reason"], "SOCIAL_DAY_EVIDENCE_REQUIRED")
        self.assertIs(out["provider_io_started"], True)

    def test_success_and_cached_success_have_fresh_invocation_truth(self):
        journal, calls = MemoryJournal(), []

        def send(_):
            calls.append("send")
            return "success", {"ok": True, "post_id": 90512, "provider_io_started": False}

        first = m.guarded_delivery(new_intent(), send, journal_factory=journal)
        cached = m.guarded_delivery(new_intent(), send, journal_factory=journal)
        self.assertIs(first["provider_io_started"], True)
        self.assertIs(cached["provider_io_started"], False)
        self.assertTrue(cached["receipt_cached"])
        self.assertEqual(calls, ["send"])

    def test_prior_rejection_flag_is_not_a_current_invocation_authority(self):
        journal = MemoryJournal()
        intent = new_intent()
        row = receipt_row(intent, "planned", "canonical")
        row["next_attempt_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        row["last_rejection"] = {"ok": False, "provider_io_started": True, "reason": "vk_rate_limited"}
        journal.rows[intent.key] = row
        out = m.guarded_delivery(intent, lambda _: self.fail("early send"), journal_factory=journal)
        self.assertIs(out["provider_io_started"], False)
        self.assertEqual(journal.rows[intent.key], row)

    def test_uncertain_cooldown_is_zero_io_but_stays_uncertain(self):
        journal = MemoryJournal()
        intent = new_intent()
        row = receipt_row(intent)
        row["next_reconciliation_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        journal.rows[intent.key] = row
        out = m.guarded_delivery(intent, lambda _: self.fail("blind resend"),
            reconcile=lambda *_: self.fail("early history call"), journal_factory=journal)
        self.assertIs(out["provider_io_started"], False)
        self.assertIs(out["delivery_outcome_uncertain"], True)
        self.assertEqual(out["receipt_status"], "uncertain")

    def test_public_send_exception_preserves_uncertain_receipt_semantics(self):
        journal = MemoryJournal()

        def broken(_):
            raise TimeoutError("simulated lost public-write reply")

        out = m.guarded_delivery(new_intent(), broken, journal_factory=journal)
        self.assertIs(out["provider_io_started"], True)
        self.assertIs(out["delivery_outcome_uncertain"], True)
        self.assertEqual(journal.rows[new_intent().key]["status"], "uncertain")
        self.assertIn("attempted_at", journal.rows[new_intent().key])

    def test_success_commit_failure_keeps_inflight_for_native_reconciliation(self):
        journal = MemoryJournal()

        def fail_commit(row):
            if row["status"] == "success":
                raise OSError("simulated commit failure")

        journal.on_save = fail_commit
        out = m.guarded_delivery(new_intent(), lambda _: ("success", {"ok": True, "post_id": 90512}),
                                 journal_factory=journal)
        self.assertIs(out["provider_io_started"], True)
        self.assertEqual(out["reason"], "SOCIAL_DELIVERY_RECEIPT_PERSIST_UNCERTAIN")
        self.assertEqual(journal.rows[new_intent().key]["status"], "inflight")


def selected_due_namespace(raw, extra):
    names = {"due_posts_all", "_social_vk_day_evidence_rejected_without_io", "_social_delivery_context",
             "_required_delivery_platforms", "_merge_delivery_ids", "_all_required_delivery_ok",
             "_mark_social_delivery_published", "_project_clock"}
    nodes = [n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"datetime": datetime, "timezone": timezone, "timedelta": timedelta,
                 "sys": sys, **extra}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "actual_due_source_under_test", "exec"), namespace)
    return namespace


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value


class Storage:
    account_id, key = Field("account_id"), Field("key")

    def __init__(self, **values):
        self.__dict__.update(values)


class DueHarness:
    """Actual due AST and actual guarded_delivery; only environmental boundaries are injected."""
    def __init__(self, *, runner_source=None, tg_mode="existing", block_old_qa=False,
                 vk_mode="native", record_protocol="SOCIAL_RECEIPT_V1"):
        self.runner_source = runner_source or SOURCE.read_text()
        self.tg_mode, self.vk_mode = tg_mode, vk_mode
        self.qa_blocked = {"old"} if block_old_qa else set()
        self.events, self.qa_checks, self.persisted, self.notifications = [], [], [], []
        self.projects = [
            {"id": "dushi", "name": "Dushi", "platforms": "both", "vk_owner_id": -42,
             "channel_tg": "@dushi", "tz_offset": 3},
            {"id": "other", "name": "Other", "platforms": "both", "vk_owner_id": -77,
             "channel_tg": "@other", "tz_offset": 3},
        ]
        self.records = {}
        for rid, pid, created, tg_id in (
            ("new", "dushi", "2026-10-09 06:00", 175),
            ("old", "dushi", "2026-10-08 06:00", 173),
            ("other", "other", "2026-10-07 06:00", 200),
        ):
            self.records[rid] = {"id": rid, "project_id": pid, "created_at": created,
                "status": "partial", "publication_status": "partial", "text": rid + " text",
                "banner": rid + ".png", "generation_key": "slot:" + rid,
                "delivery_protocol": record_protocol, "quality_policy": "SOCIAL_QUALITY_V1",
                "ids": {"tg": {"chat_id": "@" + pid, "message_id": tg_id}}}
        if tg_mode != "existing":
            self.records["new"]["ids"] = {}
        self.journal = MemoryJournal(self.capacity)
        self.journal.rows[new_intent("old").key] = receipt_row(new_intent("old"))
        self.counters = {}
        self.namespace = selected_due_namespace(self.runner_source, {
            "SessionLocal": self.session, "Storage": Storage,
            "_all_user_buckets": lambda: ["u2"], "_social_delivery_external_wait_map": lambda: {},
            "_social_vk_rate_backoff_remaining": lambda: 0,
            "project_local_now": lambda p, d: d.replace(tzinfo=timezone.utc).astimezone(
                timezone(timedelta(hours=p.get("tz_offset", 3)))),
            "_try_post_delivery_lock": lambda account, rid: (account, rid),
            "_release_post_delivery_lock": lambda *args: None,
            "_load_post_record": lambda account, rid: deepcopy(self.records.get(rid)),
            "_persist_post_record": self.persist,
            "_resolve_banner_path": lambda account, banner: "/offline/" + banner,
            "_banner_delivery_ready": lambda *args: (True, ""),
            "_ocean_tracking_record": lambda *args: {},
            "_social_delivery_quality_gate": self.quality,
            "_vk_text_only_fallback_enabled": lambda *args: False,
            "_apply_vk_delivery_outcome": lambda rec, proj, result, **kwargs: rec,
            "_send_telegram": self.send_tg, "_send_vk": self.send_vk,
            "_notify_posted": lambda *args: self.notifications.append(args),
            "social_post_publish_verify_once": lambda **kwargs: {"checked": 0},
            "social_metrics_collect_once": lambda **kwargs: {"checked": 0},
            "observe_suppressed": lambda *args, **kwargs: None,
        })

    def session(self):
        owner = self

        class Query:
            def filter(self, *conditions):
                self.conditions = dict(conditions)
                return self

            def first(self):
                key = self.conditions["key"]
                if key == "posting_projects":
                    return types.SimpleNamespace(value=json.dumps(owner.projects))
                if key == "posting_posts":
                    return types.SimpleNamespace(value=json.dumps(list(owner.records.values())))
                return owner.counters.get(key)

        return types.SimpleNamespace(query=lambda model: Query(), close=lambda: None,
            commit=lambda: None, add=lambda row: owner.counters.__setitem__(row.key, row))

    def persist(self, account, record):
        self.records[record["id"]] = deepcopy(record)
        self.persisted.append(record["id"])
        return True

    def quality(self, account, record, project, banner):
        self.qa_checks.append(record["id"])
        ok = record["id"] not in self.qa_blocked
        return {"pass": ok, "blockers": [] if ok else ["VISION_NOT_PASS"], "record_updates": {},
                "public_texts": {"tg": record["text"], "vk": record["text"]}}

    def capacity(self, intent, number):
        if intent.context["post_id"] == "new":
            old = self.journal.rows[new_intent("old").key]
            if old["status"] != "success":
                return admission_block()
        return admission_ok()

    def send_tg(self, channel, text, banner, **kwargs):
        rid = kwargs["delivery_context"]["post_id"]
        self.events.append((rid, "tg_call"))
        if self.tg_mode == "uncertain":
            return {"ok": False, "delivery_outcome_uncertain": True, "receipt_cached": True,
                    "provider_io_started": False, "reason": "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN"}
        return {"ok": True, "message_id": 176, "receipt_cached": self.tg_mode == "cached",
                "provider_io_started": self.tg_mode != "cached"}

    def send_vk(self, owner_id, text, banner, **kwargs):
        context = kwargs["delivery_context"]
        rid = context["post_id"]
        self.events.append((rid, "vk_guard"))
        intent = m.DeliveryIntent("u2", "vk", str(owner_id), "wall_post", text, "asset", context)
        if rid == "new" and self.vk_mode != "native":
            out = m._capacity_rejection(intent, admission_block())
            if self.vk_mode == "missing_flag":
                return out
            out["provider_io_started"] = self.vk_mode == "after_prepare"
            if self.vk_mode == "after_prepare":
                self.events.append((rid, "prepare"))
            return out

        def prepare(_):
            self.events.append((rid, "prepare"))
            return True, {}

        def send(_):
            if rid == "old":
                raise AssertionError("uncertain old post must be reconciled, never blindly resent")
            self.events.append((rid, "send"))
            return "success", {"ok": True, "post_id": 91000 if rid == "new" else 92000}

        def reconcile(_intent, receipt):
            self.events.append((rid, "reconcile"))
            return {"status": "confirmed", "provider_published_at": "2026-10-08T07:00:00+00:00",
                    "result": {"ok": True, "post_id": 90512}}

        return m.guarded_delivery(intent, send, prepare=prepare, reconcile=reconcile,
                                 journal_factory=self.journal)

    def run(self):
        policy = types.ModuleType("app.services.social_owner_policy_v3")
        policy.is_guarded = lambda project: True
        action = types.ModuleType("app.services.action_log")
        action.log_action, action.ACTOR_CRON = lambda **kwargs: None, "cron"
        output = io.StringIO()
        with patch.dict(sys.modules, {policy.__name__: policy, action.__name__: action}), contextlib.redirect_stdout(output):
            self.namespace["due_posts_all"]()
        return output.getvalue()


class DueHeadOfLineTests(unittest.TestCase):
    def test_newer_zero_io_refusal_allows_old_native_reconcile_then_one_send_next_pass(self):
        h = DueHarness()
        h.run()
        self.assertEqual([x for x in h.events if x[1] in {"prepare", "send", "reconcile"}], [("old", "reconcile")])
        self.assertEqual(h.records["old"]["ids"]["vk"]["post_id"], 90512)
        self.assertEqual(h.records["old"]["status"], "published")
        self.assertEqual(h.records["new"]["status"], "partial")
        self.assertEqual(h.records["new"]["ids"]["tg"]["message_id"], 175)
        self.assertIs(h.records["new"]["delivery_failures"]["vk"]["provider_io_started"], False)
        self.assertEqual(h.records["other"]["delivery_failures"]["vk"]["reason"], "vk_delivery_paced")
        self.assertEqual(h.qa_checks[:2], ["new", "old"])
        h.events.clear()
        h.run()
        self.assertEqual([x for x in h.events if x[1] in {"prepare", "send", "reconcile"}],
                         [("new", "prepare"), ("new", "send")])
        self.assertEqual(h.records["new"]["status"], "published")
        self.assertEqual(h.records["old"]["ids"]["vk"]["post_id"], 90512)

    def test_old_quality_failure_is_not_bypassed_to_reconcile(self):
        h = DueHarness(block_old_qa=True)
        h.run()
        self.assertIn("old", h.qa_checks)
        self.assertNotIn(("old", "vk_guard"), h.events)
        self.assertNotIn(("old", "reconcile"), h.events)
        self.assertEqual(h.records["old"]["quality_status"], "delivery_recheck_blocked")
        self.assertIn("VISION_NOT_PASS", h.records["old"]["error"])

    def test_fresh_or_cached_or_uncertain_tg_invocation_keeps_project_turn_consumed(self):
        for mode in ("fresh", "cached", "uncertain"):
            with self.subTest(mode=mode):
                h = DueHarness(tg_mode=mode)
                h.run()
                self.assertIn(("new", "tg_call"), h.events)
                self.assertNotIn(("old", "vk_guard"), h.events)
                self.assertNotIn(("old", "reconcile"), h.events)
                # A no-IO VK rejection can still leave the one global VK turn
                # for a different project; it cannot start this project's old TG.
                self.assertEqual([x for x in h.events if x[1] == "send"], [("other", "send")])

    def test_missing_flag_and_capacity_refusal_after_prepare_remain_paced(self):
        for mode in ("missing_flag", "after_prepare"):
            with self.subTest(mode=mode):
                h = DueHarness(vk_mode=mode)
                h.run()
                self.assertNotIn(("old", "vk_guard"), h.events)
                self.assertNotIn(("other", "vk_guard"), h.events)
                self.assertEqual(h.records["other"]["delivery_failures"]["vk"]["reason"], "vk_delivery_paced")

    def test_noncanonical_record_does_not_gain_new_pacing_permission(self):
        h = DueHarness(vk_mode="explicit_zero", record_protocol="")
        h.run()
        self.assertNotIn(("old", "vk_guard"), h.events)
        self.assertNotIn(("other", "vk_guard"), h.events)

    def test_exact_zero_io_predicate_rejects_missing_false_like_and_unrelated_states(self):
        ns = selected_due_namespace(SOURCE.read_text(), {})
        helper = ns["_social_vk_day_evidence_rejected_without_io"]
        record = {"delivery_protocol": "SOCIAL_RECEIPT_V1"}
        good = m._capacity_rejection(new_intent(), admission_block())
        good["provider_io_started"] = False
        self.assertIs(helper(good, record), True)
        changes = [
            ("ok", True), ("ok", 0), ("stage", "wall.post"),
            ("reason", "SOCIAL_DAILY_SLOTS_RESERVED"), ("dependency_state", "waiting_external"),
            ("receipt_status", "uncertain"), ("delivery_outcome_uncertain", True),
            ("delivery_outcome_uncertain", 0), ("provider_io_started", True),
            ("provider_io_started", 0), ("provider_io_started", None), ("receipt_key", ""),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.assertIs(helper({**good, key: value}, record), False)
        for key in ("ok", "stage", "reason", "dependency_state", "receipt_status",
                    "delivery_outcome_uncertain", "provider_io_started", "receipt_key"):
            incomplete = dict(good)
            incomplete.pop(key)
            self.assertIs(helper(incomplete, record), False)


if __name__ == "__main__":
    unittest.main()
