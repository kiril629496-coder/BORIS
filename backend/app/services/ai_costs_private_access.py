# -*- coding: utf-8 -*-
"""
BORIS internal AI economics access.

This is intentionally NOT based on role='owner'.
Only the actual BORIS platform owner may read internal costs.
"""

from fastapi import Depends, HTTPException
from app.api.auth import get_current_user

from app.services.platform_roles import is_private_platform_owner


def require_ai_costs_private_owner(
    user=Depends(get_current_user),
):
    if not is_private_platform_owner(user):
        raise HTTPException(
            status_code=403,
            detail="Раздел недоступен",
        )

    return user
