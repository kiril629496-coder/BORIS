"""Adapter for BORIS's existing campaign_prepare queue; no cloud calls."""
from pathlib import Path
import hashlib
import json
import sys

from .boris_content import Blocked, prepare, safe_id
from .boris_bridge import read_campaign


def revision(db, campaign_id, account):
    from app.models.campaign_item import CampaignItem
    from app.models.media_link import MediaLink
    rows = db.query(CampaignItem).filter(CampaignItem.campaign_id == campaign_id).order_by(CampaignItem.id).all()
    if not rows or any(str(r.account_id) != account for r in rows):
        raise Blocked("empty_or_mixed_account_campaign")
    ids = [str(r.id) for r in rows]
    links = db.query(MediaLink).filter(MediaLink.entity_type == 'campaign_item', MediaLink.entity_id.in_(ids)).order_by(MediaLink.id).all()
    data = {'rows': [(r.id, r.status, r.payload_json, r.content_hash, r.feed_identity) for r in rows],
            'links': [(l.entity_id, l.media_id, l.position) for l in links]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def enqueue_local(db, campaign_id, account, owner_user_id, local_model=None, rewrite=False):
    from app.models.background_job import BackgroundJob
    from app.services.jobs import enqueue, _campaign_job_context
    from sqlalchemy import text
    from types import SimpleNamespace
    account = safe_id(account)
    if local_model is not None:
        import re
        if not isinstance(local_model, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", local_model):
            raise Blocked("local_model_name_invalid")
    if type(rewrite) is not bool or (rewrite and not local_model):
        raise Blocked("rewrite_requires_local_model")
    payload = {'campaign_id': int(campaign_id), 'account_id': account, 'content_mode': 'local_verified_v2'}
    _campaign_job_context(db, SimpleNamespace(owner_user_id=owner_user_id, account_id=account), payload)
    if local_model:
        payload.update(local_model=local_model, rewrite=rewrite)
    current = revision(db, int(campaign_id), account)
    fingerprint = 'content-local-v2:' + hashlib.sha256((account + ':' + str(campaign_id) + ':' + current).encode()).hexdigest()
    if local_model:
        fingerprint += ':' + hashlib.sha256(json.dumps({'model': local_model, 'rewrite': rewrite, 'version': 5}, sort_keys=True).encode()).hexdigest()[:16]
    # Serialize enqueue/replay. Existing enqueue resets completed jobs, so inspect first.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 744620))'), {'key': fingerprint})
    existing = db.query(BackgroundJob).filter(BackgroundJob.fingerprint == fingerprint).first()
    if existing:
        return existing, False
    payload['source_revision'] = current
    return enqueue(db, 'campaign_prepare', payload=payload, owner_user_id=owner_user_id,
                   account_id=account, fingerprint=fingerprint, progress_total=3)


def run(db, job, payload):
    from app.services.jobs import _campaign_job_context
    C, campaign, user, allowed, account = _campaign_job_context(db, job, payload)
    campaign_id, job_id = int(campaign.id), int(job.id)
    if payload.get('content_mode') != 'local_verified_v2':
        raise Blocked('explicit_local_mode_required')
    if campaign.status in {'superseded', 'archived'}:
        return {'stage': 'local_source_inactive', 'api_cost_kopeks': 0}, 'partial'
    expected = str(payload.get('source_revision') or '')
    if not expected or revision(db, campaign_id, account) != expected:
        return {'stage': 'local_source_changed', 'api_cost_kopeks': 0}, 'partial'
    backend = Path(C.__file__).resolve().parents[2]
    root = Path('/root/BORIS/backend/storage/content_worker') / safe_id(account) / str(job_id)
    root.mkdir(parents=True, exist_ok=True)
    db.commit()  # No ORM transaction held while files are rendered.
    snapshot = root / 'snapshot.json'
    read_campaign(backend, '/root/BORIS/backend/.env', account, campaign_id, 'snapshot', snapshot)
    manifest = json.loads(snapshot.read_text())
    report = prepare(manifest, Path('/root/BORIS/backend/images'), root / 'proposals',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                     model=payload.get('local_model'), rewrite=payload.get('rewrite') is True)
    export = {'status': 'blocked'}
    try:
        export = read_campaign(backend, '/root/BORIS/backend/.env', account, campaign_id, 'export', root / 'approved-feed.xml')
    except Blocked as exc:
        export['reason'] = str(exc)
    db.expire_all()
    unchanged = revision(db, campaign_id, account) == expected
    complete = unchanged and report['ready'] == report['total'] and export['status'] == 'prepared'
    result = {'stage': ('local_proposals_ready' if complete else 'local_needs_data') if unchanged else 'local_source_changed',
              'campaign_id': campaign_id, 'account_id': account, 'ready': report['ready'],
              'total': report['total'], 'source_revision': expected, 'source_unchanged': unchanged,
              'api_cost_kopeks': 0, 'external_upload': False, 'canonical_items_changed': False,
              'local_model': payload.get('local_model'),
              'model_diagnostics': [{'id': i['id'], 'provider': (i.get('copy') or {}).get('provider'),
                  'diagnostics': (i.get('copy') or {}).get('diagnostics', [])} for i in report['items']],
              'items': [{'id': i['id'], 'status': i['status'], 'reason': i.get('reason')} for i in report['items']],
              'export': export, 'artifact_directory': str(root),
              'message': 'Предложения подготовлены. Новые материалы не опубликованы.' if complete else 'Подготовка остановлена: проверьте причины в карточках.'}
    job.actual_kopeks = 0
    job.progress_done = 3
    return result, 'completed' if complete else 'partial'


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--campaign', required=True, type=int)
    parser.add_argument('--owner', required=True, type=int)
    parser.add_argument('--local-model', default=None)
    parser.add_argument('--rewrite', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.backend).resolve()))
    from dotenv import load_dotenv
    load_dotenv('/root/BORIS/backend/.env', override=False)
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        row, created = enqueue_local(db, args.campaign, args.account, args.owner, args.local_model, args.rewrite)
        result = {'job_id': row.id, 'created': created, 'status': row.status}
        db.commit()
        print(json.dumps(result))


if __name__ == '__main__':
    main()
