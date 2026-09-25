from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import httpx

from sqlalchemy import text

from .vault import (
    load_secret,
    save_secret,
)


TIMEOUT = 30.0


def clean_domain(
    value: str,
) -> str:

    value = (
        value
        .strip()
        .replace("https://", "")
        .replace("http://", "")
        .strip("/")
    )

    return value


class ExternalCRMError(RuntimeError):
    pass


class BaseExternalCRM:

    provider = ""

    def __init__(
        self,
        db,
        owner_user_id: int,
    ):

        self.db = db
        self.owner = owner_user_id

        self.connection = (
            db.execute(
                text("""
                    SELECT *

                    FROM boris_crm_connections

                    WHERE owner_user_id=:owner
                      AND provider=:provider
                """),
                {
                    "owner": owner_user_id,
                    "provider": self.provider,
                },
            )
            .mappings()
            .first()
        )

        if not self.connection:
            raise ExternalCRMError(
                f"{self.provider}: connection missing"
            )

        self.secret = load_secret(
            db,
            owner_user_id,
            self.provider,
        )


    def _save_secret(
        self,
        payload: dict,
    ):

        self.secret.update(
            payload
        )

        save_secret(
            self.db,
            self.owner,
            self.provider,
            self.secret,
        )


    def _update_connection(
        self,
        **values,
    ):

        allowed = {
            "status",
            "external_account_id",
            "external_domain",
            "token_expires_at",
            "last_sync_at",
            "last_error",
        }

        fields = []
        params = {
            "owner": self.owner,
            "provider": self.provider,
        }

        for key, value in values.items():

            if key not in allowed:
                continue

            fields.append(
                f"{key}=:{key}"
            )

            params[key] = value

        if not fields:
            return

        self.db.execute(
            text(
                "UPDATE boris_crm_connections "
                "SET "
                + ",".join(fields)
                + ", updated_at=NOW() "
                "WHERE owner_user_id=:owner "
                "AND provider=:provider"
            ),
            params,
        )


class AmoCRM(BaseExternalCRM):

    provider = "amocrm"


    def oauth_url(
        self,
        state: str,
    ) -> str:

        client_id = self.secret.get(
            "client_id"
        )

        if not client_id:
            raise ExternalCRMError(
                "AMO_CLIENT_ID_MISSING"
            )

        return (
            "https://www.amocrm.ru/oauth?"
            + urlencode({
                "client_id": client_id,
                "state": state,
                "mode": "popup",
            })
        )


    def exchange_code(
        self,
        code: str,
        referer: str,
    ):

        domain = clean_domain(
            referer
        )

        client_id = self.secret.get(
            "client_id"
        )

        client_secret = self.secret.get(
            "client_secret"
        )

        redirect_uri = self.secret.get(
            "redirect_uri"
        )

        if not all([
            client_id,
            client_secret,
            redirect_uri,
        ]):
            raise ExternalCRMError(
                "AMO_APP_CONFIG_INCOMPLETE"
            )

        response = httpx.post(
            f"https://{domain}/oauth2/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=TIMEOUT,
        )

        if response.status_code >= 300:
            raise ExternalCRMError(
                "AMO_TOKEN_EXCHANGE_FAILED:"
                + response.text[:500]
            )

        body = response.json()

        expires_in = int(
            body.get(
                "expires_in",
                86400,
            )
        )

        self._save_secret({
            "access_token":
                body["access_token"],

            "refresh_token":
                body["refresh_token"],

            "token_expires_unix":
                int(time.time())
                + expires_in,
        })

        self._update_connection(
            status="active",
            external_domain=domain,
        )

        self.db.commit()


    def refresh(
        self,
    ):

        domain = clean_domain(
            self.connection[
                "external_domain"
            ]
        )

        response = httpx.post(
            f"https://{domain}/oauth2/access_token",
            json={
                "client_id":
                    self.secret["client_id"],

                "client_secret":
                    self.secret["client_secret"],

                "grant_type":
                    "refresh_token",

                "refresh_token":
                    self.secret["refresh_token"],

                "redirect_uri":
                    self.secret["redirect_uri"],
            },
            timeout=TIMEOUT,
        )

        if response.status_code >= 300:
            raise ExternalCRMError(
                "AMO_REFRESH_FAILED:"
                + response.text[:500]
            )

        body = response.json()

        self._save_secret({
            "access_token":
                body["access_token"],

            "refresh_token":
                body["refresh_token"],

            "token_expires_unix":
                int(time.time())
                + int(
                    body.get(
                        "expires_in",
                        86400,
                    )
                ),
        })

        self.db.commit()


    def ensure_token(
        self,
    ) -> str:

        expires = int(
            self.secret.get(
                "token_expires_unix",
                0,
            )
        )

        if (
            not self.secret.get("access_token")
            or expires <= int(time.time()) + 120
        ):
            self.refresh()

        return self.secret[
            "access_token"
        ]


    def request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> Any:

        token = self.ensure_token()

        domain = clean_domain(
            self.connection[
                "external_domain"
            ]
        )

        url = (
            f"https://{domain}"
            + path
        )

        headers = dict(
            kwargs.pop(
                "headers",
                {},
            )
        )

        headers["Authorization"] = (
            "Bearer " + token
        )

        response = httpx.request(
            method,
            url,
            headers=headers,
            timeout=TIMEOUT,
            **kwargs,
        )

        if response.status_code == 401:

            self.refresh()

            headers["Authorization"] = (
                "Bearer "
                + self.secret["access_token"]
            )

            response = httpx.request(
                method,
                url,
                headers=headers,
                timeout=TIMEOUT,
                **kwargs,
            )

        if response.status_code >= 300:
            raise ExternalCRMError(
                f"AMO_HTTP_{response.status_code}:"
                + response.text[:1000]
            )

        if not response.content:
            return {}

        return response.json()


    def pipelines(
        self,
    ) -> list[dict]:

        body = self.request(
            "GET",
            "/api/v4/leads/pipelines",
        )

        rows = (
            body.get(
                "_embedded",
                {},
            )
            .get(
                "pipelines",
                [],
            )
        )

        result = []

        for pipeline in rows:

            statuses = (
                pipeline.get(
                    "_embedded",
                    {},
                )
                .get(
                    "statuses",
                    [],
                )
            )

            result.append({
                "id":
                    str(pipeline["id"]),

                "name":
                    pipeline.get(
                        "name",
                        "",
                    ),

                "stages": [
                    {
                        "id":
                            str(stage["id"]),

                        "name":
                            stage.get(
                                "name",
                                "",
                            ),

                        "sort":
                            stage.get(
                                "sort",
                                0,
                            ),
                    }

                    for stage in statuses
                ],
            })

        return result


    def create_contact(
        self,
        contact: dict,
    ) -> str:

        payload = {
            "name":
                contact.get(
                    "display_name"
                )
                or "Клиент BORIS",
        }

        custom = []

        if contact.get(
            "primary_phone"
        ):
            custom.append({
                "field_code": "PHONE",
                "values": [{
                    "value":
                        contact[
                            "primary_phone"
                        ],
                    "enum_code": "WORK",
                }],
            })

        if contact.get(
            "primary_email"
        ):
            custom.append({
                "field_code": "EMAIL",
                "values": [{
                    "value":
                        contact[
                            "primary_email"
                        ],
                    "enum_code": "WORK",
                }],
            })

        if custom:
            payload[
                "custom_fields_values"
            ] = custom

        body = self.request(
            "POST",
            "/api/v4/contacts",
            json=[payload],
        )

        rows = (
            body.get(
                "_embedded",
                {},
            )
            .get(
                "contacts",
                [],
            )
        )

        if not rows:
            raise ExternalCRMError(
                "AMO_CONTACT_CREATE_EMPTY"
            )

        return str(
            rows[0]["id"]
        )


    def update_contact(
        self,
        external_id: str,
        contact: dict,
    ):

        payload = {
            "name":
                contact.get(
                    "display_name"
                )
                or "Клиент BORIS",
        }

        self.request(
            "PATCH",
            f"/api/v4/contacts/{external_id}",
            json=payload,
        )


    def create_deal(
        self,
        deal: dict,
        external_pipeline_id: str,
        external_stage_id: str,
    ) -> str:

        payload = {
            "name":
                deal.get(
                    "title"
                )
                or "Сделка BORIS",

            "price":
                int(
                    (
                        deal.get(
                            "amount_kopeks"
                        )
                        or 0
                    )
                    / 100
                ),

            "pipeline_id":
                int(
                    external_pipeline_id
                ),

            "status_id":
                int(
                    external_stage_id
                ),
        }

        body = self.request(
            "POST",
            "/api/v4/leads",
            json=[payload],
        )

        rows = (
            body.get(
                "_embedded",
                {},
            )
            .get(
                "leads",
                [],
            )
        )

        if not rows:
            raise ExternalCRMError(
                "AMO_DEAL_CREATE_EMPTY"
            )

        return str(
            rows[0]["id"]
        )


    def update_deal(
        self,
        external_id: str,
        deal: dict,
        external_pipeline_id: str,
        external_stage_id: str,
    ):

        payload = {
            "name":
                deal.get(
                    "title"
                )
                or "Сделка BORIS",

            "price":
                int(
                    (
                        deal.get(
                            "amount_kopeks"
                        )
                        or 0
                    )
                    / 100
                ),

            "pipeline_id":
                int(
                    external_pipeline_id
                ),

            "status_id":
                int(
                    external_stage_id
                ),
        }

        self.request(
            "PATCH",
            f"/api/v4/leads/{external_id}",
            json=payload,
        )


    def link_contact(
        self,
        external_deal_id: str,
        external_contact_id: str,
    ):

        self.request(
            "POST",
            f"/api/v4/leads/{external_deal_id}/link",
            json=[{
                "to_entity_id":
                    int(
                        external_contact_id
                    ),

                "to_entity_type":
                    "contacts",

                "metadata": {
                    "main_contact": True
                },
            }],
        )


    def get_deal(
        self,
        external_id: str,
    ) -> dict:

        return self.request(
            "GET",
            f"/api/v4/leads/{external_id}",
        )


    def get_contact(
        self,
        external_id: str,
    ) -> dict:

        return self.request(
            "GET",
            f"/api/v4/contacts/{external_id}",
        )


    def list_deals(
        self,
        limit: int = 100,
    ) -> list[dict]:

        body = self.request(
            "GET",
            "/api/v4/leads",
            params={
                "limit":
                    min(limit, 250)
            },
        )

        return (
            body.get(
                "_embedded",
                {},
            )
            .get(
                "leads",
                [],
            )
        )


    def subscribe_webhooks(
        self,
        destination: str,
    ):

        self.request(
            "POST",
            "/api/v4/webhooks",
            json={
                "destination":
                    destination,

                "settings": [
                    "add_lead",
                    "update_lead",
                    "add_contact",
                    "update_contact",
                ],
            },
        )


class Bitrix24(BaseExternalCRM):

    provider = "bitrix24"


    def oauth_url(
        self,
        state: str,
    ) -> str:

        domain = clean_domain(
            self.connection[
                "external_domain"
            ]
        )

        client_id = self.secret.get(
            "client_id"
        )

        if not domain:
            raise ExternalCRMError(
                "BITRIX_PORTAL_MISSING"
            )

        if not client_id:
            raise ExternalCRMError(
                "BITRIX_CLIENT_ID_MISSING"
            )

        return (
            f"https://{domain}/oauth/authorize/?"
            + urlencode({
                "client_id":
                    client_id,

                "state":
                    state,
            })
        )


    def exchange_code(
        self,
        code: str,
        callback_domain: str | None = None,
    ):

        response = httpx.get(
            "https://oauth.bitrix.info/oauth/token/",
            params={
                "grant_type":
                    "authorization_code",

                "client_id":
                    self.secret[
                        "client_id"
                    ],

                "client_secret":
                    self.secret[
                        "client_secret"
                    ],

                "code":
                    code,
            },
            timeout=TIMEOUT,
        )

        if response.status_code >= 300:
            raise ExternalCRMError(
                "BITRIX_TOKEN_EXCHANGE_FAILED:"
                + response.text[:500]
            )

        body = response.json()

        client_endpoint = (
            body.get(
                "client_endpoint"
            )
            or ""
        )

        portal_domain = ""

        if client_endpoint:
            portal_domain = clean_domain(
                client_endpoint
                .replace(
                    "/rest/",
                    "",
                )
            )

        self._save_secret({
            "access_token":
                body["access_token"],

            "refresh_token":
                body["refresh_token"],

            "client_endpoint":
                body.get(
                    "client_endpoint"
                ),

            "server_endpoint":
                body.get(
                    "server_endpoint"
                ),

            "member_id":
                body.get(
                    "member_id"
                ),

            "token_expires_unix":
                int(time.time())
                + int(
                    body.get(
                        "expires_in",
                        3600,
                    )
                ),
        })

        self._update_connection(
            status="active",
            external_domain=(
                portal_domain
                or callback_domain
                or self.connection[
                    "external_domain"
                ]
            ),
            external_account_id=
                body.get(
                    "member_id"
                ),
        )

        self.db.commit()


    def refresh(
        self,
    ):

        response = httpx.get(
            "https://oauth.bitrix.info/oauth/token/",
            params={
                "grant_type":
                    "refresh_token",

                "client_id":
                    self.secret[
                        "client_id"
                    ],

                "client_secret":
                    self.secret[
                        "client_secret"
                    ],

                "refresh_token":
                    self.secret[
                        "refresh_token"
                    ],
            },
            timeout=TIMEOUT,
        )

        if response.status_code >= 300:
            raise ExternalCRMError(
                "BITRIX_REFRESH_FAILED:"
                + response.text[:500]
            )

        body = response.json()

        self._save_secret({
            "access_token":
                body["access_token"],

            "refresh_token":
                body["refresh_token"],

            "client_endpoint":
                body.get(
                    "client_endpoint",
                    self.secret.get(
                        "client_endpoint"
                    ),
                ),

            "server_endpoint":
                body.get(
                    "server_endpoint",
                    self.secret.get(
                        "server_endpoint"
                    ),
                ),

            "token_expires_unix":
                int(time.time())
                + int(
                    body.get(
                        "expires_in",
                        3600,
                    )
                ),
        })

        self.db.commit()


    def ensure_token(
        self,
    ) -> str:

        expires = int(
            self.secret.get(
                "token_expires_unix",
                0,
            )
        )

        if (
            not self.secret.get(
                "access_token"
            )
            or expires <= int(time.time()) + 120
        ):
            self.refresh()

        return self.secret[
            "access_token"
        ]


    def call(
        self,
        method: str,
        params: dict | None = None,
    ):

        token = self.ensure_token()

        endpoint = self.secret.get(
            "client_endpoint"
        )

        if not endpoint:
            raise ExternalCRMError(
                "BITRIX_CLIENT_ENDPOINT_MISSING"
            )

        url = (
            endpoint.rstrip("/")
            + "/"
            + method
        )

        payload = dict(
            params or {}
        )

        payload["auth"] = token

        response = httpx.post(
            url,
            json=payload,
            timeout=TIMEOUT,
        )

        if response.status_code >= 300:
            raise ExternalCRMError(
                f"BITRIX_HTTP_{response.status_code}:"
                + response.text[:1000]
            )

        body = response.json()

        if body.get("error"):

            if body.get(
                "error"
            ) == "expired_token":

                self.refresh()

                payload["auth"] = (
                    self.secret[
                        "access_token"
                    ]
                )

                response = httpx.post(
                    url,
                    json=payload,
                    timeout=TIMEOUT,
                )

                body = response.json()

            if body.get("error"):
                raise ExternalCRMError(
                    "BITRIX_API:"
                    + str(
                        body.get(
                            "error_description"
                        )
                        or body[
                            "error"
                        ]
                    )
                )

        return body.get(
            "result"
        )


    def pipelines(
        self,
    ) -> list[dict]:

        categories_result = self.call(
            "crm.category.list",
            {
                "entityTypeId": 2,
            },
        )

        categories = []

        if isinstance(
            categories_result,
            dict,
        ):
            categories = (
                categories_result.get(
                    "categories",
                    []
                )
            )

        result = []

        for category in categories:

            category_id = int(
                category.get(
                    "id",
                    0,
                )
            )

            entity_id = (
                "DEAL_STAGE"
                if category_id == 0
                else
                f"DEAL_STAGE_{category_id}"
            )

            stages = self.call(
                "crm.status.list",
                {
                    "order": {
                        "SORT": "ASC"
                    },
                    "filter": {
                        "ENTITY_ID":
                            entity_id
                    },
                },
            ) or []

            result.append({
                "id":
                    str(category_id),

                "name":
                    category.get(
                        "name",
                        (
                            "Основная"
                            if category_id == 0
                            else str(
                                category_id
                            )
                        ),
                    ),

                "stages": [
                    {
                        "id":
                            str(
                                stage[
                                    "STATUS_ID"
                                ]
                            ),

                        "name":
                            stage.get(
                                "NAME",
                                "",
                            ),

                        "sort":
                            int(
                                stage.get(
                                    "SORT",
                                    0,
                                )
                            ),

                        "semantic":
                            (
                                stage.get(
                                    "EXTRA",
                                    {},
                                )
                                .get(
                                    "SEMANTICS"
                                )
                            ),
                    }

                    for stage in stages
                ],
            })

        return result


    def create_contact(
        self,
        contact: dict,
    ) -> str:

        fields = {
            "NAME":
                contact.get(
                    "display_name"
                )
                or "Клиент BORIS",
        }

        if contact.get(
            "primary_phone"
        ):
            fields["PHONE"] = [{
                "VALUE":
                    contact[
                        "primary_phone"
                    ],
                "VALUE_TYPE":
                    "WORK",
            }]

        if contact.get(
            "primary_email"
        ):
            fields["EMAIL"] = [{
                "VALUE":
                    contact[
                        "primary_email"
                    ],
                "VALUE_TYPE":
                    "WORK",
            }]

        result = self.call(
            "crm.contact.add",
            {
                "fields": fields
            },
        )

        return str(
            result
        )


    def update_contact(
        self,
        external_id: str,
        contact: dict,
    ):

        fields = {
            "NAME":
                contact.get(
                    "display_name"
                )
                or "Клиент BORIS",
        }

        self.call(
            "crm.contact.update",
            {
                "id":
                    int(
                        external_id
                    ),
                "fields":
                    fields,
            },
        )


    def create_deal(
        self,
        deal: dict,
        external_pipeline_id: str,
        external_stage_id: str,
    ) -> str:

        fields = {
            "TITLE":
                deal.get(
                    "title"
                )
                or "Сделка BORIS",

            "OPPORTUNITY":
                float(
                    (
                        deal.get(
                            "amount_kopeks"
                        )
                        or 0
                    )
                    / 100
                ),

            "CURRENCY_ID":
                deal.get(
                    "currency",
                    "RUB",
                ),

            "CATEGORY_ID":
                int(
                    external_pipeline_id
                ),

            "STAGE_ID":
                external_stage_id,
        }

        result = self.call(
            "crm.deal.add",
            {
                "fields":
                    fields,
            },
        )

        return str(
            result
        )


    def update_deal(
        self,
        external_id: str,
        deal: dict,
        external_pipeline_id: str,
        external_stage_id: str,
    ):

        fields = {
            "TITLE":
                deal.get(
                    "title"
                )
                or "Сделка BORIS",

            "OPPORTUNITY":
                float(
                    (
                        deal.get(
                            "amount_kopeks"
                        )
                        or 0
                    )
                    / 100
                ),

            "CURRENCY_ID":
                deal.get(
                    "currency",
                    "RUB",
                ),

            "STAGE_ID":
                external_stage_id,
        }

        self.call(
            "crm.deal.update",
            {
                "id":
                    int(
                        external_id
                    ),

                "fields":
                    fields,
            },
        )


    def link_contact(
        self,
        external_deal_id: str,
        external_contact_id: str,
    ):

        self.call(
            "crm.deal.contact.add",
            {
                "id":
                    int(
                        external_deal_id
                    ),

                "fields": {
                    "CONTACT_ID":
                        int(
                            external_contact_id
                        ),

                    "IS_PRIMARY":
                        "Y",

                    "SORT":
                        10,
                },
            },
        )


    def get_deal(
        self,
        external_id: str,
    ) -> dict:

        return (
            self.call(
                "crm.deal.get",
                {
                    "id":
                        int(
                            external_id
                        ),
                },
            )
            or {}
        )


    def get_contact(
        self,
        external_id: str,
    ) -> dict:

        return (
            self.call(
                "crm.contact.get",
                {
                    "id":
                        int(
                            external_id
                        ),
                },
            )
            or {}
        )


    def list_deals(
        self,
        limit: int = 100,
    ) -> list[dict]:

        result = self.call(
            "crm.deal.list",
            {
                "order": {
                    "ID": "DESC"
                },

                "select": [
                    "ID",
                    "TITLE",
                    "OPPORTUNITY",
                    "CURRENCY_ID",
                    "CATEGORY_ID",
                    "STAGE_ID",
                    "CONTACT_ID",
                ],

                "start": 0,
            },
        )

        if isinstance(
            result,
            list,
        ):
            return result[
                :limit
            ]

        return []


    def subscribe_webhooks(
        self,
        destination: str,
    ):

        events = [
            "ONCRMDEALADD",
            "ONCRMDEALUPDATE",
            "ONCRMCONTACTADD",
            "ONCRMCONTACTUPDATE",
        ]

        for event in events:

            self.call(
                "event.bind",
                {
                    "event":
                        event,

                    "handler":
                        destination,
                },
            )


def connector_for(
    provider: str,
    db,
    owner: int,
):

    if provider == "amocrm":
        return AmoCRM(
            db,
            owner,
        )

    if provider == "bitrix24":
        return Bitrix24(
            db,
            owner,
        )

    raise ExternalCRMError(
        "UNSUPPORTED_PROVIDER"
    )
