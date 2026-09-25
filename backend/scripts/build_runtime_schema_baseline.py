#!/usr/bin/env python3
"""Build the one-time legacy runtime-schema baseline contract.

This is a migration aid: existing production schema is captured as a canonical
baseline before all runtime DDL is removed. Future schema changes remain owned
only by numbered migrations.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP = ROOT / "app"
ARCH = ROOT / "architecture"
MIG = ROOT / "migrations" / "049_runtime_schema_baseline_contract.sql"
MANIFEST = ARCH / "runtime_schema_contract_v1.json"
DDL = re.compile(r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX)\b", re.I)
TABLE = re.compile(r"\b(?:CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
INDEX = re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def _env(tree: ast.Module) -> dict[str, object]:
    out: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


def _render(node: ast.AST, env: dict[str, object], source: str) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name) and part.value.id in env:
                parts.append(str(env[part.value.id]))
            else:
                return None
        return "".join(parts)
    return None


def source_objects() -> tuple[list[str], list[str]]:
    tables: set[str] = set()
    indexes: set[str] = set()
    for path in APP.rglob("*.py"):
        if ".bak" in path.name or "__pycache__" in path.parts or path.as_posix().endswith("app/ext_api/selftest.py"):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        env = _env(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr)):
                continue
            rendered = _render(node, env, source)
            if not rendered or not DDL.search(rendered):
                continue
            tables.update(
                x.group(1) for x in TABLE.finditer(rendered)
                if x.group(1).upper() != "IF"
            )
            indexes.update(
                x.group(1) for x in INDEX.finditer(rendered)
                if len(x.group(1)) > 3 and not x.group(1).endswith("_")
            )
    return sorted(tables), sorted(indexes)


def build_manifest(db) -> dict:
    tables, indexes = source_objects()
    table_docs = {}
    missing_tables = []
    for table in tables:
        reg = db.execute(text("select to_regclass(:n)"), {"n": f"public.{table}"}).scalar()
        if not reg:
            missing_tables.append(table)
            continue
        cols = [dict(r) for r in db.execute(text("""
            select column_name,data_type,udt_name,is_nullable,column_default
              from information_schema.columns
             where table_schema='public' and table_name=:t
             order by ordinal_position
        """), {"t": table}).mappings().all()]
        constraints = [dict(r) for r in db.execute(text("""
            select c.conname as name, c.contype as type
              from pg_constraint c
              join pg_class r on r.oid=c.conrelid
              join pg_namespace n on n.oid=r.relnamespace
             where n.nspname='public' and r.relname=:t
             order by c.conname
        """), {"t": table}).mappings().all()]
        table_docs[table] = {"columns": cols, "constraints": constraints}
    present_indexes = []
    missing_indexes = []
    for name in indexes:
        reg = db.execute(text("select to_regclass(:n)"), {"n": f"public.{name}"}).scalar()
        (present_indexes if reg else missing_indexes).append(name)
    return {
        "contract_version": 1,
        "owner": "numbered_migrations",
        "source": "legacy_runtime_schema_baseline",
        "tables": table_docs,
        "indexes": present_indexes,
        "missing_source_tables": missing_tables,
        "missing_source_indexes": missing_indexes,
    }


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def migration_sql(manifest: dict) -> str:
    tables = sorted(manifest["tables"])
    indexes = sorted(manifest["indexes"])
    lines = [
        "BEGIN;",
        "",
        "-- BORIS_RUNTIME_SCHEMA_BASELINE_V1",
        "-- Legacy runtime-created schema becomes migration-owned. This migration",
        "-- is intentionally verification-only: production already contains the",
        "-- baseline; missing objects fail closed instead of being recreated by app code.",
        "DO $$",
        "DECLARE missing text;",
        "BEGIN",
        "  SELECT string_agg(x, ', ' ORDER BY x) INTO missing",
        "    FROM unnest(ARRAY[" + ",".join(_q('public.' + x) for x in tables) + "]::text[]) x",
        "   WHERE to_regclass(x) IS NULL;",
        "  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing relations: %', missing; END IF;",
        "  SELECT string_agg(x, ', ' ORDER BY x) INTO missing",
        "    FROM unnest(ARRAY[" + ",".join(_q('public.' + x) for x in indexes) + "]::text[]) x",
        "   WHERE to_regclass(x) IS NULL;",
        "  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing indexes: %', missing; END IF;",
    ]
    for table, doc in sorted(manifest["tables"].items()):
        cols = [str(x["column_name"]) for x in doc["columns"]]
        lines += [
            "  SELECT string_agg(x, ', ' ORDER BY x) INTO missing",
            "    FROM unnest(ARRAY[" + ",".join(_q(x) for x in cols) + "]::text[]) x",
            f"   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name={_q(table)} AND c.column_name=x);",
            f"  IF missing IS NOT NULL THEN RAISE EXCEPTION 'RUNTIME_SCHEMA_BASELINE missing columns on {table}: %', missing; END IF;",
        ]
    lines += ["END $$;", "", "COMMIT;", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        manifest = build_manifest(db)
    finally:
        db.close()
    print(json.dumps({
        "tables": len(manifest["tables"]),
        "indexes": len(manifest["indexes"]),
        "missing_source_tables": manifest["missing_source_tables"],
        "missing_source_indexes": manifest["missing_source_indexes"],
    }, ensure_ascii=False))
    if manifest["missing_source_tables"] or manifest["missing_source_indexes"]:
        return 2
    if args.write:
        ARCH.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        MIG.write_text(migration_sql(manifest), encoding="utf-8")
        print(f"WROTE {MANIFEST} {MIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
