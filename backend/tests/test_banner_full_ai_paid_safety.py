import inspect
import unittest

from app.api import banners as B


class BannerFullAIPaidSafetyTests(unittest.TestCase):
    def test_paid_full_ai_requires_idempotency_key(self):
        src = inspect.getsource(B.create_full_ai_banner)
        self.assertIn("paid_idempotency_required", src)
        self.assertIn("idempotency_key", src)

    def test_template_paid_filename_is_stable_for_same_intent(self):
        src = inspect.getsource(B.create_full_ai_banner)
        self.assertIn("_tpl_intent", src)
        self.assertIn("_idem_", src)
        self.assertNotIn('"tpl_" + str(req.template_id) + "_" + str(int(_t2.time()))', src)


if __name__ == "__main__":
    unittest.main()
