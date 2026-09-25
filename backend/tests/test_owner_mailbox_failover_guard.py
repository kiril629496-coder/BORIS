import os
from app.services import client_mailboxes as cm


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount
    def mappings(self):
        return self
    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows, writes):
        self.rows = rows
        self.writes = writes
    def execute(self, stmt, params=None):
        sql = str(stmt)
        if 'SELECT id,email_address' in sql and 'client_mailboxes' in sql:
            return _Result(self.rows)
        if 'UPDATE client_mailboxes SET' in sql:
            self.writes.append((sql, params or {}))
            return _Result(rowcount=1)
        return _Result()
    def commit(self):
        pass
    def rollback(self):
        pass
    def close(self):
        pass


def _alternate_row():
    return {
        'id': 2,
        'email_address': 'eliseev-ko@mail.ru',
        'smtp_host': 'smtp.mail.ru', 'smtp_port': 465, 'smtp_ssl': True,
        'imap_host': 'imap.mail.ru', 'imap_port': 993, 'imap_ssl': True,
        'username': 'eliseev-ko@mail.ru',
        'smtp_last_error': 'SMTPAuthenticationError:535',
        'imap_last_error': 'Application password is REQUIRED',
    }


def _canonical():
    return {
        'email_address': 'noreply@boris-ai.pro',
        'smtp_host': 'smtp.yandex.ru', 'smtp_port': 465,
        'imap_host': 'imap.yandex.ru', 'imap_port': 993,
        'username': 'noreply@boris-ai.pro', 'password': 'unused',
    }


def test_owner_transport_never_silently_switches_when_failover_disabled(monkeypatch):
    writes = []
    monkeypatch.delenv('PROSPECT_OWNER_ALLOW_TRANSPORT_FAILOVER', raising=False)
    monkeypatch.setattr(cm, '_canonical_owner_transport', _canonical)
    monkeypatch.setattr(cm, 'ensure_schema', lambda db: None)
    monkeypatch.setattr(cm, 'SessionLocal', lambda: _Session([_alternate_row()], writes))
    monkeypatch.setattr(cm, '_probe_transport_config', lambda cfg: (_ for _ in ()).throw(AssertionError('probe must not run')))

    out = cm.ensure_owner_outreach_transport()

    assert out['switched'] == 0
    assert out['results'][2]['status'] == 'alternate_unhealthy_failover_disabled'
    assert writes == []


def test_owner_transport_failover_requires_explicit_opt_in(monkeypatch):
    writes = []
    monkeypatch.setenv('PROSPECT_OWNER_ALLOW_TRANSPORT_FAILOVER', 'true')
    monkeypatch.setattr(cm, '_canonical_owner_transport', _canonical)
    monkeypatch.setattr(cm, 'ensure_schema', lambda db: None)
    sessions = [_Session([_alternate_row()], writes), _Session([], writes)]
    monkeypatch.setattr(cm, 'SessionLocal', lambda: sessions.pop(0))
    monkeypatch.setattr(cm, '_probe_transport_config', lambda cfg: {'smtp': True, 'imap': True, 'imap_cursor': 123})

    out = cm.ensure_owner_outreach_transport()

    assert out['switched'] == 1
    assert out['results'][2]['status'] == 'switched_to_canonical'
    assert len(writes) == 1


def test_owner_outreach_reply_to_keeps_both_owner_mailboxes(monkeypatch):
    configured = 'noreply@boris-ai.pro,ostapenko-kirill-86@yandex.ru,eliseev-ko@mail.ru'
    monkeypatch.setenv('PROSPECT_OUTREACH_REPLY_TO', configured)
    row = {'account_id': '__owner_outreach__', 'email_address': 'fallback@example.test'}

    assert cm._mailbox_reply_to(row) == configured


def test_owner_outreach_transport_cannot_be_changed_through_generic_save_mailbox():
    import pytest
    with pytest.raises(ValueError, match='owner_outreach_transport_is_system_managed'):
        cm.save_mailbox(
            2,
            '__owner_outreach__',
            email_address='eliseev-ko@mail.ru',
            display_name='wrong transport',
            smtp_host='smtp.mail.ru',
            smtp_port=587,
            imap_host='imap.mail.ru',
            imap_port=993,
            username='eliseev-ko@mail.ru',
            password='not-used-because-policy-blocks-first',
            smtp_ssl=False,
            imap_ssl=True,
        )


def test_imap_cursor_resets_when_saved_uid_is_from_another_provider():
    assert cm._effective_imap_cursor(159795, 18) == 0
    assert cm._effective_imap_cursor(17, 18) == 17
    assert cm._effective_imap_cursor(0, 18) == 0
    assert cm._effective_imap_cursor(17, None) == 17
