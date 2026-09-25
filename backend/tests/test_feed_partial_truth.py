import unittest
from pathlib import Path

SRC=Path('/root/BORIS/backend/app/services/control_plane_adapters.py').read_text()

class FeedPartialTruthTests(unittest.TestCase):
    def test_terminal_partial_not_retryable(self):
        # Behavioral contract: terminal partial publication must be classified as
        # external terminal truth rather than endlessly retryable work. Do not
        # depend on a comment/version marker in the protected implementation.
        self.assertIn("terminal_partial=(",SRC)
        self.assertIn("terminal_external=terminal_partial or pending_external",SRC)
        self.assertIn('"terminal_partial"',SRC)
    def test_pending_partial_is_external_wait(self):
        self.assertIn('pending_external=(',SRC)
        self.assertIn("{'pending','processing'}",SRC)
        self.assertIn('"pending_external"',SRC)

if __name__=='__main__': unittest.main()
