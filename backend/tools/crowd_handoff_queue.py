#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import service_marketplace as marketplace

OUT = ROOT / 'data' / 'service_marketplaces' / 'human_onboarding_queue.json'
HANDOFF = 'http://127.0.0.1:8765/internal/create'

HUMAN_CHECKPOINTS = {
    'captcha_required','captcha_age_and_terms_required','terms_acceptance_required',
    'registration_agreement_required','age_declaration_required','age_and_terms_declaration_required',
    'email_verification_required','sms_or_verification_code_required','business_email_required',
    'external_account_sso_required','truthful_company_identity_required',
}

RELEVANCE = {
    'cyberforum': 45, 'zismo': 42, 'forum_seo': 40, 'searchengines': 40,
    'nulled': 38, 'wjunction': 36, 'digitalpoint': 36, 'programming': 34,
    'webdev': 34, 'forum_promotion': 32, 'partnersearch': 30, 'mmgp': 28,
    'skripters': 28, 'affiliate': 26, 'mse_script': 26, 'hard_tm': 24,
    'bugfun': 24, 'joomlaforum': 24, 'amit': 22,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def draft_index() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for draft in marketplace.list_drafts():
        if str(draft.get('status') or '') != 'approved':
            continue
        platform = str(draft.get('platform') or '')
        if platform:
            result.setdefault(platform, []).append(draft)
    return result


def classify(checkpoint: str) -> str:
    c = checkpoint or ''
    if 'captcha' in c:
        return 'captcha'
    if 'terms' in c or 'agreement' in c or 'age_' in c:
        return 'terms_or_declaration'
    if 'email' in c or 'sms' in c:
        return 'verification_code'
    if 'sso' in c:
        return 'sso'
    if 'identity' in c or 'business_email' in c:
        return 'identity'
    return 'human_checkpoint'


def score(platform: str, checkpoint: str, has_draft: bool) -> int:
    s = 100 if has_draft else 0
    if checkpoint == 'captcha_required':
        s += 55
    elif checkpoint == 'captcha_age_and_terms_required':
        s += 48
    elif checkpoint in {'terms_acceptance_required','registration_agreement_required'}:
        s += 35
    elif checkpoint in {'age_declaration_required','age_and_terms_declaration_required'}:
        s += 25
    elif checkpoint in {'email_verification_required','sms_or_verification_code_required'}:
        s += 20
    for key, value in RELEVANCE.items():
        if key in platform.lower():
            s += value
    if platform == 'cyberforum_freelancers':
        s += 100
    return s


def collect() -> dict:
    drafts = draft_index()
    rows = []
    for row in marketplace.registration_plan():
        platform = str(row.get('platform') or '')
        checkpoint = str(row.get('checkpoint') or '')
        status = str(row.get('status') or '')
        if checkpoint not in HUMAN_CHECKPOINTS and status != 'verification_required':
            continue
        pobj = marketplace.get_platform(platform)
        platform_url = str(getattr(pobj, 'url', '') or '') if pobj else ''
        approved = drafts.get(platform, [])
        item = {
            'platform': platform,
            'status': status,
            'checkpoint': checkpoint,
            'kind': classify(checkpoint),
            'account_url': str(row.get('account_url') or ''),
            'platform_url': platform_url,
            'domain': (urlparse(str(row.get('account_url') or platform_url)).hostname or '').lower(),
            'approved_drafts': [str(x.get('id') or '') for x in approved],
            'approved_draft_count': len(approved),
            'priority': score(platform, checkpoint, bool(approved)),
            'last_error': str(row.get('last_error') or '')[:1200] or None,
            'updated_at': str(row.get('updated_at') or ''),
        }
        rows.append(item)
    rows.sort(key=lambda x: (-int(x['priority']), x['platform']))
    payload = {
        'generated_at': now_iso(),
        'count': len(rows),
        'captcha_count': sum(1 for x in rows if x['kind'] == 'captcha'),
        'with_approved_draft': sum(1 for x in rows if x['approved_draft_count']),
        'rows': rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(OUT)
    return payload


def choose(payload: dict) -> dict | None:
    for item in payload.get('rows') or []:
        if item.get('status') == 'ready':
            continue
        if item.get('approved_draft_count') and item.get('kind') == 'captcha':
            return item
    for item in payload.get('rows') or []:
        if item.get('status') != 'ready' and item.get('approved_draft_count'):
            return item
    return next(iter(payload.get('rows') or []), None)


def open_handoff(platform: str) -> dict:
    data = json.dumps({'platform': platform}).encode('utf-8')
    req = urllib.request.Request(HANDOFF, data=data, headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('collect')
    sub.add_parser('next')
    op = sub.add_parser('open')
    op.add_argument('platform')
    args = parser.parse_args()

    payload = collect()
    if args.cmd == 'collect':
        print(json.dumps({k:payload[k] for k in ('generated_at','count','captcha_count','with_approved_draft')}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == 'next':
        print(json.dumps(choose(payload), ensure_ascii=False, indent=2))
        return 0
    result = open_handoff(args.platform)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
