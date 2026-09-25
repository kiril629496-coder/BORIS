import inspect

from app.api import messenger
from app.api import mop_combat_training


def test_effective_learning_rules_dedupes_and_blocks_identity_conflicts():
    rules = [
        {"id": 1, "rule": "Не сообщай клиенту, что запрос уже передан специалисту, пока это не подтверждено."},
        {"id": 2, "rule": "Не сообщай клиенту, что запрос уже передан специалисту, пока это не подтверждено."},
        {"id": 3, "rule": "Правило для МОПа: Меня зовут Борис, я помощник Марии. Наш эксперт ответит на все вопросы!"},
    ]
    out = messenger._mop_effective_learning_rules(rules)
    assert [x["id"] for x in out] == [1]


def test_negative_safety_rule_with_obligatory_contact_phrase_is_kept():
    rule = {
        "id": 28116,
        "rule": "Не сообщай, что специалист обязательно свяжется, пока это действие не подтверждено фактом системы.",
    }
    out = messenger._mop_effective_learning_rules([rule])
    assert out and out[0]["id"] == 28116


def test_sparring_learning_has_policy_gate_before_persist():
    src = inspect.getsource(mop_combat_training.sparring_learning)
    assert "MOP_TRAINING_POLICY_GATE_V1" in src
    assert "_mop_effective_learning_rules" in src
    assert "blocked_by_policy" in src
    assert src.index("_mop_effective_learning_rules") < src.index("INSERT INTO client_facts")


def test_real_dialog_feedback_has_same_policy_gate():
    src = inspect.getsource(mop_combat_training.real_dialog_feedback)
    assert "_mop_effective_learning_rules" in src
    assert "blocked_by_policy" in src
    assert src.index("_mop_effective_learning_rules") < src.index("INSERT INTO client_facts")
