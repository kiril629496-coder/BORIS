"""
BORIS Zero-Touch Client Launch Finalizer.

READ-ONLY launch truth CLI.

It does not grant:
- owners
- products
- subscriptions
- Avito credentials
- roles

Its job is to convert Factory state into a deterministic
human-readable launch decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from typing import Any

from sqlalchemy import inspect, text

from app.db.session import SessionLocal


READY = "READY"

BORIS_CAN_FIX = "BORIS_CAN_FIX"

PLATFORM_REQUIRED = "PLATFORM_REQUIRED"

CLIENT_REQUIRED = "CLIENT_REQUIRED"

# BORIS_Z3_PENDING_FACTORY_EVIDENCE
PENDING = "PENDING"


@dataclass
class Gate:
    field: str

    status: str

    reason: str

    current: Any = None

    action: str | None = None


def _tables(db) -> set[str]:

    return set(
        inspect(
            db.get_bind()
        ).get_table_names()
    )


def _columns(
    db,
    table: str,
) -> set[str]:

    return {
        c["name"]
        for c in inspect(
            db.get_bind()
        ).get_columns(
            table
        )
    }


def _load_factory(
    db,
    account_id: str,
):

    launch = db.execute(
        text("""
            SELECT *
            FROM factory_launch_state
            WHERE account_id=:account
        """),
        {
            "account":
                account_id,
        },
    ).mappings().first()

    provisioning = db.execute(
        text("""
            SELECT *
            FROM factory_provisioning_state
            WHERE account_id=:account
        """),
        {
            "account":
                account_id,
        },
    ).mappings().first()

    return (
        dict(launch)
        if launch
        else None,
        dict(provisioning)
        if provisioning
        else None,
    )


def _account_row(
    db,
    account_id: str,
):

    tables = _tables(db)

    if "accounts" not in tables:
        return None

    cols = _columns(
        db,
        "accounts",
    )

    if "account_id" not in cols:
        return None

    selected = [
        c
        for c in (
            "account_id",
            "owner_user_id",
            "name",
            "billing_mode",
            "company_niche",
            "company_website",
            "avito_user_id",
            "avito_client_id",
        )
        if c in cols
    ]

    row = db.execute(
        text(
            "SELECT "
            + ", ".join(
                f'"{c}"'
                for c in selected
            )
            + """
              FROM accounts
              WHERE account_id=:account
            """
        ),
        {
            "account":
                account_id,
        },
    ).mappings().first()

    return (
        dict(row)
        if row
        else None
    )


def classify(
    account_id: str,
) -> dict[str, Any]:

    db = SessionLocal()

    try:

        db.execute(
            text(
                "SET TRANSACTION READ ONLY"
            )
        )

        account = _account_row(
            db,
            account_id,
        )

        launch, provisioning = (
            _load_factory(
                db,
                account_id,
            )
        )

        gates: list[Gate] = []

        #######################################################################
        # ACCOUNT
        #######################################################################

        if account is None:

            gates.append(
                Gate(
                    field="account",
                    status=PLATFORM_REQUIRED,
                    reason=(
                        "account_id is absent from canonical accounts table"
                    ),
                    action=(
                        "create/register the client account through "
                        "the canonical onboarding path"
                    ),
                )
            )

            result = {
                "account_id":
                    account_id,

                "overall":
                    PLATFORM_REQUIRED,

                "launch_status":
                    None,

                "provisioning_status":
                    None,

                "gates": [
                    asdict(x)
                    for x in gates
                ],
            }

            return result

        #######################################################################
        # OWNER
        #######################################################################

        owner = account.get(
            "owner_user_id"
        )

        if (
            isinstance(
                owner,
                int,
            )
            and owner > 0
        ):

            gates.append(
                Gate(
                    field="owner_user_id",
                    status=READY,
                    reason="owner assigned",
                    current=owner,
                )
            )

        else:

            gates.append(
                Gate(
                    field="owner_user_id",
                    status=CLIENT_REQUIRED,
                    reason=(
                        "no verified owner relation exists"
                    ),
                    current=owner,
                    action=(
                        "identify the real client/LPR owner; "
                        "do not infer from account name"
                    ),
                )
            )

        #######################################################################
        # BUSINESS PROFILE
        #######################################################################

        niche = (
            account.get(
                "company_niche"
            )
        )

        if niche:

            gates.append(
                Gate(
                    field="business.niche",
                    status=READY,
                    reason="niche present",
                    current=niche,
                )
            )

        else:

            packet = (
                (launch or {}).get(
                    "packet"
                )
                or {}
            )

            business = (
                (
                    packet.get(
                        "onboarding"
                    )
                    or {}
                ).get(
                    "business"
                )
                or {}
            )

            resolved_niche = (
                business.get(
                    "niche"
                )
            )

            if resolved_niche:

                gates.append(
                    Gate(
                        field="business.niche",
                        status=READY,
                        reason=(
                            "niche resolved by Factory onboarding"
                        ),
                        current=resolved_niche,
                    )
                )

            else:

                gates.append(
                    Gate(
                        field="business.niche",
                        status=CLIENT_REQUIRED,
                        reason="business niche absent",
                        action=(
                            "collect product/service direction from client"
                        ),
                    )
                )

        #######################################################################
        # PRODUCT TRUTH
        #######################################################################

        launch_products = int(
            (launch or {}).get(
                "product_count"
            )
            or 0
        )

        prov_products = int(
            (provisioning or {}).get(
                "product_count"
            )
            or 0
        )

        product_count = max(
            launch_products,
            prov_products,
        )

        if product_count > 0:

            gates.append(
                Gate(
                    field="products",
                    status=READY,
                    reason=(
                        "Factory has confirmed product truth"
                    ),
                    current=product_count,
                )
            )

        else:

            gates.append(
                Gate(
                    field="products",
                    status=CLIENT_REQUIRED,
                    reason=(
                        "no confirmed product/entitlement exists"
                    ),
                    current=0,
                    action=(
                        "select or activate the purchased BORIS product; "
                        "do not auto-grant"
                    ),
                )
            )

        #######################################################################
        # FACTORY BLOCKERS
        #######################################################################

        blockers = (
            (provisioning or {}).get(
                "blockers"
            )
            or []
        )

        blocked_fields = {
            str(
                x.get(
                    "field"
                )
            )
            for x in blockers
            if isinstance(
                x,
                dict,
            )
        }

        #######################################################################
        # AVITO
        #######################################################################

        if provisioning is None:

            gates.append(
                Gate(
                    field="channels.avito",
                    status=PENDING,
                    reason=(
                        "Factory has not evaluated Avito channel yet"
                    ),
                    action=(
                        "run Factory bootstrap before deciding READY"
                    ),
                )
            )

        elif "channels.avito" in blocked_fields:

            gates.append(
                Gate(
                    field="channels.avito",
                    status=PLATFORM_REQUIRED,
                    reason=(
                        "Factory explicitly reports Avito channel missing"
                    ),
                    action=(
                        "connect/assign the existing real Avito account "
                        "or collect credentials from client"
                    ),
                )
            )

        else:

            gates.append(
                Gate(
                    field="channels.avito",
                    status=READY,
                    reason=(
                        "Factory evaluated the account and reports no "
                        "Avito channel blocker"
                    ),
                )
            )

        #######################################################################
        # ROLES
        #######################################################################

        if provisioning is None:

            gates.append(
                Gate(
                    field="access.roles",
                    status=PENDING,
                    reason=(
                        "Factory has not evaluated access roles yet"
                    ),
                    action=(
                        "run Factory bootstrap before deciding READY"
                    ),
                )
            )

        elif "access.roles" in blocked_fields:

            gates.append(
                Gate(
                    field="access.roles",
                    status=PLATFORM_REQUIRED,
                    reason=(
                        "Factory explicitly reports access role blocker"
                    ),
                    action=(
                        "assign roles from purchased entitlements"
                    ),
                )
            )

        else:

            gates.append(
                Gate(
                    field="access.roles",
                    status=READY,
                    reason=(
                        "Factory evaluated the account and reports no "
                        "role blocker"
                    ),
                )
            )

        #######################################################################
        # FACTORY STATE
        #######################################################################

        launch_status = (
            (launch or {}).get(
                "launch_status"
            )
        )

        provisioning_status = (
            (provisioning or {}).get(
                "provisioning_status"
            )
        )

        if (
            provisioning_status
            == "READY_INTERNAL"
        ):

            gates.append(
                Gate(
                    field="factory",
                    status=READY,
                    reason=(
                        "Factory provisioning is READY_INTERNAL"
                    ),
                    current=provisioning_status,
                )
            )

        elif provisioning is None:

            gates.append(
                Gate(
                    field="factory",
                    status=BORIS_CAN_FIX,
                    reason=(
                        "Factory provisioning state not built yet"
                    ),
                    action=(
                        "run canonical Factory reconciliation"
                    ),
                )
            )

        else:

            gates.append(
                Gate(
                    field="factory",
                    status=BORIS_CAN_FIX,
                    reason=(
                        "Factory state exists but client is not READY_INTERNAL"
                    ),
                    current=provisioning_status,
                    action=(
                        "rerun Factory after external gates are satisfied"
                    ),
                )
            )

        #######################################################################
        # OVERALL
        #######################################################################

        statuses = {
            gate.status
            for gate in gates
        }

        if CLIENT_REQUIRED in statuses:

            overall = CLIENT_REQUIRED

        elif PLATFORM_REQUIRED in statuses:

            overall = PLATFORM_REQUIRED

        elif BORIS_CAN_FIX in statuses:

            overall = BORIS_CAN_FIX

        elif PENDING in statuses:

            overall = PENDING

        else:

            overall = READY

        return {
            "account_id":
                account_id,

            "account_name":
                account.get(
                    "name"
                ),

            "overall":
                overall,

            "launch_status":
                launch_status,

            "provisioning_status":
                provisioning_status,

            "product_count":
                product_count,

            "blockers":
                blockers,

            "gates": [
                asdict(
                    gate
                )
                for gate in gates
            ],
        }

    finally:

        db.rollback()
        db.close()


def human_report(
    result: dict[str, Any],
):

    print(
        "=" * 78
    )

    print(
        "BORIS ZERO-TOUCH CLIENT FINALIZER"
    )

    print(
        "=" * 78
    )

    print(
        "account_id=",
        result.get(
            "account_id"
        ),
    )

    print(
        "account_name=",
        result.get(
            "account_name"
        ),
    )

    print(
        "overall=",
        result.get(
            "overall"
        ),
    )

    print(
        "launch_status=",
        result.get(
            "launch_status"
        ),
    )

    print(
        "provisioning_status=",
        result.get(
            "provisioning_status"
        ),
    )

    print(
        "product_count=",
        result.get(
            "product_count"
        ),
    )

    print()

    print(
        "--- GATES ---"
    )

    for gate in result.get(
        "gates",
        []
    ):

        print()

        print(
            f"[{gate['status']}] "
            f"{gate['field']}"
        )

        print(
            "  reason:",
            gate[
                "reason"
            ],
        )

        if gate.get(
            "current"
        ) is not None:

            print(
                "  current:",
                gate[
                    "current"
                ],
            )

        if gate.get(
            "action"
        ):

            print(
                "  next:",
                gate[
                    "action"
                ],
            )

    print()
    print(
        "=" * 78
    )


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "account_id",
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args()

    result = classify(
        args.account_id
    )

    if args.json:

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    else:

        human_report(
            result
        )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
