"""Local client account alias resolver.

Canonical source never contains production client identifiers.
Production values live in ignored client_aliases.local.json (0600) or a path
specified by BORIS_CLIENT_ALIASES_FILE. Clean clones get deterministic
synthetic values so unit tests can exercise alias-dependent logic safely.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_LOCAL_DEFAULT = Path(__file__).resolve().with_name("client_aliases.local.json")
_EXAMPLE = Path(__file__).resolve().with_name("client_aliases.local.json.example")


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    source = Path(os.environ.get("BORIS_CLIENT_ALIASES_FILE", str(_LOCAL_DEFAULT)))
    if source.exists():
        data = json.loads(source.read_text(encoding="utf-8"))
    else:
        data = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("BORIS client alias map must be a JSON object")
    out: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise RuntimeError("BORIS client alias map contains invalid entry")
        out[key] = value.strip()
    return out


def client_id(alias: str) -> str:
    try:
        return _aliases()[alias]
    except KeyError as exc:
        raise RuntimeError(f"BORIS client alias is not configured: {alias}") from exc


def client_text(template: str) -> str:
    """Resolve canonical client-alias placeholders inside runtime strings."""
    out = template
    for alias, value in _aliases().items():
        out = out.replace(f"__BORIS_CLIENT_ALIAS_{alias}__", value)
    return out
