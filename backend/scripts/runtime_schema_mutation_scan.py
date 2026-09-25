#!/usr/bin/env python3
"""Fail-closed scan for production schema DDL embedded in runtime Python.

Only numbered SQL migrations may own production schema changes.  This scanner
reports runtime DDL locations; it deliberately does not auto-approve legacy
bootstrap helpers just because they already exist.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
DDL = re.compile(
    r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX|"
    r"DROP\s+(?:TABLE|INDEX|COLUMN)|ADD\s+COLUMN)\b",
    re.IGNORECASE,
)
# This file contains a deliberately hostile SQL string used to verify that the
# ops SQL endpoint rejects destructive statements. It never executes the DDL.
NON_RUNTIME_FIXTURES = {"app/ext_api/selftest.py"}


def scan() -> list[dict]:
    violations: list[dict] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in NON_RUNTIME_FIXTURES or ".bak" in path.name or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if DDL.search(line):
                violations.append({"path": rel, "line": line_no, "snippet": line.strip()[:180]})
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
            if name in {"create_all", "drop_all"}:
                violations.append({"path": rel, "line": int(getattr(node, "lineno", 0) or 0),
                                   "snippet": f"ORM schema call: {name}(...)"})
    return violations


def main() -> int:
    rows = scan()
    payload = {
        "check": "RUNTIME_SCHEMA_MUTATION_SCAN",
        "ok": not rows,
        "unapproved_schema_mutations": len(rows),
        "violations": rows,
    }
    print(json.dumps(payload, ensure_ascii=False))
    if rows:
        print(f"UNAPPROVED_SCHEMA_MUTATIONS={len(rows)}", file=sys.stderr)
        return 1
    print("UNAPPROVED_SCHEMA_MUTATIONS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
