from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPX = ROOT / "app/api/cpxpromo.py"


def _source() -> str:
    return CPX.read_text(encoding="utf-8")


def test_direct_setmanual_success_requires_exact_postwrite_readback():
    s = _source()
    block = s[s.index('def set_manual('):s.index('@router.post("/set_auto")')]
    assert 'CPXPROMO_SETMANUAL_EXACT_POSTWRITE_VERIFY_V1' in block
    attempting = block.index('_direct_receipt_mark(body.account_id, _request_key, "attempting")')
    provider = block.index('_provider_set_manual_bid(')
    verify = block.index('httpx.get(f"{BASE}/getBids/{body.item_id}"', provider)
    exact = block.index('if _observed_penny != int(body.bid_penny):')
    succeeded = block.index('_direct_receipt_mark(body.account_id, _request_key, "succeeded"')
    audit = block.index('_audit_log(body.account_id, "cpxpromo_set_manual"')
    assert attempting < provider < verify < exact < succeeded < audit
    assert 'provider_5xx_ambiguous' in block
    assert 'delivery_unknown' in block
    assert 'parsedate_to_datetime' in block


def test_direct_remove_success_requires_promotion_absent_readback():
    s = _source()
    block = s[s.index('def remove_promotion('):s.index('class SetManualBody')]
    assert 'CPXPROMO_REMOVE_EXACT_POSTWRITE_VERIFY_V1' in block
    attempting = block.index('_remove_receipt_mark(body.account_id, _request_key, "attempting")')
    provider = block.index('_provider_remove_promotion(')
    verify = block.index('httpx.post(f"{BASE}/getPromotionsByItemIds"')
    proof = block.index('_promotion_off = bool(')
    succeeded = block.index('_remove_receipt_mark(body.account_id, _request_key, "succeeded"')
    action_log = block.index('_remove_log_action(')
    assert attempting < provider < verify < proof < succeeded < action_log
    assert 'provider_5xx_ambiguous' in block
    assert 'postremove_promotion_still_present_or_unproven' in block


def test_direct_money_routes_have_final_shared_throttle_fence_after_receipt():
    s = _source()
    setmanual = s[s.index('def set_manual('):s.index('@router.post("/set_auto")')]
    assert setmanual.index('_direct_receipt_mark(body.account_id, _request_key, "attempting")') < setmanual.index('_final_shared_retry = account_throttle_remaining(body.account_id)') < setmanual.index('_provider_set_manual_bid(')

    remove = s[s.index('def remove_promotion('):s.index('class SetManualBody')]
    assert remove.index('_remove_receipt_mark(body.account_id, _request_key, "attempting")') < remove.index('_retry = _direct_cpx_throttle_remaining(body.account_id)', remove.index('_remove_receipt_mark(body.account_id, _request_key, "attempting")')) < remove.index('_provider_remove_promotion(')
