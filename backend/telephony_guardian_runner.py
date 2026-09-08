import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv

# Direct CLI/diagnostic execution must observe the same runtime configuration
# as the systemd guardian. Existing systemd/container environment always wins.
ROOT = Path(__file__).resolve().parent
PHONE_ROOT = (ROOT.parent / "clients" / "boris-phone").resolve()
load_dotenv(ROOT / ".env", override=False)

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.mcn_core import current_mcn_accounts, mcn_company_card_manual_delivery_confirmation
from app.services.telephony_core import (
    ensure_schema,
    active_phone_entitlements,
    phone_client_readiness,
    phone_native_release_environment,
    reconcile_phone_manager_action,
    retire_phone_manager_action,
    telephony_autonomy_guardian,
)


def _latest_source_generation(root: Path, excluded: set[str]) -> str:
    """Cheap source generation token without hashing build/node_modules trees."""
    latest_ns = 0
    allowed = {
        ".ts", ".tsx", ".js", ".json", ".kt", ".java", ".xml", ".gradle",
        ".kts", ".swift", ".plist", ".sh", ".py", ".properties",
    }
    special = {"package.json", "package-lock.json", "settings.gradle", "gradlew"}
    if not root.is_dir():
        return "missing"
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excluded]
        for name in files:
            p = Path(base) / name
            if p.suffix.lower() not in allowed and name not in special:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            latest_ns = max(latest_ns, st.st_mtime_ns, st.st_ctime_ns)
    return hashlib.sha256(str(latest_ns).encode("ascii")).hexdigest()[:14]


def _windows_signing_input_ready() -> bool:
    """Only a local certificate file counts; never fetch signing material."""
    raw = str(os.getenv("CSC_LINK") or "").strip()
    if not raw:
        return False
    try:
        return Path(raw).expanduser().is_file()
    except OSError:
        return False


def _android_connected_device_state() -> dict:
    """Detect only unambiguous online ADB devices; never expose serials."""
    adb = str(os.getenv("ADB_BIN") or "").strip() or shutil.which("adb")
    if not adb:
        local = ROOT.parent / ".tools" / "android-sdk" / "platform-tools" / "adb"
        if local.is_file() and os.access(local, os.X_OK):
            adb = str(local)
    if not adb:
        return {"status": "adb_unavailable", "count": 0}
    try:
        cp = subprocess.run(
            [adb, "devices"], text=True, capture_output=True, timeout=5, check=False
        )
    except Exception:
        return {"status": "adb_error", "count": 0}
    if cp.returncode != 0:
        return {"status": "adb_error", "count": 0}
    devices = []
    for line in (cp.stdout or "").splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if not devices:
        return {"status": "none_connected", "count": 0}
    if len(devices) > 1:
        return {"status": "multiple_connected", "count": len(devices)}
    fingerprint = hashlib.sha256(devices[0].encode("utf-8")).hexdigest()[:8]
    return {"status": "one_connected", "count": 1, "device_fingerprint": fingerprint}


def _release_candidates() -> list[dict]:
    env = phone_native_release_environment(PHONE_ROOT)
    out = []
    android = env.get("android") or {}
    if android.get("release_preflight_inputs_ready"):
        out.append({
            "platform": "android",
            "root": PHONE_ROOT / "android",
            "paths": [
                "../clients/boris-phone/android",
                "../clients/boris-phone/scripts/artifact-manifest.py",
                "../clients/boris-phone/native-release-evidence.json",
            ],
            "goal": (
                "Собрать Android production release BORIS Phone штатным release-build.sh, "
                "проверить подпись, обновить канонический artifact manifest. Не считать "
                "release_verified без доказанного real-device QA."
            ),
            "acceptance": [
                "Release-preflight подтверждает наличие Android signing/Firebase inputs без вывода секретов",
                "Release AAB собран и криптографическая подпись проверена",
                "Канонический release-artifacts.json обновлён и SHA-256 совпадает с текущим AAB",
                "Без real-device QA release_verified остаётся false и внешний блокер показан честно",
            ],
        })
    if _windows_signing_input_ready():
        out.append({
            "platform": "windows",
            "root": PHONE_ROOT / "desktop",
            "paths": [
                "../clients/boris-phone/desktop",
                "../clients/boris-phone/scripts/artifact-manifest.py",
                "../clients/boris-phone/native-release-evidence.json",
            ],
            "goal": (
                "Собрать подписанный Windows production release BORIS Phone через штатный "
                "electron-builder, проверить подпись и обновить канонический manifest. "
                "Не считать real-device QA выполненным без отдельного доказательства."
            ),
            "acceptance": [
                "Windows signing-preflight подтверждает локальный code-signing certificate без вывода секрета",
                "Windows installer собран и подпись проверена",
                "Канонический release-artifacts.json обновлён и SHA-256 совпадает с installer",
                "Без real Windows smoke/device QA финальная release-приёмка не подделывается",
            ],
        })
    return out


def native_release_autopilot_once() -> dict:
    """DETECT external inputs -> enqueue one release job and dependent device QA.

    Heavy builds and real-device checks never run inside the one-minute guardian.
    The existing Development Orchestrator owns execution, capacity gates, retries
    and QA. Request ids are deterministic, so repeated timer ticks create no
    duplicate work.
    """
    candidates = _release_candidates()
    device_state = _android_connected_device_state()
    if not candidates:
        return {
            "status": "waiting_external_inputs",
            "ready_platforms": [],
            "created": [],
            "existing": [],
            "device_state": device_state,
            "device_qa_created": [],
            "device_qa_existing": [],
        }

    from app.ext_api import db as ext_db, dev
    from app.ext_api.clients import LEAD_KEY, EXECUTOR

    created = []
    existing_ids = []
    release_jobs = {}
    generations = {}
    for c in candidates:
        generation = _latest_source_generation(
            c["root"], {"node_modules", "build", "dist", "release", "release-win-qa", ".gradle", ".cache"}
        )
        generations[c["platform"]] = generation
        request_id = f"phone_rel_{c['platform']}_{generation}"[:40]
        existing = ext_db.one(
            "SELECT id,status FROM ext_dev_jobs WHERE request_id=:r ORDER BY id DESC LIMIT 1",
            r=request_id,
        )
        if existing:
            job_id = f"dev_{existing['id']}"
            existing_ids.append(job_id)
            release_jobs[c["platform"]] = job_id
            continue
        job = dev.create(
            LEAD_KEY,
            request_id,
            f"BORIS Phone {c['platform']} — autonomous signed release",
            c["goal"],
            c["acceptance"],
            scope={"paths": c["paths"], "workstream_id": "telephony_core"},
            owner=EXECUTOR,
            priority=82,
            locks=c["paths"],
        )
        created.append(job["dev_job_id"])
        release_jobs[c["platform"]] = job["dev_job_id"]

    device_qa_created = []
    device_qa_existing = []
    android_candidate = next((c for c in candidates if c["platform"] == "android"), None)
    android_release_job = release_jobs.get("android")
    if (
        android_candidate
        and android_release_job
        and device_state.get("status") == "one_connected"
        and device_state.get("device_fingerprint")
    ):
        generation = generations.get("android") or "unknown"
        fingerprint = str(device_state["device_fingerprint"])[:8]
        qa_request_id = f"phone_qa_android_{generation[:10]}_{fingerprint}"[:40]
        existing_qa = ext_db.one(
            "SELECT id,status FROM ext_dev_jobs WHERE request_id=:r ORDER BY id DESC LIMIT 1",
            r=qa_request_id,
        )
        if existing_qa:
            device_qa_existing.append(f"dev_{existing_qa['id']}")
        else:
            qa_job = dev.create(
                LEAD_KEY,
                qa_request_id,
                "BORIS Phone Android — autonomous real-device preflight",
                (
                    "После текущего подписанного Android release автоматически выполнить "
                    "реальный device-preflight на единственном подключённом устройстве: "
                    "установка/версия, уведомления и self-managed Telecom. Не считать "
                    "полный device QA доказанным без фактических locked/background-call, "
                    "Bluetooth и network-recovery проверок."
                ),
                [
                    "Зависимость от текущего signed Android release завершена и проверяется тот же актуальный артефакт",
                    "device-preflight.sh проходит на реально подключённом Android: пакет установлен, версия считана, уведомления разрешены где требуются, self-managed Telecom зарегистрирован",
                    "Серийный номер устройства и другие чувствительные device identifiers не попадают в публичные evidence/логи задачи",
                    "Locked/background call E2E, Bluetooth headset и network recovery остаются NOT_PROVEN до реального доказательства; device_qa_verified=true не выставляется только по preflight",
                ],
                scope={"paths": android_candidate["paths"], "workstream_id": "telephony_core"},
                owner=EXECUTOR,
                priority=81,
                depends_on=[android_release_job],
                locks=android_candidate["paths"],
            )
            device_qa_created.append(qa_job["dev_job_id"])

    if created or device_qa_created:
        dev.schedule()
    return {
        "status": "queued" if (created or device_qa_created) else "already_queued",
        "ready_platforms": [c["platform"] for c in candidates],
        "created": created,
        "existing": existing_ids,
        "device_state": device_state,
        "device_qa_created": device_qa_created,
        "device_qa_existing": device_qa_existing,
    }


_MCN_ACCEPTANCE_STORAGE_KEY = "mcn_real_e2e_runtime"
_MCN_ACCEPTANCE_REFRESH_SECONDS = 300


def _real_mcn_accounts(include_synthetic: bool = False) -> list[str]:
    """Compatibility wrapper around the canonical MCN account resolver."""
    return current_mcn_accounts(include_synthetic=include_synthetic)


def _safe_primary_action(readiness: dict) -> dict:
    action = readiness.get("primary_next_action") if isinstance(readiness, dict) else {}
    action = action if isinstance(action, dict) else {}
    return {
        "code": str(action.get("code") or "unknown")[:120],
        "actor": str(action.get("actor") or "boris")[:40],
        "owner_action_required": bool(action.get("owner_action_required")),
        "text": str(action.get("text") or "")[:500],
    }


def _mcn_acceptance_payload(account_id: str, readiness: dict) -> dict:
    """Safe, secret-free acceptance state derived only from real runtime evidence."""
    gates = dict(readiness.get("telephony_gates") or {})
    media = dict(readiness.get("media_diagnostics") or {})
    turn = dict(media.get("turn_runtime") or {})
    mcn_ready = dict(readiness.get("mcn_readiness") or {})
    mcn_transport = dict(readiness.get("mcn_transport") or {})
    mcn_network = dict(readiness.get("mcn_network") or {})
    provider = dict(readiness.get("provider_evidence") or {})
    action = _safe_primary_action(readiness)

    checks = {
        "provider_selected": bool(gates.get("provider_selected")),
        "provider_connected": bool(gates.get("provider_connected")),
        "mcn_trunk_and_did_ready": bool(mcn_ready.get("ready")),
        "mcn_transport_registered": bool(mcn_transport.get("ready")),
        "mcn_network_ready": bool(mcn_network.get("ready")),
        "device_online": bool(gates.get("device_online")),
        "turn_runtime_ready": bool(turn.get("runtime_ready")) if turn else None,
        "real_provider_call_seen": bool(gates.get("real_provider_call_seen")),
        "real_inbound_seen": bool(gates.get("real_inbound_seen")),
        "real_outbound_seen": bool(gates.get("real_outbound_seen")),
        "real_answer_seen": bool(gates.get("real_answer_seen")),
        "real_call_completed": bool(gates.get("real_call_completed")),
        "real_media_provider": bool(gates.get("real_media_provider")),
        "real_hold_completed": bool(gates.get("real_hold_completed")),
        "real_resume_completed": bool(gates.get("real_resume_completed")),
        "real_crm_linked": bool(gates.get("real_crm_linked")),
        "real_cleanup_completed": bool(gates.get("real_cleanup_completed")),
        "mcn_carrier_linked": bool(gates.get("mcn_carrier_linked")),
        "recording_received": bool(gates.get("recording_received")),
        "transcription_completed": bool(gates.get("transcription_completed")),
        "ai_analysis_completed": bool(gates.get("ai_analysis_completed")),
        "real_inbound_e2e_chain": bool(gates.get("real_inbound_e2e_chain")),
        "real_outbound_lifecycle": bool(gates.get("real_outbound_lifecycle")),
    }
    required = [value for value in checks.values() if value is not None]
    verified = bool(required) and all(required)
    remaining = [key for key, value in checks.items() if value is False]
    status = "verified" if verified else ("owner_action" if action["owner_action_required"] else "waiting_evidence")
    semantic = {
        "status": status,
        "checks": checks,
        "remaining_codes": remaining,
        "primary_next_action_code": action["code"],
        "owner_action_required": action["owner_action_required"],
        "provider_status": str(provider.get("status") or "not_configured"),
        "provider_health_status": str(provider.get("last_health_status") or ""),
    }
    fingerprint = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema": 1,
        "status": status,
        "account_id": str(account_id),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "evidence_fingerprint": fingerprint,
        "telephony_readiness_pct": readiness.get("telephony_readiness_pct"),
        "provider_status": semantic["provider_status"],
        "provider_health_status": semantic["provider_health_status"],
        "checks": checks,
        "remaining_codes": remaining,
        "primary_next_action": action,
        "owner_action_required": bool(action["owner_action_required"] and not verified),
        "truth": "read-only real-evidence watcher; never originates a call and never treats source/UI readiness as telephony proof",
    }


def _persist_mcn_acceptance(payload: dict) -> bool:
    """Persist only state changes or a bounded freshness refresh."""
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        return False
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"{account_id}:{_MCN_ACCEPTANCE_STORAGE_KEY}"
        })
        row = db.execute(text("""
            SELECT id,value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"a": account_id, "k": _MCN_ACCEPTANCE_STORAGE_KEY}).mappings().first()
        previous = {}
        if row and row.get("value"):
            try:
                previous = json.loads(str(row["value"]))
            except Exception:
                previous = {}
        same = (
            str(previous.get("evidence_fingerprint") or "")
            == str(payload.get("evidence_fingerprint") or "")
        )
        fresh = False
        stamp = previous.get("checked_at")
        if same and stamp:
            try:
                previous_at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if previous_at.tzinfo is None:
                    previous_at = previous_at.replace(tzinfo=timezone.utc)
                fresh = (datetime.now(timezone.utc) - previous_at).total_seconds() < _MCN_ACCEPTANCE_REFRESH_SECONDS
            except Exception:
                fresh = False
        if same and fresh:
            db.rollback()
            return False
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:id"), {
                "v": raw, "id": int(row["id"])
            })
        else:
            db.execute(text("""
                INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)
            """), {"a": account_id, "k": _MCN_ACCEPTANCE_STORAGE_KEY, "v": raw})
        db.commit()
        return True
    finally:
        db.close()


def _retired_mcn_acceptance_payload(account_id: str, previous: dict | None = None) -> dict:
    """Turn old MCN runtime evidence into explicit history after MCN is no longer current."""
    previous = previous if isinstance(previous, dict) else {}
    previous_status = str(previous.get("status") or "unknown")
    previous_fingerprint = str(previous.get("evidence_fingerprint") or "")[:40]
    checked_at = datetime.now(timezone.utc).isoformat()
    semantic = {
        "status": "retired",
        "account_id": str(account_id),
        "previous_status": previous_status,
        "previous_evidence_fingerprint": previous_fingerprint,
    }
    fingerprint = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema": 1,
        "status": "retired",
        "account_id": str(account_id),
        "checked_at": checked_at,
        "retired_at": checked_at,
        "evidence_fingerprint": fingerprint,
        "previous_status": previous_status,
        "previous_evidence_fingerprint": previous_fingerprint,
        "telephony_readiness_pct": None,
        "provider_status": "not_mcn",
        "provider_health_status": "",
        "checks": {},
        "remaining_codes": [],
        "primary_next_action": {
            "code": "mcn_not_configured",
            "actor": "none",
            "owner_action_required": False,
            "text": "MCN больше не является текущим оператором этого аккаунта; старое E2E-состояние оставлено только как история.",
        },
        "owner_action_required": False,
        "truth": "retired runtime evidence; no current MCN provider/trunk/DID is authoritative for this account",
    }


def _retire_stale_mcn_acceptance(active_accounts: list[str] | set[str],
                                 include_synthetic: bool = False) -> int:
    """Retire runtime rows whose MCN contour was removed or explicitly switched away."""
    active = {str(x).strip() for x in (active_accounts or []) if str(x or "").strip()}
    db = SessionLocal()
    retired = 0
    try:
        rows = db.execute(text("""
            SELECT DISTINCT ON (account_id) id,account_id,value
            FROM storage
            WHERE key=:k
              AND account_id IS NOT NULL
              AND btrim(account_id)<>''
              AND (:include_synthetic OR NOT (
                    lower(account_id) ~ '^__.*qa'
                    OR lower(account_id) ~ '^qa[-_]'
              ))
            ORDER BY account_id,id DESC
        """), {
            "k": _MCN_ACCEPTANCE_STORAGE_KEY,
            "include_synthetic": bool(include_synthetic),
        }).mappings().all()
        for row in rows:
            account_id = str(row.get("account_id") or "").strip()
            if not account_id or account_id in active:
                continue
            previous = {}
            try:
                previous = json.loads(str(row.get("value") or "{}"))
            except Exception:
                previous = {}
            if str(previous.get("status") or "") == "retired":
                continue
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
                "k": f"{account_id}:{_MCN_ACCEPTANCE_STORAGE_KEY}"
            })
            payload = _retired_mcn_acceptance_payload(account_id, previous)
            db.execute(text("UPDATE storage SET value=:v WHERE id=:id"), {
                "v": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "id": int(row["id"]),
            })
            retired += 1
        db.commit()
        return retired
    finally:
        db.close()


def _retire_stale_mcn_manager_actions(active_accounts: list[str] | set[str],
                                      include_synthetic: bool = False) -> dict:
    """Cancel manager actions created by this MCN watcher after MCN stops being current."""
    active = {str(x).strip() for x in (active_accounts or []) if str(x or "").strip()}
    db = SessionLocal()
    try:
        if not db.execute(text("SELECT to_regclass('public.telephony_manager_actions')")).scalar():
            return {"cancelled": 0, "errors": 0, "schema_missing": True}
        rows = db.execute(text("""
            SELECT DISTINCT account_id
            FROM telephony_manager_actions
            WHERE status='open'
              AND metadata_json->>'source'='mcn_acceptance_watcher'
              AND (:include_synthetic OR NOT (
                    lower(account_id) ~ '^__.*qa'
                    OR lower(account_id) ~ '^qa[-_]'
              ))
            ORDER BY account_id
        """), {"include_synthetic": bool(include_synthetic)}).scalars().all()
    finally:
        db.close()
    cancelled = 0
    errors = 0
    for raw in rows:
        account_id = str(raw or "").strip()
        if not account_id or account_id in active:
            continue
        try:
            out = retire_phone_manager_action(account_id, "mcn_not_current")
            cancelled += int(out.get("cancelled") or 0)
        except Exception:
            errors += 1
    return {"cancelled": cancelled, "errors": errors}


def real_mcn_acceptance_watch_once(include_synthetic: bool = False) -> dict:
    """Observe every current MCN account and automatically close E2E evidence when it becomes true."""
    accounts = _real_mcn_accounts(include_synthetic=include_synthetic)
    retired = _retire_stale_mcn_acceptance(accounts, include_synthetic=include_synthetic)
    manager_actions_retired = _retire_stale_mcn_manager_actions(
        accounts, include_synthetic=include_synthetic
    )
    if not accounts:
        return {
            "status": "waiting_external_inputs",
            "real_accounts": 0,
            "verified": 0,
            "waiting_evidence": 0,
            "owner_action_accounts": 0,
            "manager_action_accounts": 0,
            "boris_action_accounts": 0,
            "internal_errors": 0,
            "persisted": 0,
            "retired": retired,
            "manager_actions_retired": manager_actions_retired,
            "owner_action_required": False,
            "truth": "no current MCN account exists; stale runtime evidence and manager actions are retired instead of treated as live",
        }

    results = []
    persisted = 0
    internal_errors = 0
    for account_id in accounts:
        try:
            readiness = phone_client_readiness(account_id)
            payload = _mcn_acceptance_payload(account_id, readiness)
            if _persist_mcn_acceptance(payload):
                persisted += 1
            manager_action_result = {"status": "not_run"}
            try:
                manager_action_result = reconcile_phone_manager_action(
                    account_id, readiness, include_synthetic=include_synthetic
                )
            except Exception as action_exc:
                internal_errors += 1
                manager_action_result = {
                    "status": "error",
                    "error_type": type(action_exc).__name__[:120],
                }
            action = manager_action_result.get("action") if isinstance(manager_action_result, dict) else None
            results.append({
                "account_id": account_id,
                "status": payload["status"],
                "evidence_fingerprint": payload["evidence_fingerprint"],
                "remaining_codes": payload["remaining_codes"],
                "primary_next_action": payload["primary_next_action"],
                "owner_action_required": payload["owner_action_required"],
                "manager_action": {
                    "status": manager_action_result.get("status"),
                    "id": (action or {}).get("id") if isinstance(action, dict) else None,
                    "action_code": (action or {}).get("action_code") if isinstance(action, dict) else None,
                    "assigned_user_id": (action or {}).get("assigned_user_id") if isinstance(action, dict) else None,
                    "error_type": manager_action_result.get("error_type"),
                },
            })
        except Exception as exc:
            internal_errors += 1
            results.append({
                "account_id": account_id,
                "status": "diagnostic_error",
                "error_type": type(exc).__name__[:120],
                "owner_action_required": False,
            })

    verified = sum(1 for item in results if item.get("status") == "verified")
    owner_action_accounts = sum(1 for item in results if item.get("owner_action_required"))
    manager_action_accounts = sum(
        1 for item in results
        if str((item.get("primary_next_action") or {}).get("actor") or "") == "manager"
    )
    boris_action_accounts = sum(
        1 for item in results
        if str((item.get("primary_next_action") or {}).get("actor") or "") == "boris"
    )
    waiting = sum(1 for item in results if item.get("status") in {"waiting_evidence", "owner_action"})
    if internal_errors:
        status = "degraded"
    elif owner_action_accounts:
        status = "owner_action"
    elif verified == len(accounts):
        status = "verified"
    else:
        status = "waiting_evidence"
    return {
        "status": status,
        "real_accounts": len(accounts),
        "verified": verified,
        "waiting_evidence": waiting,
        "owner_action_accounts": owner_action_accounts,
        "manager_action_accounts": manager_action_accounts,
        "boris_action_accounts": boris_action_accounts,
        "internal_errors": internal_errors,
        "persisted": persisted,
        "retired": retired,
        "manager_actions_retired": manager_actions_retired,
        "owner_action_required": bool(owner_action_accounts),
        "results": results,
        "truth": "guardian watches real evidence only; it never creates a paid/test call",
    }



_MCN_MAILBOX_AUTONBOARD_STORAGE_KEY = "mcn_mailbox_auto_onboard_runtime"
_MCN_MAILBOX_AUTONBOARD_REFRESH_SECONDS = 300
_MCN_COMPANY_CARD_SEND_STATE_KEY = "mcn_company_card_send_v1"
_MCN_OWNER_ALERT_STORAGE_KEY = "mcn_phone_owner_alert_v1"
_MCN_OWNER_ALERT_RETRY_SECONDS = 3600
_MCN_FOLLOWUP_STORAGE_KEY = "mcn_company_card_followup_v1"
_MCN_FOLLOWUP_POLICY_STORAGE_KEY = "mcn_company_card_followup_policy_v1"
_MCN_FOLLOWUP_FIRST_DELAY_SECONDS = 48 * 3600
_MCN_FOLLOWUP_REPEAT_DELAY_SECONDS = 72 * 3600
_MCN_FOLLOWUP_SAFE_RETRY_SECONDS = 3600
_MCN_FOLLOWUP_MAX_ATTEMPTS = 2


def _single_active_phone_account() -> dict:
    rows = sorted({
        str(x.get("account_id") or "").strip()
        for x in active_phone_entitlements()
        if str(x.get("account_id") or "").strip()
    })
    if len(rows) == 1:
        return {"status": "ok", "account_id": rows[0]}
    if not rows:
        return {"status": "waiting_phone_account", "accounts": 0}
    return {"status": "ambiguous_phone_accounts", "accounts": len(rows)}


def _single_active_imap_mailbox() -> dict:
    db = SessionLocal()
    try:
        rows = [
            dict(x)
            for x in db.execute(text("""
                SELECT id,email_address
                FROM client_mailboxes
                WHERE status='active'
                  AND imap_host IS NOT NULL AND btrim(imap_host)<>''
                  AND imap_last_error IS NULL
                ORDER BY id
            """)).mappings().all()
        ]
    finally:
        db.close()
    if len(rows) == 1:
        return {
            "status": "ok",
            "mailbox_id": int(rows[0]["id"]),
            "mailbox": str(rows[0].get("email_address") or "")[:200],
        }
    if not rows:
        return {"status": "waiting_imap_mailbox", "mailboxes": 0}
    return {"status": "ambiguous_imap_mailboxes", "mailboxes": len(rows)}


def _mailbox_autoonboard_recent(account_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"a": account_id, "k": _MCN_MAILBOX_AUTONBOARD_STORAGE_KEY}).first()
    finally:
        db.close()
    if not row or not row[0]:
        return None
    try:
        payload = json.loads(str(row[0]))
        stamp = datetime.fromisoformat(str(payload.get("checked_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - stamp).total_seconds() < _MCN_MAILBOX_AUTONBOARD_REFRESH_SECONDS:
            return payload
    except Exception:
        return None
    return None


def _persist_mailbox_autoonboard(account_id: str, payload: dict) -> None:
    safe = dict(payload or {})
    safe["checked_at"] = datetime.now(timezone.utc).isoformat()
    raw = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"{account_id}:{_MCN_MAILBOX_AUTONBOARD_STORAGE_KEY}"
        })
        row = db.execute(text("""
            SELECT id FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"a": account_id, "k": _MCN_MAILBOX_AUTONBOARD_STORAGE_KEY}).first()
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:id"), {
                "v": raw, "id": int(row[0])
            })
        else:
            db.execute(text("""
                INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)
            """), {"a": account_id, "k": _MCN_MAILBOX_AUTONBOARD_STORAGE_KEY, "v": raw})
        db.commit()
    finally:
        db.close()


def _mcn_owner_action_alert_once(payload: dict) -> dict:
    """Notify the platform owner once when real MCN SIP is blocked on a human product choice.

    This path is deliberately global rather than account-scoped: at this point
    the correct Phone account is precisely what is unknown. No SIP credentials,
    phone numbers or banking details are included in the alert.
    """
    src = payload if isinstance(payload, dict) else {}
    action = src.get("primary_next_action") if isinstance(src.get("primary_next_action"), dict) else {}
    code = str(action.get("code") or "").strip()[:120]
    allowed = {
        "mcn_phone_entitlement_required",
        "mcn_phone_account_binding_required",
    }
    if not bool(src.get("owner_action_required")) or code not in allowed:
        return {"status": "not_needed", "sent": False}

    operational = src.get("operational_reply") if isinstance(src.get("operational_reply"), dict) else {}
    signature_payload = {
        "code": code,
        "provider_message_id": str(operational.get("message_id") or "")[:500],
        "candidate_count": int(src.get("candidate_count") or 0),
        "phone_account_status": str(src.get("phone_account_status") or "")[:80],
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    now = datetime.now(timezone.utc)
    lock_key = f"mcn-phone-owner-alert|{signature}"
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
        row = db.execute(text("""
            SELECT id,value FROM storage
            WHERE account_id='__mcn_mailbox_watch__' AND key=:k
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
        """), {"k": _MCN_OWNER_ALERT_STORAGE_KEY}).first()
        current = {}
        if row and row[1]:
            try:
                current = json.loads(str(row[1]))
            except Exception:
                current = {}
        same = str(current.get("signature") or "") == signature
        status = str(current.get("status") or "")
        updated_at = None
        try:
            updated_at = datetime.fromisoformat(str(current.get("updated_at") or "").replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except Exception:
            updated_at = None
        if same and status == "sent":
            db.commit()
            return {"status": "already_sent", "sent": True, "signature": signature[:16]}
        if same and status == "sending" and updated_at is not None:
            age = max(0.0, (now - updated_at.astimezone(timezone.utc)).total_seconds())
            if age < _MCN_OWNER_ALERT_RETRY_SECONDS:
                db.commit()
                return {"status": "in_flight", "sent": False, "signature": signature[:16]}
        if same and status == "retry" and updated_at is not None:
            age = max(0.0, (now - updated_at.astimezone(timezone.utc)).total_seconds())
            if age < _MCN_OWNER_ALERT_RETRY_SECONDS:
                db.commit()
                return {"status": "throttled", "sent": False, "signature": signature[:16]}

        claim = {
            "signature": signature,
            "status": "sending",
            "code": code,
            "attempts": int(current.get("attempts") or 0) + 1 if same else 1,
            "updated_at": now.isoformat(),
        }
        raw = json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:i"), {"v": raw, "i": int(row[0])})
        else:
            db.execute(text("""
                INSERT INTO storage(account_id,key,value)
                VALUES('__mcn_mailbox_watch__',:k,:v)
            """), {"k": _MCN_OWNER_ALERT_STORAGE_KEY, "v": raw})
        db.commit()
    except Exception:
        db.rollback()
        return {"status": "state_error", "sent": False}
    finally:
        db.close()

    text_msg = (
        "BORIS Phone: MCN уже прислал готовые SIP-параметры. "
        + str(action.get("text") or "").strip()
        + " Секретные SIP-данные в уведомление не выводятся."
    )[:3600]
    try:
        from app.ext_api.notify import send as notify_owner
        delivered = bool(notify_owner(text_msg))
    except Exception:
        delivered = False

    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
        row = db.execute(text("""
            SELECT id,value FROM storage
            WHERE account_id='__mcn_mailbox_watch__' AND key=:k
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
        """), {"k": _MCN_OWNER_ALERT_STORAGE_KEY}).first()
        current = {}
        if row and row[1]:
            try:
                current = json.loads(str(row[1]))
            except Exception:
                current = {}
        if not row or str(current.get("signature") or "") != signature:
            db.rollback()
            return {"status": "state_changed", "sent": delivered, "signature": signature[:16]}
        current["status"] = "sent" if delivered else "retry"
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        current["delivered"] = delivered
        db.execute(
            text("UPDATE storage SET value=:v WHERE id=:i"),
            {
                "v": json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "i": int(row[0]),
            },
        )
        db.commit()
        return {
            "status": "sent" if delivered else "retry",
            "sent": delivered,
            "signature": signature[:16],
        }
    except Exception:
        db.rollback()
        return {"status": "finalize_error", "sent": delivered, "signature": signature[:16]}
    finally:
        db.close()


def _owner_company_card_readiness() -> dict:
    required = (
        "org_name", "inn", "ogrnip", "address",
        "bank_account", "bank_bik", "bank_name", "corr_account",
        "email", "phone",
    )
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id='__owner' AND key='requisites'
            ORDER BY id DESC LIMIT 1
        """)).first()
    finally:
        db.close()
    if not row or not row[0]:
        return {"status": "missing", "complete": False, "missing": list(required)}
    try:
        payload = json.loads(str(row[0]))
    except Exception:
        return {"status": "invalid", "complete": False, "missing": list(required)}
    if not isinstance(payload, dict):
        return {"status": "invalid", "complete": False, "missing": list(required)}
    missing = [k for k in required if not str(payload.get(k) or "").strip()]
    return {
        "status": "ready" if not missing else "incomplete",
        "complete": not missing,
        "missing": missing,
        "updated_at": str(payload.get("updated_at") or "")[:80] or None,
        "fields_present": len(required) - len(missing),
        "fields_required": len(required),
    }


def _prepare_mcn_company_card_draft(
    mailbox_id: int,
    request_message_id: str | None = None,
    request_date: str | None = None,
) -> dict:
    script = ROOT / "scripts" / "phone-mcn-company-card-send.py"
    python_bin = ROOT / "venv" / "bin" / "python"
    argv = [
        str(python_bin), str(script),
        "--mailbox-id", str(int(mailbox_id)),
        "--save-draft",
    ]
    if str(request_message_id or "").strip():
        argv += ["--request-message-id", str(request_message_id).strip()]
    if str(request_date or "").strip():
        argv += ["--request-date", str(request_date).strip()]
    try:
        cp = subprocess.run(
            argv,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "retry", "reason": "company_card_draft_timeout"}
    except Exception as exc:
        return {
            "status": "retry",
            "reason": "company_card_draft_runner_error",
            "error_type": type(exc).__name__[:120],
        }
    try:
        raw = json.loads((cp.stdout or "").strip() or "{}")
    except Exception:
        return {"status": "retry", "reason": "company_card_draft_invalid_json"}
    status = str(raw.get("status") or "")[:80]
    reason = str(raw.get("reason") or "")[:120] or None
    return {
        "status": status or ("ok" if cp.returncode == 0 else "retry"),
        "reason": reason,
        "ready": status in {"draft_saved", "draft_exists"},
        "sent": status == "already_sent",
        "delivery_ambiguous": status == "delivery_ambiguous",
        "card_fingerprint": str(raw.get("card_fingerprint") or "")[:64] or None,
        "returncode": int(cp.returncode),
    }


def _parse_mail_time(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _company_card_send_state_evidence(
    mailbox_id: int,
    request_message_id: str | None = None,
    request_date: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id='__mcn_mailbox_watch__' AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"k": _MCN_COMPANY_CARD_SEND_STATE_KEY}).first()
    finally:
        db.close()
    if not row or not row[0]:
        return {"sent": False, "source": "send_state", "reason": "state_not_found"}
    try:
        payload = json.loads(str(row[0]))
    except Exception:
        return {"sent": False, "source": "send_state", "reason": "state_invalid"}
    if not isinstance(payload, dict):
        return {"sent": False, "source": "send_state", "reason": "state_invalid"}
    if str(payload.get("status") or "") != "accepted":
        return {
            "sent": False,
            "source": "send_state",
            "reason": f"state_{str(payload.get('status') or 'unknown')[:40]}",
        }
    if int(payload.get("mailbox_id") or 0) != int(mailbox_id):
        return {"sent": False, "source": "send_state", "reason": "mailbox_mismatch"}
    action_id = str(payload.get("action_id") or "")
    if not action_id.startswith("boris-mcn-company-card-v"):
        return {"sent": False, "source": "send_state", "reason": "action_mismatch"}

    request_mid = str(request_message_id or "").strip()
    state_reply = str(payload.get("in_reply_to") or "").strip()
    if request_mid and state_reply != request_mid:
        return {"sent": False, "source": "send_state", "reason": "thread_mismatch"}

    req_dt = _parse_mail_time(request_date)
    sent_dt = _parse_mail_time(str(payload.get("updated_at") or ""))
    if req_dt is not None and (sent_dt is None or sent_dt < req_dt):
        return {"sent": False, "source": "send_state", "reason": "state_before_request"}

    return {
        "sent": True,
        "source": "send_state",
        "reason": "smtp_accepted",
        "sent_at": sent_dt.isoformat() if sent_dt else None,
    }


def _company_card_manual_delivery_evidence(
    mailbox_id: int,
    request_message_id: str | None = None,
    request_date: str | None = None,
) -> dict:
    payload = mcn_company_card_manual_delivery_confirmation()
    if str(payload.get("status") or "") != "confirmed":
        return {"sent": False, "source": "manual_confirmation", "reason": "manual_confirmation_not_found"}
    if int(payload.get("mailbox_id") or 0) != int(mailbox_id):
        return {"sent": False, "source": "manual_confirmation", "reason": "mailbox_mismatch"}
    request_mid = str(request_message_id or "").strip()
    confirmed_mid = str(payload.get("request_message_id") or "").strip()
    if request_mid and confirmed_mid != request_mid:
        return {"sent": False, "source": "manual_confirmation", "reason": "thread_mismatch"}
    req_dt = _parse_mail_time(request_date)
    sent_dt = _parse_mail_time(str(payload.get("sent_at") or ""))
    if sent_dt is None:
        return {"sent": False, "source": "manual_confirmation", "reason": "sent_at_invalid"}
    if req_dt is not None and sent_dt < req_dt:
        return {"sent": False, "source": "manual_confirmation", "reason": "manual_send_before_request"}
    return {
        "sent": True,
        "source": "manual_confirmation",
        "reason": "owner_confirmed_manual_delivery",
        "sent_at": sent_dt.isoformat(),
    }


def _mcn_company_card_sent_evidence(
    mailbox_id: int,
    request_date: str | None = None,
    request_message_id: str | None = None,
) -> dict:
    durable = _company_card_send_state_evidence(
        mailbox_id,
        request_message_id=request_message_id,
        request_date=request_date,
    )
    if durable.get("sent"):
        return durable

    manual = _company_card_manual_delivery_evidence(
        mailbox_id,
        request_message_id=request_message_id,
        request_date=request_date,
    )
    if manual.get("sent"):
        return manual

    script = ROOT / "scripts" / "phone-company-card-mailbox-discovery.py"
    python_bin = ROOT / "venv" / "bin" / "python"
    try:
        cp = subprocess.run(
            [
                str(python_bin), str(script),
                "--mailbox-id", str(int(mailbox_id)),
                "--provider-domain", "mcn.ru",
                "--limit", "250",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"sent": False, "source": "sent_folder", "reason": "sent_scan_timeout"}
    except Exception as exc:
        return {
            "sent": False,
            "source": "sent_folder",
            "reason": "sent_scan_error",
            "error_type": type(exc).__name__[:120],
        }

    try:
        result = json.loads((cp.stdout or "").strip() or "{}")
    except Exception:
        return {"sent": False, "source": "sent_folder", "reason": "sent_scan_invalid_json"}
    if result.get("status") != "ok":
        return {
            "sent": False,
            "source": "sent_folder",
            "reason": str(result.get("reason") or "sent_scan_blocked")[:120],
        }

    req_dt = _parse_mail_time(request_date)
    request_mid = str(request_message_id or "").strip()
    trusted_newest: datetime | None = None
    content_newest: datetime | None = None
    manual_newest: datetime | None = None
    prior_content_newest: datetime | None = None
    for item in result.get("items") or []:
        if not isinstance(item, dict) or not item.get("company_card_signal"):
            continue
        item_dt = _parse_mail_time(str(item.get("date") or ""))
        content_ok = item.get("current_card_match") is True
        if (
            content_ok
            and item_dt is not None
            and req_dt is not None
            and item_dt < req_dt
            and (prior_content_newest is None or item_dt > prior_content_newest)
        ):
            # Discovery already filters Sent by recipient domain mcn.ru and
            # current_card_match compares every required field with today's
            # server-side card. This is safe evidence of prior disclosure of
            # the same document to the same provider.
            prior_content_newest = item_dt
        if req_dt is not None and (item_dt is None or item_dt < req_dt):
            continue
        action_ok = bool(item.get("boris_action_signal"))
        reply_ok = (not request_mid) or str(item.get("in_reply_to") or "").strip() == request_mid

        # MCN_SENT_EVIDENCE_THREAD_BOUND_V1:
        # Evidence for a specific provider request must be bound to that exact
        # Message-ID. A matching company-card attachment sent after the request
        # but in reply to another MCN message is only provider-level disclosure
        # evidence, not proof that this request was answered.
        if reply_ok and item_dt is not None and (
            manual_newest is None or item_dt > manual_newest
        ):
            manual_newest = item_dt
        if (
            content_ok
            and item_dt is not None
            and (content_newest is None or item_dt > content_newest)
        ):
            # Provider-level exact-current-card evidence is enough to suppress
            # duplicate sends. Mail.ru can strip In-Reply-To when a prepared
            # draft is sent, but discovery already proves: recipient is mcn.ru,
            # the DOCX matches all current required fields, and it was sent
            # after the provider request. Do not resend the same document just
            # because the transport lost the thread header.
            content_newest = item_dt
        if action_ok and reply_ok:
            if item_dt is not None and (trusted_newest is None or item_dt > trusted_newest):
                trusted_newest = item_dt
            elif req_dt is None and item_dt is None:
                return {
                    "sent": True,
                    "source": "sent_folder",
                    "reason": "boris_marked_attachment_seen",
                }
    if content_newest is not None:
        return {
            "sent": True,
            "source": "sent_folder",
            "reason": "current_card_content_verified_after_request",
            "sent_at": content_newest.isoformat(),
        }
    if trusted_newest is not None:
        return {
            "sent": True,
            "source": "sent_folder",
            "reason": "boris_marked_attachment_after_request",
            "sent_at": trusted_newest.isoformat(),
        }
    if manual_newest is not None:
        return {
            "sent": False,
            "source": "sent_folder",
            "reason": "unverified_manual_attachment_after_request",
            "sent_at": manual_newest.isoformat(),
            "delivery_ambiguous": True,
        }
    out = {
        "sent": False,
        "source": "sent_folder",
        "reason": "no_company_card_after_request",
    }
    if prior_content_newest is not None:
        out["prior_current_card_sent_to_provider"] = True
        out["prior_current_card_sent_at"] = prior_content_newest.isoformat()
    return out



def _mcn_repeat_card_auto_resend_enabled() -> bool:
    """Fail closed until the narrow repeat-card policy has passed production QA."""
    db = SessionLocal()
    try:
        raw = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id='__mcn_mailbox_watch__'
              AND key='mcn_repeat_card_auto_resend_v1'
            ORDER BY id DESC LIMIT 1
        """)).scalar()
    except Exception:
        return False
    finally:
        db.close()
    try:
        payload = json.loads(str(raw or "{}"))
    except Exception:
        return False
    return bool(isinstance(payload, dict) and payload.get("enabled") is True)


def _mcn_company_card_auto_resend_if_authorized(
    mailbox_id: int,
    operational_reply: dict | None,
    prior_evidence: dict | None,
    draft: dict | None,
) -> dict:
    """Resend only the same current card to the same verified provider on a fresh repeat request."""
    if not _mcn_repeat_card_auto_resend_enabled():
        return {"authorized": False, "reason": "repeat_card_autonomy_disabled"}
    op = operational_reply if isinstance(operational_reply, dict) else {}
    prior = prior_evidence if isinstance(prior_evidence, dict) else {}
    prepared = draft if isinstance(draft, dict) else {}
    if str(op.get("code") or "") != "mcn_company_card_required":
        return {"authorized": False, "reason": "not_company_card_request"}
    if str(op.get("sender_domain") or "").strip().lower() != "mcn.ru":
        return {"authorized": False, "reason": "provider_domain_not_exact"}
    request_mid = str(op.get("message_id") or "").strip()
    request_dt = _parse_mail_time(str(op.get("date") or ""))
    prior_dt = _parse_mail_time(str(prior.get("prior_current_card_sent_at") or ""))
    now = datetime.now(timezone.utc)
    if not request_mid or request_dt is None:
        return {"authorized": False, "reason": "fresh_request_evidence_missing"}
    if request_dt.tzinfo is None:
        request_dt = request_dt.replace(tzinfo=timezone.utc)
    if request_dt > now + timedelta(minutes=5) or now - request_dt > timedelta(days=14):
        return {"authorized": False, "reason": "repeat_request_not_fresh"}
    if not prior.get("prior_current_card_sent_to_provider") or prior_dt is None:
        return {"authorized": False, "reason": "prior_exact_delivery_missing"}
    if prior_dt.tzinfo is None:
        prior_dt = prior_dt.replace(tzinfo=timezone.utc)
    if prior_dt >= request_dt or request_dt - prior_dt > timedelta(days=180):
        return {"authorized": False, "reason": "prior_delivery_not_reusable"}
    if request_dt - prior_dt < timedelta(hours=6):
        return {
            "authorized": False,
            "reason": "repeat_request_too_soon",
            "prior_sent_at": prior_dt.isoformat(),
            "cooldown_hours": 6,
        }
    fingerprint = str(prepared.get("card_fingerprint") or "").strip().lower()
    if (
        not prepared.get("ready")
        or len(fingerprint) != 64
        or any(ch not in "0123456789abcdef" for ch in fingerprint)
    ):
        return {"authorized": False, "reason": "current_card_not_prepared"}

    script = ROOT / "scripts" / "phone-mcn-company-card-send.py"
    python_bin = ROOT / "venv" / "bin" / "python"
    try:
        cp = subprocess.run(
            [
                str(python_bin), str(script),
                "--mailbox-id", str(int(mailbox_id)),
                "--request-message-id", request_mid,
                "--request-date", str(op.get("date") or "").strip(),
                "--apply", "--confirm-share-banking",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        safe = {
            "authorized": True,
            "status": "delivery_ambiguous",
            "reason": "sender_timeout",
            "retry_blocked": True,
        }
    except Exception as exc:
        safe = {
            "authorized": True,
            "status": "send_runner_error",
            "reason": type(exc).__name__[:120],
            "retry_blocked": True,
        }
    else:
        try:
            raw = json.loads((cp.stdout or "").strip() or "{}")
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        safe = {
            "authorized": True,
            "status": str(raw.get("status") or "invalid_sender_result")[:80],
            "reason": str(raw.get("reason") or "")[:120] or None,
            "recipient_domain_verified": bool(raw.get("recipient_domain_verified")),
            "sent_copy_saved": bool(raw.get("sent_copy_saved")),
            "send_state_persisted": bool(raw.get("send_state_persisted")),
            "retry_blocked": bool(raw.get("retry_blocked")),
        }

    try:
        from app.services.telephony_core import _audit
        _audit(
            "__mcn_mailbox_watch__",
            "mcn.company_card.auto_resend",
            result=str(safe.get("status") or "unknown")[:80],
            actor_user_id=None,
            provider="mcn",
            metadata={
                "authorization_basis": "same_current_card_previously_delivered_to_mcn",
                "provider_reply_date": str(op.get("date") or "")[:160],
                "prior_sent_at": str(prior.get("prior_current_card_sent_at") or "")[:80],
                "card_fingerprint": fingerprint,
                "recipient_domain_verified": bool(safe.get("recipient_domain_verified")),
                "send_state_persisted": bool(safe.get("send_state_persisted")),
                "retry_blocked": bool(safe.get("retry_blocked")),
            },
        )
    except Exception:
        pass
    return safe


def _cleanup_mcn_company_card_draft_after_sent(mailbox_id: int) -> dict:
    """Remove BORIS's own prepared draft after Sent-folder proof appears.

    Manual send from the mail UI must converge to the same exactly-once state as
    API send: no stale duplicate draft should remain available for re-send.
    """
    script = ROOT / "scripts" / "phone-mcn-company-card-send.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "phone_mcn_company_card_send_guardian_cleanup",
            script,
        )
        if spec is None or spec.loader is None:
            return {"removed": False, "reason": "cleanup_module_unavailable", "count": 0}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.remove_current_company_card_draft(int(mailbox_id))
        return {
            "removed": bool((out or {}).get("removed")),
            "reason": str((out or {}).get("reason") or "")[:120] or None,
            "count": int((out or {}).get("count") or 0),
        }
    except Exception as exc:
        return {
            "removed": False,
            "reason": ("cleanup_error:" + type(exc).__name__)[:120],
            "count": 0,
        }


def _mcn_company_card_followup_enabled() -> bool:
    """Fail closed unless an explicit durable policy enables outbound follow-up."""
    db = SessionLocal()
    try:
        raw = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id='__mcn_mailbox_watch__' AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"k": _MCN_FOLLOWUP_POLICY_STORAGE_KEY}).scalar()
    except Exception:
        return False
    finally:
        db.close()
    try:
        payload = json.loads(str(raw or "{}"))
    except Exception:
        return False
    return bool(isinstance(payload, dict) and payload.get("enabled") is True)


def _mcn_company_card_followup(
    mailbox_id: int,
    operational_reply: dict | None,
    sent_evidence: dict | None,
    now: datetime | None = None,
) -> dict:
    """Bounded, exactly-once-ish MCN follow-up after a verified company-card send.

    This path never sends attachments, banking data, SIP secrets, commercial
    promises or paid calls. A durable pre-send claim blocks duplicate retries
    whenever delivery may be ambiguous.
    """
    op = operational_reply if isinstance(operational_reply, dict) else {}
    evidence = sent_evidence if isinstance(sent_evidence, dict) else {}
    if not bool(evidence.get("sent")):
        return {"status": "not_needed", "sent": False}
    if not _mcn_company_card_followup_enabled():
        return {
            "status": "disabled_by_policy",
            "sent": False,
            "attempts": 0,
            "auto_retry_blocked": True,
            "owner_action_required": False,
        }

    recipient = str(op.get("sender_email") or "").strip().lower()
    if "@" not in recipient:
        return {"status": "waiting_recipient_evidence", "sent": False}
    domain = recipient.rsplit("@", 1)[-1].strip(".")
    if domain != "mcn.ru" and not domain.endswith(".mcn.ru"):
        return {"status": "blocked_recipient_domain", "sent": False}

    request_message_id = str(op.get("message_id") or "").strip()[:500]
    if not request_message_id:
        return {"status": "waiting_thread_evidence", "sent": False}

    sent_at = _parse_mail_time(str(evidence.get("sent_at") or ""))
    if sent_at is None:
        return {"status": "waiting_sent_time_evidence", "sent": False}

    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_dt = now_dt.astimezone(timezone.utc)

    signature_material = json.dumps(
        {
            "mailbox_id": int(mailbox_id),
            "request_message_id": request_message_id,
            "recipient": recipient,
            "company_card_sent_at": sent_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hashlib.sha256(signature_material.encode("utf-8")).hexdigest()
    lock_key = f"mcn-company-card-followup|{signature}"
    state_account = "__mcn_mailbox_watch__"

    def _parse_dt(value) -> datetime | None:
        return _parse_mail_time(str(value or ""))

    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
        row = db.execute(text("""
            SELECT id,value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
        """), {"a": state_account, "k": _MCN_FOLLOWUP_STORAGE_KEY}).first()
        current = {}
        if row and row[1]:
            try:
                parsed = json.loads(str(row[1]))
                if isinstance(parsed, dict) and str(parsed.get("signature") or "") == signature:
                    current = parsed
            except Exception:
                current = {}

        status = str(current.get("status") or "")
        attempts = max(0, int(current.get("attempts") or 0))
        if status in {"sending", "ambiguous"}:
            db.commit()
            return {
                "status": "delivery_ambiguous",
                "sent": False,
                "attempts": attempts,
                "auto_retry_blocked": True,
            }
        if attempts >= _MCN_FOLLOWUP_MAX_ATTEMPTS:
            db.commit()
            return {
                "status": "max_attempts_reached",
                "sent": False,
                "attempts": attempts,
                "auto_retry_blocked": True,
            }

        if status == "retry":
            retry_at = _parse_dt(current.get("retry_at"))
            if retry_at is not None and now_dt < retry_at:
                db.commit()
                return {
                    "status": "retry_wait",
                    "sent": False,
                    "attempts": attempts,
                    "next_due_at": retry_at.isoformat(),
                }

        if status == "accepted":
            base = _parse_dt(current.get("last_sent_at")) or sent_at
            due_at = _parse_dt(current.get("next_due_at")) or (
                base + timedelta(seconds=_MCN_FOLLOWUP_REPEAT_DELAY_SECONDS)
            )
        else:
            due_at = sent_at + timedelta(seconds=_MCN_FOLLOWUP_FIRST_DELAY_SECONDS)

        if now_dt < due_at:
            db.commit()
            return {
                "status": "not_due",
                "sent": False,
                "attempts": attempts,
                "next_due_at": due_at.isoformat(),
            }

        claim_id = hashlib.sha256(
            f"{signature}|{now_dt.isoformat()}|{attempts + 1}".encode("utf-8")
        ).hexdigest()[:24]
        claim = {
            "signature": signature,
            "status": "sending",
            "attempts": attempts,
            "attempt_number": attempts + 1,
            "claim_id": claim_id,
            "request_message_id": request_message_id,
            "recipient_hash": hashlib.sha256(recipient.encode("utf-8")).hexdigest()[:16],
            "updated_at": now_dt.isoformat(),
        }
        raw = json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:i"), {
                "v": raw, "i": int(row[0]),
            })
        else:
            db.execute(text("""
                INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)
            """), {"a": state_account, "k": _MCN_FOLLOWUP_STORAGE_KEY, "v": raw})
        db.commit()
    except Exception:
        db.rollback()
        return {"status": "state_error", "sent": False}
    finally:
        db.close()

    subject = str(op.get("subject") or "Подключение телефонии MCN").strip()[:300]
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    body = (
        "Добрый день!\n\n"
        "Уточняем статус оформления по ранее отправленной карточке компании. "
        "Если договоры или SIP-параметры уже готовы, пожалуйста, пришлите их "
        "ответным письмом в этой переписке.\n\nСпасибо!"
    )
    headers = {
        "In-Reply-To": request_message_id,
        "References": request_message_id,
        "X-BORIS-Auto-Followup": f"mcn-company-card-v1-{attempts + 1}",
    }
    try:
        from app.services.client_mailboxes import send_outbound
        ok, reason, message_id = send_outbound(
            int(mailbox_id),
            recipient,
            subject,
            body,
            headers=headers,
            attachments=None,
        )
        reason = str(reason or "")[:160]
        delivery_unknown = (not ok) and reason.startswith("delivery_unknown:")
    except Exception as exc:
        ok = False
        delivery_unknown = True
        reason = ("delivery_unknown:" + type(exc).__name__)[:160]
        message_id = ""

    final_now = datetime.now(timezone.utc) if now is None else now_dt
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
        row = db.execute(text("""
            SELECT id,value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
        """), {"a": state_account, "k": _MCN_FOLLOWUP_STORAGE_KEY}).first()
        current = {}
        if row and row[1]:
            try:
                current = json.loads(str(row[1]))
            except Exception:
                current = {}
        if (
            not row
            or str(current.get("signature") or "") != signature
            or str(current.get("claim_id") or "") != claim_id
            or str(current.get("status") or "") != "sending"
        ):
            db.rollback()
            return {
                "status": "state_changed",
                "sent": bool(ok),
                "attempts": attempts,
            }

        current["updated_at"] = final_now.isoformat()
        current["transport_reason"] = reason
        current["message_id_hash"] = (
            hashlib.sha256(str(message_id or "").encode("utf-8")).hexdigest()[:16]
            if message_id else None
        )
        if ok:
            current["status"] = "accepted"
            current["attempts"] = attempts + 1
            current["last_sent_at"] = final_now.isoformat()
            current["next_due_at"] = (
                final_now + timedelta(seconds=_MCN_FOLLOWUP_REPEAT_DELAY_SECONDS)
            ).isoformat()
            current.pop("retry_at", None)
        elif delivery_unknown:
            current["status"] = "ambiguous"
            current["attempts"] = attempts
            current.pop("retry_at", None)
        else:
            current["status"] = "retry"
            current["attempts"] = attempts
            current["retry_at"] = (
                final_now + timedelta(seconds=_MCN_FOLLOWUP_SAFE_RETRY_SECONDS)
            ).isoformat()

        db.execute(
            text("UPDATE storage SET value=:v WHERE id=:i"),
            {
                "v": json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "i": int(row[0]),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        return {
            "status": "finalize_error",
            "sent": bool(ok),
            "attempts": attempts + (1 if ok else 0),
        }
    finally:
        db.close()

    if ok:
        return {
            "status": "sent",
            "sent": True,
            "attempts": attempts + 1,
            "next_due_at": current.get("next_due_at"),
        }
    if delivery_unknown:
        return {
            "status": "delivery_ambiguous",
            "sent": False,
            "attempts": attempts,
            "auto_retry_blocked": True,
        }
    return {
        "status": "retry",
        "sent": False,
        "attempts": attempts,
        "next_due_at": current.get("retry_at"),
    }


def _mcn_company_card_progress(mailbox_id: int, operational_reply: dict | None = None) -> dict:
    op = operational_reply if isinstance(operational_reply, dict) else {}
    evidence = _mcn_company_card_sent_evidence(
        mailbox_id,
        request_date=str(op.get("date") or "") or None,
        request_message_id=str(op.get("message_id") or "") or None,
    )
    evidence_safe = {
        "sent": bool(evidence.get("sent")),
        "source": str(evidence.get("source") or "")[:80] or None,
        "reason": str(evidence.get("reason") or "")[:120] or None,
        "sent_at": str(evidence.get("sent_at") or "")[:80] or None,
        "delivery_ambiguous": bool(evidence.get("delivery_ambiguous")),
        "prior_current_card_sent_to_provider": bool(
            evidence.get("prior_current_card_sent_to_provider")
        ),
        "prior_current_card_sent_at": (
            str(evidence.get("prior_current_card_sent_at") or "")[:80] or None
        ),
    }
    if evidence_safe["sent"]:
        draft_cleanup = _cleanup_mcn_company_card_draft_after_sent(int(mailbox_id))
        followup = _mcn_company_card_followup(int(mailbox_id), op, evidence_safe)
        return {
            "status": "waiting_mcn_response",
            "owner_action_required": False,
            "company_card_sent_evidence": evidence_safe,
            "company_card_draft_cleanup": draft_cleanup,
            "company_card_followup": followup,
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
                "text": "Карточка MCN уже отправлена. BORIS сам ждёт ответ с оформлением/SIP и продолжит подключение автоматически.",
            },
        }
    if evidence_safe["delivery_ambiguous"]:
        return {
            "status": "owner_action",
            "owner_action_required": True,
            "company_card_sent_evidence": evidence_safe,
            "primary_next_action": {
                "code": "mcn_company_card_delivery_verify",
                "actor": "owner",
                "owner_action_required": True,
                "text": "В отправленных найдено письмо с карточкой после запроса MCN, но оно не имеет BORIS-маркера. Повторная отправка заблокирована; нужно подтвердить, что карточку действительно отправили вручную.",
            },
        }

    draft = _prepare_mcn_company_card_draft(
        int(mailbox_id),
        request_message_id=str(op.get("message_id") or "") or None,
        request_date=str(op.get("date") or "") or None,
    )
    base = {
        "company_card_draft": draft,
        "company_card_sent_evidence": evidence_safe,
    }
    if draft.get("sent"):
        return {
            **base,
            "status": "waiting_mcn_response",
            "owner_action_required": False,
            "primary_next_action": {
                "code": "mcn_company_card_sent_waiting_reply",
                "actor": "boris",
                "owner_action_required": False,
                "text": "Карточка MCN уже отправлена. BORIS сам ждёт ответ с оформлением/SIP и продолжит подключение автоматически.",
            },
        }
    if draft.get("delivery_ambiguous"):
        return {
            **base,
            "status": "owner_action",
            "owner_action_required": True,
            "primary_next_action": {
                "code": "mcn_company_card_delivery_verify",
                "actor": "owner",
                "owner_action_required": True,
                "text": "Результат отправки карточки не подтверждён однозначно. Повторная отправка заблокирована; нужно проверить факт доставки, чтобы не отправить документ дважды.",
            },
        }
    if draft.get("ready"):
        auto_resend = _mcn_company_card_auto_resend_if_authorized(
            int(mailbox_id), op, evidence_safe, draft
        )
        if auto_resend.get("reason") == "repeat_request_too_soon":
            prior_sent_at = str(
                auto_resend.get("prior_sent_at")
                or evidence_safe.get("prior_current_card_sent_at")
                or ""
            )
            followup = _mcn_company_card_followup(
                int(mailbox_id),
                op,
                {"sent": True, "sent_at": prior_sent_at},
            )
            return {
                **base,
                "status": "waiting_mcn_response",
                "owner_action_required": False,
                "company_card_auto_resend": auto_resend,
                "company_card_followup": followup,
                "primary_next_action": {
                    "code": "mcn_company_card_recently_sent_waiting_reply",
                    "actor": "boris",
                    "owner_action_required": False,
                    "text": "Эта же актуальная карточка недавно уже отправлялась MCN. BORIS не создаёт дубль и сам ждёт ответ/контролирует follow-up.",
                },
            }
        if auto_resend.get("authorized"):
            base["company_card_auto_resend"] = auto_resend
            auto_status = str(auto_resend.get("status") or "")
            if auto_status in {"sent", "already_sent", "sent_state_pending"}:
                refreshed = _mcn_company_card_sent_evidence(
                    mailbox_id,
                    request_date=str(op.get("date") or "") or None,
                    request_message_id=str(op.get("message_id") or "") or None,
                )
                refreshed_safe = {
                    "sent": bool(refreshed.get("sent")),
                    "source": str(refreshed.get("source") or "")[:80] or None,
                    "reason": str(refreshed.get("reason") or "")[:120] or None,
                    "sent_at": str(refreshed.get("sent_at") or "")[:80] or None,
                    "delivery_ambiguous": bool(refreshed.get("delivery_ambiguous")),
                }
                if refreshed_safe["sent"]:
                    cleanup = _cleanup_mcn_company_card_draft_after_sent(int(mailbox_id))
                    followup = _mcn_company_card_followup(
                        int(mailbox_id), op, refreshed_safe
                    )
                    return {
                        **base,
                        "status": "waiting_mcn_response",
                        "owner_action_required": False,
                        "company_card_sent_evidence": refreshed_safe,
                        "company_card_draft_cleanup": cleanup,
                        "company_card_followup": followup,
                        "primary_next_action": {
                            "code": "mcn_company_card_sent_waiting_reply",
                            "actor": "boris",
                            "owner_action_required": False,
                            "text": "MCN повторно запросил ту же неизменившуюся карточку. BORIS безопасно отправил её один раз и сам ждёт следующий ответ.",
                        },
                    }
                return {
                    **base,
                    "status": "recovering",
                    "owner_action_required": False,
                    "primary_next_action": {
                        "code": "mcn_company_card_auto_resend_verifying",
                        "actor": "boris",
                        "owner_action_required": False,
                        "text": "BORIS отправил повторно ранее уже раскрытую MCN карточку и сам проверяет подтверждение доставки.",
                    },
                }
            if auto_status == "delivery_ambiguous":
                return {
                    **base,
                    "status": "owner_action",
                    "owner_action_required": True,
                    "primary_next_action": {
                        "code": "mcn_company_card_delivery_verify",
                        "actor": "owner",
                        "owner_action_required": True,
                        "text": "Повторная отправка той же карточки могла дойти до MCN, но подтверждение доставки неоднозначно. BORIS заблокировал повтор, чтобы не отправить документ дважды.",
                    },
                }
            return {
                **base,
                "status": "recovering",
                "owner_action_required": False,
                "primary_next_action": {
                    "code": "mcn_company_card_auto_resend_retry",
                    "actor": "boris",
                    "owner_action_required": False,
                    "text": "BORIS сам повторит безопасную отправку той же ранее раскрытой карточки после восстановления почтового контура.",
                },
            }
        return {
            **base,
            "status": "owner_action",
            "owner_action_required": True,
            "primary_next_action": {
                "code": "mcn_company_card_send_approval",
                "actor": "owner",
                "owner_action_required": True,
                "text": "Карточка владельца проверена, письмо с вложением уже подготовлено в черновиках. Требуется только разрешение отправить его MCN.",
            },
        }
    return {
        **base,
        "status": "recovering",
        "owner_action_required": False,
        "primary_next_action": {
            "code": "mcn_company_card_draft_self_heal",
            "actor": "boris",
            "owner_action_required": False,
            "text": "BORIS сам восстанавливает безопасную подготовку письма с карточкой MCN.",
        },
    }


def mcn_mailbox_autoonboard_once(force_refresh: bool=False) -> dict:
    """Discover MCN mail continuously; apply only to one unambiguous Phone account.\n\n    force_refresh is reserved for explicit owner/business state changes such as\n    paid Phone activation, where waiting for the normal discovery throttle would\n    create an unnecessary manual delay.\n    """
    configured_accounts = current_mcn_accounts()
    if configured_accounts:
        payload = {
            "status": "not_needed",
            "reason": "mcn_already_configured",
            "owner_action_required": False,
            "primary_next_action": {
                "code": "mcn_connected",
                "actor": "boris",
                "owner_action_required": False,
                "text": "MCN уже привязан к BORIS. Старое действие подключения закрыто.",
            },
            "configured_accounts": len(configured_accounts),
        }
        _persist_mailbox_autoonboard("__mcn_mailbox_watch__", payload)
        return payload

    target = _single_active_phone_account()
    target_ready = target.get("status") == "ok"
    account_id = str(target.get("account_id") or "") if target_ready else ""
    state_account = account_id or "__mcn_mailbox_watch__"

    recent = None if force_refresh else _mailbox_autoonboard_recent(state_account)
    if recent:
        out = {
            "status": "throttled",
            "account_id": account_id or None,
            "phone_account_status": target.get("status"),
            "last_status": recent.get("status"),
            "last_reason": recent.get("reason"),
            "owner_action_required": bool(recent.get("owner_action_required")),
        }
        if isinstance(recent.get("primary_next_action"), dict):
            out["primary_next_action"] = dict(recent["primary_next_action"])
        if isinstance(recent.get("operational_reply"), dict):
            out["operational_reply"] = dict(recent["operational_reply"])
        if isinstance(recent.get("company_card_draft"), dict):
            out["company_card_draft"] = dict(recent["company_card_draft"])
        action = out.get("primary_next_action") if isinstance(out.get("primary_next_action"), dict) else {}
        if (
            str(action.get("code") or "") in {
                "mcn_company_card_send_approval",
                "mcn_company_card_sent_waiting_reply",
                "mcn_company_card_delivery_verify",
                "mcn_company_card_auto_resend_verifying",
            }
            and recent.get("mailbox_id")
        ):
            progress = _mcn_company_card_progress(
                int(recent["mailbox_id"]),
                recent.get("operational_reply") if isinstance(recent.get("operational_reply"), dict) else None,
            )
            out.update(progress)
            enriched = dict(recent)
            enriched.update(progress)
            old_code = str(action.get("code") or "")
            new_action = progress.get("primary_next_action") if isinstance(progress.get("primary_next_action"), dict) else {}
            new_code = str(new_action.get("code") or "")
            state_changed = (
                new_code != old_code
                or str(progress.get("status") or "") != str(recent.get("status") or "")
                or bool(progress.get("owner_action_required")) != bool(recent.get("owner_action_required"))
            )
            # MCN_WAITING_REPLY_THROTTLE_NO_SLIDING_V1:
            # do not refresh checked_at while "waiting reply" is unchanged.
            # Otherwise the 5-minute mailbox throttle becomes a sliding window
            # and a guardian running every minute can postpone inbox scanning forever.
            if state_changed:
                _persist_mailbox_autoonboard(state_account, enriched)
        owner_alert = _mcn_owner_action_alert_once(out)
        if owner_alert.get("status") != "not_needed":
            out["owner_notification"] = owner_alert
        return out

    mailbox = _single_active_imap_mailbox()
    if mailbox.get("status") != "ok":
        payload = {
            **mailbox,
            "status": mailbox.get("status"),
            "phone_account_status": target.get("status"),
            "owner_action_required": mailbox.get("status") == "ambiguous_imap_mailboxes",
        }
        _persist_mailbox_autoonboard(state_account, payload)
        return {"account_id": account_id or None, **payload}

    script = ROOT / "scripts" / "phone-mcn-mailbox-onboard.py"
    python_bin = ROOT / "venv" / "bin" / "python"
    args = [
        str(python_bin), str(script),
        "--mailbox-id", str(mailbox["mailbox_id"]),
        "--limit", "40",
    ]
    if target_ready:
        args.extend(["--account-id", account_id, "--apply"])

    try:
        cp = subprocess.run(
            args,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        payload = {
            "status": "blocked",
            "reason": "mailbox_discovery_timeout",
            "mailbox_id": mailbox["mailbox_id"],
            "phone_account_status": target.get("status"),
            "owner_action_required": False,
        }
        _persist_mailbox_autoonboard(state_account, payload)
        return {"account_id": account_id or None, **payload}
    except Exception as exc:
        payload = {
            "status": "error",
            "reason": "mailbox_autoonboard_runner_error",
            "error_type": type(exc).__name__[:120],
            "phone_account_status": target.get("status"),
            "owner_action_required": False,
        }
        _persist_mailbox_autoonboard(state_account, payload)
        return {"account_id": account_id or None, **payload}

    try:
        result = json.loads((cp.stdout or "").strip() or "{}")
    except Exception:
        result = {
            "status": "error",
            "reason": "mailbox_autoonboard_invalid_json",
        }

    candidate_count = (
        result.get("unique_complete_candidates")
        if result.get("unique_complete_candidates") is not None
        else None
    )
    candidate_evidence = None
    raw_candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    if candidate_count == 1 and raw_candidates and isinstance(raw_candidates[0], dict):
        candidate = raw_candidates[0]
        fields = candidate.get("fields_found") if isinstance(candidate.get("fields_found"), dict) else {}
        candidate_evidence = {
            "uid": str(candidate.get("uid") or "")[:80] or None,
            "date": str(candidate.get("date") or "")[:160] or None,
            "sender_domain": str(candidate.get("sender_domain") or "")[:120] or None,
            "subject": str(candidate.get("subject") or "")[:300] or None,
            "auth_mode": str(candidate.get("auth_mode") or "")[:40] or None,
            "fields_found": {
                "registrar": bool(fields.get("registrar")),
                "username": bool(fields.get("username")),
                "password": bool(fields.get("password")),
                "did": bool(fields.get("did")),
                "source_ip": bool(fields.get("source_ip")),
            },
        }
    reason = str(result.get("reason") or "")[:160] or None
    operational = result.get("operational_reply") if isinstance(result.get("operational_reply"), dict) else {}
    operational_safe = {
        "code": str(operational.get("code") or "")[:120] or None,
        "date": str(operational.get("date") or "")[:160] or None,
        "message_id": str(operational.get("message_id") or "")[:500] or None,
        "subject": str(operational.get("subject") or "")[:300] or None,
        "sender_domain": str(operational.get("sender_domain") or "")[:120] or None,
        "sender_email": str(operational.get("sender_email") or "")[:320] or None,
        "requirements": [str(x)[:80] for x in (operational.get("requirements") or [])[:10]],
        "provider_path": [str(x)[:80] for x in (operational.get("provider_path") or [])[:10]],
    } if operational else None
    status = str(result.get("status") or ("ok" if cp.returncode == 0 else "blocked"))[:80]

    # Discovery without a target account is intentionally read-only. Translate
    # raw script outcomes into stable operational states without exposing mail.
    if not target_ready:
        if reason in {"mcn_contract_required_before_credentials", "mcn_company_card_required"}:
            status = "owner_action"
        elif reason == "no_complete_mcn_letter":
            status = "waiting_mcn_setup_letter"
        elif reason in {"no_active_phone_account", "ambiguous_active_phone_accounts"} and candidate_count == 1:
            status = "mcn_letter_ready_waiting_phone_account"
        elif reason == "ambiguous_complete_mcn_letters":
            status = "ambiguous_mcn_setup_letters"

    safe = {
        "status": status,
        "reason": reason,
        "mailbox_id": mailbox["mailbox_id"],
        "candidate_count": candidate_count,
        "candidate_evidence": candidate_evidence,
        "phone_account_status": target.get("status"),
        "discovery_only": not target_ready,
        "operational_reply": operational_safe,
        "owner_action_required": bool(
            reason in {
                "mcn_contract_required_before_credentials",
                "mcn_company_card_required",
                "ambiguous_complete_mcn_letters",
                "provider_conflict",
            }
            or (target.get("status") == "ambiguous_phone_accounts" and candidate_count == 1)
        ),
        "returncode": int(cp.returncode),
    }
    if (
        candidate_count == 1
        and target.get("status") in {"waiting_phone_account", "ambiguous_phone_accounts"}
    ):
        no_account = target.get("status") == "waiting_phone_account"
        safe["owner_action_required"] = True
        safe["primary_next_action"] = {
            "code": (
                "mcn_phone_entitlement_required"
                if no_account
                else "mcn_phone_account_binding_required"
            ),
            "actor": "owner",
            "owner_action_required": True,
            "text": (
                "MCN уже прислал готовые SIP-параметры, но в BORIS нет активного оплаченного Phone/Telephony-модуля. "
                "Нужно один раз активировать Phone для нужного аккаунта; после появления единственного оплаченного Phone-slot "
                "BORIS сам применит SIP/DID и продолжит проверки."
                if no_account
                else
                "MCN уже прислал готовые SIP-параметры, но найдено несколько активных Phone/Telephony-аккаунтов. "
                "Нужно один раз выбрать, к какому аккаунту относится линия; после выбора BORIS продолжит автоматически."
            ),
        }

    if reason == "mcn_company_card_required":
        card = _owner_company_card_readiness()
        safe["owner_company_card"] = card
        if card.get("complete"):
            safe.update(
                _mcn_company_card_progress(
                    int(mailbox["mailbox_id"]),
                    operational_safe if isinstance(operational_safe, dict) else None,
                )
            )
        else:
            safe["primary_next_action"] = {
                "code": "mcn_company_card_confirmation",
                "actor": "owner",
                "owner_action_required": True,
                "text": "Дополнить актуальные реквизиты владельца в BORIS и отправить карточку MCN. После этого MCN подготовит оформление и выдаст SIP-параметры.",
            }
    elif reason == "mcn_contract_required_before_credentials":
        safe["primary_next_action"] = {
            "code": "mcn_contract_documents",
            "actor": "owner",
            "owner_action_required": True,
            "text": "Оформить клиентский договор MCN и предоставить запрошенный пакет документов; после выдачи SIP BORIS продолжит автоматически.",
        }
    if target_ready and safe["status"] == "ok":
        safe["connected"] = True
    owner_alert = _mcn_owner_action_alert_once(safe)
    if owner_alert.get("status") != "not_needed":
        safe["owner_notification"] = owner_alert
    _persist_mailbox_autoonboard(state_account, safe)
    return {"account_id": account_id or None, **safe}


def run_guardian_once():
    """Run runtime self-heal and ownerless native-release continuation once."""
    ensure_schema()
    result = telephony_autonomy_guardian(100)
    mailbox_result = mcn_mailbox_autoonboard_once()
    result["mcn_mailbox_autoonboard"] = mailbox_result
    if bool(mailbox_result.get("owner_action_required")):
        action = mailbox_result.get("primary_next_action") if isinstance(mailbox_result.get("primary_next_action"), dict) else {}
        code = str(action.get("code") or mailbox_result.get("reason") or "mcn_mailbox_owner_action").strip()[:120]
        codes = list(result.get("owner_action_codes") or [])
        if code and code not in codes:
            codes.append(code)
        result["owner_action_codes"] = codes
        result["owner_action_required"] = True
    result["mcn_real_acceptance_watch"] = real_mcn_acceptance_watch_once()
    result["native_release_autopilot"] = native_release_autopilot_once()
    return result


if __name__ == '__main__':
    print(run_guardian_once(), flush=True)
