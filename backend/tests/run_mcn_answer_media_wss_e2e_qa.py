from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env", override=True)

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.asterisk_gateway import active_channel_count
from tests.mcn_media_qa_cleanup import cleanup_mcn_media_qa

STATE = Path("/tmp/boris_mcn_answer_media_state.json")
INVITE = Path("/tmp/boris_media_invite_qa.json")
ACCOUNT = "__qa_mcn_answer_media_v1"
OUTER = "qa-mcn-answer-outer-v1"


def _real_mcn_trunks() -> int:
    db = SessionLocal()
    try:
        return int(db.execute(text("""
          SELECT count(*) FROM telephony_trunks
          WHERE provider='mcn'
            AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]')
        """)).scalar() or 0)
    finally:
        db.close()


def main() -> None:
    active = active_channel_count()
    assert active == 0, f"refuse_answer_media_qa_active_channels:{active}"
    real = _real_mcn_trunks()
    assert real == 0, f"refuse_answer_media_qa_real_mcn_trunks:{real}"

    fallback = {"account_id": ACCOUNT, "outer_channel_id": OUTER}
    try:
        cleanup_mcn_media_qa(fallback)
    except Exception:
        pass
    STATE.unlink(missing_ok=True)
    INVITE.unlink(missing_ok=True)

    try:
        prep = subprocess.run(
            [sys.executable, "/root/BORIS/backend/tests/prepare_mcn_answer_media_e2e.py"],
            cwd="/root/BORIS/backend", check=False, capture_output=True, text=True, timeout=45,
        )
        print(prep.stdout, end="")
        if prep.stderr:
            print(prep.stderr, end="", file=sys.stderr)
        assert prep.returncode == 0, f"answer_media_prepare_failed:{prep.returncode}"
        assert STATE.exists() and INVITE.exists(), "answer_media_prepare_state_missing"

        probe = subprocess.run(
            [sys.executable, "/root/BORIS/backend/tests/run_mcn_wss_registration_media_runtime_qa.py", str(STATE)],
            cwd="/root/BORIS/backend", check=False, capture_output=True, text=True, timeout=60,
        )
        print(probe.stdout, end="")
        if probe.stderr:
            print(probe.stderr, end="", file=sys.stderr)
        assert probe.returncode == 0, f"answer_media_wss_probe_failed:{probe.returncode}"
        print("MCN_ANSWER_MEDIA_WSS_E2E_OK")
    finally:
        state = fallback
        if STATE.exists():
            try:
                state = json.loads(STATE.read_text())
            except Exception:
                state = fallback
        try:
            cleanup = cleanup_mcn_media_qa(state)
            print("MCN_ANSWER_MEDIA_E2E_FINALLY", {
                "status": cleanup.get("status"),
                "rows_deleted": cleanup.get("rows_deleted"),
            })
        finally:
            STATE.unlink(missing_ok=True)
            INVITE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
