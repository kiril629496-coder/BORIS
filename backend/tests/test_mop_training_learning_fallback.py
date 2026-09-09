import inspect

from app.api import messenger


def test_explicit_owner_training_rule_has_deterministic_provider_outage_fallback():
    rules = [{
        "rule": "Правило для МОПа: Если клиент пишет «TEST TRIGGER 47291», "
                "ответь ровно «TEST RULE APPLIED 47291» и ничего больше."
    }]
    assert messenger._mop_deterministic_learning_reply(
        "TEST TRIGGER 47291", rules
    ) == "TEST RULE APPLIED 47291"


def test_unrelated_rule_is_not_forced():
    rules = [{
        "rule": "Если клиент пишет «одобрение ипотеки», ответь «Уточню вашу ситуацию»."
    }]
    assert messenger._mop_deterministic_learning_reply(
        "Хочу подобрать квартиру", rules
    ) is None


def test_emergency_path_checks_training_before_generic_safe_reply():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    learned = src.index("_learned_emergency = _mop_deterministic_learning_reply(")
    generic = src.index('if _emergency_re.search(', learned)
    assert learned < generic
    assert "if _learned_emergency:" in src
