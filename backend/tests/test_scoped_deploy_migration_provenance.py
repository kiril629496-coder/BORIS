"""Offline checks: a module release must retain the baseline migration runner."""
import ast
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

WRAPPER=Path('/usr/local/sbin/boris-backend-scoped-deploy')


def helper():
    tree=ast.parse(WRAPPER.read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_stage_certification_migration_runner')
    def fail(message):raise RuntimeError(message)
    scope={'Path':Path,'shutil':shutil,'fail':fail,'sha256':lambda p:hashlib.sha256(p.read_bytes()).hexdigest()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(WRAPPER),'exec'),scope)
    return scope['_stage_certification_migration_runner'],scope


class ScopedMigrationProvenanceTests(unittest.TestCase):
    def test_preserves_baseline_bytes_and_not_workspace_bytes(self):
        fn,scope=helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);baseline=root/'baseline';stage=root/'stage';workspace=root/'workspace'
            for p in (baseline,stage,workspace):(p/'migrations').mkdir(parents=True)
            (baseline/'migrations/run_all.py').write_text('BASELINE')
            (workspace/'migrations/run_all.py').write_text('UNRELATED_UPGRADE')
            scope['WORKSPACE']=workspace
            fn(baseline,stage)
            self.assertEqual((stage/'migrations/run_all.py').read_text(),'BASELINE')
            self.assertEqual((workspace/'migrations/run_all.py').read_text(),'UNRELATED_UPGRADE')

    def test_missing_baseline_refuses_implicit_upgrade(self):
        fn,scope=helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);baseline=root/'baseline';stage=root/'stage';workspace=root/'workspace'
            (workspace/'migrations').mkdir(parents=True)
            (workspace/'migrations/run_all.py').write_text('UNRELATED')
            scope['WORKSPACE']=workspace
            with self.assertRaisesRegex(RuntimeError,'CERT_BASELINE_MIGRATION_RUNNER_MISSING'):
                fn(baseline,stage)
            self.assertFalse((stage/'migrations/run_all.py').exists())

    def test_corrupt_copy_is_rejected(self):
        fn,scope=helper()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);baseline=root/'baseline';stage=root/'stage'
            (baseline/'migrations').mkdir(parents=True)
            (baseline/'migrations/run_all.py').write_text('BASELINE')
            def corrupt(src,dst):Path(dst).write_text('CORRUPT')
            with patch.object(shutil,'copy2',side_effect=corrupt):
                with self.assertRaisesRegex(RuntimeError,'COPY_MISMATCH'):fn(baseline,stage)

    def test_canonical_entrypoint_uses_baseline_helper(self):
        source=WRAPPER.read_text()
        self.assertIn('_stage_certification_migration_runner(baseline_backend,source)',source)
        self.assertNotIn('mig_runner=WORKSPACE/"migrations"/"run_all.py"',source)
        self.assertIn('SCOPED_CERTIFICATION_FAILED',source)
        self.assertIn('APPROVED_HASH_MISMATCH',source)


if __name__=='__main__':unittest.main()
