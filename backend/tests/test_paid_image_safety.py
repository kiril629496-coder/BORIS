import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from io import BytesIO
from PIL import Image
from unittest.mock import patch

from app.services import image_service as IMG


class _DB:
    pass


class _FixedContext:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self.value
    def __exit__(self, exc_type, exc, tb):
        return False


class PaidImageSafetyTests(unittest.TestCase):
    def test_existing_artifact_short_circuits_budget_and_provider(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "cached.png"
            Image.new("RGB", (512, 512), (120, 130, 140)).save(target, "PNG")
            with patch.object(IMG, "budget_reserve", side_effect=AssertionError("budget touched")):
                out = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target))
            self.assertTrue(out["ok"])
            self.assertTrue(out["cache_hit"])
            self.assertEqual(out["cost_rub"], 0.0)

    def test_blocked_reserve_never_reaches_provider(self):
        import app.services.openai_image_transport as transport
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "blocked.png"
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext({"allowed": True})), \
                 patch.object(IMG, "budget_reserve", return_value={"status": "blocked"}), \
                 patch.object(transport, "generate_image_http", side_effect=AssertionError("provider called")):
                out = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target))
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "AI_BUDGET_RESERVE_FAILED")
            self.assertFalse(target.exists())

    def test_denied_allow_never_reaches_provider(self):
        import app.services.openai_image_transport as transport
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "denied.png"
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext({"allowed": True})), \
                 patch.object(IMG, "budget_reserve", return_value={"status": "reserved"}), \
                 patch.object(IMG, "budget_allow", return_value={"allowed": False, "reason": "cap"}), \
                 patch.object(IMG, "budget_release", return_value=None), \
                 patch.object(transport, "generate_image_http", side_effect=AssertionError("provider called")):
                out = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target))
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "AI_BUDGET_NOT_ALLOWED")
            self.assertFalse(target.exists())

    def test_burst_cap_never_reaches_provider(self):
        import app.services.openai_image_transport as transport
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "burst.png"
            burst = {"allowed": False, "code": "PAID_IMAGE_BURST_CAP", "used": 10, "cap": 10, "window_min": 10}
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext(burst)), \
                 patch.object(transport, "generate_image_http", side_effect=AssertionError("provider called")):
                out = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target))
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "PAID_IMAGE_BURST_CAP")
            self.assertFalse(target.exists())

    def test_reference_burst_cap_never_reaches_edit_provider(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ref-out.png"
            reference = Path(td) / "ref.png"
            reference.write_bytes(b"reference")
            burst = {"allowed": False, "code": "PAID_IMAGE_BURST_CAP", "used": 10, "cap": 10, "window_min": 10}
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext(burst)), \
                 patch.object(IMG, "_edit_with_reference", side_effect=AssertionError("edit provider called")):
                out = IMG.generate(_DB(), "acc-a", "prompt", str(target), reference_path=str(reference))
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "PAID_IMAGE_BURST_CAP")
            self.assertFalse(target.exists())

    def test_generate_preserves_exact_guarded_request_id(self):
        expected = {"ok": True, "path": "/tmp/x.png", "request_id": "req-exact", "cost_rub": 4.56, "error": None}
        with patch.object(IMG, "generate_banner_financially_guarded", return_value=dict(expected)) as guarded:
            out = IMG.generate(_DB(), "acc-a", "prompt", "/tmp/x.png")
        self.assertEqual(out["request_id"], "req-exact")
        self.assertEqual(out["cost_rub"], 4.56)
        self.assertEqual(out["operation_used"], "generations")
        guarded.assert_called_once()

    def test_paid_cost_commits_before_local_persistence_failure(self):
        import app.services.openai_image_transport as transport
        fake = {"ok": True, "path_data": b"paid-bytes", "request_id": None, "usage": {}, "size": "1024x1024", "quality": "medium"}
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "disk-fail.png"
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext({"allowed": True})), \
                 patch.object(IMG, "budget_reserve", return_value={"status": "reserved"}), \
                 patch.object(IMG, "budget_allow", return_value={"allowed": True}), \
                 patch.object(IMG, "budget_commit", return_value={"status": "committed"}) as committed, \
                 patch.object(IMG, "budget_release", return_value=None), \
                 patch("app.usage.log_usage", return_value=5.01), \
                 patch.object(transport, "generate_image_http", return_value=fake), \
                 patch.object(IMG, "_atomic_write_bytes", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target), model="gpt-image-2", quality="medium")
            self.assertEqual(committed.call_count, 1)
            self.assertTrue(str(committed.call_args.args[1]).startswith("ambiguous-image:acc-a:"))

    def test_paid_transport_is_single_attempt(self):
        import app.services.openai_image_transport as transport
        seen = {}
        def fake_transport(*args, **kwargs):
            seen.update(kwargs)
            buf = BytesIO()
            Image.new("RGB", (512, 512), (120, 130, 140)).save(buf, "PNG")
            return {"ok": True, "path_data": buf.getvalue(), "request_id": "req-safe-1", "usage": {}, "size": "1024x1024", "quality": "medium"}
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "one.png"
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext({"allowed": True})), \
                 patch.object(IMG, "budget_reserve", return_value={"status": "reserved"}), \
                 patch.object(IMG, "budget_allow", return_value={"allowed": True}), \
                 patch.object(IMG, "budget_commit", return_value={"status": "committed"}), \
                 patch.object(IMG, "budget_release", return_value=None), \
                 patch("app.usage.log_usage", return_value=1.23), \
                 patch.object(transport, "generate_image_http", side_effect=fake_transport):
                out = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target), model="gpt-image-2", quality="medium")
            self.assertTrue(out["ok"])
            self.assertEqual(seen["max_attempts"], 1)
            self.assertTrue(IMG._valid_image_artifact(target))

    def test_ambiguous_image_transport_is_quarantined_and_not_rebought(self):
        import app.services.openai_image_transport as transport
        ambiguous = {"ok": False, "path_data": None, "request_id": None, "usage": None,
                     "size": "1024x1024", "quality": "medium", "status_code": None,
                     "ambiguous_transport": True, "error_code": "transport_outcome_unknown"}
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "uncertain.png"
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext({"allowed": True})), \
                 patch.object(IMG, "budget_reserve", return_value={"status": "reserved"}), \
                 patch.object(IMG, "budget_allow", return_value={"allowed": True}), \
                 patch.object(IMG, "budget_release", return_value={"status":"released"}), \
                 patch.object(transport, "generate_image_http", return_value=ambiguous) as provider:
                first = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target), model="gpt-image-2", quality="medium")
                second = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target), model="gpt-image-2", quality="medium")
            self.assertEqual(first["error"], "PAID_IMAGE_OUTCOME_UNCERTAIN")
            self.assertEqual(second["error"], "PAID_IMAGE_OUTCOME_UNCERTAIN")
            self.assertEqual(provider.call_count, 1)
            self.assertTrue(Path(str(target) + ".paid-intent-uncertain.json").exists())

    def test_transport_network_exception_is_marked_ambiguous(self):
        import app.services.openai_image_transport as transport
        with patch.object(transport, "dependency_call", side_effect=RuntimeError("socket closed after send")):
            out = transport.generate_image_http("prompt", account_id="acc-a", idempotency_key="intent-1")
        self.assertFalse(out["ok"])
        self.assertTrue(out["ambiguous_transport"])
        self.assertEqual(out["error_code"], "transport_outcome_unknown")

    def test_reference_edit_ambiguous_outcome_is_quarantined_and_not_rebought(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "edit-uncertain.png"
            reference = Path(td) / "reference.png"
            reference.write_bytes(b"reference")
            ambiguous = {"ok": False, "path": None, "request_id": None, "provider_request_id": None,
                         "provider_idempotency_key": "edit-intent-1", "cost_rub": None,
                         "operation_used": "edits", "error": "PAID_IMAGE_OUTCOME_UNCERTAIN",
                         "error_code": "edit_transport_outcome_unknown", "ambiguous_provider_outcome": True}
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext({"allowed": True})), \
                 patch.object(IMG, "budget_reserve", return_value={"status": "reserved"}), \
                 patch.object(IMG, "budget_allow", return_value={"allowed": True}), \
                 patch.object(IMG, "budget_release", return_value={"status":"released"}), \
                 patch.object(IMG, "_edit_with_reference", return_value=ambiguous) as provider:
                first = IMG.generate(_DB(), "acc-a", "prompt", str(target), reference_path=str(reference))
                second = IMG.generate(_DB(), "acc-a", "prompt", str(target), reference_path=str(reference))
            self.assertEqual(first["error"], "PAID_IMAGE_OUTCOME_UNCERTAIN")
            self.assertEqual(second["error"], "PAID_IMAGE_OUTCOME_UNCERTAIN")
            self.assertEqual(provider.call_count, 1)
            self.assertTrue(Path(str(target) + ".paid-intent-uncertain.json").exists())

    def test_image_daily_cap_code_is_preserved(self):
        import app.services.openai_image_transport as transport
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "daily.png"
            burst = {"allowed": False, "code": "PAID_IMAGE_DAILY_CAP", "daily_used": 50, "daily_cap": 50}
            with patch.object(IMG, "_artifact_generation_lock", lambda *a, **k: nullcontext()), \
                 patch.object(IMG, "_paid_image_account_guard", lambda *a, **k: _FixedContext(burst)), \
                 patch.object(transport, "generate_image_http", side_effect=AssertionError("provider called")):
                out = IMG.generate_banner_financially_guarded(_DB(), "acc-a", "prompt", str(target))
            self.assertEqual(out["error"], "PAID_IMAGE_DAILY_CAP")


if __name__ == "__main__":
    unittest.main()
