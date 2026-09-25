# -*- coding: utf-8 -*-
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.services.operation_contract import OperationState


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
CONSTITUTION = json.loads((BACKEND / "architecture" / "constitution_v1.json").read_text(encoding="utf-8"))


def _python_files():
    for path in APP.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))


def _silent_broad_pass_count(path):
    total = 0
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"})
        silent = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
        if broad and silent:
            total += 1
    return total


def test_constitution_ids_are_unique_and_versioned():
    ids = [row["id"] for row in CONSTITUTION["invariants"]]
    assert CONSTITUTION["constitution_version"] >= 1
    assert len(ids) == len(set(ids))
    assert ids == [f"CONST-{n:02d}" for n in range(1, len(ids) + 1)]


def test_operation_states_match_constitution_exactly():
    runtime_states = [state.value for state in OperationState]
    assert runtime_states == CONSTITUTION["operation_states"]


def test_no_duplicate_top_level_functions_in_runtime_app():
    duplicates = []
    for path in _python_files():
        names = defaultdict(list)
        for node in _tree(path).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names[node.name].append(node.lineno)
        for name, lines in names.items():
            if len(lines) > 1:
                duplicates.append((str(path.relative_to(BACKEND)), name, lines))
    assert not duplicates, f"duplicate top-level functions: {duplicates[:30]}"


def test_silent_exception_debt_may_only_go_down():
    actual = Counter()
    for rel, ceiling in CONSTITUTION["silent_exception_ceiling"].items():
        path = BACKEND / rel
        assert path.is_file(), f"constitution references missing file: {rel}"
        actual[rel] = _silent_broad_pass_count(path)
        assert actual[rel] <= int(ceiling), (
            f"silent exception debt increased in {rel}: actual={actual[rel]} ceiling={ceiling}"
        )


def test_source_write_lock_is_global_not_patch_scoped():
    source = (APP / "ext_api" / "repo.py").read_text(encoding="utf-8")
    assert 'lock_path = "/tmp/boris-production-source-write.lock"' in source
    assert 'boris-patch-apply-{pid}' not in source


def test_no_silent_broad_exception_pass_anywhere_in_runtime_app():
    violations = []
    for path in _python_files():
        count = _silent_broad_pass_count(path)
        if count:
            violations.append((str(path.relative_to(BACKEND)), count))
    assert not violations, f"silent broad exception/pass is constitutionally forbidden: {violations[:50]}"


def test_release_builder_enforces_constitutional_source_gates():
    source = (BACKEND / "scripts" / "build_backend_release.py").read_text(encoding="utf-8")
    assert "def _assert_no_silent_broad_pass" in source
    assert "SILENT_BROAD_EXCEPTION_FORBIDDEN" in source
    assert source.count("_assert_no_silent_broad_pass(app_files") >= 2
    assert "def _assert_no_duplicate_top_level_functions" in source
    assert "DUPLICATE_TOP_LEVEL_FUNCTION_FORBIDDEN" in source
    assert source.count("_assert_no_duplicate_top_level_functions(app_files") >= 2
    assert "def _assert_constitution_source_contract" in source
    assert "ARCHITECTURE_CONSTITUTION_INVALID" in source
    assert "ARCHITECTURE_OPERATION_STATES_MISMATCH" in source
    assert source.count("_assert_constitution_source_contract()") >= 2
