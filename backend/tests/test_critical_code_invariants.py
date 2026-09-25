import ast
from pathlib import Path


CRITICAL_FILES = (
    "app/mop_core.py",
    "app/api/messenger.py",
    "app/api/inbox_slots.py",
    "app/services/sales_ai_router.py",
)


def _top_level_duplicates(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    seen = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            key = (type(node).__name__, node.name)
            seen.setdefault(key, []).append(node.lineno)
    return {key: lines for key, lines in seen.items() if len(lines) > 1}


def test_critical_modules_have_no_duplicate_top_level_symbols():
    root = Path(__file__).resolve().parents[1]
    failures = {}
    for rel in CRITICAL_FILES:
        dup = _top_level_duplicates(root / rel)
        if dup:
            failures[rel] = dup
    assert failures == {}, (
        "Critical production code contains duplicate top-level definitions. "
        "Python would silently use the last definition and make earlier fixes ineffective: "
        f"{failures}"
    )


def test_mop_has_one_chat_ownership_source_and_one_final_send_guard():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app/mop_core.py").read_text(encoding="utf-8")
    assert source.count("def human_takeover_state(") == 1
    assert "def human_takeover_active(" not in source
    # Marker occurs in the explanatory comment and policy_version only.
    assert source.count("MOP_HUMAN_TAKEOVER_SEND_GUARD_V2") == 2
    assert "human_takeover_active(" not in source
