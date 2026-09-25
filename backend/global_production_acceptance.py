# GLOBAL_ACCEPTANCE_DASHBOARD_HARD_GATE_V1: client dashboard availability participates in final global health truth.
#!/usr/bin/env python3
"""BORIS global production acceptance state machine.

This is intentionally domain-agnostic. It never mutates Avito, marketing,
social, CRM, telephony, feed or client data. It only classifies the global
runtime/deploy state and tells the existing production guardian when the
canonical deploy daemon itself needs a bounded restart.

GLOBAL_PRODUCTION_ACCEPTANCE_V1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path('/root/BORIS/backend')
STATE_DEFAULT = ROOT / 'data' / 'global_production_acceptance.json'
HEAL_BACKOFF_SEC = 30 * 60
SOURCE_QUIET_SEC = 180
PERSISTENT_STALL_TICKS = 2
ESCALATE_STALL_TICKS = 6


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _http_ok(url: str, timeout: float = 4.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {'ok': 200 <= r.status < 300, 'status': int(r.status)}
    except Exception as exc:
        return {'ok': False, 'status': None, 'error_type': type(exc).__name__}


def _rolling_active() -> bool:
    try:
        cp = subprocess.run(
            ['pgrep', '-f', '^/bin/bash /root/BORIS/backend/rolling_restart_backend.sh$'],
            capture_output=True, text=True, timeout=3,
        )
        return cp.returncode == 0 and bool(cp.stdout.strip())
    except Exception:
        return False


def _newest_runtime_source_age(now: float | None = None) -> float | None:
    now = time.time() if now is None else float(now)
    newest = None
    roots = [ROOT / 'app', ROOT]
    for base in roots:
        try:
            iterator = base.rglob('*') if base.name == 'app' else base.iterdir()
            for p in iterator:
                try:
                    if not p.is_file() or p.suffix not in {'.py', '.sh'}:
                        continue
                    if '/backups/' in str(p) or '/.boris_backups/' in str(p) or '/venv/' in str(p):
                        continue
                    st = p.stat()
                    changed_at = max(st.st_mtime, st.st_ctime)
                    newest = changed_at if newest is None or changed_at > newest else newest
                except OSError:
                    continue
        except OSError:
            continue
    return None if newest is None else max(0.0, now - newest)


def decide(
    contract: dict[str, Any],
    contract_rc: int,
    health: dict[str, dict[str, Any]],
    rolling_active: bool,
    source_age_sec: float | None,
    previous: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Pure decision function used by runtime and regression tests."""
    previous = previous or {}
    now = time.time() if now is None else float(now)
    all_health = all(bool((health.get(k) or {}).get('ok')) for k in ('api1', 'api2', 'public', 'dashboard'))
    public_ok = bool((health.get('public') or {}).get('ok'))
    accounts = int(contract.get('accounts') or 0)
    passed = int(contract.get('passed_accounts') or 0)
    cstatus = str(contract.get('status') or '').lower()
    contract_pass = contract_rc == 0 and cstatus == 'pass' and accounts > 0 and passed == accounts
    quiet = source_age_sec is not None and source_age_sec >= SOURCE_QUIET_SEC
    contract_converging = cstatus == 'converging' or contract_rc == 75

    prev_stall = int(previous.get('consecutive_stalled_ticks') or 0)
    last_heal = float(previous.get('last_self_heal_at') or 0)
    last_pass_at = previous.get('last_pass_at')

    # GLOBAL_ACCEPTANCE_ACTIVE_ROLL_NOT_PASS_V1: a contract sampled in the tiny
    # window between replica steps is useful evidence but is not a stable final
    # certificate. Keep acceptance in convergence until the canonical roll ends.
    if rolling_active:
        state = 'converging' if all_health else ('converging_degraded_redundancy' if public_ok else 'failed')
        stall = 0 if public_ok else prev_stall + 1
        owner_action = False
        heal_action = None
        reason = ('canonical_deploy_in_progress' if all_health else
                  'canonical_deploy_in_progress_redundancy_degraded' if public_ok else
                  'public_health_failed_during_canonical_deploy')
    elif contract_pass and all_health:
        state = 'pass'
        stall = 0
        owner_action = False
        heal_action = None
        last_pass_at = now
        reason = 'contract_and_all_runtime_health_pass'
    elif contract_converging and not quiet:
        state = 'converging'
        stall = 0
        owner_action = False
        heal_action = None
        reason = 'source_not_quiet'
    elif contract_converging and quiet and not rolling_active:
        state = 'stalled_convergence'
        stall = prev_stall + 1
        heal_due = stall >= PERSISTENT_STALL_TICKS and now - last_heal >= HEAL_BACKOFF_SEC
        heal_action = 'restart_boris_deploy' if heal_due else None
        owner_action = bool(stall >= ESCALATE_STALL_TICKS and last_heal > 0 and now - last_heal >= 15 * 60)
        reason = 'generation_drift_persisted_after_source_quiet_without_active_roll'
    elif not public_ok and rolling_active:
        state = 'converging_degraded_redundancy'
        stall = 0
        owner_action = False
        heal_action = None
        reason = 'public_health_degraded_during_active_canonical_roll'
    else:
        state = 'failed'
        stall = prev_stall + 1
        heal_due = quiet and not rolling_active and stall >= PERSISTENT_STALL_TICKS and now - last_heal >= HEAL_BACKOFF_SEC
        heal_action = 'restart_boris_deploy' if heal_due else None
        owner_action = bool(stall >= ESCALATE_STALL_TICKS and last_heal > 0 and now - last_heal >= 15 * 60)
        reason = 'global_acceptance_failed_after_runtime_check'

    return {
        'contract': 'BORIS_GLOBAL_PRODUCTION_ACCEPTANCE',
        'version': 1,
        'checked_at': now,
        'state': state,
        'reason': reason,
        'contract_status': cstatus or None,
        'contract_rc': int(contract_rc),
        'accounts': accounts,
        'passed_accounts': passed,
        'generation_fingerprint': contract.get('generation_fingerprint'),
        'health': health,
        'all_health_ok': all_health,
        'rolling_active': bool(rolling_active),
        'source_age_sec': None if source_age_sec is None else round(float(source_age_sec), 3),
        'source_quiet': bool(quiet),
        'consecutive_stalled_ticks': stall,
        'self_heal_action': heal_action,
        'last_self_heal_at': last_heal or None,
        'owner_action_required': owner_action,
        'last_pass_at': last_pass_at,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract-file', required=True)
    ap.add_argument('--contract-rc', type=int, required=True)
    ap.add_argument('--state-file', default=str(STATE_DEFAULT))
    args = ap.parse_args()

    contract = _read_json(Path(args.contract_file))
    state_path = Path(args.state_file)
    previous = _read_json(state_path)
    health = {
        'api1': _http_ok('http://127.0.0.1:8000/health'),
        'api2': _http_ok('http://127.0.0.1:8001/health'),
        'public': _http_ok('https://boris-ai.pro/health'),
        'dashboard': _http_ok('https://boris-ai.pro/dashboard'),
    }
    result = decide(
        contract=contract,
        contract_rc=args.contract_rc,
        health=health,
        rolling_active=_rolling_active(),
        source_age_sec=_newest_runtime_source_age(),
        previous=previous,
    )
    _atomic_write(state_path, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['state'] == 'pass' else (75 if result['state'].startswith('converging') else 2)


if __name__ == '__main__':
    raise SystemExit(main())
