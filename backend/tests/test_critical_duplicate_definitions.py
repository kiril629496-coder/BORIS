import ast
from pathlib import Path


ROOT = Path('/root/BORIS/backend/app')
CRITICAL_FILES = [
    ROOT / 'mop_core.py',
    ROOT / 'services' / 'telephony_core.py',
    ROOT / 'services' / 'service_pricing.py',
    ROOT / 'services' / 'service_marketplace.py',
    ROOT / 'services' / 'platform_rules.py',
    ROOT / 'factory' / 'client_launch_entry.py',
]


def _top_level_duplicates(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.setdefault(node.name, []).append(node.lineno)
    return {name: lines for name, lines in found.items() if len(lines) > 1}


def test_no_shadowed_top_level_definitions_in_critical_modules():
    bad = {str(path.relative_to(ROOT)): _top_level_duplicates(path)
           for path in CRITICAL_FILES}
    bad = {path: duplicates for path, duplicates in bad.items() if duplicates}
    assert not bad, f'Critical duplicate definitions shadow each other: {bad}'
