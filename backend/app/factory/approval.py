from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text


class ApprovalError(RuntimeError):
    pass


ALLOWED_FIELDS = {
    "schedule.mop_poll",
    "schedule.autopilot",
}
ALLOWED_OPERATION = "migrate.schedule"


@dataclass
class ApprovalResult:
    confirmation_id: int
    account_id: str
    field: str
    operation: str
    status: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _payload_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def _validate_row(row: dict) -> None:
    field = row.get("field")
    operation = row.get("operation")

    if field not in ALLOWED_FIELDS:
        raise ApprovalError("unsupported_confirmation_field")

    if operation != ALLOWED_OPERATION:
        raise ApprovalError("unsupported_confirmation_operation")

    if row.get("status") != "pending":
        raise ApprovalError("confirmation_not_pending")

    payload = _payload_dict(row.get("payload"))

    decision = _payload_dict(payload.get("decision"))

    if decision:
        decision_field = decision.get("field")
        decision_operation = decision.get("operation")

        if decision_field and decision_field not in ALLOWED_FIELDS:
            raise ApprovalError("payload_field_mismatch")

        if decision_operation and decision_operation != ALLOWED_OPERATION:
            raise ApprovalError("payload_operation_mismatch")

        policy = decision.get("policy")
        if policy and policy != "CONFIRM_REQUIRED":
            raise ApprovalError("unsupported_confirmation_policy")


def approve_one(
    db,
    confirmation_id: int,
    owner_user_id: int,
) -> ApprovalResult:
    """
    Approve exactly one pending factory confirmation.

    Contract is intentionally based on the REAL DB schema:
      field + operation + fingerprint + payload + status

    No legacy `kind` column/field is used.
    """

    row = db.execute(
        text("""
            SELECT
                id,
                account_id,
                field,
                operation,
                fingerprint,
                status,
                payload,
                created_at,
                updated_at,
                decided_at,
                decided_by
            FROM factory_confirmations
            WHERE id = :id
            FOR UPDATE
        """),
        {"id": confirmation_id},
    ).mappings().first()

    if row is None:
        raise ApprovalError("confirmation_not_found")

    row = dict(row)

    _validate_row(row)

    now = _utcnow()

    updated = db.execute(
        text("""
            UPDATE factory_confirmations
            SET
                status = 'approved',
                updated_at = :now,
                decided_at = :now,
                decided_by = :actor
            WHERE id = :id
              AND status = 'pending'
        """),
        {
            "id": confirmation_id,
            "now": now,
            "actor": str(owner_user_id),
        },
    )

    if updated.rowcount != 1:
        raise ApprovalError("confirmation_approval_race")

    return ApprovalResult(
        confirmation_id=int(row["id"]),
        account_id=str(row["account_id"]),
        field=str(row["field"]),
        operation=str(row["operation"]),
        status="approved",
    )
