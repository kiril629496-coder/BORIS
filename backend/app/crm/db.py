"""
BORIS CRM DB adapter.

Why this exists:
The main BORIS database configuration currently has two competing
DATABASE_URL sources. systemd launches the real backend with the correct
host-side URL, while a fresh import can later overwrite os.environ through
application configuration.

CRM deliberately reads DATABASE_URL from the process INITIAL environment
(/proc/self/environ) before falling back to os.environ.

This isolates CRM without changing the existing BORIS DB configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _initial_environment_value(name: str) -> str | None:
    try:
        raw = Path("/proc/self/environ").read_bytes()
    except Exception:
        return None

    prefix = name.encode() + b"="

    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            try:
                return item[len(prefix):].decode()
            except UnicodeDecodeError:
                return None

    return None


DATABASE_URL = (
    os.environ.get("CRM_DATABASE_URL")
    or os.environ.get("BORIS_CRM_DATABASE_URL")
    or _initial_environment_value("DATABASE_URL")
    or os.environ.get("DATABASE_URL")
)

if not DATABASE_URL:
    raise RuntimeError("CRM_DATABASE_URL_MISSING")


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)
