from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv("/root/BORIS/backend/.env", override=True)

from app.db.session import SessionLocal
from app.services.phone_media_gateway import delete_webrtc_endpoint
from app.services.asterisk_gateway import hangup_channel
from app.services.telephony_core import release_media_session

STATE_FILE = Path("/tmp/boris_mcn_media_guardian_state.json")
INVITE_FILE = Path("/tmp/boris_media_invite_qa.json")
QA_PREFIX = "__qa_mcn_"
QA_CHANNEL_PREFIX = "qa-mcn-"


def _safe_state(raw: dict[str, Any]) -> dict[str, Any]:
    account_id = str(raw.get("account_id") or "")
    channel_id = str(raw.get("channel_id") or "")
    endpoint_id = str(raw.get("endpoint_id") or "")
    if not account_id.startswith(QA_PREFIX):
        raise RuntimeError("refuse_non_qa_account")
    if channel_id and not channel_id.startswith(QA_CHANNEL_PREFIX):
        raise RuntimeError("refuse_non_qa_channel")
    for key in ("outer_channel_id","provider_channel_id"):
        value=str(raw.get(key) or "")
        if value and not value.startswith(QA_CHANNEL_PREFIX):
            raise RuntimeError(f"refuse_non_qa_{key}")
    if endpoint_id and not endpoint_id.startswith("bp_"):
        raise RuntimeError("refuse_non_qa_endpoint")
    return raw


def cleanup_mcn_media_qa(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Always-safe cleanup for synthetic MCN media QA only.

    Hard refuses any non-QA account/channel. This helper is intentionally
    unusable for real client data.
    """
    result: dict[str, Any] = {"status": "ok", "released": None, "endpoint": None, "rows_deleted": 0}
    if state is None and STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    state = _safe_state(dict(state or {
        "account_id": "__qa_mcn_media_guardian_v1",
        "channel_id": "qa-mcn-media-carrier-v1",
    }))
    account_id = str(state["account_id"])
    channel_id = str(state.get("channel_id") or "")
    endpoint_id = str(state.get("endpoint_id") or "")
    channel_ids=[]
    for key in ("channel_id","outer_channel_id","provider_channel_id"):
        value=str(state.get(key) or "")
        if value and value not in channel_ids:
            channel_ids.append(value)
    for channel in channel_ids:
        try:
            hangup_channel(channel)
        except Exception:
            pass
    if endpoint_id:
        try:
            result["endpoint"] = delete_webrtc_endpoint(endpoint_id)
        except Exception as exc:
            result["endpoint"] = {"status": "cleanup_pending", "error_type": type(exc).__name__}
    if state.get("lease_id") and state.get("device_id") and state.get("call_id"):
        try:
            result["released"] = release_media_session(
                account_id,
                str(state["device_id"]),
                str(state["call_id"]),
                int(state["lease_id"]),
                "qa_finally_cleanup",
            )
        except Exception as exc:
            result["released"] = {"status": "cleanup_pending", "error_type": type(exc).__name__}

    db = SessionLocal()
    try:
        statements = [
            "DELETE FROM telephony_push_outbox WHERE account_id=:a",
            "DELETE FROM telephony_call_carrier_links WHERE account_id=:a",
            "DELETE FROM telephony_asterisk_events WHERE account_id=:a OR channel_id=:ch",
            "DELETE FROM telephony_asterisk_recordings WHERE account_id=:a",
            "DELETE FROM telephony_media_sessions WHERE account_id=:a",
            "DELETE FROM telephony_commands WHERE account_id=:a",
            "DELETE FROM telephony_call_targets WHERE account_id=:a",
            "DELETE FROM telephony_events WHERE account_id=:a",
            "DELETE FROM telephony_recordings WHERE account_id=:a",
            "DELETE FROM telephony_calls WHERE account_id=:a",
            "DELETE FROM telephony_devices WHERE account_id=:a",
            "DELETE FROM telephony_dids WHERE account_id=:a",
            "DELETE FROM telephony_provider_configs WHERE account_id=:a",
            "DELETE FROM telephony_audit WHERE account_id=:a",
        ]
        for sql in statements:
            r = db.execute(text(sql), {"a": account_id, "ch": channel_id})
            try:
                result["rows_deleted"] += max(0, int(r.rowcount or 0))
            except Exception:
                pass
        db.commit()
    finally:
        db.close()

    for path in (STATE_FILE, INVITE_FILE):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    return result


if __name__ == "__main__":
    print("MCN_MEDIA_QA_CLEANUP", cleanup_mcn_media_qa())
