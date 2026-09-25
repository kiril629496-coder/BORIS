#!/usr/bin/env python3
"""Infrastructure supervisor for BORIS telephony guardian.

Scope: local systemd + local source contract only.
It never touches client data, provider APIs, MCN, telephony calls, or paid AI.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path("/root/BORIS/backend")
PYTHON = ROOT / "venv/bin/python"
CONTRACT = ROOT / "scripts/phone-guardian-contract-check.py"
GUARDIAN_SERVICE = "boris-telephony-guardian.service"
GUARDIAN_TIMER = "boris-telephony-guardian.timer"
PREFLIGHT = ROOT / "scripts/phone-python-bytecode-preflight.py"
PREFLIGHT_DROPIN = Path("/etc/systemd/system/boris-telephony-guardian.service.d/65-python-bytecode-preflight.conf")
EXPECTED_PREFLIGHT_DROPIN = (
    "[Service]\n"
    f"ExecStartPre={PYTHON} {PREFLIGHT}\n"
)
DROPIN = Path("/etc/systemd/system/boris-telephony-guardian.service.d/70-mcn-acceptance-contract.conf")
EXPECTED_DROPIN = (
    "[Service]\n"
    f"ExecStartPre={PYTHON} {CONTRACT}\n"
)

ALERT_SCRIPT = ROOT / "scripts/phone-guardian-failure-notify.py"
ALERT_SERVICE_NAME = "boris-telephony-supervisor-alert.service"
ALERT_SERVICE = Path(f"/etc/systemd/system/{ALERT_SERVICE_NAME}")
ESCALATION_DROPIN = Path(
    "/etc/systemd/system/boris-telephony-supervisor.service.d/80-failure-escalation.conf"
)
EXPECTED_ALERT_SERVICE = (
    "[Unit]\n"
    "Description=BORIS Telephony Supervisor Persistent Failure Escalation\n"
    "After=network-online.target\n"
    "Wants=network-online.target\n"
    "\n"
    "[Service]\n"
    "Type=oneshot\n"
    "User=root\n"
    "WorkingDirectory=/root/BORIS/backend\n"
    "EnvironmentFile=-/root/BORIS/backend/.env\n"
    f"ExecStart={PYTHON} {ALERT_SCRIPT}\n"
    "Nice=15\n"
)
EXPECTED_ESCALATION_DROPIN = (
    "[Unit]\n"
    f"OnFailure={ALERT_SERVICE_NAME}\n"
    "\n"
    "[Service]\n"
    f"ExecStartPost={PYTHON} {ALERT_SCRIPT} --mark-healthy\n"
)


def _run(args: list[str], timeout: int = 30) -> dict:
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": cp.returncode == 0,
            "rc": int(cp.returncode),
            "stdout": (cp.stdout or "").strip(),
            "stderr": (cp.stderr or "").strip(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "rc": 255,
            "stdout": "",
            "stderr": type(exc).__name__,
        }


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


def _contract_state() -> dict:
    if not CONTRACT.is_file():
        return {"ok": False, "reason": "contract_script_missing"}
    out = _run([str(PYTHON), str(CONTRACT)], timeout=20)
    return {
        "ok": bool(out["ok"] and "TELEPHONY_GUARDIAN_CONTRACT=PASS" in out["stdout"]),
        "rc": out["rc"],
        "result": (out["stdout"] or out["stderr"])[:500],
    }


def _preflight_dropin_state() -> dict:
    try:
        actual = PREFLIGHT_DROPIN.read_text(encoding="utf-8") if PREFLIGHT_DROPIN.is_file() else ""
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "loaded": False}
    loaded = False
    # ExecStartPre is a multi-valued systemd property. systemctl show
    # returns one line per command, while _show() keeps only the last value
    # for ordinary scalar properties. Inspect raw stdout here so both
    # preflight and contract commands are visible.
    shown = _run(["systemctl", "show", GUARDIAN_SERVICE, "-p", "ExecStartPre", "--no-pager"], timeout=12)
    if shown["ok"]:
        loaded_text = str(shown.get("stdout") or "")
        loaded = str(PREFLIGHT) in loaded_text and "scripts/python_bytecode_preflight.py" not in loaded_text
    return {
        "ok": bool(PREFLIGHT.is_file() and actual == EXPECTED_PREFLIGHT_DROPIN and loaded),
        "script_present": PREFLIGHT.is_file(),
        "file_exact": actual == EXPECTED_PREFLIGHT_DROPIN,
        "loaded": loaded,
    }


def _restore_preflight_dropin() -> bool:
    if not PREFLIGHT.is_file():
        return False
    if not _atomic_write_text(PREFLIGHT_DROPIN, EXPECTED_PREFLIGHT_DROPIN):
        return False
    reload_out = _run(["systemctl", "daemon-reload"], timeout=20)
    return bool(reload_out["ok"])


def _dropin_state() -> dict:
    try:
        actual = DROPIN.read_text(encoding="utf-8") if DROPIN.is_file() else ""
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "loaded": False}
    loaded = False
    show = _show(GUARDIAN_SERVICE, "ExecStartPre")
    if "_error" not in show:
        loaded = str(CONTRACT) in str(show.get("ExecStartPre") or "")
    return {
        "ok": actual == EXPECTED_DROPIN and loaded,
        "file_exact": actual == EXPECTED_DROPIN,
        "loaded": loaded,
    }


def _atomic_write_dropin() -> bool:
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".70-mcn-acceptance-contract.", dir=str(DROPIN.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(EXPECTED_DROPIN)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, DROPIN)
        dir_fd = os.open(str(DROPIN.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _atomic_write_text(path: Path, content: str) -> bool:
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        tmp = None
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _escalation_state() -> dict:
    try:
        alert_exact = ALERT_SERVICE.is_file() and ALERT_SERVICE.read_text(encoding="utf-8") == EXPECTED_ALERT_SERVICE
    except Exception:
        alert_exact = False
    try:
        dropin_exact = (
            ESCALATION_DROPIN.is_file()
            and ESCALATION_DROPIN.read_text(encoding="utf-8") == EXPECTED_ESCALATION_DROPIN
        )
    except Exception:
        dropin_exact = False

    sup = _show("boris-telephony-supervisor.service", "OnFailure", "ExecStartPost", "LoadState")
    alert = _show(ALERT_SERVICE_NAME, "LoadState", "ExecStart")
    on_failure_loaded = ALERT_SERVICE_NAME in str(sup.get("OnFailure") or "")
    post_loaded = (
        str(ALERT_SCRIPT) in str(sup.get("ExecStartPost") or "")
        and "--mark-healthy" in str(sup.get("ExecStartPost") or "")
    )
    alert_loaded = alert.get("LoadState") == "loaded" and str(ALERT_SCRIPT) in str(alert.get("ExecStart") or "")
    script_present = ALERT_SCRIPT.is_file()
    return {
        "ok": bool(alert_exact and dropin_exact and on_failure_loaded and post_loaded and alert_loaded and script_present),
        "script_present": script_present,
        "alert_file_exact": alert_exact,
        "dropin_file_exact": dropin_exact,
        "on_failure_loaded": on_failure_loaded,
        "post_loaded": post_loaded,
        "alert_loaded": alert_loaded,
    }


def _restore_escalation_wiring() -> bool:
    if not ALERT_SCRIPT.is_file():
        return False
    alert_ok = _atomic_write_text(ALERT_SERVICE, EXPECTED_ALERT_SERVICE)
    dropin_ok = _atomic_write_text(ESCALATION_DROPIN, EXPECTED_ESCALATION_DROPIN)
    if not (alert_ok and dropin_ok):
        return False
    reload_out = _run(["systemctl", "daemon-reload"], timeout=20)
    return bool(reload_out["ok"])


def _timer_state() -> dict:
    state = _show(GUARDIAN_TIMER, "ActiveState", "UnitFileState", "LastTriggerUSec")
    return {
        "active": state.get("ActiveState") == "active",
        "enabled": state.get("UnitFileState") == "enabled",
        "last_trigger": state.get("LastTriggerUSec"),
        "raw": state,
    }


def _service_state() -> dict:
    state = _show(
        GUARDIAN_SERVICE,
        "ActiveState",
        "Result",
        "ExecMainStatus",
        "InactiveEnterTimestamp",
    )
    try:
        exit_ok = int(state.get("ExecMainStatus") or 0) == 0
    except Exception:
        exit_ok = False
    active_state = state.get("ActiveState") or ""
    in_progress = active_state in {"activating", "reloading"}
    success = state.get("Result") == "success" and exit_ok and active_state != "failed"
    return {
        "success": success,
        "in_progress": in_progress,
        "active_state": active_state,
        "result": state.get("Result"),
        "exec_main_status": state.get("ExecMainStatus"),
        "last_finished": state.get("InactiveEnterTimestamp"),
        "raw": state,
    }


def supervise() -> dict:
    before = {
        "escalation": _escalation_state(),
        "preflight_dropin": _preflight_dropin_state(),
        "contract": _contract_state(),
        "dropin": _dropin_state(),
        "timer": _timer_state(),
        "service": _service_state(),
    }
    actions: list[str] = []
    errors: list[str] = []

    # Protect the protection first. If OnFailure/healthy-marker wiring was deleted
    # or drifted, restore it before touching the guardian. This way a later
    # unhealable guardian failure still has a bounded escalation path.
    if not before["escalation"]["ok"]:
        if _restore_escalation_wiring():
            escalation_now = _escalation_state()
            if escalation_now["ok"]:
                actions.append("restored_supervisor_escalation")
            else:
                errors.append("supervisor_escalation_reload_unverified")
        else:
            errors.append("supervisor_escalation_restore_failed")

    if not before["preflight_dropin"]["ok"]:
        if _restore_preflight_dropin():
            preflight_now = _preflight_dropin_state()
            if preflight_now["ok"]:
                actions.append("restored_phone_bytecode_preflight")
            else:
                errors.append("phone_preflight_reload_unverified")
        else:
            errors.append("phone_preflight_restore_failed")

    # Fail closed: never start/restart guardian if its local no-call contract fails.
    if not before["contract"]["ok"]:
        after = {
            **before,
            "escalation": _escalation_state(),
            "preflight_dropin": _preflight_dropin_state(),
        }
        return {
            "status": "blocked_contract",
            "before": before,
            "actions": actions,
            "after": after,
            "errors": errors + ["telephony_guardian_contract_failed"],
            "owner_action_required": False,
        }

    if not before["dropin"]["ok"]:
        if _atomic_write_dropin():
            reload_out = _run(["systemctl", "daemon-reload"], timeout=20)
            if reload_out["ok"]:
                actions.append("restored_contract_dropin")
            else:
                errors.append("daemon_reload_failed")
        else:
            errors.append("dropin_restore_failed")

    timer_now = _timer_state()
    if not timer_now["active"] or not timer_now["enabled"]:
        out = _run(["systemctl", "enable", "--now", GUARDIAN_TIMER], timeout=30)
        if out["ok"]:
            actions.append("enabled_guardian_timer")
        else:
            errors.append("guardian_timer_enable_failed")

    service_now = _service_state()
    # Do not race a normal minute tick that is already running.
    if not service_now["success"] and not service_now["in_progress"]:
        _run(["systemctl", "reset-failed", GUARDIAN_SERVICE], timeout=12)
        out = _run(["systemctl", "start", GUARDIAN_SERVICE], timeout=45)
        if out["ok"]:
            actions.append("retried_guardian_service")
        else:
            errors.append("guardian_service_retry_failed")

    after = {
        "escalation": _escalation_state(),
        "preflight_dropin": _preflight_dropin_state(),
        "contract": _contract_state(),
        "dropin": _dropin_state(),
        "timer": _timer_state(),
        "service": _service_state(),
    }
    healthy = (
        after["escalation"]["ok"]
        and after["preflight_dropin"]["ok"]
        and after["contract"]["ok"]
        and after["dropin"]["ok"]
        and after["timer"]["active"]
        and after["timer"]["enabled"]
        and (after["service"]["success"] or after["service"]["in_progress"])
        and not errors
    )
    return {
        "status": "ok" if healthy else "degraded",
        "before": before,
        "actions": actions,
        "after": after,
        "errors": errors,
        "owner_action_required": False,
        "truth": "local infrastructure supervisor only; no provider/client/paid boundary is called",
    }


def main() -> int:
    result = supervise()
    print(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
