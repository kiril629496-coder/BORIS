from app.api.campaigns import _ff_formula_meta, _ff_learned_formula


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _DB:
    def __init__(self, value):
        self.value = value
        self.params = None

    def execute(self, _query, params):
        self.params = params
        return _ScalarResult(self.value)


def test_campaign_formula_reuses_account_leader_from_observed_memory():
    db = _DB('{"account_id":"account-a","items":[{"copy_formula_id":"EXPERT","views":100,"contacts":8,"conversion":8.0},{"copy_formula_id":"PAS","views":120,"contacts":2,"conversion":1.7}]}')
    assert _ff_learned_formula(db, "account-a") == "EXPERT"
    assert db.params == {"a": "account-a"}
    result = _ff_formula_meta({"variant": 1, "learned_formula_id": "EXPERT"}, "Кухня на заказ")
    assert result["copy_formula_id"] == "EXPERT"
    assert result["copy_formula_version"] == "1"


def test_campaign_formula_fails_closed_without_proven_observation():
    db = _DB('{"account_id":"account-a","items":[{"copy_formula_id":"EXPERT","views":0,"contacts":0,"conversion":0}]}')
    assert _ff_learned_formula(db, "account-a") == ""
    assert _ff_formula_meta({"variant": 1}, "Кухня на заказ")["copy_formula_id"] == "PAS"
    assert _ff_formula_meta({"variant": 1, "learned_formula_id": "UNKNOWN"}, "Кухня на заказ")["copy_formula_id"] == "PAS"
