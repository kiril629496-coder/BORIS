import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import production_contract_runner as C


class RuntimeGenerationCtimeGuardTests(unittest.TestCase):
    def test_atomic_edit_ctime_marks_pre_edit_runtime_stale_even_when_mtime_is_old(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "runtime_source.py"
            p.write_text("x=1\n", encoding="utf-8")
            now = time.time()
            os.utime(p, (now - 120, now - 120))
            # Metadata/content replacement changes ctime even if an editor preserves
            # an older mtime. A runtime started before that ctime must be stale.
            ctime = p.stat().st_ctime
            groups = {"qa_runtime_generation": (["qa.service"], [p])}
            with patch.object(C, "RUNTIME_GROUPS", groups), patch.object(C, "_service_started_epoch", return_value=ctime - 1.0):
                state, stale = C._runtime_generation_state()
            self.assertFalse(state["qa_runtime_generation"])
            self.assertEqual(stale["qa_runtime_generation"], ["qa.service"])

    def test_runtime_newer_than_both_mtime_and_ctime_passes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "runtime_source.py"
            p.write_text("x=1\n", encoding="utf-8")
            newest = max(p.stat().st_mtime, p.stat().st_ctime)
            groups = {"qa_runtime_generation": (["qa.service"], [p])}
            with patch.object(C, "RUNTIME_GROUPS", groups), patch.object(C, "_service_started_epoch", return_value=newest + 1.0):
                state, stale = C._runtime_generation_state()
            self.assertTrue(state["qa_runtime_generation"])
            self.assertNotIn("qa_runtime_generation", stale)

    def test_background_generation_includes_post_publish_watchdog(self):
        expected = C.BACKEND_ACTIVE_ROOT / "app/services/campaign_post_publish_watch.py"
        self.assertIn(expected, C.BACKGROUND_RUNTIME_PATHS)


if __name__ == "__main__":
    unittest.main()
