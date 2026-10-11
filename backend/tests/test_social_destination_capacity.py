"""No DB/provider calls: scheduling policy and receipt-boundary failure cases."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ast
import sys
import unittest
import types
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import social_delivery_receipts as m

NOW = datetime(2026, 10, 9, 7, tzinfo=timezone.utc)


def project(account="u2", pid="p", target="-42", **extra):
    return {"account_id": account, "id": pid, "platforms": "both", "vk_owner_id": target,
            "channel_tg": "@channel", "tz_offset": 3, "mode": "auto", "times": ["9", "18"],
            "paid_auto_generation": True, "expected_auto_generation": True,
            "_capacity_owner": True, **extra}


def post(rid="old", key="", account="u2", project_id="p", **extra):
    return {"account_id": account, "id": rid, "project_id": project_id, "generation_key": key,
            "status": "partial", "ids": {}, **extra}


def receipt(rid="done", *, platform="vk", target="-42", day="2026-10-09T06:00:00+00:00",
            status="success", account="u2", step=None, **extra):
    return {"identity": {"tenant": account, "platform": platform, "target": target,
                         "step": step or ("photo" if platform == "tg" else "wall_post"), "lifecycle": rid},
            "context": {"post_id": rid, "project_id": "p"}, "status": status,
            "attempted_at": day, "completed_at": day, **extra}


class CapacityPolicyTest(unittest.TestCase):
    def decide(self, candidate=None, projects=None, posts=None, receipts=None, platform="vk", now=NOW, step=None):
        candidate = candidate or post()
        return m.capacity_decision(account=candidate["account_id"], platform=platform,
            target="-42" if platform == "vk" else "@channel",
            context={"post_id": candidate["id"], "project_id": candidate["project_id"],
                     "generation_key": candidate["generation_key"], "_delivery_step": step or "wall_post"},
            projects=projects or [project()], posts=[candidate, *(posts or [])], receipts=receipts or [], now=now)

    def test_one_old_fits_two_unmaterialized_today_slots_but_second_does_not(self):
        first = self.decide()
        self.assertTrue(first["ok"])
        self.assertEqual((first["used"], first["reserved"]), (0, 2))
        second = self.decide(receipts=[receipt()])
        self.assertFalse(second["ok"])
        self.assertEqual((second["used"], second["reserved"]), (1, 2))

    def test_each_fresh_slot_can_use_its_reserved_place_after_one_backlog(self):
        morning = post("morning", "social-slot:u2:p:20261009_09")
        self.assertTrue(self.decide(morning, receipts=[receipt()])["ok"])
        r = receipt("morning"); r["context"]["generation_key"] = morning["generation_key"]
        evening = post("evening", "social-slot:u2:p:20261009_18")
        self.assertTrue(self.decide(evening, posts=[morning], receipts=[receipt(), r])["ok"])

    def test_today_comes_from_project_timezone_not_host_utc(self):
        out = self.decide(now=datetime(2026, 10, 8, 21, 1, tzinfo=timezone.utc))
        self.assertEqual(out["local_day"], "2026-10-09")
        self.assertTrue(all("20261009" in k for k in out["reserved_keys"]))

    def test_moderation_delay_reserves_previous_service_day_slot(self):
        out = self.decide(projects=[project(mode="moderate", times=["23"], auto_publish_hours=3)])
        self.assertEqual(out["reserved_keys"], ["social-slot:u2:p:20261008_23"])

    def test_minute_schedule_uses_existing_hour_key_without_double_reservation(self):
        out = self.decide(projects=[project(times=["09", "09:30", "18:45"])])
        self.assertTrue(out["ok"])
        self.assertEqual(out["reserved_keys"], ["social-slot:u2:p:20261009_09", "social-slot:u2:p:20261009_18"])
        self.assertEqual(self.decide(projects=[project(times=["09:60"])])["reason"], "SOCIAL_CAPACITY_SCHEDULE_INVALID")

    def test_multiple_accounts_sharing_target_reserve_together(self):
        out = self.decide(projects=[project(times=["9"]), project("u9", "foreign", times=["9", "18"])])
        self.assertFalse(out["ok"])
        self.assertEqual(out["reserved"], 3)

    def test_explicitly_expired_non_owner_project_does_not_reserve(self):
        foreign = project("u9", "foreign", _capacity_owner=False,
                          subscription={"status": "expired", "paid_at": "2026-10-09", "period_days": 30})
        self.assertTrue(self.decide(projects=[project(), foreign])["ok"])

    def test_date_proven_yesterday_tg_is_not_today_vk(self):
        old = post("partial", ids={"tg": {"message_id": 173, "chat_id": "@channel", "date": 1791440164}})
        out = self.decide(posts=[old], receipts=[receipt("partial")], platform="tg")
        self.assertEqual(out["used"], 0)
        self.assertEqual(self.decide(posts=[old], receipts=[receipt("partial")])["used"], 1)

    def test_unknown_tg_date_blocks_only_tg(self):
        old = post("partial", ids={"tg": {"message_id": 173, "chat_id": "@channel"}})
        self.assertEqual(self.decide(posts=[old], platform="tg")["reason"], "SOCIAL_DAY_EVIDENCE_REQUIRED")
        self.assertTrue(self.decide(posts=[old])["ok"])

    def test_historical_target_survives_project_retarget_or_deletion(self):
        old = post("historical", project_id="deleted", status="published",
                   ids={"vk": {"post_id": 99, "owner_id": -42, "date": int(NOW.timestamp())}})
        self.assertEqual(self.decide(posts=[old])["used"], 1)

    def test_legacy_complete_yesterday_upper_bound_excludes_without_inventing_date(self):
        old = post("old_complete", status="published", published_at="2026-10-08T16:00:00+00:00",
                   ids={"vk": {"post_id": 99, "owner_id": -42}})
        self.assertEqual(self.decide(posts=[old])["used"], 0)
        old["published_at"] = NOW.isoformat()
        out = self.decide(posts=[old])
        self.assertEqual(out["used"], 1)
        self.assertTrue(out["evidence_warnings"])

    def test_uncertain_write_reserves_capacity_and_planned_rejection_does_not(self):
        self.assertFalse(self.decide(receipts=[receipt(status="uncertain")])["ok"])
        self.assertTrue(self.decide(receipts=[receipt(status="planned")])["ok"])

    def test_reconciled_success_needs_provider_date_not_confirmation_time(self):
        r = receipt(reconciliation={"status": "confirmed"}); r.pop("attempted_at")
        self.assertEqual(self.decide(receipts=[r])["reason"], "SOCIAL_DAY_EVIDENCE_REQUIRED")
        r["reconciliation"]["provider_published_at"] = "2026-10-08T16:00:00+00:00"
        self.assertEqual(self.decide(receipts=[r])["used"], 0)

    def test_photo_and_overflow_one_logical_publication_even_across_midnight(self):
        own = post("same")
        photo = receipt("same", platform="tg", target="@channel", day="2026-10-08T20:59:00+00:00")
        out = self.decide(own, receipts=[photo], platform="tg", step="overflow_text")
        self.assertTrue(out["ok"])
        self.assertTrue(out["continuation"])
        self.assertEqual(out["additional"], 0)
        out = self.decide(own, receipts=[photo], platform="tg", step="photo")
        self.assertFalse(out["continuation"])

    def test_unknown_or_reversed_intervals_never_free_capacity(self):
        r = receipt();r["completed_at"] = "2026-10-08T00:00:00+00:00"
        self.assertEqual(self.decide(receipts=[r])["reason"], "SOCIAL_DAY_EVIDENCE_REQUIRED")

    def test_external_rejection_exposes_no_other_account_keys_or_ids(self):
        intent = m.DeliveryIntent("u2", "vk", "-42", "wall_post", "x")
        out = m._capacity_rejection(intent, {"reason": "SOCIAL_DAILY_SLOTS_RESERVED", "used": 1,
            "reserved": 2, "reserved_keys": ["secret-foreign-key"], "record_id": "foreign", "evidence_warnings": ["foreign"]})
        self.assertNotIn("foreign", repr(out))
        self.assertEqual(out["capacity"], {"limit": 3, "used": 1, "reserved": 2})

    def test_merge_preserves_existing_destination_date_and_unknown_fields(self):
        source = (Path(__file__).parents[1] / "posting_runner.py").read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "_merge_delivery_ids")
        env = {};exec(compile(ast.Module(body=[node], type_ignores=[]), "merge", "exec"), env)
        before = {"tg": {"chat_id": "@old", "message_id": 173, "date": 1791440164, "extra": "keep"}}
        result = env["_merge_delivery_ids"](before, {"channel_tg": "@new"}, {"tg": {"ok": True, "message_id": 173, "skipped_existing": True}})
        self.assertEqual(result, before)
        result = env["_merge_delivery_ids"](before, {"channel_tg": "@new"}, {"tg": {"ok": True, "result": {"message_id": 174}}})
        self.assertNotIn("date", result["tg"])


class ReceiptBoundaryTest(unittest.TestCase):
    def fixture(self, checks=None, on_save=None):
        owner = self
        self.row, self.calls = None, []
        checks = checks or []
        class Journal:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def load(self): return deepcopy(owner.row)
            def save(self, value):
                owner.row = deepcopy(value)
                if on_save: on_save(value)
            def destination_admission(self):
                class Admission:
                    def __enter__(self): return self
                    def __exit__(self, *args): return None
                    def check(self, now=None):
                        if checks:
                            return checks.pop(0)()
                        d = m.datetime.now(timezone.utc)
                        return {"ok": True, "local_day": d.date().isoformat(), "timezone_offset_hours": 0}
                return Admission()
        self.intent = m.DeliveryIntent("u2", "vk", "-42", "wall_post", "text", context={"post_id": "one", "protocol": m.PROTOCOL})
        return lambda _: Journal()

    def send(self, prepared):
        self.calls.append("send")
        return "success", {"ok": True, "post_id": 1}

    def test_slow_upload_rechecks_admission_before_public_write(self):
        checks = [lambda: {"ok": True}, lambda: {"ok": False, "reason": "SOCIAL_DAILY_SLOTS_RESERVED"}]
        factory = self.fixture(checks)
        out = m.guarded_delivery(self.intent, self.send, prepare=lambda _: (True, {"attachment": "uploaded"}), journal_factory=factory)
        self.assertEqual(out["reason"], "SOCIAL_DAILY_SLOTS_RESERVED")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row["status"], "planned")

    def test_final_capacity_read_crossing_midnight_never_sends_on_stale_day(self):
        class Clock(datetime):
            instant = datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc)
            @classmethod
            def now(cls, tz=None): return cls.instant
        def slow_check():
            Clock.instant += timedelta(seconds=2)
            return {"ok": True, "local_day": "2026-10-08", "timezone_offset_hours": 0}
        factory = self.fixture([lambda: {"ok": True}, slow_check])
        with patch.object(m, "datetime", Clock):
            out = m.guarded_delivery(self.intent, self.send, journal_factory=factory)
        self.assertEqual(out["reason"], "SOCIAL_DAY_BOUNDARY_RETRY")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row["status"], "planned")

    def test_inflight_commit_crossing_midnight_returns_proven_unsent_to_planned(self):
        class Clock(datetime):
            instant = datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc)
            @classmethod
            def now(cls, tz=None): return cls.instant
        def commit(value):
            if value["status"] == "inflight": Clock.instant += timedelta(seconds=2)
        factory = self.fixture(on_save=commit)
        with patch.object(m, "datetime", Clock):
            out = m.guarded_delivery(self.intent, self.send, journal_factory=factory)
        self.assertEqual(out["reason"], "SOCIAL_DAY_BOUNDARY_RETRY")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row["status"], "planned")

    def test_destination_commit_failure_discards_session(self):
        class Connection:
            closed = False
            invalidated = False
            def execute(self, *args): return self
            def scalar(self): return True
            def commit(self): raise OSError("commit outcome unknown")
            def invalidate(self): self.invalidated = True
        conn = Connection()
        journal = m.PgReceiptJournal(m.DeliveryIntent("u2", "vk", "-42", "wall_post", "text"))
        journal.conn, journal.locked = conn, True
        with patch.dict(sys.modules, {"sqlalchemy": types.SimpleNamespace(text=lambda x: x)}):
            with self.assertRaises(OSError): m.PgDestinationCapacity(journal).__enter__()
        self.assertTrue(conn.invalidated)

    def test_lost_destination_unlock_is_not_returned_to_pool(self):
        class Connection:
            closed = False
            invalidated = False
            def execute(self, *args): return self
            def scalar(self): return False
            def in_transaction(self): return False
            def invalidate(self): self.invalidated = True
        conn = Connection()
        journal = m.PgReceiptJournal(m.DeliveryIntent("u2", "vk", "-42", "wall_post", "text"))
        journal.conn, journal.locked = conn, True
        guard = m.PgDestinationCapacity(journal); guard.locked = True
        with patch.dict(sys.modules, {"sqlalchemy": types.SimpleNamespace(text=lambda x: x)}):
            with self.assertRaises(RuntimeError): guard.__exit__(None, None, None)
        self.assertTrue(conn.invalidated)


if __name__ == "__main__":
    unittest.main()
