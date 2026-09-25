# -*- coding: utf-8 -*-
"""Deprecated facade. Social authority is /usr/local/sbin/boris-social-owner-preflight.py."""
import json
import os
import subprocess
from pathlib import Path

def _root(root=None):
    return str(Path(root or Path(__file__).resolve().parents[2]).resolve())

def _run(root=None):
    p = subprocess.run(
        ["/usr/local/sbin/boris-social-owner-preflight.py", _root(root), "--runtime"],
        capture_output=True, text=True, timeout=20,
    )
    try:
        data = json.loads((p.stdout or "{}").strip().splitlines()[-1])
    except Exception:
        data = {"ok": False, "source": {"ok": False, "reason": "external_preflight_invalid_output"}}
    return data

def ensure_owner_policy_source(root=None):
    return (_run(root).get("source") or {})

def ensure_social_consumers(root=None):
    return (_run(root).get("consumers") or {})

def ensure_legacy_shims(root=None):
    return (_run(root).get("shims") or {})

def ensure_preflight_module(root=None):
    return {"ok": True, "self_healed": False, "authority": "external_root_of_trust"}

def run(root=None):
    return _run(root)
