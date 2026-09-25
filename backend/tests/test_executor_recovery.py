# -*- coding: utf-8 -*-
import json
import os
import time
import unittest
from unittest.mock import patch

from app.ext_api import a2a, aiprov, executor
from app.ext_api.errors import ApiError


def _order():
    return {"order_id": 85563, "dev_job_id": "dev_1056", "iteration": 0, "scope": {}}


class ExecutorRecoveryTests(unittest.TestCase):
    def _router_patches(self, candidates):
        return (
            patch.object(aiprov, "candidates", return_value=list(candidates)),
            patch.object(aiprov, "development_candidates", return_value=list(candidates)),
            patch.object(aiprov, "mark", return_value=None),
            patch.object(aiprov, "_seen", return_value=None),
            patch.object(aiprov, "_remember", return_value=None),
            patch.object(aiprov, "why_empty", return_value=[]),
            patch.object(aiprov.time, "sleep", return_value=None),
        )

    def test_claude_rate_limited_immediately_fails_over_to_next_coding_provider(self):
        calls=[]
        def claude():
            calls.append("claude_code")
            raise ApiError("DEPENDENCY_UNAVAILABLE", "Claude Code API_ERROR_STATUS 429 weekly limit")
        def gemini():
            calls.append("gemini_cli")
            return {"summary":"worked"}
        patches=self._router_patches(["claude_code","gemini_cli","openai"]) + (patch.object(aiprov,"TEMP_RETRIES",5),)
        for p in patches: p.start()
        try:
            out=aiprov.call(aiprov.CODING,{"claude_code":claude,"gemini_cli":gemini,"openai":lambda: self.fail("must stop after success")})
        finally:
            for p in reversed(patches): p.stop()
        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"],"gemini_cli")
        self.assertEqual(calls,["claude_code","gemini_cli"])
        self.assertEqual(out["attempts"],[{"provider":"claude_code","kind":aiprov.RATE_LIMITED,"error":"DEPENDENCY_UNAVAILABLE: Claude Code API_ERROR_STATUS 429 weekly limit","try":1}])

    def test_temp_error_keeps_bounded_retry(self):
        calls=[]
        def transient():
            calls.append(1)
            if len(calls)<3:
                raise ApiError("DEPENDENCY_UNAVAILABLE","service unavailable 503")
            return "ok"
        patches=self._router_patches(["claude_code"]) + (patch.object(aiprov,"TEMP_RETRIES",2),)
        for p in patches: p.start()
        try: out=aiprov.call(aiprov.CODING,{"claude_code":transient})
        finally:
            for p in reversed(patches): p.stop()
        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"],"claude_code")
        self.assertEqual(len(calls),3)
        self.assertEqual([a["kind"] for a in out["attempts"]],[aiprov.TEMP_ERROR,aiprov.TEMP_ERROR])

    def test_all_coding_candidates_unavailable_is_bounded_and_diagnostic(self):
        calls=[]
        def claude():
            calls.append(1)
            raise ApiError("DEPENDENCY_UNAVAILABLE","429 weekly limit")
        patches=self._router_patches(["claude_code","anthropic_api"]) + (patch.object(aiprov,"TEMP_RETRIES",3),)
        for p in patches: p.start()
        try: out=aiprov.call(aiprov.CODING,{"claude_code":claude})
        finally:
            for p in reversed(patches): p.stop()
        self.assertFalse(out["ok"])
        self.assertEqual(len(calls),1)
        self.assertEqual(out["attempts"][0]["kind"],aiprov.RATE_LIMITED)
        self.assertEqual(out["attempts"][1],{"provider":"anthropic_api","kind":"NO_RUNNER","error":"NO_RUNNER: caller did not provide runner","try":0})

    def test_executor_coding_runners_cover_supported_runtime_providers(self):
        calls=[]
        def fake_run(order,prov):
            calls.append(prov)
            if prov=="claude_code":
                raise ApiError("DEPENDENCY_UNAVAILABLE","429 weekly limit")
            return {"result":{"summary":"worked"},"provider":prov,"model":prov,"steps":1,"calls":0,"ptok":0,"ctok":0}
        patches=self._router_patches(["claude_code","gemini_cli","openai"]) + (patch.dict(os.environ,{"EXT_API_EXECUTOR_PROVIDER":""},clear=False), patch.object(executor,"_run_provider",side_effect=fake_run))
        for p in patches: p.start()
        try:
            os.environ.pop("EXT_API_EXECUTOR_PROVIDER",None)
            out=executor._run_with_provider_failover(_order())
        finally:
            for p in reversed(patches): p.stop()
        self.assertEqual(out["provider"],"gemini_cli")
        self.assertEqual(calls,["claude_code","gemini_cli"])

    def test_gemini_daily_quota_never_uses_nested_short_retry(self):
        reset=int(time.time())+3600
        raw=(
            "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
            f"reset_epoch={reset} model=gemini-2.5-flash; "
            "You have exhausted your daily quota on this model. "
            "You exceeded your current quota "
            "generate_content_free_tier_requests. Please retry in 27s"
        )
        self.assertIsNone(aiprov.free_tier_short_retry_after(raw))
        self.assertEqual(aiprov.gemini_daily_quota_reset_epoch(raw), float(reset))

    def test_gemini_state_never_becomes_usable_before_daily_reset(self):
        reset=int(time.time())+3600
        rows=[{
            "name":"gemini_cli",
            "state":aiprov.RATE_LIMITED,
            "note":(
                "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
                f"reset_epoch={reset}"
            ),
            "last_failure_at":None,
            "retry_at":None,
            # Simulate the generic DB cooldown already expired.
            "cooling":False,
        }]
        with patch.object(aiprov.db,"rows",return_value=rows):
            state=aiprov.state("gemini_cli")
        self.assertFalse(state["usable"])
        self.assertTrue(state["cooling"])
        self.assertEqual(state["daily_reset_epoch"],reset)

    def test_gemini_available_mark_cannot_erase_current_daily_reset(self):
        reset = int(time.time()) + 3600
        current = {
            "note": (
                "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
                f"reset_epoch={reset} model=gemini-2.5-flash"
            )
        }
        with patch.object(aiprov.db, "one", return_value=current), \
             patch.object(aiprov.db, "rows", return_value=[]), \
             patch.object(aiprov.db, "q") as q:
            out = aiprov.mark("gemini_cli", aiprov.AVAILABLE, "sales runtime success")
        self.assertEqual(out["state"], aiprov.RATE_LIMITED)
        self.assertGreater(out["retry_after"], 3500)
        self.assertEqual(q.call_args.kwargs["s"], aiprov.RATE_LIMITED)
        self.assertIn("BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED", q.call_args.kwargs["note"])
        self.assertIn("preserved_from=provider_state", q.call_args.kwargs["note"])

    def test_gemini_available_mark_recovers_daily_reset_from_a2a_history(self):
        reset = int(time.time()) + 3600
        history = [{
            "body_json": json.dumps({
                "reason": "all_free_providers_unavailable",
                "provider_attempts": [{
                    "provider": "gemini_cli",
                    "error": (
                        "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
                        f"reset_epoch={reset} model=gemini-2.5-flash"
                    ),
                }],
            })
        }]
        with patch.object(aiprov.db, "one", return_value={"note": "sales runtime success"}), \
             patch.object(aiprov.db, "rows", return_value=history), \
             patch.object(aiprov.db, "q") as q:
            out = aiprov.mark("gemini_cli", aiprov.AVAILABLE, "sales runtime success")
        self.assertEqual(out["state"], aiprov.RATE_LIMITED)
        self.assertEqual(q.call_args.kwargs["s"], aiprov.RATE_LIMITED)
        self.assertIn("preserved_from=a2a_history", q.call_args.kwargs["note"])

    def test_gemini_available_mark_is_allowed_after_daily_reset(self):
        reset = int(time.time()) - 60
        current = {
            "note": (
                "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
                f"reset_epoch={reset}"
            )
        }
        with patch.object(aiprov.db, "one", return_value=current), \
             patch.object(aiprov.db, "rows", return_value=[]), \
             patch.object(aiprov.db, "q") as q:
            out = aiprov.mark("gemini_cli", aiprov.AVAILABLE, "health success")
        self.assertEqual(out["state"], aiprov.AVAILABLE)
        self.assertEqual(q.call_args.kwargs["s"], aiprov.AVAILABLE)

    def test_gemini_reprobe_restores_stale_available_from_durable_history(self):
        reset = int(time.time()) + 3600
        row = {
            "state": aiprov.AVAILABLE,
            "note": "sales runtime success",
            "retry_at": None,
            "updated_at": None,
            "due": False,
        }
        with patch.object(aiprov.db, "one", return_value=row), \
             patch.object(
                 aiprov, "_durable_gemini_daily_reset_evidence",
                 return_value=(float(reset), "a2a_history"),
             ), \
             patch.object(aiprov, "mark") as mark, \
             patch("subprocess.run") as run:
            out = aiprov.reprobe_free_development(min_interval_min=5)
        self.assertEqual(out["status"], "daily_quota_cooldown")
        self.assertEqual(out["reset_epoch"], reset)
        self.assertTrue(out["restored_state"])
        self.assertEqual(out["evidence_source"], "a2a_history")
        mark.assert_called_once()
        self.assertEqual(mark.call_args.args[0], "gemini_cli")
        self.assertEqual(mark.call_args.args[1], aiprov.RATE_LIMITED)
        run.assert_not_called()

    def test_gemini_reprobe_honors_durable_daily_reset_without_network_call(self):
        reset=int(time.time())+3600
        row={
            "state":aiprov.RATE_LIMITED,
            "note":(
                "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 "
                f"reset_epoch={reset} model=gemini-2.5-flash"
            ),
            "retry_at":None,
            "updated_at":None,
            "due":True,
        }
        with patch.object(aiprov.db,"one",return_value=row), \
             patch("subprocess.run") as run:
            out=aiprov.reprobe_free_development(min_interval_min=5)
        self.assertEqual(out["status"],"daily_quota_cooldown")
        self.assertEqual(out["reset_epoch"],reset)
        self.assertFalse(out["probed"])
        run.assert_not_called()

    def test_provider_review_block_recovers_to_review_not_executor_queue(self):
        row={
            "id":77,
            "status":a2a.BLOCKED_INFRA,
            "blocked_reason":"provider_unavailable: review provider quota/cooling",
            "last_review_json":'{"verdict":"BLOCKED_INFRA"}',
            "last_evidence_json":'{"summary":"work preserved","evidence":"PASS"}',
        }
        with patch.object(a2a.db,"rows",return_value=[row]), \
             patch.object(aiprov,"development_candidates",return_value=["gemini_cli"]), \
             patch.object(a2a,"_workspace_access_blocker_healed",return_value=False), \
             patch.object(a2a,"_filesystem_scope_blocker_healed",return_value=False), \
             patch.object(a2a,"_context_truncation_blocker_healed",return_value=False), \
             patch.object(a2a,"_read_only_production_conflict_healed",return_value=False), \
             patch.object(a2a,"_loop_guard_root_cause_recovery",return_value=None), \
             patch.object(a2a,"_set") as setter, \
             patch.object(a2a,"message") as message:
            recovered=a2a.recover_internal_blockers()
        self.assertEqual(recovered,[77])
        self.assertEqual(setter.call_args.kwargs["status"],a2a.REVIEW)
        self.assertEqual(message.call_args.args[1],"AUTO_RECOVERY_TO_REVIEW")

    def test_preexecution_provider_block_still_recovers_to_queue(self):
        row={
            "id":78,
            "status":a2a.BLOCKED_INFRA,
            "blocked_reason":"all free development providers unavailable",
            "last_review_json":None,
            "last_evidence_json":None,
        }
        with patch.object(a2a.db,"rows",return_value=[row]), \
             patch.object(aiprov,"development_candidates",return_value=["gemini_cli"]), \
             patch.object(a2a,"_workspace_access_blocker_healed",return_value=False), \
             patch.object(a2a,"_filesystem_scope_blocker_healed",return_value=False), \
             patch.object(a2a,"_context_truncation_blocker_healed",return_value=False), \
             patch.object(a2a,"_read_only_production_conflict_healed",return_value=False), \
             patch.object(a2a,"_loop_guard_root_cause_recovery",return_value=None), \
             patch.object(a2a,"_set") as setter, \
             patch.object(a2a,"message"):
            recovered=a2a.recover_internal_blockers()
        self.assertEqual(recovered,[78])
        self.assertEqual(setter.call_args.kwargs["status"],a2a.QUEUED)


if __name__ == "__main__":
    unittest.main()
