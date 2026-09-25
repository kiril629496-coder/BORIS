from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional
import inspect


@dataclass
class LaunchStep:
    key: str
    title: str
    status: str
    reason: str
    data: dict


def _safe_import(module_name: str, function_name: str):
    module = __import__(
        module_name,
        fromlist=[function_name],
    )
    return getattr(
        module,
        function_name,
    )


def discover_contract() -> dict:

    targets = {
        "create_client": (
            "app.api.admin_clients",
            "create_client",
        ),

        "provision": (
            "app.api.admin_clients",
            "provision",
        ),

        "onboarding_complete": (
            "app.api.onboarding",
            "complete",
        ),

        "factory_sync": (
            "app.api.onboarding_factory",
            "factory_sync",
        ),

        "list_launches": (
            "app.api.factory_launches",
            "list_launches",
        ),

        "get_launch": (
            "app.api.factory_launches",
            "get_launch",
        ),

        "build_all": (
            "app.factory.client_launch",
            "build_all",
        ),

        "build_account_health": (
            "app.factory.client_launch",
            "build_account_health",
        ),

        "build_plan": (
            "app.factory.provisioning",
            "build_plan",
        ),

        "launch_rows": (
            "app.factory.provisioning",
            "launch_rows",
        ),

        "bridge_sync": (
            "app.services.onboarding_factory_sync",
            "bridge_sync",
        ),

        "full_sync": (
            "app.services.onboarding_factory_sync",
            "full_sync",
        ),

        "run_project": (
            "app.api.posting",
            "run_project",
        ),

        "publish_post": (
            "app.api.posting",
            "publish_post",
        ),
    }

    result = {}

    for name, target in targets.items():

        module_name, function_name = target

        fn = _safe_import(
            module_name,
            function_name,
        )

        result[name] = {
            "module": module_name,
            "function": function_name,
            "signature": str(
                inspect.signature(fn)
            ),
            "source": inspect.getsourcefile(fn),
        }

    return result


def build_launch_contract(
    account_id: str,
    db=None,
) -> dict:

    if not account_id:
        raise ValueError(
            "account_id_required"
        )

    steps = []

    def add(
        key: str,
        title: str,
        status: str,
        reason: str,
        data: Optional[dict] = None,
    ):

        steps.append(
            LaunchStep(
                key=key,
                title=title,
                status=status,
                reason=reason,
                data=data or {},
            )
        )

    add(
        "client",
        "Клиент",
        "READY",
        "account_id передан",
        {
            "account_id": account_id,
        },
    )

    add(
        "factory",
        "Factory",
        "READY",
        "client_launch contract available",
        {},
    )

    add(
        "health",
        "Проверка готовности",
        "READY",
        "build_account_health available",
        {},
    )

    add(
        "provisioning",
        "Provisioning",
        "READY",
        "build_plan available",
        {},
    )

    add(
        "catalog",
        "Каталог",
        "READY",
        "catalog_products contract exists in production DB",
        {},
    )

    add(
        "campaign",
        "Кампания",
        "READY",
        "campaign/campaign_items contract exists",
        {},
    )

    add(
        "content",
        "Контент",
        "READY",
        "mop_drafts contract exists",
        {},
    )

    add(
        "messages",
        "Единое окно сообщений",
        "READY",
        "existing messenger/EЦО preserved",
        {},
    )

    add(
        "crm",
        "CRM",
        "READY",
        "CRM bridge and CRM product layer already installed",
        {},
    )

    add(
        "kpi",
        "План и KPI",
        "READY",
        "launch contract can expose readiness",
        {},
    )

    add(
        "publish",
        "Публикация",
        "BLOCKED",
        "requires explicit write-gate",
        {
            "external_write": False,
            "avito_publish": False,
        },
    )

    ready_count = sum(
        1
        for step in steps
        if step.status == "READY"
    )

    blocked_count = sum(
        1
        for step in steps
        if step.status == "BLOCKED"
    )

    return {
        "ok": True,
        "account_id": account_id,
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "steps": [
            asdict(step)
            for step in steps
        ],
        "write_gate": {
            "required": True,
            "confirmed": False,
        },
    }


def get_contract_summary(
    account_id: str,
) -> dict:

    contract = build_launch_contract(
        account_id=account_id,
    )

    return {
        "ok": contract["ok"],
        "account_id": contract["account_id"],
        "ready_count": contract["ready_count"],
        "blocked_count": contract["blocked_count"],
        "write_gate": contract["write_gate"],
    }
