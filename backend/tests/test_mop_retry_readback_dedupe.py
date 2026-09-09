import inspect
import unittest

from app import mop_core


def provider(messages):
    return {"status": "ok", "messages": {"messages": messages}}


def msg(mid, direction, created, body="", kind="text"):
    return {
        "id": mid,
        "direction": direction,
        "created": created,
        "type": kind,
        "content": {"text": body},
    }


class MopRetryReadbackDedupeTest(unittest.TestCase):
    def test_exact_reply_already_visible_blocks_second_post(self):
        data = provider([
            msg("in-1", "in", 100, "Здравствуйте"),
            msg("out-1", "out", 101, "Добрый день! Чем помочь?"),
        ])
        self.assertEqual(
            mop_core.retry_send_readback_decision(
                "in-1", "Добрый день! Чем помочь?", data
            ),
            "already_sent",
        )

    def test_other_outbound_means_answered_externally(self):
        data = provider([
            msg("in-1", "in", 100, "Здравствуйте"),
            msg("out-human", "out", 101, "Мария уже ответила вручную"),
        ])
        self.assertEqual(
            mop_core.retry_send_readback_decision(
                "in-1", "Добрый день! Чем помочь?", data
            ),
            "answered_externally",
        )

    def test_newer_inbound_supersedes_old_draft(self):
        data = provider([
            msg("in-1", "in", 100, "Здравствуйте"),
            msg("in-2", "in", 102, "Вы тут?"),
        ])
        self.assertEqual(
            mop_core.retry_send_readback_decision(
                "in-1", "Добрый день! Чем помочь?", data
            ),
            "superseded",
        )

    def test_original_inbound_still_latest_allows_retry(self):
        data = provider([
            msg("old-out", "out", 90, "Старый ответ"),
            msg("in-1", "in", 100, "Новый вопрос"),
        ])
        self.assertEqual(
            mop_core.retry_send_readback_decision(
                "in-1", "Новый ответ", data
            ),
            "safe_to_send",
        )

    def test_missing_or_failed_provider_evidence_fails_closed(self):
        self.assertEqual(
            mop_core.retry_send_readback_decision(
                "in-1", "Ответ", {"status": "error", "message": "timeout"}
            ),
            "unknown",
        )
        self.assertEqual(
            mop_core.retry_send_readback_decision(
                "in-1", "Ответ", provider([msg("other", "in", 100, "x")])
            ),
            "unknown",
        )

    def test_do_send_wires_readback_before_retry_post(self):
        src = inspect.getsource(mop_core.do_send)
        self.assertIn('if pre_status == "send_failed"', src)
        self.assertIn("retry_send_readback_decision", src)
        self.assertIn("fetch_chat_messages", src)
        self.assertIn('retry_decision != "safe_to_send"', src)
        self.assertLess(
            src.index('if pre_status == "send_failed"'),
            src.index("send_message as avito_send"),
        )


if __name__ == "__main__":
    unittest.main()
