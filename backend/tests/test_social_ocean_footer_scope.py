import json
import unittest
from pathlib import Path

import posting_runner as pr
from app.db.session import SessionLocal
from app.models.storage import Storage


class SocialOceanFooterScopeTest(unittest.TestCase):
    def test_live_ocean_footer_is_client_configuration(self):
        s=SessionLocal()
        try:
            row=s.query(Storage).filter(
                Storage.account_id=="u2",Storage.key=="posting_projects"
            ).first()
            projects=json.loads(row.value or "[]") if row else []
        finally:s.close()
        ocean=next(x for x in projects if x.get("id")=="a374ced25745")
        self.assertTrue(str(ocean.get("footer_signature") or "").strip())

    def test_pre_slot_requires_footer_without_inventing_it(self):
        source=Path("posting_runner.py").read_text(encoding="utf-8")
        self.assertIn('"ocean_footer_signature_missing"',source)
        self.assertIn('project.get("footer_signature")',source)

    def test_current_live_ocean_is_ready(self):
        h=pr.social_owner_pre_slot_readiness_health("u2")
        ocean=next(x for x in h["projects"] if x.get("project_id")=="a374ced25745")
        self.assertNotIn("ocean_footer_signature_missing",ocean.get("blocked_reasons") or [])


if __name__=="__main__":
    unittest.main()
