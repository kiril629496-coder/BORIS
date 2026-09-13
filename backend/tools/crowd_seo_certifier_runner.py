#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CERTIFIER = ROOT / "scripts" / "crowd_seo_production_certifier.py"
STATUS = ROOT / "data" / "service_marketplaces" / "crowd_seo_certificate_latest.json"
TMP = STATUS.with_suffix(".json.tmp")


def main() -> int:
    proc = subprocess.run(
        [str(ROOT / "venv" / "bin" / "python"), str(CERTIFIER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
    )
    raw = (proc.stdout or "").strip()
    marker = "\nBORIS_CROWD_SEO_PRODUCTION_CERTIFIER_V1="
    payload_text = raw.rsplit(marker, 1)[0].strip() if marker in raw else raw
    payload = {}
    try:
        payload = json.loads(payload_text) if payload_text else {}
    except Exception:
        payload = {
            "certificate": "BORIS_CROWD_SEO_PRODUCTION_CERTIFIER_V1",
            "state": "FAIL",
            "infrastructure_ok": False,
            "delivery_ready": False,
            "parse_error": True,
            "stdout_tail": raw[-2000:],
        }

    state = str(payload.get("state") or "FAIL")
    record = {
        **payload,
        "runner_at": datetime.now(timezone.utc).isoformat(),
        "certifier_returncode": proc.returncode,
        "stderr_tail": (proc.stderr or "")[-2000:],
        "timer_policy": {
            "PASS": "healthy",
            "EXTERNAL_ONBOARDING_PENDING": "healthy_but_waiting_external_checkpoint",
            "FAIL": "service_failure",
        },
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    TMP.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    TMP.replace(STATUS)
    print(json.dumps({
        "state": state,
        "infrastructure_ok": bool(payload.get("infrastructure_ok")),
        "delivery_ready": bool(payload.get("delivery_ready")),
        "status_file": str(STATUS),
    }, ensure_ascii=False))
    return 1 if state == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
