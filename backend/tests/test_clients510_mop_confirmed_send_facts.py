import contextlib
import io
import types
from unittest import mock

import pytest
from app.api import messenger as m

COLLATERAL="Одобрение ипотеки, рефинансирование и кредит под залог недвижимости."
REPLY=("Здравствуйте! Наша специализация — одобрение ипотеки, рефинансирование и консолидация кредитов, "
       "кредит под залог недвижимости. Спасибо, номер получил. Дальше по вашему запросу подключится менеджер.")


class Result:
    def __init__(self, value):
        self.value=value
    def mappings(self):
        return self
    def first(self):
        return self.value
    def fetchall(self):
        return self.value


class FakeDB:
    profiles={"business_a":{"company_niche":"","company_description":COLLATERAL,"company_advantages":""},
              "business_b":{"company_niche":"Монтаж окон","company_description":"Окна и балконы","company_advantages":""}}
    def __init__(self):
        self.seen=[]
    def execute(self, sql, params=None):
        q=str(sql); params=params or {}
        self.seen.append((q,params))
        if "FROM accounts" in q:
            assert "WHERE account_id=:a" in q
            assert set(params)=={"a"}
            return Result(self.profiles.get(params["a"]))
        if "FROM messenger_messages" in q:
            assert params["a"] in self.profiles
            return Result([("Напишите в мессенджер",)])
        raise AssertionError("Unexpected SQL")
    def close(self):
        pass


def invoke(account_id, reply, *, memory="", base_allowed=True):
    dbs=[]
    def session():
        d=FakeDB(); dbs.append(d); return d
    def confirmed(db, aid, **kwargs):
        assert aid==account_id
        assert kwargs["shared"] is False
        assert kwargs["include_rules"] is False
        return memory
    response=types.SimpleNamespace(status_code=200, json=lambda:{"id":"mock_provider_message"},text="")
    with mock.patch.object(m,"SessionLocal",side_effect=session), \
         mock.patch.object(m,"_mop_trial_access",return_value={"allowed":False}), \
         mock.patch.object(m,"get_manager_balance",return_value={"owner_pilot":False}), \
         mock.patch("app.services.subscription_gate.base_subscription_access",return_value={"allowed":base_allowed}), \
         mock.patch("app.api.client_memory.confirmed_context",side_effect=confirmed), \
         mock.patch.object(m,"_get_user_id_and_token",return_value=("mock_user","mock_token")), \
         mock.patch.object(m,"_avito_http_post",return_value=response) as post, \
         mock.patch.object(m.httpx.Client,"request",side_effect=AssertionError("No real provider I/O")), \
         contextlib.redirect_stdout(io.StringIO()):
        result=m.send_message(account_id,"mock_chat",reply)
        return result,post.call_count,dbs


@pytest.mark.parametrize("reply",[
    "Предоплата обязательна.",
    "Нужен депозит.",
    "Внесите залог.",
    "Для кредита под залог недвижимости нужна предоплата.",
    "Кредит под залог недвижимости доступен без депозита.",
])
def test_collateral_does_not_authorize_cash_or_advance(reply):
    assert m._mop_has_unconfirmed_prepayment(reply,COLLATERAL)
    assert "unconfirmed_prepayment" in m._mop_output_policy_violations(reply,"",COLLATERAL)


@pytest.mark.parametrize("reply, trusted",[
    ("Кредит под залог недвижимости.",COLLATERAL),
    ("Займ под залог квартиры.","Заём под залог квартиры."),
    ("Предоплата обязательна.","Предоплата обязательна."),
    ("Залог обязателен.","Залог обязателен."),
])
def test_confirmed_same_category_remains_supported(reply,trusted):
    assert not m._mop_has_unconfirmed_prepayment(reply,trusted)


@pytest.mark.parametrize("reply,trusted",[
    ("Нужен депозит.","Работа по предоплате."),
    ("Нужна предоплата.","Обеспечительный залог."),
    ("Кредит под залог автомобиля.",COLLATERAL),
    ("Кредит под залог недвижимости.","Окна и балконы."),
])
def test_different_terms_or_asset_are_not_authorized(reply,trusted):
    assert m._mop_has_unconfirmed_prepayment(reply,trusted)


def test_real_maria_handoff_passes_final_boundary_from_confirmed_profile():
    result,posts,_=invoke("business_a",REPLY)
    assert result["status"]=="ok"
    assert result["avito_message"]["id"]=="mock_provider_message"
    assert posts==1


def test_other_account_cannot_borrow_maria_fact():
    result,posts,_=invoke("business_b",REPLY)
    assert result["status"]=="blocked"
    assert "unconfirmed_prepayment" in result["policy_guard"]
    assert posts==0


def test_send_boundary_rejects_invented_deposit_despite_collateral_profile():
    result,posts,_=invoke("business_a","Необходим депозит.")
    assert result["status"]=="blocked" and posts==0


def test_customer_claims_do_not_supply_confirmed_terms():
    problems=m._mop_output_policy_violations(
        "Обязательна предоплата.","Клиент: Вы работаете по предоплате.","")
    assert "unconfirmed_prepayment" in problems


def test_confirmed_own_memory_is_available_at_final_boundary():
    result,posts,_=invoke("business_b","Предоплата обязательна.",memory="Предоплата обязательна.")
    assert result["status"]=="ok" and posts==1


def test_expired_base_gate_keeps_priority():
    result,posts,dbs=invoke("business_a",REPLY,base_allowed=False)
    assert result["status"]=="blocked"
    assert result["code"]=="subscription_expired" and posts==0
    assert not any("FROM accounts" in q for d in dbs for q,_ in d.seen)


def test_unknown_amount_remains_blocked_with_known_collateral():
    assert "unconfirmed_price" in m._mop_output_policy_violations(
        "Кредит под залог недвижимости за 999999 рублей.","",COLLATERAL)


def test_missing_account_never_reads_some_other_profile():
    class NoRead:
        def execute(self,*args):
            raise AssertionError("empty account should not query")
    assert m._mop_confirmed_send_context(NoRead(),"")==""


def test_fact_read_failure_does_not_authorize_collateral():
    response=types.SimpleNamespace(status_code=200,json=lambda:{},text="")
    with mock.patch.object(m,"_mop_confirmed_send_context",side_effect=RuntimeError("fact store unavailable")):
        result,posts,_=invoke("business_a",REPLY)
    assert result["status"]=="blocked" and posts==0


@pytest.mark.parametrize("reply,trusted",[
    ("Внесите предоплату 10000 рублей.",COLLATERAL+" Без предоплаты."),
    ("Внесите депозит 5000 рублей.",COLLATERAL+" Депозит не требуется."),
    ("Внесите залог наличными 10000 рублей.","Залоговое кредитование недвижимости."),
    ("Предоплата обязательна.","Предоплата не обязательна."),
    ("Предоплата обязательна.","Работа по предоплате."),
    ("Кредит под залог недвижимости.","Мы не предоставляем кредит под залог недвижимости."),
    ("Нужен депозит.","Обсуждаем депозит после согласования."),
    ("Предоплата 50%.","Предоплата 20%."),
])
def test_peer_review_no_negation_or_generic_term_authorization(reply,trusted):
    assert m._mop_has_unconfirmed_prepayment(reply,trusted)
    assert "unconfirmed_prepayment" in m._mop_output_policy_violations(reply,"",trusted)


@pytest.mark.parametrize("clause",["Предоплата не требуется.","Депозит обязателен.","Предоплата 20%."])
def test_exact_confirmed_payment_condition_preserves_polarity_and_value(clause):
    assert not m._mop_has_unconfirmed_prepayment(clause,clause)


def test_own_confirmed_negation_cannot_authorize_new_payment_at_send_boundary():
    result,posts,_=invoke("business_a","Предоплата обязательна.",memory="Предоплата не обязательна.")
    assert result["status"]=="blocked" and posts==0


@pytest.mark.parametrize("separator",[".",","])
def test_decimal_payment_condition_is_compared_in_full(separator):
    reply="Предоплата 1"+separator+"9 тысячи рублей."
    trusted="Предоплата 1"+separator+"5 тысячи рублей."
    assert m._mop_has_unconfirmed_prepayment(reply,trusted)
    result,posts,_=invoke("business_a",reply,memory=trusted)
    assert result["status"]=="blocked" and posts==0
    assert "unconfirmed_prepayment" in result["policy_guard"]


def test_identical_decimal_payment_condition_remains_supported():
    assert not m._mop_has_unconfirmed_prepayment("Предоплата 1,5 тысячи рублей.","Предоплата 1.5 тысячи рублей.")
