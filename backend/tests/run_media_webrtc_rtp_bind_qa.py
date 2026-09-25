from __future__ import annotations

import os
import subprocess
import sys

ROOT = "/root/BORIS/backend"
PYTHON = os.path.join(ROOT, "venv/bin/python")


def _run(script: str, timeout: int = 90) -> None:
    cp = subprocess.run(
        [PYTHON, os.path.join(ROOT, "tests", script)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ROOT},
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if cp.stdout:
        print(cp.stdout, end="")
    if cp.stderr:
        print(cp.stderr, end="", file=sys.stderr)
    if cp.returncode != 0:
        raise SystemExit(f"{script}:failed:{cp.returncode}")


def main() -> None:
    """Truthful synthetic WebRTC signaling/bridge QA.

    The old probe hard-coded a browser endpoint that no longer exists and then
    labelled a successful bridge as RTP proof. This replacement provisions a
    fresh short-lived endpoint, performs real WSS SIP registration, receives
    and answers the Asterisk INVITE, verifies ACK + activated media bridge, and
    cleans all synthetic state. It deliberately does NOT claim that real audio
    moved: production RTP evidence must come from device/browser transport
    counters through /api/telephony/media/session/proof.
    """
    _run("prepare_mcn_media_guardian_qa.py")
    _run("run_mcn_wss_registration_media_runtime_qa.py")
    print("MEDIA_WEBRTC_SIGNALING_BRIDGE_QA=PASS")
    print("RTP_AUDIO_PROOF=NOT_PROVEN_BY_SYNTHETIC_SIGNALING_QA")


if __name__ == "__main__":
    main()
