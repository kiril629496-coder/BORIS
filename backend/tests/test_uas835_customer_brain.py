from app.services.mop_customer_brain import identity_from_chat


def _chat(chat_id='chat-1', seller='seller-1', buyer='buyer-1'):
    return {
        'id': chat_id,
        'context': {'type': 'item', 'value': {'user_id': seller}},
        'users': [{'id': seller, 'name': 'Seller'}, {'id': buyer, 'name': 'Buyer'}],
    }


def test_same_buyer_across_listings_gets_same_account_local_key():
    a = identity_from_chat('account-a', _chat('chat-1'))
    b = identity_from_chat('account-a', _chat('chat-2'))
    assert a['customer_key'] == b['customer_key']
    assert a['cross_chat'] is True
    assert a['confidence'] == 100


def test_same_external_buyer_never_crosses_business_account_boundary():
    a = identity_from_chat('account-a', _chat('chat-1'))
    b = identity_from_chat('account-b', _chat('chat-2'))
    assert a['customer_key'] != b['customer_key']


def test_missing_buyer_identity_falls_back_to_single_chat_only():
    ch = {'id': 'chat-x', 'context': {'value': {'user_id': 'seller'}}, 'users': [{'id': 'seller'}]}
    a = identity_from_chat('account-a', ch)
    b = identity_from_chat('account-a', {**ch, 'id': 'chat-y'})
    assert a['source'] == 'chat_fallback'
    assert a['cross_chat'] is False
    assert a['customer_key'] != b['customer_key']
