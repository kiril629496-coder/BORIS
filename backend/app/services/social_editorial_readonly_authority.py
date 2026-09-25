from __future__ import annotations

import json
from pathlib import Path

DEFAULT_AUTHORITY = Path(
    "/root/BORIS/backend/client-data/u2/social-editorial/authority/editorial_client_v3.json"
)
EXPECTED_SCHEMA = 1
EXPECTED_VERSION = "social_editorial_v3_20260905"
EXPECTED_ACCOUNT = "u2"
OCEAN_PROJECT_ID = "a374ced25745"


class SocialEditorialAuthorityError(RuntimeError):
    pass


def load_client_editorial_authority(path: Path | None = None) -> dict:
    target = path or DEFAULT_AUTHORITY
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SocialEditorialAuthorityError(
            "client_editorial_authority_unreadable:" + type(exc).__name__
        ) from exc

    if data.get("schema") != EXPECTED_SCHEMA:
        raise SocialEditorialAuthorityError("client_editorial_authority_schema_invalid")
    if data.get("version") != EXPECTED_VERSION:
        raise SocialEditorialAuthorityError("client_editorial_authority_version_invalid")
    if data.get("account_id") != EXPECTED_ACCOUNT:
        raise SocialEditorialAuthorityError("client_editorial_authority_account_invalid")
    projects = data.get("projects")
    if not isinstance(projects, dict):
        raise SocialEditorialAuthorityError("client_editorial_authority_projects_invalid")
    return data


def ocean_footer_signature(path: Path | None = None) -> str:
    authority = load_client_editorial_authority(path)
    project = (authority.get("projects") or {}).get(OCEAN_PROJECT_ID)
    if not isinstance(project, dict):
        raise SocialEditorialAuthorityError("client_editorial_ocean_project_missing")
    value = str(project.get("footer_signature") or "").strip()
    if not value:
        raise SocialEditorialAuthorityError("client_editorial_ocean_footer_missing")
    return value
