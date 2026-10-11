"""Execute the actual scheduler/save functions against an in-memory store.

No application import, PostgreSQL, provider, publication or filesystem asset
operation is used. The concurrent case exercises the final save barrier only;
it does not stand in for a lock around concurrent pre-materialization providers.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(os.environ.get("SOCIAL_PAID_SLOT_SOURCE") or
              Path(__file__).resolve().parents[1] / "posting_runner.py")
ACCOUNT = "qa_slot_account"
PROJECT = "qa_ocean_project"
KEY = "social-slot:qa_slot_account:qa_ocean_project:20261009_09"


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value


class _Storage:
    account_id = _Column("account_id")
    key = _Column("key")


class _Query:
    def __init__(self, db):
        self.db = db
        self.where = {}

    def filter(self, *conditions):
        self.where.update(conditions)
        return self

    def first(self):
        key = (self.where.get("account_id"), self.where.get("key"))
        if key[1] == "posting_posts" and self.db.store.read_error:
            raise RuntimeError("fixture_storage_offline")
        value = self.db.store.rows.get(key)
        return None if key not in self.db.store.rows else SimpleNamespace(value=value)


class _Session:
    def __init__(self, store):
        self.store = store
        self.info = {}
        self.held = False

    def query(self, _model):
        return _Query(self)

    def _release(self):
        if self.held:
            self.held = False
            self.store.lock.release()

    def rollback(self):
        self._release()

    def close(self):
        self._release()


class _Store:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.read_error = False
        self.save_count = 0
        self.asset_calls = []

    def session(self):
        return _Session(self)

    def get_posts(self, account=ACCOUNT):
        return json.loads(self.rows.get((account, "posting_posts"), "[]"))

    def set_posts(self, posts, account=ACCOUNT):
        self.rows[(account, "posting_posts")] = json.dumps(posts, ensure_ascii=False)

    def load_for_update(self, db, account):
        self.lock.acquire()
        db.held = True
        db.info["posting_posts_lock:" + str(account)] = True
        return object(), self.get_posts(account)

    def save_locked(self, db, account, _row, posts):
        if not db.held:
            raise AssertionError("final save without the account lock")
        self.set_posts(posts, account)
        self.save_count += 1
        db._release()

    def persist_asset(self, account, path, **kwargs):
        self.asset_calls.append((account, path))
        return {"filename": Path(path).name, "fixture": True}


def _load_native_functions(store):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {
        "_posting_generation_record", "_posting_generation_exists",
        "_save_post_record", "_autopost_projects_impl", "_scheduled_due_hour",
    }
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
    required = wanted - {"_posting_generation_record"}
    if not required.issubset({node.name for node in functions}):
        raise AssertionError("native scheduled lifecycle functions missing")
    namespace = {
        "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "json": json, "Storage": _Storage, "SessionLocal": store.session,
        "load_posting_posts_for_update": store.load_for_update,
        "save_posting_posts_locked": store.save_locked,
        "BANNER_DIR": "/fixture-no-filesystem-operation",
    }
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


class PaidSlotLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.native = _load_native_functions(self.store)
        self.project = {
            "id": PROJECT, "name": "Fixture scheduled business", "mode": "auto",
            "times": ["9", "19"], "tz_offset": 3, "platforms": "both",
            "channel_tg": "@qa_fixture", "vk_owner_id": -1,
            "paid_auto_generation": True, "expected_auto_generation": True,
        }
        self.store.rows[(ACCOUNT, "posting_projects")] = json.dumps([self.project])
        self.paid_calls = []

        def paid_generation(*args, **kwargs):
            self.paid_calls.append((args, deepcopy(kwargs)))
            return {
                "text": "Prepared paid post", "banner": "/fixture/banner.png",
                "banner_cost_rub": 1.706, "banner_cost_known": True,
                "banner_attempt_costs": [1.30], "banner_vision_qa_costs": [0.406],
                "banner_attempts": 1, "quality_policy": "SOCIAL_QUALITY_V1",
                "quality_status": "banner_pass", "vision_qa": {
                    "pass": True, "request_id": "qa_fixture_initial_proof",
                },
            }

        self.native.update({
            "_project_clock": lambda _project: datetime(2026, 10, 9, 9, 1),
            "_scheduled_plan_with_date_context": lambda *a, **k: (KEY, {
                "theme": "Scheduled fixture theme", "occasion": "",
                "reference_date": "2026-10-09", "text_prompt_snapshot": "Frozen prompt",
                "occasion_context": {"kind": "evergreen"},
            }),
            "generate_post": paid_generation,
            "_banner_delivery_ready": lambda *a: (True, ""),
            "_ocean_tracking_record": lambda _text, _project, rid: {"utm_content": "p" + rid[:8]},
            "_project_delivery_text_tracked": lambda text, *a, **k: text,
        })
        app = ModuleType("app")
        app.__path__ = []
        services = ModuleType("app.services")
        services.__path__ = []
        assets = ModuleType("app.services.social_media_assets")
        assets.persist_banner_asset = self.store.persist_asset
        entitlement = ModuleType("app.services.social_entitlement")
        entitlement.normalize_posting_subscription = lambda sub: (sub, [])
        quality = ModuleType("app.services.social_quality")
        quality.quality_tracking = lambda result: {
            key: deepcopy(value) for key, value in result.items()
            if key not in {"text", "banner"}
        }
        services.social_quality = quality
        app.services = services
        self.modules = patch.dict(sys.modules, {
            "app": app, "app.services": services,
            "app.services.social_media_assets": assets,
            "app.services.social_entitlement": entitlement,
            "app.services.social_quality": quality,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def tick(self):
        return self.native["_autopost_projects_impl"](
            ACCOUNT, owner=True, respect_time=True, only_id=PROJECT,
        )

    def save(self, *, record_id="new-candidate", generation_key=KEY, **kwargs):
        return self.native["_save_post_record"](
            ACCOUNT, PROJECT, "Fixture scheduled business", "Candidate text",
            "/fixture/new-banner.png", "draft", generation_key=generation_key,
            record_id=record_id, tracking={"banner_cost_rub": 1.50, "banner_cost_known": True},
            **kwargs,
        )

    def exists(self, key=KEY):
        return self.native["_posting_generation_exists"](ACCOUNT, key)

    def test_first_paid_tick_then_editorial_error_does_not_buy_or_mint_again(self):
        neighbor = {"id": "other-project", "generation_key": "another-slot", "status": "published"}
        self.store.set_posts([neighbor])
        self.tick()
        records = self.store.get_posts()
        first = next(rec for rec in records if rec.get("generation_key") == KEY)
        original_id = first["id"]
        # Reproduce the actual separate editorial actor: a paid draft becomes
        # error while its initial QA, delivery QA failure and cost remain saved.
        first.update(status="error", publication_status="draft",
                     error="EDITORIAL_CONTRACT_BLOCKED:tg:ocean_video_or_url",
                     banner_cost_rub=2.1097,
                     delivery_quality={"status": "blocked", "blockers": ["vision_qa_failed:semantic_match"]},
                     delivery_qa_attempts={"qa_fixture_final": {"cost_rub": 0.4037,
                                                              "verdict": {"pass": False}}})
        self.store.set_posts(records)
        before = self.store.rows[(ACCOUNT, "posting_posts")]
        self.tick()
        self.assertEqual(len(self.paid_calls), 1, "the second tick purchased another generation")
        self.assertEqual(self.store.save_count, 1)
        self.assertEqual(len(self.store.asset_calls), 1)
        self.assertEqual(self.store.rows[(ACCOUNT, "posting_posts")], before)
        matching = [rec for rec in self.store.get_posts() if rec.get("generation_key") == KEY]
        self.assertEqual([rec["id"] for rec in matching], [original_id])

    def test_paid_history_is_preserved_without_reset_or_recount(self):
        record = {
            "id": "retained-history", "generation_key": KEY, "status": "error",
            "banner_cost_rub": 0, "banner_cost_known": False,
            "lifecycle_history": [{"id": "older-attempt", "banner_cost_rub": 2.10,
                                   "banner_cost_known": True,
                                   "lifecycle_history": [{"id": "oldest", "banner_cost_rub": 1.91}]}],
        }
        self.store.set_posts([record])
        before = self.store.rows[(ACCOUNT, "posting_posts")]
        self.assertTrue(self.exists())
        self.assertEqual(self.save(), "retained-history")
        self.assertEqual(self.store.rows[(ACCOUNT, "posting_posts")], before)
        self.assertEqual(self.store.save_count, 0)
        self.assertEqual(self.store.asset_calls, [])

    def test_ambiguous_provider_or_delivery_result_never_authorizes_generation(self):
        cases = [
            {"ambiguous_provider_outcome": True},
            {"delivery_outcome_uncertain": True},
            {"ids": {"tg": {"message_id": 315, "chat_id": "@qa_fixture"}}},
            {"external_post_id": {"vk": {"post_id": 41, "owner_id": -1}}},
            {"banner_cost_rub": None, "banner_cost_known": False},
            {"lifecycle_history": [{"ambiguous_provider_outcome": True}]},
        ]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                record = {"id": "uncertain-existing", "generation_key": KEY,
                          "status": "error", **evidence}
                self.store.set_posts([record])
                before = self.store.rows[(ACCOUNT, "posting_posts")]
                self.tick()
                self.assertEqual(self.paid_calls, [])
                self.assertEqual(self.save(), "uncertain-existing")
                self.assertEqual(self.store.rows[(ACCOUNT, "posting_posts")], before)

    def test_status_and_missing_cost_fields_are_not_free_retry_evidence(self):
        for status in ["draft", "partial", "published", "quality_blocked", "error",
                       "failed", "cancelled", "", None]:
            with self.subTest(status=status):
                self.store.set_posts([{"id": "existing", "generation_key": KEY,
                                       "status": status, "banner_cost_rub": 0}])
                self.assertTrue(self.exists())
                self.assertEqual(self.save(), "existing")
        self.assertEqual(self.store.save_count, 0)
        self.assertEqual(self.store.asset_calls, [])

    def test_unreadable_or_invalid_state_stops_before_paid_generation(self):
        for invalid in ["{broken-json", "null", "{}", "", None]:
            with self.subTest(invalid=invalid):
                self.store.rows[(ACCOUNT, "posting_posts")] = invalid
                with self.assertRaisesRegex(RuntimeError, "SOCIAL_GENERATION_STATE_UNAVAILABLE"):
                    self.tick()
        self.store.read_error = True
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_GENERATION_STATE_UNAVAILABLE"):
            self.tick()
        self.assertEqual(self.paid_calls, [])
        self.assertEqual(self.store.save_count, 0)

    def test_missing_existing_identity_fails_instead_of_returning_invented_id(self):
        self.store.set_posts([{"generation_key": KEY, "status": "error", "banner_cost_rub": 1.5}])
        before = self.store.rows[(ACCOUNT, "posting_posts")]
        self.assertTrue(self.exists())
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_GENERATION_RECORD_ID_MISSING"):
            self.save(record_id="must-not-be-returned")
        self.assertEqual(self.store.rows[(ACCOUNT, "posting_posts")], before)
        self.assertEqual(self.store.save_count, 0)
        self.assertEqual(self.store.asset_calls, [])

    def test_a_distinct_future_slot_is_not_blocked_by_old_error(self):
        old = {"id": "yesterday", "generation_key": KEY.replace("20261009", "20261008"),
               "status": "error", "banner_cost_rub": 3.90}
        self.store.set_posts([old])
        self.assertFalse(self.exists())
        self.tick()
        self.assertEqual(len(self.paid_calls), 1)
        self.assertEqual(len(self.store.get_posts()), 2)
        self.assertIn(old, self.store.get_posts())

    def test_empty_manual_key_does_not_collapse_independent_manual_records(self):
        self.assertFalse(self.exists(""))
        self.assertEqual(self.save(record_id="manual-a", generation_key=""), "manual-a")
        self.assertEqual(self.save(record_id="manual-b", generation_key=""), "manual-b")
        self.assertEqual({x["id"] for x in self.store.get_posts()}, {"manual-a", "manual-b"})

    def test_two_final_saves_return_one_persisted_identity_and_do_not_copy_again(self):
        barrier = threading.Barrier(2)

        def final_save(identity):
            barrier.wait(timeout=3)
            return self.save(record_id=identity)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(final_save, identity) for identity in ("candidate-a", "candidate-b")]
            ids = [future.result(timeout=5) for future in futures]
        records = self.store.get_posts()
        self.assertEqual(len(records), 1)
        self.assertEqual(ids, [records[0]["id"], records[0]["id"]])
        self.assertEqual(self.store.save_count, 1)
        self.assertEqual(self.store.asset_calls, [(ACCOUNT, "/fixture/new-banner.png")])
        self.assertEqual(records[0]["banner_cost_rub"], 1.50)
        self.assertEqual(self.paid_calls, [])


if __name__ == "__main__":
    unittest.main()
