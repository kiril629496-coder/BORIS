"""
BORIS Z1 Automatic Launch Action Engine.

Safety model:

PLAN:
    read-only.
    Determines which actions are allowed.

APPLY:
    only canonical internal Factory reconciliation.

Never performs:
    owner assignment
    product/entitlement grants
    Avito credential creation
    access role invention
    publication
    paid promotion
    outbound messaging
    AI calls
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from app.factory.zero_touch_finalizer import (
    BORIS_CAN_FIX,
    CLIENT_REQUIRED,
    PLATFORM_REQUIRED,
    PENDING,
    READY,
    classify,
)


BACKEND = Path(
    "/root/BORIS/backend"
)

PYTHON = (
    BACKEND
    / "venv/bin/python3"
)

FACTORY_D = (
    BACKEND
    / "factory_d.py"
)

FACTORY_G = (
    BACKEND
    / "factory_g.py"
)


@dataclass
class Action:
    action: str
    actor: str
    allowed: bool
    reason: str
    mutating: bool = False


def build_actions(
    result: dict[str, Any],
) -> list[Action]:

    overall = result.get(
        "overall"
    )

    actions: list[Action] = []

    if overall == READY:

        actions.append(
            Action(
                action="NOOP",
                actor="BORIS",
                allowed=True,
                reason=(
                    "client already READY; "
                    "no launch repair required"
                ),
                mutating=False,
            )
        )

        return actions

    gates = result.get(
        "gates",
        []
    )

    for gate in gates:

        status = gate.get(
            "status"
        )

        field = gate.get(
            "field"
        )

        if status == CLIENT_REQUIRED:

            actions.append(
                Action(
                    action=(
                        "WAIT_CLIENT:"
                        + str(field)
                    ),
                    actor="CLIENT",
                    allowed=False,
                    reason=gate.get(
                        "reason"
                    )
                    or "client input required",
                    mutating=False,
                )
            )

        elif status == PLATFORM_REQUIRED:

            actions.append(
                Action(
                    action=(
                        "WAIT_PLATFORM:"
                        + str(field)
                    ),
                    actor="PLATFORM",
                    allowed=False,
                    reason=gate.get(
                        "reason"
                    )
                    or "platform action required",
                    mutating=False,
                )
            )

        elif (
            status
            == BORIS_CAN_FIX
        ):

            if field == "factory":

                actions.append(
                    Action(
                        action=(
                            "FACTORY_RECONCILE"
                        ),
                        actor="BORIS",
                        allowed=True,
                        reason=gate.get(
                            "reason"
                        )
                        or (
                            "Factory reconciliation required"
                        ),
                        mutating=True,
                    )
                )

            else:

                actions.append(
                    Action(
                        action=(
                            "UNSUPPORTED_INTERNAL:"
                            + str(field)
                        ),
                        actor="BORIS",
                        allowed=False,
                        reason=(
                            "Z1 has no explicitly approved "
                            "executor for this field"
                        ),
                        mutating=False,
                    )
                )

    return actions


def plan(
    account_id: str,
) -> dict[str, Any]:

    before = classify(
        account_id
    )

    actions = build_actions(
        before
    )

    external_blockers = [
        action
        for action in actions
        if action.actor
        in {
            "CLIENT",
            "PLATFORM",
        }
    ]

    allowed_mutations = [
        action
        for action in actions
        if (
            action.actor == "BORIS"
            and action.allowed
            and action.mutating
        )
    ]

    return {
        "account_id":
            account_id,

        "before":
            before,

        "actions": [
            asdict(x)
            for x in actions
        ],

        "external_blockers":
            len(
                external_blockers
            ),

        "allowed_internal_mutations":
            len(
                allowed_mutations
            ),

        "can_apply":
            bool(
                allowed_mutations
            )
            and not bool(
                external_blockers
            ),
    }


def _run(
    path: Path,
    *args: str,
) -> dict[str, Any]:

    proc = subprocess.run(
        [
            str(PYTHON),
            str(path),
            *args,
        ],
        cwd=str(BACKEND),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )

    return {
        "file":
            str(path),

        "returncode":
            proc.returncode,

        "output":
            proc.stdout,
    }


def apply(
    account_id: str,
) -> dict[str, Any]:

    launch_plan = plan(
        account_id
    )

    if not launch_plan[
        "can_apply"
    ]:

        return {
            "account_id":
                account_id,

            "applied":
                False,

            "reason":
                (
                    "external blocker exists "
                    "or no approved internal mutation exists"
                ),

            "plan":
                launch_plan,

            "after":
                launch_plan[
                    "before"
                ],
        }

    action_names = {
        x["action"]
        for x in launch_plan[
            "actions"
        ]
        if x["allowed"]
    }

    allowed = {
        "FACTORY_RECONCILE",
    }

    unknown = (
        action_names
        - allowed
    )

    unknown.discard(
        "NOOP"
    )

    if unknown:

        raise RuntimeError(
            "FAIL CLOSED: unknown approved actions: "
            + repr(
                sorted(
                    unknown
                )
            )
        )

    runs = []

    if (
        "FACTORY_RECONCILE"
        in action_names
    ):

        d = _run(
            FACTORY_D,
            "--apply",
        )

        runs.append(
            d
        )

        if d[
            "returncode"
        ] != 0:

            raise RuntimeError(
                "Factory D failed:\n"
                + d[
                    "output"
                ][-4000:]
            )

        g = _run(
            FACTORY_G,
            "--apply",
        )

        runs.append(
            g
        )

        if g[
            "returncode"
        ] != 0:

            raise RuntimeError(
                "Factory G failed:\n"
                + g[
                    "output"
                ][-4000:]
            )

    after = classify(
        account_id
    )

    return {
        "account_id":
            account_id,

        "applied":
            True,

        "plan":
            launch_plan,

        "runs":
            runs,

        "after":
            after,
    }


# BORIS_Z3_REGISTRATION_BOOTSTRAP
def bootstrap(
    account_id: str,
) -> dict[str, Any]:
    """
    Refresh canonical internal Factory state for an already registered account.

    Does NOT assign:
    - owner
    - products
    - entitlements
    - Avito credentials
    - roles
    """

    before = classify(
        account_id
    )

    if (
        before.get("account_name") is None
        and
        before.get("launch_status") is None
        and
        before.get("provisioning_status") is None
        and
        any(
            gate.get("field") == "account"
            for gate in before.get(
                "gates",
                [],
            )
        )
    ):

        return {
            "account_id":
                account_id,

            "bootstrapped":
                False,

            "reason":
                "canonical account does not exist",

            "before":
                before,

            "after":
                before,

            "runs":
                [],
        }

    runs = []

    d = _run(
        FACTORY_D,
        "--apply",
    )

    runs.append(d)

    if d["returncode"] != 0:

        raise RuntimeError(
            "Factory D apply failed:\n"
            + d["output"][-4000:]
        )

    g = _run(
        FACTORY_G,
        "--apply",
    )

    runs.append(g)

    if g["returncode"] != 0:

        raise RuntimeError(
            "Factory G apply failed:\n"
            + g["output"][-4000:]
        )

    after = classify(
        account_id
    )

    return {
        "account_id":
            account_id,

        "bootstrapped":
            True,

        "before":
            before,

        "runs":
            runs,

        "after":
            after,
    }



def print_human(
    obj: dict[str, Any],
):

    print(
        "=" * 78
    )

    print(
        "BORIS Z1 AUTOMATIC LAUNCH ACTION ENGINE"
    )

    print(
        "=" * 78
    )

    print(
        "account_id=",
        obj.get(
            "account_id"
        ),
    )

    if "before" in obj:

        print(
            "before=",
            obj[
                "before"
            ].get(
                "overall"
            ),
        )

        print(
            "external_blockers=",
            obj.get(
                "external_blockers"
            ),
        )

        print(
            "allowed_internal_mutations=",
            obj.get(
                "allowed_internal_mutations"
            ),
        )

        print(
            "can_apply=",
            obj.get(
                "can_apply"
            ),
        )

        print()

        print(
            "--- ACTIONS ---"
        )

        for action in obj.get(
            "actions",
            []
        ):

            print(
                f"{action['actor']:8s} "
                f"{action['action']:40s} "
                f"allowed={action['allowed']} "
                f"mutating={action['mutating']}"
            )

            print(
                "  ",
                action[
                    "reason"
                ],
            )

    else:

        print(
            "applied=",
            obj.get(
                "applied"
            ),
        )

        print(
            "reason=",
            obj.get(
                "reason"
            ),
        )

        after = obj.get(
            "after"
        ) or {}

        print(
            "after=",
            after.get(
                "overall"
            ),
        )

        print(
            "launch_status=",
            after.get(
                "launch_status"
            ),
        )

        print(
            "provisioning_status=",
            after.get(
                "provisioning_status"
            ),
        )

    print(
        "=" * 78
    )


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "account_id"
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Execute only explicitly approved "
            "internal BORIS actions"
        ),
    )

    mode.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Build/refresh Factory state for an already registered account"
        ),
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args()

    if args.bootstrap:

        obj = bootstrap(
            args.account_id
        )

    elif args.apply:

        obj = apply(
            args.account_id
        )

    else:

        obj = plan(
            args.account_id
        )

    if args.json:

        print(
            json.dumps(
                obj,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    else:

        print_human(
            obj
        )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
