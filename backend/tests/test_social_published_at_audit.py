import hashlib
import json
import subprocess
import unittest
from pathlib import Path

BASE=Path("/var/lib/boris-social-audit")

class SocialPublishedAtAuditTest(unittest.TestCase):
    def test_baseline_is_content_addressed_and_nonempty(self):
        sha=(BASE/"baseline.sha").read_text().strip()
        p=BASE/f"baseline.{sha}.json"
        self.assertTrue(p.exists())
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),sha)
        data=json.loads(p.read_text())
        self.assertGreaterEqual(len(data["known_historical_missing"]),91)
        self.assertFalse(any(
            str(x.get("account_id") or "")=="u2"
            and str(x.get("id") or "")=="df6a951bc312"
            for x in data["known_historical_missing"]
        ))

    def test_auditor_pins_and_can_restore_baseline_root_object(self):
        audit_sha=(BASE/"audit.sha").read_text().strip()
        audit=BASE/f"audit.{audit_sha}.py"
        text=audit.read_text()
        baseline_sha=(BASE/"baseline.sha").read_text().strip()
        self.assertIn(f'EXPECTED_BASELINE_SHA="{baseline_sha}"',text)
        self.assertIn('OBJECTS=Path("/var/lib/boris-integrity/objects")',text)
        self.assertIn('"published_at_baseline_root_object_failed"',text)
        self.assertIn('BASELINE_HEALED.append("pointer")',text)
        self.assertIn('BASELINE_HEALED.append("payload")',text)
        root=Path("/var/lib/boris-integrity/objects")/baseline_sha
        self.assertTrue(root.exists())
        self.assertEqual(hashlib.sha256(root.read_bytes()).hexdigest(),baseline_sha)

    def test_auditor_is_content_addressed(self):
        sha=(BASE/"audit.sha").read_text().strip()
        p=BASE/f"audit.{sha}.py"
        self.assertTrue(p.exists())
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),sha)

    def test_systemd_service_verifies_hash(self):
        text=Path("/etc/systemd/system/boris-social-published-at-audit.service").read_text()
        sha=(BASE/"audit.sha").read_text().strip()
        self.assertIn("sha256sum -c",text)
        self.assertIn(sha,text)

    def test_guardian_owns_audit_timer(self):
        drop=Path("/etc/systemd/system/boris-social-guardian.service.d/published-at-audit.conf").read_text()
        self.assertIn("Wants=boris-social-published-at-audit.timer",drop)
        timer=Path("/etc/systemd/system/boris-social-published-at-audit.timer").read_text()
        self.assertIn("OnUnitActiveSec=120s",timer)

    def test_live_audit_heartbeat_is_green(self):
        code='''from app.db.session import SessionLocal
from sqlalchemy import text
s=SessionLocal()
try:
 r=s.execute(text("select state,details_json from reliability_heartbeats where module='runtime' and worker_id='social_published_at_audit' order by id desc limit 1")).mappings().first()
 assert r and r["state"]=="ok", r
 assert int((r["details_json"] or {}).get("new_missing_after",999))==0, r
finally:s.close()
'''
        p=subprocess.run(
            ["/root/BORIS/backend/venv/bin/python","-c",code],
            cwd="/root/BORIS/backend",capture_output=True,text=True
        )
        self.assertEqual(p.returncode,0,p.stderr or p.stdout)

if __name__=="__main__":
    unittest.main()
