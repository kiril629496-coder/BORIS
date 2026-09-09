from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from sqlalchemy import text


KEY_FILE = Path(
    "/root/BORIS/.secrets/crm_vault.key"
)


def _fernet() -> Fernet:

    if not KEY_FILE.exists():
        raise RuntimeError(
            "CRM_VAULT_KEY_MISSING"
        )

    key = KEY_FILE.read_text().strip()

    return Fernet(
        key.encode()
    )


def encrypt_json(value: dict) -> str:

    raw = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()

    return (
        _fernet()
        .encrypt(raw)
        .decode()
    )


def decrypt_json(value: str) -> dict:

    raw = (
        _fernet()
        .decrypt(value.encode())
    )

    return json.loads(
        raw.decode()
    )


def load_secret(
    db,
    owner_user_id: int,
    provider: str,
) -> dict:

    row = db.execute(
        text("""
            SELECT encrypted_json

            FROM boris_crm_connector_secrets

            WHERE owner_user_id=:owner
              AND provider=:provider
        """),
        {
            "owner": owner_user_id,
            "provider": provider,
        },
    ).first()

    if not row:
        return {}

    return decrypt_json(
        row[0]
    )


def save_secret(
    db,
    owner_user_id: int,
    provider: str,
    payload: dict,
):

    encrypted = encrypt_json(
        payload
    )

    db.execute(
        text("""
            INSERT INTO boris_crm_connector_secrets (
                owner_user_id,
                provider,
                encrypted_json
            )

            VALUES (
                :owner,
                :provider,
                :payload
            )

            ON CONFLICT (
                owner_user_id,
                provider
            )

            DO UPDATE SET
                encrypted_json=EXCLUDED.encrypted_json,
                updated_at=NOW()
        """),
        {
            "owner": owner_user_id,
            "provider": provider,
            "payload": encrypted,
        },
    )


# BORIS_CRM_VAULT_STRING_API_V2

def encrypt_secret(plain: str) -> str:
    """
    BORIS CRM vault string encryption.

    Uses the SAME dedicated CRM vault key as encrypt_json().
    Empty strings are preserved.
    """
    if not plain:
        return plain

    return (
        _fernet()
        .encrypt(
            str(plain).encode("utf-8")
        )
        .decode("utf-8")
    )


def decrypt_secret(value: str) -> str:
    """
    BORIS CRM vault string decryption.

    Primary format:
      CRM vault Fernet.

    Legacy/plaintext fallback is intentionally fail-compatible:
    older connection rows must not make the entire backend unavailable.
    """
    if not value:
        return value

    try:
        return (
            _fernet()
            .decrypt(
                str(value).encode("utf-8")
            )
            .decode("utf-8")
        )

    except (InvalidToken, ValueError):
        # A pre-migration/plaintext value may exist.
        # Return unchanged instead of killing application startup.
        return value
