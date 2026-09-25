import hashlib
import unittest
from pathlib import Path

from app.services.jobs import _submitted_snapshot_feed_ids


class CampaignObservationScopeTests(unittest.TestCase):
    def setUp(self):
        self.root=Path("/root/BORIS/backend/images/_test_observation_scope")
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/"submitted.xml"
        self.data=b'''<?xml version="1.0" encoding="UTF-8"?>
<Ads>
 <Ad><Id>ci-one</Id><Title>A</Title></Ad>
 <Ad><Id>ci-two</Id><Title>B</Title></Ad>
</Ads>'''
        self.path.write_bytes(self.data)
        self.sha=hashlib.sha256(self.data).hexdigest()

    def tearDown(self):
        try: self.path.unlink()
        except FileNotFoundError: pass
        try: self.root.rmdir()
        except OSError: pass

    def test_exact_submitted_scope_is_read_from_sha_verified_artifact(self):
        ids=_submitted_snapshot_feed_ids({
            "sha256":self.sha,
            "validated_artifact":"/images/_test_observation_scope/submitted.xml",
        })
        self.assertEqual(ids,["ci-one","ci-two"])

    def test_wrong_sha_fails_closed(self):
        self.assertEqual(_submitted_snapshot_feed_ids({
            "sha256":"0"*64,
            "validated_artifact":"/images/_test_observation_scope/submitted.xml",
        }),[])

    def test_live_feed_url_is_not_scope_authority(self):
        self.assertEqual(_submitted_snapshot_feed_ids({
            "sha256":self.sha,
            "validated_artifact":"https://boris-ai.pro/api/avito/feed/acc.xml",
        }),[])

    def test_same_lock_scope_selfheal_contract_is_present(self):
        src=Path("/root/BORIS/backend/app/services/jobs.py").read_text(encoding="utf-8")
        self.assertIn("OBSERVATION_SCOPE_SUBMITTED_XML_SELFHEAL_V1",src)
        self.assertIn('existing["scope_feed_ids"]=list(_snapshot_scope_ids)',src)
        self.assertIn('existing["scope_source"]="submitted_validated_artifact"',src)


if __name__=="__main__":
    unittest.main()
