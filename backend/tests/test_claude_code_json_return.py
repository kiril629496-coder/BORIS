# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ext_api import lead
from app.ext_api.errors import ApiError


class ClaudeCodeJsonReturnTests(unittest.TestCase):
    def _patches(self, completed):
        return (
            patch.object(lead, "claude_cli", return_value="/usr/bin/claude"),
            patch.object(lead, "oauth_token", return_value="token"),
            patch.object(lead, "_env_all", return_value={}),
            patch.object(lead, "_backend_dir", return_value="/tmp"),
            patch.object(lead.subprocess, "run", return_value=completed),
        )

    def test_ask_claude_code_returns_parsed_result(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"result":"{\\"summary\\":\\"ok\\",\\"evidence\\":\\"pytest: 3 passed\\"}"}',
            stderr="",
        )
        patches = self._patches(completed)
        for p in patches: p.start()
        try:
            with tempfile.TemporaryDirectory() as td, patch.object(lead.tempfile, "gettempdir", return_value=td):
                out = lead._ask_claude_code("test", tools=False)
        finally:
            for p in reversed(patches): p.stop()
        self.assertEqual(out["summary"], "ok")
        self.assertEqual(out["evidence"], "pytest: 3 passed")
    def test_mcp_config_is_unique_temp_and_cleaned(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"result":"{\\"summary\\":\\"ok\\"}"}',
            stderr="",
        )
        captured = {}

        def fake_run(argv, **kwargs):
            cfg = argv[argv.index("--mcp-config") + 1]
            captured["cfg"] = cfg
            self.assertTrue(os.path.exists(cfg))
            self.assertTrue(os.path.basename(cfg).startswith("boris_mcp_"))
            self.assertNotEqual(os.path.basename(cfg), "boris_mcp.json")
            return completed

        with tempfile.TemporaryDirectory() as td,              patch.object(lead, "claude_cli", return_value="/usr/bin/claude"),              patch.object(lead, "oauth_token", return_value="token"),              patch.object(lead, "_env_all", return_value={}),              patch.object(lead, "_backend_dir", return_value="/tmp"),              patch.object(lead.tempfile, "gettempdir", return_value=td),              patch.object(lead.subprocess, "run", side_effect=fake_run):
            out = lead._ask_claude_code("test", tools=False)

        self.assertEqual(out["summary"], "ok")
        self.assertIn("cfg", captured)
        self.assertFalse(os.path.exists(captured["cfg"]))

    def test_ask_claude_code_nonzero_is_dependency_error(self):
        completed = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        patches = self._patches(completed)
        for p in patches: p.start()
        try:
            with tempfile.TemporaryDirectory() as td, patch.object(lead.tempfile, "gettempdir", return_value=td):
                with self.assertRaises(ApiError) as exc:
                    lead._ask_claude_code("test", tools=False)
        finally:
            for p in reversed(patches): p.stop()
        self.assertEqual(exc.exception.code, "DEPENDENCY_UNAVAILABLE")
        self.assertIn("boom", exc.exception.message)


if __name__ == "__main__":
    unittest.main()
