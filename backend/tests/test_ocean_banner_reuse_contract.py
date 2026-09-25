# -*- coding: utf-8 -*-
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import posting_runner as pr
from app.services import prospect_banner


class OceanBannerReuseContractTest(unittest.TestCase):
    def test_existing_ocean_social_pool_is_nonempty(self):
        pool = prospect_banner._social_project_paths()
        self.assertGreater(len(pool), 0)
        self.assertTrue(all(p.is_file() for p in pool))

    def test_reuse_selector_is_deterministic_and_uses_existing_pool(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "existing.png"
            Image.new("RGB", (512, 512), (100, 100, 100)).save(path)
            # Pad to match the production provenance size guard.
            if path.stat().st_size < 50_000:
                with path.open("ab") as fh:
                    fh.write(b"x" * (50_000 - path.stat().st_size + 1))

            with patch.object(prospect_banner, "_social_project_paths", return_value=[path]):
                first = pr._reuse_existing_ocean_social_banner(
                    "u2", "ocean", "theme", "text"
                )
                second = pr._reuse_existing_ocean_social_banner(
                    "u2", "ocean", "theme", "text"
                )
            self.assertEqual(first, str(path))
            self.assertEqual(first, second)

    def test_generate_post_reuse_never_calls_image_generator(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "existing.png"
            Image.new("RGB", (512, 512), (110, 110, 110)).save(path)
            if path.stat().st_size < 50_000:
                with path.open("ab") as fh:
                    fh.write(b"x" * (50_000 - path.stat().st_size + 1))

            with patch.object(pr, "_openai_text", return_value="Полезный пост"),                  patch.object(
                     pr,
                     "_reuse_existing_ocean_social_banner",
                     return_value=str(path),
                 ),                  patch(
                     "app.services.image_service.generate_banner_financially_guarded"
                 ) as image_api:
                result = pr.generate_post(
                    "u2",
                    "Автоматизация продаж",
                    image_source="reuse",
                    project_id="ocean",
                )

            self.assertEqual(result.get("banner"), str(path))
            self.assertEqual(result.get("banner_engine"), "reuse_existing_social")
            self.assertEqual(result.get("banner_model"), "none")
            image_api.assert_not_called()

    def test_reuse_is_media_required_not_text_only_fallback(self):
        source = inspect.getsource(pr._banner_delivery_ready)
        self.assertIn('("ai", "pexels", "reuse")', source)
        ok, error = pr._banner_delivery_ready(
            {"banner": None, "banner_error": "REUSE_BANNER_FAILED"},
            "reuse",
        )
        self.assertFalse(ok)
        self.assertIn("REUSE_BANNER_FAILED", error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
