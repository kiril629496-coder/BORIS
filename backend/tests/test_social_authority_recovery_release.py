"""Release-bundle validation, including negative and atomic-write checks.

These tests do not install the release, change systemd or call a provider.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('social_release_review',ROOT/'run/social_authority_release_recovery_20261008.py')
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class SocialAuthorityReleaseTests(unittest.TestCase):
    def setUp(self):
        self.plan=release.load_plan()

    def test_exact_reviewed_live_policy_is_preserved(self):
        release.validate_live_review()
        for path in (release.POL,release.PREF,release.POLICY_SOURCE):
            self.assertNotIn(str(path),[r['path'] for r in self.plan['targets']])

    def test_guard_logic_is_identical_except_reviewed_dependency_versions(self):
        reverse={self.plan['new'][k]:v for k,v in self.plan['old'].items()}
        for name in ('hardguard','lock','authority'):
            row=next(r for r in self.plan['objects'] if r['key']==name)
            candidate=(release.BUNDLE/row['path']).read_text()
            for new,old in reverse.items():
                candidate=candidate.replace(new,old)
            original=(release.OBJ/self.plan['old'][name]).read_text()
            self.assertEqual(ast.dump(ast.parse(candidate)),ast.dump(ast.parse(original)))

    def test_dependency_chain_has_all_content_addressed_objects(self):
        by={r['key']:r for r in self.plan['objects']}
        for key,row in by.items():
            self.assertEqual(release.sha(release.BUNDLE/row['path']),self.plan['new'][key])
        for key,deps in [('hardguard',['policy','preflight']),('lock',['policy','preflight','hardguard']),('authority',['policy','preflight','hardguard','lock'])]:
            code=(release.BUNDLE/by[key]['path']).read_text()
            for dep in deps:self.assertIn(self.plan['new'][dep],code)

    def test_all_existing_immutable_targets_remain_immutable(self):
        for row in self.plan['targets']:
            if row['path']!=str(release.SOURCE):
                self.assertTrue(row['immutable'],row['path'])

    def test_contract_pointer_and_owner_are_bound(self):
        rows={r['path']:r for r in self.plan['targets']}
        raw=(release.BUNDLE/rows[str(release.CONTRACT)]['candidate']).read_bytes()
        pointer=(release.BUNDLE/rows[str(release.POINTER)]['candidate']).read_text().strip()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),pointer)
        value=json.loads(raw)
        self.assertTrue(value['single_writer'])
        self.assertFalse(value['source_mutation_allowed'])
        self.assertEqual(value['owner_workstream'],'social_reliability_core')
        self.assertEqual(value['authority_mode'],'read_only_verifier')

    def test_corrupt_candidate_is_rejected_without_writing_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle=Path(tmp)/'bundle';shutil.copytree(release.BUNDLE,bundle)
            row=self.plan['objects'][0]
            with (bundle/row['path']).open('ab') as f:f.write(b'corruption')
            with patch.object(release,'BUNDLE',bundle):
                with self.assertRaisesRegex(RuntimeError,'bundle_object_corrupt'):
                    release.load_plan()

    def test_atomic_write_preserves_data_mode_and_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'target';p.write_bytes(b'before')
            state={'immutable':True};changes=[]
            def set_flag(path,value):state['immutable']=value;changes.append(value)
            with patch.object(release,'immutable',side_effect=lambda path:state['immutable']),patch.object(release,'set_immutable',side_effect=set_flag):
                release.atomic_target(p,b'after',{'mode':0o440,'uid':os.getuid(),'gid':os.getgid(),'immutable':True})
            self.assertEqual(p.read_bytes(),b'after')
            self.assertEqual(p.stat().st_mode&0o777,0o440)
            self.assertEqual(changes,[False,True])

    def test_atomic_failure_restores_immutable_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'target';p.write_bytes(b'before')
            state={'immutable':True}
            def set_flag(path,value):state['immutable']=value
            with patch.object(release,'immutable',side_effect=lambda path:state['immutable']),patch.object(release,'set_immutable',side_effect=set_flag),patch.object(release.os,'replace',side_effect=OSError('simulated')):
                with self.assertRaises(OSError):
                    release.atomic_target(p,b'after',{'mode':0o440,'uid':os.getuid(),'gid':os.getgid(),'immutable':True})
            self.assertEqual(p.read_bytes(),b'before')
            self.assertTrue(state['immutable'])


if __name__=='__main__':unittest.main()
