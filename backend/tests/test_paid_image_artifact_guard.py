import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from app.services import image_service as image


class PaidImageArtifactGuardTests(unittest.TestCase):
    def test_valid_png_is_cache_hit_without_provider(self):
        with tempfile.TemporaryDirectory(prefix='boris-paid-artifact-') as td:
            path = Path(td) / 'valid.png'
            Image.new('RGB', (512, 512), (120, 130, 140)).save(path, 'PNG')
            with patch.object(image, '_audit_ai_guard'):
                result = image.generate_banner_financially_guarded(
                    db=None, account_id='qa_artifact', prompt='must not run', save_path=str(path)
                )
            self.assertTrue(result.get('ok'))
            self.assertTrue(result.get('cache_hit'))
            self.assertEqual(result.get('cost_rub'), 0.0)

    def test_truncated_existing_file_fails_closed_without_provider(self):
        with tempfile.TemporaryDirectory(prefix='boris-paid-artifact-') as td:
            path = Path(td) / 'truncated.png'
            path.write_bytes(b'not-an-image' * 200)
            with patch.object(image, '_audit_ai_guard') as audit, \
                 patch.object(image, '_generate_banner_after_artifact_lock', side_effect=AssertionError('provider path called')):
                result = image.generate_banner_financially_guarded(
                    db=None, account_id='qa_artifact', prompt='must not run', save_path=str(path)
                )
            self.assertFalse(result.get('ok'))
            self.assertEqual(result.get('error'), 'PAID_IMAGE_ARTIFACT_INVALID')
            self.assertTrue(result.get('invalid_existing_artifact'))
            audit.assert_called_once()

    def test_tiny_file_is_never_a_paid_cache_hit(self):
        with tempfile.TemporaryDirectory(prefix='boris-paid-artifact-') as td:
            path = Path(td) / 'tiny.jpg'
            path.write_bytes(b'\xff\xd8\xff\xd9')
            self.assertFalse(image._valid_image_artifact(path))


if __name__ == '__main__':
    unittest.main()
