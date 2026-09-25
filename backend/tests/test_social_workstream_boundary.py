import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.services.social_workstream_boundary import social_workstream_boundary_health


class SocialWorkstreamBoundaryTest(unittest.TestCase):
    def test_live_registry_has_one_social_runtime_chat(self):
        out = social_workstream_boundary_health()
        self.assertTrue(out["ok"], out)

    def test_legacy_chat_cannot_become_second_runtime_writer(self):
        live = Path("/root/BORIS/backend/config/workstream_scope_registry.json")
        data = json.loads(live.read_text(encoding="utf-8"))
        rows = data["workstreams"]["workstream_coordination"]["neighbor_chat_map"]
        target = next(x for x in rows if x["chat"] == "Обновление автопостинга BORIS")
        target["owner"] = "social_reliability_core"
        target["mode"] = "social_runtime_only"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "registry.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            out = social_workstream_boundary_health(p)
        self.assertFalse(out["ok"], out)
        reasons = {x.get("reason") for x in out["violations"]}
        self.assertIn("owner_mismatch", reasons)
        self.assertIn("mode_mismatch", reasons)

    def test_editorial_chat_cannot_claim_shared_runtime_path(self):
        live = Path("/root/BORIS/backend/config/workstream_scope_registry.json")
        data = json.loads(live.read_text(encoding="utf-8"))
        data = deepcopy(data)
        data["workstreams"]["social_editorial_client_config"]["writable_paths"].append(
            "posting_runner.py"
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "registry.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            out = social_workstream_boundary_health(p)
        self.assertFalse(out["ok"], out)
        self.assertTrue(
            any(x.get("reason") == "editorial_claims_social_runtime_path"
                for x in out["violations"]),
            out,
        )


if __name__ == "__main__":
    unittest.main()
