import inspect
import unittest

from app.api import messenger


class MessengerPaidSafetyTests(unittest.TestCase):
    def test_dialogue_analysis_uses_shared_exactly_once_guard(self):
        src = inspect.getsource(messenger.analyze_dialogues)
        self.assertIn('_ff_guarded_openai_response', src)
        self.assertIn('messenger-analysis:', src)
        self.assertIn('operation="messenger_dialogue_analysis"', src)
        self.assertNotIn('https://api.openai.com/v1/chat/completions', src)
        self.assertNotIn('_rt_guarded_post(', src)

    def test_analysis_script_uses_shared_exactly_once_guard(self):
        src = inspect.getsource(messenger.analysis_make_script)
        self.assertIn('_ff_guarded_openai_response', src)
        self.assertIn('messenger-analysis-script:', src)
        self.assertIn('operation="messenger_analysis_script"', src)
        self.assertNotIn('https://api.openai.com/v1/chat/completions', src)
        self.assertNotIn('_rt_guarded_post(', src)


if __name__ == '__main__':
    unittest.main()
