from pathlib import Path

from scripts.runtime_schema_mutation_scan import scan


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_schema_scan_is_clean():
    assert scan() == []


def test_runtime_schema_scan_detects_sqlalchemy_schema_calls():
    source = (ROOT / "scripts" / "runtime_schema_mutation_scan.py").read_text(encoding="utf-8")
    assert "ast.Call" in source
    assert '"create_all", "drop_all"' in source


def test_release_builder_has_same_orm_schema_gate():
    source = (ROOT / "scripts" / "build_backend_release.py").read_text(encoding="utf-8")
    assert 'orm_ddl_names = {"create_all", "drop_all"}' in source
    assert "RUNTIME_SCHEMA_MUTATION_FORBIDDEN" in source


def test_core_runtime_schema_has_numbered_owner():
    sql = (ROOT / "migrations" / "055_core_runtime_schema_ownership.sql").read_text(encoding="utf-8")
    assert "BORIS_CORE_RUNTIME_SCHEMA_OWNERSHIP_V1" in sql
    assert all(name in sql for name in ("public.accounts", "public.storage", "public.tasks"))
