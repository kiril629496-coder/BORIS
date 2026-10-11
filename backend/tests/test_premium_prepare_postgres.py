#!/usr/bin/env python3
"""Opt-in real PostgreSQL prepare gate; never uses the default application DB.

Run with the same socket-only disposable cluster used by the lease runner:

  BORIS_QA_POSTGRES_URL=<explicit-isolated-unix-socket-DSN> \
  BORIS_QA_CLUSTER_ROOT=/tmp/boris-email-qa-banner-UUID \
  python -B tests/test_premium_prepare_postgres.py --allow-isolated-cluster \
    --candidate-root <combined-candidate> --runtime-root /root/BORIS/backend \
    --report <prepare-postgres.json>

Three bounded cases use the actual jobs/clock/models/revision hooks and real
independent PostgreSQL connections. No full prepare handler or provider is run.
Receipt and quantity boundary answers are controlled fixtures; this suite proves
child UNIQUE-race preservation and final READY transaction concurrency, not image
quality, receipt correctness, provider billing, production rollout, or SLA.
"""
import argparse
import ast
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import uuid
from datetime import datetime, timezone

sys.dont_write_bytecode = True

from test_postgres_banner_lease import LeaseGate, require, source_identity, within


class PrepareGate(LeaseGate):
    def __init__(self, *, models, candidate_root, cluster_root, **kwargs):
        super().__init__(**kwargs)
        self.Campaign, self.CampaignItem, self.MediaAsset, self.MediaLink = models
        self.temp = tempfile.TemporaryDirectory(prefix="prepare-images-", dir=cluster_root)
        self.image_root = Path(self.temp.name)
        self.contract = importlib.import_module("app.services.campaign_banner_media_contract")
        self.original_file_check = self.contract.linked_file_current
        # Redirect only the location: actual path containment/SHA code still runs.
        self.contract.linked_file_current = lambda asset: self.original_file_check(asset, self.image_root)
        path = candidate_root / "app/api/campaigns.py"
        names = {"_ff_items", "_ff_payload", "_ff_state_load", "_ff_state_save",
                 "_ff_publication_locked", "_ff_order_event"}
        tree = ast.parse(path.read_text())
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        require({node.name for node in selected} == names, "required actual Campaign helper missing")
        env = {"json": json}
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), "exec"), env)
        self.C = SimpleNamespace(**{name: env[name] for name in names})
        self.ready = True
        self.C._ff_premium_banner_ready = lambda db, row, payload=None, **kw: self.ready
        self.C._ff_order_progress = lambda db, campaign, ff, account_id, **kw: {
            **dict(ff.get("order_contract") or {}),
            "requested_total": 2, "created_count": 2, "qa_pass_count": 2,
        }
        self.source_helpers = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                               "extracted_functions": sorted(names)}

    def close(self):
        self.contract.linked_file_current = self.original_file_check
        self.temp.cleanup()

    def seed_scope(self):
        with self.session() as db:
            campaign = self.Campaign(name="ISOLATED PREPARE QA", default_account_id=self.account_id,
                owner_user_id=1, status="draft", settings_json=json.dumps({"feed_factory": {
                    "step": "banners", "order_contract": {"requested_total": 2, "status": "PROCESSING"}}}))
            db.add(campaign); db.flush()
            ids = []
            for slot in range(2):
                row = self.CampaignItem(campaign_id=campaign.id, account_id=self.account_id,
                    status="draft", payload_json=json.dumps({"title": f"Current trusted text {slot}",
                        "description": "Current trusted description", "images": []}))
                db.add(row); db.flush(); ids.append(int(row.id))
                key = f"fixture-{uuid.uuid4().hex}.bin"
                raw = f"isolated file hash fixture {campaign.id}/{row.id}/{uuid.uuid4().hex}".encode()
                (self.image_root / key).write_bytes(raw)
                asset = self.MediaAsset(owner_user_id=1, account_id=self.account_id,
                    filename=key, storage_key=key, sha256=hashlib.sha256(raw).hexdigest(),
                    media_type="banner", source_provider="openai_full_ai_premium", tags=[])
                db.add(asset); db.flush()
                db.add(self.MediaLink(media_id=asset.id, entity_type="campaign_item",
                    entity_id=str(row.id), role="hero", position=0))
                row.hero_media_id = asset.id
            parent = self.BackgroundJob(kind="campaign_prepare", status="running", account_id=self.account_id,
                payload_json={"campaign_id": campaign.id, "account_id": self.account_id, "item_ids": ids,
                    "stage": "review_artifact", "_premium_prepare_intent_policy": "canonical_banner_child_v1",
                    "_execution_lease_token": uuid.uuid4().hex},
                progress_done=7, progress_total=8, attempts=1, started_at=datetime.now(timezone.utc))
            db.add(parent); db.commit()
            self.job_ids.append(int(parent.id))
            return int(parent.id), int(campaign.id), ids

    @contextmanager
    def owner(self, job_id, campaign_id):
        conn, db = self.acquire(job_id), self.session()
        guard = None
        try:
            job, campaign = db.get(self.BackgroundJob, job_id), db.get(self.Campaign, campaign_id)
            guard = self.clock.ExecutionClock(job, ownership_connection=conn, db=db)
            with guard:
                yield db, job, campaign
        finally:
            if guard is not None:
                guard.stop.set()
                if hasattr(guard, "thread"):
                    guard.thread.join(timeout=4)
                guard._remove_guards()
            db.rollback(); db.close(); conn.close()

    def independent_state(self, campaign_id, job_id):
        with self.session() as db:
            campaign = db.get(self.Campaign, campaign_id)
            job = db.get(self.BackgroundJob, job_id)
            rows = db.query(self.CampaignItem).filter_by(campaign_id=campaign_id).order_by(self.CampaignItem.id).all()
            return {"campaign_status": campaign.status, "ff": self.C._ff_state_load(campaign)[1],
                    "job_payload": dict(job.payload_json or {}), "job_done": job.progress_done,
                    "item_statuses": [r.status for r in rows],
                    "item_payloads": [self.C._ff_payload(r) for r in rows]}

    def unique_race_preserves_terminal_child(self):
        from sqlalchemy import event
        job_id, campaign_id, ids = self.seed_scope()
        fingerprint = f"campaign_banners:{campaign_id}:{self.account_id}"
        history = {"campaign_id": campaign_id, "item_ids": ids, "preserve_terminal_history": uuid.uuid4().hex,
                   "banner_item_intents": {str(i): f"original-known-image:{i}" for i in ids},
                   "banner_item_budget_intents": {str(i): f"original-budget:{i}" for i in ids}}
        winner_result = {"code": "ORIGINAL_UNKNOWN_IMAGE", "errors": []}
        race = {"fired": False}
        self.ready = False
        with self.owner(job_id, campaign_id) as (db, job, campaign):
            mutation_pid = self.backend(db)
            bound_connection = db.connection()
            def before_insert(conn, cursor, statement, params, context, executemany):
                if race["fired"] or not statement.lstrip().upper().startswith("INSERT INTO BACKGROUND_JOBS"):
                    return
                race["fired"] = True
                def concurrent_winner():
                    try:
                        with self.session() as other:
                            race["winner_backend_pid"] = self.backend(other)
                            child = self.BackgroundJob(kind="campaign_banners", status="completed",
                                account_id=self.account_id, fingerprint=fingerprint,
                                payload_json=history, result_json=winner_result,
                                finished_at=datetime.now(timezone.utc))
                            other.add(child); other.commit(); race["winner_job_id"] = int(child.id)
                    except BaseException as exc:
                        race["error"] = repr(exc)
                thread = threading.Thread(target=concurrent_winner)
                thread.start(); thread.join(timeout=5)
                require(not thread.is_alive(), "independent UNIQUE winner blocked unexpectedly")
                require("error" not in race, "independent winner failed: " + str(race.get("error")))
            event.listen(bound_connection, "before_cursor_execute", before_insert)
            try:
                result, status = self.jobs._campaign_premium_prepare_banner_gate(db, job,
                    dict(job.payload_json), self.C, campaign, SimpleNamespace(id=1),
                    [self.account_id], self.account_id, ids)
            finally:
                event.remove(bound_connection, "before_cursor_execute", before_insert)
            require(race["fired"], "test did not exercise INSERT UNIQUE conflict")
            require(race["winner_backend_pid"] != mutation_pid, "race did not use independent connection")
            require(status == "partial" and result["code"] == winner_result["code"],
                    "terminal child became a successful/requeued result")
            with self.session() as probe:
                children = probe.query(self.BackgroundJob).filter_by(fingerprint=fingerprint).all()
                require(len(children) == 1, "second canonical child was created")
                child = children[0]
                require(child.status == "completed" and child.payload_json == history
                        and child.result_json == winner_result, "terminal child economic history was overwritten")
                parent = probe.get(self.BackgroundJob, job_id)
                require(parent.payload_json["_premium_banner_child_job_id"] == child.id,
                        "parent wait checkpoint did not persist the winning child")
        self.ready = True
        return {"job_id": job_id, "campaign_id": campaign_id, **race,
                "mutation_backend_pid": mutation_pid, "real_unique_violation_caught": True,
                "single_terminal_child_unchanged": True, "parent_pointer_durable": True}

    def late_committed_edit_blocks_final_ready(self):
        job_id, campaign_id, ids = self.seed_scope()
        with self.owner(job_id, campaign_id) as (db, job, campaign):
            snapshot = self.jobs._campaign_premium_validation_snapshot(db, self.C, campaign,
                [self.account_id], self.account_id, ids)
            require(snapshot["ok"], "initial current snapshot failed")
            db.commit()  # actual external validation boundary has no open write locks
            # Hold an independent read-only checkout across the editor write.
            # The previous commit returned its connection to the pool; comparing
            # that historical PID would incorrectly reject legitimate pool reuse.
            mutation_pid = self.backend(db)
            with self.session() as editor:
                editor_pid = self.backend(editor)
                row = editor.get(self.CampaignItem, ids[0])
                edited = self.C._ff_payload(row); edited["description"] = "Later independently committed owner edit"
                row.payload_json = json.dumps(edited); editor.commit()
            require(editor_pid != mutation_pid, "late editor did not use independent connection")
            result = self.jobs._campaign_premium_finalize_ready(db, job, dict(job.payload_json), self.C,
                campaign, [self.account_id], self.account_id, ids, snapshot)
            require(result is not None and result[1] == "partial"
                    and result[0]["code"] == "PREMIUM_VALIDATED_REVISION_CHANGED", "late edit was promoted to READY")
            after = self.independent_state(campaign_id, job_id)
            require(after["item_statuses"] == ["draft", "draft"] and after["campaign_status"] == "draft",
                    "blocked finalization changed pending statuses")
            require(after["item_payloads"][0]["description"] == edited["description"], "owner edit was overwritten")
            require(after["job_done"] == 7 and after["ff"]["order_contract"]["status"] == "PROCESSING",
                    "blocked finalization leaked READY checkpoint/order")
        return {"job_id": job_id, "campaign_id": campaign_id, "mutation_backend_pid": mutation_pid,
                "editor_backend_pid": editor_pid, "committed_edit_preserved": True,
                "pending_statuses_preserved": True, "result_code": result[0]["code"]}

    def healthy_final_ready_is_atomic(self):
        from sqlalchemy import event
        job_id, campaign_id, ids = self.seed_scope()
        observed = []
        with self.owner(job_id, campaign_id) as (db, job, campaign):
            snapshot = self.jobs._campaign_premium_validation_snapshot(db, self.C, campaign,
                [self.account_id], self.account_id, ids)
            require(snapshot["ok"], "initial current snapshot failed")
            db.commit()
            def committed(_session):
                state = self.independent_state(campaign_id, job_id)
                require(state["item_statuses"] == ["ready", "ready"] and state["campaign_status"] == "ready_for_review"
                    and state["ff"]["order_contract"]["status"] == "READY_FOR_OWNER_REVIEW"
                    and state["job_done"] == 8 and state["job_payload"]["stage"] == "ready_for_review",
                    "another connection observed a partial READY transaction")
                observed.append(True)
            event.listen(db, "after_commit", committed)
            try:
                result = self.jobs._campaign_premium_finalize_ready(db, job, dict(job.payload_json), self.C,
                    campaign, [self.account_id], self.account_id, ids, snapshot)
            finally:
                event.remove(db, "after_commit", committed)
            require(result is None and observed, "healthy finalization never committed")
            require(self.jobs._job_execution_lock_is_held(job_id), "owner exact lock lost during final commit")
        return {"job_id": job_id, "campaign_id": campaign_id, "atomic_ready_visible": True,
                "observed_commits": len(observed), "actual_lease_guard_used": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-isolated-cluster", action="store_true")
    parser.add_argument("--cluster-root", type=Path, default=os.environ.get("BORIS_QA_CLUSTER_ROOT"),
                        help="temporary cluster root; defaults to BORIS_QA_CLUSTER_ROOT")
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = {"gate": "isolated_postgres_premium_prepare", "status": "FAIL", "tests": [],
              "provider_calls": 0, "avito_calls": 0, "notifications": 0,
              "full_run_once_executed": False, "claim_fixture": "isolated ORM parent with durable token; actual prepare helpers",
              "cleanup": "not_started"}
    bootstrap = engine = db_module = jobs = gate = None
    schema_created = False
    schema = "boris_qa_premium_prepare_" + uuid.uuid4().hex
    started = time.perf_counter()
    try:
        require(args.allow_isolated_cluster, "explicit --allow-isolated-cluster is required")
        require(args.cluster_root is not None, "--cluster-root or BORIS_QA_CLUSTER_ROOT is required")
        cluster_root = args.cluster_root.resolve()
        require(cluster_root.parent == Path("/tmp") and cluster_root.name.startswith("boris-"),
                "cluster root must be an immediate /tmp/boris-* directory")
        require(cluster_root.is_dir(), "cluster root does not exist")
        raw_url = os.environ.get("BORIS_QA_POSTGRES_URL", "")
        require(bool(raw_url), "BORIS_QA_POSTGRES_URL is required; no DATABASE_URL fallback is allowed")

        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
        from sqlalchemy.pool import NullPool

        url = make_url(raw_url)
        require(url.get_backend_name() == "postgresql", "only PostgreSQL is supported")
        host = url.query.get("host") or url.host
        require(isinstance(host, str) and host.startswith("/") and within(host, cluster_root),
                "DSN must use one Unix socket directory inside cluster root")
        require(not url.host or url.host == host, "TCP/mixed host DSNs are forbidden")
        require(bool(url.database), "explicit isolated database name required")
        bootstrap = create_engine(url, poolclass=NullPool)
        with bootstrap.connect() as conn:
            details = dict(conn.execute(text("""
                SELECT current_setting('data_directory') AS data_directory,
                       current_setting('listen_addresses') AS listen_addresses,
                       current_setting('unix_socket_directories') AS unix_socket_directories,
                       current_database() AS database, inet_server_addr() AS server_addr,
                       version() AS postgres_version,
                       to_regclass('public.background_jobs')::text AS public_jobs,
                       to_regclass('public.storage')::text AS public_storage
            """)).mappings().one())
            require(within(details["data_directory"], cluster_root), "database is outside supplied temporary cluster")
            require(details["listen_addresses"] == "" and details["server_addr"] is None,
                    "PostgreSQL must be socket-only (listen_addresses='')")
            require(details["public_jobs"] is None and details["public_storage"] is None,
                    "public application tables found; refusing nonempty application database")
            for socket_path in str(details["unix_socket_directories"]).split(","):
                socket_path = socket_path.strip().strip("'")
                require(socket_path.startswith("/") and within(socket_path, cluster_root),
                        "server exposes a Unix socket outside temporary cluster root")
        report["isolation"] = dict(details, cluster_root=str(cluster_root), schema=schema,
                                   public_fallback=False, public_template_used=False)

        with bootstrap.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        isolated_url = url.update_query_dict({"options": f"-c search_path={schema},pg_catalog -c statement_timeout=12000 -c lock_timeout=2000"})
        os.environ["DATABASE_URL"] = isolated_url.render_as_string(hide_password=False)
        os.environ["BORIS_RUNTIME_ROLE"] = "worker"
        os.environ["BORIS_DB_POOL_SIZE"] = "4"
        os.environ["BORIS_DB_MAX_OVERFLOW"] = "2"
        os.environ["BORIS_DB_APPLICATION_NAME"] = "boris-banner-isolated-prepare-gate"
        # Existing production .env is allowed to supply runtime import settings,
        # but python-dotenv must never override this already-set DATABASE_URL.
        candidate_root, runtime_root = args.candidate_root.resolve(), args.runtime_root.resolve()
        require(candidate_root != runtime_root, "candidate and runtime must be distinct paths")
        for name in ("jobs.py", "job_execution_clock.py"):
            require((candidate_root / "app/services" / name).is_file(), f"candidate {name} missing")
        require((runtime_root / "app/db/session.py").is_file(), "runtime dependency tree missing")

        def deny_network(event, event_args):
            if event == "socket.connect":
                address = event_args[1]
                require(isinstance(address, str) and within(address, cluster_root),
                        "outbound network is forbidden in prepare gate")
        sys.addaudithook(deny_network)
        sys.path.insert(0, str(runtime_root))
        sys.path.insert(0, str(candidate_root))
        app = importlib.import_module("app")
        if str(candidate_root / "app") not in app.__path__:
            app.__path__ = [str(candidate_root / "app"), *list(app.__path__)]
        services = importlib.import_module("app.services")
        if str(candidate_root / "app/services") not in services.__path__:
            services.__path__ = [str(candidate_root / "app/services"), *list(services.__path__)]
        clock = importlib.import_module("app.services.job_execution_clock")
        jobs = importlib.import_module("app.services.jobs")
        db_module = importlib.import_module("app.db.session")
        BackgroundJob = importlib.import_module("app.models.background_job").BackgroundJob
        Storage = importlib.import_module("app.models.storage").Storage
        Campaign = importlib.import_module("app.models.campaign").Campaign
        CampaignItem = importlib.import_module("app.models.campaign_item").CampaignItem
        MediaAsset = importlib.import_module("app.models.media_asset").MediaAsset
        MediaLink = importlib.import_module("app.models.media_link").MediaLink
        models = (BackgroundJob, Storage, Campaign, CampaignItem, MediaAsset, MediaLink)
        require(all(model.__table__.schema in (None, schema) for model in models),
                "application model forces external schema")
        require(BackgroundJob.__table__.schema in (None, schema) and Storage.__table__.schema in (None, schema),
                "model forces a schema outside this QA run")
        require(Path(clock.__file__).resolve() == candidate_root / "app/services/job_execution_clock.py",
                "clock import did not select candidate source")
        require(Path(jobs.__file__).resolve() == candidate_root / "app/services/jobs.py",
                "jobs import did not select candidate source")
        report["actual_sources"] = {"clock": source_identity(clock), "jobs": source_identity(jobs)}
        engine = create_engine(isolated_url, poolclass=NullPool)
        for label, checked_engine in (("gate", engine), ("app", db_module.engine),
                                      ("exact_lock", jobs._TENANT_SLOT_ENGINE)):
            with checked_engine.connect() as conn:
                search = conn.execute(text("SELECT current_schema(), current_setting('search_path'), current_database()" )).one()
                require(search[0] == schema and "public" not in search[1] and search[2] == url.database,
                        f"{label} connection escaped isolated schema")
        for model in models:
            model.__table__.create(engine, checkfirst=False)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE api_usage (id BIGSERIAL PRIMARY KEY, account_id TEXT, operation TEXT, provenance_json JSONB)"))
        sequences = {}
        with engine.begin() as conn:
            for table in ("background_jobs", "storage", "campaigns", "campaign_items", "media_assets", "media_links", "api_usage"):
                actual = conn.execute(text("SELECT to_regclass(:name)::oid = to_regclass(:qualified)::oid"),
                                      {"name": table, "qualified": f"{schema}.{table}"}).scalar_one()
                require(actual is True, f"unqualified {table} did not resolve to QA schema")
                seq = conn.execute(text("SELECT pg_get_serial_sequence(:table,'id')"),
                                   {"table": f"{schema}.{table}"}).scalar_one()
                require(bool(seq) and seq.startswith(schema + "."), f"{table} sequence not owned by QA schema")
                sequences[table] = seq
            # Advisory lock namespaces are database-wide, so concurrent QA
            # schemas must not accidentally use identical fixture job IDs.
            first_job_id = 7_000_000_000_000 + (uuid.uuid4().int % 10_000_000_000)
            conn.execute(text(f'ALTER SEQUENCE "{schema}".background_jobs_id_seq RESTART WITH {first_job_id}'))
        report["isolation"]["own_sequences"] = sequences
        report["isolation"]["first_qa_job_id"] = first_job_id
        report["isolation"]["unqualified_model_tables_verified"] = True

        gate = PrepareGate(jobs=jobs, clock=clock, db_module=db_module,
            BackgroundJob=BackgroundJob, Storage=Storage, engine=engine, text=text, schema=schema,
            models=(Campaign, CampaignItem, MediaAsset, MediaLink),
            candidate_root=candidate_root, cluster_root=cluster_root)
        report["actual_sources"]["campaign_helpers"] = gate.source_helpers
        report["actual_sources"]["media_contract"] = source_identity(gate.contract)
        report["qa_account_id"] = gate.account_id
        report["boundaries"] = {"premium_receipt": "controlled boolean fixture",
            "quantity_proof": "controlled two-card fixture", "file_hash": "actual helper; QA image root",
            "revision_hooks": "actual model-installed hooks", "providers": "not imported/called by handlers"}
        tests = ("unique_race_preserves_terminal_child", "late_committed_edit_blocks_final_ready",
                 "healthy_final_ready_is_atomic")
        for name in tests:
            began = time.perf_counter()
            result = getattr(gate, name)()
            report["tests"].append({"name": name, "status": "PASS",
                                    "elapsed_ms": round((time.perf_counter() - began) * 1000, 3), **result})
            print("PASS " + name, flush=True)
        report["qa_job_ids"] = gate.job_ids
        report["status"] = "PASS"
    except BaseException as exc:
        report["status"] = "FAIL"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        print("FAIL " + type(exc).__name__ + ": " + str(exc), file=sys.stderr, flush=True)
    finally:
        cleanup_errors = []
        if gate is not None:
            try:
                gate.close()
            except BaseException as exc:
                cleanup_errors.append(type(exc).__name__ + ": " + str(exc))
        for owned_engine in (engine, getattr(db_module, "engine", None), getattr(jobs, "_TENANT_SLOT_ENGINE", None)):
            if owned_engine is not None:
                try:
                    owned_engine.dispose()
                except BaseException as exc:
                    cleanup_errors.append(type(exc).__name__ + ": " + str(exc))
        if schema_created and bootstrap is not None:
            try:
                from sqlalchemy import text
                with bootstrap.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout='3s'"))
                    conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                    require(conn.execute(text("SELECT to_regnamespace(:schema) IS NULL"),
                                         {"schema": schema}).scalar_one(), "QA schema survived cleanup")
                report["cleanup"] = "schema_removed"
            except BaseException as exc:
                cleanup_errors.append(type(exc).__name__ + ": " + str(exc))
        elif not schema_created:
            report["cleanup"] = "no_schema_created"
        if bootstrap is not None:
            bootstrap.dispose()
        if cleanup_errors:
            report["status"] = "FAIL"
            report["cleanup"] = "FAILED"
            report["cleanup_errors"] = cleanup_errors
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
