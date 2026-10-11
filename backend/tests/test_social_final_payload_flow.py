"""Actual generation/final-QA/delivery functions; all external IO is mocked.

No production modules are imported. Functions execute from the candidate AST;
pure SQ/editorial/owner policies load against isolated in-memory app modules.
Only provider, recent-history/feedback and database boundaries are replaced.
"""
from __future__ import annotations
import ast
from collections import deque
from contextvars import ContextVar
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SERVICES = BACKEND / "app/services"
PUBLIC_FIXTURE = BACKEND / "tests/fixtures/final_payload_cases.json"
PINNED_DEPENDENCIES = {
    "social_editorial_contract.py": "da752096d760f8778849a5c4cabd519001b91187501bc544f3abfc1bcb1abd2e",
    "social_owner_policy_v3.py": "bc51ee0577b7aca1ead949206e402497dc7362c7b2a5eab2f46cb8240e545a2e",
}
NOW = datetime(2026, 10, 9, 6, 1, tzinfo=timezone.utc)


class Clock(datetime):
    instant = NOW
    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz is not None else cls.instant.replace(tzinfo=None)


def no_io(*args, **kwargs):
    raise AssertionError("Real external IO is forbidden in this fixture")


class Column:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return self.name, value


class Storage:
    account_id, key = Column("account_id"), Column("key")
    def __init__(self, **kwargs): self.__dict__.update(kwargs)


class Query:
    def __init__(self, db): self.db, self.where, self.locked = db, {}, False
    def filter(self, *clauses): self.where.update(clauses); return self
    def with_for_update(self): self.locked = True; return self
    def first(self):
        if self.locked and not self.db.held:
            self.db.store.lock.acquire(); self.db.held = True
        return self.db.store.rows.get((self.where["account_id"], self.where["key"]))


class Session:
    def __init__(self, store): self.store, self.held, self.info = store, False, {}
    def query(self, model): return Query(self)
    def execute(self, *args, **kwargs): return SimpleNamespace(scalar=lambda: True)
    def add(self, row): self.store.rows[(row.account_id, row.key)] = row
    def commit(self): self.close()
    def rollback(self): self.close()
    def close(self):
        if self.held: self.held = False; self.store.lock.release()


class Store:
    def __init__(self): self.rows, self.lock = {}, threading.RLock()
    def session(self): return Session(self)
    def write(self, key, value): self.rows[("u2", key)] = Storage(account_id="u2", key=key, value=json.dumps(value, ensure_ascii=False))
    def read(self, key):
        row = self.rows.get(("u2", key))
        return json.loads(row.value) if row else None
    def load_posts(self, db, account):
        self.lock.acquire(); db.held = True
        return self.rows.get((account, "posting_posts")), self.read("posting_posts") or []
    def save_posts(self, db, account, row, posts): self.write("posting_posts", posts); db.commit()


class FinalPayloadFlowTests(unittest.TestCase):
    def load_module(self, name, source):
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        for name, expected in PINNED_DEPENDENCIES.items():
            self.assertEqual(hashlib.sha256((SERVICES / name).read_bytes()).hexdigest(), expected,
                             "staged dependency differs from the reviewed policy: " + name)
        self.fixtures = json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory(prefix="final-payload-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store()
        self.image_calls, self.qa_calls, self.text_calls = [], [], []
        self.qa_outputs = deque()
        self.qa_drop_keys = set()
        self.qa_meta = None
        self.qa_hook = None
        self.image_cost, self.qa_cost = 1.30, .40
        self.public_calls = []
        modules = {}
        for name in ("app", "app.db", "app.models", "app.services"):
            modules[name] = ModuleType(name); modules[name].__path__ = []
        for name in ("app.db.session", "app.models.storage", "app.services.exception_observability",
                     "app.services.media_semantic_validator", "app.services.image_service",
                     "app.services.social_image_costs", "app.services.social_entitlement", "sqlalchemy"):
            modules[name] = ModuleType(name)
        modules["app.db.session"].SessionLocal = self.store.session
        modules["app.models.storage"].Storage = Storage
        modules["app.services.exception_observability"].observe_suppressed = lambda *a, **k: None
        modules["app.services.media_semantic_validator"]._vision_estimate_kopeks = lambda: 100
        modules["app.services.media_semantic_validator"]._vision_chat_json = self.provider_qa
        images = modules["app.services.image_service"]
        images.PREMIUM_MODEL = "gpt-image-2"
        images.estimate_kopeks = lambda *a, **k: self.image_cost * 100
        images.generate_banner_financially_guarded = self.provider_image
        costs = modules["app.services.social_image_costs"]
        costs.image_provenance = lambda account, project, path, intent: {"account_id": account, "project_id": project, "intent": intent}
        costs.reconcile_image_cost = lambda **k: {"cost_rub": k["response"]["cost_rub"], "request_id": k["response"]["request_id"]}
        modules["app.services.social_entitlement"].normalize_posting_subscription = lambda sub: (sub, [])
        modules["sqlalchemy"].text = lambda value: value
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start(); self.addCleanup(self.module_patch.stop)
        self.ed = self.load_module("app.services.social_editorial_contract", SERVICES / "social_editorial_contract.py")
        self.owner = self.load_module("app.services.social_owner_policy_v3", SERVICES / "social_owner_policy_v3.py")
        self.assets = self.load_module("app.services.social_media_assets", SERVICES / "social_media_assets.py")
        self.sq = self.load_module("app.services.social_quality", SERVICES / "social_quality.py")
        modules["app.services"].social_quality = self.sq
        self.ed.datetime = self.sq.datetime = Clock
        self.sq.SessionLocal = no_io
        self.sq.recent_project_posts = lambda *a, **k: []
        self.sq._feedback_preferences = lambda *a, **k: {"likes": 0, "dislikes": {}, "recent": []}
        self.sq._social_vision_usage_receipt = lambda *a: None
        source = BACKEND / "posting_runner.py"
        tree = ast.parse(source.read_text())
        body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef): body.append(node)
            elif isinstance(node, ast.Assign):
                try: ast.literal_eval(node.value)
                except (TypeError, ValueError): continue
                body.append(node)
        ns = {"datetime": Clock, "date": date, "timedelta": timedelta, "timezone": timezone,
              "json": json, "os": os, "sys": sys, "re": re, "Storage": Storage,
              "SessionLocal": self.store.session, "observe_suppressed": lambda *a, **k: None,
              "project_local_now": self.ed.project_local_now, "parse_reference_date": self.ed.parse_reference_date,
              "planned_content_date": self.ed.planned_content_date, "_SOCIAL_EDITORIAL_DATE": ContextVar("fixture_date", default=None),
              "load_posting_posts_for_update": self.store.load_posts, "save_posting_posts_locked": self.store.save_posts}
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source), "exec"), ns)
        self.ns = ns
        self.original_project_occasion = ns["_project_occasion"]
        self.original_build_text_prompt = ns["_build_text_prompt"]
        ns.update({"BANNER_DIR": str(self.root), "_openai_text": self.provider_text,
                   "_is_nail_ctx": lambda *a: False, "_project_occasion": lambda *a: "",
                   "_choose_rotating_project_theme": lambda *a, **k: self.theme,
                   "_build_text_prompt": lambda *a, **k: "Fixture frozen text request",
                   "_send_tg": no_io, "_send_vk": no_io})
        self.raw_text = self.fixtures["posts"]["ocean"]["text"]
        self.theme = self.fixtures["posts"]["ocean"]["theme"]
        self.project = copy.deepcopy(self.fixtures["projects"]["a374ced25745"])
        self.project.update(mode="auto", image_source="ai", allow_hashtags=True,
                            paid_auto_generation=True, expected_auto_generation=True, themes=[self.theme])
        self.store.write("posting_projects", [self.project])

    def provider_text(self, *args, **kwargs):
        self.text_calls.append((args, kwargs)); return self.raw_text

    def provider_image(self, db, account, prompt, path, **kwargs):
        from PIL import Image
        self.image_calls.append({"account": account, "prompt": prompt, "path": path, **kwargs})
        Image.new("RGB", (512, 512), (30 + len(self.image_calls), 50, 80)).save(path, compress_level=0)
        return {"ok": True, "cost_rub": self.image_cost, "request_id": "qa_fixture_image_" + str(len(self.image_calls))}

    def provider_qa(self, account, operation, content, **kwargs):
        self.qa_calls.append({"account": account, "operation": operation, "content": copy.deepcopy(content), **kwargs})
        flags = {k: True for k in ("pass", *self.sq.SOCIAL_VISION_REQUIRED_FLAGS, *self.sq.NATIVE_PREMIUM_REQUIRED_FLAGS)}
        flags["issues"] = []
        if self.qa_outputs: flags.update(self.qa_outputs.popleft())
        for key in self.qa_drop_keys: flags.pop(key, None)
        if self.qa_hook: self.qa_hook()
        meta = self.qa_meta if self.qa_meta is not None else {"cost_rub": self.qa_cost, "provider_request_id": "qa_fixture_vision_" + str(len(self.qa_calls))}
        return flags, copy.deepcopy(meta)

    def tick(self): return self.ns["_autopost_projects_impl"]("u2", owner=True, respect_time=True, only_id=self.project["id"])
    def post(self): return copy.deepcopy(self.store.read("posting_posts")[0])
    def path(self, record): return str(self.root / Path(record["banner"]).name)

    def gate(self, record, *, project=None, now=NOW, public_texts=None):
        before = copy.deepcopy(record)
        gate = self.ns["_social_delivery_quality_gate"]("u2", record, project or self.project, self.path(record), now=now, public_texts=public_texts)
        self.assertEqual(record, before)
        changed = copy.deepcopy(record); changed.update(gate["record_updates"])
        return gate, changed

    def test_initial_final_qa_is_single_paid_proof_and_delivery_reuses_it(self):
        self.tick(); record = self.post()
        self.assertEqual((len(self.text_calls), len(self.image_calls), len(self.qa_calls)), (1, 1, 1))
        self.assertEqual(record["status"], "draft")
        self.assertAlmostEqual(record["banner_cost_rub"], 1.70)
        self.assertEqual(record["banner_vision_qa_costs"], [.4])
        self.assertEqual(record["id"], record["final_payload_qa"]["record_id"])
        self.assertEqual(set(record["delivery_quality"]), {"tg", "vk"})
        for p in ("tg", "vk"):
            self.assertEqual(record["delivery_quality"][p]["public_text_sha256"], self.ed._digest(record["delivery_texts"][p]))
        gate, after = self.gate(record)
        self.assertTrue(gate["pass"]); self.assertEqual(gate["rechecked_platforms"], [])
        self.assertEqual(len(self.qa_calls), 1); self.assertEqual(after["banner_cost_rub"], record["banner_cost_rub"])
        self.tick(); self.assertEqual(len(self.image_calls), 1)

    def test_first_qa_contains_valid_final_channel_json_and_unchanged_image(self):
        self.tick(); record = self.post()
        prompt = self.qa_calls[0]["content"][0]["text"]
        captions = json.loads(prompt.split("FINAL POST TEXT (hashtags included):\n", 1)[1])
        self.assertEqual(captions, {p: self.ed.public_text_for_qa(t) for p, t in record["delivery_texts"].items()})
        self.assertNotIn("whatsapp", prompt.lower())
        self.assertNotIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)
        self.assertEqual(record["vision_qa"]["qa_input_contract"], self.sq.final_payload_qa_contract(self.project))
        self.assertRegex(record["vision_qa"]["qa_input_sha256"], r"^[0-9a-f]{64}$")

    def test_ocean_actual_copy_keeps_all_useful_paragraphs_with_one_cta(self):
        before = copy.deepcopy(self.project)
        for platform in ("tg", "vk"):
            text = self.ns["_project_delivery_text_tracked"](self.raw_text, self.project, platform, "pfixture", final_payload=True)
            self.assertIn("Если тишина", text); self.assertIn("не сливаете бюджет на склад", text)
            self.assertNotIn("AI-Авитолог", text); self.assertNotIn("WhatsApp", text)
            self.assertEqual(text.count("Получите бесплатно аудит"), 1)
            self.assertEqual(len(re.findall(r"https?://\S+", text)), 1)
            self.assertTrue(self.ns["_ocean_publication_gate"](text, self.raw_text, platform, "pfixture")["ok"])
        self.assertEqual(self.project, before)

    def test_ocean_dev_route_is_preserved_without_catalogue(self):
        raw = "Разработка CRM на заказ начинается с процесса продаж. Сначала опишите этапы сделки и ответственных.\n\n#crm #разработка #автоматизация #сайты #бизнес"
        rendered = self.ns["_project_delivery_text_tracked"](raw, self.project, "vk", "pdevtest", final_payload=True)
        self.assertIn("/software-dev/", rendered); self.assertIn("utm_campaign=dev", rendered)
        self.assertEqual(rendered.count("Обсудить разработку:"), 1)
        self.assertNotIn("Получите бесплатно аудит", rendered)

    def test_partial_tg_keeps_same_vk_proof_without_a_new_request(self):
        self.tick(); record = self.post(); record["ids"] = {"tg": {"message_id": 315, "chat_id": "@fixture"}}
        gate, after = self.gate(record)
        self.assertTrue(gate["pass"]); self.assertEqual(gate["pending_platforms"], ["vk"])
        self.assertEqual(len(self.qa_calls), 1); self.assertEqual(after["ids"], record["ids"])

    def test_changed_vk_copy_is_rechecked_only_for_vk_within_same_budget(self):
        self.tick(); record = self.post()
        self.assertIn("Если тишина", record["delivery_texts"]["vk"])
        record["delivery_texts"]["vk"] = record["delivery_texts"]["vk"].replace("Если тишина", "Если обращений пока мало")
        gate, after = self.gate(record)
        self.assertTrue(gate["pass"]); self.assertEqual(gate["rechecked_platforms"], ["vk"])
        self.assertEqual(len(self.qa_calls), 2); self.assertAlmostEqual(after["banner_cost_rub"], 2.10)
        captions = json.loads(self.qa_calls[-1]["content"][0]["text"].split("FINAL POST TEXT (hashtags included):\n", 1)[1])
        self.assertEqual(set(captions), {"vk"})

    def test_date_asset_and_required_contract_changes_invalidate_proof(self):
        self.tick(); original = self.post()
        for change in ("date", "asset", "contract"):
            record = copy.deepcopy(original)
            before = len(self.qa_calls)
            if change == "asset":
                from PIL import Image
                Image.new("RGB", (512, 512), (200, 10, 40)).save(self.path(record), compress_level=0)
            if change == "contract":
                with patch.object(self.sq, "VISION_CONTRACT_VERSION", "FIXTURE_ACTUAL_CONTRACT_CHANGE"):
                    gate, after = self.gate(record)
            else:
                gate, after = self.gate(record, now=NOW + timedelta(days=1) if change == "date" else NOW)
            self.assertTrue(gate["pass"], gate)
            self.assertEqual(len(self.qa_calls), before + 1)
            self.assertAlmostEqual(after["banner_cost_rub"], 2.10)

    def test_initial_final_failure_has_same_id_and_actual_cost_and_no_rebuy(self):
        self.qa_outputs.append({"pass": False, "copy_season_ok": False, "issues": ["fixture_copy_failure"]})
        self.tick(); record = self.post(); first = copy.deepcopy(record)
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["id"], record["final_payload_qa"]["record_id"])
        self.assertAlmostEqual(record["banner_cost_rub"], 1.70)
        self.assertEqual(len(record["delivery_qa_attempts"]), 1)
        self.assertFalse(record["vision_qa"]["pass"])
        self.tick(); self.assertEqual(self.post(), first); self.assertEqual(len(self.qa_calls), 1)

    def test_one_allowed_image_repair_preserves_both_actual_attempts(self):
        self.image_cost = 1.0
        self.qa_outputs.extend([{"pass": False, "semantic_match": False, "issues": ["fixture_wrong_scene"]}, {}])
        self.tick(); record = self.post()
        self.assertEqual(record["status"], "draft")
        self.assertEqual((len(self.image_calls), len(self.qa_calls)), (2, 2))
        self.assertAlmostEqual(record["banner_cost_rub"], 2.80)
        self.assertEqual(record["banner_attempt_costs"], [1., 1.])
        self.assertEqual(record["banner_vision_qa_costs"], [.4, .4])
        self.assertEqual(len(record["delivery_qa_attempts"]), 2)
        self.assertEqual(len(record["banner_attempt_artifacts"]), 2)
        self.assertEqual([a["verdict"]["pass"] for a in record["delivery_qa_attempts"].values()], [False, True])

    def test_actual_qa_cost_survives_asset_loss_after_response(self):
        self.qa_hook = lambda: Path(self.image_calls[-1]["path"]).unlink()
        self.tick(); record = self.post()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertAlmostEqual(record["banner_cost_rub"], 1.70)
        self.assertIn("BANNER_CHANGED_DURING_QA", record["vision_qa"]["issues"])
        self.assertEqual(len(record["delivery_qa_attempts"]), 1)
        self.tick(); self.assertEqual(len(self.image_calls), 1)

    def test_unknown_qa_cost_blocks_and_never_becomes_free(self):
        self.qa_meta = {"cost_rub": None, "cache_hit": True, "provider_request_id": "qa_fixture_missing_receipt"}
        self.tick(); record = self.post()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertIs(record["banner_cost_known"], False)
        self.assertEqual(record["banner_attempt_costs"], [1.3])
        self.tick(); self.assertEqual(len(self.qa_calls), 1)

    def test_cache_recovery_requires_original_receipt_and_counts_it_once(self):
        self.qa_meta = {"cost_rub": None, "cache_hit": True, "provider_request_id": "qa_fixture_cached"}
        self.sq._social_vision_usage_receipt = lambda account, rid: {"cost_rub": .4, "source": "fixture_exact_receipt", "account_id": account,
                                                                 "operation": "social_banner_vision_qa_v1", "request_id": rid}
        self.tick(); record = self.post()
        self.assertEqual(record["status"], "draft"); self.assertAlmostEqual(record["banner_cost_rub"], 1.7)
        self.assertEqual(record["vision_qa"]["qa_new_charge_rub"], 0.)
        gate, after = self.gate(record); self.assertTrue(gate["pass"])
        self.assertEqual(len(self.qa_calls), 1); self.assertEqual(after["banner_cost_rub"], 1.7)

    def vk_record_with_equivalent_encoded_url(self):
        self.project.update(platforms="vk", channel_tg="")
        self.store.write("posting_projects", [self.project])
        self.tick(); record = self.post()
        public = record["delivery_texts"]["vk"]
        self.assertIn("utm_medium=social", public)
        record["delivery_texts"]["vk"] = public.replace("utm_medium=social", "utm_medium=s%6Fcial")
        return record

    def test_same_record_cached_receipt_alias_keeps_positive_original_cost_without_double_count(self):
        record = self.vk_record_with_equivalent_encoded_url()
        original = copy.deepcopy(record["delivery_qa_attempts"])
        self.qa_meta = {"cost_rub": None, "cache_hit": True, "provider_request_id": "qa_fixture_vision_1"}
        self.sq._social_vision_usage_receipt = lambda account, rid: {
            "cost_rub": .4, "source": "fixture_exact_receipt", "account_id": account,
            "operation": "social_banner_vision_qa_v1", "request_id": rid}
        gate, after = self.gate(record)
        self.assertTrue(gate["pass"], gate)
        self.assertEqual(len(self.qa_calls), 2)
        self.assertEqual(self.qa_calls[0]["content"], self.qa_calls[1]["content"])
        self.assertEqual(after["banner_cost_rub"], 1.7)
        self.assertEqual(after["banner_vision_qa_costs"], [.4])
        self.assertEqual({k: after["delivery_qa_attempts"][k] for k in original}, original)
        aliases = [v for k, v in after["delivery_qa_attempts"].items() if k not in original]
        self.assertEqual(len(aliases), 1)
        self.assertEqual((aliases[0]["cost_rub"], aliases[0]["new_charge_rub"], aliases[0]["accounted_cost_rub"]), (.4, 0., 0.))
        self.assertEqual(aliases[0]["cost_evidence"]["request_id"], "qa_fixture_vision_1")
        self.assertEqual(aliases[0]["verdict"]["qa_input_sha256"], next(iter(original.values()))["verdict"]["qa_input_sha256"])
        repeated, repeated_record = self.gate(after)
        self.assertTrue(repeated["pass"]); self.assertEqual(len(self.qa_calls), 2)
        self.assertEqual(repeated_record["banner_cost_rub"], 1.7)

    def test_repeated_receipt_with_positive_new_charge_requires_reconciliation(self):
        record = self.vk_record_with_equivalent_encoded_url()
        original = copy.deepcopy(record["delivery_qa_attempts"])
        self.qa_meta = {"cost_rub": .4, "cache_hit": False, "provider_request_id": "qa_fixture_vision_1"}
        gate, after = self.gate(record)
        self.assertFalse(gate["pass"])
        self.assertIs(after["banner_cost_known"], False)
        self.assertTrue(after["final_payload_reconciliation_required"])
        self.assertEqual(after["banner_cost_rub"], 1.7)
        self.assertEqual({k: after["delivery_qa_attempts"][k] for k in original}, original)
        observed = next(v for k, v in after["delivery_qa_attempts"].items() if k not in original)
        self.assertEqual((observed["cost_rub"], observed["new_charge_rub"]), (.4, .4))
        self.assertIsNone(observed["accounted_cost_rub"])
        self.assertIn("DELIVERY_QA_RECEIPT_CONFLICT", observed["verdict"]["issues"])

    def test_cached_receipt_amount_conflict_is_not_written_as_zero_cost(self):
        record = self.vk_record_with_equivalent_encoded_url()
        original = copy.deepcopy(record["delivery_qa_attempts"])
        self.qa_meta = {"cost_rub": .5, "cache_hit": True, "provider_request_id": "qa_fixture_vision_1"}
        gate, after = self.gate(record)
        self.assertFalse(gate["pass"]); self.assertIs(after["banner_cost_known"], False)
        self.assertTrue(after["final_payload_reconciliation_required"])
        self.assertEqual(after["banner_cost_rub"], 1.7)
        self.assertEqual(after["banner_vision_qa_costs"], [.4])
        self.assertEqual({k: after["delivery_qa_attempts"][k] for k in original}, original)
        observed = next(v for k, v in after["delivery_qa_attempts"].items() if k not in original)
        self.assertEqual(observed["cost_rub"], .5); self.assertIsNone(observed["accounted_cost_rub"])

    def test_same_cached_receipt_for_different_actual_qa_input_requires_reconciliation(self):
        record = self.vk_record_with_equivalent_encoded_url()
        original = copy.deepcopy(record["delivery_qa_attempts"])
        self.qa_meta = {"cost_rub": .4, "cache_hit": True, "provider_request_id": "qa_fixture_vision_1"}
        gate, after = self.gate(record, now=NOW + timedelta(days=1))
        self.assertFalse(gate["pass"]); self.assertIs(after["banner_cost_known"], False)
        self.assertTrue(after["final_payload_reconciliation_required"])
        self.assertEqual(after["banner_cost_rub"], 1.7)
        self.assertNotEqual(self.qa_calls[0]["content"], self.qa_calls[1]["content"])
        observed = next(v for k, v in after["delivery_qa_attempts"].items() if k not in original)
        self.assertIsNone(observed["accounted_cost_rub"])
        self.assertIn("DELIVERY_QA_RECEIPT_CONFLICT", observed["verdict"]["issues"])

    def test_invalid_public_utm_is_blocked_before_semantically_equivalent_qa(self):
        self.tick(); record = self.post()
        record["delivery_texts"]["vk"] = record["delivery_texts"]["vk"].replace("utm_source=vk", "utm_source=tg")
        gate, after = self.gate(record)
        self.assertFalse(gate["pass"])
        self.assertIn("OCEAN_FINAL_PAYLOAD_PUBLIC_CONTRACT:vk", gate["blockers"])
        self.assertEqual(len(self.qa_calls), 1)
        self.assertEqual(after["banner_cost_rub"], 1.7)

    def test_changed_context_does_not_buy_qa_without_remaining_reserve(self):
        self.tick(); record = self.post(); record["banner_cost_rub"] = 3.50
        record["delivery_texts"]["vk"] += " "
        gate, after = self.gate(record)
        self.assertFalse(gate["pass"]); self.assertEqual(len(self.qa_calls), 1)
        self.assertEqual(after["banner_cost_rub"], 3.50)

    def test_running_claim_blocks_changed_history_and_cannot_be_erased_by_plan_wrapper(self):
        key, plan = self.ns["_scheduled_generation_plan"]("u2", self.project, slot_hour=9)
        context = plan["final_payload_qa"]
        self.ns["_social_final_payload_state"]("u2", context, "claim")
        stale = copy.deepcopy(plan); stale["text_prompt_snapshot"] = "Changed history must not buy another intent"
        self.store.write(context["storage_key"], stale)
        blocked = self.tick()
        self.assertEqual(blocked["status"], "generation_blocked")
        self.assertFalse(blocked["generated"]); self.assertFalse(blocked["materialized"])
        self.assertEqual(blocked["reason"], "SOCIAL_FINAL_PAYLOAD_RECONCILIATION_REQUIRED")
        self.assertEqual((self.text_calls, self.image_calls, self.qa_calls), ([], [], []))
        self.assertEqual(self.store.read("social_final_payload_generation:" + context["record_id"])["state"], "running")

    def test_completed_generation_recovers_before_materialization_without_paid_calls(self):
        self.tick(); original = self.post()
        self.store.rows.pop(("u2", "posting_posts"))
        self.tick(); restored = self.post()
        self.assertEqual(restored["id"], original["id"])
        self.assertEqual(restored["delivery_quality"], original["delivery_quality"])
        self.assertEqual(restored["banner_cost_rub"], original["banner_cost_rub"])
        self.assertEqual((len(self.text_calls), len(self.image_calls), len(self.qa_calls)), (1, 1, 1))

    def test_previous_day_completed_result_materializes_without_any_new_paid_calls(self):
        self.tick(); original = self.post()
        self.store.rows.pop(("u2", "posting_posts"))
        with patch.object(Clock, "instant", NOW + timedelta(days=1)):
            self.ns["_autopost_projects_impl"]("u2", owner=True, respect_time=True,
                only_id=self.project["id"], scheduled_hour_override=9, scheduled_service_day_override="20261009")
        restored = self.post()
        self.assertEqual(restored["id"], original["id"])
        for key in ("delivery_quality", "delivery_qa_attempts", "banner_cost_rub", "generation_key", "content_brief", "final_payload_qa"):
            self.assertEqual(restored[key], original[key], key)
        self.assertEqual(restored["publish_at"], "2026-10-09 09:00")
        self.assertEqual((len(self.text_calls), len(self.image_calls), len(self.qa_calls)), (1, 1, 1))

    def test_new_manicure_plan_ignores_unrelated_next_holiday_but_preserves_explicit_topic(self):
        project = copy.deepcopy(self.fixtures["projects"]["270dbf472f7f"])
        project.update(mode="auto", allow_hashtags=True)
        self.theme = self.fixtures["posts"]["manicure"]["theme"]
        self.assertIn("День работника сельского хозяйства", self.original_project_occasion(project))
        with patch.dict(self.ns, {"_project_occasion": self.original_project_occasion,
                                  "_build_text_prompt": self.original_build_text_prompt}):
            _, ordinary = self.ns["_scheduled_generation_plan"]("u2", project, slot_hour=9)
            self.assertEqual(ordinary["occasion"], "")
            self.assertEqual(ordinary["occasion_context"], {"kind": "evergreen"})
            self.assertNotIn("День работника сельского хозяйства", ordinary["text_prompt_snapshot"])
            self.theme = "Как подготовить поздравление ко Дню работника сельского хозяйства"
            _, selected = self.ns["_scheduled_generation_plan"]("u2", project, slot_hour=18)
            self.assertEqual(selected["occasion"], "")
            self.assertEqual(selected["occasion_context"]["kind"], "dated")
            self.assertEqual(selected["occasion_context"]["date"], "2026-10-11")
        self.assertEqual((self.text_calls, self.image_calls, self.qa_calls), ([], [], []))

    def test_late_failed_generation_keeps_original_slot_deadline_and_does_not_shift_to_evening(self):
        self.qa_outputs.append({"pass": False, "copy_season_ok": False, "issues": ["fixture_copy_failure"]})
        with patch.object(Clock, "instant", NOW + timedelta(hours=1)):
            self.tick(); record = self.post()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["generation_key"], "social-slot:u2:a374ced25745:20261009_09")
        self.assertEqual(record["publish_at"], "2026-10-09 09:00")
        self.assertEqual((len(self.image_calls), len(self.qa_calls)), (1, 1))

    def test_previous_day_running_or_missing_claim_cannot_start_paid_work(self):
        _, plan = self.ns["_scheduled_generation_plan"]("u2", self.project, slot_hour=9)
        context = plan["final_payload_qa"]
        options = {"project": self.project, "final_payload_context": context}
        with patch.object(Clock, "instant", NOW + timedelta(days=1)):
            with self.assertRaisesRegex(RuntimeError, "PAST_CLAIM_FORBIDDEN"):
                self.ns["_social_generate_final_payload"]("u2", self.theme, options)
        self.assertIsNone(self.store.read("social_final_payload_generation:" + context["record_id"]))
        self.ns["_social_final_payload_state"]("u2", context, "claim")
        saved = self.store.read("social_final_payload_generation:" + context["record_id"])
        with patch.object(Clock, "instant", NOW + timedelta(days=1)):
            with self.assertRaisesRegex(RuntimeError, "RECONCILIATION_REQUIRED"):
                self.ns["_social_generate_final_payload"]("u2", self.theme, options)
        self.assertEqual(self.store.read("social_final_payload_generation:" + context["record_id"]), saved)
        self.assertEqual((self.text_calls, self.image_calls, self.qa_calls), ([], [], []))

    def test_invalid_or_far_past_context_is_blocked_before_claim_lookup(self):
        _, plan = self.ns["_scheduled_generation_plan"]("u2", self.project, slot_hour=9)
        original = plan["final_payload_qa"]
        for changes in ({"local_date": "2026-10-07"}, {"local_date": "2026-10-09broken"},
                        {"generation_key": "social-slot:u2:a374ced25745:20261008_09"},
                        {"storage_key": "posting_generation_plan:other:20261009_09"}):
            context = {**original, **changes}
            with patch.dict(self.ns, {"_social_final_payload_state": no_io}):
                with self.assertRaisesRegex(RuntimeError, "SOCIAL_FINAL_PAYLOAD_CONTEXT_INVALID"):
                    self.ns["_social_generate_final_payload"]("u2", self.theme, {"project": self.project, "final_payload_context": context})
        self.assertEqual((self.text_calls, self.image_calls, self.qa_calls), ([], [], []))

    def test_targeted_owner_ready_calls_continue_after_an_occupied_project_claim(self):
        ocean = copy.deepcopy(self.project)
        _, plan = self.ns["_scheduled_generation_plan"]("u2", ocean, slot_hour=9)
        context = plan["final_payload_qa"]
        self.ns["_social_final_payload_state"]("u2", context, "claim")
        original_claim = self.store.read("social_final_payload_generation:" + context["record_id"])
        dushi = copy.deepcopy(self.fixtures["projects"]["f938caa3a944"])
        source = self.fixtures["posts"]["dushi"]
        self.theme, self.raw_text = source["theme"], source["text"]
        dushi.update(themes=[self.theme], allow_hashtags=True, image_source="ai", mode="auto",
                     times=["09:00"], paid_auto_generation=True, expected_auto_generation=True)
        self.store.write("posting_projects", [ocean, dushi])
        outcomes = []
        # Same caller shape as worker's owner_ready targeted loop: no per-project
        # exception handler. An expected occupied claim must not abort this loop.
        for project in (ocean, dushi):
            outcomes.append(self.ns["autopost_projects"]("u2", owner=True, respect_time=True, only_id=project["id"]))
        self.assertEqual(outcomes[0]["status"], "generation_blocked")
        self.assertEqual(outcomes[0]["record_id"], context["record_id"])
        self.assertFalse(outcomes[0]["paid_work_started_in_this_call"])
        self.assertEqual(self.store.read("social_final_payload_generation:" + context["record_id"]), original_claim)
        posts = self.store.read("posting_posts")
        self.assertEqual(len(posts), 1); self.assertEqual(posts[0]["project_id"], dushi["id"])
        self.assertEqual(posts[0]["status"], "draft")
        self.assertEqual((len(self.text_calls), len(self.image_calls), len(self.qa_calls)), (1, 1, 1))

    def test_existing_plans_are_not_backfilled_and_corruption_does_not_mint(self):
        _, plan = self.ns["_scheduled_generation_plan"]("u2", self.project, slot_hour=9)
        context = plan.pop("final_payload_qa"); self.store.write(context["storage_key"], plan)
        _, existing = self.ns["_scheduled_generation_plan"]("u2", self.project, slot_hour=9)
        self.assertNotIn("final_payload_qa", existing)
        self.store.rows[("u2", context["storage_key"])].value = "{broken"
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_GENERATION_PLAN_UNAVAILABLE"):
            self.ns["_scheduled_generation_plan"]("u2", self.project, slot_hour=9)
        self.assertEqual(self.image_calls, [])

    def test_only_today_auto_target_projects_are_enrolled(self):
        for pid, mode, enrolled in (("a374ced25745", "auto", True), ("f938caa3a944", "auto", True),
                                     ("270dbf472f7f", "auto", True), ("other", "auto", False),
                                     ("fixture_moderate", "moderate", False)):
            project = {**self.project, "id": pid, "mode": mode}
            _, plan = self.ns["_scheduled_generation_plan"]("u2", project, slot_hour=9)
            self.assertEqual("final_payload_qa" in plan, enrolled)
        project = {**self.project, "id": "a374ced25745"}
        _, yesterday = self.ns["_scheduled_generation_plan"]("u2", project, slot_hour=9, service_day="20261008")
        self.assertNotIn("final_payload_qa", yesterday)

    def test_historical_partial_frozen_payload_is_not_rerendered_or_sent(self):
        record = {"id": "0d6f5494f122", "delivery_protocol": "LEGACY", "ids": {"tg": {"message_id": 315}},
                  "delivery_texts": {"tg": "Frozen TG315", "vk": "Frozen agreed VK with historical footer"}, "text": "Historical original"}
        before = copy.deepcopy(record)
        with patch.dict(self.ns, {"_project_delivery_text_tracked": no_io, "_send_tg": no_io, "_send_vk": no_io}):
            texts = self.ns["_social_delivery_public_texts"](record, self.project, ["vk"])
        self.assertEqual(texts, {"vk": record["delivery_texts"]["vk"]})
        self.assertEqual(record, before); self.assertEqual(self.qa_calls, [])

    def test_dushi_actual_flow_keeps_own_style_and_reuses_final_proof(self):
        source = self.fixtures["posts"]["dushi"]
        self.project = copy.deepcopy(self.fixtures["projects"]["f938caa3a944"])
        self.theme, self.raw_text = source["theme"], source["text"]
        self.project.update(themes=[self.theme], allow_hashtags=True, image_source="ai", mode="auto",
                            paid_auto_generation=True, expected_auto_generation=True)
        self.store.write("posting_projects", [self.project])
        self.tick(); record = self.post()
        self.assertEqual(record["status"], "draft")
        self.assertEqual(len(self.qa_calls), 1)
        prompt = self.qa_calls[0]["content"][0]["text"]
        self.assertNotIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)
        self.assertEqual(record["vision_qa"]["vision_qa_contract"]["profile"], "dushi_personal")
        gate, after = self.gate(record); self.assertTrue(gate["pass"])
        self.assertEqual(len(self.qa_calls), 1); self.assertEqual(after["banner_cost_rub"], 1.7)

    def run_manicure_native_flow(self, *, missing_flags=()):
        from PIL import Image
        import inspect
        self.project = copy.deepcopy(self.fixtures["projects"]["270dbf472f7f"])
        source = self.fixtures["posts"]["manicure"]
        self.theme, self.raw_text = source["theme"], source["text"]
        self.qa_drop_keys = set(missing_flags)
        self.project.update(themes=[self.theme], allow_hashtags=True, image_source="ai", mode="auto", platforms="vk", channel_tg="",
                            paid_auto_generation=True, expected_auto_generation=True)
        self.store.write("posting_projects", [self.project])
        reference = self.root / "fixture_owner_reference.png"
        Image.new("RGB", (512, 512), (140, 40, 180)).save(reference, compress_level=0)
        native = ModuleType("app.services.banner_full_ai_premium")
        native.OWNER_REFERENCE_IMAGES = {"manicure": str(reference), "pedicure": str(reference)}
        native.OWNER_REFERENCE_VERSION = "FIXTURE_JULY_OWNER_REFERENCE"
        native.estimate_cost_rub = lambda: 1.3
        rows, calls = {}, []

        def create_preview(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            build = native_scope["build_prompt"]
            calls[-1]["native_prompt"] = build(**{k: v for k, v in kwargs.items() if k in inspect.signature(build).parameters})
            path = self.root / "u2_native_fixture.png"
            Image.new("RGB", (512, 512), (40, 80, 120)).save(path, compress_level=0)
            row = {"status": "ok", "banner_id": "native_fixture_id", "version": 1,
                   "absolute_path": str(path), "actual_cost_rub": 1.3, "qa_cost_rub": 0.,
                   "total_cost_rub": 1.3, "cost_reconciled": True, "technical_qa": {"passed": True}, "qa_passed": False,
                   "cost_sla_pass": True, "preview_status": "VISION_QA_PENDING",
                   "provider_request_id": "qa_fixture_native_image",
                   "engine": "full_ai_premium", "engine_source": "fixture_native_july",
                   "qa_contract": {"exact_title": kwargs["exact_title"],
                                   "exact_subtitle": kwargs["exact_subtitle"],
                                   "advantages": kwargs["advantages"]}}
            rows[row["banner_id"]] = [copy.deepcopy(row)]
            return row

        def version_row(account, identity, version):
            current = copy.deepcopy(rows[identity]); return current, current[0]

        native_scope = {"re": re, "_version_row": version_row, "_save_versions": lambda a, i, r: rows.update({i: copy.deepcopy(r)}),
                        "MAX_COST_RUB": 4., "_trip_cost_circuit": lambda **k: {}, "_now_iso": lambda: NOW.isoformat()}
        native_source = BACKEND / "tests/fixtures/native_banner_33c5.py"
        self.assertEqual(hashlib.sha256(native_source.read_bytes()).hexdigest(), "33c5ce15194fedc92fbf777e477c2baf4fbe700fd1a5a7660c4da70f53a91460")
        defs = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
        needed = {"_apply_vision_verdict", "build_prompt", "_clean", "_text_block", "_safe_zone_percent", "_owner_style_contract"}
        for node in ast.parse(native_source.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in needed:
                defs.append(node)
            elif isinstance(node, ast.Assign):
                try: ast.literal_eval(node.value)
                except (TypeError, ValueError): continue
                defs.append(node)
        exec(compile(ast.fix_missing_locations(ast.Module(body=defs, type_ignores=[])), str(native_source), "exec"), native_scope)
        native.create_preview = create_preview
        native._apply_vision_verdict = native_scope["_apply_vision_verdict"]
        with patch.dict(sys.modules, {"app.services.banner_full_ai_premium": native}):
            self.load_module("app.services.social_premium_banner", SERVICES / "social_premium_banner.py")
            self.tick(); record = self.post()
            if missing_flags:
                self.assertEqual(record["status"], "quality_blocked")
                self.assertFalse(record["vision_qa"]["pass"])
                self.assertTrue(record["vision_qa"]["qa_schema_repair_required"])
                self.assertFalse(record["vision_qa"]["image_repair_allowed"])
                self.assertEqual(record["vision_qa"]["repair_scope"], "qa")
                self.assertEqual(len(calls), 1); self.assertEqual(len(self.qa_calls), 1)
                self.assertAlmostEqual(record["banner_cost_rub"], 1.7)
                self.assertEqual(len(record["delivery_qa_attempts"]), 1)
                prompt = self.qa_calls[0]["content"][0]["text"]
                required_line = prompt.split(". Also return issues", 1)[0]
                for field in missing_flags:
                    self.assertIn('"' + field + '"', required_line)
                    self.assertIn("vision_qa_invalid_boolean:" + field, record["vision_qa"]["issues"])
                return
            self.assertEqual(record["status"], "draft", record.get("error"))
            self.assertEqual(len(calls), 1); self.assertTrue(calls[0]["defer_vision_qa"])
            self.assertEqual(record["content_brief"]["occasion"], {"kind": "evergreen"})
            self.assertEqual(record["publish_at"], "2026-10-09 09:00")
            self.assertLessEqual(len(calls[0]["description"]), 3000)
            for detail in ("шоколадный металлик", "средней длине", "овальной формой"):
                self.assertIn(detail, record["visual_brief"]["post_visual_source_text"])
                self.assertIn(detail, calls[0]["native_prompt"])
            self.assertIn("OWNER-APPROVED VISUAL BENCHMARK [manicure]", calls[0]["native_prompt"])
            self.assertNotIn("День работника сельского хозяйства", calls[0]["native_prompt"])
            self.assertEqual(len(self.qa_calls), 1)
            self.assertEqual(len(self.qa_calls[0]["content"]), 3)
            self.assertIn("NAIL/REFERENCE PREMIUM STYLE RULES", self.qa_calls[0]["content"][0]["text"])
            self.assertEqual(set(record["delivery_quality"]), {"vk"})
            self.assertEqual(set(record["delivery_texts"]), {"vk"})
            self.assertTrue(record["vision_qa"]["reference_style_match"])
            self.assertEqual(record["vision_qa"]["reference"]["sha256"], hashlib.sha256(reference.read_bytes()).hexdigest())
            self.assertEqual(record["july_premium"]["preview_status"], "DRAFT_PREVIEW")
            self.assertAlmostEqual(record["banner_cost_rub"], 1.7)
            gate, after = self.gate(record)
            self.assertTrue(gate["pass"], gate)
            self.assertEqual(len(self.qa_calls), 1); self.assertEqual(after["banner_cost_rub"], record["banner_cost_rub"])

    def test_manicure_actual_flow_uses_native_reference_full_flags_and_vk_only_proof(self):
        self.run_manicure_native_flow()

    def test_missing_native_qa_keys_keep_paid_failure_and_do_not_buy_a_second_image(self):
        self.run_manicure_native_flow(missing_flags=("exact_text_ok", "composition_ok", "editorial_richness_ok"))


if __name__ == "__main__": unittest.main(verbosity=2)
