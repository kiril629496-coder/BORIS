"""Reconcile exact Social image cost when the provider's file cache returns zero.

Receipts contain only identifiers, digest and actual paid cost. A missing or
ambiguous receipt blocks publication; it never authorizes another paid image.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import tempfile

from app.services.social_media_assets import file_sha256


def positive_cost(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def image_provenance(account_id, project_id, path, intent):
    return {'source': 'social', 'account_id': str(account_id),
            'project_id': str(project_id), 'asset_filename': Path(path).name,
            'intent_sha256': str(intent)}


def _ledger_rows(account_id, intent, provenance):
    from sqlalchemy import text
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return [dict(row) for row in db.execute(text('''
            SELECT request_id, cost_rub, provenance_json, count(*) OVER() AS matching_rows
            FROM api_usage
            WHERE account_id=:account AND operation='banner_social_post'
              AND (provenance_json @> CAST(:proof AS jsonb)
                   OR provenance_json->'boris_provenance' @> CAST(:proof AS jsonb))
            ORDER BY id DESC LIMIT 3
        '''), {'account': str(account_id), 'proof': json.dumps(provenance, sort_keys=True)}).mappings()]
    finally:
        db.close()


def reconcile_image_cost(*, account_id, project_id, path, intent, response, lookup=None):
    """Return the original positive cost, never zero for an already paid image."""
    asset = Path(path)
    if not asset.is_file() or not re.fullmatch(r'[0-9a-f]{64}', str(intent)):
        raise ValueError('SOCIAL_IMAGE_COST_ASSET_INVALID')
    if asset.name != f'{account_id}_social_{intent}.png':
        raise ValueError('SOCIAL_IMAGE_COST_INTENT_MISMATCH')
    digest = file_sha256(asset)
    expected = image_provenance(account_id, project_id, path, intent)
    receipt_path = asset.with_suffix(asset.suffix + '.cost.json')
    if receipt_path.is_symlink():
        raise ValueError('SOCIAL_IMAGE_COST_RECEIPT_UNSAFE')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('provenance') != expected or receipt.get('asset_sha256') != digest
                or positive_cost(receipt.get('cost_rub')) is None or not receipt.get('request_id')):
            raise ValueError('SOCIAL_IMAGE_COST_RECEIPT_MISMATCH')
        return dict(receipt, cost_source='durable_social_receipt')

    amount = positive_cost((response or {}).get('cost_rub'))
    request_id = str((response or {}).get('request_id') or '').strip()
    source = 'paid_provider_result'
    if not (amount is not None and request_id and (response or {}).get('ok') is True
            and (response or {}).get('cache_hit') is not True):
        lookup = lookup or (lambda account, key: _ledger_rows(account, key, expected))
        candidates = []
        rows = list(lookup(str(account_id), str(intent)))
        if len(rows) != 1 or rows[0].get("matching_rows", len(rows)) != 1:
            raise ValueError('SOCIAL_IMAGE_ACTUAL_COST_RECONCILIATION_REQUIRED')
        for row in rows:
            provenance = row.get('provenance_json')
            if isinstance(provenance, str):
                provenance = json.loads(provenance)
            if isinstance(provenance, dict) and isinstance(provenance.get('boris_provenance'), dict):
                provenance = provenance['boris_provenance']
            cost = positive_cost(row.get('cost_rub'))
            rid = str(row.get('request_id') or '').strip()
            if isinstance(provenance, dict) and all(provenance.get(k) == v for k, v in expected.items()) and cost is not None and rid:
                candidates.append((rid, cost))
        if len(candidates) != 1:
            raise ValueError('SOCIAL_IMAGE_ACTUAL_COST_RECONCILIATION_REQUIRED')
        request_id, amount = candidates[0]
        source = 'canonical_api_usage'
    receipt = {'version': 'SOCIAL_IMAGE_ACTUAL_COST_V1', 'provenance': expected,
               'asset_sha256': digest, 'request_id': request_id, 'cost_rub': amount}
    fd, temporary = tempfile.mkstemp(prefix='.social-cost-', dir=asset.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(receipt, out, ensure_ascii=False, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temporary, receipt_path)
        except FileExistsError:
            if json.loads(receipt_path.read_text()) != receipt:
                raise ValueError('SOCIAL_IMAGE_COST_RECEIPT_CONFLICT')
        directory_fd = os.open(asset.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return dict(receipt, cost_source=source)
