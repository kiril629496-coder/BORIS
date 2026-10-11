from pathlib import Path

ROOT=Path('/root/BORIS')
API=ROOT/'backend/app/api/internal_messenger.py'

def test_internal_messenger_readiness_is_read_only_and_account_scoped():
    source=API.read_text(encoding='utf-8')
    start=source.index('@router.get("/readiness")')
    end=source.index('@router.get("/people")',start)
    block=source[start:end]
    assert '_access(db, user, account_id)' in block
    assert 'GENERAL_CHAT_TITLE' in block
    assert 'internal_messenger_events' in block
    assert 'lead_push_subscriptions' in block
    assert 'events/stream' in block
    assert 'ensure_general_conversation_membership' not in block
    assert 'INSERT ' not in block
    assert 'UPDATE ' not in block
    assert 'DELETE ' not in block


def test_internal_messenger_readiness_exposes_durable_observability_without_secrets():
    source=API.read_text(encoding='utf-8')
    start=source.index('@router.get("/readiness")')
    end=source.index('@router.get("/people")',start)
    block=source[start:end]
    assert 'events_5m' in block
    assert 'failed_deliveries_24h' in block
    assert 'pending_outbox' in block
    assert 'lead_notification_deliveries' in block
    assert 'lead_notification_outbox' in block
    # Internal endpoint_hash ownership joins are allowed; response payloads are not.
    import ast
    tree = ast.parse(block)
    returned = [node.value for node in ast.walk(tree) if isinstance(node, ast.Return)]
    assert returned
    response_source = '\n'.join(ast.unparse(node) for node in returned)
    assert 'endpoint' not in response_source
    assert 'subscription_enc' not in response_source
    assert 's.user_id=:u' in block
    assert "target_user_ids' @> jsonb_build_array" in block
    assert 'c.id::text=o.payload_json' in block
