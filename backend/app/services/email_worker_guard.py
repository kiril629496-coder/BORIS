from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal

BASE = Path("/root/BORIS/backend")
HEARTBEAT = BASE / ".email_worker_heartbeat"
TIMER_UNIT = "boris-email-queue.timer"
SERVICE_UNIT = "boris-email-queue.service"


def _active_outreach() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                text("SELECT count(*) FROM prospect_campaigns WHERE status='active'")
            ).scalar()
            or 0
        )
    except Exception:
        return 0
    finally:
        db.close()


def _heartbeat_age_seconds() -> float | None:
    try:
        return max(0.0, datetime.now().timestamp() - HEARTBEAT.stat().st_mtime)
    except OSError:
        return None


def _systemctl(*args: str, timeout: int = 20) -> tuple[bool, str]:
    try:
        cp = subprocess.run(
            ["systemctl", *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return cp.returncode == 0, (cp.stdout or cp.stderr or "").strip()
    except Exception as exc:
        return False, type(exc).__name__


def _timer_state() -> tuple[bool, bool]:
    enabled, _ = _systemctl("is-enabled", TIMER_UNIT)
    active, _ = _systemctl("is-active", TIMER_UNIT)
    return enabled, active


def _repair_timer() -> bool:
    ok, _ = _systemctl("enable", "--now", TIMER_UNIT, timeout=30)
    if not ok:
        return False
    enabled, active = _timer_state()
    return enabled and active


def _trigger_service_once() -> bool:
    # Canonical recovery path: use the systemd service itself, not a second
    # direct Python runner. This preserves one lifecycle and one EnvironmentFile.
    ok, _ = _systemctl("start", SERVICE_UNIT, timeout=190)
    return ok


def ensure(max_heartbeat_age_seconds: int = 300) -> dict:
    """Self-heal the canonical systemd email worker when outreach is active."""
    active_campaigns = _active_outreach()
    if active_campaigns <= 0:
        return {"status": "idle", "active_campaigns": 0}

    timer_enabled, timer_active = _timer_state()
    timer_repaired = False
    if not (timer_enabled and timer_active):
        timer_repaired = _repair_timer()
        timer_enabled, timer_active = _timer_state()

    age = _heartbeat_age_seconds()
    stale = age is None or age > max(60, int(max_heartbeat_age_seconds))
    service_triggered = False
    if stale and timer_enabled and timer_active:
        service_triggered = _trigger_service_once()
        age = _heartbeat_age_seconds()
        stale = age is None or age > max(60, int(max_heartbeat_age_seconds))

    status = "ok" if timer_enabled and timer_active and not stale else "degraded"
    return {
        "status": status,
        "active_campaigns": active_campaigns,
        "timer_enabled": timer_enabled,
        "timer_active": timer_active,
        "timer_repaired": timer_repaired,
        "service_triggered": service_triggered,
        "heartbeat_age_seconds": round(age, 1) if age is not None else None,
    }
