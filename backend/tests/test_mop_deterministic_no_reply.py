import inspect
import unittest

from app import mop_core
from app.api import messenger


class MopDeterministicNoReplyTests(unittest.TestCase):
    def test_gratitude_ack_is_zero_ai_no_reply(self):
        self.assertEqual(
            mop_core.deterministic_no_reply_reason("Отлично, спасибо."),
            "gratitude_ack",
        )
        self.assertEqual(
            mop_core.deterministic_no_reply_reason("Большое спасибо вам!"),
            "gratitude_ack",
        )

    def test_simple_ack_is_zero_ai_no_reply_but_scheduling_detail_is_not(self):
        for text in ("Хорошо", "ОК", "Окей", "Понял", "Договорились", "Принято"):
            self.assertEqual(mop_core.deterministic_no_reply_reason(text), "simple_ack")
        self.assertIsNone(mop_core.deterministic_no_reply_reason("Хорошо, завтра в 15:00"))
        self.assertIsNone(mop_core.deterministic_no_reply_reason("Хорошо?"))

    def test_vendor_solicitation_needs_multiple_strong_signals(self):
        text = (
            "Просто вижу что вы можете больше клиентов принимать. "
            "Работаю с Авито, есть регулярный приход по обращениям. "
            "Ищем кому можем передать заявки. Сотрудничаем только за результат. "
            "Было бы интересно созвониться и обсудить?"
        )
        self.assertEqual(
            mop_core.deterministic_no_reply_reason(text),
            "vendor_solicitation",
        )

    def test_questions_and_ambiguous_customer_text_are_never_auto_closed(self):
        self.assertIsNone(
            mop_core.deterministic_no_reply_reason("Спасибо, а какая цена?")
        )
        self.assertIsNone(
            mop_core.deterministic_no_reply_reason("Нужна бытовка на месяц")
        )
        self.assertIsNone(
            mop_core.deterministic_no_reply_reason("Можно созвониться и обсудить доставку?")
        )

    def test_explicit_human_request_is_deterministic_handoff(self):
        for text in (
            "Соедините с менеджером",
            "Хочу поговорить с человеком",
            "Можно живого человека?",
            "Позовите оператора",
        ):
            self.assertEqual(
                mop_core.deterministic_handoff_reason(text),
                "client_requested_human",
            )
        self.assertIsNone(
            mop_core.deterministic_handoff_reason("Можно созвониться и обсудить доставку?")
        )

    def test_business_partnership_offer_is_zero_ai_handoff(self):
        for text in (
            "Интересует ли вас доп заработок по продуктам страхования недвижимости?",
            "Предлагаю сотрудничество по страхованию объектов",
            "Есть партнерская программа для агентств недвижимости",
        ):
            self.assertEqual(
                mop_core.deterministic_handoff_reason(text),
                "business_partnership_request",
            )

    def test_begin_incoming_and_background_recovery_use_same_policy(self):
        core_src = inspect.getsource(mop_core.begin_incoming)
        recovery_src = inspect.getsource(mop_core.reconcile_trivial_human_required)
        scheduler_src = inspect.getsource(messenger)
        self.assertIn("deterministic_no_reply_reason", core_src)
        self.assertIn("no_reply_required", core_src)
        self.assertIn("MOP_DETERMINISTIC_NO_REPLY_V1", core_src)
        self.assertIn("deterministic_no_reply_reason", recovery_src)
        self.assertIn("external_action", recovery_src)
        self.assertIn("reconcile_trivial_human_required", scheduler_src)


if __name__ == "__main__":
    unittest.main()
