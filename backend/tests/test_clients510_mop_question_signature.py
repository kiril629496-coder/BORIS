"""Regression for persisted Albert draft17911; no DB/provider I/O."""
import importlib.util
import os
import sys
from pathlib import Path
import pytest

if os.environ.get("BORIS_MOP_QUALITY_SOURCE"):
    target=Path(os.environ["BORIS_MOP_QUALITY_SOURCE"])
    spec=importlib.util.spec_from_file_location("app.api.messenger_quality_candidate",target)
    m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m
    spec.loader.exec_module(m)
else:
    from app.api import messenger as m

ASKED="Чтобы рассчитать стоимость пластикового окна для проема 3000×5800 мм, уточните, пожалуйста, какой стеклопакет вам необходим и требуется ли установка?"
REPLY="Чтобы посчитать стоимость пластикового окна для проёма 3000x5800 мм, уточните, пожалуйста, какой стеклопакет вам нужен и требуется ли установка?"
HISTORY="Клиент: Возможен вариант с пластиковым профилем\nМы: "+ASKED+"\nКлиент: Да требуется"

def test_actual_albert17911_repetition_is_caught_by_generation_policy():
    assert m._mop_reply_repeats_answered_question(REPLY,HISTORY)
    assert "repeated_answered_question:any" in m._mop_output_policy_violations(REPLY,HISTORY)

@pytest.mark.parametrize("separator",["x","х","×"," x "," х "," × "])
def test_dimension_separators_have_identical_question_signature(separator):
    baseline=m._mop_question_signature("Проём 3000×5800 подходит?")
    assert m._mop_question_signature("Проём 3000"+separator+"5800 подходит?")==baseline
    assert {"3000","5800"}.issubset(baseline)

@pytest.mark.parametrize("answer",["Повторите, пожалуйста","Не понял вопрос","Что имеете в виду?"])
def test_request_for_clarification_does_not_close_question(answer):
    history="Мы: "+ASKED+"\nКлиент: "+answer
    assert not m._mop_reply_repeats_answered_question(REPLY,history)

def test_question_without_client_answer_remains_open():
    assert not m._mop_reply_repeats_answered_question(REPLY,"Мы: "+ASKED)

def test_no_cross_dialogue_memory():
    history="Мы: В каком городе нужен расчёт?\nКлиент: Уфа"
    assert not m._mop_reply_repeats_answered_question(REPLY,history)

def test_other_question_is_not_closed_by_yes():
    assert not m._mop_reply_repeats_answered_question("Когда удобен замер?",HISTORY)

def test_normalization_preserves_actual_numbers():
    a=m._mop_question_signature("3000x5800 подходит?")
    b=m._mop_question_signature("2000x4700 подходит?")
    assert a!=b and {"3000","5800"} <= a and {"2000","4700"} <= b

def test_letter_x_outside_two_numbers_is_unchanged():
    assert m._mop_question_signature("Холодный профиль нужен?")=={"холодн","профил"}

def test_actual_answer_does_not_create_glazing_or_installation_facts():
    # The change is question identity only, with no qualification mutation.
    result=m._deterministic_mop_qualification(HISTORY, "Принял уточнение.", {})
    fields=result.get("qualification_fields") or {}
    assert not any(k in fields for k in ("glass_type","glazing_type","installation","installation_required"))
