import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from app.services import control_plane, reliability


def test_json_safe_normalizes_runtime_native_values_recursively():
    now = datetime(2026, 9, 10, 21, 52, 26, tzinfo=timezone.utc)
    uid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    payload = {
        'at': now,
        'id': uid,
        'path': Path('/tmp/example'),
        'nested': [{'when': now, 'values': {uid, 'ok'}}],
    }
    safe = reliability.json_safe(payload)
    encoded = json.dumps(safe, ensure_ascii=False)
    assert safe['at'] == now.isoformat()
    assert safe['id'] == str(uid)
    assert safe['path'] == '/tmp/example'
    assert now.isoformat() in encoded


def test_open_incident_is_json_safe_before_first_db_flush(monkeypatch):
    now = datetime(2026, 9, 10, 21, 52, 26, tzinfo=timezone.utc)
    db = Mock()

    def verify_flush():
        row = db.add.call_args.args[0]
        json.dumps(row.details_json, ensure_ascii=False)
        assert row.details_json['runtime']['checked_at'] == now.isoformat()

    db.flush.side_effect = verify_flush
    monkeypatch.setattr(control_plane, 'record_event', lambda *args, **kwargs: None)
    row = control_plane.open_incident(
        db,
        incident_type='JSON_BOUNDARY_REGRESSION',
        symptom='runtime diagnostic contains datetime',
        module='mop',
        details={'runtime': {'checked_at': now}},
    )
    assert row.details_json['runtime']['checked_at'] == now.isoformat()
    assert db.flush.call_count == 1


def test_reliability_event_is_json_safe_before_flush():
    now = datetime(2026, 9, 10, 21, 52, 26, tzinfo=timezone.utc)
    db = Mock()

    def verify_flush():
        row = db.add.call_args.args[0]
        json.dumps(row.details_json, ensure_ascii=False)
        assert row.details_json['at'] == now.isoformat()

    db.flush.side_effect = verify_flush
    reliability.record_event(db, 'control_plane', 'json_boundary', details={'at': now})
    assert db.flush.call_count == 1


def test_guardian_existing_incident_update_uses_same_json_boundary():
    source = Path('/root/BORIS/backend/control_plane_guardian_runner.py').read_text(encoding='utf-8')
    assert 'row.details_json=json_safe(details)' in source
