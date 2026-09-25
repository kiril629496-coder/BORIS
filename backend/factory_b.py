# -*- coding: utf-8 -*-
"""
BORIS Factory B
Minimal-human / zero-touch readiness.

READ ONLY.

Не меняет:
- Client Config;
- accounts;
- storage;
- entitlement;
- cron/systemd;
- Avito/API;
- Telegram;
- какие-либо бизнес-данные.

Factory A классифицирует источник значения.
Factory B отвечает на продуктовый вопрос:
    ЧТО ФИЗИЧЕСКИ НУЖНО СПРОСИТЬ У ЧЕЛОВЕКА ДО ЗАПУСКА?
"""

import os
import sys
import json
import collections

RUNTIME_BACKEND = os.environ.get("BORIS_RUNTIME_BACKEND_ROOT", "/root/BORIS/backend")
sys.path.insert(0, RUNTIME_BACKEND)
os.chdir("/root/BORIS/backend")

from sqlalchemy import text as T
from app.db.session import SessionLocal
from app.client_config import schema as S
import factory_a as A


# ----------------------------------------------------------------------
# VERDICTS
# ----------------------------------------------------------------------

AUTO = "AUTO"
HUMAN_BLOCKER = "HUMAN_BLOCKER"
HUMAN_CONFIRM = "HUMAN_CONFIRM"
HUMAN_OPTIONAL = "HUMAN_OPTIONAL"
INTERNAL_BLOCKER = "INTERNAL_BLOCKER"
NOT_REQUIRED = "NOT_REQUIRED"

VERDICTS = (
    AUTO,
    HUMAN_BLOCKER,
    HUMAN_CONFIRM,
    HUMAN_OPTIONAL,
    INTERNAL_BLOCKER,
    NOT_REQUIRED,
)


# ----------------------------------------------------------------------
# HUMAN POLICY
#
# Это НЕ вторая схема Client Config.
# Это правило взаимодействия:
# кто должен что сделать ДО запуска.
# ----------------------------------------------------------------------

def interaction_verdict(row):
    cls = row.get("cls")
    severity = row.get("severity")
    owner = row.get("owner")
    confirm = bool(row.get("needs_confirmation"))
    field = row.get("field")

    # Уже известно / BORIS сам достал / перенёс / назначил.
    if cls in (
        A.KNOWN,
        A.DERIVE,
        A.AUTO_MIGRATE,
        A.OPERATIONAL,
    ):
        if cls == A.DERIVE and confirm:
            # Значение уже есть.
            # Подтверждение полезно, но запуск блокировать не должно.
            return HUMAN_CONFIRM

        return AUTO

    if cls == A.NOT_REQUIRED:
        return NOT_REQUIRED

    # Конфликт нельзя угадывать.
    if cls == A.CONFLICT:
        if owner == "client":
            return HUMAN_BLOCKER
        return INTERNAL_BLOCKER

    # ASK_CLIENT:
    # blocker = реально спросить до старта;
    # required_soon/improvement = можно после запуска.
    if cls == A.ASK:
        if severity == A.BLOCKER:
            return HUMAN_BLOCKER

        if severity == A.SOON:
            return HUMAN_OPTIONAL

        return HUMAN_OPTIONAL

    # UNKNOWN — это особенно важно:
    # UNKNOWN продукта/тарифа нельзя превращать в вопрос клиенту.
    if cls == A.UNKNOWN:
        if owner in ("tariff", "platform", "owner"):
            return INTERNAL_BLOCKER

        if owner == "client" and severity == A.BLOCKER:
            return HUMAN_BLOCKER

        if owner == "client":
            return HUMAN_OPTIONAL

        return INTERNAL_BLOCKER

    return INTERNAL_BLOCKER


def enrich(row):
    x = dict(row)
    x["interaction"] = interaction_verdict(row)
    return x


def human_required(rows):
    return [
        r for r in rows
        if r["interaction"] == HUMAN_BLOCKER
    ]


def human_confirm(rows):
    return [
        r for r in rows
        if r["interaction"] == HUMAN_CONFIRM
    ]


def human_optional(rows):
    return [
        r for r in rows
        if r["interaction"] == HUMAN_OPTIONAL
    ]


def internal_blockers(rows):
    return [
        r for r in rows
        if r["interaction"] == INTERNAL_BLOCKER
    ]


def auto_rows(rows):
    return [
        r for r in rows
        if r["interaction"] == AUTO
    ]


def launch_screen(field):
    """
    Реальный mandatory screen.
    Не копируем старую схему экранов механически.
    """
    if field == "channels.avito":
        return "connect_avito"

    if field.startswith("kpi."):
        return "business_goals"

    if field.startswith("automation.") or field.startswith("limits."):
        return "automation_permissions"

    if field.startswith("profile."):
        return "business_profile"

    return "required_input"


def mandatory_screens(rows):
    return sorted(set(
        launch_screen(r["field"])
        for r in human_required(rows)
    ))


def post_launch_prompts(rows):
    """
    То, что можно спросить ПОСЛЕ старта:
    в интерфейсе, одним кликом, без остановки BORIS.
    """
    out = []

    for r in human_confirm(rows):
        out.append({
            "field": r["field"],
            "type": "confirm",
            "candidate": r.get("candidate_value"),
            "source": r.get("candidate_source"),
            "confidence": r.get("confidence"),
        })

    for r in human_optional(rows):
        out.append({
            "field": r["field"],
            "type": "optional",
            "candidate": r.get("candidate_value"),
            "source": r.get("candidate_source"),
            "confidence": r.get("confidence"),
        })

    return out


def internal_tasks(rows):
    """
    Не показываем клиенту как вопрос.
    Это задача Factory / тарифа / платформы.
    """
    out = []

    for r in internal_blockers(rows):
        out.append({
            "field": r["field"],
            "owner": r.get("owner"),
            "reason": r.get("reason"),
            "severity": r.get("severity"),
        })

    return out


def launch_verdict(rows):
    mandatory = human_required(rows)
    internal = internal_blockers(rows)

    if mandatory:
        return "WAIT_HUMAN"

    if internal:
        # Клиента не спрашиваем.
        # Но автоматический запуск конкретного зависимого продукта
        # может ждать внутреннего разрешения тарифа.
        return "ZERO_TOUCH_WITH_INTERNAL_TASKS"

    return "ZERO_TOUCH_READY"


def product_launch(rows, products):
    """
    Readiness по фактически подключённому продукту
    именно с точки зрения human interaction.
    """
    out = {}

    for product in A.PRODUCTS:
        if product != "platform" and products.get(product) is not True:
            out[product] = "not_connected"
            continue

        rel = [
            r for r in rows
            if product in r.get("required_for", [])
            and r["interaction"] != NOT_REQUIRED
        ]

        human = [
            r for r in rel
            if r["interaction"] == HUMAN_BLOCKER
        ]

        internal = [
            r for r in rel
            if r["interaction"] == INTERNAL_BLOCKER
        ]

        if human:
            out[product] = "wait_human:" + ",".join(
                r["field"] for r in human
            )
        elif internal:
            out[product] = "wait_internal:" + ",".join(
                r["field"] for r in internal
            )
        else:
            out[product] = "ready"

    return out


def inspect_account(db, account_id):
    products, original = A.run_account(db, account_id)
    rows = [enrich(r) for r in original]

    return {
        "account_id": account_id,
        "products": products,
        "rows": rows,
        "human_required": human_required(rows),
        "human_confirm": human_confirm(rows),
        "human_optional": human_optional(rows),
        "internal_tasks": internal_tasks(rows),
        "mandatory_screens": mandatory_screens(rows),
        "post_launch": post_launch_prompts(rows),
        "launch": launch_verdict(rows),
        "product_launch": product_launch(rows, products),
    }


def print_account(v):
    print(
        "%-45s %-31s mandatory=%d screens=%s internal=%d post=%d"
        % (
            v["account_id"][:45],
            v["launch"],
            len(v["human_required"]),
            v["mandatory_screens"],
            len(v["internal_tasks"]),
            len(v["post_launch"]),
        )
    )


def readiness_plan(report):
    """Translate fresh factory_readiness output to the stable B->C contract.

    The minimal-human report below is a newer diagnostic view. Factory C still
    owns the persistent launch-state contract and expects the historical plan
    keys/status vocabulary. Keep the two views in parallel instead of making C
    consume diagnostic-only statuses.
    """
    import factory_readiness as RA

    raw_products = report.get("products") or []
    products = list(raw_products.keys()) if isinstance(raw_products, dict) else list(raw_products)
    actual_products = [p for p in products if p != "platform"]
    rows = report.get("rows") or {}

    actions = []
    for field, raw in rows.items():
        raw = raw or {}
        cls = raw.get("class")
        sev = raw.get("severity")
        ev = raw.get("evidence") or {}
        if cls in (RA.KNOWN, RA.NOT_REQUIRED):
            continue
        if cls == RA.DERIVE:
            actor, action = "BORIS", "DERIVE"
        elif cls == RA.AUTO_MIGRATE:
            actor, action = "BORIS", "MIGRATE"
        elif cls == RA.OPERATIONAL:
            actor, action = "PLATFORM", "ASSIGN"
        elif cls == RA.ASK:
            actor, action = "CLIENT", "ASK"
        else:
            actor, action = "PLATFORM", "REVIEW"
        item = {"field": field, "actor": actor, "action": action,
                "severity": sev, "evidence": ev}
        if actor == "CLIENT":
            item["screen"] = RA.SCREENS.get(field, "Прочее")
        actions.append(item)

    if not actual_products:
        launch_status = "NO_PRODUCTS"
    elif report.get("blockers"):
        launch_status = "NEEDS_CLIENT"
    elif any((raw or {}).get("severity") == RA.BLOCKER and
             (raw or {}).get("class") == RA.OPERATIONAL
             for raw in rows.values()):
        launch_status = "NEEDS_PLATFORM"
    else:
        launch_status = "ZERO_TOUCH_READY"

    core = []
    for field in ("channels.avito", "access.roles"):
        raw = rows.get(field)
        if raw and raw.get("class") not in (RA.KNOWN, RA.DERIVE):
            core.append({"field": field, "class": raw.get("class"),
                         "evidence": raw.get("evidence")})

    return {
        "account_id": report["account_id"],
        "products": products,
        "product_count": len(actual_products),
        "launch_status": launch_status,
        "self_pct": report.get("self_pct", 0),
        "human_questions": [a for a in actions if a["actor"] == "CLIENT"],
        "platform_actions": [a for a in actions if a["actor"] == "PLATFORM"],
        "boris_actions": [a for a in actions if a["actor"] == "BORIS"],
        "core_blockers": core,
    }


def main():
    db = SessionLocal()

    try:
        accounts = [
            r[0]
            for r in db.execute(T("""
                SELECT DISTINCT c.account_id
                FROM client_config c
                WHERE c.account_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM reliability_kill_switches r
                       WHERE r.account_id=c.account_id AND r.blocked=true
                         AND r.module IN ('actions','background')
                  )
                ORDER BY c.account_id
            """)).fetchall()
        ]

        print("======================================================================")
        print(" FACTORY B — MINIMAL HUMAN")
        print("======================================================================")
        print("accounts=%d fields=%d" % (len(accounts), len(S.FIELDS)))

        results = []
        errors = []

        interaction_counts = collections.Counter()
        launch_counts = collections.Counter()
        mandatory_screen_counts = collections.Counter()
        mandatory_field_counts = collections.Counter()
        confirm_field_counts = collections.Counter()
        optional_field_counts = collections.Counter()
        internal_field_counts = collections.Counter()

        product_counts = collections.defaultdict(collections.Counter)

        for account_id in accounts:
            try:
                v = inspect_account(db, account_id)
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass

                errors.append((account_id, str(e)[:200]))
                continue

            results.append(v)
            launch_counts[v["launch"]] += 1
            mandatory_screen_counts[len(v["mandatory_screens"])] += 1

            for row in v["rows"]:
                interaction_counts[row["interaction"]] += 1

            for row in v["human_required"]:
                mandatory_field_counts[row["field"]] += 1

            for row in v["human_confirm"]:
                confirm_field_counts[row["field"]] += 1

            for row in v["human_optional"]:
                optional_field_counts[row["field"]] += 1

            for task in v["internal_tasks"]:
                internal_field_counts[task["field"]] += 1

            for product, state in v["product_launch"].items():
                bucket = state.split(":", 1)[0]
                product_counts[product][bucket] += 1

        print()
        print("=== 1. INTERACTION CELLS ===")
        total = sum(interaction_counts.values())

        for verdict in VERDICTS:
            n = interaction_counts[verdict]
            print(
                "  %-20s %4d  %5.1f%%"
                % (
                    verdict,
                    n,
                    100.0 * n / max(1, total),
                )
            )

        print()
        print("=== 2. LAUNCH VERDICT ===")
        for k, n in sorted(launch_counts.items()):
            print("  %-32s %d" % (k, n))

        print()
        print("=== 3. ОБЯЗАТЕЛЬНЫЕ ЭКРАНЫ ДО ЗАПУСКА ===")
        for n, cnt in sorted(mandatory_screen_counts.items()):
            print("  %d mandatory screens -> %d аккаунтов" % (n, cnt))

        avg = (
            sum(k * v for k, v in mandatory_screen_counts.items())
            / float(max(1, sum(mandatory_screen_counts.values())))
        )

        print("  AVERAGE MANDATORY SCREENS = %.2f" % avg)

        print()
        print("=== 4. ЧТО РЕАЛЬНО НУЖНО СПРОСИТЬ ДО СТАРТА ===")
        if mandatory_field_counts:
            for field, n in mandatory_field_counts.most_common():
                print("  %-35s %d аккаунтов" % (field, n))
        else:
            print("  НИЧЕГО")

        print()
        print("=== 5. ЧТО МОЖНО ПОДТВЕРДИТЬ ПОСЛЕ СТАРТА ===")
        if confirm_field_counts:
            for field, n in confirm_field_counts.most_common():
                print("  %-35s %d" % (field, n))
        else:
            print("  нет")

        print()
        print("=== 6. НЕОБЯЗАТЕЛЬНЫЕ НАСТРОЙКИ ПОСЛЕ СТАРТА ===")
        if optional_field_counts:
            for field, n in optional_field_counts.most_common():
                print("  %-35s %d" % (field, n))
        else:
            print("  нет")

        print()
        print("=== 7. ВНУТРЕННИЕ ДОЛГИ — КЛИЕНТА НЕ СПРАШИВАТЬ ===")
        if internal_field_counts:
            for field, n in internal_field_counts.most_common():
                print("  %-35s %d" % (field, n))
        else:
            print("  нет")

        print()
        print("=== 8. PRODUCT ZERO-TOUCH READINESS ===")
        for product in A.PRODUCTS:
            print("  %-12s %s" % (product, dict(product_counts[product])))

        print()
        print("=== 9. ВСЕ 30 АККАУНТОВ ===")
        for v in results:
            print_account(v)

        print()
        print("=== 10. ПЯТЬ КОНТРОЛЬНЫХ КЛИЕНТОВ ===")

        wanted = (
            "prodazha_bytovok_25677",
            "3411770_94346",
            "garik_plitka_mo_58647",
            "pbi_artashes_plitka_mo_83993",
            "evgeniy_peregorodki_12447",
        )

        by_id = {
            v["account_id"]: v
            for v in results
        }

        for account_id in wanted:
            v = by_id.get(account_id)

            if not v:
                print("  %s -> нет Client Config draft" % account_id)
                continue

            print()
            print("  ACCOUNT:", account_id)
            print("    launch          :", v["launch"])
            print("    mandatory       :", [
                r["field"] for r in v["human_required"]
            ])
            print("    screens         :", v["mandatory_screens"])
            print("    post-launch     :", [
                x["field"] for x in v["post_launch"]
            ])
            print("    internal tasks  :", [
                x["field"] for x in v["internal_tasks"]
            ])
            print("    products        :", v["product_launch"])

        print()
        print("=== 11. ZERO-TOUCH НОВЫЙ КЛИЕНТ БЕЗ ДАННЫХ ===")

        probe = inspect_account(
            db,
            "__factory_b_probe__",
        )

        print("  launch         =", probe["launch"])
        print("  mandatory      =", [
            r["field"] for r in probe["human_required"]
        ])
        print("  screens        =", probe["mandatory_screens"])
        print("  post_launch    =", [
            x["field"] for x in probe["post_launch"]
        ])
        print("  internal_tasks =", [
            x["field"] for x in probe["internal_tasks"]
        ])

        print()
        print("=== 12. ERRORS ===")
        print("  count=%d" % len(errors))

        for account_id, err in errors:
            print("  %s -> %s" % (account_id, err))

        report = {
            "accounts": len(accounts),
            "processed": len(results),
            "errors": errors,
            "interaction_counts": dict(interaction_counts),
            "launch_counts": dict(launch_counts),
            "mandatory_screen_counts": dict(mandatory_screen_counts),
            "mandatory_fields": dict(mandatory_field_counts),
            "confirmation_fields": dict(confirm_field_counts),
            "optional_fields": dict(optional_field_counts),
            "internal_fields": dict(internal_field_counts),
            "products": {
                k: dict(v)
                for k, v in product_counts.items()
            },
            "accounts_detail": results,
            "probe": probe,
        }

        with open(
            "/root/BORIS/backend/run/factory_b_report.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                report,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        # Stable B->C artifact. factory_d_runner always runs factory_readiness
        # immediately before B, so this source is fresh for the same cycle.
        with open("/root/BORIS/backend/run/factory_a.json", encoding="utf-8") as fh:
            readiness_source = json.load(fh)
        plans = [readiness_plan(r) for r in readiness_source]
        owners = {r[0]: r[1] for r in db.execute(T(
            "SELECT account_id, owner_user_id FROM accounts"
        )).fetchall()}
        for plan in plans:
            plan["owner_user_id"] = owners.get(plan["account_id"])
        with open("/root/BORIS/backend/run/factory_b_plan.json", "w", encoding="utf-8") as fh:
            json.dump(plans, fh, ensure_ascii=False, indent=2, default=str)

        print()
        print("report=/root/BORIS/backend/run/factory_b_report.json")
        print("plan=/root/BORIS/backend/run/factory_b_plan.json")

        if errors:
            return 2

        if len(results) != len(accounts):
            return 3

        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# BORIS COMPATIBILITY ADAPTER
# ---------------------------------------------------------------------------
# bridge.py historically calls:
#
#     plan = B.make_plan(report)
#
# Current factory_b exposes the newer primitives:
#     interaction_verdict
#     human_required
#     human_confirm
#     human_optional
#     internal_tasks
#     mandatory_screens
#     post_launch_prompts
#     launch_verdict
#     product_launch
#
# This adapter translates the current account_report() shape into the
# current factory_b plan shape without DB writes or external calls.
# ---------------------------------------------------------------------------

def make_plan(report):
    """
    Compatibility adapter:
    factory_readiness.account_report(...)
        ->
    factory_c.build_packet(...)

    Handles the REAL report contract:
        products = list
        rows = dict
    """

    if not isinstance(report, dict):
        raise TypeError("make_plan: report must be dict")

    # app.factory.bridge consumes this public adapter as well. Keep one
    # authoritative translation for both file pipeline B->C and bridge calls.
    return readiness_plan(report)

    account_id = report.get("account_id")

    ###########################################################################
    # REAL PRODUCTS CONTRACT
    #
    # account_report() returns products_of(...), which is a list.
    # factory_b.product_launch() expects a mapping:
    #     product_name -> True/False
    ###########################################################################

    raw_products = report.get("products") or []

    if isinstance(raw_products, dict):
        products = dict(raw_products)

    elif isinstance(raw_products, (list, tuple, set)):
        products = {
            str(product): True
            for product in raw_products
            if product is not None
        }

    else:
        raise TypeError(
            "make_plan: unsupported products type: "
            + type(raw_products).__name__
        )

    ###########################################################################
    # ROWS
    ###########################################################################

    source_rows = report.get("rows") or {}

    if not isinstance(source_rows, dict):
        raise TypeError(
            "make_plan: report.rows must be dict"
        )

    rows = []

    for field, raw in source_rows.items():

        raw = dict(raw or {})

        evidence = raw.get("evidence")
        if evidence is None:
            evidence = {}

        row = {
            "field": field,
            "cls": raw.get("class"),
            "severity": raw.get("severity"),
            "evidence": evidence,
        }

        if isinstance(evidence, dict):

            for key in (
                "owner",
                "needs_confirmation",
                "candidate_value",
                "candidate_source",
                "confidence",
                "reason",
            ):
                if key in evidence:
                    row[key] = evidence[key]

        rows.append(enrich(row))

    ###########################################################################
    # CURRENT FACTORY_B DECISIONS
    ###########################################################################

    human_required_rows = human_required(rows)
    human_confirm_rows = human_confirm(rows)
    human_optional_rows = human_optional(rows)

    internal_task_rows = internal_tasks(rows)
    screens = mandatory_screens(rows)
    post_launch = post_launch_prompts(rows)

    launch = launch_verdict(rows)
    product_launch_status = product_launch(rows, products)

    ###########################################################################
    # FACTORY_C CONTRACT
    ###########################################################################

    if launch == "ZERO_TOUCH_READY":
        launch_status = "READY"

    elif launch == "ZERO_TOUCH_WITH_INTERNAL_TASKS":
        launch_status = "INTERNAL_TASKS"

    elif launch == "WAIT_HUMAN":
        launch_status = "WAIT_HUMAN"

    else:
        launch_status = str(launch)

    plan = {
        "account_id": account_id,

        "products": products,
        "product_count": sum(
            1 for value in products.values()
            if value is True
        ),

        "rows": rows,

        "human_required": human_required_rows,
        "human_confirm": human_confirm_rows,
        "human_optional": human_optional_rows,

        "internal_tasks": internal_task_rows,
        "mandatory_screens": screens,
        "post_launch": post_launch,

        "launch": launch,
        "launch_status": launch_status,

        "product_launch": product_launch_status,

        # Preserve source metrics from readiness.
        "required": report.get("required", 0),
        "known": report.get("known", 0),
        "boris_can": report.get("boris_can", 0),
        "ask": report.get("ask", []),
        "screens_source": report.get("screens", []),
        "blockers": report.get("blockers", []),
        "self_pct": report.get("self_pct", 0),
    }

    return plan
