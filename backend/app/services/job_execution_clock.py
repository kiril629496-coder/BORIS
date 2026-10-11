"""Persistent execution-clock intervals for the canonical background_jobs worker."""
import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

CURRENT_CLOCK = ContextVar("campaign_execution_clock", default=None)


class ExecutionOwnershipLost(RuntimeError):
    """The former worker must roll back, never persist a new job outcome."""


def _assert_lease_row(db, *, job_id, account_id, token, lock_job):
    from sqlalchemy import text
    suffix = " FOR UPDATE" if lock_job else ""
    with db.no_autoflush:
        row = db.execute(text(
            "SELECT status, account_id, payload_json->>'_execution_lease_token' AS lease_token "
            "FROM background_jobs WHERE id=:job_id" + suffix
        ), {"job_id": int(job_id)}).mappings().first()
    if (not row or str(row.get("lease_token") or "") != str(token)
            or row.get("status") not in {"running", "validating"}
            or str(row.get("account_id") or "__system__") != str(account_id)):
        raise ExecutionOwnershipLost("EXECUTION_LEASE_REPLACED_OR_CANCELLED")


def assert_current_execution_owner(db=None):
    """Fence the active durable execution; HTTP callers have no worker lease.

    Pass the mutation's Session before taking item locks/writing. Its locked job
    row keeps cancellation/recovery serialized until that transaction commits.
    Without a Session this is a read-only ownership check, not a commit fence.
    """
    clock = CURRENT_CLOCK.get()
    return clock.assert_current_owner(db) if clock is not None else False

def now():
    return datetime.now(timezone.utc)

def transition(state, mode, at):
    """Pure interval reducer. Queue/provider delays never enter active_ms."""
    state = dict(state or {})
    previous = state.get("at")
    if previous:
        elapsed = max(0.0, (at - datetime.fromisoformat(previous)).total_seconds() * 1000)
        key = "active_ms" if state.get("mode") == "active" else "provider_ms"
        if state.get("mode") in {"active", "provider"}:
            state[key] = max(0.0, float(state.get(key) or 0)) + elapsed
    state.update(mode=mode, at=at.isoformat(), heartbeat_at=at.isoformat(), version=1)
    return state

def _save(job_id, account_id, state, lease_token=None):
    from sqlalchemy import text
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    with SessionLocal() as db:
        key = f"job_execution_clock:{job_id}"
        db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 3471))"), {"k": key})
        if lease_token is not None:
            # Do not take the handler's job-row lock from the heartbeat thread:
            # it can be held by the same handler's still-open transaction.
            _assert_lease_row(db, job_id=job_id, account_id=account_id,
                              token=lease_token, lock_job=False)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        if row is None:
            row = Storage(account_id=account_id, key=key)
            db.add(row)
        row.value = json.dumps(state)
        db.commit()

def load(job):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    with SessionLocal() as db:
        row = db.query(Storage).filter(Storage.account_id == str(job.account_id or "__system__"),
                                      Storage.key == f"job_execution_clock:{job.id}").first()
        return json.loads(row.value) if row else None

def summary(job, state=None, at=None):
    at = at or now()
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value and not value.tzinfo else value
    created, finished = utc(job.created_at), utc(job.finished_at)
    end = finished or at
    total = max(0.0, (end - created).total_seconds()) if created else 0.0
    if state is None:
        state = load(job)
    # Old jobs have no measured active intervals; never invent historical timing.
    active = provider = None
    if state:
        view = dict(state)
        recorded = datetime.fromisoformat(view["at"])
        if view.get("mode") in {"active", "provider"} and job.status in {"running", "validating"}:
            # Only extend a live heartbeat. A dead worker's downtime is waiting.
            until = end if 0 <= (end-recorded).total_seconds() <= 2 else recorded
            view = transition(view, view["mode"], max(recorded, until))
        active = min(total, max(0.0, float(view.get("active_ms") or 0) / 1000))
        provider = min(total - active, max(0.0, float(view.get("provider_ms") or 0) / 1000))
    waiting = max(0.0, total - active - provider) if active is not None and provider is not None else None
    return {"duration_total_seconds": round(total, 3),
            "duration_active_seconds": round(active, 3) if active is not None else None,
            "duration_waiting_seconds": round(waiting, 3) if waiting is not None else None,
            "duration_provider_seconds": round(provider, 3) if provider is not None else None,
            "timing_contract": 2 if state else 0}

def _job_provenance(job):
    """Compact durable execution provenance safe for the api_usage ledger."""
    payload = dict(getattr(job, "payload_json", None) or {})
    job_kind = str(getattr(job, "kind", "") or "")[:96]
    stage = str(payload.get("stage") or "")[:96]
    if stage in {"", "queued"}:
        stage = {
            "campaign_banners": "banners",
            "campaign_media_shard": "media",
            "campaign_enhance": "enhance",
            "campaign_deliver": "delivery",
            "campaign_inventory_maintain": "inventory",
            "campaign_editor": "editor",
        }.get(job_kind, stage)
    out = {
        "attribution_contract": 2,
        "account_id": str(getattr(job, "account_id", None) or "__system__"),
        "job_id": int(getattr(job, "id", 0) or 0),
        "job_kind": job_kind,
        "stage": stage,
    }
    try:
        campaign_id = int(payload.get("campaign_id") or 0)
    except (TypeError, ValueError):
        campaign_id = 0
    if campaign_id > 0:
        out["campaign_id"] = campaign_id

    raw_item_ids = payload.get("item_ids")
    item_ids = []
    if isinstance(raw_item_ids, (list, tuple)):
        for raw in raw_item_ids:
            try:
                item_id = int(raw)
            except (TypeError, ValueError):
                continue
            if item_id > 0:
                item_ids.append(item_id)
    direct_item = payload.get("campaign_item_id") or payload.get("item_id")
    if direct_item:
        try:
            item_id = int(direct_item)
        except (TypeError, ValueError):
            item_id = 0
        if item_id > 0:
            out["campaign_item_id"] = item_id
    elif len(item_ids) == 1:
        out["campaign_item_id"] = item_ids[0]
    elif item_ids:
        out["item_scope_count"] = len(item_ids)
    return out


def current_job_provenance():
    clock = CURRENT_CLOCK.get()
    if clock is None:
        return {}
    base = dict(getattr(clock, "provenance", None) or {})
    job = getattr(clock, "job", None)
    if job is not None:
        # Campaign checkpoints mutate the same durable job payload as the work
        # advances through texts/media/banners. Read that live payload at the
        # moment usage is recorded so cost provenance carries the real stage,
        # not only the initial queued/preflight snapshot.
        base.update(_job_provenance(job))
    return base


class ExecutionClock:
    def __init__(self, job, *, ownership_connection=None, db=None):
        self.job = job
        self.job_id = int(job.id)
        self.account_id = str(job.account_id or "__system__")
        self.provenance = _job_provenance(job)
        self.state = load(job) or {}
        # Previous unfinished segment stops at its persisted heartbeat on recovery.
        self.state["mode"] = "waiting"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.depth = 0
        self.error = None
        self.db = db
        self.ownership_connection = ownership_connection
        self.ownership_required = bool(
            ownership_connection is not None or db is not None
            or str(getattr(job, "kind", "") or "") in {
                "campaign_editor", "campaign_prepare", "campaign_enhance",
                "campaign_banners", "campaign_media_shard",
            }
        )
        self.lease_token = str((getattr(job, "payload_json", None) or {}).get("_execution_lease_token") or "")
        self.owner_backend_pid = None
        self.listeners = []
        if self.ownership_required:
            if ownership_connection is None or not self.lease_token:
                raise ExecutionOwnershipLost("EXECUTION_OWNERSHIP_MISSING")
            try:
                if ownership_connection.closed or ownership_connection.invalidated:
                    raise ExecutionOwnershipLost("EXECUTION_OWNER_CONNECTION_LOST")
                owner = dict(ownership_connection.info.get("boris_execution_owner") or {})
                if int(owner.get("job_id") or 0) != self.job_id:
                    raise ExecutionOwnershipLost("EXECUTION_OWNER_JOB_MISMATCH")
                self.owner_backend_pid = int(owner.get("backend_pid") or 0)
                if self.owner_backend_pid <= 0:
                    raise ExecutionOwnershipLost("EXECUTION_OWNER_BACKEND_UNKNOWN")
            except ExecutionOwnershipLost:
                raise
            except Exception as exc:
                raise ExecutionOwnershipLost("EXECUTION_OWNER_CONNECTION_UNKNOWN") from exc

    def assert_current_owner(self, db=None):
        if not self.ownership_required:
            return False
        from sqlalchemy import text
        with self.lock:
            try:
                conn = self.ownership_connection
                if conn is None or conn.closed or conn.invalidated:
                    raise ExecutionOwnershipLost("EXECUTION_OWNER_CONNECTION_LOST")
                # A new/reconnected backend is never allowed to re-acquire the
                # old lease implicitly. Observe the exact existing advisory lock.
                row = conn.execute(text("""
                    SELECT pg_backend_pid() AS backend_pid,
                           EXISTS (
                               SELECT 1 FROM pg_locks l
                               WHERE l.pid=pg_backend_pid()
                                 AND l.locktype='advisory' AND l.granted
                                 AND l.objsubid=1
                                 AND l.classid::bigint=((hashtextextended(:key,744619) >> 32) & 4294967295)
                                 AND l.objid::bigint=(hashtextextended(:key,744619) & 4294967295)
                           ) AS held
                """), {"key": f"boris:background-job:{self.job_id}"}).mappings().first()
                conn.commit()
                if (not row or int(row.get("backend_pid") or 0) != self.owner_backend_pid
                        or row.get("held") is not True):
                    raise ExecutionOwnershipLost("EXECUTION_EXACT_LOCK_LOST")
                if db is not None:
                    _assert_lease_row(db, job_id=self.job_id, account_id=self.account_id,
                                      token=self.lease_token, lock_job=True)
                else:
                    from app.db.session import SessionLocal
                    with SessionLocal() as probe:
                        _assert_lease_row(probe, job_id=self.job_id, account_id=self.account_id,
                                          token=self.lease_token, lock_job=False)
                return True
            except ExecutionOwnershipLost:
                raise
            except Exception as exc:
                raise ExecutionOwnershipLost("EXECUTION_OWNERSHIP_UNPROVEN") from exc

    def _before_flush(self, session, *args):
        self.assert_current_owner(session)

    def _before_commit(self, session):
        self.assert_current_owner(session)

    def _before_bulk_write(self, state):
        if state.is_update or state.is_delete:
            self.assert_current_owner(state.session)

    def _install_guards(self):
        if self.db is None:
            return
        from sqlalchemy import event
        for name, callback in (("before_flush", self._before_flush),
                               ("before_commit", self._before_commit),
                               ("do_orm_execute", self._before_bulk_write)):
            event.listen(self.db, name, callback)
            self.listeners.append((name, callback))

    def _remove_guards(self):
        if self.db is None:
            return
        from sqlalchemy import event
        for name, callback in self.listeners:
            event.remove(self.db, name, callback)
        self.listeners = []

    def mark(self, mode):
        with self.lock:
            self.assert_current_owner()
            self.state = transition(self.state, mode, now())
            _save(self.job_id, self.account_id, self.state,
                  lease_token=self.lease_token if self.ownership_required else None)

    def heartbeat(self):
        while not self.stop.wait(0.5):
            try:
                with self.lock:
                    self.mark(self.state.get("mode", "active"))
            except Exception as exc:
                self.error = exc
                return

    def __enter__(self):
        self.mark("active")
        self._install_guards()
        self.token = CURRENT_CLOCK.set(self)
        self.thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, tb):
        self.stop.set()
        self.thread.join(timeout=3)
        CURRENT_CLOCK.reset(self.token)
        self._remove_guards()
        if isinstance(self.error, ExecutionOwnershipLost):
            raise self.error
        self.mark("waiting")
        if self.error is not None and kind is None:
            raise RuntimeError("Execution timing checkpoint failed") from self.error

@contextmanager
def provider_wait():
    clock = CURRENT_CLOCK.get()
    if clock is None:
        yield
        return
    with clock.lock:
        clock.depth += 1
        if clock.depth == 1:
            clock.mark("provider")
    try:
        yield
    finally:
        with clock.lock:
            clock.depth -= 1
            if not clock.depth:
                clock.mark("active")

def provider_call(fn, *args, **kwargs):
    with provider_wait():
        return fn(*args, **kwargs)
