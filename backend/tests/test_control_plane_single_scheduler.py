from pathlib import Path


ROOT = Path('/root/BORIS/backend')
SYSTEMD = Path('/etc/systemd/system')
RUNNER = 'control_plane_guardian_runner.py'


def test_control_plane_has_one_live_systemd_writer_owner():
    hits = []
    for unit in SYSTEMD.glob('*.service'):
        try:
            text = unit.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if RUNNER in text and '--json' in text:
            hits.append(unit.name)
    assert sorted(hits) == ['boris-system-brain.service']


def test_production_guardian_supervises_brain_but_does_not_execute_second_writer():
    text = (ROOT / 'production_guardian_tick.sh').read_text(encoding='utf-8')
    assert 'CONTROL_PLANE_SINGLE_SCHEDULER_V1' in text
    assert './venv/bin/python control_plane_guardian_runner.py --json' not in text
    assert 'boris-system-brain.timer' in text
    assert 'SYSTEM_BRAIN_SCHEDULER' in text


def test_production_contract_enforces_single_control_plane_scheduler():
    text = (ROOT / 'production_contract_runner.py').read_text(encoding='utf-8')
    assert 'CONTROL_PLANE_SINGLE_SCHEDULER_RUNTIME_V1' in text
    assert 'control_plane_single_scheduler_runtime_scan' in text
    assert 'sorted(_systemd_hits) == ["boris-system-brain.service"]' in text
