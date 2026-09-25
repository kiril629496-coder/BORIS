"""
BORIS CRM — External Provider Configuration.

Secrets are intentionally NOT stored here.

Environment variables are read only at runtime.

No .env mutation is performed by BORIS CRM.
"""

from __future__ import annotations

import os


PROVIDERS = {
    "amocrm": {
        "name": "amoCRM",
        "authorization_base": "https://www.amocrm.ru/oauth",
        "token_path": "/oauth2/access_token",
    },
    "bitrix24": {
        "name": "Bitrix24",
        "authorization_base": "https://oauth.bitrix.info/oauth/authorize/",
        "token_url": "https://oauth.bitrix.info/oauth/token/",
    },
}


def provider_enabled(provider: str) -> bool:
    provider = provider.lower().strip()

    if provider == "amocrm":
        return bool(
            os.environ.get("BORIS_AMO_CLIENT_ID")
            and os.environ.get("BORIS_AMO_CLIENT_SECRET")
            and os.environ.get("BORIS_AMO_REDIRECT_URI")
        )

    if provider == "bitrix24":
        return bool(
            os.environ.get("BORIS_BITRIX_CLIENT_ID")
            and os.environ.get("BORIS_BITRIX_CLIENT_SECRET")
            and os.environ.get("BORIS_BITRIX_REDIRECT_URI")
        )

    return False


def client_id(provider: str) -> str | None:

    if provider == "amocrm":
        return os.environ.get("BORIS_AMO_CLIENT_ID")

    if provider == "bitrix24":
        return os.environ.get("BORIS_BITRIX_CLIENT_ID")

    return None


def client_secret(provider: str) -> str | None:

    if provider == "amocrm":
        return os.environ.get("BORIS_AMO_CLIENT_SECRET")

    if provider == "bitrix24":
        return os.environ.get("BORIS_BITRIX_CLIENT_SECRET")

    return None


def redirect_uri(provider: str) -> str | None:

    if provider == "amocrm":
        return os.environ.get("BORIS_AMO_REDIRECT_URI")

    if provider == "bitrix24":
        return os.environ.get("BORIS_BITRIX_REDIRECT_URI")

    return None


def public_status(provider: str) -> dict:

    return {
        "provider": provider,
        "enabled": provider_enabled(provider),
        "client_configured": bool(client_id(provider)),
        "redirect_configured": bool(redirect_uri(provider)),
    }
