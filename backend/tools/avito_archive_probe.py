from __future__ import annotations

import json
import time
from pathlib import Path

from app.api.avito import get_avito_token, _get_avito_credentials, _avito_http_get
from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle

OUT = Path('/root/BORIS/avito_archive_samples.json')
ACCOUNTS = ['otdushi', 'marketing_pod_klyuch_20035']
STATUS = 'old,removed'
MAX_DETAILS_PER_ACCOUNT = 10


def retry_after(resp) -> int:
    try:
        return max(1, int(float((resp.headers or {}).get('Retry-After') or 30)))
    except Exception:
        return 30


def safe_item_summary(item: dict) -> dict:
    return {
        'id': item.get('id'),
        'status': item.get('status'),
        'title': item.get('title'),
        'category': item.get('category'),
        'url': item.get('url'),
        'updated_at': item.get('updated_at') or item.get('updatedAt'),
    }


def collect(account_id: str) -> dict:
    result = {
        'account_id': account_id,
        'status_filter': STATUS,
        'list_http': None,
        'total_listed': 0,
        'samples': [],
        'error': None,
    }
    remaining = int(account_throttle_remaining(account_id) or 0)
    if remaining > 0:
        result['error'] = f'throttled:{remaining}'
        return result

    token_data = get_avito_token(account_id)
    token = token_data.get('access_token') if isinstance(token_data, dict) else None
    if not token:
        result['error'] = 'token_unavailable'
        return result

    _, _, user_id = _get_avito_credentials(account_id)
    if not user_id:
        me = _avito_http_get(
            'https://api.avito.ru/core/v1/accounts/self',
            headers={'Authorization': f'Bearer {token}'},
            timeout=20,
        )
        if me.status_code == 429:
            record_account_throttle(account_id, retry_after(me), source='archive_probe_me_429')
            result['error'] = 'me_429'
            return result
        if me.status_code != 200:
            result['error'] = f'me_http_{me.status_code}'
            return result
        user_id = str((me.json() or {}).get('id') or '')

    listing = _avito_http_get(
        'https://api.avito.ru/core/v1/items',
        headers={'Authorization': f'Bearer {token}'},
        params={'per_page': 50, 'page': 1, 'status': STATUS},
        timeout=25,
    )
    result['list_http'] = int(listing.status_code)
    if listing.status_code == 429:
        record_account_throttle(account_id, retry_after(listing), source='archive_probe_list_429')
        result['error'] = 'list_429'
        return result
    if listing.status_code != 200:
        result['error'] = f'list_http_{listing.status_code}'
        try:
            result['provider_error'] = listing.json()
        except Exception:
            result['provider_error'] = (listing.text or '')[:500]
        return result

    resources = (listing.json() or {}).get('resources') or []
    result['total_listed'] = len(resources)
    result['list_meta'] = (listing.json() or {}).get('meta')

    for raw in resources[:MAX_DETAILS_PER_ACCOUNT]:
        item_id = raw.get('id')
        row = safe_item_summary(raw)
        if not item_id:
            result['samples'].append(row)
            continue
        remaining = int(account_throttle_remaining(account_id) or 0)
        if remaining > 0:
            row['detail_error'] = f'throttled:{remaining}'
            result['samples'].append(row)
            break
        detail = _avito_http_get(
            f'https://api.avito.ru/core/v1/accounts/{user_id}/items/{item_id}/',
            headers={'Authorization': f'Bearer {token}'},
            timeout=20,
        )
        row['detail_http'] = int(detail.status_code)
        if detail.status_code == 429:
            record_account_throttle(account_id, retry_after(detail), source='archive_probe_detail_429')
            row['detail_error'] = '429'
            result['samples'].append(row)
            break
        if detail.status_code == 200:
            d = detail.json() or {}
            row.update({
                'title': d.get('title') or row.get('title'),
                'description': d.get('description') or '',
                'price': d.get('price'),
                'address': d.get('address'),
                'category': d.get('category') or row.get('category'),
                'detail_keys': sorted(d.keys()),
            })
        else:
            try:
                row['detail_error'] = detail.json()
            except Exception:
                row['detail_error'] = (detail.text or '')[:300]
        result['samples'].append(row)
        time.sleep(0.12)

    return result


def main() -> None:
    payload = {'generated_at': time.time(), 'accounts': [collect(a) for a in ACCOUNTS]}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for a in payload['accounts']:
        print(a['account_id'], 'http=', a.get('list_http'), 'listed=', a.get('total_listed'), 'samples=', len(a.get('samples') or []), 'error=', a.get('error'))


if __name__ == '__main__':
    main()
