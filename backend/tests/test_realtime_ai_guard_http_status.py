import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app.services import realtime_ai_guard as guard
from app.services.reliability import _shared_provider_failure


class FakeResponse:
    def __init__(self, status_code, request_id="req-test", text="SECRET_RESPONSE_BODY"):
        self.status_code = status_code
        self.headers = {"x-request-id": request_id}
        self.text = text


class RealtimeAIHTTPGuardTests(unittest.TestCase):
    def test_200_response_is_preserved(self):
        response = FakeResponse(200)
        self.assertIs(guard._checked_openai_response(response, account_id="acct"), response)

    def test_429_is_shared_failure_and_logs_safe_metadata_only(self):
        response = FakeResponse(429, request_id="req-429", text="SECRET CLIENT DATA")
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(guard.OpenAIHTTPError) as ctx:
                guard._checked_openai_response(response, account_id="acct")
        exc = ctx.exception
        self.assertEqual(exc.status_code, 429)
        self.assertIn("rate_limit", str(exc))
        self.assertIn("req-429", str(exc))
        self.assertTrue(_shared_provider_failure("openai.text", exc))
        log = out.getvalue()
        self.assertIn("status=429", log)
        self.assertIn("request_id=req-429", log)
        self.assertNotIn("SECRET CLIENT DATA", log)

    def test_503_is_shared_provider_failure(self):
        with self.assertRaises(guard.OpenAIHTTPError) as ctx:
            guard._checked_openai_response(FakeResponse(503), account_id="acct")
        self.assertTrue(_shared_provider_failure("openai.text", ctx.exception))

    def test_plain_gemini_sales_timeout_is_tenant_scoped(self):
        self.assertFalse(
            _shared_provider_failure(
                "gemini.sales",
                RuntimeError("gemini timeout"),
            )
        )

    def test_gemini_sales_503_timeout_is_still_shared(self):
        self.assertTrue(
            _shared_provider_failure(
                "gemini.sales",
                RuntimeError("gemini timeout service unavailable 503"),
            )
        )

    def test_gemini_shared_quota_marker_is_global(self):
        self.assertTrue(
            _shared_provider_failure(
                "gemini.sales",
                RuntimeError("gemini shared_quota_429"),
            )
        )

    def test_gemini_shared_location_marker_is_global(self):
        self.assertTrue(
            _shared_provider_failure(
                "gemini.sales",
                RuntimeError("gemini shared_unsupported_location_400"),
            )
        )

    def test_gemini_plain_cli_error_is_tenant_scoped(self):
        self.assertFalse(
            _shared_provider_failure(
                "gemini.sales",
                RuntimeError("gemini cli_error"),
            )
        )

    def test_400_is_tenant_local_request_failure(self):
        with self.assertRaises(guard.OpenAIHTTPError) as ctx:
            guard._checked_openai_response(FakeResponse(400), account_id="acct")
        self.assertFalse(_shared_provider_failure("openai.text", ctx.exception))

    def test_401_is_shared_authentication_failure(self):
        with self.assertRaises(guard.OpenAIHTTPError) as ctx:
            guard._checked_openai_response(FakeResponse(401), account_id="acct")
        self.assertIn("authentication", str(ctx.exception))
        self.assertTrue(_shared_provider_failure("openai.text", ctx.exception))

    def test_realtime_guard_routes_openai_through_checked_provider_post(self):
        seen = {}

        def fake_requests_post(url, **kwargs):
            seen["request_url"] = url
            return FakeResponse(429, request_id="req-integration")

        def fake_dependency_call(dependency, fn, *args, **kwargs):
            seen["dependency"] = dependency
            seen["provider_fn"] = fn.__name__
            # Only pass provider/network kwargs; reliability controls are consumed
            # by the real dependency_call implementation.
            return fn(args[0], timeout=kwargs.get("timeout"))

        def fake_guarded_post(post_fn, url, **kwargs):
            return post_fn(url, timeout=7)

        with (
            patch.object(guard, "_caller", return_value=("acct", "messenger", "reply")),
            patch.object(guard, "guarded_post", side_effect=fake_guarded_post),
            patch("app.services.reliability.dependency_call", side_effect=fake_dependency_call),
        ):
            with self.assertRaises(guard.OpenAIHTTPError) as ctx:
                guard.realtime_guarded_post(fake_requests_post, guard.OPENAI_CHAT_URL)

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(seen["dependency"], "openai.text")
        self.assertEqual(seen["provider_fn"], "_provider_post")
        self.assertEqual(seen["request_url"], guard.OPENAI_CHAT_URL)

    def test_real_mass_guard_marks_http_failure_unsuccessful_and_reraises(self):
        from app.services import mass_ai_guard

        finish_calls = []

        def fake_requests_post(url, **kwargs):
            return FakeResponse(429, request_id="req-mass")

        def fake_dependency_call(dependency, fn, *args, **kwargs):
            return fn(args[0], timeout=kwargs.get("timeout"))

        def fake_reserve(**kwargs):
            return {"id": 1}

        def fake_finish(token, response=None, success=False):
            finish_calls.append({"token": token, "response": response, "success": success})

        with (
            patch.object(guard, "_caller", return_value=("acct", "messenger", "reply")),
            patch("app.services.reliability.dependency_call", side_effect=fake_dependency_call),
            patch.object(mass_ai_guard, "reserve", side_effect=fake_reserve),
            patch.object(mass_ai_guard, "finish", side_effect=fake_finish),
        ):
            with self.assertRaises(guard.OpenAIHTTPError):
                guard.realtime_guarded_post(
                    fake_requests_post,
                    guard.OPENAI_CHAT_URL,
                    timeout=7,
                )

        self.assertEqual(len(finish_calls), 1)
        self.assertFalse(finish_calls[0]["success"])
        self.assertIsNone(finish_calls[0]["response"])

    def test_non_openai_url_remains_passthrough(self):
        sentinel = object()

        def fake_post(url, **kwargs):
            return sentinel

        self.assertIs(
            guard.realtime_guarded_post(fake_post, "https://example.invalid/test"),
            sentinel,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
