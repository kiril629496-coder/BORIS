#!/usr/bin/env python3
"""Read-only QA for proposed KPI/marketing patches.

Applies selected proposed patch edits to an isolated temporary source overlay,
compiles every changed Python file, then runs the lifecycle invariant functions
against that overlay. Production files and providers are never mutated.
"""
from __future__ import annotations
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path('/root/BORIS/backend')
PROJECT = Path('/root/BORIS')
sys.path.insert(0, str(ROOT))
from app.ext_api import repo


def main(argv: list[str]) -> int:
    if not argv:
        print('usage: run_kpi_proposed_patch_preview.py PATCH_ID [PATCH_ID ...]')
        return 64
    patch_ids = [int(x) for x in argv]
    sources: dict[str, str] = {}
    changed: set[str] = set()
    for pid in patch_ids:
        p = repo.get_patch(pid)
        if p.get('status') not in {'proposed', 'approved'}:
            raise RuntimeError(f'patch {pid} status={p.get("status")} is not previewable')
        for edit in p.get('edits') or []:
            rel = str(edit.get('path') or '')
            if not rel.startswith('backend/'):
                raise RuntimeError(f'patch {pid} path outside backend preview scope: {rel}')
            backend_rel = rel[len('backend/'):]
            current = sources.get(backend_rel)
            if current is None:
                current = (PROJECT / rel).read_text(encoding='utf-8')
            mode = edit.get('mode')
            if mode == 'replace':
                old, new = edit.get('old'), edit.get('new')
                count = current.count(old or '')
                if count != 1:
                    raise RuntimeError(f'patch {pid} stale: {rel} old fragment count={count}')
                current = current.replace(old, new, 1)
            elif mode == 'full':
                current = edit.get('content') or ''
            else:
                raise RuntimeError(f'patch {pid} unsupported mode={mode}')
            if backend_rel.endswith('.py'):
                compile(current, backend_rel, 'exec')
            sources[backend_rel] = current
            changed.add(backend_rel)

    with tempfile.TemporaryDirectory(prefix='boris_kpi_patch_preview_') as td:
        preview = Path(td)
        # The lifecycle test suite reads only these four source files today.
        required = {
            'app/api/avito.py',
            'app/api/cpx_advisor.py',
            'app/services/marketing_experiment_journal.py',
            'marketer_rollout_runner.py',
        }
        for rel in sorted(required):
            dst = preview / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if rel in sources:
                dst.write_text(sources[rel], encoding='utf-8')
            else:
                dst.symlink_to(ROOT / rel)

        spec = importlib.util.spec_from_file_location('kpi_lifecycle_preview_tests', ROOT / 'tests/test_kpi_lifecycle_invariants.py')
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        mod.ROOT = preview
        names = sorted(n for n in dir(mod) if n.startswith('test_') and callable(getattr(mod, n)))
        failed = []
        for name in names:
            try:
                getattr(mod, name)()
                print('PASS', name)
            except Exception as exc:
                failed.append((name, type(exc).__name__, str(exc)))
                print('FAIL', name, type(exc).__name__, str(exc))
        print(f'KPI_PROPOSED_PATCH_PREVIEW patches={patch_ids} changed={sorted(changed)} total={len(names)} passed={len(names)-len(failed)} failed={len(failed)}')
        return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
