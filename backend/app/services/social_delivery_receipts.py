"""Durable Social publication receipts. No schema creation and no sends on import."""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

PROTOCOL = "SOCIAL_RECEIPT_V1"
_ACTIVE_RECEIPT_JOURNAL = ContextVar("active_social_receipt_journal", default=None)


def current_receipt_connection():
    """Borrow an idle locked journal connection for short VK cooldown sessions.

    The borrower owns only its ORM transaction; the journal retains the
    connection and its session-level advisory locks. Never reconnect or join
    an unfinished journal transaction after an active-owner failure.
    """
    journal = _ACTIVE_RECEIPT_JOURNAL.get()
    if journal is None or journal._connection_token is None:
        return None
    conn = journal._require_connection()
    if conn.in_transaction():
        raise RuntimeError("SOCIAL_RECEIPT_TRANSACTION_ACTIVE")
    return conn


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeliveryIntent:
    tenant: str
    platform: str
    target: str
    step: str
    text: str
    asset_sha256: str = ""
    context: dict | None = None

    @property
    def payload_hash(self) -> str:
        return digest({"text": self.text, "asset": self.asset_sha256})

    @property
    def identity(self) -> dict:
        context = self.context or {}
        # An existing lifecycle cannot escape uncertain by editing its payload.
        lifecycle = str(context.get("post_id") or context.get("generation_key") or "")
        return {
            "tenant": str(self.tenant), "platform": str(self.platform),
            "target": str(self.target), "step": self.step,
            "lifecycle": lifecycle or "content:" + self.payload_hash,
        }

    @property
    def key(self) -> str:
        return "social_delivery:v1:" + digest(self.identity)

    @property
    def legacy(self) -> bool:
        c = self.context or {}
        return bool(c.get("post_id") and c.get("protocol") != PROTOCOL)


def canonical_destination(platform, target):
    return str(int(target)) if platform == "vk" else str(target).strip().lower()


def _capacity_datetime(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return out.astimezone(timezone.utc) if out.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def capacity_decision(*, account, platform, target, context, projects, posts, receipts, now):
    """One logical post per destination; preserve today's undelivered slots.

    Called from a fresh snapshot while the global destination lock is held.
    No provider calls, writes, new quotas, or inference from record.created_at.
    """
    import re
    target = canonical_destination(platform, target)
    field = "channel_tg" if platform == "tg" else "vk_owner_id"
    id_field = "message_id" if platform == "tg" else "post_id"
    relevant = [p for p in projects if p.get(field) and canonical_destination(platform, p[field]) == target
                and str(p.get("platforms") or "both") in (platform, "both")]
    by_project = {(str(p.get("account_id")), str(p.get("id"))): p for p in relevant}
    own = (str(account), str(context.get("post_id") or ""))
    candidate = next((p for p in posts if (str(p.get("account_id")), str(p.get("id"))) == own), None)
    proj = by_project.get((str(account), str(context.get("project_id") or "")))
    def blocked(reason, **extra):
        return {"ok": False, "reason": reason, **extra}
    if (not candidate or not proj or str(candidate.get("project_id")) != str(proj.get("id"))
            or str(candidate.get("generation_key") or "") != str(context.get("generation_key") or "")):
        return blocked("SOCIAL_CAPACITY_CONTEXT_MISSING")
    try:
        offsets = {int(p.get("tz_offset", 3) if p.get("tz_offset") not in (None, "") else 3) for p in relevant}
        if len(offsets) != 1 or not -23 <= next(iter(offsets)) <= 23:
            return blocked("SOCIAL_DESTINATION_TIMEZONE_CONFLICT")
        zone = timezone(timedelta(hours=next(iter(offsets))))
        local = now.astimezone(zone)
    except (TypeError, ValueError):
        return blocked("SOCIAL_DESTINATION_TIMEZONE_CONFLICT")
    day = local.date()
    used, occupied_keys, success_any, photo_success = set(), set(), set(), set()
    warnings = []
    post_map = {(str(p.get("account_id")), str(p.get("id"))): p for p in posts}
    grouped = {}
    for receipt in receipts:
        identity = receipt.get("identity") or {}
        if identity.get("platform") != platform or canonical_destination(platform, identity.get("target")) != target:
            continue
        if receipt.get("status") == "planned":
            # Explicit rejection may retain an old attempted_at. It is not a write.
            continue
        if receipt.get("status") not in {"success", "inflight", "uncertain"}:
            return blocked("SOCIAL_CAPACITY_RECEIPT_INVALID")
        c = receipt.get("context") or {}
        life = (str(identity.get("tenant") or ""), str(c.get("post_id") or identity.get("lifecycle") or ""))
        if not all(life):
            return blocked("SOCIAL_DAY_EVIDENCE_REQUIRED")
        grouped.setdefault(life, []).append(receipt)
    for life, rows in grouped.items():
        successes = [r for r in rows if r.get("status") == "success"]
        if successes:
            success_any.add(life)
            for r in successes:
                rc = r.get("context") or {}
                rkey = str(rc.get("generation_key") or "").split(":")
                rproject = str(rc.get("project_id") or (rkey[2] if len(rkey) == 4 and rkey[0] == "social-slot" else ""))
                if ((r.get("identity") or {}).get("step") == "photo"
                        and rproject == str(context.get("project_id") or "")):
                    photo_success.add(life)
        # A Telegram photo and overflow are one lifecycle. Anchor it to the
        # first successful publication, not the later continuation/reconciliation.
        intervals = []
        for r in successes or rows:
            provider = r.get("provider_published_at") or (r.get("reconciliation") or {}).get("provider_published_at")
            result = r.get("result") or {}
            provider = provider or (result.get("result") or {}).get("date") or result.get("provider_published_at")
            exact = _capacity_datetime(provider)
            start = _capacity_datetime(r.get("attempted_at"))
            end = _capacity_datetime(r.get("completed_at"))
            if exact is not None:
                intervals.append((exact, exact))
            elif r.get("status") == "success" and not r.get("reconciliation") and start and end:
                intervals.append((start, end))
            elif r.get("status") in {"inflight", "uncertain"} and start:
                # An unresolved public write may have completed any time since
                # its attempt; it remains capacity until readback proves a day.
                intervals.append((start, now))
            else:
                return blocked("SOCIAL_DAY_EVIDENCE_REQUIRED")
        if any(start > end or start > now or end > now for start, end in intervals):
            return blocked("SOCIAL_DAY_EVIDENCE_REQUIRED")
        first = min(intervals, key=lambda x: x[0])
        if first[0].astimezone(zone).date() <= day <= first[1].astimezone(zone).date():
            used.add(life)
        record = post_map.get(life) or {}
        key = str(record.get("generation_key") or (rows[0].get("context") or {}).get("generation_key") or "")
        if key:
            occupied_keys.add(key)
    all_projects = {(str(p.get("account_id")), str(p.get("id"))): p for p in projects}
    for life, record in post_map.items():
        project = all_projects.get((life[0], str(record.get("project_id")))) or {}
        if life in grouped:
            continue
        part = (record.get("ids") or {}).get(platform) or {}
        if not isinstance(part, dict) or not part.get(id_field):
            continue
        dest = part.get("chat_id" if platform == "tg" else "owner_id")
        if not dest:
            return blocked("SOCIAL_DAY_EVIDENCE_REQUIRED")
        if canonical_destination(platform, dest) != target:
            continue
        exact = _capacity_datetime(part.get("date") or part.get("published_at"))
        if exact is not None:
            if exact.astimezone(zone).date() == day:
                used.add(life)
        else:
            upper = _capacity_datetime(record.get("published_at"))
            if record.get("status") == "published" and upper:
                if upper.astimezone(zone).date() >= day:
                    used.add(life)
                    warnings.append("legacy_complete_upper_bound:" + life[1])
            else:
                return blocked("SOCIAL_DAY_EVIDENCE_REQUIRED", platform=platform, record_id=life[1])
        success_any.add(life)
        if record.get("generation_key"):
            occupied_keys.add(str(record["generation_key"]))
    slots = set()
    for p in relevant:
        if str(p.get("mode") or "auto") == "off" or p.get("expected_auto_generation") is False:
            continue
        if not (p.get("paid_auto_generation") or p.get("expected_auto_generation")):
            continue
        if p.get("_capacity_owner") is not True:
            sub = p.get("subscription") or {}
            if str(sub.get("status") or "").lower() in {"expired", "cancelled", "canceled", "suspended", "blocked", "expired_owner_confirmed"}:
                continue
            try:
                paid = datetime.fromisoformat(str(sub.get("paid_at"))).date()
                if (day - paid).days >= int(sub.get("period_days", 30)):
                    continue
            except (TypeError, ValueError):
                continue
        delay = int(p.get("auto_publish_hours") or 0) if p.get("mode") == "moderate" else 0
        if delay < 0 or delay > 48:
            return blocked("SOCIAL_CAPACITY_SCHEDULE_INVALID")
        for ago in range(3):
            service_day = day - timedelta(days=ago)
            for raw in p.get("times") or p.get("schedule") or []:
                m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", str(raw).strip())
                if not m or int(m[1]) > 23 or (m[2] and int(m[2]) > 59):
                    return blocked("SOCIAL_CAPACITY_SCHEDULE_INVALID")
                hour = int(m[1])
                deadline = datetime.combine(service_day, datetime.min.time(), tzinfo=zone) + timedelta(hours=hour + delay)
                if deadline.date() == day:
                    slots.add("social-slot:%s:%s:%s_%02d" % (p["account_id"], p["id"], service_day.strftime("%Y%m%d"), hour))
    own_key = str(context.get("generation_key") or "")
    reserved_keys = slots - occupied_keys - {own_key}
    # Only a proven photo's missing overflow is the same logical publication,
    # including after midnight. It cannot authorize a new photo/record/target.
    continuation = (platform == "tg" and context.get("_delivery_step") == "overflow_text"
                    and own in photo_success)
    additional = 0 if own in used or continuation else 1
    reserved = len(reserved_keys)
    ok = continuation or len(used) + additional + reserved <= 3
    return {"ok": ok, "reason": "SOCIAL_CAPACITY_OK" if ok else "SOCIAL_DAILY_SLOTS_RESERVED",
            "used": len(used), "reserved": reserved, "additional": additional,
            "own_slot": own_key in slots, "continuation": continuation,
            "local_day": day.isoformat(), "timezone_offset_hours": next(iter(offsets)),
            "reserved_keys": sorted(reserved_keys),
            "evidence_warnings": warnings}


class DestinationBusy(RuntimeError):
    pass


class PgDestinationCapacity:
    """Global destination lock after the intent lock; never blocks on a lock.

    Both session locks use the same dedicated journal connection. Commits end
    read transactions but retain locks through the durable public-write receipt.
    """
    def __init__(self, journal):
        self.journal = journal
        self.intent = journal.intent
        self.locked = False
        self.key = "social-destination:v1:%s:%s" % (self.intent.platform, canonical_destination(self.intent.platform, self.intent.target))

    def __enter__(self):
        from sqlalchemy import text
        self.journal._require_connection()
        try:
            self.locked = bool(self.journal.conn.execute(text(
                "SELECT pg_try_advisory_lock(hashtextextended(:k,731929))"
            ), {"k": self.key}).scalar())
            self.journal.conn.commit()
        except BaseException:
            # __exit__ is not called when __enter__ fails; discard the actual
            # session even if acquisition/commit outcome is ambiguous.
            self.journal.conn.invalidate()
            raise
        if not self.locked:
            raise DestinationBusy("SOCIAL_DESTINATION_BUSY")
        return self

    def check(self, now=None):
        from sqlalchemy import text
        conn = self.journal._require_connection()
        rows = conn.execute(text("SELECT DISTINCT ON(account_id,key) account_id,key,value FROM storage "
                                 "WHERE key IN ('posting_projects','posting_posts') OR key LIKE 'social_delivery:v1:%' "
                                 "ORDER BY account_id,key,id DESC")).all()
        owner_accounts = self.journal.capacity_owner_accounts()
        conn.commit()
        projects, posts, receipts = [], [], []
        for account, key, raw in rows:
            value = json.loads(raw)
            if key in {"posting_projects", "posting_posts"}:
                if not isinstance(value, list):
                    raise ValueError("SOCIAL_CAPACITY_STORAGE_INVALID")
                (projects if key == "posting_projects" else posts).extend(
                    {**p, "account_id": str(account), "_capacity_owner": str(account) in owner_accounts} for p in value if isinstance(p, dict))
            elif isinstance(value, dict):
                receipts.append(value)
            else:
                raise ValueError("SOCIAL_CAPACITY_RECEIPT_INVALID")
        return capacity_decision(account=self.intent.tenant, platform=self.intent.platform, target=self.intent.target,
                                 context={**(self.intent.context or {}), "_delivery_step": self.intent.step}, projects=projects, posts=posts,
                                 receipts=receipts, now=now or datetime.now(timezone.utc))

    def __exit__(self, typ, exc, tb):
        from sqlalchemy import text
        try:
            if self.journal.conn.closed or self.journal.conn.invalidated:
                return
            if self.journal.conn.in_transaction():
                self.journal.conn.rollback()
            if self.locked:
                unlocked = self.journal.conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k,731929))"), {"k": self.key}).scalar()
                if unlocked is not True:
                    raise RuntimeError("SOCIAL_DESTINATION_LOCK_LOST")
                self.journal.conn.commit()
                self.locked = False
        finally:
            if self.locked and not self.journal.conn.closed:
                self.journal.conn.invalidate()


def _capacity_rejection(intent, decision):
    reason = str(decision.get("reason") or "SOCIAL_CAPACITY_UNAVAILABLE")
    return {"ok": False, "error": reason, "reason": reason,
            "stage": "destination_capacity", "dependency_state": "queued",
            "retryable_external": False, "owner_action_required": False,
            "delivery_outcome_uncertain": False,
            "capacity": {"limit": 3, **{k: decision[k] for k in ("used", "reserved") if k in decision}},
            "receipt_key": intent.key, "receipt_status": "planned"}


class ReceiptBusy(RuntimeError):
    pass


class PgReceiptJournal:
    """One dedicated connection holds the session lock across durable commits."""

    def __init__(self, intent: DeliveryIntent, engine=None):
        self.intent = intent
        self.engine = engine
        self.conn = None
        self.locked = False
        self._connection_token = None

    def __enter__(self):
        from sqlalchemy import text
        if self.conn is not None or self._connection_token is not None:
            raise RuntimeError("SOCIAL_RECEIPT_JOURNAL_ALREADY_ACTIVE")
        if self.engine is None:
            from app.db.session import engine
            self.engine = engine
        if self.engine.dialect.name != "postgresql":
            raise RuntimeError("SOCIAL_RECEIPT_REQUIRES_POSTGRESQL")
        self.conn = self.engine.connect()
        try:
            self.locked = bool(self.conn.execute(text(
                "SELECT pg_try_advisory_lock(hashtextextended(:k,731928))"
            ), {"k": self.intent.key}).scalar())
            self.conn.commit()
            if not self.locked:
                raise ReceiptBusy("SOCIAL_DELIVERY_BUSY")
            self._connection_token = _ACTIVE_RECEIPT_JOURNAL.set(self)
            return self
        except BaseException:
            # Closing a pooled connection alone does not release session-level
            # advisory locks. A failed acquisition/commit must discard it.
            try:
                self.conn.invalidate()
            finally:
                try:
                    self.conn.close()
                finally:
                    self.conn = None
                    self.locked = False
            raise

    def _require_connection(self):
        if (self.conn is None or not self.locked
                or self.conn.closed or self.conn.invalidated):
            # A suppressed cooldown disconnect must not reconnect without
            # the session locks and then persist a false delivery result.
            raise RuntimeError("SOCIAL_RECEIPT_CONNECTION_UNAVAILABLE")
        return self.conn

    def capacity_owner_accounts(self):
        from sqlalchemy import select
        from app.models.user import User
        conn = self._require_connection()
        return {"u" + str(x) for x in conn.execute(select(User.id).where(User.role == "owner")).scalars().all()}

    def destination_admission(self):
        return PgDestinationCapacity(self)

    def load(self) -> dict | None:
        from sqlalchemy import text
        conn = self._require_connection()
        row = conn.execute(text(
            "SELECT value FROM storage WHERE account_id=:a AND key=:k "
            "ORDER BY id DESC LIMIT 1"
        ), {"a": self.intent.tenant, "k": self.intent.key}).first()
        conn.commit()
        if row is None:
            return None
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise RuntimeError("SOCIAL_RECEIPT_INVALID")
        return value

    def save(self, value: dict) -> None:
        from sqlalchemy import text
        self._require_connection()
        values = {"a": self.intent.tenant, "k": self.intent.key,
                  "v": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}
        row = self.conn.execute(text(
            "SELECT id FROM storage WHERE account_id=:a AND key=:k "
            "ORDER BY id DESC LIMIT 1"
        ), values).first()
        if row:
            self.conn.execute(text(
                "UPDATE storage SET value=:v WHERE id=:id AND account_id=:a AND key=:k"
            ), {**values, "id": row[0]})
        else:
            self.conn.execute(text(
                "INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"
            ), values)
        # This commit must finish before a mutating provider call is permitted.
        self.conn.commit()

    def __exit__(self, typ, exc, tb):
        try:
            if self.conn is None:
                return
            try:
                if self.conn.closed or self.conn.invalidated:
                    return
                if self.conn.in_transaction():
                    self.conn.rollback()
                if self.locked:
                    from sqlalchemy import text
                    self.conn.execute(text(
                        "SELECT pg_advisory_unlock(hashtextextended(:k,731928))"
                    ), {"k": self.intent.key})
                    self.conn.commit()
                    self.locked = False
            finally:
                try:
                    if self.locked and not self.conn.closed:
                        # A failed unlock must never return a still-locked
                        # session to unrelated application work.
                        self.conn.invalidate()
                finally:
                    self.conn.close()
        finally:
            self.conn = None
            self.locked = False
            token, self._connection_token = self._connection_token, None
            if token is not None:
                _ACTIVE_RECEIPT_JOURNAL.reset(token)


def _stamp_uncertain_review(receipt):
    if receipt.get("review_after"):
        return
    raw = receipt.get("attempted_at") or receipt.get("uncertain_since")
    if not raw and receipt.get("origin") == "legacy":
        raw = (receipt.get("context") or {}).get("created_at")
    try:
        started = datetime.fromisoformat(str(raw or receipt.get("created_at") or utc_now()).replace("Z", "+00:00"))
        started = started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started.astimezone(timezone.utc)
    except (TypeError, ValueError):
        started = datetime.now(timezone.utc)
    receipt["uncertain_since"] = started.isoformat()
    receipt["review_after"] = (started + timedelta(hours=6)).isoformat()


def waiting(intent: DeliveryIntent, reason: str, *, evidence=None, receipt=None) -> dict:
    result = {
        "ok": False, "error": reason, "reason": reason,
        "stage": "delivery_receipt", "dependency_state": "waiting_external",
        "retryable_external": False, "owner_action_required": False,
        "delivery_outcome_uncertain": True, "receipt_key": intent.key,
        "receipt_status": "uncertain", "reconciliation_required": True,
    }
    if evidence:
        result["delivery_evidence"] = evidence
    if receipt is not None:
        _stamp_uncertain_review(receipt)
        overdue = str(receipt["review_after"]) <= utc_now()
        result.update(review_after=receipt["review_after"], review_overdue=overdue)
        # A provider cooldown has no useful owner remedy. Ask for a concrete
        # historical/candidate check only when automatic proof cannot finish.
        evidence = evidence or {}
        actionable = evidence.get("status") in {"ambiguous", "absent"} or evidence.get("reason") in {
            "telegram_private_history_unavailable", "publication_window_missing",
        }
        if overdue and actionable:
            result["owner_action_required"] = True
            result["owner_action"] = (
                "inspect_provider_candidates_text_and_banner"
                if evidence.get("candidates") else "inspect_provider_history_for_this_intent"
            )
    return result


def _internal_block(intent, reason, *, evidence=None):
    result = {
        "ok": False, "error": reason, "reason": reason,
        "stage": "delivery_receipt", "dependency_state": "internal_error",
        "retryable_external": False, "owner_action_required": False,
        "delivery_outcome_uncertain": False, "receipt_key": intent.key,
    }
    if evidence:
        result["delivery_evidence"] = evidence
    return result


def delivery_reconciliation_wait_state(record, required_platforms):
    """Observer classification only; never changes a row or authorizes a send.

    An actual uncertain receipt is a visible reconciliation wait. Any missing
    channel with an ordinary internal error still makes the rescue unhealthy.
    """
    ids = record.get("ids") if isinstance(record.get("ids"), dict) else {}
    failures = record.get("delivery_failures") if isinstance(record.get("delivery_failures"), dict) else {}
    uncertain, owner_actions, review_after = {}, {}, {}
    for platform in required_platforms:
        part = ids.get(platform) if isinstance(ids.get(platform), dict) else {}
        if part.get("message_id" if platform == "tg" else "post_id"):
            continue
        failure = failures.get(platform) if isinstance(failures.get(platform), dict) else {}
        reason = str(failure.get("reason") or "")
        is_uncertain = (
            failure.get("delivery_outcome_uncertain") is True
            and failure.get("stage") == "delivery_receipt"
            and str(failure.get("receipt_key") or "").startswith("social_delivery:v1:")
            and reason in {
                "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN",
                "SOCIAL_DELIVERY_TRANSPORT_UNCERTAIN",
                "SOCIAL_DELIVERY_RECEIPT_PERSIST_UNCERTAIN",
            }
        )
        if is_uncertain:
            uncertain[platform] = failure["receipt_key"]
            if failure.get("review_after"):
                review_after[platform] = failure["review_after"]
            if bool(failure.get("owner_action_required")):
                owner_actions[platform] = failure.get("owner_action") or "inspect_provider_history_for_this_intent"
        elif bool(failure.get("owner_action_required")):
            return None
        elif failure.get("dependency_state") == "waiting_external" and failure.get("retryable_external") is True:
            continue
        elif failure.get("dependency_state") == "queued" and reason in {"vk_delivery_paced", "social_delivery_busy"}:
            continue
        else:
            return None
    if not uncertain:
        return None
    return {
        "dependency_state": "waiting_external", "owner_action_required": bool(owner_actions),
        "transport_reason": "social_delivery_reconciliation_required",
        "reconciliation_required": True, "retryable_external": False,
        "next_action": "owner_provider_history_inspection" if owner_actions else "read_only_provider_reconciliation",
        "receipt_keys": uncertain, "owner_actions": owner_actions,
        "reconciliation_review_after": review_after,
    }


def _receipt_result(intent, receipt, *, cached=False):
    result = dict(receipt["result"])
    result.update({
        "receipt_key": intent.key, "receipt_status": "success",
        "receipt_cached": bool(cached),
    })
    if receipt.get("reconciliation"):
        result["delivery_evidence"] = receipt["reconciliation"]
    return result


def guarded_delivery(
    intent: DeliveryIntent,
    send: Callable[[dict], tuple[str, dict]],
    *,
    prepare: Callable[[dict], tuple[bool, dict]] | None = None,
    reconcile: Callable[[DeliveryIntent, dict], dict] | None = None,
    journal_factory=PgReceiptJournal,
) -> dict:
    """Serialize one external mutation and quarantine any ambiguous outcome.

    send returns (success|rejected|uncertain, compatible provider result).
    prepare can upload reusable VK media, but must never publish a post.
    Reconciliation can confirm success. Missing content alone never permits a
    retry; legacy negative evidence requires a complete scan AND a recorded
    provider rejection before the publication call.
    """
    # Invocation-local proof, independent of receipt/publication state.
    # False means no provider-capable callback was invoked. True is
    # conservative, including callback errors or a cached-media prepare.
    provider_io_started = False

    def _with_provider_io(result):
        return {**result, "provider_io_started": provider_io_started}

    if not str(intent.tenant).strip() or not str(intent.target).strip():
        return _with_provider_io(_internal_block(intent, "SOCIAL_DELIVERY_IDENTITY_MISSING"))
    attempted = False
    try:
        with journal_factory(intent) as journal:
            receipt = journal.load()
            if receipt is not None:
                if receipt.get("identity") != intent.identity:
                    return _with_provider_io(_internal_block(intent, "SOCIAL_RECEIPT_IDENTITY_MISMATCH"))
                if receipt.get("payload_hash") != intent.payload_hash:
                    if receipt.get("status") != "planned":
                        return _with_provider_io(waiting(intent, "SOCIAL_DELIVERY_INTENT_CHANGED"))
                    # A proven rejected/not-yet-attempted draft may be edited.
                    # Inflight, uncertain and delivered payloads stay immutable.
                    receipt.setdefault("superseded_payloads", []).append(receipt["payload_hash"])
                    receipt.update(payload_hash=intent.payload_hash,
                                   asset_sha256=intent.asset_sha256, prepared={})
                    journal.save(receipt)
                if receipt.get("status") == "success":
                    return _with_provider_io(_receipt_result(intent, receipt, cached=True))
                if receipt.get("status") not in {"planned", "inflight", "uncertain"}:
                    return _with_provider_io(_internal_block(intent, "SOCIAL_RECEIPT_INVALID_STATE"))
            else:
                receipt = {
                    "protocol": PROTOCOL, "identity": intent.identity,
                    "payload_hash": intent.payload_hash,
                    "asset_sha256": intent.asset_sha256,
                    "status": "uncertain" if intent.legacy else "planned",
                    "origin": "legacy" if intent.legacy else "canonical",
                    "created_at": utc_now(), "updated_at": utc_now(),
                    "context": {
                        k: v for k, v in (intent.context or {}).items()
                        if k in {"post_id", "project_id", "generation_key", "created_at",
                                 "protocol", "legacy_error", "legacy_stage"}
                    },
                    "prepared": {},
                }
                if intent.legacy:
                    receipt["uncertain_reason"] = "LEGACY_DELIVERY_WITHOUT_RECEIPT"
                    _stamp_uncertain_review(receipt)
                journal.save(receipt)

            if receipt["status"] == "inflight":
                receipt.update(status="uncertain",
                               uncertain_reason="WORKER_EXIT_AFTER_ATTEMPT",
                               updated_at=utc_now())
                _stamp_uncertain_review(receipt)
                journal.save(receipt)

            if receipt["status"] == "uncertain":
                if not receipt.get("review_after"):
                    _stamp_uncertain_review(receipt)
                    journal.save(receipt)
                evidence = {}
                retry_at = receipt.get("next_reconciliation_at")
                if retry_at and str(retry_at) > utc_now():
                    return _with_provider_io(waiting(intent, "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN",
                                   evidence=receipt.get("last_reconciliation"), receipt=receipt))
                if reconcile is not None:
                    try:
                        provider_io_started = True
                        evidence = dict(reconcile(intent, dict(receipt)) or {})
                    except Exception as exc:
                        evidence = {"status": "unavailable", "error_type": type(exc).__name__}
                receipt["last_reconciliation"] = evidence
                receipt["updated_at"] = utc_now()
                receipt["next_reconciliation_at"] = (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat()
                if evidence.get("status") == "confirmed" and evidence.get("result", {}).get("ok"):
                    receipt.update(status="success", result=evidence["result"],
                                   reconciliation=evidence, completed_at=utc_now())
                    journal.save(receipt)
                    return _with_provider_io(_receipt_result(intent, receipt, cached=True))
                can_release_legacy = (
                    receipt.get("origin") == "legacy"
                    and not receipt.get("attempted_at")
                    and evidence.get("status") == "absent"
                    and evidence.get("scan_complete") is True
                    and evidence.get("pre_send_rejection_proven") is True
                )
                if can_release_legacy:
                    receipt.update(status="planned", legacy_reconciliation=evidence)
                    journal.save(receipt)
                else:
                    journal.save(receipt)
                    return _with_provider_io(waiting(intent, "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN", evidence=evidence, receipt=receipt))

            next_attempt = receipt.get("next_attempt_at")
            if next_attempt and str(next_attempt) > utc_now():
                result = dict(receipt.get("last_rejection") or {})
                result.update(ok=False, receipt_key=intent.key, receipt_status="planned")
                return _with_provider_io(result)

            with journal.destination_admission() as capacity:
                decision = capacity.check()
                if decision.get("ok") is not True:
                    return _with_provider_io(_capacity_rejection(intent, decision))
                if prepare is not None:
                    provider_io_started = True
                    ready, preparation = prepare(dict(receipt.get("prepared") or {}))
                    if not ready:
                        # No publication was attempted: ordinary provider cooldowns
                        # remain safely retryable through the existing scheduler.
                        receipt.update(last_rejection=preparation, updated_at=utc_now())
                        journal.save(receipt)
                        return _with_provider_io({**preparation, "receipt_key": intent.key, "receipt_status": "planned"})
                    receipt["prepared"] = dict(preparation)
                    receipt["updated_at"] = utc_now()
                    journal.save(receipt)

                attempt_now = datetime.now(timezone.utc)
                decision = capacity.check(now=attempt_now)
                if decision.get("ok") is not True:
                    return _with_provider_io(_capacity_rejection(intent, decision))
                fresh_now = datetime.now(timezone.utc)
                zone = timezone(timedelta(hours=int(decision["timezone_offset_hours"])))
                if fresh_now.astimezone(zone).date().isoformat() != decision["local_day"]:
                    return _with_provider_io(_capacity_rejection(intent, {"reason": "SOCIAL_DAY_BOUNDARY_RETRY"}))
                receipt.update(status="inflight", attempted_at=fresh_now.isoformat(), updated_at=fresh_now.isoformat())
                journal.save(receipt)
                if datetime.now(timezone.utc).astimezone(zone).date().isoformat() != decision["local_day"]:
                    receipt.update(status="planned", updated_at=utc_now())
                    journal.save(receipt)
                    return _with_provider_io(_capacity_rejection(intent, {"reason": "SOCIAL_DAY_BOUNDARY_RETRY"}))
                attempted = True
                try:
                    provider_io_started = True
                    disposition, result = send(dict(receipt.get("prepared") or {}))
                except Exception as exc:
                    disposition = "uncertain"
                    result = waiting(intent, "SOCIAL_DELIVERY_TRANSPORT_UNCERTAIN",
                                     evidence={"error_type": type(exc).__name__})
                if disposition == "success" and result.get("ok"):
                    receipt.update(status="success", result=dict(result),
                                   completed_at=utc_now(), updated_at=utc_now())
                    journal.save(receipt)
                    return _with_provider_io(_receipt_result(intent, receipt))
                if disposition == "rejected":
                    receipt.update(status="planned", last_rejection=dict(result),
                                   updated_at=utc_now())
                    seconds = int(result.get("retry_after_seconds") or 0)
                    if seconds > 0:
                        receipt["next_attempt_at"] = (
                            datetime.now(timezone.utc) + timedelta(seconds=seconds)
                        ).isoformat()
                    journal.save(receipt)
                    return _with_provider_io({**result, "receipt_key": intent.key, "receipt_status": "planned"})
                receipt.update(status="uncertain", uncertain_reason=result.get("reason")
                               or "PROVIDER_RESPONSE_UNCERTAIN", updated_at=utc_now())
                _stamp_uncertain_review(receipt)
                journal.save(receipt)
                return _with_provider_io(waiting(intent, "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN", receipt=receipt))
    except DestinationBusy:
        return _with_provider_io(_capacity_rejection(intent, {"ok": False, "reason": "SOCIAL_DESTINATION_BUSY"}))
    except ReceiptBusy:
        return _with_provider_io({
            "ok": False, "error": "SOCIAL_DELIVERY_BUSY",
            "reason": "social_delivery_busy", "dependency_state": "queued",
            "retryable_external": False, "owner_action_required": False,
            "receipt_key": intent.key,
        })
    except Exception as exc:
        # If a post succeeded but DB commit failed, the durable inflight marker
        # remains. A later process reconciles it; it never blindly republishes.
        if attempted:
            return _with_provider_io(waiting(intent, "SOCIAL_DELIVERY_RECEIPT_PERSIST_UNCERTAIN",
                           evidence={"error_type": type(exc).__name__}, receipt=receipt))
        return _with_provider_io(_internal_block(intent, "SOCIAL_DELIVERY_RECEIPT_UNAVAILABLE",
                               evidence={"error_type": type(exc).__name__}))

