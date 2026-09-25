# -*- coding: utf-8 -*-
import ast
from pathlib import Path


PREFLIGHT = Path("/usr/local/sbin/boris-social-owner-preflight.py")
HARDGUARD = Path("/opt/boris-social-hardguard/hardguard.py")


def _function_calls(path: Path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            out.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            out.add(node.func.attr)
    return out


def test_social_preflight_is_read_only_guardian():
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "SOCIAL_GUARDIAN_READ_ONLY_V1" in source
    assert "source_drift_requires_release" in source
    calls = _function_calls(PREFLIGHT, "run")
    forbidden = {"atomic_write", "atomic_copy", "write_text", "write_bytes", "copyfile", "replace"}
    assert not (calls & forbidden), sorted(calls & forbidden)


def test_social_hardguard_is_read_only_guardian():
    source = HARDGUARD.read_text(encoding="utf-8")
    assert "SOCIAL_HARDGUARD_READ_ONLY_V1" in source
    calls = _function_calls(HARDGUARD, "main")
    forbidden = {"restore", "atomic_write", "atomic_copy", "write_text", "write_bytes", "copyfile", "replace"}
    assert not (calls & forbidden), sorted(calls & forbidden)


def test_social_authority_guard_is_read_only_and_contract_bound():
    import hashlib
    import json

    source_path = Path('/root/BORIS/backend/run/social_authority_guard.py')
    source = source_path.read_text(encoding='utf-8')
    assert 'SOCIAL_AUTHORITY_GUARD_READ_ONLY_V1' in source
    assert 'source_mutation_allowed' in source
    calls = _function_calls(source_path, 'main')
    forbidden = {
        'atomic_from', 'set_i', 'write_text', 'write_bytes', 'copyfile',
        'replace', 'restart', 'start', 'daemon_reload',
    }
    assert not (calls & forbidden), sorted(calls & forbidden)

    contract_path = Path('/usr/local/share/boris-social-authority-contract.json')
    pointer_path = Path('/usr/local/share/boris-social-authority-contract.sha')
    raw = contract_path.read_bytes()
    contract = json.loads(raw)
    assert contract['version'] >= 4
    assert contract['authority_mode'] == 'read_only_verifier'
    assert contract['source_mutation_allowed'] is False
    assert hashlib.sha256(raw).hexdigest() == pointer_path.read_text(encoding='utf-8').strip()


def test_active_social_authority_service_uses_read_only_object():
    import re
    import subprocess

    unit = subprocess.check_output(
        ['systemctl', 'cat', 'boris-social-authority-guard.service'], text=True
    )
    match = re.search(r'/var/lib/boris-integrity/objects/([0-9a-f]{64})', unit)
    assert match, unit
    obj = Path('/var/lib/boris-integrity/objects') / match.group(1)
    assert obj.is_file()
    assert 'SOCIAL_AUTHORITY_GUARD_READ_ONLY_V1' in obj.read_text(encoding='utf-8')
