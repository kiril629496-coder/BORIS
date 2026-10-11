"""Run as a standalone offline unittest script; every external dependency is a fixture."""
import copy
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_candidate():
    names = ("app", "app.db", "app.db.session", "app.services", "app.services.exception_observability",
        "app.services.ai_budget", "app.services.ai_guard_audit", "app.services.ai_privacy", "app.usage",
        "app.services.job_execution_clock", "app.services.banner_full_ai_premium", "sqlalchemy",
        "sqlalchemy.exc", "requests")
    modules = {name: types.ModuleType(name) for name in names}
    for module in modules.values():
        module.__path__ = []
    modules["sqlalchemy"].text = lambda sql: sql
    modules["sqlalchemy.exc"].TimeoutError = type("SQLTimeout", (TimeoutError,), {})
    modules["app.services.exception_observability"].observe_suppressed = lambda *a, **k: None
    modules["app.services.ai_guard_audit"].log_guard_event = lambda **kw: None
    modules["app.services.ai_privacy"].safe_external_ai_payload = lambda data: (data, {})
    modules["app.services.ai_privacy"].restore_external_ai_output = lambda data, vault: data
    modules["app.services"].ai_budget = modules["app.services.ai_budget"]
    sys.modules.update(modules)
    spec = importlib.util.spec_from_file_location("validator_candidate", ROOT / "app/services/media_semantic_validator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, modules


validator, modules = load_candidate()


class Result:
    def __init__(self, rows):
        self.rows = rows
    def mappings(self):
        return self
    def first(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None


class LedgerDB:
    def __init__(self, fixture):
        self.fixture = fixture
    def execute(self, sql, parameters):
        assert sql.startswith("SELECT request_id,") and "FROM api_usage" in sql, sql
        return Result([r for r in self.fixture.ledger if r["account_id"] == parameters["a"]
            and r["operation"] == parameters["o"] and r["request_id"] == parameters["r"]])
    def close(self):
        pass


class LockConnection:
    def execution_options(self, **kwargs):
        assert kwargs == {"isolation_level": "AUTOCOMMIT"}
        return self
    def execute(self, sql, params):
        assert "pg_advisory_" in sql, sql
    def close(self):
        pass


class Response:
    def __init__(self, content='{"pass": true}', status=200, request_id="request-1", usage=None):
        self.status_code = status
        self.headers = {"x-request-id": request_id} if request_id else {}
        self.body = {"usage": {"prompt_tokens": 100, "completion_tokens": 20} if usage is None else usage,
            "choices": [{"message": {"content": content}}]}
    def json(self):
        if isinstance(self.body, BaseException):
            raise self.body
        return copy.deepcopy(self.body)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = []
        self.responses = [Response()]
        self.calls = []
        self.wait_calls = 0
        self.drop_log = False
        self.rate = "table"
        self.pricing = "fixture-pricing-v1"
        self.allowance = {"allowed": True}
        self.burst = {"allowed": True}
        env = patch.dict(os.environ, {"OPENAI_API_KEY": "offline-fixture-only", "PLUSVIBE_API_KEY": "",
            "DEEPSEEK_API_KEY": "", "BORIS_VISION_PAID_CACHE_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        replacements = [(modules["app.db.session"], "SessionLocal", lambda: LedgerDB(self)),
            (modules["app.usage"], "log_usage", self.log_usage),
            (modules["app.services.ai_budget"], "reserve", lambda **k: {"status": "reserved"}),
            (modules["app.services.ai_budget"], "allow_next", lambda *a: self.allowance),
            (modules["app.services.ai_budget"], "commit", lambda *a: {"status": "committed"}),
            (modules["app.services.ai_budget"], "release", lambda *a: None),
            (modules["app.services.job_execution_clock"], "provider_call", self.provider_call),
            (modules["requests"], "post", self.post),
            (modules["requests"], "Session", lambda: types.SimpleNamespace(post=self.post)),
            (validator, "_vision_advisory_engine", lambda: types.SimpleNamespace(connect=LockConnection)),
            (validator, "_vision_burst_guard", lambda *a: nullcontext(self.burst))]
        for obj, name, value in replacements:
            handle = patch.object(obj, name, value, create=True)
            handle.start()
            self.addCleanup(handle.stop)
    def post(self, url, **kwargs):
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        if not self.responses:
            raise AssertionError("Unexpected extra provider call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response
    def provider_call(self, fn, *args, **kwargs):
        self.wait_calls += 1
        return fn(*args, **kwargs)
    def log_usage(self, account, provider, model, operation, **kwargs):
        if self.drop_log:
            return 0
        rid = kwargs["request_id"]
        if not any(row["request_id"] == rid for row in self.ledger):
            details = kwargs["usage_details"]
            self.ledger.append({"account_id": account, "provider": provider, "model": model,
                "operation": operation, "request_id": rid, "cost_rub": 0.1,
                "rate_source": self.rate, "pricing_version": self.pricing,
                "prompt_tokens": kwargs["prompt_tokens"], "completion_tokens": kwargs["completion_tokens"],
                "provenance_json": {**details.get("boris_provenance", {}), "intent_key": details["boris_intent_key"]}})
        return 0.1
    def call(self, return_meta=True):
        return validator._vision_chat_json("acct", "banner_premium_vision_qa_batch_v1",
            [{"type": "text", "text": "Exact fixture QA contract"}], max_tokens=900, timeout=120,
            return_meta=return_meta, provenance={"qa_version": "fixture-qa-v2", "budget_intent_hashes": "a" * 32,
                "qa_allocation_key": "premium_banner_qa_allocation:fixture"})


class CostAndCacheTests(Fixture):
    def test_legacy_return_shape_and_metadata_cache_cost_are_both_preserved(self):
        raw, meta = self.call()
        self.assertEqual({"pass": True}, raw)
        self.assertTrue(meta["cost_known"])
        self.assertEqual(0.1, meta["cost_rub"])
        self.assertEqual("request-1", meta["request_id"])
        self.assertEqual({"prompt_tokens": 100, "completion_tokens": 20}, meta["usage"])
        self.assertEqual("fixture-qa-v2", meta["qa_version"])
        self.assertGreaterEqual(meta["latency_ms"], 0)
        self.assertEqual(raw, self.call(return_meta=False))
        _, replay = self.call()
        self.assertTrue(replay["cache_hit"])
        self.assertEqual(0.1, replay["cost_rub"])
        self.assertEqual(0.0, replay["incremental_cost_rub"])
        self.assertEqual(0, replay["provider_calls"])
        self.assertEqual(1, replay["historical_provider_calls"])
        self.assertEqual((1, 1), (len(self.calls), self.wait_calls))
    def test_missing_ledger_is_unknown_and_cache_replay_without_keys_does_not_repay(self):
        self.drop_log = True
        raw, meta = self.call()
        self.assertFalse(meta["cost_known"])
        self.assertIsNone(meta["cost_rub"])
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            replay, metadata = self.call()
        self.assertEqual(raw, replay)
        self.assertTrue(metadata["cache_hit"])
        self.assertIsNone(metadata["cost_rub"])
        self.assertEqual(1, len(self.calls))
    def test_receipt_reconciliation_after_restart_never_requires_new_provider(self):
        self.drop_log = True
        self.call()
        saved = json.loads(next(Path(self.tmp.name).glob("*.json")).read_text())
        self.drop_log = False
        self.log_usage("acct", "openai", "gpt-4o-mini", "banner_premium_vision_qa_batch_v1",
            request_id="request-1", prompt_tokens=100, completion_tokens=20,
            usage_details={"boris_intent_key": "vision:" + saved["intent_hash"], "boris_provenance": {"vision_usage_known": True}})
        _, replay = self.call()
        self.assertTrue(replay["cost_known"])
        self.assertEqual(0.1, replay["cost_rub"])
        self.assertEqual(1, len(self.calls))
    def test_foreign_account_or_missing_rate_pricing_never_confirms_cost(self):
        self.call()
        row = self.ledger[0]
        for field, bad in (("account_id", "foreign"), ("rate_source", "fallback"), ("pricing_version", ""), ("cost_rub", float("nan"))):
            with self.subTest(field=field):
                old = row[field]
                row[field] = bad
                self.assertFalse(self.call()[1]["cost_known"])
                row[field] = old
        self.assertEqual(1, len(self.calls))
    def test_missing_token_usage_does_not_turn_table_pricing_into_known_zero(self):
        self.responses = [Response(usage={})]
        _, meta = self.call()
        self.assertFalse(meta["cost_known"])
        self.assertFalse(self.ledger[0]["provenance_json"]["vision_usage_known"])
    def test_malformed_token_types_still_preserve_the_result_as_unknown_cost(self):
        self.responses = [Response(usage={"prompt_tokens": "bad", "completion_tokens": {"bad": 1}})]
        raw, meta = self.call()
        self.assertEqual({"pass": True}, raw)
        self.assertFalse(meta["cost_known"])
        self.assertEqual(1, len(self.ledger))
        self.assertEqual(1, len(self.calls))
    def test_json_false_is_cached_and_replayed_without_another_payment(self):
        self.responses = [Response(content="false")]
        self.assertIs(False, self.call(return_meta=False))
        self.assertIs(False, self.call(return_meta=False))
        self.assertEqual(1, len(self.calls))


class FailedAttemptTests(Fixture):
    def test_paid_malformed_json_has_exact_cost_and_durable_quarantine_metadata(self):
        self.responses = [Response(content="malformed JSON")]
        with self.assertRaises(RuntimeError) as first:
            self.call()
        self.assertTrue(first.exception.vision_meta["cost_known"])
        self.assertEqual(0.1, first.exception.vision_meta["cost_rub"])
        self.assertEqual("request-1", first.exception.vision_meta["request_id"])
        with self.assertRaises(RuntimeError) as replay:
            self.call()
        self.assertEqual("request-1", replay.exception.vision_meta["request_id"])
        self.assertTrue(replay.exception.vision_meta["cost_known"])
        self.assertEqual(0, replay.exception.vision_meta["provider_calls"])
        self.assertEqual((1, 1), (len(self.calls), len(self.ledger)))
    def test_invalid_choices_cannot_drop_the_cost_journal(self):
        self.responses[0].body["choices"] = {"bad": "shape"}
        with self.assertRaises(RuntimeError) as error:
            self.call()
        self.assertTrue(error.exception.vision_meta["cost_known"])
        self.assertEqual(1, len(self.ledger))
    def test_pre_provider_cap_is_proved_zero_and_qa_retryable(self):
        self.burst = {"allowed": False, "code": "VISION_BURST_CAP", "used": 20, "cap": 20}
        with self.assertRaises(RuntimeError) as error:
            self.call()
        metadata = error.exception.vision_meta
        self.assertEqual("not_requested", metadata["provider_cost_state"])
        self.assertTrue(metadata["retryable"])
        self.assertEqual((0, 0), (len(self.calls), len(self.ledger)))
        self.burst = {"allowed": True}
        self.assertTrue(self.call()[1]["cost_known"])
    def test_explicit_429_allows_same_intent_qa_retry_but_only_one_inference_charge(self):
        self.responses = [Response(status=429), Response(request_id="accepted-request")]
        with self.assertRaises(RuntimeError) as error:
            self.call()
        self.assertEqual("known_not_billed", error.exception.vision_meta["provider_cost_state"])
        self.assertTrue(error.exception.vision_meta["retryable"])
        self.assertEqual(0.0, error.exception.vision_meta["cost_rub"])
        self.assertTrue(self.call()[1]["cost_known"])
        self.assertEqual(self.calls[0]["headers"]["Idempotency-Key"], self.calls[1]["headers"]["Idempotency-Key"])
        self.assertEqual((2, 1), (len(self.calls), len(self.ledger)))
    def test_500_is_unknown_and_quarantined_instead_of_implicitly_retried(self):
        self.responses = [Response(status=500)]
        with self.assertRaises(RuntimeError):
            self.call()
        with self.assertRaises(RuntimeError) as replay:
            self.call()
        self.assertFalse(replay.exception.vision_meta["cost_known"])
        self.assertIsNone(replay.exception.vision_meta["cost_rub"])
        self.assertEqual(1, len(self.calls))
    def test_ambiguous_transport_never_calls_fallback(self):
        self.responses = [TimeoutError("lost response")]
        with patch.dict(os.environ, {"PLUSVIBE_API_KEY": "fixture-fallback"}):
            with self.assertRaises(RuntimeError):
                self.call()
        self.assertEqual(1, len(self.calls))
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(1, len(self.calls))


class PremiumGuardTests(unittest.TestCase):
    def setUp(self):
        self.row = types.SimpleNamespace(id=12, account_id="acct", campaign_id=600)
        self.asset = types.SimpleNamespace(id=101, account_id="acct", source_provider="openai_full_ai_premium",
            source_ref="premium:banner-A:v1", storage_key="acct/banners/A.png", sha256="a" * 64)
        self.link = types.SimpleNamespace(position=0, role="hero", id=201)
        self.payload = {"feed_factory": True, "title": "Точный заголовок", "description": "Точное описание",
            "category": "Услуги", "banner_url": "/images/acct/banners/A.png", "images": ["/images/acct/banners/A.png"]}
        fingerprint = validator.semantic_content_fingerprint(self.payload)
        self.payload["banner_qa"] = {"passed": True, "banner_id": "banner-A", "version": 1,
            "artifact_sha256": "a" * 64, "qa_version": "qa-v2", "content_fingerprint": fingerprint}
        self.current = {"status": "ok", "receipt_current": True, "artifact_current": True,
            "qa_terminal": True, "qa_passed": True, "cost_reconciled": True, "cost_sla_pass": True,
            "artifact_sha256": "a" * 64, "qa_version": "qa-v2", "url": "/images/acct/banners/A.png",
            "provenance": {"campaign_id": 600, "campaign_item_id": 12, "content_fingerprint": fingerprint}}
        handle = patch.object(modules["app.services.banner_full_ai_premium"], "read_current_preview",
            lambda *args: copy.deepcopy(self.current), create=True)
        handle.start()
        self.addCleanup(handle.stop)
        handle = patch.object(validator, "_linked_media", lambda *args: [(self.link, self.asset)])
        handle.start()
        self.addCleanup(handle.stop)
    def test_runtime_single_hero_case_remains_valid_with_exact_proof(self):
        self.assertEqual([], validator.validate_linked_media_semantics(None, self.row, self.payload))
    def test_current_receipt_and_tenant_item_hash_version_content_are_all_required(self):
        changes = [(self.current, "receipt_current", False), (self.current, "qa_terminal", False),
            (self.current, "qa_passed", False), (self.current, "cost_reconciled", False),
            (self.current, "cost_sla_pass", False), (self.current, "artifact_sha256", "b" * 64),
            (self.current, "qa_version", "old"), (self.current, "url", "/images/acct/banners/other.png"),
            (self.current["provenance"], "campaign_item_id", 13), (self.current["provenance"], "campaign_id", 601),
            (self.payload, "description", "Changed description"), (self.payload["banner_qa"], "passed", "true")]
        for target, key, bad in changes:
            with self.subTest(key=key, bad=bad):
                old = target[key]
                target[key] = bad
                self.assertNotEqual([], validator.validate_linked_media_semantics(None, self.row, self.payload))
                target[key] = old
        self.asset.account_id = "foreign"
        self.assertNotEqual([], validator.validate_linked_media_semantics(None, self.row, self.payload))
    def test_universal_guard_cannot_use_weaker_generic_evidence_to_bypass_premium_failure(self):
        self.payload["media_semantic_guard"] = {"mode": "universal"}
        self.payload["media_semantic_evidence"] = [{"media_id": 101, "position": 0, "match": True,
            "confidence": 1.0, "content_fingerprint": validator.semantic_content_fingerprint(self.payload)}]
        self.assertEqual([], validator.validate_linked_media_semantics(None, self.row, self.payload))
        self.current["receipt_current"] = False
        errors = validator.validate_linked_media_semantics(None, self.row, self.payload)
        self.assertEqual("premium_banner_receipt_invalid", errors[0]["code"])
    def test_nonpremium_legacy_path_does_not_acquire_new_premium_restrictions(self):
        self.payload["media_semantic_guard"] = {"mode": "universal"}
        self.asset.source_provider = "openai"
        self.current["receipt_current"] = False
        self.assertEqual([], validator.validate_linked_media_semantics(None, self.row, self.payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_premium_legacy_alias_same_identity_and_sha_is_valid():
    row = types.SimpleNamespace(id=12, account_id="acct", campaign_id=600)
    asset = types.SimpleNamespace(
        id=101,
        account_id="acct",
        source_provider="openai_full_ai_premium",
        source_ref="premium:banner-A:v1",
        storage_key="acct/banners/up_premium_banner-A_v1.png",
        sha256="a" * 64,
    )
    link = types.SimpleNamespace(position=0, role="hero", id=201)
    payload = {
        "feed_factory": True,
        "media_semantic_guard": {"mode": "universal"},
        "title": "Точный заголовок",
        "description": "Точное описание",
        "category": "Услуги",
        "banner_url": "/images/acct/banners/up_premium_banner-A_v1.png",
        "images": ["/images/acct/banners/up_premium_banner-A_v1.png"],
    }
    fp = validator.semantic_content_fingerprint(payload)
    payload["banner_qa"] = {
        "passed": True,
        "banner_id": "banner-A",
        "version": 1,
        "artifact_sha256": "a" * 64,
        "qa_version": "qa-v2",
        "content_fingerprint": fp,
    }
    current = {
        "status": "ok",
        "receipt_current": True,
        "artifact_current": True,
        "qa_terminal": True,
        "qa_passed": True,
        "cost_reconciled": True,
        "cost_sla_pass": True,
        "artifact_sha256": "a" * 64,
        "qa_version": "qa-v2",
        "url": "/images/acct/banners/premium_ai/premium_banner-A_v1.png",
        "provenance": {
            "campaign_id": 600,
            "campaign_item_id": 12,
            "content_fingerprint": fp,
        },
    }
    with patch.object(modules["app.services.banner_full_ai_premium"], "read_current_preview", lambda *args: copy.deepcopy(current), create=True):
        with patch.object(validator, "_linked_media", lambda *args: [(link, asset)]):
            assert validator.validate_linked_media_semantics(None, row, payload) == []


def test_premium_legacy_alias_requires_exact_sha_and_identity():
    row = types.SimpleNamespace(id=12, account_id="acct", campaign_id=600)
    asset = types.SimpleNamespace(
        id=101,
        account_id="acct",
        source_provider="openai_full_ai_premium",
        source_ref="premium:banner-A:v1",
        storage_key="acct/banners/up_premium_banner-A_v1.png",
        sha256="b" * 64,
    )
    link = types.SimpleNamespace(position=0, role="hero", id=201)
    payload = {
        "feed_factory": True,
        "media_semantic_guard": {"mode": "universal"},
        "title": "Точный заголовок",
        "description": "Точное описание",
        "category": "Услуги",
        "banner_url": "/images/acct/banners/up_premium_banner-A_v1.png",
        "images": ["/images/acct/banners/up_premium_banner-A_v1.png"],
    }
    fp = validator.semantic_content_fingerprint(payload)
    payload["banner_qa"] = {
        "passed": True,
        "banner_id": "banner-A",
        "version": 1,
        "artifact_sha256": "a" * 64,
        "qa_version": "qa-v2",
        "content_fingerprint": fp,
    }
    current = {
        "status": "ok",
        "receipt_current": True,
        "artifact_current": True,
        "qa_terminal": True,
        "qa_passed": True,
        "cost_reconciled": True,
        "cost_sla_pass": True,
        "artifact_sha256": "a" * 64,
        "qa_version": "qa-v2",
        "url": "/images/acct/banners/premium_ai/premium_banner-A_v1.png",
        "provenance": {
            "campaign_id": 600,
            "campaign_item_id": 12,
            "content_fingerprint": fp,
        },
    }
    with patch.object(modules["app.services.banner_full_ai_premium"], "read_current_preview", lambda *args: copy.deepcopy(current), create=True):
        with patch.object(validator, "_linked_media", lambda *args: [(link, asset)]):
            errors = validator.validate_linked_media_semantics(None, row, payload)
            assert errors and errors[0]["code"] == "premium_banner_receipt_invalid"
