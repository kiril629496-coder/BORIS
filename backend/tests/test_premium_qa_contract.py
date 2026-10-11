"""Offline contract tests: no server, paid provider, database or Avito access."""
import copy
import importlib.util
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

def load_candidate():
    modules = {}
    names = (
        "sqlalchemy", "app", "app.db", "app.db.session", "app.services",
        "app.services.image_service", "app.services.media_semantic_validator",
        "app.services.job_execution_clock", "app.services.banner_media",
        "app.services.campaign_owner_banner_examples",
    )
    for name in names:
        module = types.ModuleType(name)
        module.__path__ = []
        modules[name] = module
    modules["sqlalchemy"].text = lambda value: value
    modules["app.db.session"].SessionLocal = lambda: (_ for _ in ()).throw(AssertionError("real DB forbidden"))
    modules["app.services.image_service"].estimate_kopeks = lambda *a, **k: 100
    modules["app.services.media_semantic_validator"]._vision_estimate_kopeks = lambda: 5
    modules["app.services.media_semantic_validator"]._vision_chat_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("real provider forbidden"))
    modules["app.services.job_execution_clock"].provider_call = lambda fn, *a, **kw: fn(*a, **kw)
    modules["app.services.banner_media"].register_banner = lambda **kw: {"id": 100, **kw}
    modules["app.services.campaign_owner_banner_examples"].CONTRACT_VERSION = "test_stub"
    modules["app.services.campaign_owner_banner_examples"].DEFAULT_ACCENT = "#F97316"
    modules["app.services.campaign_owner_banner_examples"].PROFILES = {}
    modules["app.services.campaign_owner_banner_examples"].combine_reference_style = lambda variant, existing: existing
    modules["app.services.campaign_owner_banner_examples"].owner_variant = lambda *a, **k: None

    # Keep the provider/DB child stubs alive for deferred imports performed
    # by the Premium candidate during tests, but restore the parent packages and
    # owner-example module so neighboring tests import the real implementation
    # instead of this fixture. This removes pytest collection-order coupling
    # without allowing any real DB/provider access from this offline suite.
    restore_names = ("app", "app.services", "app.services.campaign_owner_banner_examples")
    previous = {name: sys.modules.get(name) for name in restore_names}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("premium_qa_candidate", ROOT / "app/services/banner_full_ai_premium.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, prior in previous.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
    return module, modules["app.services.media_semantic_validator"]

premium, semantic = load_candidate()
CHECKS = ("semantic_match", "headline_ok", "exact_text_ok", "spelling_ok", "forbidden_claims_ok", "composition_ok", "crop_safe_ok", "professional_ok", "mobile_ok", "selling_typography_ok", "palette_ok", "reference_style_match", "editorial_richness_ok", "secondary_copy_density_ok")

class Result:
    def __init__(self, rows):
        self.rows = rows
    def mappings(self):
        return self
    def all(self):
        return copy.deepcopy(self.rows)
    def first(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None

class FakeDB:
    def __init__(self, fixture):
        self.f = fixture
    def execute(self, sql, params):
        if "FROM api_usage" not in sql:
            raise AssertionError("Unexpected SQL outside read-only journal mock: " + sql)
        rows = [x for x in self.f.ledger if x["account_id"] == params["a"]]
        if "AND request_id=:r" in sql:
            rows = [x for x in rows if x["operation"] == params["o"] and x["request_id"] == params["r"]]
        else:
            def belongs(row):
                prov = row["provenance_json"]
                return (prov.get("budget_intent_hash") == params["s"]
                    or params["s"] in str(prov.get("budget_intent_hashes") or "").split(",")
                    or params["legacy"] and str(prov.get("campaign_id") or "") == params["c"] and
                    (str(prov.get("campaign_item_id") or prov.get("item_id") or "") == params["i"] or row["operation"] == params["bq"]))
            rows = [x for x in rows if x["operation"] in (params["im"], params["sq"], params["bq"]) and belongs(x)]
        return Result(rows)
    def close(self):
        pass

class LockConnection:
    def __init__(self, fixture):
        self.f = fixture
    def execution_options(self, **kw):
        if kw != {"isolation_level": "AUTOCOMMIT"}:
            raise AssertionError("Paid QA lock must not hold a transaction")
        return self
    def execute(self, sql, params):
        with self.f.lock_registry_guard:
            lock = self.f.locks.setdefault(params["k"], threading.RLock())
        if "unlock" in sql:
            lock.release()
        else:
            lock.acquire()
    def close(self):
        pass

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.images = Path(self.tmp.name) / "images"
        self.rows, self.storage, self.ledger = {}, {}, []
        self.calls, self.provider_items, self.writes = 0, [], 0
        self.locks = {}
        self.lock_registry_guard = threading.Lock()
        self.provider_guard = threading.Lock()
        self.mutate_response = None
        self.after_response = None
        self.provider_error = None
        self.batch_cost = 0.1
        self.batch_rate = "table"
        self.provider_entered = None
        self.provider_release = None
        replacements = {
            "IMAGES_ROOT": self.images, "LOCK_ROOT": Path(self.tmp.name) / "locks",
            "CIRCUIT_PATH": Path(self.tmp.name) / "circuit.json",
            "SessionLocal": lambda: FakeDB(self),
            "_load_versions": lambda account, banner: copy.deepcopy(self.rows.get((account, banner), [])),
            "_save_versions": self.save_versions, "_storage_json": self.storage_json,
            "_generate_image_core": lambda **kw: (_ for _ in ()).throw(AssertionError("paid image call forbidden")),
            "_trip_cost_circuit": lambda **kw: {"tripped": True, **kw},
        }
        for key, value in replacements.items():
            p = patch.object(premium, key, value)
            p.start()
            self.addCleanup(p.stop)
        for key, value in {"_vision_chat_json": self.provider,
                           "_vision_advisory_engine": lambda: types.SimpleNamespace(connect=lambda: LockConnection(self))}.items():
            p = patch.object(semantic, key, value, create=True)
            p.start()
            self.addCleanup(p.stop)
    def save_versions(self, account, banner, versions):
        self.writes += 1
        self.rows[(account, banner)] = copy.deepcopy(versions)
    def storage_json(self, account, key, value=None):
        if value is not None:
            self.writes += 1
            self.storage[(account, key)] = copy.deepcopy(value)
        return copy.deepcopy(self.storage.get((account, key)))
    def add_usage(self, rid, operation, cost, prov, account="acct", rate="table", pricing="prices-fixture-v1"):
        row = {"account_id": account, "request_id": rid, "provider": "openai", "model": "gpt-4o-mini",
               "operation": operation, "cost_rub": cost, "rate_source": rate, "pricing_version": pricing,
               "provenance_json": copy.deepcopy(prov), "prompt_tokens": 20, "completion_tokens": 10}
        self.ledger.append(row)
        return row
    def make_row(self, banner, item=None, account="acct", cost=1.0, budget_key=None):
        item = item or banner
        folder = self.images / account
        folder.mkdir(parents=True, exist_ok=True)
        image = folder / (banner + ".png")
        rng = random.Random(banner)
        Image.frombytes("RGB", (64, 64), rng.randbytes(64*64*3)).save(image)
        digest = premium._artifact_sha256(image)
        prov = {"campaign_id": 600, "campaign_item_id": item, "item_id": item,
                "budget_intent_key": budget_key or "campaign_banners:900:item:" + str(item)}
        prov.update(premium._budget_provenance(prov, banner))
        prov.update({"intent_key": "image:"+banner, "banner_id": banner, "generation_version": 1})
        self.add_usage("image-"+banner, "banner_premium_ai", cost, prov, account)
        row = {"status": "ok", "account_id": account, "banner_id": banner, "version": 1,
            "absolute_path": str(image), "artifact_sha256": digest, "idempotency_key": "idem:"+banner,
            "technical_qa": {"passed": True, "artifact_sha256": digest}, "provenance": prov,
            "qa_contract": {"headline": "Точный текст", "composition_family": "left"},
            "vision_qa_deferred": True, "preview_status": "VISION_QA_PENDING", "qa_passed": False,
            "vision_qa": {"passed": False, "qa_status": "PENDING", "qa_terminal": False, "qa_cost_status": "not_requested"},
            "actual_cost_rub": cost, "cost_reconciled": True, "cost_sla_pass": cost <= 4,
            "request_id": "image-"+banner, "provider_request_id": "image-"+banner,
            "approved": False, "rejected": False, "image_outcome": "succeeded"}
        self.rows[(account, banner)] = [row]
        return row
    def provider(self, account, operation, content, **kwargs):
        with self.provider_guard:
            self.calls += 1
            rid = "qa-" + str(self.calls)
        if self.provider_error:
            raise self.provider_error
        identities = []
        for element in content:
            if element.get("type") == "text" and element.get("text", "").startswith("SLOT "):
                line = element["text"]
                slot = int(line.split(";", 1)[0].split()[1])
                identity = json.JSONDecoder().raw_decode(line.split("IDENTITY=", 1)[1])[0]
                identities.append((slot, identity))
        self.provider_items.extend(x[1]["banner_id"] for x in identities)
        if operation == premium.VISION_QA_OPERATION:
            identity = json.JSONDecoder().raw_decode(content[0]["text"].split("IDENTITY=", 1)[1])[0]
            raw = {**identity, "pass": True, **dict.fromkeys(CHECKS, True), "issues": []}
        else:
            results = [{**identity, "slot": slot, "pass": True, **dict.fromkeys(CHECKS, True), "issues": []}
                       for slot, identity in identities]
            raw = {"results": results}
        if self.mutate_response:
            self.mutate_response(raw)
        prov = {**kwargs["provenance"], "intent_key": "qa-intent:"+rid}
        self.add_usage(rid, operation, self.batch_cost, prov, account, rate=self.batch_rate)
        if self.provider_entered:
            self.provider_entered.set()
            if not self.provider_release.wait(3):
                raise AssertionError("test did not release provider")
        if self.after_response:
            self.after_response()
        return raw, {"request_id": rid, "provider_request_id": rid, "cache_hit": False,
                     "intent_key": prov["intent_key"], "latency_ms": 15, "usage": {"total_tokens": 30}}
    def finalize(self, *banners, account="acct"):
        return premium.finalize_preview_vision_batch(account_id=account,
            entries=[{"banner_id": x, "version": 1} for x in banners], provenance={"campaign_id": 600})
    def fake_image_core(self, **kwargs):
        image = Path(kwargs["save_path"])
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.frombytes("RGB", (64, 64), random.Random(kwargs["prompt"]).randbytes(64*64*3)).save(image)
        rid = "new-image-" + str(len(self.ledger))
        self.add_usage(rid, kwargs["operation"], 1.0, kwargs["provenance"], kwargs["account_id"])
        return {"ok": True, "path": str(image), "request_id": rid, "provider_request_id": rid, "cost_rub": 1.0}

class StrictParserTests(Fixture):
    def test_non_boolean_truthy_values_are_not_pass(self):
        for value in ("false", "true", 1, 0, None, [], {}):
            with self.subTest(value=value):
                raw = {"pass": True, **dict.fromkeys(CHECKS, True), "issues": []}
                raw["exact_text_ok"] = value
                result = premium._normalize_vision_result(raw, {"cost_known": True, "cost_rub": 0.01})
                self.assertFalse(result["passed"])
                self.assertFalse(result["qa_terminal"])
                self.assertEqual("INVALID_RESPONSE", result["qa_status"])
    def test_real_false_is_terminal_reject(self):
        raw = {"pass": False, **dict.fromkeys(CHECKS, True), "issues": ["bad text"]}
        raw["exact_text_ok"] = False
        result = premium._normalize_vision_result(raw, {"cost_known": True, "cost_rub": 0})
        self.assertTrue(result["qa_terminal"])
        self.assertEqual("REJECT", result["qa_status"])
        self.assertEqual(0.0, result["qa_cost_rub"])
    def test_unknown_cost_is_none_not_zero(self):
        raw = {"pass": True, **dict.fromkeys(CHECKS, True), "issues": []}
        for value in (None, float("nan"), -1, True):
            out = premium._normalize_vision_result(raw, {"cost_known": True, "cost_rub": value})
            self.assertIsNone(out["qa_cost_rub"])
            self.assertFalse(out["qa_cost_known"])

class BatchReceiptTests(Fixture):
    def test_one_batch_sums_reserve_for_multiple_banners_in_the_same_budget_scope(self):
        self.make_row("A", item="same", budget_key="one-order", cost=1.9875)
        self.make_row("B", item="same", budget_key="one-order", cost=1.9875)
        self.batch_cost = 0.05
        result = self.finalize("A", "B")
        self.assertFalse(result["ok"])
        self.assertEqual(0, self.calls, "Both allocated QA shares must fit the same 4-ruble budget")
        self.assertTrue(all(row["retry_allowed"] is False for row in result["previews"].values()))
    def test_groups_1_2_4_5_8(self):
        for count in (1, 2, 4, 5, 8):
            with self.subTest(count=count):
                banners = [f"n{count}_{i}" for i in range(count)]
                for banner in banners:
                    self.make_row(banner)
                before = self.calls
                for start in range(0, count, 4):
                    out = self.finalize(*banners[start:start+4])
                    self.assertTrue(out["ok"], out)
                self.assertEqual((count+3)//4, self.calls-before)
    def test_reject_neighbor_does_not_repurchase_good_assets_or_itself(self):
        for x in "ABCD":
            self.make_row(x)
        self.mutate_response = lambda raw: raw["results"][2].update({"pass": False, "exact_text_ok": False, "issues": ["text mismatch"]})
        out = self.finalize(*"ABCD")
        self.assertFalse(out["ok"])
        self.assertEqual([], out["unresolved"])
        self.assertTrue(out["previews"]["C:v1"]["qa_terminal"])
        self.assertEqual("regenerate_quality", out["previews"]["C:v1"]["retry_action"])
        for x in "ABD":
            self.assertTrue(out["previews"][x+":v1"]["qa_passed"])
        self.finalize(*"ABCD")
        self.assertEqual(1, self.calls)
    def test_slot_identity_version_hash_and_duplicates_fail_closed_per_item(self):
        mutators = (
            lambda rs: rs[1].update(banner_id="alien"),
            lambda rs: rs[1].update(version=999),
            lambda rs: rs[1].update(version="1"),
            lambda rs: rs[1].update(asset_sha256="0"*64),
            lambda rs: rs[1].update(qa_version="old"),
            lambda rs: rs[1].update(contract_sha256="0"*64),
            lambda rs: rs.append(copy.deepcopy(rs[1])),
        )
        for idx, mutate in enumerate(mutators):
            with self.subTest(case=idx):
                names = [f"case{idx}_{x}" for x in "ABCD"]
                for name in names:
                    self.make_row(name)
                self.mutate_response = lambda raw, f=mutate: f(raw["results"])
                before = self.calls
                out = self.finalize(*names)
                self.assertFalse(out["previews"][names[1]+":v1"]["qa_passed"])
                self.assertFalse(out["previews"][names[1]+":v1"]["qa_terminal"])
                for pos in (0, 2, 3):
                    self.assertTrue(out["previews"][names[pos]+":v1"]["qa_passed"])
                self.assertEqual(0, out["recovery_calls"])
                self.finalize(*names)
                self.assertEqual(1, self.calls-before)
    def test_duplicate_input_never_pays(self):
        self.make_row("A")
        out = self.finalize("A", "A")
        self.assertEqual(0, self.calls)
        self.assertFalse(out["ok"])
    def test_missing_slot_preserves_batch_charge_and_never_silent_single_retry(self):
        for x in "ABCD":
            self.make_row(x)
        self.mutate_response = lambda raw: raw["results"].pop(2)
        out = self.finalize(*"ABCD")
        self.assertIn("C:v1", out["unresolved"])
        self.assertEqual(0.025, out["previews"]["C:v1"]["qa_cost_rub"])
        self.assertEqual(1, self.calls)
        self.assertEqual(0, out["recovery_calls"])
    def test_unknown_cost_terminal_pass_reconciles_without_provider(self):
        for x in "AB":
            self.make_row(x)
        self.batch_rate = "unknown"
        first = self.finalize("A", "B")
        self.assertTrue(first["previews"]["A:v1"]["qa_terminal"])
        self.assertFalse(first["previews"]["A:v1"]["cost_reconciled"])
        self.assertEqual("COST_RECONCILIATION_REQUIRED", first["previews"]["A:v1"]["preview_status"])
        self.ledger[-1]["rate_source"] = "table"
        second = self.finalize("A", "B")
        self.assertTrue(second["ok"], second)
        self.assertEqual(1, self.calls)
    def test_provider_unknown_not_a_quality_reject_or_new_image_retry(self):
        self.make_row("A")
        self.provider_error = TimeoutError("lost transport")
        first = self.finalize("A")
        self.assertFalse(first["previews"]["A:v1"]["qa_terminal"])
        self.assertFalse(first["previews"]["A:v1"]["retry_allowed"])
        self.finalize("A")
        self.assertEqual(1, self.calls)
    def test_trusted_unpaid_failure_retries_qa_only_and_does_not_lose_good_neighbor(self):
        self.make_row("A")
        self.make_row("B")
        self.finalize("A")
        error = RuntimeError("VISION_BURST_CAP:20/20")
        error.vision_meta = {"provider_cost_state": "not_requested", "cost_known": True,
            "cost_rub": 0.0, "retryable": True, "provider_calls": 0, "provider_call_count_known": True}
        self.provider_error = error
        first = self.finalize("A", "B")
        failed = first["previews"]["B:v1"]
        self.assertTrue(failed["cost_reconciled"])
        self.assertEqual(0.0, failed["qa_cost_rub"])
        self.assertFalse(failed["qa_terminal"])
        self.assertTrue(failed["retry_allowed"])
        self.assertEqual("retry_qa", failed["retry_action"])
        self.assertEqual(0, first["provider_calls"])
        self.provider_error = None
        final = self.finalize("A", "B")
        self.assertTrue(final["ok"], final)
        self.assertEqual(["A", "B"], self.provider_items)
        self.assertEqual(2, len([r for r in self.ledger if r["operation"] != "banner_premium_ai"]))
    def test_text_error_without_nocharge_metadata_cannot_authorize_zero_or_retry(self):
        self.make_row("A")
        self.provider_error = RuntimeError("VISION_BURST_CAP:untrusted wrapper text")
        out = self.finalize("A")["previews"]["A:v1"]
        self.assertFalse(out["cost_reconciled"])
        self.assertIsNone(out["qa_cost_rub"])
        self.assertFalse(out["retry_allowed"])
    def test_fractional_batch_allocation_conserves_charge_including_proved_zero(self):
        for x in "ABCD":
            self.make_row(x)
        self.batch_cost = 0.0003
        out = self.finalize(*"ABCD")
        shares = [out["previews"][x+":v1"]["qa_cost_rub"] for x in "ABCD"]
        self.assertEqual([0.0001, 0.0001, 0.0001, 0.0], shares)
        self.assertEqual(3, sum(int(premium._money(x)*10000) for x in shares))
        self.assertTrue(out["ok"])

class IntegrityAndBudgetTests(Fixture):
    def test_direct_unknown_image_cost_reconciles_then_qa_uses_same_artifact(self):
        def unknown_cost_core(**kwargs):
            result = self.fake_image_core(**kwargs)
            self.ledger[-1]["rate_source"] = "unknown"
            return result
        with patch.object(premium, "_generate_image_core", unknown_cost_core):
            row = premium.create_preview(account_id="acct", description="Service", idempotency_key="cost-pending", defer_vision_qa=False)
        self.assertFalse(row["cost_reconciled"])
        self.assertEqual(0, self.calls)
        self.ledger[-1]["rate_source"] = "table"
        final = self.finalize(row["banner_id"])
        self.assertTrue(final["ok"], final)
        self.assertEqual(1, len([r for r in self.ledger if r["operation"] == "banner_premium_ai"]))
    def test_actual_image_cost_surprise_blocks_next_qa_before_payment(self):
        key = "same-correcting-order"
        self.make_row("prior", item="item", cost=2.0, budget_key=key)
        def higher_actual_core(**kwargs):
            result = self.fake_image_core(**kwargs)
            self.ledger[-1]["cost_rub"] = 2.0
            return result
        with patch.object(premium, "_generate_image_core", higher_actual_core):
            row = premium.create_preview(account_id="acct", description="Service", idempotency_key="cost-surprise",
                provenance={"campaign_id": 600, "campaign_item_id": "item", "budget_intent_key": key})
        self.assertEqual(4.0, row["total_cost_rub"])
        self.assertEqual(0, self.calls)
        self.assertFalse(self.finalize(row["banner_id"])["ok"])
        self.assertEqual(0, self.calls)
    def test_open_circuit_allows_free_replay_and_reconciliation_but_no_new_qa(self):
        self.make_row("A")
        self.make_row("B")
        self.finalize("A")
        with patch.object(premium, "_paid_preset_guard", return_value={"allowed": False, "circuit_ok": False, "estimated_cost_rub": 1.0}):
            replay = premium.create_preview(account_id="acct", description="same", idempotency_key="idem:A", parent_banner_id="A")
            self.assertTrue(replay["idempotency_replay"])
            self.assertTrue(self.finalize("A")["ok"])
            blocked = self.finalize("B")["previews"]["B:v1"]
            self.assertEqual("COST_CIRCUIT_OPEN", blocked["code"])
            self.assertFalse(blocked["retry_allowed"])
        self.assertEqual(1, self.calls)
    def test_manual_reject_cannot_regain_ready_through_receipt_read(self):
        self.make_row("A")
        self.finalize("A")
        premium.reject_preview("acct", "A", 1, reason="Do not use")
        self.assertFalse(premium.read_current_preview("acct", "A", 1)["qa_passed"])
    def test_deferred_constructor_proves_zero_qa_not_requested_and_reaches_batch(self):
        with patch.object(premium, "_generate_image_core", self.fake_image_core):
            row = premium.create_preview(account_id="acct", description="Service description", exact_title="Точный текст",
                idempotency_key="fresh-deferred", defer_vision_qa=True,
                provenance={"campaign_id": 600, "campaign_item_id": 99, "budget_intent_key": "job:99"})
        self.assertEqual("VISION_QA_PENDING", row["preview_status"])
        self.assertTrue(row["cost_reconciled"])
        self.assertTrue(row["cost_sla_pass"])
        self.assertEqual("not_requested", row["vision_qa"]["qa_cost_status"])
        self.assertEqual(0.0, row["vision_qa"]["qa_cost_rub"])
        self.assertEqual(0, self.calls)
        out = self.finalize(row["banner_id"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(1, self.calls)
    def test_direct_constructor_saves_current_receipt_and_can_approve(self):
        with patch.object(premium, "_generate_image_core", self.fake_image_core):
            row = premium.create_preview(account_id="acct", description="Service", exact_title="Точный текст",
                idempotency_key="fresh-single", defer_vision_qa=False,
                provenance={"campaign_id": 600, "campaign_item_id": 98, "budget_intent_key": "job:98"})
        self.assertTrue(row["qa_passed"], row)
        self.assertEqual(1.1, row["total_cost_rub"])
        approved = premium.approve_preview("acct", row["banner_id"], 1)
        self.assertEqual("ok", approved["status"], approved)
        self.assertTrue(premium.read_current_preview("acct", row["banner_id"], 1)["qa_passed"])
        self.assertEqual(1, self.calls)
    def test_local_diversity_reject_before_qa_is_terminal_with_known_zero_cost(self):
        self.make_row("A")
        out = premium.reject_preview("acct", "A", 1, reason="BANNER_DUPLICATE:peer=B:distance=1", quality_rejection=True)
        self.assertTrue(out["qa_terminal"], out)
        self.assertTrue(out["cost_reconciled"])
        self.assertEqual(0.0, out["qa_cost_rub"])
        self.assertEqual("regenerate_quality", out["retry_action"])
        self.assertTrue(out["retry_allowed"])
        self.finalize("A")
        self.assertEqual(0, self.calls)
        self.assertFalse(premium.read_current_preview("acct", "A", 1)["qa_passed"])
    def test_local_diversity_reject_after_qa_retains_charge_and_original_proof(self):
        self.make_row("A")
        self.finalize("A")
        out = premium.reject_preview("acct", "A", 1, reason="BANNER_DUPLICATE:peer=B:distance=1", quality_rejection=True)
        self.assertEqual(0.1, out["qa_cost_rub"])
        self.assertEqual(1.1, out["total_cost_rub"])
        self.assertEqual("PASS", out["qa_history"][0]["verdict"]["qa_status"])
        self.assertEqual("REJECT", out["vision_qa"]["qa_status"])
        self.finalize("A")
        self.assertEqual(1, self.calls)
    def test_manual_rejection_does_not_authorize_paid_quality_retry(self):
        self.make_row("A")
        out = premium.reject_preview("acct", "A", 1, reason="BANNER_DUPLICATE:just user supplied text")
        self.assertFalse(out["retry_allowed"])
        self.assertEqual("blocked", out["retry_action"])
    def test_changed_bytes_block_replay_approval_and_do_not_regenerate(self):
        row = self.make_row("A")
        self.finalize("A")
        Image.frombytes("RGB", (64, 64), random.Random("changed").randbytes(64*64*3)).save(row["absolute_path"])
        result = premium.create_preview(account_id="acct", description="valid", idempotency_key="idem:A", parent_banner_id="A")
        self.assertEqual("PREMIUM_ARTIFACT_HASH_MISMATCH", result["code"])
        self.assertFalse(premium.read_current_preview("acct", "A", 1)["qa_passed"])
        self.assertEqual("PREMIUM_ARTIFACT_HASH_MISMATCH", premium.approve_preview("acct", "A", 1)["code"])
        self.assertEqual(1, self.calls)
    def test_changed_bytes_during_provider_call_do_not_receive_good_verdict(self):
        a = self.make_row("A")
        self.make_row("B")
        self.after_response = lambda: Image.frombytes("RGB", (64, 64), random.Random(77).randbytes(64*64*3)).save(a["absolute_path"])
        result = self.finalize("A", "B")
        self.assertFalse(result["previews"]["A:v1"]["qa_passed"])
        self.assertTrue(result["previews"]["B:v1"]["qa_passed"])
    def test_qa_version_and_contract_change_invalidate_without_paid_recheck(self):
        self.make_row("A")
        self.finalize("A")
        with patch.object(premium, "VISION_QA_VERSION", "next-version"):
            out = self.finalize("A")
            self.assertFalse(out["previews"]["A:v1"]["qa_passed"])
            self.assertEqual("QA_RECEIPT_UPGRADE_REQUIRED", out["previews"]["A:v1"]["preview_status"])
        self.rows[("acct", "A")][0]["qa_contract"]["headline"] = "Changed confirmed copy"
        self.assertFalse(premium.read_current_preview("acct", "A", 1)["qa_passed"])
        self.assertEqual(1, self.calls)
    def test_legacy_boolean_pass_is_not_promoted(self):
        row = self.make_row("A")
        row.update(vision_qa_deferred=False, vision_qa={"passed": True}, qa_passed=True, preview_status="DRAFT_PREVIEW")
        result = self.finalize("A")
        self.assertEqual(0, self.calls)
        self.assertFalse(result["previews"]["A:v1"]["qa_passed"])
    def test_cross_account_row_or_request_never_confirms_asset(self):
        row = self.make_row("A", account="other")
        self.rows[("acct", "A")] = [copy.deepcopy(row)]
        out = self.finalize("A")
        self.assertFalse(out["previews"]["A:v1"]["qa_passed"])
        self.assertEqual(0, self.calls)
        self.assertFalse(premium._exact_usage_receipt("acct", "banner_premium_ai", "image-A")["cost_known"])
    def test_cost_receipt_requires_exact_rate_and_pricing_version(self):
        row = self.make_row("A")
        receipt = self.ledger[0]
        for field, value in (("rate_source", "fallback"), ("pricing_version", ""), ("cost_rub", None), ("cost_rub", float("nan"))):
            with self.subTest(field=field, value=value):
                old = receipt[field]
                receipt[field] = value
                self.assertFalse(premium._exact_usage_receipt("acct", "banner_premium_ai", "image-A")["cost_known"])
                receipt[field] = old
    def test_new_budget_scope_does_not_charge_an_earlier_independent_order(self):
        self.make_row("A", item=1, budget_key="order:old:item:1", cost=3.9)
        fresh = {"campaign_id": 600, "campaign_item_id": 1, "budget_intent_key": "order:new:item:1"}
        costs = premium._budget_scope_cost("acct", fresh, "new")
        self.assertTrue(costs["known"])
        self.assertEqual(0, costs["total_cost_rub"])
    def test_quality_regeneration_budget_counts_all_banner_ids_and_blocks_before_paid_call(self):
        scope = "one-logical-order:item:1"
        self.make_row("A", item=1, budget_key=scope, cost=1.7)
        self.make_row("B", item=1, budget_key=scope, cost=1.6)
        prov = {"campaign_id": 600, "campaign_item_id": 1, "budget_intent_key": scope}
        cost = premium._budget_scope_cost("acct", prov, "new-candidate")
        self.assertEqual(3.3, cost["total_cost_rub"])
        result = premium.create_preview(account_id="acct", description="valid", idempotency_key="correction-2", provenance=prov)
        self.assertEqual("BANNER_COST_LIMIT", result["code"])
        self.assertFalse(result["retry_allowed"])
        self.assertEqual(0, self.calls)
    def test_duplicate_economic_intent_is_reported_and_blocks(self):
        row = self.make_row("A")
        self.add_usage("image-A-duplicate", "banner_premium_ai", 1.0, row["provenance"])
        costs = premium._budget_scope_cost("acct", row["provenance"], "A")
        self.assertEqual(1, costs["duplicate_paid_calls"])
        self.assertFalse(self.finalize("A")["ok"])
        self.assertEqual(0, self.calls)
    def test_read_current_preview_is_read_only_and_rechecks_current_cost(self):
        row = self.make_row("A")
        self.finalize("A")
        writes, calls = self.writes, self.calls
        self.assertTrue(premium.read_current_preview("acct", "A", 1)["qa_passed"])
        self.ledger[-1]["rate_source"] = "unknown"
        self.assertFalse(premium.read_current_preview("acct", "A", 1)["qa_passed"])
        self.assertEqual((writes, calls), (self.writes, self.calls))
    def test_read_current_rejects_known_qa_receipt_missing_budget_scope(self):
        self.make_row("A")
        self.batch_rate, self.batch_cost = "unknown", 3.1
        self.finalize("A")
        # Accounting repair resolved a real charge but failed to link it to the order.
        # Every individual receipt is known; excluding QA would hide a 4.1 RUB total.
        self.ledger[-1]["rate_source"] = "table"
        self.ledger[-1]["provenance_json"].pop("budget_intent_hashes")
        writes, calls = self.writes, self.calls
        current = premium.read_current_preview("acct", "A", 1)
        self.assertFalse(current["qa_passed"])
        self.assertFalse(current["cost_reconciled"])
        self.assertEqual("budget_journal_incomplete", current["budget"]["reason"])
        self.assertEqual((writes, calls), (self.writes, self.calls))
    def test_prior_correction_cost_cannot_mask_a_qa_receipt_missing_from_scope(self):
        self.make_row("prior", item="same", budget_key="same-order", cost=2.8)
        self.make_row("A", item="same", budget_key="same-order", cost=1.0)
        self.batch_rate, self.batch_cost = "unknown", 0.3
        self.finalize("A")
        self.ledger[-1]["rate_source"] = "table"
        self.ledger[-1]["provenance_json"].pop("budget_intent_hashes")
        # 3.8 scope subtotal is larger than this asset's 1 + 0.3 receipts,
        # but the actual logical order spent 4.1. Check request membership too.
        current = premium.read_current_preview("acct", "A", 1)
        self.assertFalse(current["qa_passed"])
        self.assertEqual("budget_journal_incomplete", current["budget"]["reason"])
        reconciled = self.finalize("A")["previews"]["A:v1"]
        self.assertFalse(reconciled["cost_reconciled"])
        self.assertEqual(1, self.calls)

class ConcurrentTests(Fixture):
    def test_two_quality_corrections_share_budget_lock_and_only_one_can_pay(self):
        key = "one-quality-correction-order"
        self.make_row("prior", item="item", cost=2.1, budget_key=key)
        entered, release = threading.Event(), threading.Event()
        paid, results, failures = [], [], []
        def image_core(**kwargs):
            paid.append(kwargs["save_path"])
            entered.set()
            if not release.wait(3):
                raise AssertionError("test did not release paid image")
            return self.fake_image_core(**kwargs)
        def run(idempotency):
            try:
                results.append(premium.create_preview(account_id="acct", description="Service",
                    idempotency_key=idempotency, defer_vision_qa=True,
                    provenance={"campaign_id": 600, "campaign_item_id": "item", "budget_intent_key": key}))
            except Exception as exc:
                failures.append(exc)
        with patch.object(premium, "_generate_image_core", image_core):
            first = threading.Thread(target=run, args=("correction-A",))
            second = threading.Thread(target=run, args=("correction-B",))
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            release.set()
            first.join(3)
            second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive(), "deadlock")
        self.assertEqual([], failures)
        self.assertEqual(1, len(paid))
        self.assertEqual({"ok", "blocked"}, {r["status"] for r in results})
        self.assertEqual(2, len([r for r in self.ledger if r["operation"] == "banner_premium_ai"]))
    def test_overlapping_batches_pay_for_shared_asset_once(self):
        for x in "ABC":
            self.make_row(x)
        self.provider_entered = threading.Event()
        self.provider_release = threading.Event()
        results, errors = [], []
        def run(names):
            try:
                results.append(self.finalize(*names))
            except Exception as exc:
                errors.append(exc)
        first = threading.Thread(target=run, args=("AB",))
        second = threading.Thread(target=run, args=("BC",))
        first.start()
        self.assertTrue(self.provider_entered.wait(2))
        second.start()
        self.provider_release.set()
        first.join(3)
        second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive(), "deadlock")
        self.assertEqual([], errors)
        self.assertEqual(2, self.calls)
        self.assertEqual(["A", "B", "C"], sorted(self.provider_items))
        self.assertTrue(all(x["ok"] for x in results), results)

if __name__ == "__main__":
    unittest.main(verbosity=2)


class MetadataIdentityFalseRejectTests(Fixture):
    def test_metadata_only_false_reject_rechecks_same_artifact_without_image_generation(self):
        row = self.make_row("META")
        original_sha = row["artifact_sha256"]
        self.mutate_response = lambda raw: raw["results"][0].update({
            "pass": False,
            "issues": ["Malformed ID in banner_id", "Malformed phone number in asset_sha256"],
        })
        first = self.finalize("META")
        self.assertFalse(first["previews"]["META:v1"]["qa_passed"])
        self.assertEqual("REJECT", first["previews"]["META:v1"]["vision_qa"]["qa_status"])
        self.assertEqual(1, self.calls)

        old_row = copy.deepcopy(self.rows[("acct", "META")][0])
        old_receipts = copy.deepcopy(old_row.get("qa_receipts") or {})
        self.assertEqual(1, len(old_receipts))
        old_contract_sha = premium._qa_identity("acct", old_row)["contract_sha256"]

        self.mutate_response = None
        second = self.finalize("META")
        self.assertTrue(second["previews"]["META:v1"]["qa_passed"], second)
        self.assertEqual(2, self.calls, "policy upgrade must buy QA only once")
        new_row = self.rows[("acct", "META")][0]
        self.assertEqual(original_sha, new_row["artifact_sha256"], "same PNG must be reused")
        self.assertEqual(premium.VISIBLE_QA_METADATA_POLICY, new_row["qa_contract"]["machine_identity_policy"])
        self.assertNotEqual(old_contract_sha, premium._qa_identity("acct", new_row)["contract_sha256"])
        self.assertEqual(2, len(new_row.get("qa_receipts") or {}), "old false REJECT remains auditable beside new receipt")
        self.assertTrue(new_row.get("qa_policy_upgrades"))

    def test_real_visual_reject_is_never_policy_upgraded_or_rechecked(self):
        self.make_row("REAL")
        self.mutate_response = lambda raw: raw["results"][0].update({
            "pass": False,
            "exact_text_ok": False,
            "issues": ["visible headline text mismatch"],
        })
        first = self.finalize("REAL")
        self.assertFalse(first["previews"]["REAL:v1"]["qa_passed"])
        self.assertEqual(1, self.calls)
        self.mutate_response = None
        second = self.finalize("REAL")
        self.assertFalse(second["previews"]["REAL:v1"]["qa_passed"])
        self.assertEqual(1, self.calls, "real visual rejection must stay terminal")
        row = self.rows[("acct", "REAL")][0]
        self.assertNotIn("machine_identity_policy", row["qa_contract"])
        self.assertFalse(row.get("qa_policy_upgrades"))

    def test_qa_prompts_explicitly_exclude_machine_identity_from_visible_copy(self):
        src = (ROOT / "app/services/banner_full_ai_premium.py").read_text(encoding="utf-8")
        assert "NOT visible banner copy" in src
        assert "Evaluate ONLY visible pixels against CONTRACT" in src
        assert "never mention them in issues" in src
