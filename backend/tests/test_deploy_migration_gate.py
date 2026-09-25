from app.ext_api import deploy


def test_migration_gate_clean_ledger_does_not_apply(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy.os.path, 'isfile', lambda _p: True)
    monkeypatch.setattr(deploy, 'backend_dir', lambda: '/tmp/backend')
    monkeypatch.setattr(deploy, '_py', lambda: 'python3')

    def fake_run(argv, cwd=None, timeout=900):
        calls.append(tuple(argv))
        if argv[-1] == 'status':
            return 0, '001 APPLIED 001_x.sql\n'
        if argv[-1] == 'verify':
            return 0, 'MIGRATION_LEDGER_VERIFY=PASS\n'
        raise AssertionError(argv)

    monkeypatch.setattr(deploy, '_run', fake_run)
    result = deploy._migration_gate()
    assert result['ok'] is True
    assert result['pending_before'] == []
    assert result['applied_pending'] is False
    assert not any(call[-1] == 'apply-pending' for call in calls)


def test_migration_gate_applies_pending_before_verify(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy.os.path, 'isfile', lambda _p: True)
    monkeypatch.setattr(deploy, 'backend_dir', lambda: '/tmp/backend')
    monkeypatch.setattr(deploy, '_py', lambda: 'python3')

    def fake_run(argv, cwd=None, timeout=900):
        calls.append(tuple(argv))
        if argv[-1] == 'status':
            return 0, '044 PENDING 044_new.sql\n'
        if argv[-1] == 'apply-pending':
            return 0, 'MIGRATION_BACKUP=PASS\n044 APPLIED\nMIGRATION_APPLY=PASS\n'
        if argv[-1] == 'verify':
            return 0, '044 APPLIED 044_new.sql\nMIGRATION_LEDGER_VERIFY=PASS\n'
        raise AssertionError(argv)

    monkeypatch.setattr(deploy, '_run', fake_run)
    result = deploy._migration_gate()
    assert result['ok'] is True
    assert result['pending_before'] == ['044_new.sql']
    assert result['applied_pending'] is True
    assert [call[-1] for call in calls] == ['status', 'apply-pending', 'verify']


def test_migration_gate_blocks_checksum_drift_without_mutation(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy.os.path, 'isfile', lambda _p: True)
    monkeypatch.setattr(deploy, 'backend_dir', lambda: '/tmp/backend')
    monkeypatch.setattr(deploy, '_py', lambda: 'python3')

    def fake_run(argv, cwd=None, timeout=900):
        calls.append(tuple(argv))
        return 0, '035 CHECKSUM_MISMATCH 035_bad.sql\n'

    monkeypatch.setattr(deploy, '_run', fake_run)
    result = deploy._migration_gate()
    assert result['ok'] is False
    assert result['stage'] == 'verify_source'
    assert 'CHECKSUM_MISMATCH' in result['errors']
    assert [call[-1] for call in calls] == ['status']


def test_migration_gate_blocks_noncanonical_sql(monkeypatch):
    monkeypatch.setattr(deploy.os.path, 'isfile', lambda _p: True)
    monkeypatch.setattr(deploy, 'backend_dir', lambda: '/tmp/backend')
    monkeypatch.setattr(deploy, '_py', lambda: 'python3')
    monkeypatch.setattr(
        deploy, '_run',
        lambda argv, cwd=None, timeout=900: (0, 'NONCANONICAL_MIGRATION_FILES random.sql\n'),
    )
    result = deploy._migration_gate()
    assert result['ok'] is False
    assert result['stage'] == 'verify_source'
    assert 'NONCANONICAL_MIGRATION_FILES' in result['errors']
