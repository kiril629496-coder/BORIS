"""
BORIS CRM — Stage E.2 Connection Control.

Responsibilities:

- OAuth state generation
- OAuth callback validation
- connection status
- encrypted credential storage through vault
- provider token exchange
- token refresh
- provider identity discovery
- pipeline discovery
- safe remote read operations

IMPORTANT:

This module does NOT create/update external CRM entities.

External writes belong to a later explicit write-gate stage.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import text

from .db import SessionLocal
from .vault import encrypt_secret, decrypt_secret
from .provider_config import (
    PROVIDERS,
    provider_enabled,
    client_id,
    client_secret,
    redirect_uri,
)


ALLOWED_PROVIDERS = {"amocrm", "bitrix24"}

STATE_TTL_SECONDS = 600


def _provider_ok(provider: str) -> str:

    provider = provider.lower().strip()

    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")

    return provider


def _now() -> int:
    return int(time.time())


def _hash_state(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def create_oauth_state(
    owner_user_id: int,
    provider: str,
) -> str:

    provider = _provider_ok(provider)

    raw = secrets.token_urlsafe(48)

    digest = _hash_state(raw)

    db = SessionLocal()

    try:

        db.execute(
            text(
                """
                INSERT INTO crm_sync_state
                (
                    owner_user_id,
                    provider,
                    state_hash,
                    state_created_at,
                    state_expires_at,
                    consumed
                )
                VALUES
                (
                    :owner,
                    :provider,
                    :state_hash,
                    :created,
                    :expires,
                    FALSE
                )
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
                "state_hash": digest,
                "created": _now(),
                "expires": _now() + STATE_TTL_SECONDS,
            },
        )

        db.commit()

    finally:
        db.close()

    return raw


def consume_oauth_state(
    owner_user_id: int,
    provider: str,
    raw_state: str,
) -> bool:

    provider = _provider_ok(provider)

    digest = _hash_state(raw_state)

    db = SessionLocal()

    try:

        row = db.execute(
            text(
                """
                SELECT id
                FROM crm_sync_state
                WHERE owner_user_id = :owner
                  AND provider = :provider
                  AND state_hash = :state_hash
                  AND consumed = FALSE
                  AND state_expires_at >= :now
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
                "state_hash": digest,
                "now": _now(),
            },
        ).first()

        if not row:
            return False

        db.execute(
            text(
                """
                UPDATE crm_sync_state
                SET consumed = TRUE
                WHERE id = :id
                """
            ),
            {"id": row[0]},
        )

        db.commit()

        return True

    finally:
        db.close()


def authorization_url(
    owner_user_id: int,
    provider: str,
) -> str:

    provider = _provider_ok(provider)

    if not provider_enabled(provider):
        raise RuntimeError(
            f"{provider.upper()}_OAUTH_NOT_CONFIGURED"
        )

    state = create_oauth_state(
        owner_user_id,
        provider,
    )

    cid = client_id(provider)
    redirect = redirect_uri(provider)

    if provider == "amocrm":

        params = {
            "client_id": cid,
            "redirect_uri": redirect,
            "response_type": "code",
            "state": state,
        }

        return (
            "https://www.amocrm.ru/oauth"
            "?"
            + urlencode(params)
        )

    if provider == "bitrix24":

        params = {
            "client_id": cid,
            "state": state,
        }

        return (
            "https://oauth.bitrix.info/oauth/authorize/"
            "?"
            + urlencode(params)
        )

    raise ValueError("unsupported provider")


def _request_json(
    method: str,
    url: str,
    **kwargs: Any,
) -> dict:

    timeout = kwargs.pop(
        "timeout",
        15.0,
    )

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
    ) as client:

        response = client.request(
            method,
            url,
            **kwargs,
        )

    if response.status_code >= 400:

        raise RuntimeError(
            f"provider_http_{response.status_code}"
        )

    data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError(
            "provider_invalid_json"
        )

    return data


def exchange_code(
    provider: str,
    code: str,
) -> dict:

    provider = _provider_ok(provider)

    if not provider_enabled(provider):
        raise RuntimeError(
            f"{provider.upper()}_OAUTH_NOT_CONFIGURED"
        )

    cid = client_id(provider)
    secret = client_secret(provider)
    redirect = redirect_uri(provider)

    if provider == "amocrm":

        return _request_json(
            "POST",
            "https://www.amocrm.ru/oauth2/access_token",
            json={
                "client_id": cid,
                "client_secret": secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect,
            },
        )

    if provider == "bitrix24":

        return _request_json(
            "POST",
            "https://oauth.bitrix.info/oauth/token/",
            params={
                "grant_type": "authorization_code",
                "client_id": cid,
                "client_secret": secret,
                "code": code,
            },
        )

    raise ValueError("unsupported provider")


def refresh_token(
    provider: str,
    refresh: str,
) -> dict:

    provider = _provider_ok(provider)

    cid = client_id(provider)
    secret = client_secret(provider)

    if not cid or not secret:
        raise RuntimeError(
            f"{provider.upper()}_OAUTH_NOT_CONFIGURED"
        )

    if provider == "amocrm":

        return _request_json(
            "POST",
            "https://www.amocrm.ru/oauth2/access_token",
            json={
                "client_id": cid,
                "client_secret": secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "redirect_uri": redirect_uri(provider),
            },
        )

    if provider == "bitrix24":

        return _request_json(
            "POST",
            "https://oauth.bitrix.info/oauth/token/",
            params={
                "grant_type": "refresh_token",
                "client_id": cid,
                "client_secret": secret,
                "refresh_token": refresh,
            },
        )

    raise ValueError("unsupported provider")


def _save_connection(
    owner_user_id: int,
    provider: str,
    token_data: dict,
    identity: dict,
) -> None:

    provider = _provider_ok(provider)

    access = token_data.get("access_token")
    refresh = token_data.get("refresh_token")

    if not access:
        raise RuntimeError(
            "provider_access_token_missing"
        )

    encrypted_access = encrypt_secret(access)

    encrypted_refresh = (
        encrypt_secret(refresh)
        if refresh
        else None
    )

    expires_in = int(
        token_data.get(
            "expires_in",
            3600,
        )
    )

    db = SessionLocal()

    try:

        db.execute(
            text(
                """
                DELETE FROM crm_external_connections
                WHERE owner_user_id = :owner
                  AND provider = :provider
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
            },
        )

        db.execute(
            text(
                """
                INSERT INTO crm_external_connections
                (
                    owner_user_id,
                    provider,
                    status,
                    external_account_id,
                    external_domain,
                    encrypted_access_token,
                    encrypted_refresh_token,
                    access_expires_at,
                    scope,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    :owner,
                    :provider,
                    'active',
                    :external_account_id,
                    :external_domain,
                    :access,
                    :refresh,
                    :expires,
                    :scope,
                    :metadata,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
                "external_account_id": identity.get(
                    "account_id"
                ),
                "external_domain": identity.get(
                    "domain"
                ),
                "access": encrypted_access,
                "refresh": encrypted_refresh,
                "expires": _now() + expires_in,
                "scope": identity.get(
                    "scope"
                ),
                "metadata": identity,
            },
        )

        db.commit()

    finally:
        db.close()


def save_connection(
    owner_user_id: int,
    provider: str,
    token_data: dict,
    identity: dict,
) -> None:

    _save_connection(
        owner_user_id,
        provider,
        token_data,
        identity,
    )


def connection_status(
    owner_user_id: int,
    provider: str,
) -> dict:

    provider = _provider_ok(provider)

    db = SessionLocal()

    try:

        row = db.execute(
            text(
                """
                SELECT
                    status,
                    external_account_id,
                    external_domain,
                    access_expires_at,
                    scope,
                    updated_at
                FROM crm_external_connections
                WHERE owner_user_id = :owner
                  AND provider = :provider
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
            },
        ).mappings().first()

        if not row:

            return {
                "provider": provider,
                "status": "not_connected",
                "configured": provider_enabled(provider),
                "external_writes": False,
            }

        expires = row["access_expires_at"]

        state = row["status"]

        if expires is not None:

            try:

                if int(expires) <= _now():
                    state = "expired"

            except Exception:
                pass

        return {
            "provider": provider,
            "status": state,
            "external_account_id": row[
                "external_account_id"
            ],
            "external_domain": row[
                "external_domain"
            ],
            "access_expires_at": expires,
            "scope": row["scope"],
            "updated_at": str(
                row["updated_at"]
            ),
            "configured": provider_enabled(provider),
            "external_writes": False,
        }

    finally:
        db.close()


def all_connection_status(
    owner_user_id: int,
) -> dict:

    return {
        provider: connection_status(
            owner_user_id,
            provider,
        )
        for provider in sorted(
            ALLOWED_PROVIDERS
        )
    }


def _load_tokens(
    owner_user_id: int,
    provider: str,
) -> dict:

    provider = _provider_ok(provider)

    db = SessionLocal()

    try:

        row = db.execute(
            text(
                """
                SELECT
                    encrypted_access_token,
                    encrypted_refresh_token,
                    access_expires_at
                FROM crm_external_connections
                WHERE owner_user_id = :owner
                  AND provider = :provider
                  AND status IN ('active','degraded')
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {
                "owner": owner_user_id,
                "provider": provider,
            },
        ).mappings().first()

        if not row:
            raise RuntimeError(
                f"{provider.upper()}_NOT_CONNECTED"
            )

        return {
            "access_token": decrypt_secret(
                row["encrypted_access_token"]
            ),
            "refresh_token": (
                decrypt_secret(
                    row["encrypted_refresh_token"]
                )
                if row["encrypted_refresh_token"]
                else None
            ),
            "expires_at": row[
                "access_expires_at"
            ],
        }

    finally:
        db.close()


def access_token(
    owner_user_id: int,
    provider: str,
) -> str:

    tokens = _load_tokens(
        owner_user_id,
        provider,
    )

    expires_at = tokens.get("expires_at")

    if expires_at:

        try:

            if int(expires_at) <= _now() + 120:

                refresh = tokens.get(
                    "refresh_token"
                )

                if not refresh:
                    raise RuntimeError(
                        "REFRESH_TOKEN_MISSING"
                    )

                data = refresh_token(
                    provider,
                    refresh,
                )

                identity = {
                    "account_id": None,
                    "domain": None,
                    "scope": None,
                }

                save_connection(
                    owner_user_id,
                    provider,
                    data,
                    identity,
                )

                return data["access_token"]

        except ValueError:
            pass

    return tokens["access_token"]


def provider_identity(
    provider: str,
    token_data: dict,
) -> dict:

    provider = _provider_ok(provider)

    access = token_data.get(
        "access_token"
    )

    if not access:
        raise RuntimeError(
            "access_token_missing"
        )

    if provider == "amocrm":

        #
        # amoCRM API identity endpoint.
        #
        data = _request_json(
            "GET",
            "https://api.amocrm.ru/api/v4/account",
            headers={
                "Authorization":
                    f"Bearer {access}"
            },
        )

        return {
            "account_id": str(
                data.get("id")
                or data.get("subdomain")
                or ""
            ),
            "domain": (
                data.get("subdomain")
                or ""
            ),
            "scope": None,
            "raw": data,
        }

    if provider == "bitrix24":

        endpoint = (
            token_data.get(
                "client_endpoint"
            )
            or ""
        )

        if not endpoint:
            raise RuntimeError(
                "BITRIX_CLIENT_ENDPOINT_MISSING"
            )

        data = _request_json(
            "POST",
            endpoint.rstrip("/")
            + "/user.current.json",
            json={
                "auth": access,
            },
        )

        return {
            "account_id": str(
                data.get("result", {}).get(
                    "ID",
                    "",
                )
            ),
            "domain": token_data.get(
                "domain"
            ),
            "scope": token_data.get(
                "scope"
            ),
            "raw": data,
        }

    raise ValueError("unsupported provider")


def remote_pipelines(
    owner_user_id: int,
    provider: str,
) -> dict:

    provider = _provider_ok(provider)

    token = access_token(
        owner_user_id,
        provider,
    )

    if provider == "amocrm":

        data = _request_json(
            "GET",
            "https://api.amocrm.ru/api/v4/leads/pipelines",
            headers={
                "Authorization":
                    f"Bearer {token}"
            },
        )

        return data

    if provider == "bitrix24":

        #
        # Bitrix24 pipeline discovery is deliberately
        # read-only in this stage.
        #
        db = SessionLocal()

        try:

            row = db.execute(
                text(
                    """
                    SELECT external_domain
                    FROM crm_external_connections
                    WHERE owner_user_id = :owner
                      AND provider = 'bitrix24'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"owner": owner_user_id},
            ).first()

        finally:
            db.close()

        if not row or not row[0]:
            raise RuntimeError(
                "BITRIX_DOMAIN_MISSING"
            )

        domain = str(row[0]).rstrip("/")

        data = _request_json(
            "POST",
            f"https://{domain}/rest/crm.dealcategory.list.json",
            json={
                "auth": token,
            },
        )

        return data

    raise ValueError("unsupported provider")
