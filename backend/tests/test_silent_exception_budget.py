import ast
from pathlib import Path

ROOT = Path('/root/BORIS/backend/app')
# Baseline debt. This test is monotonic: future changes may reduce the count,
# but may not add new silent `except Exception: pass` handlers to critical code.
MAX_SILENT_EXCEPTION_PASS = {
    'api/avito.py': 58,
    'api/messenger.py': 32,
    'api/calltracking.py': 23,
    'api/campaigns.py': 19,
    'services/telephony_core.py': 17,
    'services/sales_ai_router.py': 15,
    'mop_core.py': 5,
}


def _count_silent_pass(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not (isinstance(node.type, ast.Name) and node.type.id == 'Exception'):
            continue
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            total += 1
    return total


def test_critical_modules_cannot_add_silent_exception_pass():
    over = {}
    for rel, maximum in MAX_SILENT_EXCEPTION_PASS.items():
        current = _count_silent_pass(ROOT / rel)
        if current > maximum:
            over[rel] = {'current': current, 'maximum': maximum}
    assert not over, f'New silent exception swallowing is forbidden: {over}'
