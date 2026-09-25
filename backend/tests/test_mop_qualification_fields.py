import json
import unittest

from app.api.messenger import (
    _normalize_mop_structured,
    _repair_meta_client_reply,
    _initial_short_subject_clarification,
    _deterministic_mop_qualification,
    _history_role_messages,
)
from app.crm.bridge import _qualification_to_lead_fields


class MopQualificationFieldsTests(unittest.TestCase):
    def test_structured_fields_are_preserved_without_invention(self):
        raw = json.dumps({
            "reply_text": "Подскажите адрес доставки.",
            "intent": "аренда бытовки",
            "qualification_stage": "QUALIFIED",
            "lead_temperature": "warm",
            "target_action": "получить адрес доставки",
            "phone_received": False,
            "crm_action": "create_deal",
            "next_action": "уточнить адрес доставки",
            "reactivation_candidate": False,
            "qualification_fields": {
                "нужный размер бытовки": "6×2,4 м",
                "аренда или покупка": "аренда",
                "на какой срок нужна бытовка": "2 месяца",
                "адрес объекта/доставки": None,
                "мусор": {"nested": "forbidden"},
            },
        }, ensure_ascii=False)

        reply, analysis, err = _normalize_mop_structured(raw)

        self.assertIsNone(err)
        self.assertEqual(reply, "Подскажите адрес доставки.")
        self.assertEqual(analysis["qualification_fields"]["нужный размер бытовки"], "6×2,4 м")
        self.assertEqual(analysis["qualification_fields"]["аренда или покупка"], "аренда")
        self.assertEqual(analysis["qualification_fields"]["на какой срок нужна бытовка"], "2 месяца")
        self.assertIsNone(analysis["qualification_fields"]["адрес объекта/доставки"])
        self.assertNotIn("мусор", analysis["qualification_fields"])

    def test_handoff_text_forces_machine_handoff_even_if_model_flag_is_false(self):
        raw = json.dumps({
            "reply_text": "Передам вопрос менеджеру, он уточнит актуальную стоимость.",
            "qualification_stage": "ENGAGED",
            "human_handoff": False,
            "handoff_reason": None,
        }, ensure_ascii=False)
        _, analysis, err = _normalize_mop_structured(raw)
        self.assertIsNone(err)
        self.assertIs(analysis["human_handoff"], True)

    def test_explicit_handoff_reason_forces_machine_handoff(self):
        raw = json.dumps({
            "reply_text": "Уточню точную стоимость доставки.",
            "qualification_stage": "QUALIFIED",
            "human_handoff": False,
            "handoff_reason": "индивидуальный расчет доставки",
        }, ensure_ascii=False)
        _, analysis, err = _normalize_mop_structured(raw)
        self.assertIsNone(err)
        self.assertIs(analysis["human_handoff"], True)

    def test_missing_fields_degrade_to_empty_object(self):
        raw = json.dumps({
            "reply_text": "Уточню и вернусь к вам.",
            "qualification_stage": "ENGAGED",
        }, ensure_ascii=False)
        _, analysis, err = _normalize_mop_structured(raw)
        self.assertIsNone(err)
        self.assertEqual(analysis["qualification_fields"], {})

    def test_handoff_reason_dict_is_normalized_to_reason_text(self):
        raw = json.dumps({
            "reply_text": "Передам вопрос коллеге.",
            "human_handoff": True,
            "handoff_reason": {"reason": "нужен точный расчёт"},
        }, ensure_ascii=False)
        _, analysis, err = _normalize_mop_structured(raw)
        self.assertIsNone(err)
        self.assertEqual(analysis["handoff_reason"], "нужен точный расчёт")
        self.assertNotIn("{'reason'", analysis["handoff_reason"])

    def test_meta_reply_for_short_subject_becomes_human_clarification(self):
        reply, analysis, reason = _repair_meta_client_reply(
            "Предложу созвониться и получить номер телефона",
            "Камри",
            {"human_handoff": True, "handoff_reason": "Хочет связаться для продвижения"},
        )
        self.assertEqual(reply, "Понял, вас интересует Камри. Подскажите, пожалуйста, что именно хотите уточнить?")
        self.assertIs(analysis["human_handoff"], False)
        self.assertIsNone(analysis["handoff_reason"])
        self.assertEqual(reason, "meta_reply_subject_clarification")

    def test_normal_client_reply_is_not_rewritten(self):
        reply, analysis, reason = _repair_meta_client_reply(
            "Понял, интересует Camry. Какой год рассматриваете?",
            "Камри",
            {"human_handoff": False},
        )
        self.assertEqual(reply, "Понял, интересует Camry. Какой год рассматриваете?")
        self.assertIsNone(reason)
        self.assertIs(analysis["human_handoff"], False)

    def test_initial_short_subject_is_zero_ai_candidate(self):
        self.assertEqual(
            _initial_short_subject_clarification("Камри", "Клиент: Камри"),
            "Понял, вас интересует Камри. Подскажите, пожалуйста, что именно хотите уточнить?",
        )

    def test_short_answer_inside_existing_dialog_is_not_intercepted(self):
        self.assertIsNone(
            _initial_short_subject_clarification("Уфа", "Мы: В каком городе нужен замер?\nКлиент: Уфа")
        )

    def test_short_price_question_is_not_intercepted(self):
        self.assertIsNone(_initial_short_subject_clarification("Цена?", "Клиент: Цена?"))

    def test_greeting_is_not_treated_as_subject(self):
        self.assertIsNone(_initial_short_subject_clarification("Здравствуйте", "Клиент: Здравствуйте"))

    def test_maria_short_interest_first_turn_is_instant_clarification(self):
        self.assertEqual(
            _initial_short_subject_clarification(
                "добрый день интересует ипотека",
                "Клиент: добрый день интересует ипотека",
            ),
            "Добрый день! Понял ваш запрос. Подскажите, пожалуйста, что именно хотите уточнить?",
        )

    def test_specific_mortgage_price_question_still_uses_normal_guard_or_ai(self):
        self.assertIsNone(
            _initial_short_subject_clarification(
                "добрый день интересует сколько стоит одобрение ипотеки",
                "Клиент: добрый день интересует сколько стоит одобрение ипотеки",
            )
        )

    def test_multiline_client_block_is_preserved_as_one_message(self):
        history = (
            "Клиент: Нужна бытовка 6 х2,4\n"
            "Для строителей\n"
            "Территориально ул. Шостаковича 1\n"
            "На месяц с оформлением на юр. лицо\n"
            "Мы: Уточню формат."
        )
        self.assertEqual(
            _history_role_messages(history, "client"),
            ["Нужна бытовка 6 х2,4\nДля строителей\nТерриториально ул. Шостаковича 1\nНа месяц с оформлением на юр. лицо"],
        )
        self.assertEqual(_history_role_messages(history, "ours"), ["Уточню формат."])

    def test_multiline_explicit_fields_are_extracted_without_ai(self):
        history = (
            "Клиент: Нужна бытовка 6 х2,4\n"
            "Для строителей\n"
            "Территориально ул. Шостаковича 1\n"
            "На месяц с оформлением на юр. лицо"
        )
        analysis = _deterministic_mop_qualification(
            history,
            "Уточните, пожалуйста: речь об аренде на указанный срок или о покупке?",
            {"human_handoff": False, "qualification_fields": {"аренда или покупка": None}},
        )
        fields = analysis["qualification_fields"]
        self.assertEqual(fields["нужный размер бытовки"], "6×2,4 м")
        self.assertEqual(fields["на какой срок нужна бытовка"], "1 месяц")
        self.assertEqual(fields["адрес объекта/доставки"], "ул. Шостаковича 1")
        self.assertEqual(fields["оформление"], "юридическое лицо")
        self.assertIsNone(fields["аренда или покупка"])

    def test_explicit_rent_fills_format_but_temporary_term_alone_does_not(self):
        rent = _deterministic_mop_qualification(
            "Клиент: Нужна бытовка в аренду на 2 месяца",
            "Принял.",
            {"qualification_fields": {}},
        )["qualification_fields"]
        ambiguous = _deterministic_mop_qualification(
            "Клиент: Нужна бытовка на 2 месяца",
            "Уточните формат.",
            {"qualification_fields": {}},
        )["qualification_fields"]
        self.assertEqual(rent["аренда или покупка"], "аренда")
        self.assertEqual(rent["на какой срок нужна бытовка"], "2 месяца")
        self.assertNotIn("аренда или покупка", ambiguous)

    def test_structured_fields_reach_existing_crm_lead_columns(self):
        vals = _qualification_to_lead_fields({
            "qualification_fields": {
                "нужный размер бытовки": "6×2,4 м",
                "аренда или покупка": "аренда",
                "на какой срок нужна бытовка": "1 месяц",
                "адрес объекта/доставки": "ул. Шостаковича 1",
                "оформление": "юридическое лицо",
            }
        })
        self.assertEqual(vals["object_place"], "ул. Шостаковича 1")
        self.assertEqual(vals["rent_term"], "1 месяц")
        self.assertIn("6×2,4 м", vals["need_type"])
        self.assertIn("аренда", vals["need_type"])
        self.assertIn("юридическое лицо", vals["need_type"])


if __name__ == "__main__":
    unittest.main()
