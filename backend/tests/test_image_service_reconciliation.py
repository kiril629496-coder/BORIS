"""Offline behavioral coverage for the source/runtime image-service merge.

The complete candidate module is loaded with database and provider boundaries
replaced by stubs. No production imports, credentials, HTTP or database calls.
Run in an isolated interpreter; the source path is relative to this test file.
"""
import base64
import importlib.util
import io
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


SOURCE = Path(__file__).resolve().parents[1] / "app/services/image_service.py"


def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    return result


def forbidden(*args, **kwargs):
    raise AssertionError("external/database boundary must be explicitly stubbed")


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (256, 256), "#f97316").save(output, "PNG")
    return output.getvalue()


class Connection:
    def __init__(self, recent, daily):
        self.recent, self.daily = recent, daily
        self.statements = []
        self.closed = False

    def execution_options(self, **kwargs):
        return self

    def execute(self, statement, params):
        self.statements.append((statement, params))
        if "count(*)" in statement:
            count = self.recent if "cut" in params else self.daily
            return types.SimpleNamespace(fetchone=lambda: (count, count * 2.15))
        return types.SimpleNamespace(fetchone=lambda: None)

    def close(self):
        self.closed = True


class ImageServiceReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "banner.png"
        self.reference = Path(self.tmp.name) / "reference.png"
        self.reference.write_bytes(png_bytes())
        self.budget = module("app.services.ai_budget")
        for name in ("reserve", "allow_next", "commit", "release"):
            setattr(self.budget, name, Mock(side_effect=forbidden))
        self.audit = Mock()
        stubs = {
            "sqlalchemy": module("sqlalchemy", text=lambda value: value, create_engine=forbidden),
            "sqlalchemy.pool": module("sqlalchemy.pool", NullPool=object),
            "app": module("app", __path__=[]),
            "app.db": module("app.db", __path__=[]),
            "app.db.session": module("app.db.session", DATABASE_URL="offline-no-database"),
            "app.services": module("app.services", __path__=[], ai_budget=self.budget),
            "app.services.ai_budget": self.budget,
            "app.services.ai_guard_audit": module("app.services.ai_guard_audit", log_guard_event=self.audit),
            "app.services.exception_observability": module(
                "app.services.exception_observability", observe_suppressed=lambda *args, **kwargs: None),
            "app.usage": module("app.usage", log_usage=forbidden),
        }
        self.modules = patch.dict(sys.modules, stubs)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location("offline_image_service", SOURCE)
        self.sut = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sut)

    def generate(self, **changes):
        values = dict(db=object(), account_id="tenant-a", prompt="Verified product brief",
                      save_path=str(self.path), model="gpt-image-2", quality="low",
                      operation="banner_premium_ai", reference_path=str(self.reference))
        values.update(changes)
        return self.sut.generate(**values)

    def test_reference_banner_cap_is_unchanged_and_rejects_460_kopeks(self):
        for operation in ("banner_premium_ai", "campaign_openai_gpt_image_2"):
            with self.subTest(operation=operation):
                guard = self.sut.banner_reference_cost_guard(operation, "gpt-image-2", "low")
                self.assertFalse(guard["allowed"])
                self.assertEqual(guard["estimated_cost_rub"], 4.6)
                self.assertEqual(guard["max_cost_rub"], 4.0)

    def test_reference_guard_does_not_change_non_banner_policy(self):
        guard = self.sut.banner_reference_cost_guard("photo_master", "gpt-image-2", "low")
        self.assertTrue(guard["allowed"])
        self.assertFalse(guard["applies"])

    def test_new_explicit_reference_is_blocked_before_budget_or_provider(self):
        with patch.object(self.sut, "_artifact_generation_lock", return_value=nullcontext()), \
             patch.object(self.sut, "_paid_image_account_guard", side_effect=forbidden), \
             patch.object(self.sut, "_edit_with_reference", side_effect=forbidden):
            result = self.generate(provenance={"budget_intent_key": "intent-a"})
        self.assertEqual(result["error"], "BANNER_REFERENCE_COST_CAP_EXCEEDED")
        self.assertIsNone(result["cost_rub"])
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.sut._read_uncertain_intent(str(self.path)))
        self.budget.reserve.assert_not_called()
        self.assertEqual(self.audit.call_args.kwargs["event_code"], "BANNER_REFERENCE_COST_CAP_EXCEEDED")

    def test_exact_cached_reference_remains_free_without_budget_or_provider(self):
        before = png_bytes()
        self.path.write_bytes(before)
        with patch.object(self.sut, "banner_reference_cost_guard", side_effect=forbidden), \
             patch.object(self.sut, "_artifact_generation_lock", side_effect=forbidden), \
             patch.object(self.sut, "_edit_with_reference", side_effect=forbidden):
            result = self.generate()
        self.assertTrue(result["ok"])
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["cost_rub"], 0.0)
        self.assertEqual(self.path.read_bytes(), before)
        self.budget.reserve.assert_not_called()

    def test_unknown_paid_outcome_is_quarantined_without_new_attempt(self):
        self.sut._write_uncertain_intent(str(self.path), {
            "status": "provider_outcome_uncertain", "provider_request_id": "req-existing",
            "error_code": "transport_timeout",
        })
        with patch.object(self.sut, "_artifact_generation_lock", side_effect=forbidden), \
             patch.object(self.sut, "_edit_with_reference", side_effect=forbidden):
            result = self.generate()
        self.assertEqual(result["error"], "PAID_IMAGE_OUTCOME_UNCERTAIN")
        self.assertEqual(result["request_id"], "req-existing")
        self.assertTrue(result["ambiguous_provider_outcome"])
        self.budget.reserve.assert_not_called()

    def test_primary_generation_preserves_provenance_and_preset(self):
        provenance = {"campaign_id": 536, "item_id": 11, "budget_intent_key": "job:7:item:11",
                      "content_fingerprint": "verified-copy"}
        with patch.object(self.sut, "generate_banner_financially_guarded", return_value={
                "ok": True, "request_id": "req-primary", "cost_rub": 2.15}) as generate, \
             patch.object(self.sut, "_edit_with_reference", side_effect=forbidden):
            result = self.generate(reference_path=None, provenance=provenance, burst_override="burst_only")
        self.assertEqual(result["operation_used"], "generations")
        self.assertEqual(generate.call_args.kwargs["provenance"], provenance)
        self.assertEqual(generate.call_args.kwargs["burst_override"], "burst_only")
        self.assertEqual(generate.call_args.kwargs["model"], "gpt-image-2")
        self.assertEqual(generate.call_args.kwargs["quality"], "low")

    def test_allowed_reference_keeps_provenance_and_single_budget_attempt(self):
        provenance = {"campaign_id": 536, "item_id": 11, "budget_intent_key": "job:7:item:11"}
        with patch.object(self.sut, "_artifact_generation_lock", return_value=nullcontext()), \
             patch.object(self.sut, "_paid_image_account_guard", return_value=nullcontext({"allowed": True})), \
             patch.object(self.sut, "budget_reserve", return_value={"status": "reserved"}) as reserve, \
             patch.object(self.sut, "budget_allow", return_value={"allowed": True}) as allow, \
             patch.object(self.sut, "budget_release", return_value={}) as release, \
             patch.object(self.sut, "_edit_with_reference", return_value={
                 "ok": True, "request_id": "req-photo", "cost_rub": 2.15}) as edit:
            result = self.generate(operation="photo_master", provenance=provenance)
        self.assertTrue(result["ok"])
        self.assertEqual(edit.call_args.kwargs["provenance"], provenance)
        self.assertEqual(reserve.call_args.args[2], 460)
        self.assertEqual(reserve.call_args.args[4], 1)
        allow.assert_called_once()
        release.assert_called_once()
        self.assertIsNone(self.sut._read_uncertain_intent(str(self.path)))

    def test_edit_usage_keeps_exact_intent_and_commits_before_artifact(self):
        events = []
        body = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}],
                "usage": {"input_tokens": 100, "output_tokens": 200}}
        response = types.SimpleNamespace(status_code=200, headers={"x-request-id": "req-exact"},
                                         json=lambda: body)
        provider = Mock(return_value=response)
        stubs = {
            "requests": module("requests", post=forbidden),
            "app.services.reliability": module("app.services.reliability", dependency_call=provider,
                ProviderDeferred=type("ProviderDeferred", (Exception,), {}),
                CircuitOpen=type("CircuitOpen", (Exception,), {})),
            "app.services.ai_privacy": module("app.services.ai_privacy",
                sanitize_external_reference_image_bytes=lambda data, **kwargs: (data, "image/png"),
                safe_external_ai_payload=lambda text: (text, {})),
        }
        provenance = {"campaign_id": 536, "item_id": 11, "budget_intent_key": "budget-11",
                      "content_fingerprint": "copy-v1", "not_scalar": {"ignored": True}}
        def usage(*args, **kwargs):
            events.append("usage")
            self.assertEqual(kwargs["request_id"], "req-exact")
            self.assertEqual(kwargs["usage_details"]["boris_provenance"],
                             {k: v for k, v in provenance.items() if k != "not_scalar"})
            return 2.15
        original_write = self.sut._atomic_write_bytes
        def persist(*args):
            events.append("artifact")
            return original_write(*args)
        with patch.dict(sys.modules, stubs), patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test-value"}), \
             patch.object(self.sut, "log_usage", side_effect=usage), \
             patch.object(self.sut, "_atomic_write_bytes", side_effect=persist):
            result = self.sut._edit_with_reference("tenant-a", "Verified brief", str(self.path),
                str(self.reference), "gpt-image-2", "low", "1024x1024", "photo_master",
                on_paid_request=lambda rid: events.append("commit:" + rid), provenance=provenance)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cost_rub"], 2.15)
        self.assertEqual(events, ["usage", "commit:req-exact", "artifact"])
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(provider.call_args.kwargs["headers"]["Idempotency-Key"].startswith("boris-image-edit:"))

    def account_guard(self, recent, daily, override):
        connection = Connection(recent, daily)
        engine = types.SimpleNamespace(connect=lambda: connection, dispose=Mock())
        with patch.object(self.sut, "_create_engine", return_value=engine), \
             patch.object(self.sut, "_paid_image_burst_limits", return_value=(2, 10, 5)):
            with self.sut._paid_image_account_guard(object(), "tenant-a", override=override) as result:
                value = dict(result)
        self.assertTrue(connection.closed)
        engine.dispose.assert_called_once()
        self.assertTrue(any("pg_advisory_unlock" in sql for sql, _ in connection.statements))
        return value

    def test_burst_only_still_stops_at_daily_cap(self):
        result = self.account_guard(recent=9, daily=5, override="burst_only")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["code"], "PAID_IMAGE_DAILY_CAP")

    def test_burst_only_skips_short_window_with_daily_capacity(self):
        result = self.account_guard(recent=3, daily=4, override="burst_only")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["override_mode"], "burst_only")

    def test_explicit_all_and_legacy_true_preserve_existing_override(self):
        for override in ("all", True):
            with self.subTest(override=override):
                result = self.account_guard(recent=20, daily=20, override=override)
                self.assertTrue(result["allowed"])
                self.assertEqual(result["override_mode"], "all")

    def test_unknown_truthy_override_does_not_bypass_limits(self):
        result = self.account_guard(recent=3, daily=4, override="unrecognized")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["code"], "PAID_IMAGE_BURST_CAP")
        self.assertEqual(result["override_mode"], "none")


if __name__ == "__main__":
    unittest.main()
