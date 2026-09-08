#!/root/BORIS/backend/venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.ext_api import aiprov, sched

STATE = Path("/var/lib/boris-integrity/workstream-scopes/development_provider_watch.json")
JOURNAL = Path("/var/lib/boris-integrity/workstream-scopes/development_provider_watch.jsonl")
DEDUP_SECONDS = 6 * 60 * 60
FREE_REPROBE_MINUTES = 30

FREE = ("gemini_cli", "claude_code")
HUMAN_STATES = {"AUTH_ERROR", "UNAVAILABLE_BILLING", "UNSUPPORTED_LOCATION", "DISABLED_BY_OWNER"}
CANONICAL_CLI_HOME = "/root"
CANONICAL_CLI_BIN = "/usr/local/lib/boris-dev-cli"
CANONICAL_GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
CANONICAL_GEMINI_WRAPPER = Path("/usr/local/lib/boris-dev-cli/gemini")
CANONICAL_MODEL_DROPINS = (
    Path("/etc/systemd/system/boris-dispatcher.service.d/70-gemini-model-fallback.conf"),
    Path("/etc/systemd/system/boris-lead.service.d/70-gemini-model-fallback.conf"),
    Path("/etc/systemd/system/boris-executor.service.d/70-gemini-model-fallback.conf"),
    *tuple(
        Path(
            f"/etc/systemd/system/boris-executor-{idx}.service.d/"
            "70-gemini-model-fallback.conf"
        )
        for idx in range(2, 9)
    ),
)
MODEL_CONFIG_BACKUPS = Path(
    "/var/lib/boris-integrity/workstream-scopes/model-runtime-backups"
)
CANONICAL_CLI_PROXY_ENV = Path("/etc/boris-dev-cli-proxy.env")
CANONICAL_PROXY_KEYS = {
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
}
WRONG_PROBE_HOME_MARKER = "/opt/sentinelx-cloud-core/.gemini"
SAFE_GEMINI_PROBE_MARKERS = (
    'Return only JSON: {"status":"ok"}',
    "Reply exactly OK",
)
TRANSIENT_PROVIDER_BLOCK_PREFIX = (
    "all free development providers unavailable; paid fallback denied:"
)
TRANSIENT_PROVIDER_KINDS = {
    "TEMP_ERROR",
    "RATE_LIMITED",
    "UNAVAILABLE_BILLING",
}
REVIEW_QUOTA_LOOP_MARKER = (
    "provider_unavailable: review provider quota/cooling"
)
REVIEW_QUOTA_LOOP_WINDOW_SECONDS = 120
REVIEW_QUOTA_LOOP_THRESHOLD = 2
REVIEW_QUOTA_COOLDOWN_SECONDS = 300
# Persisted non-AVAILABLE state must not become runnable merely because a short
# retry_at elapsed between two 5-minute guard cycles. The watcher probes on its
# own plan and a successful probe explicitly marks AVAILABLE, so this latch is
# safety-only and does not delay real recovery.
PROVIDER_PROBE_SUCCESS_LATCH_SECONDS = FREE_REPROBE_MINUTES * 60
GEMINI_FALLBACK_UNTIL = Path("/run/boris/gemini-model-fallback-until")
GEMINI_DOUBLE_DAILY_LATCH = Path("/run/boris/gemini-double-daily-quota-until")
GEMINI_DAILY_QUOTA_SAFETY_SECONDS = 15
WRAPPER_PROVIDER_COOLDOWN_MARKER = "BORIS_GEMINI_PROVIDER_COOLDOWN_V1"
WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER = "BORIS_GEMINI_FALLBACK_ONLY_COOLDOWN_V1"
WRAPPER_DAILY_QUOTA_SANITIZE_MARKER = "BORIS_GEMINI_DAILY_QUOTA_SANITIZE_V1"
WRAPPER_DAILY_PROBE_LATCH_MARKER = "BORIS_GEMINI_DAILY_PROBE_LATCH_V1"


def _canonicalize_wrapper_model_text(raw: str) -> str:
    """Make every legacy fallback alias resolve to the verified free model."""
    text_value = str(raw or "")
    # Canonicalize already-installed sanitizer revisions too. The marker alone
    # must not freeze an older implementation forever.
    text_value = text_value.replace(
        "report=$(grep -Eo '/tmp/gemini-client-error-",
        "report=$(grep -hEo '/tmp/gemini-client-error-",
    )
    for legacy_model in (
        "flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3.5-flash-lite",
    ):
        text_value = text_value.replace(
            '${BORIS_GEMINI_FALLBACK_MODEL:-' + legacy_model + '}',
            '${BORIS_GEMINI_FALLBACK_MODEL:-'
            + CANONICAL_GEMINI_FALLBACK_MODEL
            + '}',
        )
    fallback_line = (
        'FALLBACK_MODEL="${BORIS_GEMINI_FALLBACK_MODEL:-'
        + CANONICAL_GEMINI_FALLBACK_MODEL
        + '}"'
    )
    canonical_model_line = (
        'CANONICAL_FALLBACK_MODEL="${BORIS_GEMINI_CANONICAL_FALLBACK_MODEL:-'
        + CANONICAL_GEMINI_FALLBACK_MODEL
        + '}"'
    )
    alias_case_line = (
        '  flash-lite|gemini-2.5-flash-lite|gemini-3.5-flash-lite) '
        'FALLBACK_MODEL="$CANONICAL_FALLBACK_MODEL" ;;'
    )
    alias_guard = (
        canonical_model_line + "\n"
        'case "$FALLBACK_MODEL" in\n'
        + alias_case_line + "\n"
        'esac'
    )
    if "CANONICAL_FALLBACK_MODEL=" in text_value:
        text_value = re.sub(
            r'CANONICAL_FALLBACK_MODEL="\$\{BORIS_GEMINI_CANONICAL_FALLBACK_MODEL:-[^}]+\}"',
            canonical_model_line,
            text_value,
            count=1,
        )
        text_value = re.sub(
            r'  flash-lite\|gemini-2\.5-flash-lite(?:\|gemini-3\.5-flash-lite)?\) FALLBACK_MODEL="\$CANONICAL_FALLBACK_MODEL" ;;',
            alias_case_line,
            text_value,
            count=1,
        )
    elif fallback_line in text_value:
        text_value = text_value.replace(
            fallback_line,
            fallback_line + "\n" + alias_guard,
            1,
        )

    # A second recovery loop (recover.py) can request its own Gemini health
    # probe based only on provider.updated_at. When both primary and fallback
    # have proven daily quota exhaustion, those probes must be answered locally
    # until reset instead of touching Gemini again. Keep this in the wrapper so
    # every caller shares one fail-closed transport latch without modifying the
    # occupied autonomous_dev_execution workstream.
    if WRAPPER_DAILY_PROBE_LATCH_MARKER not in text_value:
        log_line = 'LOG_FILE="${BORIS_GEMINI_FALLBACK_LOG:-/run/boris/gemini-model-fallback.log}"'
        latch_line = 'DAILY_LATCH_FILE="${BORIS_GEMINI_DAILY_LATCH_STATE:-/run/boris/gemini-double-daily-quota-until}"'
        if latch_line not in text_value and log_line in text_value:
            text_value = text_value.replace(log_line, log_line + "\n" + latch_line, 1)

        old_headless = (
            'is_headless=0\n'
            'for arg in "$@"; do\n'
            '  case "$arg" in\n'
            '    -p|--prompt) is_headless=1 ;;\n'
            '  esac\n'
            'done'
        )
        new_headless = (
            'is_headless=0\n'
            'is_health_probe=0\n'
            'for arg in "$@"; do\n'
            '  case "$arg" in\n'
            '    -p|--prompt) is_headless=1 ;;\n'
            '    \'Return only JSON: {"status":"ok"}\'|\'Reply exactly OK\') is_health_probe=1 ;;\n'
            '  esac\n'
            'done'
        )
        if old_headless in text_value:
            text_value = text_value.replace(old_headless, new_headless, 1)

        until_anchor = (
            'case "$until" in\n'
            "  ''|*[!0-9]*) until=0 ;;\n"
            'esac\n\n'
            'if [ "$until" -gt "$now" ]; then'
        )
        if until_anchor in text_value:
            latch_guard = (
                'case "$until" in\n'
                "  ''|*[!0-9]*) until=0 ;;\n"
                'esac\n\n'
                '# ' + WRAPPER_DAILY_PROBE_LATCH_MARKER + '\n'
                'daily_latch=0\n'
                'if [ -r "$DAILY_LATCH_FILE" ]; then\n'
                '  read -r daily_latch <"$DAILY_LATCH_FILE" || daily_latch=0\n'
                'fi\n'
                'case "$daily_latch" in\n'
                "  ''|*[!0-9]*) daily_latch=0 ;;\n"
                'esac\n'
                'if [ "$daily_latch" -gt 0 ] && [ "$daily_latch" -le "$now" ]; then\n'
                '  rm -f "$DAILY_LATCH_FILE"\n'
                '  daily_latch=0\n'
                'fi\n'
                'if [ "$is_health_probe" -eq 1 ] && [ "$daily_latch" -gt "$now" ]; then\n'
                '  echo "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 '
                'reset_epoch=$daily_latch model=$FALLBACK_MODEL report=local-double-daily-quota-latch" >&2\n'
                '  exit 1\n'
                'fi\n\n'
                'if [ "$until" -gt "$now" ]; then'
            )
            text_value = text_value.replace(until_anchor, latch_guard, 1)

        # Once a real call succeeds the double-quota latch is stale.
        text_value = text_value.replace(
            '  early_rc=$?\n  # ' + WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER,
            '  early_rc=$?\n  [ "$early_rc" -eq 0 ] && rm -f "$DAILY_LATCH_FILE"\n  # '
            + WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER,
            1,
        )
        text_value = text_value.replace(
            '  rm -f "$STATE_FILE"\n  exit 0',
            '  rm -f "$STATE_FILE" "$DAILY_LATCH_FILE"\n  exit 0',
            1,
        )
        text_value = text_value.replace(
            '  if [ "$frc" -eq 0 ]; then\n    cat "${out}.fallback"',
            '  if [ "$frc" -eq 0 ]; then\n    rm -f "$DAILY_LATCH_FILE"\n    cat "${out}.fallback"',
            1,
        )

        # Persist the latch only on explicit daily quota, never generic 429.
        text_value = text_value.replace(
            '  if [ "$early_rc" -ne 0 ] && grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "$early_out" "$early_err"; then\n'
            '    report=$(grep -hEo ',
            '  if [ "$early_rc" -ne 0 ] && grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "$early_out" "$early_err"; then\n'
            '    printf \'%s\\n\' "$until" >"$DAILY_LATCH_FILE"\n'
            '    report=$(grep -hEo ',
            1,
        )
        text_value = text_value.replace(
            '  if grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "${out}.fallback" "${err}.fallback"; then\n'
            '    report=$(grep -hEo ',
            '  if grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "${out}.fallback" "${err}.fallback"; then\n'
            '    printf \'%s\\n\' "$reset" >"$DAILY_LATCH_FILE"\n'
            '    report=$(grep -hEo ',
            1,
        )

    # Immediate control-plane feedback on a proven double-quota failure. The
    # wrapper already owns the exact stderr for primary+fallback; marking the
    # shared provider state here closes the race before lead records
    # BLOCKED_INFRA and dispatcher can requeue completed review work.
    if (
        WRAPPER_PROVIDER_COOLDOWN_MARKER not in text_value
        and '  frc=$?\n' in text_value
    ):
        cooldown = (
            '  frc=$?\n'
            '  # ' + WRAPPER_PROVIDER_COOLDOWN_MARKER + '\n'
            '  if [ "$frc" -ne 0 ] && grep -Eiq '
            "'daily quota|quota.*exhaust|exhausted.*quota|resource_exhausted|"
            "usage limit reached|quota exceeded|exceeded.*quota|status[^0-9]*429|code[^0-9]*429' "
            '"${out}.fallback" "${err}.fallback"; then\n'
            '    ( cd /root/BORIS/backend && HOME=/root '
            '/root/BORIS/backend/venv/bin/python -c '
            "'from app.ext_api import aiprov; "
            'aiprov.mark("gemini_cli", aiprov.RATE_LIMITED, '
            '"Gemini wrapper: primary and fallback quota exhausted; immediate anti-loop cooldown", '
            "retry_after=300)' ) >/dev/null 2>&1 || true\n"
            '  fi\n'
        )
        text_value = text_value.replace('  frc=$?\n', cooldown, 1)

    fallback_only = (
        'if [ "$until" -gt "$now" ]; then\n'
        '  exec "$REAL_GEMINI" -m "$FALLBACK_MODEL" "$@"\n'
        'fi'
    )
    if (
        WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER not in text_value
        and fallback_only in text_value
    ):
        fallback_capture = (
            'if [ "$until" -gt "$now" ]; then\n'
            '  early_out=$(mktemp /tmp/boris-gemini-fallback-out.XXXXXX)\n'
            '  early_err=$(mktemp /tmp/boris-gemini-fallback-err.XXXXXX)\n'
            '  "$REAL_GEMINI" -m "$FALLBACK_MODEL" "$@" >"$early_out" 2>"$early_err"\n'
            '  early_rc=$?\n'
            '  # ' + WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER + '\n'
            '  if [ "$early_rc" -ne 0 ] && grep -Eiq '
            "'daily quota|quota.*exhaust|exhausted.*quota|resource_exhausted|"
            "usage limit reached|quota exceeded|exceeded.*quota|status[^0-9]*429|code[^0-9]*429' "
            '"$early_out" "$early_err"; then\n'
            '    ( cd /root/BORIS/backend && HOME=/root '
            '/root/BORIS/backend/venv/bin/python -c '
            "'from app.ext_api import aiprov; "
            'aiprov.mark("gemini_cli", aiprov.RATE_LIMITED, '
            '"Gemini wrapper: fallback quota exhausted; immediate anti-loop cooldown", '
            "retry_after=300)' ) >/dev/null 2>&1 || true\n"
            '  fi\n'
            '  cat "$early_out"\n'
            '  cat "$early_err" >&2\n'
            '  rm -f "$early_out" "$early_err"\n'
            '  exit "$early_rc"\n'
            'fi'
        )
        text_value = text_value.replace(fallback_only, fallback_capture, 1)

    # A Gemini CLI daily-quota failure can contain a nested short-window hint
    # (for example "Please retry in 11s") inside the same error. The current
    # aiprov short-retry parser sees that nested hint and can re-open the
    # provider before the daily quota reset. Keep raw diagnostics in Gemini's
    # own /tmp report, but expose only a deterministic HTTP429 daily-quota
    # marker to the parent process. Ordinary short rate limits remain raw.
    if WRAPPER_DAILY_QUOTA_SANITIZE_MARKER not in text_value:
        early_emit = (
            '  cat "$early_out"\n'
            '  cat "$early_err" >&2\n'
            '  rm -f "$early_out" "$early_err"\n'
        )
        if early_emit in text_value:
            early_sanitized = (
                '  # ' + WRAPPER_DAILY_QUOTA_SANITIZE_MARKER + '\n'
                '  if [ "$early_rc" -ne 0 ] && grep -Eiq '
                "'daily quota|exhausted[^\\n]*daily quota' "
                '"$early_out" "$early_err"; then\n'
                '    report=$(grep -hEo '
                "'/tmp/gemini-client-error-[^[:space:]]+\\.json' "
                '"$early_err" "$early_out" 2>/dev/null | head -n1 || true)\n'
                '    echo "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 '
                'reset_epoch=$until model=$FALLBACK_MODEL report=$report" >&2\n'
                '  else\n'
                '    cat "$early_out"\n'
                '    cat "$early_err" >&2\n'
                '  fi\n'
                '  rm -f "$early_out" "$early_err"\n'
            )
            text_value = text_value.replace(early_emit, early_sanitized, 1)

        failure_emit = (
            '  fi\n'
            '  cat "${out}.fallback"\n'
            '  cat "${err}.fallback" >&2\n'
            '  echo "BORIS_GEMINI_MODEL_FALLBACK_FAILED primary_rc=$rc fallback_rc=$frc" >&2\n'
        )
        if failure_emit in text_value:
            failure_sanitized = (
                '  fi\n'
                '  # ' + WRAPPER_DAILY_QUOTA_SANITIZE_MARKER + '\n'
                '  if grep -Eiq '
                "'daily quota|exhausted[^\\n]*daily quota' "
                '"${out}.fallback" "${err}.fallback"; then\n'
                '    report=$(grep -hEo '
                "'/tmp/gemini-client-error-[^[:space:]]+\\.json' "
                '"${err}.fallback" "${out}.fallback" 2>/dev/null | head -n1 || true)\n'
                '    echo "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED HTTP429 '
                'reset_epoch=$reset model=$FALLBACK_MODEL report=$report" >&2\n'
                '  else\n'
                '    cat "${out}.fallback"\n'
                '    cat "${err}.fallback" >&2\n'
                '  fi\n'
                '  echo "BORIS_GEMINI_MODEL_FALLBACK_FAILED primary_rc=$rc fallback_rc=$frc" >&2\n'
            )
            text_value = text_value.replace(failure_emit, failure_sanitized, 1)

    # Normalize installed latch revisions on every pass. This intentionally
    # runs after sanitizer insertion so an upgrade from a much older wrapper
    # converges in one pass rather than freezing a partial marker revision.
    text_value = text_value.replace(
        '  early_rc=$?\n  # ' + WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER,
        '  early_rc=$?\n  [ "$early_rc" -eq 0 ] && rm -f "$DAILY_LATCH_FILE"\n  # '
        + WRAPPER_FALLBACK_ONLY_COOLDOWN_MARKER,
        1,
    )
    text_value = text_value.replace(
        '  rm -f "$STATE_FILE"\n  exit 0',
        '  rm -f "$STATE_FILE" "$DAILY_LATCH_FILE"\n  exit 0',
        1,
    )
    text_value = text_value.replace(
        '  if [ "$frc" -eq 0 ]; then\n    cat "${out}.fallback"',
        '  if [ "$frc" -eq 0 ]; then\n    rm -f "$DAILY_LATCH_FILE"\n    cat "${out}.fallback"',
        1,
    )
    text_value = text_value.replace(
        '  if [ "$early_rc" -ne 0 ] && grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "$early_out" "$early_err"; then\n'
        '    report=$(grep -hEo ',
        '  if [ "$early_rc" -ne 0 ] && grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "$early_out" "$early_err"; then\n'
        '    printf \'%s\\n\' "$until" >"$DAILY_LATCH_FILE"\n'
        '    report=$(grep -hEo ',
        1,
    )
    text_value = text_value.replace(
        '  if grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "${out}.fallback" "${err}.fallback"; then\n'
        '    report=$(grep -hEo ',
        '  if grep -Eiq \'daily quota|exhausted[^\\n]*daily quota\' "${out}.fallback" "${err}.fallback"; then\n'
        '    printf \'%s\\n\' "$reset" >"$DAILY_LATCH_FILE"\n'
        '    report=$(grep -hEo ',
        1,
    )
    return text_value


def _canonicalize_dropin_model_text(raw: str) -> str:
    """Keep the systemd environment on the one verified fallback model."""
    lines = str(raw or "").splitlines()
    expected = (
        "Environment=BORIS_GEMINI_FALLBACK_MODEL="
        + CANONICAL_GEMINI_FALLBACK_MODEL
    )
    changed = False
    out = []
    for line in lines:
        if line.startswith("Environment=BORIS_GEMINI_FALLBACK_MODEL="):
            if not changed:
                out.append(expected)
                changed = True
            continue
        out.append(line)
    if not changed:
        if out and out[-1].strip():
            out.append("")
        if not any(line.strip() == "[Service]" for line in out):
            out.append("[Service]")
        out.append(expected)
    return "\n".join(out).rstrip() + "\n"


def _runtime_model_config_health(repair=False) -> dict:
    """Detect and self-heal stale Gemini runtime model configuration.

    Wrapper repair is immediately effective for new CLI subprocesses. Unit
    drop-ins are repaired and daemon-reloaded, but live executor/lead processes
    are never killed: the wrapper alias guard makes even an old inherited
    flash-lite value resolve to the canonical model safely.
    """
    wrapper_expected = (
        '${BORIS_GEMINI_FALLBACK_MODEL:-'
        + CANONICAL_GEMINI_FALLBACK_MODEL
        + '}'
    )
    expected_env = (
        "Environment=BORIS_GEMINI_FALLBACK_MODEL="
        + CANONICAL_GEMINI_FALLBACK_MODEL
    )
    checks = []
    changed_paths = []
    errors = []
    repair_allowed = bool(repair and os.geteuid() == 0)

    targets = [(CANONICAL_GEMINI_WRAPPER, "wrapper")]
    targets.extend((path, "dropin") for path in CANONICAL_MODEL_DROPINS)
    for path, kind in targets:
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:
            checks.append({
                "path": str(path),
                "kind": kind,
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:180]}",
            })
            errors.append(str(path) + ":read")
            continue

        if kind == "wrapper":
            canonical = _canonicalize_wrapper_model_text(raw)
            # The canonicalizer is the wrapper contract. Checking only a fixed
            # subset of old markers lets newly required safety guards exist in
            # code but never reach /usr/local. Exact canonical equality makes
            # every idempotent contract addition detectable and self-healing.
            ok = canonical == raw
        else:
            canonical = _canonicalize_dropin_model_text(raw)
            ok = expected_env in raw and (
                "BORIS_GEMINI_FALLBACK_MODEL=flash-lite" not in raw
                and "BORIS_GEMINI_FALLBACK_MODEL=gemini-2.5-flash-lite" not in raw
                and "BORIS_GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite" not in raw
            )

        repaired = False
        if not ok and repair_allowed and canonical != raw:
            try:
                MODEL_CONFIG_BACKUPS.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = MODEL_CONFIG_BACKUPS / (
                    path.name + "." + stamp + ".bak"
                )
                backup.write_text(raw, encoding="utf-8")
                os.chmod(backup, 0o400)
                path.write_text(canonical, encoding="utf-8")
                if kind == "wrapper":
                    os.chmod(path, 0o755)
                repaired = True
                changed_paths.append(str(path))
                raw = canonical
                if kind == "wrapper":
                    ok = bool(
                        wrapper_expected in raw
                        and "flash-lite|gemini-2.5-flash-lite|gemini-3.5-flash-lite" in raw
                        and 'FALLBACK_MODEL="$CANONICAL_FALLBACK_MODEL"' in raw
                    )
                else:
                    ok = expected_env in raw
            except Exception as exc:
                errors.append(
                    str(path) + ":repair:" + type(exc).__name__
                )
        checks.append({
            "path": str(path),
            "kind": kind,
            "ok": bool(ok),
            "repaired": repaired,
        })

    daemon_reload = None
    if changed_paths:
        try:
            proc = subprocess.run(
                ["systemctl", "daemon-reload"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            daemon_reload = {
                "rc": int(proc.returncode),
                "ok": proc.returncode == 0,
                "stderr": (proc.stderr or "")[-300:],
            }
            if proc.returncode != 0:
                errors.append("systemctl_daemon_reload")
        except Exception as exc:
            daemon_reload = {
                "rc": None,
                "ok": False,
                "stderr": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
            errors.append("systemctl_daemon_reload_exception")

    return {
        "ok": all(item.get("ok") for item in checks) and not errors,
        "repair_requested": bool(repair),
        "repair_allowed": repair_allowed,
        "changed_count": len(changed_paths),
        "changed_paths": changed_paths,
        "checks": checks,
        "daemon_reload": daemon_reload,
        "errors": errors,
        "canonical_model": CANONICAL_GEMINI_FALLBACK_MODEL,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _append(payload: dict) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        os.chmod(JOURNAL, 0o600)
    except OSError:
        pass


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _effective_retry_at(next_probe_at, retry_at):
    """Return the first actually safe retry time, never a stale earlier hint.

    Provider retry_at is a hard transport/cooldown fence. The watcher may also
    have a later anti-storm probe window. Recovery is safe only after BOTH
    constraints allow it, so the effective time is the later valid timestamp.
    """
    parsed = [dt for dt in (_parse_dt(next_probe_at), _parse_dt(retry_at)) if dt]
    if not parsed:
        return None
    return max(parsed).isoformat()


def _safe_probe_cmdline(cmdline: str) -> bool:
    raw = str(cmdline or "")
    if "/usr/bin/gemini" not in raw and "/usr/local/lib/boris-dev-cli/gemini" not in raw:
        return False
    if "EXECUTOR_ORDER" in raw or '"order_id"' in raw:
        return False
    return any(marker in raw for marker in SAFE_GEMINI_PROBE_MARKERS)


def _proc_age_seconds(pid: int) -> float:
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().split()
        start_ticks = int(fields[21])
        hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        return max(0.0, uptime - (start_ticks / float(hz)))
    except Exception:
        return 0.0


def _gemini_order_id_from_cmdline(cmdline: str) -> int | None:
    """Extract only a real A2A order id from an executor Gemini prompt."""
    raw = str(cmdline or "")
    if "/usr/bin/gemini" not in raw and "/usr/local/lib/boris-dev-cli/gemini" not in raw:
        return None
    import re
    match = re.search(r'["\\]order_id["\\]\s*:\s*(\d+)', raw)
    if not match:
        match = re.search(r'"order_id"\s*:\s*(\d+)', raw)
    try:
        return int(match.group(1)) if match else None
    except Exception:
        return None


def _stale_real_order_gemini_plan(
    processes: list[dict],
    running_runs: list[dict],
    now_epoch: float,
    timeout_seconds: int = 600,
    grace_seconds: int = 45,
) -> list[dict]:
    """Return only orphaned Gemini task processes whose owning run is gone.

    A real order process is never reaped merely for being old. We require
    orphan parent=1 plus either a newer currently-running DB run for the same
    order or no matching current run at all after timeout+grace.
    """
    active = {}
    for row in running_runs or []:
        try:
            oid = int(row.get("order_id"))
        except Exception:
            continue
        started = _parse_dt(row.get("started_at"))
        if started:
            active.setdefault(oid, []).append(started.timestamp())

    out = []
    for proc in processes or []:
        try:
            oid = int(proc.get("order_id"))
            pid = int(proc.get("pid"))
            ppid = int(proc.get("ppid"))
            age = float(proc.get("age_seconds") or 0)
        except Exception:
            continue
        if ppid != 1 or age <= 0:
            continue
        proc_started = float(now_epoch) - age
        starts = sorted(active.get(oid) or [])
        newer = [x for x in starts if x > proc_started + float(grace_seconds)]
        matching = [
            x for x in starts
            if abs(x - proc_started) <= float(grace_seconds)
        ]
        reason = None
        if newer:
            reason = "newer_running_attempt_exists"
        elif not matching and age >= float(timeout_seconds + grace_seconds):
            reason = "owning_run_not_running_after_timeout"
        if reason:
            out.append({
                "pid": pid,
                "order_id": oid,
                "age_seconds": int(age),
                "reason": reason,
                "proc_started_epoch": proc_started,
                "newest_running_started_epoch": max(starts) if starts else None,
            })
    return sorted(out, key=lambda x: (x["order_id"], x["pid"]))


def _reap_stale_real_order_gemini_processes(
    timeout_seconds=600,
    grace_seconds=45,
) -> dict:
    """Self-heal orphan Gemini children left behind after executor timeout."""
    processes = []
    now_epoch = time.time()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmd = (entry / "cmdline").read_bytes().replace(bytes([0]), b" ").decode(
                "utf-8", "ignore"
            )
            oid = _gemini_order_id_from_cmdline(cmd)
            if oid is None:
                continue
            fields = (entry / "stat").read_text().split()
            ppid = int(fields[3])
            age = _proc_age_seconds(pid)
            processes.append({
                "pid": pid,
                "ppid": ppid,
                "age_seconds": age,
                "order_id": oid,
            })
        except Exception:
            continue

    db = SessionLocal()
    try:
        running_runs = [
            dict(x) for x in db.execute(text("""
              SELECT order_id, started_at
              FROM ext_a2a_runs
              WHERE outcome='running' AND finished_at IS NULL
            """)).mappings().all()
        ]
    finally:
        db.close()

    candidates = _stale_real_order_gemini_plan(
        processes,
        running_runs,
        now_epoch,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
    )
    terminated = []
    for row in candidates:
        pid = int(row["pid"])
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append({**row, "signal": "TERM"})
        except ProcessLookupError:
            pass
        except Exception:
            continue
    if terminated:
        time.sleep(0.35)
    for row in terminated:
        pid = int(row["pid"])
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
                row["signal"] = "KILL"
            except ProcessLookupError:
                pass
            except Exception:
                pass
    return {
        "candidate_count": len(candidates),
        "terminated_count": len(terminated),
        "terminated": terminated,
    }


def _reap_stale_gemini_probe_processes(min_age_seconds=90) -> dict:
    """Kill only stale, exact health-probe Gemini processes; never task prompts."""
    candidates = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmd = (entry / "cmdline").read_bytes().replace(bytes([0]), b" ").decode(
                "utf-8", "ignore"
            )
        except Exception:
            continue
        age = _proc_age_seconds(pid)
        if age >= float(min_age_seconds) and _safe_probe_cmdline(cmd):
            candidates.append((pid, age))
    terminated = []
    for pid, age in sorted(candidates, reverse=True):
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append({"pid": pid, "age_seconds": int(age), "signal": "TERM"})
        except ProcessLookupError:
            pass
        except Exception:
            continue
    if terminated:
        time.sleep(0.35)
    for row in terminated:
        pid = int(row["pid"])
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
                row["signal"] = "KILL"
            except ProcessLookupError:
                pass
            except Exception:
                pass
    return {
        "candidate_count": len(candidates),
        "terminated_count": len(terminated),
        "terminated": terminated,
    }


def _transient_provider_block(reason: str) -> dict:
    """Classify only the exact autonomous-executor temporary-provider blocker."""
    raw = str(reason or "").strip()
    if not raw.startswith(TRANSIENT_PROVIDER_BLOCK_PREFIX):
        return {"recoverable": False, "reason": "prefix_mismatch", "attempts": []}
    payload = raw[len(TRANSIENT_PROVIDER_BLOCK_PREFIX):].strip()
    try:
        attempts = json.loads(payload)
    except Exception:
        return {"recoverable": False, "reason": "invalid_json", "attempts": []}
    if not isinstance(attempts, list) or not attempts:
        return {"recoverable": False, "reason": "empty_attempts", "attempts": []}
    kinds = []
    for item in attempts:
        if not isinstance(item, dict):
            return {"recoverable": False, "reason": "malformed_attempt", "attempts": attempts}
        kind = str(item.get("kind") or "").strip().upper()
        if kind not in TRANSIENT_PROVIDER_KINDS:
            return {
                "recoverable": False,
                "reason": "non_transient_kind:" + (kind or "missing"),
                "attempts": attempts,
            }
        kinds.append(kind)
    return {
        "recoverable": True,
        "reason": "transient_provider_only",
        "attempts": attempts,
        "kinds": kinds,
    }


def _transient_provider_block_with_checkpoint(reason: str, checkpoint_body=None) -> dict:
    """Recover safely when ext_a2a_orders.blocked_reason was length-truncated.

    Executor persists only a bounded blocked_reason. A long provider error can
    cut the JSON array mid-object, so the prefix alone is not enough to recover.
    In that case require the newest durable all_free_providers_unavailable
    CHECKPOINT and validate every recorded provider-attempt kind as transient.
    """
    direct = _transient_provider_block(reason)
    if direct.get("recoverable"):
        direct["evidence_source"] = "blocked_reason"
        return direct
    raw = str(reason or "").strip()
    if not raw.startswith(TRANSIENT_PROVIDER_BLOCK_PREFIX):
        return direct
    if isinstance(checkpoint_body, str):
        try:
            checkpoint = json.loads(checkpoint_body)
        except Exception:
            return direct
    elif isinstance(checkpoint_body, dict):
        checkpoint = dict(checkpoint_body)
    else:
        return direct
    if not isinstance(checkpoint, dict):
        return direct
    if str(checkpoint.get("reason") or "") != "all_free_providers_unavailable":
        return direct
    attempts = checkpoint.get("provider_attempts")
    if not isinstance(attempts, list) or not attempts:
        return direct
    kinds = []
    for item in attempts:
        if not isinstance(item, dict):
            return direct
        kind = str(item.get("kind") or "").strip().upper()
        if kind not in TRANSIENT_PROVIDER_KINDS:
            return {
                "recoverable": False,
                "reason": "checkpoint_non_transient_kind:" + (kind or "missing"),
                "attempts": attempts,
                "evidence_source": "checkpoint",
            }
        kinds.append(kind)
    return {
        "recoverable": True,
        "reason": "transient_provider_checkpoint_evidence",
        "attempts": attempts,
        "kinds": kinds,
        "evidence_source": "checkpoint",
    }


def _recoverable_provider_job_wait(wait_reason: str) -> bool:
    """Only scheduler wait classes compatible with a stale provider block."""
    raw = str(wait_reason or "").strip()
    return bool(
        raw.startswith("BLOCKED_INFRA: " + TRANSIENT_PROVIDER_BLOCK_PREFIX)
        or raw.startswith("WAITING_FREE_CODING_PROVIDER")
        or raw.startswith("PRODUCTION_CONVERGENCE_DRAIN:")
    )


def _provider_recovery_gate(facts: dict, lock_health: dict, production_gate: dict) -> dict:
    """Fail closed unless provider, scope safety and production convergence agree."""
    candidates = list((facts or {}).get("development_candidates") or [])
    capacity = int((facts or {}).get("executor_capacity") or 0)
    reasons = []
    if not candidates:
        reasons.append("no_free_provider")
    if (
        bool((facts or {}).get("under_pressure"))
        and not bool((facts or {}).get("historical_load_only"))
    ):
        reasons.append("server_under_pressure")
    if capacity <= 0:
        reasons.append("no_executor_capacity")
    if not bool((lock_health or {}).get("ok")):
        reasons.append("lock_health_not_ok")
    if (lock_health or {}).get("running_conflicts"):
        reasons.append("running_lock_conflict")
    if int((lock_health or {}).get("potential_overlap_pairs") or 0) != 0:
        reasons.append("potential_lock_overlap")
    if bool((production_gate or {}).get("block_new_writes")):
        reasons.append("production_convergence_drain")
    limit = max(0, min(4, capacity))
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "limit": limit,
        "provider_candidates": candidates,
    }


def _provider_resume_checkpoint_payload(hint_body, gate: dict, block_kinds: list) -> dict | None:
    """Promote a coordination RECOVERY_HINT only after the order becomes open.

    Provider failures append their own CHECKPOINT after the useful diagnostic
    checkpoint. Without replay, the next executor sees only the generic
    provider error and loses the exact remediation context. A RECOVERY_HINT is
    inert while BLOCKED_INFRA and is converted to the newest CHECKPOINT inside
    the same atomic requeue transaction.
    """
    if isinstance(hint_body, str):
        try:
            hint = json.loads(hint_body)
        except Exception:
            return None
    elif isinstance(hint_body, dict):
        hint = dict(hint_body)
    else:
        return None
    if not isinstance(hint, dict) or not hint:
        return None
    hint = dict(hint)
    hint["_replayed_after_provider_recovery"] = True
    hint["_replay_policy"] = "COORDINATION_RECOVERY_HINT_REPLAY_V1"
    hint["_provider_candidates"] = list((gate or {}).get("provider_candidates") or [])
    hint["_previous_block_kinds"] = list(block_kinds or [])
    return hint


def _queued_recovery_hint_payload(hint_id, hint_body) -> dict | None:
    """Convert a coordination recovery hint into an idempotent queued checkpoint."""
    payload = _provider_resume_checkpoint_payload(hint_body, {}, [])
    if not payload:
        return None
    payload["_replay_policy"] = "COORDINATION_QUEUED_RETRY_HINT_REPLAY_V1"
    payload["_source_recovery_hint_id"] = int(hint_id)
    payload.pop("_provider_candidates", None)
    payload.pop("_previous_block_kinds", None)
    return payload


def _replay_recovery_hints_for_queued_orders(limit=4) -> dict:
    """Replay latest coordination diagnostics before same-order queued retry."""
    result = {"examined": 0, "replayed": 0, "order_ids": []}
    db = SessionLocal()
    try:
        rows = db.execute(text("""
          SELECT o.id AS order_id, h.id AS hint_id, h.body_json
          FROM ext_a2a_orders o
          JOIN LATERAL (
            SELECT id, body_json
            FROM ext_a2a_messages
            WHERE order_id=o.id
              AND kind='RECOVERY_HINT'
              AND author LIKE 'chat-coordination%'
            ORDER BY id DESC
            LIMIT 1
          ) h ON TRUE
          WHERE o.status='queued'
          ORDER BY o.priority DESC,o.id
          LIMIT :lim
        """), {"lim": max(1, int(limit) * 4)}).mappings().all()
        for raw in rows:
            row = dict(raw)
            result["examined"] += 1
            oid = int(row["order_id"])
            hint_id = int(row["hint_id"])
            marker = f'"_source_recovery_hint_id": {hint_id}'
            exists = db.execute(text("""
              SELECT 1
              FROM ext_a2a_messages
              WHERE order_id=:oid
                AND kind='CHECKPOINT'
                AND author='chat-coordination-recovery'
                AND body_json LIKE :marker
              LIMIT 1
            """), {"oid": oid, "marker": "%" + marker + "%"}).first()
            if exists:
                continue
            payload = _queued_recovery_hint_payload(hint_id, row.get("body_json"))
            if not payload:
                continue
            body = json.dumps(payload, ensure_ascii=False, default=str)
            ins = db.execute(text("""
              INSERT INTO ext_a2a_messages(order_id,kind,author,body_json)
              SELECT :oid,'CHECKPOINT','chat-coordination-recovery',:body
              WHERE EXISTS (
                SELECT 1 FROM ext_a2a_orders
                WHERE id=:oid AND status='queued'
              )
              RETURNING id
            """), {"oid": oid, "body": body}).first()
            if not ins:
                db.rollback()
                continue
            db.commit()
            result["replayed"] += 1
            result["order_ids"].append(oid)
            if result["replayed"] >= max(1, int(limit)):
                break
    finally:
        db.close()
    return result


def _canonical_parent_review_evidence(parent: dict, current: dict | None = None) -> dict | None:
    """Build review evidence only from a fully proven canonical parent job.

    This is recovery, not inference: every acceptance criterion and phase text
    comes from dev.get(). If QA/deployment/gates/criteria are not already
    closed, fail closed and return None.
    """
    from app.ext_api import dev as _dev

    parent = dict(parent or {})
    criteria = list(parent.get("acceptance_criteria") or [])
    if not criteria or not all(bool(c.get("done")) for c in criteria):
        return None
    if str(parent.get("qa_status") or "").upper() != "PASS":
        return None
    if str(parent.get("deployment_status") or "").lower() != "deployed":
        return None
    try:
        if _dev.gate_report(parent):
            return None
    except Exception:
        return None

    current = dict(current or {})
    current_pass = sum(
        1 for c in (current.get("acceptance_criteria") or [])
        if str(c.get("status") or "").upper() == "PASS"
    )
    current_checked = all(
        str(current.get(k) or "").strip().upper() not in {"", "NOT_CHECKED"}
        for k in ("build", "tests", "runtime", "e2e")
    )
    if current_pass >= len(criteria) and current_checked:
        return None

    phases = parent.get("phases") or {}
    def phase_ev(*names):
        parts = []
        for name in names:
            row = phases.get(name) or {}
            if str(row.get("state") or "").lower() == "pass" and row.get("evidence"):
                parts.append(f"{name}: {row['evidence']}")
        return " | ".join(parts)

    normalized = [
        {
            "criterion": str(c.get("text") or ""),
            "status": "PASS",
            "evidence": str(c.get("evidence") or ""),
        }
        for c in criteria
    ]
    job_ref = str(parent.get("dev_job_id") or parent.get("id") or "parent")
    return {
        "summary": (
            "Canonical parent evidence restored after reviewer-provider replay; "
            "no new implementation performed."
        ),
        "files_changed": [],
        "build": phase_ev("TESTING") or "Parent TESTING phase is PASS",
        "tests": phase_ev("TESTING", "REGRESSION") or "Parent tests/regression are PASS",
        "runtime": phase_ev("DEPLOYMENT", "PRODUCTION_QA") or "Parent production QA is PASS",
        "e2e": phase_ev("INTEGRATION", "ACCEPTANCE") or "Parent integration/acceptance are PASS",
        "acceptance_criteria": normalized,
        "evidence": (
            f"{job_ref}: qa_status=PASS; deployment_status=deployed; "
            f"gates=[]; acceptance={len(criteria)}/{len(criteria)} done."
        ),
        "blockers": [],
        "safety": phase_ev("ACCEPTANCE") or "Recovered only from canonical parent evidence.",
        "execution": {
            "worker": "chat-coordination-parent-evidence-recovery",
            "provider": "none",
            "model": "none",
            "steps": 0,
            "tool_calls": 0,
            "reason": "review_replay_evidence_recovery",
        },
        "iteration": int(current.get("iteration") or 0),
    }


def _repair_quarantined_review_evidence_from_parent(limit=20) -> dict:
    """Repair weak replay evidence from a fully proven parent, race-safely."""
    from app.ext_api import dev as _dev

    result = {"examined": 0, "repaired_order_ids": [], "skipped": 0}
    db = SessionLocal()
    try:
        rows = db.execute(text("""
          SELECT id AS order_id, dev_job_id, last_evidence_json
          FROM ext_a2a_orders
          WHERE status='blocked_infra'
            AND COALESCE(last_review_json,'') ILIKE :marker
          ORDER BY priority DESC NULLS LAST,id
          LIMIT :lim
        """), {
            "marker": "%" + REVIEW_QUOTA_LOOP_MARKER + "%",
            "lim": max(1, int(limit)),
        }).mappings().all()
        for raw in rows:
            row = dict(raw)
            result["examined"] += 1
            old_raw = str(row.get("last_evidence_json") or "")
            try:
                current = json.loads(old_raw) if old_raw else {}
            except Exception:
                current = {}
            try:
                parent = _dev.get(int(row["dev_job_id"]))
            except Exception:
                result["skipped"] += 1
                continue
            repaired = _canonical_parent_review_evidence(parent, current)
            if not repaired:
                result["skipped"] += 1
                continue
            new_raw = json.dumps(repaired, ensure_ascii=False, default=str)
            upd = db.execute(text("""
              UPDATE ext_a2a_orders
                 SET last_evidence_json=:new_evidence, updated_at=NOW()
               WHERE id=:oid
                 AND status='blocked_infra'
                 AND COALESCE(last_review_json,'') ILIKE :marker
                 AND COALESCE(last_evidence_json,'')=:old_evidence
            """), {
                "oid": int(row["order_id"]),
                "marker": "%" + REVIEW_QUOTA_LOOP_MARKER + "%",
                "old_evidence": old_raw,
                "new_evidence": new_raw,
            })
            if upd.rowcount != 1:
                db.rollback()
                result["skipped"] += 1
                continue
            body = json.dumps({
                "policy": "PARENT_CANONICAL_REVIEW_EVIDENCE_RECOVERY_V1",
                "source_dev_job_id": parent.get("dev_job_id"),
                "qa_status": parent.get("qa_status"),
                "deployment_status": parent.get("deployment_status"),
                "acceptance_done": len(parent.get("acceptance_criteria") or []),
                "new_ai_calls": 0,
                "owner_action_required": False,
            }, ensure_ascii=False)
            db.execute(text("""
              INSERT INTO ext_a2a_messages(order_id,kind,author,body_json)
              VALUES (:oid,'PARENT_EVIDENCE_RESTORED','chat-coordination',:body)
            """), {"oid": int(row["order_id"]), "body": body})
            db.commit()
            result["repaired_order_ids"].append(int(row["order_id"]))
    finally:
        db.close()
    if result["repaired_order_ids"]:
        _append({
            "at": _now_iso(),
            "status": "parent_review_evidence_restored",
            "recovery": result,
        })
    return result


def _quarantine_queued_review_provider_orders() -> dict:
    """Keep previously reviewed work out of executor queue while provider is down.

    Legacy dispatcher recovery may already have converted a reviewer quota
    blocker to QUEUED before this guard runs. When no free provider is usable,
    that queue state has no legitimate work to do and only risks replay once a
    provider clock opens. Re-quarantine it using the durable last_review marker;
    last_evidence_json is never touched.
    """
    candidates = list(aiprov.development_candidates())
    result = {"quarantined_order_ids": [], "provider_candidates": candidates}
    if candidates:
        return result
    db = SessionLocal()
    try:
        rows = db.execute(text("""
          SELECT o.id AS order_id,o.dev_job_id
          FROM ext_a2a_orders o
          JOIN ext_dev_jobs j ON j.id=o.dev_job_id
          WHERE o.status IN ('queued','review_requested')
            AND COALESCE(o.last_review_json,'') ILIKE :marker
            AND j.status IN ('waiting','queued','review')
            AND COALESCE(j.blocked_reason,'')=''
          ORDER BY o.priority DESC NULLS LAST,o.id
          LIMIT 200
        """), {"marker": "%" + REVIEW_QUOTA_LOOP_MARKER + "%"}).mappings().all()
        for raw in rows:
            oid = int(raw["order_id"])
            jid = int(raw["dev_job_id"])
            order_upd = db.execute(text("""
              UPDATE ext_a2a_orders
                 SET status='blocked_infra', blocked_reason=:reason,
                     claimed_by=NULL, claimed_at=NULL, updated_at=NOW()
               WHERE id=:oid AND status IN ('queued','review_requested')
                 AND COALESCE(last_review_json,'') ILIKE :marker
            """), {
                "oid": oid,
                "reason": REVIEW_QUOTA_LOOP_MARKER,
                "marker": "%" + REVIEW_QUOTA_LOOP_MARKER + "%",
            })
            if order_upd.rowcount != 1:
                db.rollback()
                continue
            db.execute(text("""
              UPDATE ext_dev_jobs
                 SET status='waiting', wait_reason=:wait_reason,
                     assigned_agent=NULL, updated_at=NOW()
               WHERE id=:jid
                 AND status IN ('waiting','queued','review')
                 AND COALESCE(blocked_reason,'')=''
            """), {
                "jid": jid,
                "wait_reason": "BLOCKED_INFRA: " + REVIEW_QUOTA_LOOP_MARKER,
            })
            body = json.dumps({
                "why": "reviewer provider unavailable; preserve completed review work outside executor queue",
                "policy": "REVIEW_REPLAY_QUARANTINE_V1",
                "owner_action_required": False,
                "evidence_preserved": True,
            }, ensure_ascii=False)
            db.execute(text("""
              INSERT INTO ext_a2a_messages(order_id,kind,author,body_json)
              VALUES (:oid,'REVIEW_REPLAY_QUARANTINED','chat-coordination',:body)
            """), {"oid": oid, "body": body})
            db.commit()
            result["quarantined_order_ids"].append(oid)
    finally:
        db.close()
    if result["quarantined_order_ids"]:
        _append({
            "at": _now_iso(),
            "status": "review_replay_quarantined",
            "quarantine": result,
        })
    return result


def _review_provider_recovery_gate(facts: dict) -> dict:
    candidates = list(facts.get("development_candidates") or [])
    review_capacity = max(0, int(facts.get("review_capacity") or 0))
    reasons = []
    if not candidates:
        reasons.append("no_free_provider")
    if (
        facts.get("under_pressure")
        and not facts.get("historical_load_only")
    ):
        reasons.append("server_under_pressure")
    if review_capacity <= 0:
        reasons.append("no_review_capacity")
    return {
        "allowed": not reasons,
        "limit": min(review_capacity, 4),
        "provider_candidates": candidates,
        "reasons": reasons,
    }


def _recover_review_provider_blocked_orders(facts: dict | None = None) -> dict:
    """Return provider-blocked completed work directly to REVIEW, never executor.

    `a2a.recover_internal_blockers()` currently sends every healed provider
    blocker to QUEUED. For an order that was already at REVIEW this destroys
    the lifecycle: an executor re-runs completed work and can overwrite strong
    evidence before the reviewer retries. Until the autonomous-dev single
    writer replaces that lifecycle, provider-watch owns a narrow fail-closed
    bridge: BLOCKED_INFRA(review quota) -> review_requested, preserving
    last_evidence_json verbatim.
    """
    current = facts or _facts()
    gate = _review_provider_recovery_gate(current)
    result = {
        "gate": gate,
        "recovered_order_ids": [],
        "recovered_job_ids": [],
        "eligible_count": 0,
        "race_skipped": 0,
    }
    if not gate["allowed"] or gate["limit"] <= 0:
        return result

    db = SessionLocal()
    try:
        rows = db.execute(text("""
          SELECT o.id AS order_id,o.dev_job_id,o.priority
          FROM ext_a2a_orders o
          JOIN ext_dev_jobs j ON j.id=o.dev_job_id
          WHERE o.status='blocked_infra'
            AND COALESCE(o.blocked_reason,'') LIKE :reason
            AND j.status IN ('waiting','review')
            AND COALESCE(j.blocked_reason,'')=''
          ORDER BY o.priority DESC NULLS LAST,o.id
          LIMIT 100
        """), {"reason": REVIEW_QUOTA_LOOP_MARKER + "%"}).mappings().all()
        result["eligible_count"] = len(rows)
        for raw in rows[: gate["limit"]]:
            row = dict(raw)
            oid = int(row["order_id"])
            jid = int(row["dev_job_id"])
            order_upd = db.execute(text("""
              UPDATE ext_a2a_orders
                 SET status='review_requested', blocked_reason=NULL,
                     claimed_by=NULL, claimed_at=NULL, updated_at=NOW()
               WHERE id=:oid
                 AND status='blocked_infra'
                 AND COALESCE(blocked_reason,'') LIKE :reason
            """), {"oid": oid, "reason": REVIEW_QUOTA_LOOP_MARKER + "%"})
            job_upd = db.execute(text("""
              UPDATE ext_dev_jobs
                 SET status='review', wait_reason=NULL, blocked_reason=NULL,
                     assigned_agent=NULL, updated_at=NOW()
               WHERE id=:jid
                 AND status IN ('waiting','review')
                 AND COALESCE(blocked_reason,'')=''
            """), {"jid": jid})
            if order_upd.rowcount != 1 or job_upd.rowcount != 1:
                db.rollback()
                result["race_skipped"] += 1
                continue
            body = json.dumps({
                "why": "free review provider recovered; preserved evidence returned directly to reviewer",
                "policy": "REVIEW_PROVIDER_DIRECT_RECOVERY_V1",
                "provider_candidates": gate["provider_candidates"],
                "owner_action_required": False,
                "executor_replay": False,
            }, ensure_ascii=False)
            db.execute(text("""
              INSERT INTO ext_a2a_messages(order_id,kind,author,body_json)
              VALUES (:oid,'AUTO_REVIEW_RECOVERY','chat-coordination',:body)
            """), {"oid": oid, "body": body})
            db.commit()
            result["recovered_order_ids"].append(oid)
            result["recovered_job_ids"].append(jid)
    finally:
        db.close()
    if result["recovered_order_ids"]:
        _append({
            "at": _now_iso(),
            "status": "review_provider_orders_recovered",
            "recovery": result,
        })
    return result


def _recover_transient_provider_blocked_orders(
    facts: dict | None = None,
    target_order_id: int | None = None,
) -> dict:
    """SELF-HEAL transient A2A provider blocks after a free provider recovers.

    This intentionally does not touch scope/human/permanent blockers. It also
    refuses to recover while production convergence is draining or when lock
    health is not clean. At most the measured executor capacity (capped at 4)
    is returned to QUEUED per guard cycle, preventing a thundering herd.
    """
    from app.ext_api import dev

    current = facts or _facts()
    lock_health = dev.development_lock_health(self_heal=True, limit=3000)
    production_gate = dev._production_convergence_gate()
    gate = _provider_recovery_gate(current, lock_health, production_gate)
    result = {
        "gate": gate,
        "production_gate": production_gate,
        "recovered_order_ids": [],
        "recovered_job_ids": [],
        "eligible_count": 0,
        "examined_count": 0,
        "target_order_id": (
            int(target_order_id) if target_order_id is not None else None
        ),
    }
    if not gate["allowed"] or gate["limit"] <= 0:
        return result

    db = SessionLocal()
    try:
        rows = db.execute(text("""
          SELECT
            o.id AS order_id,
            o.dev_job_id,
            o.blocked_reason AS order_blocked_reason,
            o.priority AS order_priority,
            j.status AS job_status,
            j.wait_reason,
            j.blocked_reason AS job_blocked_reason
          FROM ext_a2a_orders o
          JOIN ext_dev_jobs j ON j.id=o.dev_job_id
          WHERE o.status='blocked_infra'
            AND COALESCE(o.blocked_reason,'') LIKE :prefix
            AND (:target_order_id IS NULL OR o.id=:target_order_id)
            AND j.status IN ('waiting','queued')
            AND COALESCE(j.blocked_reason,'')=''
            AND NOT EXISTS (
              SELECT 1 FROM ext_a2a_orders s
              WHERE s.dev_job_id=o.dev_job_id
                AND s.id<>o.id
                AND s.status IN (
                  'queued','running','review_requested','returned',
                  'blocked_human','blocked_infra'
                )
            )
          ORDER BY o.priority DESC,o.id
          LIMIT 200
        """), {
            "prefix": TRANSIENT_PROVIDER_BLOCK_PREFIX + "%",
            "target_order_id": (
                int(target_order_id) if target_order_id is not None else None
            ),
        }).mappings().all()
        eligible = []
        for raw in rows:
            row = dict(raw)
            result["examined_count"] += 1
            checkpoint_row = db.execute(text("""
              SELECT body_json
              FROM ext_a2a_messages
              WHERE order_id=:oid AND kind='CHECKPOINT'
              ORDER BY id DESC
              LIMIT 1
            """), {"oid": int(row["order_id"])}).mappings().first()
            classified = _transient_provider_block_with_checkpoint(
                row.get("order_blocked_reason"),
                (checkpoint_row or {}).get("body_json"),
            )
            if not classified.get("recoverable"):
                continue
            if not _recoverable_provider_job_wait(row.get("wait_reason")):
                continue
            row["provider_block"] = classified
            eligible.append(row)
        result["eligible_count"] = len(eligible)

        for row in eligible[: gate["limit"]]:
            oid = int(row["order_id"])
            jid = int(row["dev_job_id"])
            hint_row = db.execute(text("""
              SELECT body_json
              FROM ext_a2a_messages
              WHERE order_id=:oid
                AND kind='RECOVERY_HINT'
                AND author LIKE 'chat-coordination%'
              ORDER BY id DESC
              LIMIT 1
            """), {"oid": oid}).mappings().first()
            replay_payload = _provider_resume_checkpoint_payload(
                (hint_row or {}).get("body_json"),
                gate,
                row["provider_block"].get("kinds") or [],
            )
            body = json.dumps({
                "why": "free development provider recovered; transient provider-only blocker self-healed",
                "policy": "COORDINATION_TRANSIENT_PROVIDER_RECOVERY_V1",
                "provider_candidates": gate["provider_candidates"],
                "previous_block_kinds": row["provider_block"].get("kinds") or [],
                "owner_action_required": False,
            }, ensure_ascii=False)
            # Atomic control-plane transition: executor cannot observe the
            # QUEUED order until the matching dev-job status is committed too.
            job_upd = db.execute(text("""
              UPDATE ext_dev_jobs
                 SET status='queued', wait_reason=NULL, blocked_reason=NULL,
                     assigned_agent=NULL, updated_at=NOW()
               WHERE id=:jid
                 AND status IN ('waiting','queued')
                 AND (
                   COALESCE(wait_reason,'') LIKE :provider_wait
                   OR COALESCE(wait_reason,'') LIKE 'WAITING_FREE_CODING_PROVIDER%'
                   OR COALESCE(wait_reason,'') LIKE 'PRODUCTION_CONVERGENCE_DRAIN:%'
                 )
                 AND COALESCE(blocked_reason,'')=''
            """), {
                "jid": jid,
                "provider_wait": "BLOCKED_INFRA: " + TRANSIENT_PROVIDER_BLOCK_PREFIX + "%",
            })
            order_upd = db.execute(text("""
              UPDATE ext_a2a_orders
                 SET status='queued', blocked_reason=NULL, claimed_by=NULL,
                     claimed_at=NULL, updated_at=NOW()
               WHERE id=:oid AND status='blocked_infra'
            """), {"oid": oid})
            if job_upd.rowcount != 1 or order_upd.rowcount != 1:
                db.rollback()
                result["race_skipped"] = result.get("race_skipped", 0) + 1
                continue
            db.execute(text("""
              INSERT INTO ext_a2a_messages(order_id,kind,author,body_json)
              VALUES (:oid,'AUTO_RECOVERY','chat-coordination',:body)
            """), {"oid": oid, "body": body})
            if replay_payload:
                db.execute(text("""
                  INSERT INTO ext_a2a_messages(order_id,kind,author,body_json)
                  VALUES (:oid,'CHECKPOINT','chat-coordination-recovery',:body)
                """), {
                    "oid": oid,
                    "body": json.dumps(replay_payload, ensure_ascii=False, default=str),
                })
                result["resume_hints_replayed"] = (
                    int(result.get("resume_hints_replayed") or 0) + 1
                )
            db.commit()
            result["recovered_order_ids"].append(oid)
            result["recovered_job_ids"].append(jid)
    finally:
        db.close()

    if result["recovered_order_ids"]:
        _append({
            "at": _now_iso(),
            "status": "transient_provider_orders_recovered",
            "recovery": result,
        })
    return result


def _gemini_probe_error_text(provider_note: str) -> str:
    """Recover the exact latest health-probe error instead of a truncated note."""
    raw = str(provider_note or "")
    parts = [raw]
    match = re.search(r"(/tmp/gemini-client-error-[^\s]+\.json)", raw)
    if not match:
        return raw
    path = Path(match.group(1))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return raw
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        message = str(err.get("message") or "")
        if message:
            parts.append(message)
    return "\n".join(x for x in parts if x)


def _daily_free_tier_quota_retry(
    error_text: str,
    finished_at=None,
    reset_epoch=None,
    now=None,
) -> dict | None:
    """Turn a proven per-model daily quota into one post-reset retry window."""
    raw = str(error_text or "")
    low = raw.lower()
    proven = (
        "daily quota" in low
        and ("exhaust" in low or "quota on this model" in low)
    )
    if not proven:
        return None
    now = now or datetime.now(timezone.utc)
    finished = _parse_dt(finished_at) or now
    reset_at = None
    durable_reset_epoch = reset_epoch
    if durable_reset_epoch is None:
        match = re.search(r"reset_epoch=([0-9]{9,13})", raw)
        if match:
            durable_reset_epoch = match.group(1)
    try:
        if durable_reset_epoch is not None:
            reset_at = datetime.fromtimestamp(float(durable_reset_epoch), timezone.utc)
    except Exception:
        reset_at = None
    if reset_at is None or reset_at <= finished:
        # Fail closed rather than hammering a daily quota when the wrapper's
        # reset marker is unavailable. A fresh health probe will still recover
        # immediately after this conservative one-day window.
        reset_at = finished + timedelta(days=1)
        source = "daily_quota_conservative_24h"
    else:
        source = "wrapper_daily_reset_window"
    due_at = reset_at + timedelta(seconds=GEMINI_DAILY_QUOTA_SAFETY_SECONDS)
    return {
        "kind": "daily_model_quota",
        "reset_at": reset_at.isoformat(),
        "next_probe_at": due_at.isoformat(),
        "finished_at": finished.isoformat(),
        "evidence": "explicit_daily_model_quota",
        "source": source,
    }


def _sync_gemini_double_daily_latch(daily_evidence: dict | None, apply=False, now=None) -> dict:
    """Persist/recover the transport latch used to suppress duplicate probes."""
    now = now or datetime.now(timezone.utc)
    evidence = dict(daily_evidence or {})
    reset_at = _parse_dt(evidence.get("reset_at"))
    result = {
        "path": str(GEMINI_DOUBLE_DAILY_LATCH),
        "active": False,
        "applied": False,
        "reset_at": reset_at.isoformat() if reset_at else None,
        "source": evidence.get("source"),
    }
    if evidence.get("kind") != "daily_model_quota" or not reset_at or reset_at <= now:
        if apply and GEMINI_DOUBLE_DAILY_LATCH.exists():
            try:
                GEMINI_DOUBLE_DAILY_LATCH.unlink()
                result["applied"] = True
                result["removed_stale"] = True
            except OSError as exc:
                result["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        return result

    target = int(reset_at.timestamp())
    current = None
    try:
        current = int(GEMINI_DOUBLE_DAILY_LATCH.read_text(encoding="utf-8").strip())
    except Exception:
        current = None
    result["active"] = True
    result["target_epoch"] = target
    result["current_epoch"] = current
    if current == target:
        return result
    if not apply:
        result["needs_repair"] = True
        return result
    try:
        GEMINI_DOUBLE_DAILY_LATCH.parent.mkdir(parents=True, exist_ok=True)
        GEMINI_DOUBLE_DAILY_LATCH.write_text(str(target) + "\n", encoding="utf-8")
        os.chmod(GEMINI_DOUBLE_DAILY_LATCH, 0o600)
        result["applied"] = True
        result["current_epoch"] = target
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
    return result


def _short_free_tier_quota_retry(error_text: str, finished_at=None) -> dict | None:
    """Parse proven short Gemini free-tier quota windows.

    Both free-tier input-token and request-count quotas are temporary rate
    windows when Gemini returns an explicit retry-after. Generic billing,
    credit or authentication failures must never be converted into retries.
    """
    raw = str(error_text or "")
    low = raw.lower()
    quota_metrics = (
        "generate_content_free_tier_input_token_count",
        "generate_content_free_tier_requests",
    )
    metric = next((marker for marker in quota_metrics if marker in low), None)
    if not metric or "you exceeded your current quota" not in low:
        return None
    values = []
    for pattern in (
        r"please retry in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"suggested retry after\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retrying after\s+([0-9]+(?:\.[0-9]+)?)ms",
    ):
        for match in re.finditer(pattern, low, re.I):
            try:
                value = float(match.group(1))
            except Exception:
                continue
            if pattern.endswith("ms"):
                value /= 1000.0
            if 0.0 < value <= 600.0:
                values.append(value)
    if not values:
        return None
    finished = _parse_dt(finished_at)
    return {
        "kind": (
            "free_tier_request_quota"
            if metric == "generate_content_free_tier_requests"
            else "free_tier_input_token_quota"
        ),
        "metric": metric,
        "retry_seconds": max(values),
        "finished_at": finished.isoformat() if finished else None,
        "evidence": "explicit_free_tier_quota_with_retry_after",
    }


def _gemini_reprobe_plan(provider_map: dict, quota_evidence=None, now=None) -> dict:
    """Real free-provider recovery cadence used by recover.once()."""
    now = now or datetime.now(timezone.utc)
    row = (provider_map or {}).get("gemini_cli") or {}
    state = str(row.get("state") or "")
    note = str(row.get("note") or "")
    if state == "AVAILABLE":
        return {
            "provider": "gemini_cli",
            "due": False,
            "next_probe_at": None,
            "source": "already_available",
        }
    if state == "UNSUPPORTED_LOCATION":
        return {
            "provider": "gemini_cli",
            "due": False,
            "next_probe_at": None,
            "source": "external_location_change_required",
        }
    if state == "AUTH_ERROR" and WRONG_PROBE_HOME_MARKER in note:
        # This is not proof that the production Gemini account is unauthorised.
        # It proves the health check ran with SentinelX's service home instead
        # of BORIS's canonical CLI home. Retry immediately in the canonical
        # environment so a tooling-context mistake cannot require owner action.
        return {
            "provider": "gemini_cli",
            "due": True,
            "next_probe_at": now.isoformat(),
            "source": "invalid_probe_home_recoverable",
        }
    if state in {"AUTH_ERROR", "DISABLED_BY_OWNER"}:
        return {
            "provider": "gemini_cli",
            "due": False,
            "next_probe_at": None,
            "source": "external_access_change_required",
        }
    hard_retry_at = _parse_dt(row.get("retry_at"))
    if (
        state in {"RATE_LIMITED", "UNAVAILABLE_BILLING", "TEMP_ERROR"}
        and hard_retry_at
        and hard_retry_at > now
    ):
        return {
            "provider": "gemini_cli",
            "due": False,
            "next_probe_at": hard_retry_at.isoformat(),
            "source": "provider_retry_fence",
        }
    updated = _parse_dt(row.get("updated_at"))
    quota = quota_evidence if isinstance(quota_evidence, dict) else None
    if state in {"UNAVAILABLE_BILLING", "RATE_LIMITED"} and quota:
        if quota.get("kind") == "daily_model_quota":
            due_at = _parse_dt(quota.get("next_probe_at"))
            if due_at:
                return {
                    "provider": "gemini_cli",
                    "due": now >= due_at,
                    "next_probe_at": due_at.isoformat(),
                    "source": "daily_model_quota_reset",
                    "reset_at": quota.get("reset_at"),
                }
        finished = _parse_dt(quota.get("finished_at"))
        retry_seconds = float(quota.get("retry_seconds") or 0)
        if finished and 0 < retry_seconds <= 600:
            # aiprov.reprobe_free_development intentionally has a 5-minute
            # anti-storm floor. Respect that floor while cutting the generic
            # 30-minute recovery delay for an explicitly short free-tier quota.
            floor_base = updated or finished
            due_at = max(
                finished + timedelta(seconds=retry_seconds + 15),
                floor_base + timedelta(minutes=5),
            )
            return {
                "provider": "gemini_cli",
                "due": now >= due_at,
                "next_probe_at": due_at.isoformat(),
                "source": "short_free_tier_quota_retry",
                "retry_seconds": retry_seconds,
            }
    if not updated:
        return {
            "provider": "gemini_cli",
            "due": True,
            "next_probe_at": now.isoformat(),
            "source": "missing_updated_at_nearest_dispatcher_cycle",
        }
    due_at = updated + timedelta(minutes=FREE_REPROBE_MINUTES)
    return {
        "provider": "gemini_cli",
        "due": now >= due_at,
        "next_probe_at": due_at.isoformat(),
        "source": "recover.reprobe_free_development",
    }


def _review_quota_loop_plan(block_count, provider_state, provider_usable):
    """Fail closed when real reviewer quota failures race a tiny health probe.

    A provider can look AVAILABLE after a lightweight probe while the next real
    review immediately hits quota. Two recent lead quota blocks are enough to
    prove that this is a loop, not a one-off transient. Cooldown prevents
    BLOCKED_INFRA -> executor -> REVIEW_REQUEST churn and repeated free calls.
    """
    count = max(0, int(block_count or 0))
    state = str(provider_state or "")
    usable = bool(provider_usable)
    return {
        "block_count": count,
        "provider_state": state,
        "provider_usable": usable,
        "contain": bool(count >= REVIEW_QUOTA_LOOP_THRESHOLD and usable),
        "cooldown_seconds": REVIEW_QUOTA_COOLDOWN_SECONDS,
    }


def _probe_cooldown_alignment_plan(facts, now=None) -> dict:
    """Keep provider retry_at closed until the watcher's real probe window.

    `_gemini_reprobe_plan` can intentionally add an anti-storm floor that is
    later than ext_ai_providers.retry_at. Without alignment, aiprov marks the
    provider usable early and daemon recovery starts lead/executors before the
    permitted probe, defeating the floor. Only retry_at is extended; updated_at
    must stay untouched or the five-minute floor would move on every cycle.
    """
    now = now or datetime.now(timezone.utc)
    providers = facts.get("providers") or {}
    provider = providers.get("gemini_cli") or {}
    probe = facts.get("gemini_reprobe") or {}
    state = str(provider.get("state") or "")
    retry_at = _parse_dt(provider.get("retry_at"))
    next_probe = _parse_dt(probe.get("next_probe_at"))
    candidate = "gemini_cli" in (facts.get("development_candidates") or [])
    retryable = state in {"RATE_LIMITED", "UNAVAILABLE_BILLING", "TEMP_ERROR"}
    durable_daily_quota = bool(
        aiprov.gemini_daily_quota_reset_epoch(provider.get("note"), now=now)
    )
    deferred_for_pressure = bool(
        facts.get("under_pressure")
        and not facts.get("historical_load_only")
        and probe.get("due")
    )
    # A proven daily-quota reset is already fail-closed inside aiprov.state():
    # Gemini stays unusable until a successful post-reset probe. Extending
    # retry_at by another success-latch here would move the reset forward every
    # guard cycle. Generic transient cooldowns still keep the anti-storm latch.
    target_retry = (
        next_probe
        if durable_daily_quota
        else (
            next_probe + timedelta(seconds=PROVIDER_PROBE_SUCCESS_LATCH_SECONDS)
            if next_probe else None
        )
    )
    if deferred_for_pressure:
        pressure_hold = now + timedelta(seconds=60)
        target_retry = max(
            [x for x in (target_retry, pressure_hold) if x is not None]
        )
    align = bool(
        retryable
        and target_retry
        and target_retry > now
        and (retry_at is None or retry_at < target_retry)
    )
    return {
        "align": align,
        "provider_state": state,
        "candidate_too_early": candidate,
        "deferred_for_pressure": deferred_for_pressure,
        "previous_retry_at": retry_at.isoformat() if retry_at else None,
        "next_probe_at": next_probe.isoformat() if next_probe else None,
        "target_retry_at": target_retry.isoformat() if target_retry else None,
    }


def _daily_quota_retry_reconcile_plan(provider, now=None) -> dict:
    """Canonicalize Gemini daily-quota retry_at to reset_epoch + safety margin."""
    now = now or datetime.now(timezone.utc)
    provider = provider or {}
    reset_epoch = aiprov.gemini_daily_quota_reset_epoch(
        provider.get("note"), now=now
    )
    current = _parse_dt(provider.get("retry_at"))
    if not reset_epoch:
        return {
            "drift": False,
            "reset_epoch": None,
            "previous_retry_at": current.isoformat() if current else None,
            "target_retry_at": None,
        }
    target = datetime.fromtimestamp(float(reset_epoch), timezone.utc) + timedelta(
        seconds=GEMINI_DAILY_QUOTA_SAFETY_SECONDS
    )
    drift = bool(
        current is None or abs((current - target).total_seconds()) > 2.0
    )
    return {
        "drift": drift,
        "reset_epoch": int(reset_epoch),
        "previous_retry_at": current.isoformat() if current else None,
        "target_retry_at": target.isoformat(),
    }


def _reconcile_daily_quota_retry_at(facts, apply=False) -> dict:
    provider = ((facts or {}).get("providers") or {}).get("gemini_cli") or {}
    plan = _daily_quota_retry_reconcile_plan(provider)
    plan["applied"] = False
    if not (plan.get("drift") and apply):
        return plan
    target = _parse_dt(plan.get("target_retry_at"))
    note = str(provider.get("note") or "")
    state = str(provider.get("state") or "")
    if not target or state != "RATE_LIMITED":
        return plan
    db = SessionLocal()
    try:
        result = db.execute(text("""
          UPDATE ext_ai_providers
          SET retry_at=:target_retry
          WHERE name='gemini_cli'
            AND state='RATE_LIMITED'
            AND note=:note
        """), {"target_retry": target, "note": note})
        db.commit()
        plan["applied"] = bool(result.rowcount)
    finally:
        db.close()
    return plan


def _align_provider_retry_at_to_probe_plan(facts, apply=False) -> dict:
    plan = _probe_cooldown_alignment_plan(facts)
    plan["applied"] = False
    if not (plan["align"] and apply):
        return plan
    target_retry = _parse_dt(plan.get("target_retry_at"))
    if not target_retry:
        return plan
    db = SessionLocal()
    try:
        result = db.execute(text("""
          UPDATE ext_ai_providers
          SET retry_at=:target_retry
          WHERE name='gemini_cli'
            AND (retry_at IS NULL OR retry_at < :target_retry)
        """), {"target_retry": target_retry})
        db.commit()
        plan["applied"] = bool(result.rowcount)
    finally:
        db.close()
    return plan


def _contain_reviewer_quota_loop(apply=False) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=REVIEW_QUOTA_LOOP_WINDOW_SECONDS
    )
    db = SessionLocal()
    try:
        rows = db.execute(text("""
          SELECT order_id, created_at
          FROM ext_a2a_messages
          WHERE kind='BLOCKED_INFRA'
            AND author='claude-lead'
            AND created_at >= :cutoff
            AND body_json::text ILIKE :needle
          ORDER BY created_at DESC
          LIMIT 50
        """), {
            "cutoff": cutoff,
            "needle": "%" + REVIEW_QUOTA_LOOP_MARKER + "%",
        }).mappings().all()
    finally:
        db.close()

    provider = aiprov.state("gemini_cli") or {}
    plan = _review_quota_loop_plan(
        len(rows),
        provider.get("state"),
        provider.get("usable", True),
    )
    plan["order_ids"] = sorted({int(x["order_id"]) for x in rows})
    plan["window_seconds"] = REVIEW_QUOTA_LOOP_WINDOW_SECONDS
    plan["applied"] = False
    if plan["contain"] and apply:
        aiprov.mark(
            "gemini_cli",
            aiprov.RATE_LIMITED,
            (
                "review quota loop containment: repeated lead quota/cooling "
                f"blocks={plan['block_count']} orders={plan['order_ids']}; "
                "bounded cooldown; paid dev fallback denied"
            )[:400],
            retry_after=REVIEW_QUOTA_COOLDOWN_SECONDS,
        )
        plan["applied"] = True
    return plan


def _facts() -> dict:
    db = SessionLocal()
    try:
        counts = db.execute(text("""
          SELECT
            count(*) FILTER (
              WHERE status IN (
                'queued','planning','running','waiting',
                'review','testing','fixing','acceptance'
              )
            ) AS active_jobs,
            count(*) FILTER (WHERE status='running') AS running_jobs,
            count(*) FILTER (
              WHERE status='waiting'
                AND COALESCE(wait_reason,'')
                    LIKE 'WAITING_FREE_CODING_PROVIDER%'
            ) AS provider_wait_jobs,
            count(*) FILTER (
              WHERE status='waiting'
                AND COALESCE(wait_reason,'') LIKE 'ждёт задачи:%'
            ) AS dependency_wait_jobs
          FROM ext_dev_jobs
        """)).mappings().one()
        providers = db.execute(text("""
          SELECT name,state,note,retry_at,updated_at
          FROM ext_ai_providers
          WHERE name IN ('gemini_cli','claude_code','openai')
          ORDER BY name
        """)).mappings().all()
        latest_gemini_run = db.execute(text("""
          SELECT error_text, finished_at
          FROM ext_a2a_runs
          WHERE provider='gemini_cli'
            AND outcome='failed'
            AND error_text IS NOT NULL
          ORDER BY id DESC
          LIMIT 1
        """)).mappings().first()
        runtime = db.execute(text("""
          SELECT
            count(*) FILTER (WHERE status='running') AS running_orders,
            count(*) FILTER (WHERE status='review_requested') AS review_orders,
            count(*) FILTER (WHERE status='queued') AS queued_orders,
            count(*) FILTER (WHERE status='returned') AS returned_orders,
            count(*) FILTER (
              WHERE status='blocked_infra'
                AND COALESCE(blocked_reason,'') LIKE
                    'all free development providers unavailable; paid fallback denied:%'
            ) AS transient_provider_blocked_orders
          FROM ext_a2a_orders
        """)).mappings().one()
    finally:
        db.close()

    provider_map = {}
    provider_notes_full = {}
    for raw in providers:
        row = dict(raw)
        name = str(row["name"])
        full_note = str(row.get("note") or "")
        provider_notes_full[name] = full_note
        provider_map[name] = {
            "state": str(row.get("state") or ""),
            "note": full_note[:260],
            "retry_at": (
                row["retry_at"].isoformat() if row.get("retry_at") else None
            ),
            "updated_at": (
                row["updated_at"].isoformat() if row.get("updated_at") else None
            ),
        }

    candidates = list(aiprov.development_candidates())
    free_states = {
        name: (provider_map.get(name) or {}).get("state")
        for name in FREE
    }
    active = int(counts.get("active_jobs") or 0)
    dev_running_rows = int(counts.get("running_jobs") or 0)
    running = int(runtime.get("running_orders") or 0)
    reviewing = int(runtime.get("review_orders") or 0)
    queued_orders = int(runtime.get("queued_orders") or 0)
    returned_orders = int(runtime.get("returned_orders") or 0)
    transient_provider_blocked_orders = int(
        runtime.get("transient_provider_blocked_orders") or 0
    )
    waiting = int(counts.get("provider_wait_jobs") or 0)

    capacity = sched.limits()
    pressure = sched.pressure()
    executor_capacity = int(capacity.get("MAX_EXECUTOR_WORKERS") or 0)
    review_capacity = int(capacity.get("MAX_REVIEW_WORKERS") or 0)
    runtime_pending = reviewing + queued_orders + returned_orders

    provider_blocked = bool(
        active > 0
        and running == 0
        and not candidates
        and (waiting > 0 or runtime_pending > 0)
    )
    capacity_blocked = bool(
        active > 0
        and running == 0
        and bool(candidates)
        and (
            ((queued_orders + returned_orders) > 0 and executor_capacity <= 0)
            or (reviewing > 0 and review_capacity <= 0)
        )
    )
    blocked = bool(provider_blocked or capacity_blocked)

    # Provider retry_at can be much longer than the real self-heal cadence.
    # Fresh health-probe evidence wins over stale A2A retry evidence. A proven
    # daily model quota must wait for the wrapper's real reset window; only a
    # proven short free-tier window gets the 5-minute anti-storm cadence.
    retryable_states = {"UNAVAILABLE_BILLING", "RATE_LIMITED", "TEMP_ERROR"}
    latest_run = dict(latest_gemini_run or {})
    short_quota_evidence = _short_free_tier_quota_retry(
        latest_run.get("error_text"),
        latest_run.get("finished_at"),
    )
    reset_epoch = None
    try:
        if GEMINI_FALLBACK_UNTIL.is_file():
            reset_epoch = GEMINI_FALLBACK_UNTIL.read_text(
                encoding="utf-8"
            ).strip()
    except OSError:
        reset_epoch = None
    gemini_row = provider_map.get("gemini_cli") or {}
    probe_error_text = _gemini_probe_error_text(
        provider_notes_full.get("gemini_cli", "")
    )
    # Durable A2A run evidence wins over the provider note. Provider notes are
    # intentionally bounded/truncated and /tmp Gemini reports are ephemeral;
    # ext_a2a_runs keeps the exact failed-call text and therefore survives both.
    daily_quota_evidence = _daily_free_tier_quota_retry(
        latest_run.get("error_text"),
        latest_run.get("finished_at"),
        reset_epoch=reset_epoch,
    )
    if daily_quota_evidence is None:
        daily_quota_evidence = _daily_free_tier_quota_retry(
            probe_error_text,
            gemini_row.get("updated_at"),
            reset_epoch=reset_epoch,
        )
    quota_evidence = daily_quota_evidence or short_quota_evidence
    gemini_probe = _gemini_reprobe_plan(provider_map, quota_evidence=quota_evidence)
    automatic_retries = []
    for name in FREE:
        row = provider_map.get(name) or {}
        state = str(row.get("state") or "")
        retry_at = row.get("retry_at")
        next_probe_at = (
            gemini_probe.get("next_probe_at") if name == "gemini_cli" else None
        )
        recoverable_probe_context = bool(
            name == "gemini_cli"
            and state == "AUTH_ERROR"
            and gemini_probe.get("source") == "invalid_probe_home_recoverable"
        )
        effective_retry_at = _effective_retry_at(next_probe_at, retry_at)
        if (state in retryable_states or recoverable_probe_context) and effective_retry_at:
            automatic_retries.append({
                "provider": name,
                "state": state,
                "retry_at": retry_at,
                "next_probe_at": next_probe_at,
                "effective_retry_at": effective_retry_at,
            })
    next_auto_retry_at = min(
        (
            x.get("effective_retry_at")
            for x in automatic_retries
            if x.get("effective_retry_at")
        ),
        default=None,
    )
    human_only = bool(
        provider_blocked
        and not automatic_retries
        and all(free_states.get(name) in HUMAN_STATES for name in FREE)
    )
    return {
        "checked_at": _now_iso(),
        "active_jobs": active,
        "running_jobs": running,
        "review_orders": reviewing,
        "queued_orders": queued_orders,
        "returned_orders": returned_orders,
        "transient_provider_blocked_orders": transient_provider_blocked_orders,
        "dev_running_rows": dev_running_rows,
        "provider_wait_jobs": waiting,
        "dependency_wait_jobs": int(counts.get("dependency_wait_jobs") or 0),
        "development_candidates": candidates,
        "free_provider_states": free_states,
        "providers": provider_map,
        "paid_openai_available": (
            (provider_map.get("openai") or {}).get("state") == "AVAILABLE"
        ),
        "paid_development_allowed": False,
        "executor_capacity": executor_capacity,
        "review_capacity": review_capacity,
        "capacity_source": capacity.get("source"),
        "load1": (capacity.get("facts") or {}).get("load1"),
        "load_per_cpu": (capacity.get("facts") or {}).get("load_per_cpu"),
        "ram_available_mb": (capacity.get("facts") or {}).get(
            "ram_available_mb"
        ),
        "under_pressure": bool(pressure.get("under_pressure")),
        "pressure_reasons": pressure.get("reasons") or [],
        "instant_headroom": bool(pressure.get("instant_headroom")),
        "historical_load_only": bool(pressure.get("historical_load_only")),
        "provider_blocked": provider_blocked,
        "capacity_blocked": capacity_blocked,
        "automatic_retries": automatic_retries,
        "gemini_reprobe": gemini_probe,
        "gemini_short_quota_evidence": short_quota_evidence,
        "gemini_daily_quota_evidence": daily_quota_evidence,
        "gemini_quota_evidence": quota_evidence,
        "next_auto_retry_at": next_auto_retry_at,
        "blocked": blocked,
        "human_only": human_only,
    }


def _fingerprint(facts: dict) -> str:
    # Fingerprint the blocker class, not volatile queue/load counters.
    # Otherwise the same Gemini/Claude outage can spam the owner whenever
    # load1 or queue depth changes by one.
    stable = {
        "blocked": facts.get("blocked"),
        "human_only": facts.get("human_only"),
        "free_provider_states": facts.get("free_provider_states"),
        "development_candidates": facts.get("development_candidates"),
        "paid_development_allowed": facts.get("paid_development_allowed"),
        "provider_blocked": facts.get("provider_blocked"),
        "capacity_blocked": facts.get("capacity_blocked"),
        "next_auto_retry_at": facts.get("next_auto_retry_at"),
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _message(facts: dict) -> str:
    states = facts.get("free_provider_states") or {}
    if facts.get("provider_blocked"):
        gemini_state = states.get("gemini_cli") or "unknown"
        reason = (
            "Нет доступного бесплатного coding-provider. "
            f"Claude Code: {states.get('claude_code') or 'unknown'}; "
            f"Gemini CLI: {gemini_state}."
        )
        if gemini_state == "UNSUPPORTED_LOCATION":
            reason += (
                " Gemini отклоняет сервер по географическому ограничению API; "
                "BORIS не будет пытаться обходить ограничение."
            )
        probe = facts.get("gemini_reprobe") or {}
        if probe.get("due"):
            action = (
                "Владелец сейчас НЕ нужен: бесплатная проверка Gemini уже "
                "должна выполняться ближайшим циклом dispatcher. "
                "Платный OpenAI самовольно не включается."
            )
        elif facts.get("next_auto_retry_at"):
            action = (
                "Владелец сейчас НЕ нужен: следующий реальный бесплатный "
                "health-probe запланирован на "
                f"{facts.get('next_auto_retry_at')}. "
                "Платный OpenAI самовольно не включается."
            )
        else:
            action = (
                "Автоматическое восстановление сейчас невозможно: нужен "
                "допустимый бесплатный coding-provider — восстановленная "
                "авторизация Claude Code либо Gemini в поддерживаемой среде. "
                "Платный OpenAI технически доступен, но BORIS сам его не включает."
            )
    elif facts.get("capacity_blocked"):
        reason = (
            "Coding-provider есть, но безопасная вычислительная ёмкость сервера "
            f"сейчас равна {facts.get('executor_capacity') or 0}. "
            f"load1={facts.get('load1')}; "
            f"давление={facts.get('under_pressure')}."
        )
        action = (
            "BORIS должен дождаться снижения нагрузки или освободить только "
            "необязательные/stale технические процессы, не трогая клиентский "
            "production."
        )
    else:
        reason = "Очередь не заблокирована provider/capacity."
        action = "Вмешательство владельца не требуется."

    return (
        "🚨 BORIS: автономная разработка реально остановлена.\n"
        f"Активных задач: {facts.get('active_jobs')}; "
        f"ждут coding-provider: {facts.get('provider_wait_jobs')}; "
        f"реально выполняются A2A: {facts.get('running_jobs')}; "
        f"на автоматической приёмке: {facts.get('review_orders')}; "
        f"в A2A-очереди: {facts.get('queued_orders')}.\n"
        + reason + "\n"
        "Практический эффект: клиентские исправления в очереди не двигаются. "
        + action + " После снятия причины штатный recovery подхватит очередь "
        "автоматически."
    )


def _reprobe_with_canonical_cli_env(min_interval_min=FREE_REPROBE_MINUTES) -> dict:
    """Run the free Gemini probe in the exact daemon CLI environment.

    The scope-guard ExecStartPost and manual SentinelX checks do not necessarily
    inherit the dispatcher service's HOME/PATH/proxy environment. A health probe
    from the wrong HOME previously wrote a false AUTH_ERROR into the shared
    provider state; a probe without the wrapper also bypassed model fallback.
    Fail closed unless the canonical root CLI environment can be reproduced.
    """
    managed_keys = {
        "HOME", "PATH", "TERM", "GEMINI_CLI_TRUST_WORKSPACE",
        "BORIS_GEMINI_FALLBACK_MODEL",
        *CANONICAL_PROXY_KEYS,
    }
    previous = {key: os.environ.get(key) for key in managed_keys}
    try:
        if os.geteuid() != 0:
            raise RuntimeError("canonical_cli_probe_requires_root")
        settings = Path(CANONICAL_CLI_HOME) / ".gemini" / "settings.json"
        if not settings.is_file():
            raise RuntimeError("canonical_gemini_settings_missing")

        os.environ["HOME"] = CANONICAL_CLI_HOME
        path_parts = [x for x in str(previous.get("PATH") or "").split(":") if x]
        if CANONICAL_CLI_BIN not in path_parts:
            path_parts.insert(0, CANONICAL_CLI_BIN)
        os.environ["PATH"] = ":".join(path_parts)
        os.environ.setdefault("TERM", "xterm-256color")
        os.environ["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        os.environ["BORIS_GEMINI_FALLBACK_MODEL"] = (
            CANONICAL_GEMINI_FALLBACK_MODEL
        )

        if CANONICAL_CLI_PROXY_ENV.exists():
            try:
                lines = CANONICAL_CLI_PROXY_ENV.read_text(
                    encoding="utf-8"
                ).splitlines()
            except OSError as exc:
                raise RuntimeError(
                    "canonical_cli_proxy_env_unreadable"
                ) from exc
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in CANONICAL_PROXY_KEYS:
                    continue
                os.environ[key] = value.strip().strip('"').strip("'")

        return aiprov.reprobe_free_development(
            min_interval_min=max(5, int(min_interval_min))
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _provider_probe_due(facts: dict) -> bool:
    # GEMINI_DAILY_RESET_PROACTIVE_PROBE_V1:
    # A retry_at expiry can make a provider look time-eligible before a real
    # health probe has succeeded. Probe from truth, not from the clock alone.
    #
    # Normal transient probing still requires active development work so BORIS
    # does not spend free-provider quota for no reason. A *proven daily Gemini
    # quota reset* is different: sales/MOP/ROP also depend on this provider.
    # After that reset we allow one canonical health probe even when there are
    # no active development jobs. This keeps the first live customer message
    # from becoming the recovery probe. Existing retry fences and the watcher
    # cadence remain the anti-storm boundary.
    if facts.get("under_pressure") and not facts.get("historical_load_only"):
        return False

    gemini = (facts.get("providers") or {}).get("gemini_cli") or {}
    state = str(gemini.get("state") or "")
    if state == "AVAILABLE":
        return False

    plan = facts.get("gemini_reprobe") or _gemini_reprobe_plan(
        facts.get("providers") or {}
    )
    if not plan.get("due"):
        return False

    if int(facts.get("active_jobs") or 0) > 0:
        return True

    return bool(
        state == "RATE_LIMITED"
        and plan.get("source") == "daily_model_quota_reset"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    probe = None
    probe_reap = None
    order_process_reap = None
    review_quota_containment = None
    review_replay_quarantine = None
    parent_evidence_recovery = None
    daily_probe_latch = None
    daily_quota_retry_reconcile = None
    provider_cooldown_alignment = None
    post_probe_cooldown_alignment = None
    review_provider_recovery = None
    model_config = _runtime_model_config_health(repair=not args.dry_run)
    if model_config.get("changed_count"):
        _append({
            "at": _now_iso(),
            "status": "runtime_model_config_self_healed",
            "runtime_model_config": model_config,
        })
    queued_hint_replay = None
    if not args.dry_run:
        probe_reap = _reap_stale_gemini_probe_processes()
        order_process_reap = _reap_stale_real_order_gemini_processes()
        review_replay_quarantine = _quarantine_queued_review_provider_orders()
        parent_evidence_recovery = _repair_quarantined_review_evidence_from_parent()
        queued_hint_replay = _replay_recovery_hints_for_queued_orders()
    review_quota_containment = _contain_reviewer_quota_loop(
        apply=not args.dry_run
    )
    before = _facts()
    daily_quota_retry_reconcile = _reconcile_daily_quota_retry_at(
        before, apply=not args.dry_run
    )
    if daily_quota_retry_reconcile.get("applied"):
        before = _facts()
    daily_probe_latch = _sync_gemini_double_daily_latch(
        before.get("gemini_daily_quota_evidence"), apply=not args.dry_run
    )
    provider_cooldown_alignment = _align_provider_retry_at_to_probe_plan(
        before, apply=not args.dry_run
    )
    if provider_cooldown_alignment.get("applied"):
        before = _facts()
    if not args.dry_run and _provider_probe_due(before):
        try:
            plan = before.get("gemini_reprobe") or {}
            interval = (
                5
                if plan.get("source") in {
                    "invalid_probe_home_recoverable",
                    "short_free_tier_quota_retry",
                }
                else FREE_REPROBE_MINUTES
            )
            probe = _reprobe_with_canonical_cli_env(interval)
        except Exception as exc:
            probe = {
                "status": "error",
                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
            }

    facts = _facts()
    if not args.dry_run and probe is not None:
        post_probe_cooldown_alignment = _align_provider_retry_at_to_probe_plan(
            facts, apply=True
        )
        if post_probe_cooldown_alignment.get("applied"):
            facts = _facts()
    provider_recovery = None
    if not args.dry_run:
        review_provider_recovery = _recover_review_provider_blocked_orders(facts)
        if review_provider_recovery.get("recovered_order_ids"):
            facts = _facts()
        provider_recovery = _recover_transient_provider_blocked_orders(facts)
        if provider_recovery.get("recovered_order_ids"):
            facts = _facts()
    else:
        review_provider_recovery = {
            "gate": _review_provider_recovery_gate(facts),
            "recovered_order_ids": [],
            "recovered_job_ids": [],
            "eligible_count": None,
            "race_skipped": 0,
        }
    facts["probe"] = probe
    facts["probe_reap"] = probe_reap
    facts["order_process_reap"] = order_process_reap
    facts["queued_hint_replay"] = queued_hint_replay
    facts["review_replay_quarantine"] = review_replay_quarantine
    facts["parent_evidence_recovery"] = parent_evidence_recovery
    facts["daily_probe_latch"] = daily_probe_latch
    facts["daily_quota_retry_reconcile"] = daily_quota_retry_reconcile
    facts["provider_recovery"] = provider_recovery
    facts["review_provider_recovery"] = review_provider_recovery
    facts["review_quota_containment"] = review_quota_containment
    facts["provider_cooldown_alignment"] = provider_cooldown_alignment
    facts["post_probe_cooldown_alignment"] = post_probe_cooldown_alignment
    facts["runtime_model_config"] = model_config
    fp = _fingerprint(facts)
    previous = _read_json(STATE)
    now = time.time()

    recovered = bool(
        before.get("blocked")
        and not facts.get("blocked")
        and facts.get("development_candidates")
    )
    if recovered:
        # Existing background recovery loop owns scheduling and will start work.
        # We only record the transition; do not create a second scheduler here.
        _append({
            "at": facts["checked_at"],
            "status": "provider_recovered",
            "facts": facts,
        })

    duplicate = bool(
        previous.get("fingerprint") == fp
        and now - float(previous.get("sent_epoch") or 0) < DEDUP_SECONDS
    )
    should_alert = bool(facts.get("human_only") and not duplicate)

    out = {
        "ok": True,
        "blocked": bool(facts.get("blocked")),
        "human_only": bool(facts.get("human_only")),
        "duplicate": duplicate,
        "should_alert": should_alert,
        "facts": facts,
        "message": _message(facts) if facts.get("blocked") else None,
    }

    if args.dry_run:
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0

    sent = False
    if should_alert:
        try:
            from app.ext_api import notify
            sent = bool(notify.send(out["message"]))
        except Exception:
            sent = False

    if should_alert:
        _append({
            "at": facts["checked_at"],
            "status": "sent" if sent else "send_failed",
            "fingerprint": fp,
            "facts": facts,
        })

    state_payload = {
        "fingerprint": fp,
        "checked_at": facts["checked_at"],
        "blocked": facts.get("blocked"),
        "human_only": facts.get("human_only"),
        "provider_wait_jobs": facts.get("provider_wait_jobs"),
        "transient_provider_blocked_orders": facts.get(
            "transient_provider_blocked_orders"
        ),
        "free_provider_states": facts.get("free_provider_states"),
        "development_candidates": facts.get("development_candidates"),
        "provider_blocked": facts.get("provider_blocked"),
        "capacity_blocked": facts.get("capacity_blocked"),
        "executor_capacity": facts.get("executor_capacity"),
        "load1": facts.get("load1"),
        "load_per_cpu": facts.get("load_per_cpu"),
        "under_pressure": facts.get("under_pressure"),
        "pressure_reasons": facts.get("pressure_reasons"),
        "automatic_retries": facts.get("automatic_retries"),
        "next_auto_retry_at": facts.get("next_auto_retry_at"),
        "last_probe": probe,
        "last_provider_recovery": provider_recovery,
        "sent_epoch": (
            now if sent else float(previous.get("sent_epoch") or 0)
        ),
        "sent_at": facts["checked_at"] if sent else previous.get("sent_at"),
    }
    _write_json(STATE, state_payload)
    print(json.dumps(
        {**out, "sent": sent, "status": "sent" if sent else "checked"},
        ensure_ascii=False,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
