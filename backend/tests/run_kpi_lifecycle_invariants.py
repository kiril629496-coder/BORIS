#!/usr/bin/env python3
"""Dependency-free runner for test_kpi_lifecycle_invariants.py.
Exits non-zero when any test_* function fails, so production QA cannot silently
pass just because an optional test framework is absent.
"""
from __future__ import annotations
import importlib.util
import pathlib
import traceback

HERE=pathlib.Path(__file__).resolve().parent
TARGET=HERE/"test_kpi_lifecycle_invariants.py"
spec=importlib.util.spec_from_file_location("boris_kpi_lifecycle_invariants",TARGET)
mod=importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)
failed=[]
total=0
for name,fn in sorted(vars(mod).items()):
    if not name.startswith("test_") or not callable(fn):
        continue
    total+=1
    try:
        fn()
        print(f"PASS {name}")
    except Exception as exc:
        failed.append((name,type(exc).__name__,str(exc)))
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
print(f"KPI_LIFECYCLE_INVARIANTS total={total} passed={total-len(failed)} failed={len(failed)}")
raise SystemExit(1 if failed else 0)
