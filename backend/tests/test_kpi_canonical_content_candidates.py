import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.campaign_identity import is_canonical_writable

SRC=Path('/root/BORIS/backend/app/api/avito.py').read_text()


def _row(*, identity_status="published_identity_bound", feed_identity="ci:test",
         avito_item_id="8270000001", resolution="publication_result", upload_id=None,
         payload_feed_identity=None, payload_avito_item_id=None):
    identity={
        "feed_identity": payload_feed_identity if payload_feed_identity is not None else feed_identity,
        "avito_item_id": payload_avito_item_id if payload_avito_item_id is not None else avito_item_id,
        "resolution": resolution,
    }
    if upload_id is not None:
        identity["upload_id"]=upload_id
    return SimpleNamespace(
        feed_identity=feed_identity,
        avito_item_id=avito_item_id,
        identity_status=identity_status,
        payload_json=json.dumps({"identity":identity}),
    )


class CanonicalCandidates(unittest.TestCase):
    def test_planner_rechecks_live_db_authority(self):
        self.assertIn('KPI_CANONICAL_MUTATION_AUTHORITY_V3',SRC)
        self.assertIn('is_canonical_writable as _is_mutation_writable',SRC)
        self.assertIn('len(_rows)==1 and _is_mutation_writable(_rows[0])',SRC)
        self.assertIn('_mutation_weak_items=list(canonical_weak_items)',SRC)
        self.assertIn('_content_remaining_today = (',SRC)
        self.assertIn('_content_batch_cap = (',SRC)
        self.assertIn('min(25, _content_remaining_today)',SRC)
        self.assertIn('candidate_items=_mutation_weak_items[:_content_cohort_size]',SRC)

    def test_publication_result_exact_pair_is_writable(self):
        self.assertTrue(is_canonical_writable(_row()))

    def test_autoload_exact_pair_requires_upload_id(self):
        self.assertTrue(is_canonical_writable(_row(resolution="autoload_report",upload_id=592404562)))
        self.assertFalse(is_canonical_writable(_row(resolution="autoload_report",upload_id=None)))

    def test_unresolved_live_placeholder_is_read_only(self):
        self.assertFalse(is_canonical_writable(_row(
            identity_status="external_linked_feed_unresolved",
            resolution="live_api_import",
        )))

    def test_payload_identity_mismatch_is_read_only(self):
        self.assertFalse(is_canonical_writable(_row(payload_avito_item_id="9999999999")))
        self.assertFalse(is_canonical_writable(_row(payload_feed_identity="ci:other")))

    def test_generated_feed_identity_alone_is_never_authority(self):
        self.assertFalse(is_canonical_writable(_row(
            identity_status="external_linked_feed_unresolved",
            resolution="autoload_report",
            upload_id=592404562,
        )))


if __name__=='__main__':
    unittest.main()
