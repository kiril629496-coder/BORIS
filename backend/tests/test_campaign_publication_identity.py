import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.campaign_identity import (
    bind_from_autoload_evidence, bind_publication, record_observation,
    materialize_exact_feed_target, is_canonical_writable,
)


class _Query:
    def __init__(self, rows):
        self.rows = rows
    def filter(self, *args, **kwargs):
        return self
    def order_by(self, *args, **kwargs):
        return self
    def all(self):
        return list(self.rows)
    def first(self):
        return self.rows[0] if self.rows else None


class _DB:
    def __init__(self, query_results):
        self.query_results = list(query_results)
        self.flush_count = 0
    def query(self, *args, **kwargs):
        if not self.query_results:
            raise AssertionError("unexpected query")
        return _Query(self.query_results.pop(0))
    def flush(self):
        self.flush_count += 1


def _row(**overrides):
    base = dict(
        id=10,
        account_id="acc",
        campaign_id=20,
        feed_identity="ci:test",
        avito_item_id=None,
        identity_status="feed_identity_ready",
        status="draft",
        source="feed_factory",
        source_ref="item:10",
        payload_json="{}",
        content_hash=None,
        external_item_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class CampaignPublicationIdentityRegressionTests(unittest.TestCase):
    def test_exact_feed_target_materialization_is_read_only_until_provider_bind(self):
        campaign = SimpleNamespace(id=20)
        row = _row(
            feed_identity=None,
            avito_item_id=None,
            identity_status=None,
            status="draft",
            source="autoload_exact_feed_recovery",
            source_ref=None,
        )
        db = _DB([[], [], [campaign]])
        feed = {"id":"feed:exact:1", "title":"Exact feed title", "price":12345,
                "description":"Full authoritative feed payload"}
        with patch("app.services.campaign_identity.CS.add_item", return_value=row) as add_item:
            out = materialize_exact_feed_target(db, "acc", feed, "8270000099")
        self.assertEqual(out["status"], "materialized")
        self.assertEqual(row.feed_identity, "feed:exact:1")
        self.assertEqual(row.avito_item_id, "8270000099")
        self.assertEqual(row.identity_status, "feed_identity_ready")
        self.assertFalse(is_canonical_writable(row))
        payload = json.loads(row.payload_json)
        self.assertFalse(payload["identity"]["write_authority"])
        self.assertEqual(payload["identity"]["resolution"],
                         "authoritative_feed_materialization_pending_autoload_bind")
        add_item.assert_called_once()

    def test_bind_publication_marks_item_published(self):
        row = _row()
        db = _DB([[row], []])
        out = bind_publication(
            db, "acc", "ci:test", "8270000001",
            published_at="2026-09-03T00:00:00Z",
            publication_version="upload:1",
        )
        self.assertEqual(out["status"], "bound")
        self.assertEqual(row.status, "published")
        self.assertEqual(row.identity_status, "published_identity_bound")
        self.assertEqual(row.avito_item_id, "8270000001")
        payload = json.loads(row.payload_json)
        self.assertEqual(payload["identity"]["resolution"], "publication_result")
        self.assertEqual(db.flush_count, 1)

    def test_autoload_binding_marks_existing_canonical_item_published(self):
        row = _row()
        db = _DB([[row], []])
        out = bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000002",
            upload_id=591000001,
            report_section="successful",
            avito_status="active",
        )
        self.assertEqual(out["status"], "bound")
        self.assertEqual(row.status, "published")
        self.assertEqual(row.identity_status, "published_identity_bound")
        self.assertEqual(row.avito_item_id, "8270000002")
        payload = json.loads(row.payload_json)
        self.assertEqual(payload["identity"]["resolution"], "autoload_report")
        self.assertEqual(db.flush_count, 1)

    def test_autoload_placeholder_promotion_marks_item_published(self):
        row = _row(
            feed_identity=None,
            avito_item_id="8270000003",
            identity_status="external_linked_feed_unresolved",
            source="avito_live",
            status="imported",
        )
        db = _DB([[], [row]])
        out = bind_from_autoload_evidence(
            db, "acc", "ci:recovered", "8270000003",
            upload_id=591000002,
            avito_status="active",
        )
        self.assertEqual(out["status"], "bound")
        self.assertEqual(row.feed_identity, "ci:recovered")
        self.assertEqual(row.status, "published")
        self.assertEqual(row.identity_status, "published_identity_bound")
        self.assertEqual(db.flush_count, 1)

    def test_autoload_terminal_binding_is_publication_proof_for_measurement(self):
        row = _row(
            avito_item_id="8270000004",
            identity_status="published_identity_bound",
            status="ready",
            payload_json=json.dumps({"identity": {
                "resolution":"autoload_report",
                "upload_id":592404562,
                "avito_item_id":"8270000004",
            }}),
        )
        out = record_observation(row, {"snapshot_date":"2026-09-04","views":1,"contacts":0})
        self.assertEqual(out["status"], "no_version")

    def test_unproven_autoload_payload_still_waits_for_publication(self):
        row = _row(
            avito_item_id="8270000005",
            identity_status="published_identity_bound",
            status="published",
            payload_json=json.dumps({"identity": {
                "resolution":"autoload_report",
                "upload_id":None,
                "avito_item_id":"8270000005",
            }}),
        )
        out = record_observation(row, {"snapshot_date":"2026-09-04","views":1,"contacts":0})
        self.assertEqual(out["status"], "waiting_publish")

    def test_reconcile_published_status_repairs_ready_drift_only_for_canonical_identity(self):
        from app.services.campaign_identity import reconcile_published_status
        row = _row(
            avito_item_id="8270000006",
            identity_status="published_identity_bound",
            status="ready",
            payload_json=json.dumps({"identity": {
                "feed_identity":"ci:test",
                "avito_item_id":"8270000006",
                "resolution":"autoload_report",
                "upload_id":592404562,
            }}),
        )
        out = reconcile_published_status([row])
        self.assertEqual(out["changed"], 1)
        self.assertEqual(row.status, "published")

    def test_reconcile_published_status_does_not_promote_unproven_identity(self):
        from app.services.campaign_identity import reconcile_published_status
        row = _row(
            avito_item_id="8270000007",
            identity_status="published_identity_bound",
            status="ready",
            payload_json=json.dumps({"identity": {
                "feed_identity":"ci:test",
                "avito_item_id":"8270000007",
                "resolution":"autoload_report",
                "upload_id":None,
            }}),
        )
        out = reconcile_published_status([row])
        self.assertEqual(out["changed"], 0)
        self.assertEqual(row.status, "ready")

    def test_live_stats_snapshot_wires_exact_active_status_selfheal(self):
        from pathlib import Path
        src = Path("app/api/avito.py").read_text(encoding="utf-8")
        start = src.index("def _collect_stats_unlocked")
        block = src[start:src.index("\ndef ", start + 5)]
        self.assertIn("reconcile_published_status", block)
        self.assertIn('identity_reconciliation["published_status_repair"]', block)
        self.assertIn('params={"per_page": 100, "page": page, "status": "active"}', block)

    def test_description_rejection_stays_sticky_on_same_upload(self):
        row = _row(
            avito_item_id="8270000008",
            identity_status="publication_rejected_bound",
            status="draft",
            payload_json=json.dumps({
                "identity":{
                    "resolution":"autoload_report",
                    "upload_id":100,
                    "publication_state":"rejected",
                    "publication_rejection_upload_id":100,
                },
                "publication_repairs":[{
                    "reason":"avito_message_2017_description_policy",
                    "external_action":False,
                }],
            }),
        )
        db = _DB([[row], [row]])
        bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000008",
            upload_id=100, report_section="success_skipped", avito_status="active",
        )
        self.assertEqual(row.status, "draft")
        self.assertEqual(row.identity_status, "publication_rejected_bound")
        payload=json.loads(row.payload_json)
        self.assertEqual(payload["identity"]["publication_rejection_upload_id"],100)
        self.assertEqual(payload["identity"]["publication_state"],"rejected")

    def test_newer_successful_upload_supersedes_description_rejection(self):
        row = _row(
            avito_item_id="8270000009",
            identity_status="publication_rejected_bound",
            status="draft",
            payload_json=json.dumps({
                "identity":{
                    "resolution":"autoload_report",
                    "upload_id":100,
                    "publication_state":"rejected",
                    "publication_rejection_upload_id":100,
                },
                "publication_repairs":[{
                    "reason":"avito_message_2017_description_policy",
                    "external_action":False,
                }],
            }),
        )
        db = _DB([[row], [row]])
        bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000009",
            upload_id=101, report_section="success_skipped", avito_status="active",
        )
        # success_skipped means Avito kept the existing live listing; it is not
        # proof that the rejected repaired revision was accepted. Keep the causal
        # rejection sticky until a newer non-skipped provider success exists.
        self.assertEqual(row.status, "draft")
        self.assertEqual(row.identity_status, "publication_rejected_bound")
        payload=json.loads(row.payload_json)
        self.assertEqual(payload["identity"]["publication_state"],"rejected")
        self.assertEqual(payload["identity"]["publication_rejection_upload_id"],100)
        self.assertNotIn("publication_rejection_superseded_by_upload_id",payload["identity"])

    def test_newer_non_skipped_success_clears_description_rejection(self):
        row = _row(
            avito_item_id="8270000012",
            identity_status="publication_rejected_bound",
            status="draft",
            payload_json=json.dumps({
                "identity":{
                    "resolution":"autoload_report",
                    "upload_id":100,
                    "publication_state":"rejected",
                    "publication_rejection_upload_id":100,
                },
                "publication_repairs":[{
                    "reason":"avito_message_2017_description_policy",
                    "external_action":False,
                }],
            }),
        )
        db = _DB([[row], [row]])
        out = bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000012",
            upload_id=101, report_section="successful", avito_status="active",
        )
        self.assertIn(out["status"], {"exists", "bound"})
        self.assertEqual(row.status, "published")
        self.assertEqual(row.identity_status, "published_identity_bound")
        payload=json.loads(row.payload_json)
        self.assertNotIn("publication_state",payload["identity"])
        self.assertNotIn("publication_error_status",payload["identity"])
        self.assertNotIn("publication_error_section",payload["identity"])
        self.assertEqual(payload["identity"]["publication_rejection_upload_id"],100)
        self.assertEqual(
            payload["identity"]["publication_rejection_superseded_by_upload_id"],
            101,
        )

    def test_active_error_other_funding_warning_is_not_publication_rejection(self):
        row = _row(
            avito_item_id="8270000013",
            identity_status="publication_rejected_bound",
            status="draft",
            payload_json=json.dumps({"identity": {
                "resolution": "autoload_report",
                "upload_id": 594950969,
                "publication_state": "rejected",
                "publication_rejection_upload_id": 594950969,
                "publication_error_status": "active",
                "publication_error_section": "error_other",
            }}),
        )
        db = _DB([[row], [row]])
        out = bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000013",
            upload_id=594950969,
            report_section="error_other",
            avito_status="active",
        )
        self.assertIn(out["status"], {"exists", "bound"})
        self.assertEqual(row.status, "published")
        self.assertEqual(row.identity_status, "published_identity_bound")
        payload = json.loads(row.payload_json)
        self.assertNotIn("publication_state", payload["identity"])
        self.assertNotIn("publication_error_status", payload["identity"])
        self.assertNotIn("publication_error_section", payload["identity"])

    def test_autoload_does_not_resurrect_superseded_item(self):
        row = _row(
            avito_item_id="8270000011",
            identity_status="superseded_distinct_cutover",
            status="superseded",
            payload_json=json.dumps({"identity": {
                "resolution": "publication_result",
                "avito_item_id": "8270000011",
                "feed_identity": "ci:test",
            }}),
        )
        db = _DB([[row], [row]])
        out = bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000011",
            upload_id=592715731,
            report_section="successful",
            avito_status="active",
        )
        self.assertEqual(out["reason"], "superseded_lifecycle_preserved")
        self.assertEqual(row.status, "superseded")
        self.assertTrue(row.identity_status.startswith("superseded"))
        payload = json.loads(row.payload_json)
        self.assertEqual(
            payload["identity"]["last_autoload_observation"]["lifecycle_preserved"],
            "superseded",
        )
        self.assertEqual(db.flush_count, 1)

    def test_provider_rejection_records_causal_upload(self):
        row = _row()
        db = _DB([[row], []])
        bind_from_autoload_evidence(
            db, "acc", "ci:test", "8270000010",
            upload_id=99, report_section="error_rejected", avito_status="rejected",
        )
        self.assertEqual(row.status, "draft")
        self.assertEqual(row.identity_status, "publication_rejected_bound")
        payload=json.loads(row.payload_json)
        self.assertEqual(payload["identity"]["publication_state"],"rejected")
        self.assertEqual(payload["identity"]["publication_rejection_upload_id"],99)


if __name__ == "__main__":
    unittest.main()
