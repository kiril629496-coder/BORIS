import hashlib
import unittest
from pathlib import Path

from app.services.campaign_review_page import _submitted_xml_cards


class CampaignReviewSubmittedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root=Path("/root/BORIS/backend/images/_test_review_contract")
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/"submitted.xml"
        self.data=b'''<?xml version="1.0" encoding="UTF-8"?>
<Ads><Ad><Id>ci-test</Id><Title>Test title</Title><Description>Test description</Description>
<Address>Test city</Address><Category>Test category</Category>
<Images><Image url="https://boris-ai.pro/images/test.jpg"/></Images></Ad></Ads>'''
        self.path.write_bytes(self.data)
        self.sha=hashlib.sha256(self.data).hexdigest()

    def tearDown(self):
        try: self.path.unlink()
        except FileNotFoundError: pass
        try: self.root.rmdir()
        except OSError: pass

    def test_uses_immutable_validated_artifact_not_live_feed_url(self):
        cards=_submitted_xml_cards({
            "feed_url":"https://boris-ai.pro/api/avito/feed/acc.xml",
            "validated_artifact":"/images/_test_review_contract/submitted.xml",
        },self.sha)
        self.assertEqual(len(cards),1)
        self.assertEqual(cards[0]["id"],"ci-test")

    def test_legacy_exact_sha_recovery_artifact_is_allowed(self):
        cards=_submitted_xml_cards({
            "feed_url":"https://boris-ai.pro/api/avito/feed/acc.xml",
        },self.sha,validated_artifact="/images/_test_review_contract/submitted.xml")
        self.assertEqual(cards[0]["title"],"Test title")

    def test_live_feed_url_alone_is_not_accepted_as_review_source(self):
        with self.assertRaisesRegex(RuntimeError,"immutable validated artifact"):
            _submitted_xml_cards({
                "feed_url":"https://boris-ai.pro/api/avito/feed/acc.xml",
            },self.sha)

    def test_wrong_sha_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError,"SHA differs"):
            _submitted_xml_cards({
                "validated_artifact":"/images/_test_review_contract/submitted.xml",
            },"0"*64)


if __name__=="__main__":
    unittest.main()
