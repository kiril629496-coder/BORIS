#!/root/BORIS/backend/venv/bin/python
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/root/BORIS/backend")

from app.db.session import SessionLocal
from app.models.control_plane import ControlIncident
from app.services.reliability import heartbeat
from app.services.social_workstream_boundary import social_workstream_boundary_health

INCIDENT_TYPE = "SOCIAL_WORKSTREAM_BOUNDARY_DRIFT"
ACCOUNT_ID = "shared_social_scope"


def sync_incident(health: dict) -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        row = (
            db.query(ControlIncident)
            .filter(
                ControlIncident.incident_type == INCIDENT_TYPE,
                ControlIncident.module == "social",
                ControlIncident.account_id == ACCOUNT_ID,
                ControlIncident.state == "open",
            )
            .first()
        )
        if not health.get("ok"):
            if row is None:
                row = ControlIncident(
                    id=str(uuid.uuid4()),
                    incident_type=INCIDENT_TYPE,
                    priority="P1",
                    state="open",
                    module="social",
                    account_id=ACCOUNT_ID,
                    symptom="Соседний чат пересёк границу общего Social runtime.",
                    root_cause="neighbor_chat_owner_or_mode_drift",
                    remediation=(
                        "Исправить workstream registry только штатным coordination promoter; "
                        "shared Social runtime остаётся у social_reliability_core."
                    ),
                    prevention=(
                        "single-writer registry + Social boundary guard + periodic timer + "
                        "legacy autopost read-only alias"
                    ),
                    details_json=health,
                    opened_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.details_json = health
                row.updated_at = now
        elif row is not None:
            row.state = "resolved"
            row.resolved_at = now
            row.updated_at = now
            details = dict(row.details_json or {})
            details.update({
                "resolved_by": "social_workstream_boundary_guard",
                "resolved_at": now.isoformat(),
            })
            row.details_json = details
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    health = social_workstream_boundary_health()
    health["checked_at"] = datetime.now(timezone.utc).isoformat()
    heartbeat(
        "social",
        "workstream_boundary_guard",
        state="ok" if health.get("ok") else "degraded",
        account_id=ACCOUNT_ID,
        details=health,
        min_interval_seconds=0,
    )
    try:
        sync_incident(health)
        health["incident_sync"] = "ok"
    except Exception as exc:
        health["incident_sync"] = type(exc).__name__
        health["incident_sync_error"] = str(exc)[:240]
    health["owner_action_required"] = False if health.get("ok") else True
    print(json.dumps(health, ensure_ascii=False, sort_keys=True))
    return 0 if health.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
