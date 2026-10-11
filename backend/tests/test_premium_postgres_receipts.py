"""Opt-in real PostgreSQL receipt tests in a verified disposable socket-only cluster.

No environment: all tests SKIP. With BORIS_QA_POSTGRES_URL, additionally require
BORIS_QA_CLUSTER_ROOT (absolute /tmp/... directory). Only an isolated
generated schema is created/dropped. No public fallback, paid API, or app startup.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_URL = os.environ.get("BORIS_QA_POSTGRES_URL", "")
CHECKS = ("semantic_match", "headline_ok", "exact_text_ok", "spelling_ok", "forbidden_claims_ok",
    "composition_ok", "crop_safe_ok", "professional_ok", "mobile_ok", "selling_typography_ok",
    "palette_ok", "reference_style_match", "editorial_richness_ok", "secondary_copy_density_ok")


@unittest.skipUnless(POSTGRES_URL, "BORIS_QA_POSTGRES_URL absent: isolated PostgreSQL tests are opt-in")
class RealPostgresReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine, event, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        from PIL import Image
        cls.text, cls.Image = staticmethod(text), Image
        configured_root = os.environ.get("BORIS_QA_CLUSTER_ROOT", "")
        if not configured_root:
            raise RuntimeError("BORIS_QA_CLUSTER_ROOT required; refusing connection")
        cluster_root = Path(configured_root).resolve()
        if cluster_root == Path("/tmp") or Path("/tmp") not in cluster_root.parents or not cluster_root.is_dir():
            raise RuntimeError("Disposable cluster must have its own existing /tmp directory")
        url = make_url(POSTGRES_URL)
        socket = url.query.get("host") or url.host or ""
        if not url.get_backend_name().startswith("postgresql") or not str(socket).startswith("/"):
            raise RuntimeError("Only an explicit Unix socket PostgreSQL URL is permitted")
        if not Path(socket).resolve().is_relative_to(cluster_root):
            raise RuntimeError("Socket must be inside the declared disposable cluster root")
        cls.admin = create_engine(url, poolclass=NullPool)
        cls.addClassCleanup(cls.admin.dispose)
        with cls.admin.connect() as connection:
            data = Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve()
            listen = connection.exec_driver_sql("SHOW listen_addresses").scalar_one()
            sockets = connection.exec_driver_sql("SHOW unix_socket_directories").scalar_one()
            if not data.is_relative_to(cluster_root) or listen != "":
                raise RuntimeError("Server is not the declared isolated socket-only cluster")
            if any(not Path(p.strip()).resolve().is_relative_to(cluster_root) for p in sockets.split(",")):
                raise RuntimeError("Server exposes a socket outside the disposable cluster root")
            if connection.exec_driver_sql("SELECT inet_server_addr()").scalar_one() is not None:
                raise RuntimeError("TCP connection is forbidden")
            if any(connection.exec_driver_sql("SELECT to_regclass('public." + table + "')").scalar_one()
                   for table in ("background_jobs", "storage", "api_usage")):
                raise RuntimeError("Public application tables present: refusing nonempty app cluster")
        cls.schema = "premium_receipts_qa_" + uuid.uuid4().hex
        with cls.admin.begin() as connection:
            connection.exec_driver_sql('CREATE SCHEMA "' + cls.schema + '"')
        cls.addClassCleanup(cls.drop_schema)
        cls.engine = create_engine(url, poolclass=NullPool)
        cls.addClassCleanup(cls.engine.dispose)
        @event.listens_for(cls.engine, "connect")
        def private_schema(dbapi_connection, connection_record):
            old = dbapi_connection.autocommit
            dbapi_connection.autocommit = True
            cursor = dbapi_connection.cursor()
            cursor.execute('SET search_path TO "' + cls.schema + '"')
            cursor.close()
            dbapi_connection.autocommit = old
        with cls.engine.begin() as connection:
            if connection.exec_driver_sql("SHOW search_path").scalar_one().strip('"') != cls.schema:
                raise RuntimeError("Private-only search_path was not applied")
            connection.exec_driver_sql("CREATE TABLE storage (id BIGSERIAL PRIMARY KEY, account_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL)")
            connection.exec_driver_sql("""CREATE TABLE api_usage (
                id BIGSERIAL PRIMARY KEY, account_id TEXT NOT NULL, provider TEXT, model TEXT,
                operation TEXT, prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
                images INTEGER DEFAULT 0, cost_rub NUMERIC(14,4), created_at TIMESTAMPTZ DEFAULT now(),
                request_id TEXT UNIQUE, rate_source TEXT, pricing_version TEXT, provenance_json JSONB DEFAULT '{}'::jsonb
            )""")
        cls.session_factory = sessionmaker(bind=cls.engine)
        # Import the actual candidate without importing production app configuration.
        stubs = {}
        for name in ("app", "app.db", "app.db.session", "app.services", "app.services.image_service",
            "app.services.media_semantic_validator", "app.services.job_execution_clock"):
            stubs[name] = types.ModuleType(name)
            stubs[name].__path__ = []
        stubs["app.db.session"].SessionLocal = cls.session_factory
        stubs["app.services.image_service"].estimate_kopeks = lambda *a, **k: 100
        stubs["app.services.media_semantic_validator"]._vision_advisory_engine = lambda: cls.engine
        stubs["app.services.media_semantic_validator"]._vision_estimate_kopeks = lambda: 5
        stubs["app.services.job_execution_clock"].provider_call = lambda fn, *a, **k: fn(*a, **k)
        module_patch = patch.dict(sys.modules, stubs)
        module_patch.start()
        cls.addClassCleanup(module_patch.stop)
        cls.semantic = stubs["app.services.media_semantic_validator"]
        source = Path(os.environ.get("BORIS_QA_PREMIUM_SOURCE", ROOT / "app/services/banner_full_ai_premium.py"))
        spec = importlib.util.spec_from_file_location("premium_real_postgres_candidate", source)
        cls.premium = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.premium)
    @classmethod
    def drop_schema(cls):
        with cls.admin.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + cls.schema + '" CASCADE')
    def setUp(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("TRUNCATE storage, api_usage RESTART IDENTITY")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.images = Path(self.tmp.name) / "images"
        self.calls = 0
        self.cost = 0.1
        self.rate = "table"
        for obj, key, value in ((self.premium, "IMAGES_ROOT", self.images),
            (self.premium, "CIRCUIT_PATH", Path(self.tmp.name) / "circuit.json"),
            (self.premium, "LOCK_ROOT", Path(self.tmp.name) / "locks"),
            (self.premium, "_generate_image_core", lambda **kw: (_ for _ in ()).throw(AssertionError("Image provider forbidden"))),
            (self.semantic, "_vision_chat_json", self.provider)):
            handle = patch.object(obj, key, value, create=True)
            handle.start()
            self.addCleanup(handle.stop)
    def usage(self, rid, operation, cost, provenance, account="acct", rate="table", pricing="qa-fixture-v1"):
        with self.engine.begin() as connection:
            connection.execute(self.text("""INSERT INTO api_usage
                (account_id,provider,model,operation,cost_rub,request_id,rate_source,pricing_version,provenance_json,prompt_tokens,completion_tokens)
                VALUES (:a,'openai','gpt-4o-mini',:o,:c,:r,:rate,:pricing,CAST(:p AS jsonb),100,20)"""),
                {"a": account, "o": operation, "c": cost, "r": rid, "rate": rate, "pricing": pricing,
                 "p": json.dumps(provenance)})
    def make_row(self, banner, account="acct", item=None, budget=None, cost=1.0):
        item = item or banner
        path = self.images / account / (banner + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.Image.frombytes("RGB", (64, 64), random.Random(account + banner).randbytes(64*64*3)).save(path)
        digest = self.premium._artifact_sha256(path)
        provenance = {"campaign_id": 600, "campaign_item_id": item, "budget_intent_key": budget or "job:900:item:" + str(item),
            "intent_key": "image:" + banner, "banner_id": banner, "generation_version": 1}
        provenance.update(self.premium._budget_provenance(provenance, banner))
        self.usage("image:" + account + ":" + banner, "banner_premium_ai", cost, provenance, account)
        row = {"status": "ok", "account_id": account, "banner_id": banner, "version": 1,
            "artifact_sha256": digest, "absolute_path": str(path), "url": "/images/" + account + "/" + banner + ".png",
            "technical_qa": {"passed": True, "artifact_sha256": digest}, "qa_contract": {"headline": "Точный текст"},
            "provenance": provenance, "request_id": "image:" + account + ":" + banner,
            "provider_request_id": "image:" + account + ":" + banner, "image_outcome": "succeeded",
            "vision_qa_deferred": True, "vision_qa": {"qa_status": "PENDING", "qa_cost_status": "not_requested",
                "qa_cost_known": True, "qa_cost_rub": 0.0, "qa_terminal": False},
            "preview_status": "VISION_QA_PENDING", "qa_passed": False, "approved": False, "rejected": False,
            "actual_cost_rub": cost, "cost_reconciled": True, "cost_sla_pass": cost <= 4}
        self.premium._save_versions(account, banner, [row])
        return row
    def provider(self, account, operation, content, **kwargs):
        self.calls += 1
        rid = "qa:" + str(self.calls)
        results = []
        for part in content:
            if part.get("type") == "text" and part.get("text", "").startswith("SLOT "):
                raw = part["text"]
                slot = int(raw.split(";", 1)[0].split()[1])
                identity = json.JSONDecoder().raw_decode(raw.split("IDENTITY=", 1)[1])[0]
                results.append({**identity, "slot": slot, "pass": True, **dict.fromkeys(CHECKS, True), "issues": []})
        self.usage(rid, operation, self.cost, {**kwargs["provenance"], "intent_key": "vision:" + rid}, account, rate=self.rate)
        return {"results": results}, {"request_id": rid, "provider_request_id": rid, "cache_hit": False,
            "provider_calls": 1, "intent_key": "vision:" + rid}
    def finalize(self, *banners, account="acct"):
        return self.premium.finalize_preview_vision_batch(account_id=account,
            entries=[{"banner_id": b, "version": 1} for b in banners], provenance={"campaign_id": 600})
    def test_real_storage_insert_update_and_account_scope(self):
        self.premium._storage_json("acct", "receipt-key", {"value": 1})
        self.premium._storage_json("other", "receipt-key", {"value": 2})
        self.premium._storage_json("acct", "receipt-key", {"value": 3})
        self.assertEqual({"value": 3}, self.premium._storage_json("acct", "receipt-key"))
        self.assertEqual({"value": 2}, self.premium._storage_json("other", "receipt-key"))
        with self.engine.connect() as connection:
            self.assertEqual(2, connection.exec_driver_sql("SELECT count(*) FROM storage").scalar_one())
    def test_real_exact_receipt_rejects_missing_foreign_wrong_operation_and_unknown_metadata(self):
        self.make_row("A")
        exact = self.premium._exact_usage_receipt
        self.assertTrue(exact("acct", "banner_premium_ai", "image:acct:A")["cost_known"])
        for account, operation, rid in (("other", "banner_premium_ai", "image:acct:A"),
            ("acct", "banner_premium_ai", "missing"), ("acct", self.premium.VISION_QA_OPERATION, "image:acct:A")):
            self.assertFalse(exact(account, operation, rid)["cost_known"])
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE api_usage SET rate_source='unknown'")
        self.assertIsNone(exact("acct", "banner_premium_ai", "image:acct:A")["cost_rub"])
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE api_usage SET rate_source='table',pricing_version=''")
        self.assertFalse(exact("acct", "banner_premium_ai", "image:acct:A")["cost_known"])
    def test_real_fractional_batch_allocation_and_receipts_persist_across_new_sessions(self):
        for banner in "ABCD":
            self.make_row(banner)
        self.cost = 0.0003
        outcome = self.finalize(*"ABCD")
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual([0.0001, 0.0001, 0.0001, 0.0],
            [outcome["previews"][b + ":v1"]["qa_cost_rub"] for b in "ABCD"])
        for banner in "ABCD":
            loaded = self.premium._load_versions("acct", banner)[0]
            self.assertEqual("PASS", loaded["vision_qa"]["qa_status"])
            self.assertTrue(self.premium.read_current_preview("acct", banner, 1)["qa_passed"])
        self.assertTrue(self.finalize(*"ABCD")["ok"])
        self.assertEqual(1, self.calls)
        with self.engine.connect() as connection:
            self.assertEqual(1, connection.execute(self.text("SELECT count(*) FROM api_usage WHERE operation LIKE :pattern"),
                {"pattern": "%vision%"}).scalar_one())
    def test_real_cost_pending_terminal_pass_reconciles_without_paid_repeat_or_read_mutation(self):
        self.make_row("A")
        self.rate = "unknown"
        first = self.finalize("A")["previews"]["A:v1"]
        self.assertTrue(first["qa_terminal"])
        self.assertFalse(first["cost_reconciled"])
        with self.engine.begin() as connection:
            connection.execute(self.text("UPDATE api_usage SET rate_source='table' WHERE operation LIKE :pattern"),
                {"pattern": "%vision%"})
        before = self.premium._load_versions("acct", "A")
        current = self.premium.read_current_preview("acct", "A", 1)
        self.assertTrue(current["qa_passed"], current)
        self.assertEqual(before, self.premium._load_versions("acct", "A"))
        final = self.finalize("A")
        self.assertTrue(final["ok"], final)
        self.assertEqual(1, self.calls)
    def test_real_batch_and_asset_scopes_do_not_cross_accounts(self):
        self.make_row("A", account="acct")
        self.make_row("A", account="other")
        self.assertTrue(self.finalize("A", account="acct")["ok"])
        self.assertFalse(self.premium.read_current_preview("other", "A", 1)["qa_passed"])
        self.assertTrue(self.finalize("A", account="other")["ok"])
        self.assertEqual(2, self.calls)
        self.assertTrue(self.premium.read_current_preview("acct", "A", 1)["qa_passed"])
    def test_real_budget_sums_different_banner_ids_and_detects_duplicate_paid_intents(self):
        row_a = self.make_row("A", item="one", budget="same-logical-order", cost=1.1)
        self.make_row("B", item="one", budget="same-logical-order", cost=1.2)
        scope = self.premium._budget_scope_cost("acct", row_a["provenance"], "A")
        self.assertEqual(2.3, scope["total_cost_rub"])
        self.usage("duplicate-image", "banner_premium_ai", 1.1, row_a["provenance"])
        scope = self.premium._budget_scope_cost("acct", row_a["provenance"], "A")
        self.assertEqual(3.4, scope["total_cost_rub"])
        self.assertEqual(1, scope["duplicate_paid_calls"])
        self.assertFalse(self.finalize("A")["ok"])
        self.assertEqual(0, self.calls)
    def test_real_missing_allocation_cannot_certify_zero_batch_share(self):
        self.make_row("A")
        self.make_row("B")
        self.finalize("A", "B")
        with self.engine.begin() as connection:
            connection.execute(self.text("DELETE FROM storage WHERE key LIKE :pattern"),
                {"pattern": "premium_banner_qa_allocation:%"})
        current = self.premium.read_current_preview("acct", "A", 1)
        self.assertFalse(current["cost_reconciled"])
        self.assertFalse(current["qa_passed"])
        self.assertEqual(1, self.calls)
    def test_real_known_qa_cost_with_missing_or_wrong_budget_scope_is_not_pass(self):
        self.make_row("prior", item="same", budget="same-order", cost=2.8)
        self.make_row("A", item="same", budget="same-order", cost=1.0)
        self.rate, self.cost = "unknown", 0.3
        self.finalize("A")
        for wrong_scope in (None, "foreign-budget-hash"):
            with self.subTest(wrong_scope=wrong_scope):
                with self.engine.begin() as connection:
                    connection.execute(self.text("""UPDATE api_usage
                        SET rate_source='table', provenance_json =
                            (provenance_json - 'budget_intent_hashes') || CAST(:scope AS jsonb)
                        WHERE operation LIKE :pattern"""),
                        {"scope": json.dumps({"budget_intent_hashes": wrong_scope} if wrong_scope else {}),
                         "pattern": "%vision%"})
                before = self.premium._load_versions("acct", "A")
                current = self.premium.read_current_preview("acct", "A", 1)
                self.assertFalse(current["qa_passed"])
                self.assertFalse(current["cost_reconciled"])
                self.assertEqual("budget_journal_incomplete", current["budget"]["reason"])
                self.assertEqual(before, self.premium._load_versions("acct", "A"))
        self.assertEqual(1, self.calls)
    def test_real_persisted_receipt_recomputes_image_bytes(self):
        row = self.make_row("A")
        self.finalize("A")
        self.Image.new("RGB", (64, 64), "red").save(row["absolute_path"])
        current = self.premium.read_current_preview("acct", "A", 1)
        self.assertFalse(current["artifact_current"])
        self.assertFalse(current["qa_passed"])
        self.assertEqual(1, self.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
