import os


def configured_owner(value=None):
    """Background-runner owner.

    The timer needs one explicit owner id so a background process never guesses
    which tenant it should scan.
    """
    raw=os.getenv('LEAD_RADAR_OWNER_USER_ID','') if value is None else value
    try:
        owner=int(str(raw).strip() or 0)
    except Exception:
        owner=0
    if owner<=0:
        raise RuntimeError('LEAD_RADAR_OWNER_NOT_CONFIGURED_FAIL_CLOSED')
    return owner


def assert_owner_scope(authenticated_user_id, configured_value=None):
    """Return the authenticated API tenant owner.

    API calls are protected by require_owner and scoped by the authenticated
    user's id. LEAD_RADAR_OWNER_USER_ID is only the background-runner default.
    If configured_value is explicitly supplied, strict equality is enforced.
    """
    try:
        uid=int(authenticated_user_id or 0)
    except Exception:
        uid=0
    if uid<=0:
        raise PermissionError('LEAD_RADAR_AUTHENTICATED_OWNER_REQUIRED')
    if configured_value is not None:
        owner=configured_owner(configured_value)
        if uid!=owner:
            raise PermissionError(
                f'LEAD_RADAR_OWNER_SCOPE_MISMATCH:authenticated={uid}:configured={owner}'
            )
    return uid
