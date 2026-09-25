#!/usr/bin/env python3
"""Remove legacy runtime DDL after migration 049 baseline is applied.

Default is dry-run. --apply replaces only SQL string expressions containing DDL
with a read-only SELECT marker. Mixed DDL+DML blocks are refused.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BACKUP_ROOT = ROOT / "backups" / "runtime_schema_049"
DDL = re.compile(
    r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX|"
    r"DROP\s+(?:TABLE|INDEX|COLUMN)|ADD\s+COLUMN)\b",
    re.IGNORECASE,
)
DML = re.compile(r"\b(?:INSERT\s+INTO|UPDATE\s+\w+|DELETE\s+FROM)\b", re.IGNORECASE)
REPLACEMENT = repr("SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */")
EXCLUDED = {"app/ext_api/selftest.py"}


def _offsets(source: str) -> list[int]:
    starts = [0]
    for idx, ch in enumerate(source):
        if ch == "\n":
            starts.append(idx + 1)
    return starts


def _span(node: ast.AST, starts: list[int]) -> tuple[int, int]:
    start = starts[node.lineno - 1] + node.col_offset
    end = starts[node.end_lineno - 1] + node.end_col_offset
    return start, end


def replacements(path: Path) -> list[tuple[int, int, int]]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source, filename=str(path))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    starts = _offsets(source)
    rows: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if isinstance(parents.get(node), ast.JoinedStr):
                continue
            text_value = node.value
        elif isinstance(node, ast.JoinedStr):
            text_value = ast.get_source_segment(source, node) or ""
        else:
            continue
        if not DDL.search(text_value):
            continue
        if DML.search(text_value):
            raise RuntimeError(f"mixed DDL/DML block refused: {path}:{node.lineno}")
        start, end = _span(node, starts)
        rows.append((start, end, int(node.lineno)))
    unique = {(a, b): line for a, b, line in rows}
    return [(a, b, unique[(a, b)]) for a, b in sorted(unique, reverse=True)]


def process(path: Path, *, apply: bool) -> int:
    rows = replacements(path)
    if not rows:
        return 0
    if apply:
        source = path.read_text(encoding="utf-8", errors="replace")
        for start, end, _line in rows:
            source = source[:start] + REPLACEMENT + source[end:]
        compile(source, str(path), "exec")
        backup = BACKUP_ROOT / path.relative_to(ROOT)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(source, encoding="utf-8")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    total = 0
    files = 0
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXCLUDED or ".bak" in path.name or "__pycache__" in path.parts:
            continue
        count = process(path, apply=args.apply)
        if count:
            files += 1
            total += count
            print(f"{'APPLY' if args.apply else 'WOULD_APPLY'} {rel} blocks={count}")
    print(f"RUNTIME_DDL_BLOCKS={'REMOVED' if args.apply else 'FOUND'} {total} files={files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
