import inspect
import unittest

from app.api import inbox_slots


class MopAttentionQaIsolationTests(unittest.TestCase):
    def test_owner_attention_excludes_synthetic_qa_drafts(self):
        src = inspect.getsource(inbox_slots.inbox_attention)
        self.assertIn("account_id NOT LIKE '__qa_%'", src)
        self.assertIn("avito_chat_id NOT LIKE 'qa_%'", src)
        self.assertIn("stale_unanswered_client_inquiry", src)
        self.assertIn("Просроченный вопрос клиента", src)
        self.assertIn("business_partnership_request", src)
        self.assertIn("Партнёрское предложение", src)
        self.assertIn("MOP_ATTENTION_ENTITLEMENT_FILTER_V1", src)
        self.assertIn("_mop_entitled", src)
        self.assertIn("get_manager_balance", src)


if __name__ == '__main__':
    unittest.main()
