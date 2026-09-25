import json
import tempfile
import unittest
from pathlib import Path

from app.services.social_editorial_readonly_authority import (
    SocialEditorialAuthorityError,
    load_client_editorial_authority,
    ocean_footer_signature,
)


class SocialEditorialReadonlyAuthorityTest(unittest.TestCase):
    def _authority(self, footer: str) -> dict:
        return {
            "schema": 1,
            "version": "social_editorial_v3_20260905",
            "account_id": "u2",
            "projects": {
                "a374ced25745": {"footer_signature": footer},
            },
        }

    def test_footer_follows_editorial_authority_without_hardcoded_copy(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "authority.json"
            path.write_text(
                json.dumps(self._authority("Первая подпись"), ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(ocean_footer_signature(path), "Первая подпись")
            path.write_text(
                json.dumps(self._authority("Новая подпись"), ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(ocean_footer_signature(path), "Новая подпись")

    def test_invalid_authority_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "authority.json"
            data = self._authority("Подпись")
            data["account_id"] = "wrong"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SocialEditorialAuthorityError):
                load_client_editorial_authority(path)


if __name__ == "__main__":
    unittest.main()
