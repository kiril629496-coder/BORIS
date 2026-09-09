# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch
from app.ext_api import aiprov


class AiProviderAuthClassificationTests(unittest.TestCase):
    def test_gemini_missing_auth_method_is_auth_error(self):
        msg = ('Please set an Auth method in your settings.json or specify one of '
               'GEMINI_API_KEY, GOOGLE_GENAI_USE_VERTEXAI')
        self.assertEqual(aiprov.classify(msg, 1), aiprov.AUTH_ERROR)

    def test_billing_still_precedes_rate_limit(self):
        self.assertEqual(aiprov.classify('429 insufficient_quota', 429),
                         aiprov.UNAVAILABLE_BILLING)

    def test_gemini_short_free_tier_request_quota_is_rate_limited_not_billing(self):
        msg = (
            "You exceeded your current quota; "
            "generate_content_free_tier_requests; "
            "Please retry in 21.13s."
        )
        self.assertEqual(aiprov.classify(msg, 429), aiprov.RATE_LIMITED)
        self.assertAlmostEqual(aiprov.free_tier_short_retry_after(msg), 21.13, places=2)

    def test_generic_billing_without_exact_free_tier_retry_stays_billing(self):
        self.assertEqual(
            aiprov.classify("429 insufficient_quota; add billing details", 429),
            aiprov.UNAVAILABLE_BILLING,
        )

    def test_call_persists_short_gemini_retry_instead_of_generic_five_minutes(self):
        from app.ext_api.errors import ApiError
        msg = (
            "Gemini failed; You exceeded your current quota; "
            "generate_content_free_tier_requests; Please retry in 21.13s."
        )
        def runner():
            raise ApiError("DEPENDENCY_UNAVAILABLE", msg)
        with patch.object(aiprov.db, "one", return_value=None), \
             patch.object(aiprov, "mark") as mark:
            out = aiprov.call(
                aiprov.CODING,
                {"gemini_cli": runner},
                candidate_names=["gemini_cli"],
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["attempts"][0]["kind"], aiprov.RATE_LIMITED)
        args, kwargs = mark.call_args
        self.assertEqual(args[0], "gemini_cli")
        self.assertEqual(args[1], aiprov.RATE_LIMITED)
        self.assertEqual(kwargs["retry_after"], 23)

    def test_transient_connection_stays_temporary(self):
        self.assertEqual(aiprov.classify('connection timeout', 1), aiprov.TEMP_ERROR)

    def test_unsupported_location_is_not_billing_or_temporary(self):
        msg = '{"error":{"code":400,"message":"User location is not supported for the API use.","status":"FAILED_PRECONDITION"}}'
        self.assertEqual(aiprov.classify(msg, 1), aiprov.UNSUPPORTED_LOCATION)
        self.assertTrue(aiprov.is_terminal(aiprov.UNSUPPORTED_LOCATION))

    def test_unsupported_location_never_becomes_usable_from_elapsed_cooldown(self):
        rows=[{
            'name':'gemini_cli','state':aiprov.UNSUPPORTED_LOCATION,
            'note':'geo','last_failure_at':None,'retry_at':None,'cooling':False,
        }]
        with patch.object(aiprov.db,'rows',return_value=rows):
            row=aiprov.state('gemini_cli')
        self.assertFalse(row['usable'])

    def test_wait_reason_is_external_when_geo_and_auth_are_permanent(self):
        fake={
            'gemini_cli': {'state':aiprov.UNSUPPORTED_LOCATION,'usable':False},
            'claude_code': {'state':aiprov.AUTH_ERROR,'usable':False},
        }
        with patch.object(aiprov,'development_candidates',return_value=[]),              patch.object(aiprov,'state',return_value=fake):
            reason=aiprov.development_wait_reason()
        self.assertTrue(reason.startswith('WAITING_FREE_CODING_PROVIDER_EXTERNAL:'))
        self.assertIn('Gemini=UNSUPPORTED_LOCATION',reason)
        self.assertIn('Claude=AUTH_ERROR',reason)

    def test_wait_reason_keeps_auto_retry_for_temporary_provider_outage(self):
        fake={
            'gemini_cli': {'state':aiprov.TEMP_ERROR,'usable':False},
            'claude_code': {'state':aiprov.AUTH_ERROR,'usable':False},
        }
        with patch.object(aiprov,'development_candidates',return_value=[]),              patch.object(aiprov,'state',return_value=fake):
            self.assertEqual(
                aiprov.development_wait_reason(),
                'WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically'
            )

    def test_reprobe_skips_known_unsupported_location(self):
        with patch.object(aiprov.db,'one',return_value={
            'state':aiprov.UNSUPPORTED_LOCATION,'updated_at':None,'due':True,
        }), patch('subprocess.run') as run:
            out=aiprov.reprobe_free_development(min_interval_min=5)
        run.assert_not_called()
        self.assertEqual(out['status'],aiprov.UNSUPPORTED_LOCATION)
        self.assertFalse(out['probed'])
        self.assertFalse(out['auto_retry'])

    def test_billing_does_not_become_usable_when_retry_at_elapsed(self):
        rows=[{
            'name':'openai','state':aiprov.UNAVAILABLE_BILLING,
            'note':'credit_balance_exhausted','last_failure_at':None,
            'retry_at':None,'cooling':False,
        }]
        with patch.object(aiprov.db,'rows',return_value=rows):
            row=aiprov.state('openai')
        self.assertFalse(row['usable'])
        self.assertTrue(row['probe_required'])

    def test_auth_error_does_not_become_usable_when_retry_at_elapsed(self):
        rows=[{
            'name':'openai','state':aiprov.AUTH_ERROR,
            'note':'invalid key','last_failure_at':None,
            'retry_at':None,'cooling':False,
        }]
        with patch.object(aiprov.db,'rows',return_value=rows):
            row=aiprov.state('openai')
        self.assertFalse(row['usable'])
        self.assertTrue(row['probe_required'])

    def test_openai_billing_probe_respects_cooldown_without_network(self):
        row={'state':aiprov.UNAVAILABLE_BILLING,'retry_due':False,'interval_due':True}
        with patch.dict('os.environ',{'OPENAI_API_KEY':'x'},clear=False), \
             patch.object(aiprov.db,'one',return_value=row), \
             patch('requests.post') as post:
            out=aiprov.reprobe_openai_billing(min_interval_min=5)
        post.assert_not_called()
        self.assertEqual(out['status'],'cooldown')
        self.assertFalse(out['probed'])

    def test_openai_billing_probe_runs_when_retry_due_even_if_recently_updated(self):
        # retry_at is the cadence owner. A recent updated_at must not add a
        # second six-hour delay after the explicit retry window already elapsed.
        row={'state':aiprov.UNAVAILABLE_BILLING,'retry_due':True,'interval_due':False}
        class Resp:
            status_code=200
            def json(self): return {}
        with patch.dict('os.environ',{'OPENAI_API_KEY':'x'},clear=False), \
             patch.object(aiprov.db,'one',return_value=row), \
             patch('requests.post',return_value=Resp()), \
             patch.object(aiprov,'mark') as mark:
            out=aiprov.reprobe_openai_billing(min_interval_min=5)
        self.assertTrue(out['recovered'])
        self.assertEqual(out['status'],aiprov.AVAILABLE)
        self.assertEqual(mark.call_args.args[0],'openai')
        self.assertEqual(mark.call_args.args[1],aiprov.AVAILABLE)

    def test_openai_billing_probe_unfunded_stays_fail_closed(self):
        row={'state':aiprov.UNAVAILABLE_BILLING,'retry_due':True,'interval_due':True}
        class Resp:
            status_code=429
            def json(self): return {'error':{'code':'credit_balance_exhausted','message':'quota'}}
        with patch.dict('os.environ',{'OPENAI_API_KEY':'x'},clear=False), \
             patch.object(aiprov.db,'one',return_value=row), \
             patch('requests.post',return_value=Resp()), \
             patch.object(aiprov,'mark') as mark:
            out=aiprov.reprobe_openai_billing(min_interval_min=5)
        self.assertFalse(out['recovered'])
        self.assertEqual(out['status'],aiprov.UNAVAILABLE_BILLING)
        self.assertEqual(mark.call_args.args[1],aiprov.UNAVAILABLE_BILLING)
        self.assertGreaterEqual(int(mark.call_args.kwargs['retry_after']),3600)


if __name__ == '__main__':
    unittest.main()
