#!/usr/bin/env python3
"""Fail closed when a Guardian/recovery contour can write application Python source."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYWORDS = re.compile(r"guardian|recover|selfheal|self_heal|watchdog|integrity|reconciler", re.I)
SOURCE_WRITE_METHODS = {"write_text", "write_bytes", "rename", "replace"}
COPY_FUNCS = {"copy", "copy2", "copyfile", "move"}
DANGEROUS_SHELL = re.compile(r"\b(?:git\s+(?:checkout|reset)|patch\b|sed\s+-i\b|cp\b|mv\b)" , re.I)


def _source_path_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        segment = ast.get_source_segment(_CURRENT_TEXT, value) or ""
        if ".py" not in segment:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _expr_mentions_source(expr: ast.AST | None, source_names: set[str]) -> bool:
    if expr is None:
        return False
    segment = ast.get_source_segment(_CURRENT_TEXT, expr) or ""
    if ".py" in segment:
        return True
    return any(isinstance(n, ast.Name) and n.id in source_names for n in ast.walk(expr))


def scan_file(path: Path) -> list[dict]:
    global _CURRENT_TEXT
    _CURRENT_TEXT = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(_CURRENT_TEXT, filename=str(path))
    except SyntaxError as exc:
        return [{"path": path.relative_to(ROOT).as_posix(), "line": exc.lineno or 0, "code": "guardian_scan_syntax_error"}]
    source_names = _source_path_names(tree)
    violations: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in SOURCE_WRITE_METHODS:
            target = func.value
            # Path.replace(destination) and rename(destination) can mutate the destination.
            candidate = target
            if func.attr in {"replace", "rename"} and node.args:
                if _expr_mentions_source(node.args[0], source_names):
                    candidate = node.args[0]
            if _expr_mentions_source(candidate, source_names):
                violations.append({"path": path.relative_to(ROOT).as_posix(), "line": node.lineno, "code": "guardian_python_source_write", "call": func.attr})
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr == "replace" and len(node.args) >= 2 and _expr_mentions_source(node.args[1], source_names):
                violations.append({"path": path.relative_to(ROOT).as_posix(), "line": node.lineno, "code": "guardian_python_source_replace"})
            if func.value.id == "shutil" and func.attr in COPY_FUNCS and len(node.args) >= 2 and _expr_mentions_source(node.args[1], source_names):
                violations.append({"path": path.relative_to(ROOT).as_posix(), "line": node.lineno, "code": "guardian_python_source_copy", "call": func.attr})
        elif isinstance(func, ast.Name) and func.id == "open" and node.args:
            mode = ""
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(x in mode for x in ("w", "a", "+")) and _expr_mentions_source(node.args[0], source_names):
                violations.append({"path": path.relative_to(ROOT).as_posix(), "line": node.lineno, "code": "guardian_python_source_open_write"})
        segment = ast.get_source_segment(_CURRENT_TEXT, node) or ""
        if DANGEROUS_SHELL.search(segment) and ".py" in segment:
            violations.append({"path": path.relative_to(ROOT).as_posix(), "line": node.lineno, "code": "guardian_shell_source_mutation"})
    return violations


def guardian_files() -> list[Path]:
    out = []
    for base in (ROOT / "app", ROOT / "scripts"):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sh"} or ".bak" in path.name:
                continue
            if KEYWORDS.search(path.name):
                out.append(path)
    return sorted(out)


def main() -> int:
    rows: list[dict] = []
    files = guardian_files()
    for path in files:
        if path.suffix == ".py":
            rows.extend(scan_file(path))
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), 1):
                if ".py" in line and DANGEROUS_SHELL.search(line):
                    rows.append({"path": path.relative_to(ROOT).as_posix(), "line": line_no, "code": "guardian_shell_source_mutation"})
    payload = {"check": "GUARDIAN_SOURCE_WRITE_ZERO", "ok": not rows, "guardian_files": len(files), "violations": rows}
    print(json.dumps(payload, ensure_ascii=False))
    print(f"GUARDIAN_SOURCE_WRITE_VIOLATIONS={len(rows)}")
    return 0 if not rows else 1


_CURRENT_TEXT = ""
if __name__ == "__main__":
    raise SystemExit(main())
