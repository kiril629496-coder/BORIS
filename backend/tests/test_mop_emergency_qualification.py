import unittest

from app.api.messenger import _mop_emergency_next_question


class MopEmergencyQualificationTests(unittest.TestCase):
    def test_planeta_skips_known_city_and_furniture_type(self):
        questions = [
            "В каком городе нужен проект?",
            "Какой тип мебели нужен?",
            "Есть ли примерные размеры помещения или зоны?",
            "Что важнее по комплектации и хранению?",
        ]
        history = """Клиент: Уфа
Мы: Отлично, понял.
Клиент: Нужна кухня"""
        self.assertEqual(
            _mop_emergency_next_question(questions, history),
            "Есть ли примерные размеры помещения или зоны?",
        )

    def test_bytovki_after_size_asks_rent_or_purchase(self):
        questions = [
            "нужный размер бытовки",
            "аренда или покупка",
            "на какой срок нужна бытовка",
            "адрес объекта/доставки",
        ]
        history = "Клиент: Нужна бытовка 6x2,4"
        self.assertEqual(
            _mop_emergency_next_question(questions, history),
            "Подскажите, пожалуйста, нужна аренда или покупка бытовки?",
        )

    def test_known_phone_does_not_repeat_phone_question(self):
        questions = ["Получить номер телефона для связи или время для замера"]
        history = "Клиент: Мой номер +7 999 123-45-67"
        self.assertEqual(_mop_emergency_next_question(questions, history), "")

    def test_no_required_questions_returns_empty(self):
        self.assertEqual(_mop_emergency_next_question([], "Клиент: тест"), "")


if __name__ == "__main__":
    unittest.main()
