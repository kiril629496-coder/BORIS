#!/usr/bin/env python3
"""Bounded persistent-failure escalation for BORIS telephony supervisor.

The supervisor always self-heals first. This helper is invoked by systemd only
after a supervisor failure. It sends no alert for transient failures: three
consecutive failed supervisor activations are required. A successful supervisor
activation records a health marker and resets the failure streak.

No client/provider/call/paid-AI boundary is used here. Alert delivery uses the
existing BORIS owner notification transport and failure to deliver never causes
OnFailure recursion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/BORIS/backend")
STATE_DIR = Path("/var/lib/boris-integrity/telephony-supervisor")
STATE = STATE_DIR / "failure_state.json"
HEALTH = STATE_DIR / "last_healthy.json"
JOURNAL = STATE_DIR / "failure_alerts.jsonl"
SUPERVISOR = "boris-telephony-supervisor.service"
SUPERVISOR_TIMER = "boris-telephony-supervisor.timer"
GUARDIAN = "boris-telephony-guardian.service"
GUARDIAN_TIMER = "boris-telephony-guardian.timer"
CONTRACT = ROOT / "scripts/phone-guardian-contract-check.py"
PYTHON = ROOT / "venv/bin/python"

THRESHOLD = 3
STREAK_WINDOW_SECONDS = 15 * 60
DEDUP_SECONDS = 6 * 60 * 60
DELIVERY_RETRY_SECONDS = 15 * 60


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append(entry: dict) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        os.chmod(JOURNAL, 0o600)
    except OSError:
        pass


def _run(args: list[str], timeout: int = 20) -> dict:
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": cp.returncode == 0,
            "rc": int(cp.returncode),
            "stdout": (cp.stdout or "").strip(),
            "stderr": (cp.stderr or "").strip(),
        }
    except Exception as exc:
        return {"ok": False, "rc": 255, "stdout": "", "stderr": type(exc).__name__}


def _show(unit: str, *props: str) -> dict[str, str]:
    args = ["systemctl", "show", unit, "--no-pager"]
    for prop in props:
        args.extend(["-p", prop])
    out = _run(args, timeout=12)
    if not out["ok"]:
        return {"_error": out["stderr"] or out["stdout"] or f"rc={out['rc']}"}
    result: dict[str, str] = {}
    for line in out["stdout"].splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _unit_facts(unit: str) -> dict:
    raw = _show(unit, "ActiveState", "Result", "ExecMainStatus", "UnitFileState")
    return {
        "active_state": raw.get("ActiveState") or "unknown",
        "result": raw.get("Result") or "unknown",
        "exec_main_status": raw.get("ExecMainStatus") or "unknown",
        "unit_file_state": raw.get("UnitFileState") or "unknown",
    }


def _contract_fact() -> dict:
    if not CONTRACT.is_file():
        return {"ok": False, "code": "missing"}
    out = _run([str(PYTHON), str(CONTRACT)], timeout=20)
    passed = bool(out["ok"] and "TELEPHONY_GUARDIAN_CONTRACT=PASS" in out["stdout"])
    return {"ok": passed, "code": "pass" if passed else "fail", "rc": out["rc"]}


def collect() -> dict:
    return {
        "checked_at": _utc(),
        "supervisor": _unit_facts(SUPERVISOR),
        "supervisor_timer": _unit_facts(SUPERVISOR_TIMER),
        "guardian": _unit_facts(GUARDIAN),
        "guardian_timer": _unit_facts(GUARDIAN_TIMER),
        "contract": _contract_fact(),
    }


def fingerprint(facts: dict) -> str:
    stable = {k: v for k, v in facts.items() if k != "checked_at"}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def message(facts: dict, streak: int) -> str:
    sup = facts.get("supervisor") or {}
    guard = facts.get("guardian") or {}
    contract = facts.get("contract") or {}
    return (
        "🚨 BORIS Phone не смог автоматически восстановить внутренний контроль телефонии.\n"
        f"Неудачных supervisor-проверок подряд: {streak}.\n"
        "До этого BORIS уже выполнил локальный self-heal и повтор guardian.\n"
        f"Supervisor: {sup.get('result', 'unknown')} / status {sup.get('exec_main_status', 'unknown')}.\n"
        f"Guardian: {guard.get('result', 'unknown')} / status {guard.get('exec_main_status', 'unknown')}.\n"
        f"Локальный safety-contract: {'PASS' if contract.get('ok') else 'FAIL'}.\n"
        "BORIS продолжит автоматические повторы каждые 2 минуты. "
        "Повторное одинаковое уведомление подавляется на 6 часов."
    )


def mark_healthy(now: float | None = None) -> dict:
    epoch = float(time.time() if now is None else now)
    healthy_at = _utc()
    payload = {"healthy_epoch": epoch, "healthy_at": healthy_at}
    _atomic_json(HEALTH, payload)
    # A healthy supervisor closes the incident. Reset both the consecutive
    # failure streak and incident-local delivery dedup so a later independent
    # persistent failure can alert again after its own three failed recoveries.
    state = _json(STATE)
    for key in (
        "last_alert_epoch", "last_alert_at", "last_alert_fingerprint",
        "last_delivery_epoch", "last_delivery_at", "last_delivery_fingerprint",
    ):
        state.pop(key, None)
    state.update({"streak": 0, "last_healthy_epoch": epoch, "last_healthy_at": healthy_at})
    _atomic_json(STATE, state)
    return {"ok": True, "status": "healthy_marked", **payload, "streak": 0}


def _send_with_retry(text: str) -> tuple[bool, list[dict]]:
    from app.ext_api import notify

    conf = notify._conf()
    if not (conf.get("token") and conf.get("chat_id")):
        return False, [{"ok": False, "description": "notify_not_configured"}]

    params = {
        "chat_id": conf["chat_id"],
        "text": str(text or "")[:3900],
        "disable_web_page_preview": "true",
    }
    if conf.get("thread_id"):
        params["message_thread_id"] = int(conf["thread_id"])

    attempts: list[dict] = []
    for delay in (0, 1, 3):
        if delay:
            time.sleep(delay)
        try:
            response = notify._api("sendMessage", params)
            item = {
                "ok": bool(response.get("ok")),
                "error_code": response.get("error_code"),
                "description": str(response.get("description") or "")[:240] or None,
                "message_id": (
                    (response.get("result") or {}).get("message_id")
                    if isinstance(response.get("result"), dict)
                    else None
                ),
            }
        except Exception as exc:
            item = {
                "ok": False,
                "error_code": None,
                "description": type(exc).__name__,
                "message_id": None,
            }
        attempts.append(item)
        if item["ok"]:
            return True, attempts
    return False, attempts


def process_failure(
    facts: dict | None = None,
    *,
    now: float | None = None,
    send_func=None,
) -> dict:
    epoch = float(time.time() if now is None else now)
    facts = facts or collect()
    fp = fingerprint(facts)
    state = _json(STATE)
    health = _json(HEALTH)

    last_failure = float(state.get("last_failure_epoch") or 0)
    last_health = float(health.get("healthy_epoch") or 0)
    reset_by_health = last_health > last_failure
    within_window = bool(last_failure and epoch - last_failure <= STREAK_WINDOW_SECONDS)
    if reset_by_health or not within_window:
        streak = 1
    else:
        streak = int(state.get("streak") or 0) + 1

    next_state = {
        **state,
        "streak": streak,
        "last_failure_epoch": epoch,
        "last_failure_at": facts.get("checked_at") or _utc(),
        "last_failure_fingerprint": fp,
    }
    _atomic_json(STATE, next_state)

    base = {
        "ok": True,
        "streak": streak,
        "threshold": THRESHOLD,
        "fingerprint": fp,
        "facts": facts,
        "owner_action_required": streak >= THRESHOLD,
    }

    if streak < THRESHOLD:
        out = {**base, "sent": False, "status": "retrying_before_escalation"}
        _append({"at": facts.get("checked_at") or _utc(), **out})
        return out

    same_alert = (
        state.get("last_alert_fingerprint") == fp
        and epoch - float(state.get("last_alert_epoch") or 0) < DEDUP_SECONDS
    )
    if same_alert:
        out = {**base, "sent": False, "status": "deduped"}
        _append({"at": facts.get("checked_at") or _utc(), **out})
        return out

    recent_delivery_attempt = (
        state.get("last_delivery_fingerprint") == fp
        and epoch - float(state.get("last_delivery_epoch") or 0) < DELIVERY_RETRY_SECONDS
    )
    if recent_delivery_attempt:
        out = {**base, "sent": False, "status": "delivery_retry_deferred"}
        _append({"at": facts.get("checked_at") or _utc(), **out})
        return out

    sender = send_func or _send_with_retry
    text = message(facts, streak)
    try:
        sent, attempts = sender(text)
    except Exception as exc:
        sent = False
        attempts = [{"ok": False, "description": type(exc).__name__}]

    next_state.update(
        {
            "last_delivery_epoch": epoch,
            "last_delivery_at": facts.get("checked_at") or _utc(),
            "last_delivery_fingerprint": fp,
        }
    )
    if sent:
        next_state.update(
            {
                "last_alert_epoch": epoch,
                "last_alert_at": facts.get("checked_at") or _utc(),
                "last_alert_fingerprint": fp,
            }
        )
    _atomic_json(STATE, next_state)

    out = {
        **base,
        "sent": bool(sent),
        "status": "sent" if sent else "send_failed",
        "delivery_attempts": attempts,
    }
    _append({"at": facts.get("checked_at") or _utc(), **out})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-healthy", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.mark_healthy:
        print(json.dumps(mark_healthy(), ensure_ascii=False, sort_keys=True))
        return 0

    facts = collect()
    if args.dry_run:
        state = _json(STATE)
        health = _json(HEALTH)
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "facts": facts,
                    "fingerprint": fingerprint(facts),
                    "current_streak": int(state.get("streak") or 0),
                    "last_healthy_at": health.get("healthy_at"),
                    "threshold": THRESHOLD,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    out = process_failure(facts)
    print(json.dumps(out, ensure_ascii=False, default=str, sort_keys=True))
    # Alert transport failure must never create OnFailure recursion.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
