import inspect
from app.services import marketing_inventory


def test_inventory_provider_reads_keep_account_identity():
    src=inspect.getsource(marketing_inventory.refresh_marketing_inventory)
    assert '_fetch_items(account_id, token, "active")' in src
    assert '_fetch_items(account_id, token, "old")' in src
    assert '_fetch_stats(account_id, token, str(avito_user_id or ""), ids, days=days)' in src
    assert 'MARKETING_INVENTORY_ACCOUNT_THROTTLE_IDENTITY_V1' in src
