"""Regression tests for the actual canonical prepare branch and paid handoff.

DB/provider/revision dependencies are offline doubles; the functions and the
legacy destructive block come from the actual pinned source files. Real lease
SQL is tested by the separate isolated PostgreSQL runner.
"""
import ast
import copy
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from test_banner_workers import Case, DB, ROOT, extract, qa_error

ORIGINAL_PREPARE = ROOT / "tests/banner_prepare_before_fixture.py"


class Field:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return (self.name, "eq", value)
    def __ne__(self, value): return (self.name, "ne", value)
    def in_(self, value): return (self.name, "in", value)
    def is_(self, value): return (self.name, "eq", value)
    def asc(self): return self


class Model:
    id = Field("id")
    account_id = Field("account_id")
    campaign_id = Field("campaign_id")
    status = Field("status")
    fingerprint = Field("fingerprint")
    media_id = Field("media_id")
    entity_type = Field("entity_type")
    entity_id = Field("entity_id")
    role = Field("role")
    position = Field("position")
    deleted_at = Field("deleted_at")
    def __init__(self, **kwargs):
        self.__dict__.update(dict(id=None, result_json=None, attempts=0, progress_done=0,
                                  progress_total=0, payload_json={}, status="queued"), **kwargs)


class JobModel(Model): pass
class ItemModel(Model): pass
class CampaignModel(Model): pass
class LinkModel(Model): pass
class AssetModel(Model): pass
class IntegrityError(Exception): pass


class Query:
    def __init__(self, db, models):
        self.db, self.models, self.conditions = db, models, []
    def filter(self, *conditions): self.conditions.extend(conditions); return self
    def order_by(self, *args): return self
    def join(self, *args): return self
    def populate_existing(self): self.db.fresh_reads += 1; return self
    def with_for_update(self, **kwargs): self.db.row_locks.append(self.models); return self
    def all(self):
        if self.models == (JobModel,): values = list(self.db.queue.values())
        elif self.models == (ItemModel,): values = list(self.db.case.rows.values())
        elif self.models == (CampaignModel,): values = [self.db.case.campaign]
        elif self.models == (LinkModel, AssetModel): values = list(self.db.pairs)
        elif self.models == (LinkModel,): values = [pair[0] for pair in self.db.pairs]
        else: raise AssertionError("unexpected model query " + str(self.models))
        def matches(value):
            for name, operation, wanted in self.conditions:
                objects = value if isinstance(value, tuple) else (value,)
                actual = next((getattr(obj, name) for obj in objects if hasattr(obj, name)), None)
                if operation == "eq" and actual != wanted: return False
                if operation == "ne" and actual == wanted: return False
                if operation == "in" and actual not in wanted: return False
            return True
        return [value for value in values if matches(value)]
    def first(self):
        values = self.all()
        return values[0] if values else None
    def delete(self, **kwargs):
        selected = self.all()
        self.db.deleted_links.extend(link.id for link in selected)
        self.db.pairs = [pair for pair in self.db.pairs if pair[0] not in selected]
        return len(selected)


class QueueDB(DB):
    def __init__(self, case):
        super().__init__(case.job)
        self.case, self.queue, self.pairs, self.pending = case, {}, [], None
        self.concurrent_winner = None
        self.prior_paid, self.raw_reads, self.fresh_reads = False, [], 0
        self.deleted_links, self.row_locks, self.child_commit_snapshots = [], [], []
    def query(self, *models): return Query(self, models)
    def execute(self, query, params):
        self.raw_reads.append((str(query), params))
        return types.SimpleNamespace(first=lambda: (123,) if self.prior_paid else None)
    def add(self, row): self.pending = row
    def flush(self):
        if self.concurrent_winner is not None:
            winner, self.concurrent_winner = self.concurrent_winner, None
            self.queue[winner.id] = winner
            raise IntegrityError("another canonical enqueue won UNIQUE fingerprint")
        if self.pending is not None:
            row, self.pending = self.pending, None
            if any(x.fingerprint == row.fingerprint for x in self.queue.values()):
                raise IntegrityError("unique fingerprint")
            row.id = 101 + len(self.queue)
            self.queue[row.id] = row
    def commit(self):
        super().commit()
        self.child_commit_snapshots.append({i: copy.deepcopy(x.payload_json) for i, x in self.queue.items()})
    def rollback(self):
        super().rollback()
        self.pending = None


def source_block(path, predicate, arguments):
    tree = ast.parse(path.read_text())
    original = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_campaign_prepare")
    block = next(n for n in ast.walk(original) if isinstance(n, ast.If) and predicate(n))
    fn = ast.FunctionDef(name="run_block", args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=x) for x in arguments],
         kwonlyargs=[], kw_defaults=[], defaults=[]), body=[block, ast.Return(value=ast.Constant(value=None))], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    env = {}
    exec(compile(module, str(path), "exec"), env)
    return env["run_block"]


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.c = Case(2)
        self.parent = self.c.job
        self.parent.kind, self.parent.status = "campaign_prepare", "running"
        self.parent.payload_json["_premium_prepare_intent_policy"] = "canonical_banner_child_v1"
        self.c.campaign.status = "draft"
        self.db = QueueDB(self.c)
        self.c.db = self.db
        for row in self.c.rows.values():
            row.campaign_id, row.status = 900, "draft"
            row.updated_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
            row.hero_media_id = row.id * 100
            row.payload.update(title=f"Title {row.id}", description=f"Description {row.id}",
                images=[f"/images/{row.id}-{i}.png" for i in range(9)],
                media_semantic_evidence=[{"media_id": row.id * 100 + i, "position": i} for i in range(9)])
            for pos in range(9):
                link = types.SimpleNamespace(id=row.id * 100 + pos, media_id=row.id * 100 + pos,
                    entity_type="campaign_item", entity_id=str(row.id), role="hero" if pos == 0 else "gallery", position=pos)
                asset = types.SimpleNamespace(id=link.media_id, account_id="acct-a", storage_key=f"{row.id}-{pos}.png",
                    sha256="a" * 64, deleted_at=None, bytes_current=True)
                self.db.pairs.append((link, asset))
        self.ff = {"order_contract": {"requested_total": 2}}
        self.c.C._ff_state_load = lambda campaign: ({}, self.ff)
        self.c.C._ff_publication_locked = lambda ff: bool(ff.get("publication_locked"))
        self.c.C._ff_order_progress = lambda *a, **kw: {"requested_total": 2, "created_count": 2, "qa_pass_count": 2}
        self.c.C._ff_order_event = lambda *a, **kw: None
        self.c.C._ff_state_save = lambda db, *a: db.commit()
        modules = {}
        for name, model, attr in (("background_job", JobModel, "BackgroundJob"), ("campaign_item", ItemModel, "CampaignItem"),
                                  ("campaign", CampaignModel, "Campaign"), ("media_link", LinkModel, "MediaLink"),
                                  ("media_asset", AssetModel, "MediaAsset")):
            module = types.ModuleType("app.models." + name)
            setattr(module, attr, model)
            modules[module.__name__] = module
        exc = types.ModuleType("sqlalchemy.exc"); exc.IntegrityError = IntegrityError
        modules[exc.__name__] = exc
        semantic = types.ModuleType("app.services.media_semantic_validator")
        semantic.semantic_content_fingerprint = lambda p: "fp"
        modules[semantic.__name__] = semantic
        clock = types.ModuleType("app.services.job_execution_clock")
        clock.assert_current_execution_owner = lambda db: db.info.setdefault("owner_guard_calls", []).append(True) or True
        modules[clock.__name__] = clock
        media = types.ModuleType("app.services.campaign_banner_media_contract")
        media.linked_file_current = lambda asset: asset.bytes_current
        modules[media.__name__] = media
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start(); self.addCleanup(self.module_patch.stop)
        self.functions = extract(ROOT / "app/services/jobs.py",
            {"_campaign_premium_prepare_banner_gate", "_campaign_premium_validation_snapshot", "_campaign_premium_finalize_ready", "_campaign_checkpoint"},
            {"BackgroundJob": JobModel, "_t": lambda sql: sql, "get_trace_id": lambda: "trace", "job_priority": lambda kind: 10})

    def gate(self):
        return self.functions["_campaign_premium_prepare_banner_gate"](
            self.db, self.parent, self.parent.payload_json, self.c.C, self.c.campaign, self.c.user, ["acct-a"], "acct-a", [1, 2])

    def admission_prefix(self):
        tree = ast.parse((ROOT / "app/services/jobs.py").read_text())
        original = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_campaign_prepare")
        statements = []
        for node in original.body:
            statements.append(node)
            if isinstance(node, ast.Assign) and "_campaign_checkpoint(db, job, payload, 'preflight'" in ast.unparse(node):
                break
        fn = copy.deepcopy(original)
        fn.body = statements + [ast.Return(value=ast.Name(id="payload", ctx=ast.Load()))]
        env = extract(ROOT / "app/services/jobs.py", {"_campaign_checkpoint", "_campaign_banner_required"},
            {"_campaign_job_context": lambda *a: (self.c.C, self.c.campaign, self.c.user, ["acct-a"], "acct-a"),
             "_campaign_mutation_locked": lambda *a: (False, {}, {})})
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "actual_prepare_prefix", "exec"), env)
        return env["_run_campaign_prepare"](self.db, self.parent, self.parent.payload_json)

    def child(self, status="queued", **extra):
        child = JobModel(id=101, kind="campaign_banners", status=status, account_id="acct-a", owner_user_id=8,
            fingerprint="campaign_banners:900:acct-a", payload_json={"campaign_id": 900, "item_ids": [1, 2]}, **extra)
        self.db.queue[child.id] = child
        return child

    def test_actual_old_banner_error_block_erases_all_images_despite_only_one_bad_link(self):
        self.c.rows[1].payload["media_semantic_rejections"] = [{"media_id": 101, "content_fingerprint": "fp"}]
        fn = source_block(ORIGINAL_PREPARE,
            lambda n: ast.unparse(n.test) == "banner_result.get('errors')",
            ["db", "C", "campaign", "account_id", "banner_result", "banner_chunk"])
        result, status = fn(self.db, self.c.C, self.c.campaign, "acct-a", {"errors": [{"id": 1}]}, [1])
        broken = json.loads(self.c.rows[1].payload_json)
        self.assertEqual(status, "retry")
        self.assertEqual(broken["images"], [])
        self.assertEqual(broken["media_semantic_evidence"], [])
        self.assertEqual(self.db.deleted_links, [101])
        self.assertEqual(len([link for link, _ in self.db.pairs if link.entity_id == "1"]), 8)

    def test_standard_prepare_retains_exact_original_body_when_premium_guards_are_disabled(self):
        class StandardBranch(ast.NodeTransformer):
            def visit_Assign(self, node):
                if any(isinstance(target, ast.Name) and target.id in {"_premium_prepare", "_premium_validated_snapshot"}
                       for target in node.targets):
                    return None
                return self.generic_visit(node)
            def visit_Name(self, node):
                return ast.Constant(value=False) if node.id == "_premium_prepare" else node
            def visit_UnaryOp(self, node):
                node = self.generic_visit(node)
                if isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Constant) and type(node.operand.value) is bool:
                    return ast.Constant(value=not node.operand.value)
                return node
            def visit_BoolOp(self, node):
                node = self.generic_visit(node)
                is_and = isinstance(node.op, ast.And)
                decisive, neutral = (False, True) if is_and else (True, False)
                if any(isinstance(value, ast.Constant) and value.value is decisive for value in node.values):
                    return ast.Constant(value=decisive)
                values = [value for value in node.values if not (isinstance(value, ast.Constant) and value.value is neutral)]
                if len(values) == 1: return values[0]
                node.values = values
                return node
            def visit_If(self, node):
                node = self.generic_visit(node)
                if isinstance(node.test, ast.Constant) and type(node.test.value) is bool:
                    return node.body if node.test.value else node.orelse
                return node
            def visit_IfExp(self, node):
                node = self.generic_visit(node)
                if isinstance(node.test, ast.Constant) and type(node.test.value) is bool:
                    return node.body if node.test.value else node.orelse
                return node
        def actual(path):
            return next(node for node in ast.parse(path.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_run_campaign_prepare")
        original = actual(ORIGINAL_PREPARE)
        candidate = StandardBranch().visit(actual(ROOT / "app/services/jobs.py"))
        self.assertEqual(ast.dump(candidate, include_attributes=False), ast.dump(original, include_attributes=False))

    def test_prepare_creates_one_canonical_child_with_committed_per_item_keys(self):
        result, status = self.gate()
        self.assertEqual((status, result["child_jobs_active"]), ("retry", 1))
        child = self.db.queue[101]
        self.assertEqual(child.payload_json["banner_item_intents"]["1"], "campaign_banners:101:item:1:r0")
        self.assertEqual(child.payload_json["banner_item_budget_intents"]["1"], "campaign_banners:101:item:1")
        self.assertEqual(self.parent.payload_json["_premium_banner_child_job_id"], 101)
        self.assertEqual(self.db.child_commit_snapshots[-1][101]["banner_item_intents"], child.payload_json["banner_item_intents"])
        self.gate()
        self.assertEqual(len(self.db.queue), 1)
        self.assertEqual(self.c.C.calls, [])

    def test_prepare_and_banner_worker_interleave_without_second_paid_owner(self):
        self.gate(); self.gate()
        child = self.db.queue[101]
        self.c.job, self.db.job = child, child
        child.attempts = 1
        result, status = self.c.run()
        child.result_json, child.status = result, status
        self.c.job, self.db.job = self.parent, self.parent
        self.assertEqual(status, "completed")
        self.assertEqual(self.gate()[1], "completed")
        self.assertEqual(len(self.c.C.calls), 1)
        self.assertEqual(len(self.db.queue), 1)
        self.assertEqual(self.c.C.guardian, ["campaign_prepare"])

    def test_concurrent_enqueue_winner_keeps_terminal_payload_and_is_not_resurrected(self):
        winner = JobModel(id=222, kind="campaign_banners", status="completed", account_id="acct-a",
            fingerprint="campaign_banners:900:acct-a",
            payload_json={"campaign_id": 900, "item_ids": [1, 2], "banner_item_intents": {"1": "old-original-intent"}},
            result_json={"stage": "banners_done"})
        self.db.concurrent_winner = winner
        before = copy.deepcopy(winner.payload_json)
        result, status = self.gate()
        self.assertEqual((status, result["code"]), ("partial", "BANNER_CHILD_COMPLETED_UNVERIFIED"))
        self.assertEqual(winner.status, "completed")
        self.assertEqual(winner.payload_json, before)
        self.assertEqual(len(self.db.queue), 1)

    def test_child_provider_failure_keeps_every_existing_gallery_byte_and_proof(self):
        child = self.child("partial", result_json={"code": "IMAGE_OUTCOME_UNKNOWN", "errors": [qa_error(1, "reconcile_image", retry_allowed=False)]})
        before = copy.deepcopy([row.payload for row in self.c.rows.values()])
        result, status = self.gate()
        self.assertEqual((status, result["code"]), ("partial", "IMAGE_OUTCOME_UNKNOWN"))
        self.assertEqual([row.payload for row in self.c.rows.values()], before)
        self.assertEqual(self.db.deleted_links, [])
        self.assertEqual(self.c.C.calls, [])

    def test_started_legacy_prepare_cannot_create_fresh_empty_cost_scope(self):
        self.parent.payload_json.pop("_premium_prepare_intent_policy")
        result, status = self.gate()
        self.assertEqual((status, result["code"]), ("partial", "LEGACY_BANNER_INTENT_RECONCILIATION_REQUIRED"))
        self.assertEqual(self.db.queue, {})

    def test_first_prepare_admission_survives_text_media_continuations(self):
        self.parent.payload_json.pop("_premium_prepare_intent_policy")
        first = self.admission_prefix()
        self.assertEqual(first["_premium_prepare_intent_policy"], "canonical_banner_child_v1")
        self.parent.attempts = 8
        self.parent.payload_json["_continuation_count"] = 7
        self.assertEqual(self.admission_prefix()["_premium_prepare_intent_policy"], "canonical_banner_child_v1")
        self.assertEqual(self.gate()[1], "retry")

    def test_old_prepare_first_seen_after_multiple_attempts_is_not_new_admission(self):
        self.parent.payload_json.pop("_premium_prepare_intent_policy")
        self.parent.attempts = 8
        self.assertNotIn("_premium_prepare_intent_policy", self.admission_prefix())
        self.assertEqual(self.gate()[0]["code"], "LEGACY_BANNER_INTENT_RECONCILIATION_REQUIRED")

    def test_explicit_no_banner_order_does_not_enter_premium_banner_lane(self):
        self.parent.payload_json.pop("_premium_prepare_intent_policy")
        self.ff["order_contract"]["banner_required"] = False
        self.assertNotIn("_premium_prepare_intent_policy", self.admission_prefix())

    def test_prior_paid_receipt_without_child_requires_reconciliation(self):
        self.db.prior_paid = True
        result, status = self.gate()
        self.assertEqual((status, result["code"]), ("partial", "PREMIUM_EXISTING_INTENT_RECONCILIATION_REQUIRED"))
        self.assertEqual(self.db.queue, {})

    def test_current_legacy_receipts_complete_without_new_child_or_intent(self):
        self.parent.payload_json.pop("_premium_prepare_intent_policy")
        self.c.pass_items([1, 2])
        self.assertEqual(self.gate()[1], "completed")
        self.assertEqual(self.db.queue, {})
        self.assertEqual(self.c.C.calls, [])

    def test_empty_error_completed_child_does_not_fake_current_ready_or_reset_history(self):
        child = self.child("completed", result_json={"status": "blocked", "errors": [], "code": "FULL_AI_PREMIUM_NOT_READY"})
        for _ in range(3):
            result, status = self.gate()
            self.assertEqual((status, result["code"]), ("partial", "FULL_AI_PREMIUM_NOT_READY"))
        self.assertEqual(child.status, "completed")
        self.assertEqual(self.c.C.calls, [])

    def test_actual_premium_validation_failure_returns_before_legacy_destructive_refresh(self):
        fn = source_block(ROOT / "app/services/jobs.py", lambda n: ast.unparse(n.test) == "not val.get('ok')",
                          ["_premium_prepare", "val", "campaign", "account_id"])
        result, status = fn(True, {"ok": False, "items": [{"id": 1, "errors": [{"code": "media_semantic_proof_stale"}]}]}, self.c.campaign, "acct-a")
        self.assertEqual((status, result["code"]), ("partial", "PREMIUM_INTERNAL_VALIDATION_BLOCKED"))
        self.assertEqual(self.db.deleted_links, [])

    def snapshot(self):
        return self.functions["_campaign_premium_validation_snapshot"](
            self.db, self.c.C, self.c.campaign, ["acct-a"], "acct-a", [1, 2])

    def finalize(self, validated):
        return self.functions["_campaign_premium_finalize_ready"](
            self.db, self.parent, self.parent.payload_json, self.c.C, self.c.campaign, ["acct-a"], "acct-a", [1, 2], validated)

    def test_final_ready_rechecks_card_revision_after_external_validation(self):
        self.c.pass_items([1, 2]); validated = self.snapshot()
        self.c.rows[1].payload["description"] = "Another editor changed this after the XML check"
        result, status = self.finalize(validated)
        self.assertEqual((status, result["code"]), ("partial", "PREMIUM_VALIDATED_REVISION_CHANGED"))
        self.assertEqual(self.c.rows[1].status, "draft")
        self.assertTrue(self.db.row_locks)

    def test_final_ready_rejects_changed_gallery_even_without_card_version_bump(self):
        self.c.pass_items([1, 2]); validated = self.snapshot()
        self.db.pairs[1][1].storage_key = "concurrently-replaced.png"
        result, status = self.finalize(validated)
        self.assertEqual((status, result["code"]), ("partial", "PREMIUM_VALIDATED_REVISION_CHANGED"))
        self.assertEqual(self.c.rows[1].status, "draft")

    def test_final_ready_rejects_actual_file_hash_drift(self):
        self.c.pass_items([1, 2]); validated = self.snapshot()
        self.db.pairs[1][1].bytes_current = False
        result, status = self.finalize(validated)
        self.assertEqual((status, result["code"]), ("partial", "PREMIUM_READY_MEDIA_BYTES_CHANGED"))

    def test_final_ready_rejects_new_publication_lock(self):
        self.c.pass_items([1, 2]); validated = self.snapshot()
        self.ff["publication_locked"] = True
        result, status = self.finalize(validated)
        self.assertEqual((status, result["code"]), ("partial", "PREMIUM_PREPARE_PUBLICATION_LOCKED"))

    def test_final_ready_uses_current_proofs_and_orm_updates_in_one_commit(self):
        self.c.pass_items([1, 2]); validated = self.snapshot()
        commits = len(self.db.commits)
        self.assertIsNone(self.finalize(validated))
        self.assertEqual([row.status for row in self.c.rows.values()], ["ready", "ready"])
        self.assertEqual(self.c.campaign.status, "ready_for_review")
        self.assertEqual(len(self.db.commits), commits + 1)
        self.assertEqual(self.parent.payload_json["stage"], "ready_for_review")
        self.assertEqual(self.parent.progress_done, 8)


if __name__ == "__main__":
    unittest.main()
