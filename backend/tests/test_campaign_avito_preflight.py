from app.services.campaign_avito_preflight import build_avito_preflight

def base():
    return {
        "campaign_id":517,"account_id":"acc","items":30,"approved":30,
        "account_bound":True,"publication_locked":False,"intake_ready":True,
        "projection_error":None,"semantic_ready":True,"semantic_blocked":0,
        "image_unique":True,"content_unique":True,"validation_state":"READY",
        "avito_connected":True,"autoload_ready":True,"can_send":True,
        "photos":270,"blocked_reason":None,
    }

def test_pass_is_exact_delivery_truth():
    out=build_avito_preflight(base(),wallet={"read_ok":True,"funding_state":"BALANCE_KNOWN","real_rub":100})
    assert out["state"]=="PASS"
    assert out["can_publish"] is True
    assert not out["blockers"]

def test_content_ready_but_autoload_blocked_is_not_campaign_failure():
    d=base(); d.update(autoload_ready=False,can_send=False,autoload_message="Autoload 403")
    out=build_avito_preflight(d)
    assert out["state"]=="READY_AVITO_BLOCKED"
    assert out["can_publish"] is False
    assert any(x["code"]=="autoload_access" for x in out["blockers"])

def test_missing_required_data_is_needs_data():
    d=base(); d.update(intake_ready=False,can_send=False,projection_error={"message":"Нужны параметры"})
    out=build_avito_preflight(d)
    assert out["state"]=="NEEDS_DATA"
    assert out["primary_action"]=="Заполнить обязательные данные"

def test_submitted_campaign_is_locked_exactly_once():
    d=base(); d.update(publication_locked=True,can_send=False)
    out=build_avito_preflight(d)
    assert out["state"]=="ALREADY_SUBMITTED"
    assert out["can_publish"] is False

def test_wallet_and_identity_are_advisory_not_hidden_publish_gates():
    d=base()
    out=build_avito_preflight(
        d,
        wallet={"read_ok":True,"funding_state":"BLOCKED_LOW_BALANCE","real_rub":0},
        identity={"duplicate_feed_identities":["dup"],"existing_avito_items":2},
    )
    assert out["can_publish"] is True
    by_code={x["code"]:x for x in out["checks"]}
    assert by_code["wallet"]["status"]=="WARN" and by_code["wallet"]["blocking"] is False
    assert by_code["identity_reconcile"]["status"]=="WARN" and by_code["identity_reconcile"]["blocking"] is False

def test_marketing_kill_switch_is_real_blocker():
    out=build_avito_preflight(base(),marketing_blocked=True,marketing_reason="Отключено владельцем")
    assert out["state"]=="ACTIONS_DISABLED"
    assert out["can_publish"] is False
    assert any(x["code"]=="marketing_actions" for x in out["blockers"])
