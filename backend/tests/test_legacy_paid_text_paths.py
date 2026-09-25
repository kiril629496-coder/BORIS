import inspect
import unittest

from app.api import avito, prompt_check


class LegacyPaidTextPathTests(unittest.TestCase):
    def test_prompt_check_uses_shared_exactly_once_guard(self):
        src = inspect.getsource(prompt_check.check_prompt)
        self.assertIn("_ff_guarded_openai_response", src)
        self.assertIn("prompt-check:", src)
        self.assertNotIn("requests.post", src)
        self.assertNotIn("_rt_guarded_post", src)
        self.assertNotIn("_lc(", src)

    def test_avito_chatgpt_rewrite_uses_shared_exactly_once_guard(self):
        src = inspect.getsource(avito.rewrite_text)
        self.assertIn("_ff_guarded_openai_response", src)
        self.assertIn("rewrite-text:", src)
        self.assertNotIn("_requests_local.post", src)
        self.assertNotIn("get_intl_requests_proxies", src)


if __name__ == "__main__":
    unittest.main()
