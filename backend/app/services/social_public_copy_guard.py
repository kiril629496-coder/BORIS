# -*- coding: utf-8 -*-
"""Compatibility shim to canonical Social owner policy v3."""
from app.services import social_owner_policy_v3 as _owner

OWNER_POLICY_COMPAT_SHIM = True

for _name in dir(_owner):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_owner, _name)

def __getattr__(name):
    return getattr(_owner, name)
