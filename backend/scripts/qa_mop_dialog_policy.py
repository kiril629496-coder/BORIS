#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression gate for MOP client-facing dialogue quality.

No provider calls.  This proves deterministic safeguards that must remain true
for production MOP and combat training.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.messenger import _humanize_mop_reply_text, _mop_output_policy_violations

TRUSTED = (
    "Описание компании: одобрение ипотеки; рефинансирование и консолидация "
    "кредитов; кредит под залог недвижимости; продажа и покупка недвижимости; новостройки"
)
HISTORY = """Клиент: Дом уже сдан. Остаток около 8 миллионов. Доход официальный не весь.
Клиент: Если реально можно попробовать, давайте созвонимся. Телефон я уже оставила в заявке."""

checks = []

def expect(code, reply, history=HISTORY):
    got = _mop_output_policy_violations(reply, history, TRUSTED)
    checks.append((code in got, f"{code}: {got}"))

expect("unconfirmed_assurance", "У нас есть программы, подходящие даже при неполном официальном доходе.")
expect("unconfirmed_assurance", "Можем рассмотреть несколько вариантов даже при неполном официальном доходе.")
expect("unconfirmed_callback_time", "Сейчас свяжусь с вами по указанному номеру.")
expect("unconfirmed_callback_time", "Сегодня вечером позвоним вам.")
expect("unconfirmed_callback_time", "Сейчас посмотрю заявку. Готов обсудить всё прямо сейчас или позже перезвонить.")
expect("unconfirmed_professional_documents", "Подготовьте договор долевого участия, акт приёма-передачи и справку о доходах.")
expect(
    "unconfirmed_professional_documents",
    "Для начала подготовьте документы по текущей рассрочке, сведения о квартире и личные данные.",
    HISTORY + "\nКлиент: Что лучше заранее подготовить к звонку?",
)
expect("unconfirmed_financial_benefit", "Это позволит зафиксировать ставку и снизить ежемесячный платеж.")

safe = _mop_output_policy_violations(
    "С таким запросом работаем. Детали по вашему случаю лучше уточнит специалист.",
    HISTORY,
    TRUSTED,
)
checks.append((safe == [], f"safe_reply: {safe}"))

human = _humanize_mop_reply_text(
    "Ситуация понятна. Давайте обсудим детали, чтобы подобрать оптимальное решение. Жду вас на связи."
)
checks.append(("ситуация понятна" not in human.lower(), f"humanize_filler: {human}"))
checks.append(("оптимальн" not in human.lower(), f"humanize_optimal: {human}"))
checks.append(("жду вас на связи" not in human.lower(), f"humanize_closer: {human}"))

multi = _humanize_mop_reply_text(
    "В таком случае можем рассмотреть варианты. Давайте обсудим детали подробнее? Удобнее будет пообщаться по телефону?"
)
checks.append((multi.count("?") == 1, f"humanize_one_question: {multi}"))

failed = [msg for ok, msg in checks if not ok]
for ok, msg in checks:
    print(("PASS" if ok else "FAIL"), msg)
if failed:
    raise SystemExit(1)
print(f"MOP_DIALOG_POLICY_QA=PASS checks={len(checks)}")
