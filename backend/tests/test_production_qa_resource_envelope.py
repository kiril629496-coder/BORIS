from types import SimpleNamespace

from app.ext_api import repo


def test_full_pytest_uses_bounded_systemd_unit_on_production(monkeypatch):
    captured = {}
    monkeypatch.setattr(repo, 'ROOT', '/root/BORIS')
    monkeypatch.setattr(repo.os, 'geteuid', lambda: 0)
    original_isfile = repo.os.path.isfile
    monkeypatch.setattr(
        repo.os.path, 'isfile',
        lambda path: True if path == '/usr/bin/systemd-run' else original_isfile(path),
    )

    def fake_run(command, **kwargs):
        captured['command'] = list(command)
        captured['kwargs'] = dict(kwargs)
        return SimpleNamespace(returncode=0, stdout='1 passed\n', stderr='')

    monkeypatch.setattr(repo.subprocess, 'run', fake_run)
    rc, out = repo._pytest_run('backend', timeout=300)

    assert rc == 0
    assert '1 passed' in out
    command = captured['command']
    assert command[0] == '/usr/bin/systemd-run'
    assert '--wait' in command and '--pipe' in command and '--collect' in command
    assert '--property=CPUQuota=100%' in command
    assert '--property=Nice=15' in command
    assert '--property=IOSchedulingClass=idle' in command
    assert '--property=MemoryMax=2G' in command
    assert '--property=RuntimeMaxSec=300s' in command
    assert '--working-directory=/root/BORIS/backend' in command
    assert command[-5:] == [repo.sys.executable, '-m', 'pytest', 'tests', '-q', '--tb=no'][-5:]
    assert captured['kwargs']['timeout'] == 320


def test_nonproduction_pytest_keeps_plain_runner(monkeypatch):
    captured = {}
    monkeypatch.setattr(repo, 'ROOT', '/tmp/boris-test')

    def fake_run(command, **kwargs):
        captured['command'] = list(command)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(repo.subprocess, 'run', fake_run)
    rc, _ = repo._pytest_run('backend', timeout=60)
    assert rc == 0
    assert captured['command'][0] == repo.sys.executable
    assert '/usr/bin/systemd-run' not in captured['command']
