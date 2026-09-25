import inspect
import unittest
from pathlib import Path

import posting_runner as pr


class SocialOwnerProjectIsolationTest(unittest.TestCase):
    def test_autopost_all_supports_skip_accounts_without_changing_default(self):
        sig=inspect.signature(pr.autopost_all)
        self.assertIn("skip_accounts",sig.parameters)
        self.assertIsNone(sig.parameters["skip_accounts"].default)
        src=inspect.getsource(pr.autopost_all)
        self.assertIn('if str(b) in skip_accounts:',src)
        self.assertIn('autopost_projects(b, owner=owners[b], respect_time=respect_time)',src)

    def test_worker_isolates_blocked_owner_project(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        self.assertIn('skip_accounts=set(paid_blocked_accounts)',text)
        self.assertIn('skip_accounts.add("u2")',text)
        self.assertIn('skip_accounts=skip_accounts or None',text)
        self.assertIn('autopost_projects(',text)
        self.assertIn('"targeted_ready_owner_projects":targeted_owner',text)
        self.assertIn('"targeted_ready_paid_projects":targeted_paid',text)
        self.assertIn('"other_accounts_continued":True',text)
        self.assertIn('"PRE_SLOT_READINESS_PARTIAL"',text)
        self.assertIn('transport_projects.get(("u2",pid))',text)

    def test_due_delivery_stays_outside_generation_gate(self):
        text=Path("boris_background_worker.py").read_text(encoding="utf-8")
        gate=text.index('generation_bucket = int(time.time() // 300)')
        due=text.index('due_posts_all(dry=False)',gate)
        self.assertGreater(due,gate)
        # Existing drafts must still be retried even when new generation is blocked.
        self.assertIn('details["due_checked"] = True',text[due:due+500])


if __name__=="__main__":
    unittest.main()
