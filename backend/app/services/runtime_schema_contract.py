# -*- coding: utf-8 -*-
"""Read-only verification of the migration-owned runtime schema baseline."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "architecture" / "runtime_schema_contract_v1.json"


def load_contract() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def validate_runtime_schema_contract(db) -> dict:
    contract = load_contract()
    errors: list[dict] = []
    tables = dict(contract.get("tables") or {})
    indexes = list(contract.get("indexes") or [])
    for table, spec in sorted(tables.items()):
        exists = db.execute(text("select to_regclass(:n)"), {"n": f"public.{table}"}).scalar()
        if not exists:
            errors.append({"code": "schema_table_missing", "table": table})
            continue
        actual_columns = {
            str(x[0])
            for x in db.execute(text("""
                select column_name from information_schema.columns
                 where table_schema='public' and table_name=:t
            """), {"t": table}).all()
        }
        expected_columns = {str(x.get("column_name")) for x in (spec.get("columns") or [])}
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            errors.append({"code": "schema_columns_missing", "table": table, "columns": missing_columns})
    missing_indexes = []
    for name in indexes:
        exists = db.execute(text("select to_regclass(:n)"), {"n": f"public.{name}"}).scalar()
        if not exists:
            missing_indexes.append(name)
    if missing_indexes:
        errors.append({"code": "schema_indexes_missing", "indexes": sorted(missing_indexes)})
    return {
        "ok": not errors,
        "errors": errors,
        "tables": len(tables),
        "indexes": len(indexes),
        "contract_version": contract.get("contract_version"),
        "owner": contract.get("owner"),
    }
