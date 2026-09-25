import unittest
from unittest.mock import patch

import rop_digest
from app.api import calltracking


class RopDigestTruthTests(unittest.TestCase):
    def test_render_keeps_real_avito_calls_when_ai_analysis_is_empty(self):
        accounts = {"a": "Продажа", "b": "Аренда"}
        api_stats = {
            "a": {"total": 2, "answered": 1, "missed": 1},
            "b": {"total": 1, "answered": 0, "missed": 1},
        }
        with patch.object(
            calltracking,
            "get_rop_minutes",
            return_value={"expired": True, "left": 100},
        ):
            text = rop_digest.render(
                accounts,
                "2026-09-04",
                [],
                live=False,
                api_stats=api_stats,
                chat_stats={"a": {"chats": 2, "messages": 5}, "b": {"chats": 1, "messages": 2}},
            )
        self.assertIn("Всего звонков по Avito: 3", text)
        self.assertIn("Отвечено: 1", text)
        self.assertIn("Пропущено: 2", text)
        self.assertIn("Разобрано разговоров: 0", text)
        self.assertIn("AI-разбор не выполняется: период РОП завершён", text)
        self.assertNotIn("За последние 24 часа новых звонков нет", text)

    def test_api_stats_retry_prevents_single_empty_200_from_becoming_false_zero(self):
        responses = [
            (200, {"calls": []}),
            (200, {"calls": [{"call": {"talkDuration": 45}}]}),
        ]
        with (
            patch.object(calltracking, "_ct_token", return_value=("token", None)),
            patch.object(calltracking, "_get_calls", side_effect=responses) as get_calls,
            patch("time.sleep") as sleep,
        ):
            stats = rop_digest._api_stats_for_day({"a": "Аккаунт"}, "2026-09-04")
        self.assertEqual(stats["a"], {"total": 1, "answered": 1, "missed": 0})
        self.assertEqual(get_calls.call_count, 2)
        self.assertEqual(get_calls.call_args.args[-1], "a")
        sleep.assert_called_once_with(0.4)

    def test_two_confirmed_empty_200_responses_are_accepted_as_real_zero(self):
        responses = [(200, {"calls": []}), (200, {"calls": []})]
        with (
            patch.object(calltracking, "_ct_token", return_value=("token", None)),
            patch.object(calltracking, "_get_calls", side_effect=responses),
            patch("time.sleep"),
        ):
            stats = rop_digest._api_stats_for_day({"a": "Аккаунт"}, "2026-09-05")
        self.assertEqual(stats["a"], {"total": 0, "answered": 0, "missed": 0})

    def test_verified_cache_never_regresses_to_weaker_partial_snapshot(self):
        cached = {"total": 4, "answered": 3, "missed": 1}
        weaker = {"total": 1, "answered": 0, "missed": 1}
        stronger = {"total": 5, "answered": 4, "missed": 1}
        self.assertEqual(rop_digest._prefer_verified_stats(weaker, cached), cached)
        self.assertEqual(rop_digest._prefer_verified_stats(None, cached), cached)
        self.assertEqual(rop_digest._prefer_verified_stats(stronger, cached), stronger)

    def test_token_failure_falls_back_to_verified_cache(self):
        cached = {"total": 4, "answered": 3, "missed": 1}
        fake_db = object()
        with (
            patch.object(rop_digest, "_load_api_stats_cache", return_value=cached),
            patch.object(calltracking, "_ct_token", side_effect=RuntimeError("deferred")),
            patch.object(rop_digest, "_save_api_stats_cache") as save,
        ):
            stats = rop_digest._api_stats_for_day(
                {"a": "Аккаунт"}, "2026-09-02", db=fake_db
            )
        self.assertEqual(stats["a"], cached)
        save.assert_not_called()

    def test_digest_receipt_contains_business_truth_contract_and_report_date(self):
        payload = rop_digest._digest_state_payload(
            81,
            "abc123",
            "📞 Разбор звонков — 2026-09-05\nВсего звонков по Avito: 0",
            "2026-09-05",
        )
        self.assertEqual(payload["message_id"], 81)
        self.assertEqual(payload["sha256"], "abc123")
        self.assertEqual(payload["contract"], "avito_calls_truth_v2")
        self.assertEqual(payload["report_date"], "2026-09-05")
        self.assertIn("Всего звонков по Avito: 0", payload["text"])

    def test_raw_avito_calls_prevent_deferred_empty_before_final_hour(self):
        self.assertFalse(rop_digest._should_defer_digest([], 2, False))
        self.assertTrue(rop_digest._should_defer_digest([], 0, False))
        self.assertFalse(rop_digest._should_defer_digest([], 0, True))
        self.assertFalse(rop_digest._should_defer_digest([{"id": 1}], 0, False))

    def test_state_save_retries_transient_db_disconnect_without_external_replay(self):
        from sqlalchemy.exc import OperationalError

        class Result:
            def __init__(self,row): self.row=row
            def fetchone(self): return self.row

        class DB:
            def __init__(self):
                self.calls=0; self.rollbacks=0; self.commits=0
            def execute(self,stmt,params):
                self.calls += 1
                if self.calls == 1:
                    raise OperationalError(str(stmt),params,Exception("SSL connection has been closed unexpectedly"))
                if self.calls == 2:
                    return Result((123,))
                return Result(None)
            def rollback(self): self.rollbacks += 1
            def commit(self): self.commits += 1

        db=DB()
        rop_digest._save_state(db,42,"k",{"message_id":90})
        self.assertEqual(db.rollbacks,1)
        self.assertEqual(db.commits,1)
        self.assertEqual(db.calls,3)


if __name__ == "__main__":
    unittest.main()
