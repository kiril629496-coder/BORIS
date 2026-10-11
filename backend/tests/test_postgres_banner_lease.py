#!/usr/bin/env python3
"""Opt-in real PostgreSQL gate; ordinary unittest discovery does not run it.

Run with the application's Python/venv (SQLAlchemy + PostgreSQL driver):

  BORIS_QA_POSTGRES_URL='postgresql+psycopg2://postgres@/postgres?host=/tmp/boris-qa-UUID/socket&port=55439' \
    /root/BORIS/backend/venv/bin/python tests/test_postgres_banner_lease.py \
    --allow-isolated-cluster --cluster-root /tmp/boris-qa-UUID \
    --candidate-root /tmp/combined-banner-candidate \
    --runtime-root /root/BORIS/backend --report /tmp/banner-lease-gate.json

The caller owns initdb/pg_ctl lifecycle. This runner accepts ONLY a socket-only
temporary cluster under the supplied /tmp/boris-* root. It checks the real
PostgreSQL data directory before importing app code or creating anything. It
creates two actual model tables in one unique schema, with its own sequences,
and removes that schema in finally. No public application table/template or
default DATABASE_URL is used. No run_once, banner handler, provider, notification,
or publication function is invoked. Claims below are isolated ORM test fixtures;
ownership SQL, Session hooks, exact lock acquisition/probe and startup recovery
are the actual candidate implementation, without mocks.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def within(path, root):
    try:
        Path(path).resolve().relative_to(root)
        return True
    except (ValueError, TypeError):
        return False


def source_identity(module):
    path = Path(module.__file__).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class LeaseGate:
    def __init__(self, *, jobs, clock, db_module, BackgroundJob, Storage, engine, text, schema):
        self.jobs, self.clock, self.db_module = jobs, clock, db_module
        self.BackgroundJob, self.Storage = BackgroundJob, Storage
        self.engine, self.text, self.schema = engine, text, schema
        self.account_id = "qa-banner-lease:" + uuid.uuid4().hex
        self.job_ids = []

    def session(self):
        return self.db_module.SessionLocal()

    def seed(self, *, old=False):
        """Fixture claim, with durable token + banner intent before work starts."""
        token = uuid.uuid4().hex
        stamp = datetime.now(timezone.utc)
        with self.session() as db:
            job = self.BackgroundJob(
                kind="campaign_banners", status="queued", account_id=self.account_id,
                payload_json={"campaign_id": 9000001, "item_ids": [9000002]},
                progress_done=0, progress_total=1, attempts=0,
            )
            db.add(job)
            db.commit()
            job_id = int(job.id)
            self.job_ids.append(job_id)
        self.claim(job_id, token, started_at=stamp - timedelta(seconds=600) if old else stamp)
        return job_id, token

    def claim(self, job_id, token, *, started_at=None):
        with self.session() as db:
            job = (db.query(self.BackgroundJob)
                   .filter(self.BackgroundJob.id == job_id,
                           self.BackgroundJob.account_id == self.account_id,
                           self.BackgroundJob.status == "queued")
                   .with_for_update(skip_locked=True).one())
            payload = dict(job.payload_json or {})
            payload.setdefault("banner_item_intents", {"9000002": f"campaign_banners:{job_id}:item:9000002:r0"})
            payload.setdefault("banner_item_budget_intents", {"9000002": f"campaign_banners:{job_id}:item:9000002"})
            payload.setdefault("quality_retry_counts", {"9000002": 0})
            payload["_execution_lease_token"] = token
            payload["_execution_lease_created_at"] = datetime.now(timezone.utc).isoformat()
            job.payload_json = payload
            job.status = "running"
            job.started_at = started_at or datetime.now(timezone.utc)
            job.finished_at = None
            db.commit()

    def row(self, job_id):
        with self.session() as db:
            job = db.get(self.BackgroundJob, job_id)
            return {"status": job.status, "payload": dict(job.payload_json or {}),
                    "started_at": job.started_at}

    def retire(self, job_id):
        with self.session() as db:
            job = db.get(self.BackgroundJob, job_id)
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()

    def acquire(self, job_id):
        conn = self.jobs._TENANT_SLOT_ENGINE.connect()
        try:
            require(self.jobs._acquire_job_execution_lock(conn, job_id), "actual exact lock acquisition failed")
            return conn
        except BaseException:
            conn.close()
            raise

    def canary_exists(self, key):
        with self.session() as db:
            return bool(db.query(self.Storage).filter(
                self.Storage.account_id == self.account_id, self.Storage.key == key).first())

    def put_canary(self, db, key):
        db.add(self.Storage(account_id=self.account_id, key=key, value="isolated business write"))

    def expect_stale_commit_blocked(self, db, key):
        self.put_canary(db, key)
        try:
            db.commit()
        except self.clock.ExecutionOwnershipLost as exc:
            error_code = str(exc)
            db.rollback()
        else:
            raise AssertionError("stale business transaction committed")
        require(not self.canary_exists(key), "stale business canary was persisted")
        return error_code

    def backend(self, db):
        return int(db.execute(self.text("SELECT pg_backend_pid()")).scalar_one())

    def valid_owner_heartbeat_and_final_commit(self):
        job_id, token = self.seed()
        conn = self.acquire(job_id)
        db = self.session()
        guard = None
        try:
            job = db.get(self.BackgroundJob, job_id)
            mutation_pid = self.backend(db)
            owner_pid = int(conn.info["boris_execution_owner"]["backend_pid"])
            require(mutation_pid != owner_pid, "lock and mutation connections are not independent")
            guard = self.clock.ExecutionClock(job, ownership_connection=conn, db=db)
            with guard:
                require(self.clock.assert_current_execution_owner(db), "context owner guard did not authorize owner")
                before = guard.state["heartbeat_at"]
                self.put_canary(db, "valid-owner")
                # Handler holds the job row FOR UPDATE here. The real heartbeat
                # must still write Storage from another connection without deadlock.
                time.sleep(1.2)
                with self.session() as probe:
                    state_row = probe.query(self.Storage).filter(
                        self.Storage.account_id == self.account_id,
                        self.Storage.key == f"job_execution_clock:{job_id}").one()
                    heartbeat = json.loads(state_row.value)["heartbeat_at"]
                require(heartbeat > before, "heartbeat stalled while handler held its own job row")
                require(guard.error is None, "heartbeat reported an error while valid handler held job row")
                db.commit()
            require(self.canary_exists("valid-owner"), "valid owner write did not commit")

            samples = []
            for _ in range(12):
                start = time.perf_counter()
                guard.assert_current_owner(db)
                db.rollback()
                samples.append((time.perf_counter() - start) * 1000.0)

            # Mirrors the actual worker's post-handler boundary: explicit owner
            # check, final result commit, then exact connection release.
            guard.assert_current_owner(db)
            job.status = "completed"
            job.result_json = {"gate": "valid_owner_final_commit"}
            db.commit()
            require(self.row(job_id)["status"] == "completed", "final outcome not committed")
            require(self.jobs._job_execution_lock_is_held(job_id), "exact lock was released before final outcome commit")
            conn.close()
            require(not self.jobs._job_execution_lock_is_held(job_id), "exact lock survived owner connection close")
            return {"job_id": job_id, "mutation_backend_pid": mutation_pid,
                    "owner_backend_pid": owner_pid, "heartbeat_progress_while_job_row_locked": True,
                    "final_commit_before_lock_release": True,
                    "guard_latency_ms": {"samples": len(samples), "median": round(statistics.median(samples), 3),
                                         "p95": round(sorted(samples)[-1], 3), "max": round(max(samples), 3)}}
        finally:
            if guard is not None:
                guard.stop.set()
                if hasattr(guard, "thread"):
                    guard.thread.join(timeout=4)
                guard._remove_guards()
            db.rollback()
            db.close()
            conn.close()

    def lock_held_blocks_actual_startup_recovery(self):
        job_id, token = self.seed(old=True)
        before = self.row(job_id)["payload"]
        conn = self.acquire(job_id)
        try:
            self.jobs.recover_interrupted_campaign_jobs()
            require(self.row(job_id)["status"] == "running", "startup recovery stole held exact lock")
            conn.close()
            self.jobs.recover_interrupted_campaign_jobs()
            after = self.row(job_id)
            require(after["status"] == "queued", "orphan did not recover after exact lock release")
            require(after["payload"].get("_recovery_count") == 1, "actual recovery counter wrong")
            for field in ("banner_item_intents", "banner_item_budget_intents", "quality_retry_counts"):
                require(after["payload"][field] == before[field], f"recovery changed {field}")
            return {"job_id": job_id, "actual_recovery_blocked_while_exact_lock_held": True,
                    "actual_recovery_after_release": True, "economic_identity_preserved": True}
        finally:
            conn.close()
            self.retire(job_id)

    def advanced_token_blocks_real_session_commit(self):
        job_id, token = self.seed()
        conn, db = self.acquire(job_id), self.session()
        guard = None
        try:
            job = db.get(self.BackgroundJob, job_id)
            old_pid = self.backend(db)
            guard = self.clock.ExecutionClock(job, ownership_connection=conn, db=db)
            guard._install_guards()
            replacement_token = uuid.uuid4().hex
            with self.session() as replacement:
                replacement_pid = self.backend(replacement)
                require(replacement_pid != old_pid, "replacement did not use independent database connection")
                row = replacement.query(self.BackgroundJob).filter_by(id=job_id).with_for_update().one()
                row.payload_json = dict(row.payload_json, _execution_lease_token=replacement_token)
                replacement.commit()
            reason = self.expect_stale_commit_blocked(db, "old-token")
            after = self.row(job_id)
            require(after["payload"]["_execution_lease_token"] == replacement_token,
                    "stale write overwrote replacement token")
            return {"job_id": job_id, "old_mutation_backend_pid": old_pid,
                    "replacement_backend_pid": replacement_pid, "stale_commit_blocked": True,
                    "error_code": reason}
        finally:
            if guard is not None:
                guard._remove_guards()
            db.rollback()
            db.close()
            conn.close()
            self.retire(job_id)

    def job_row_fence_serializes_replacement(self):
        job_id, token = self.seed()
        conn, db = self.acquire(job_id), self.session()
        guard, thread = None, None
        replacement = {"pid": None, "error": None, "committed": False}
        ready = threading.Event()
        replacement_token = uuid.uuid4().hex

        def replace():
            try:
                with self.session() as other:
                    replacement["pid"] = self.backend(other)
                    ready.set()
                    row = other.query(self.BackgroundJob).filter_by(id=job_id).with_for_update().one()
                    row.payload_json = dict(row.payload_json, _execution_lease_token=replacement_token)
                    other.commit()
                    replacement["committed"] = True
            except BaseException as exc:
                replacement["error"] = type(exc).__name__ + ": " + str(exc)
                ready.set()

        try:
            guard = self.clock.ExecutionClock(db.get(self.BackgroundJob, job_id), ownership_connection=conn, db=db)
            guard._install_guards()
            guard.assert_current_owner(db)
            self.put_canary(db, "serialized-owner")
            thread = threading.Thread(target=replace, daemon=True)
            thread.start()
            require(ready.wait(2), "replacement connection did not start")
            observed_lock_wait = False
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                with self.engine.connect() as probe:
                    wait = probe.execute(self.text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                                         {"pid": replacement["pid"]}).scalar_one_or_none()
                if wait == "Lock":
                    observed_lock_wait = True
                    break
                time.sleep(0.025)
            require(observed_lock_wait, "independent replacement did not wait on owner job-row fence")
            require(not replacement["committed"], "replacement committed through owner's locked job row")
            db.commit()
            thread.join(timeout=4)
            require(not thread.is_alive(), "replacement deadlocked after owner transaction committed")
            require(replacement["error"] is None, "replacement failed: " + str(replacement["error"]))
            require(replacement["committed"], "replacement never committed")
            require(self.canary_exists("serialized-owner"), "prior valid owner transaction was lost")
            reason = self.expect_stale_commit_blocked(db, "serialized-stale")
            return {"job_id": job_id, "replacement_backend_pid": replacement["pid"],
                    "real_postgres_lock_wait_observed": True, "owner_committed_before_replacement": True,
                    "subsequent_stale_transaction_blocked": True, "error_code": reason}
        finally:
            if guard is not None:
                guard._remove_guards()
            db.rollback()
            if thread is not None:
                thread.join(timeout=4)
            db.close()
            conn.close()
            self.retire(job_id)

    def lost_exact_lock_is_not_reacquired(self):
        job_id, token = self.seed()
        conn, db = self.acquire(job_id), self.session()
        guard = None
        try:
            guard = self.clock.ExecutionClock(db.get(self.BackgroundJob, job_id), ownership_connection=conn, db=db)
            guard._install_guards()
            owner_pid = guard.owner_backend_pid
            unlocked = conn.execute(self.text("SELECT pg_advisory_unlock(hashtextextended(:key,744619))"),
                                    {"key": f"boris:background-job:{job_id}"}).scalar_one()
            conn.commit()
            require(unlocked is True, "failed to release the fixture's own exact lock")
            require(not self.jobs._job_execution_lock_is_held(job_id), "fixture exact lock still held")
            reason = self.expect_stale_commit_blocked(db, "lost-exact-lock")
            require(not self.jobs._job_execution_lock_is_held(job_id), "owner guard silently reacquired lost lock")
            current_pid = int(conn.execute(self.text("SELECT pg_backend_pid()")).scalar_one())
            conn.commit()
            require(current_pid == owner_pid, "this test unexpectedly changed physical backend")
            return {"job_id": job_id, "same_live_backend_pid": owner_pid,
                    "missing_lock_blocks_commit": True, "no_implicit_reacquisition": True,
                    "error_code": reason}
        finally:
            if guard is not None:
                guard._remove_guards()
            db.rollback()
            db.close()
            conn.close()
            self.retire(job_id)

    def connection_death_recovery_and_new_owner(self):
        job_id, token = self.seed(old=True)
        conn, db = self.acquire(job_id), self.session()
        guard, next_guard, next_conn, next_db = None, None, None, None
        try:
            guard = self.clock.ExecutionClock(db.get(self.BackgroundJob, job_id), ownership_connection=conn, db=db)
            guard._install_guards()
            old_pid = guard.owner_backend_pid
            mutation_pid = self.backend(db)
            require(old_pid != mutation_pid, "termination would kill mutation session too")
            with self.engine.begin() as killer:
                # This PID was just captured from our own isolated-cluster
                # acquisition. Never terminate a user/production backend.
                terminated = killer.execute(self.text("SELECT pg_terminate_backend(:pid)"), {"pid": old_pid}).scalar_one()
            require(terminated is True, "could not terminate fixture ownership backend")
            deadline = time.monotonic() + 2
            while self.jobs._job_execution_lock_is_held(job_id) and time.monotonic() < deadline:
                time.sleep(0.025)
            require(not self.jobs._job_execution_lock_is_held(job_id), "terminated owner retained exact lock")
            reason = self.expect_stale_commit_blocked(db, "dead-owner-before-recovery")
            require(conn.invalidated or conn.closed, "dead owner connection was not marked unusable")
            self.jobs.recover_interrupted_campaign_jobs()
            require(self.row(job_id)["status"] == "queued", "real recovery did not recover dead owner")
            new_token = uuid.uuid4().hex
            self.claim(job_id, new_token)
            next_conn, next_db = self.acquire(job_id), self.session()
            next_guard = self.clock.ExecutionClock(next_db.get(self.BackgroundJob, job_id),
                                                   ownership_connection=next_conn, db=next_db)
            next_guard._install_guards()
            self.put_canary(next_db, "new-owner-after-death")
            next_db.commit()
            require(self.canary_exists("new-owner-after-death"), "new valid owner could not commit")
            self.expect_stale_commit_blocked(db, "dead-owner-after-reclaim")
            # Even an explicit SQLAlchemy reconnection cannot make the old
            # clock valid: backend identity is frozen at original acquisition.
            conn.rollback()
            reconnected_pid = int(conn.execute(self.text("SELECT pg_backend_pid()")).scalar_one())
            conn.commit()
            require(reconnected_pid != old_pid, "terminated backend unexpectedly reused")
            reconnect_reason = self.expect_stale_commit_blocked(db, "reconnected-old-clock")
            require(self.row(job_id)["payload"]["_execution_lease_token"] == new_token,
                    "old worker changed fresh claim token")
            require(self.jobs._job_execution_lock_is_held(job_id), "new owner lost its own exact lock")
            return {"job_id": job_id, "terminated_owner_pid": old_pid,
                    "surviving_mutation_pid": mutation_pid, "reconnected_old_connection_pid": reconnected_pid,
                    "new_owner_pid": next_guard.owner_backend_pid, "actual_recovery_used": True,
                    "old_write_blocked_before_and_after_reclaim": True, "new_owner_write_committed": True,
                    "implicit_lock_reacquisition": False, "death_error_code": reason,
                    "reconnected_old_clock_error_code": reconnect_reason}
        finally:
            for item in (guard, next_guard):
                if item is not None:
                    item._remove_guards()
            for item in (db, next_db):
                if item is not None:
                    item.rollback()
                    item.close()
            for item in (conn, next_conn):
                if item is not None:
                    item.close()
            self.retire(job_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-isolated-cluster", action="store_true")
    parser.add_argument("--cluster-root", type=Path, default=os.environ.get("BORIS_QA_CLUSTER_ROOT"),
                        help="temporary cluster root; defaults to BORIS_QA_CLUSTER_ROOT")
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = {"gate": "isolated_postgres_banner_lease", "status": "FAIL", "tests": [],
              "provider_calls": 0, "avito_calls": 0, "notifications": 0,
              "full_run_once_executed": False, "claim_fixture": "real ORM FOR UPDATE SKIP LOCKED",
              "cleanup": "not_started"}
    bootstrap = engine = db_module = jobs = None
    schema_created = False
    schema = "boris_qa_banner_lease_" + uuid.uuid4().hex
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
        os.environ["BORIS_DB_APPLICATION_NAME"] = "boris-banner-isolated-lease-gate"
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
                        "outbound network is forbidden in lease gate")
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
        BackgroundJob.__table__.create(engine, checkfirst=False)
        Storage.__table__.create(engine, checkfirst=False)
        sequences = {}
        with engine.begin() as conn:
            for table in ("background_jobs", "storage"):
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

        gate = LeaseGate(jobs=jobs, clock=clock, db_module=db_module, BackgroundJob=BackgroundJob,
                         Storage=Storage, engine=engine, text=text, schema=schema)
        report["qa_account_id"] = gate.account_id
        tests = ("valid_owner_heartbeat_and_final_commit", "lock_held_blocks_actual_startup_recovery",
                 "advanced_token_blocks_real_session_commit", "job_row_fence_serializes_replacement",
                 "lost_exact_lock_is_not_reacquired", "connection_death_recovery_and_new_owner")
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
