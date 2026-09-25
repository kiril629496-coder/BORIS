import os
import unittest

from app import telegram_bot


class TelegramDirectTransportTests(unittest.TestCase):
    def test_session_does_not_inherit_process_proxy(self):
        self.assertIs(telegram_bot._TELEGRAM_SESSION.trust_env, False)

    def test_error_redacts_bot_token(self):
        exc = RuntimeError("GET https://api.telegram.org/bot123456:SECRET_TOKEN/getUpdates failed")
        safe = telegram_bot._safe_telegram_error(exc)
        self.assertIn("bot<redacted>/getUpdates", safe)
        self.assertNotIn("SECRET_TOKEN", safe)
        self.assertNotIn("123456:", safe)


if __name__ == '__main__':
    unittest.main()
