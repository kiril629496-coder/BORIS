# -*- coding: utf-8 -*-
"""Security audit helpers for BORIS Messenger.

Uses the existing append-only action_log sink. Records metadata only and never
persists authentication credentials, invitation secrets or message/file content.
"""
from __future__ import annotations

from app.services.action_log import ACTOR_USER, log_action


def audit_security_event(
    *,
    user_id: int | None,
    account_id: str | None,
    action: str,
    object_kind: str,
    object_name: str = "",
    before: str = "",
    after: str = "",
    reason: str = "",
    request_id: str | None = None,
) -> None:
    safe_account = str(account_id or (f"user:{int(user_id)}" if user_id else "security"))
    log_action(
        safe_account,
        action=action,
        object_name=str(object_name or "")[:300],
        object_kind=str(object_kind or "security")[:32],
        before_val=str(before or "")[:500],
        after_val=str(after or "")[:500],
        reason=str(reason or "")[:500],
        actor=ACTOR_USER,
        user_id=(int(user_id) if user_id is not None else None),
        source="messenger_security",
        request_id=request_id,
    )
