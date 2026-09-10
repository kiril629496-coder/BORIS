"""Cross-process Avito account throttle ledger for trusted BORIS workers."""
from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

_STATE_DIR = Path("/root/BORIS/backend/run")
_LEDGER = _STATE_DIR / "avito_account_throttle.json"
_LOCK = _STATE_DIR / "avito_account_throttle.lock"
_MAX_TTL_SECONDS = 3600
_CORRUPT_QUARANTINE_SECONDS = 60
_CORRUPT_KEY = "__ledger_corrupt_quarantine__"


@contextmanager
def _locked_state():
    _LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOCK, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            try:
                raw = json.loads(_LEDGER.read_text(encoding="utf-8")) if _LEDGER.exists() else {}
                if not isinstance(raw, dict):
                    raise ValueError("throttle ledger root must be an object")
            except Exception:
                # AVITO_THROTTLE_LEDGER_CORRUPTION_FAIL_CLOSED_V1: a torn/corrupt
                # cross-process ledger must never become an implicit "no throttle".
                # Quarantine provider I/O briefly, then self-expire; reporting keeps
                # its persisted last-known snapshots and no permanent owner action is needed.
                now = time.time()
                raw = {_CORRUPT_KEY: {"until": now + _CORRUPT_QUARANTINE_SECONDS,
                                     "source": "ledger_corrupt_fail_closed",
                                     "updated_at": now}}
                # AVITO_THROTTLE_LEDGER_CORRUPTION_REPAIR_ONCE_V1: persist the
                # quarantine immediately while holding the ledger lock. Otherwise
                # every reader would reparse the same corrupt bytes and extend the
                # quarantine forever instead of self-expiring after one bounded TTL.
                _save(raw)
            yield raw
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _save(state: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="boris_avito_throttle_", suffix=".json", dir=str(_STATE_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, ensure_ascii=False, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, _LEDGER)
        # Keep the shared ledger readable/writable by the SentinelX production QA
        # principal as well as root-owned BORIS workers. Atomic replace creates a
        # fresh inode, so a restrictive inherited ACL/mask must not silently make
        # every external verifier treat the valid ledger as corrupt and start a
        # global fail-closed quarantine.
        try:
            os.chmod(_LEDGER, 0o660)
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass




def _tenant_keys(account_id: str) -> list[str]:
    """Return all internal aliases for one real Avito tenant.

    AVITO_SHARED_THROTTLE_PROVIDER_ALIAS_V1: internal BORIS account aliases that
    share one avito_user_id must share one Retry-After window as well. A 429 is
    provider-tenant pressure, not an internal-account event. DB lookup failure
    falls back to the exact account key; normal request guards remain fail-closed
    on their own DB/provider preconditions.
    """
    aid = str(account_id or "").strip()
    if not aid:
        return []
    keys = {aid}
    try:
        from app.db.session import SessionLocal
        from sqlalchemy import text
        d = SessionLocal()
        try:
            uid = d.execute(text("SELECT avito_user_id FROM accounts WHERE account_id=:a LIMIT 1"), {"a": aid}).scalar()
            uid = str(uid or "").strip()
            if uid:
                keys.add("uid:" + uid)
                aliases = d.execute(text("SELECT account_id FROM accounts WHERE avito_user_id=:u"), {"u": uid}).scalars().all()
                keys.update(str(x).strip() for x in aliases if str(x or "").strip())
        finally:
            d.close()
    except Exception:
        pass
    return sorted(keys)

def record_account_throttle(account_id: str, retry_after_seconds: int | float | None, source: str = "avito_429") -> int:
    """Record/extend tenant Retry-After; return remaining whole seconds."""
    aid = str(account_id or "").strip()
    tenant_keys = _tenant_keys(aid)
    if not tenant_keys:
        return 0
    try:
        ttl = float(retry_after_seconds if retry_after_seconds is not None else 30)
    except Exception:
        ttl = 30.0
    ttl = min(float(_MAX_TTL_SECONDS), max(5.0, ttl))
    now = time.time()
    # AVITO_SHARED_ACCOUNT_THROTTLE_LEDGER_V1
    with _locked_state() as state:
        previous_until = 0.0
        for key in tenant_keys:
            prev = state.get(key) if isinstance(state.get(key), dict) else {}
            try:
                _prev_until = float(prev.get("until") or 0)
                # AVITO_THROTTLE_EXISTING_TTL_CLAMP_V1: an old/corrupt/future-skewed
                # row must never expand a new Retry-After beyond the authoritative
                # one-hour ceiling. Persisted rows are repaired by the reader below.
                _prev_until = min(_prev_until, now + float(_MAX_TTL_SECONDS))
                previous_until = max(previous_until, _prev_until)
            except Exception:
                pass
        until = min(now + float(_MAX_TTL_SECONDS), max(previous_until, now + ttl))
        row = {"until": until, "source": str(source or "avito_429")[:80], "updated_at": now}
        for key in tenant_keys:
            state[key] = dict(row)
        _save(state)
        return max(0, int(math.ceil(until - now)))


def account_throttle_remaining(account_id: str) -> int:
    """Return remaining tenant cooldown; expired entries are pruned atomically."""
    aid = str(account_id or "").strip()
    tenant_keys = _tenant_keys(aid)
    if not tenant_keys:
        return 0
    now = time.time()
    with _locked_state() as state:
        changed = False
        for key in list(state):
            row = state.get(key) if isinstance(state.get(key), dict) else {}
            try:
                until = float(row.get("until") or 0)
                expired = until <= now
            except Exception:
                until = 0.0
                expired = True
            if expired:
                state.pop(key, None)
                changed = True
                continue
            # AVITO_THROTTLE_PERSISTED_TTL_CLAMP_V1: even syntactically valid
            # ledger data is untrusted operational state. A far-future `until`
            # caused by clock skew, a legacy bug or manual damage is repaired once
            # to the same authoritative one-hour maximum instead of blocking the
            # tenant indefinitely. The repaired value is persisted, so repeated
            # reads do not slide the window forward.
            max_ttl = (_CORRUPT_QUARANTINE_SECONDS if key == _CORRUPT_KEY else _MAX_TTL_SECONDS)
            max_until = now + float(max_ttl)
            if until > max_until:
                # AVITO_THROTTLE_CORRUPT_QUARANTINE_TTL_CLAMP_V1: the global
                # corruption quarantine has its own much shorter bound; a valid
                # JSON row under the reserved key must not turn it into a 1h block.
                row = dict(row)
                row["until"] = max_until
                row["source"] = str(row.get("source") or "")[:80] or "ttl_clamped"
                state[key] = row
                changed = True
        if changed:
            _save(state)
        # A corrupt-ledger quarantine is intentionally global: tenant identity and
        # previous cooldown rows are unprovable until the short quarantine expires.
        global_row = state.get(_CORRUPT_KEY) if isinstance(state.get(_CORRUPT_KEY), dict) else {}
        try:
            global_remaining = max(0, int(math.ceil(float(global_row.get("until") or 0) - now)))
        except Exception:
            global_remaining = 0
        if global_remaining > 0:
            return global_remaining
        remaining = 0
        for key in tenant_keys:
            row = state.get(key) if isinstance(state.get(key), dict) else {}
            try:
                remaining = max(remaining, max(0, int(math.ceil(float(row.get("until") or 0) - now))))
            except Exception:
                pass
        return remaining
