"""
Zero-touch onboarding -> BORIS Factory bridge.

Important:
- no publishing
- no bid/money actions
- no messages to customers
- bridge mode updates only onboarding-derived Factory state
- full mode reconciles only the current user's accessible accounts
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import bridge as factory_bridge
from app.factory import client_launch as factory_client_launch
from app.factory import onboarding_bridge
from app.factory import provisioning as factory_provisioning
from app.factory import state as factory_state

# ZERO_TOUCH_CENTRAL_SCHEDULE_RUNTIME_V2
# The bridge consumes fresh Factory readiness, including centralized systemd
# scheduler truth and paused-money KPI semantics; keeping this marker in
# backend/app also makes the canonical backend generation monitor roll API
# replicas when that root Factory contract changes.


def _user_id(cur):
    if isinstance(cur, dict):
        value = cur.get("id") or cur.get("user_id")
    else:
        value = (
            getattr(cur, "id", None)
            or getattr(cur, "user_id", None)
        )

    if value is None:
        raise ValueError("current user has no id")

    return int(value)


def account_ids_for_user(db, cur):
    uid = _user_id(cur)

    rows = db.execute(
        text("""
            SELECT DISTINCT account_id
            FROM (
                SELECT account_id
                FROM accounts
                WHERE owner_user_id=:uid

                UNION

                SELECT account_id
                FROM user_account_access
                WHERE user_id=:uid
                  AND can_view=TRUE
            ) x
            WHERE account_id IS NOT NULL
            ORDER BY account_id
        """),
        {"uid": uid},
    ).fetchall()

    return [str(r[0]) for r in rows]


def _sync_account_ids_for_user(db, cur):
    """
    A mutating onboarding sync requires one explicit CURRENT account.

    Read access to several accounts must never silently become write scope.
    Owner/staff contexts without a selected account fail closed instead of
    reconciling every visible tenant.
    """
    visible = account_ids_for_user(db, cur)
    current = (
        str(
            getattr(cur, "account_id", "")
            if not isinstance(cur, dict)
            else cur.get("account_id", "")
        )
        .strip()
    )

    if current:
        return (
            [current]
            if current in visible
            else []
        )

    return []


def _row_summary(account_id, obj):
    if not obj:
        return {
            "account_id": account_id,
            "state": "NO_FACTORY_STATE",
            "launch_status": None,
            "readiness_pct": 0,
            "human_questions": 0,
        }

    if hasattr(obj, "_mapping"):
        obj = dict(obj._mapping)
    elif not isinstance(obj, dict):
        try:
            obj = dict(obj)
        except Exception:
            obj = {}

    launch = (
        obj.get("launch_status")
        or obj.get("status")
    )

    readiness = (
        obj.get("readiness_pct")
        or obj.get("self_pct")
        or 0
    )

    human = obj.get("human_questions") or []

    if isinstance(human, int):
        human_count = human
    elif isinstance(human, (list, tuple, dict)):
        human_count = len(human)
    else:
        human_count = 0

    if launch == "ZERO_TOUCH_READY":
        state = "READY"
    elif launch == "BLOCKED_CORE":
        state = "NEEDS_REQUIRED_DATA"
    elif launch == "NO_PRODUCTS":
        state = "NO_PRODUCTS"
    else:
        state = str(launch or "PENDING")

    return {
        "account_id": account_id,
        "state": state,
        "launch_status": launch,
        "readiness_pct": int(readiness or 0),
        "human_questions": human_count,
    }


def _readiness_for_accounts(db, accounts):
    result = [
        _row_summary(
            account_id,
            factory_state.get(db, account_id),
        )
        for account_id in accounts
    ]

    ready = sum(
        1 for x in result
        if x["state"] == "READY"
    )

    blocked = sum(
        1 for x in result
        if x["state"] in {
            "NEEDS_REQUIRED_DATA",
            "NO_PRODUCTS",
            "BLOCKED_NO_OWNER",
        }
    )

    return {
        "status": "ok",
        "accounts": result,
        "total": len(result),
        "ready": ready,
        "blocked": blocked,
        "all_ready": bool(result) and ready == len(result),
    }


def readiness(cur):
    db = SessionLocal()

    try:
        accounts = account_ids_for_user(db, cur)
        return _readiness_for_accounts(db, accounts)
    finally:
        db.close()


def bridge_sync(cur):
    """
    Cheap sync after an onboarding step.

    Only refreshes D onboarding packets for accounts visible to this user.
    Each account is isolated by a savepoint so one broken tenant cannot roll
    back successful refreshes for sibling accounts. Readiness is computed in
    the SAME DB session: pool size=1 must never deadlock on nested SessionLocal.
    """
    db = SessionLocal()

    try:
        accounts = _sync_account_ids_for_user(
            db,
            cur,
        )

        if not accounts:
            return {
                "status": "blocked",
                "mode": "bridge",
                "reason": "current_account_required",
                "accounts_requested": 0,
                "updated": 0,
                "errors": [],
                "external_writes": False,
                "money_actions": False,
                "publish_actions": False,
            }

        updated = 0
        errors = []

        for account_id in accounts:
            try:
                with db.begin_nested():
                    onboarding_bridge.refresh_account(
                        db,
                        account_id,
                    )
                updated += 1
            except Exception as e:
                errors.append({
                    "account_id": account_id,
                    "error": (
                        type(e).__name__
                        + ": "
                        + str(e)[:260]
                    ),
                })

        db.commit()

        return {
            "status": "ok" if not errors else "degraded",
            "mode": "bridge",
            "updated": updated,
            "errors": errors,
            "readiness": _readiness_for_accounts(
                db,
                accounts,
            ),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _account_row(db, account_id):
    row = db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            company_niche,
            client_goal,
            billing_mode
        FROM accounts
        WHERE account_id=:a
    """), {"a": account_id}).mappings().first()

    return dict(row) if row else None


def _factory_launch_row(db, account_id):
    row = db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            launch_status,
            product_count,
            readiness_pct,
            human_questions,
            boris_actions,
            platform_actions,
            core_blocked,
            eligible_zero_touch,
            packet,
            source_fingerprint
        FROM factory_launch_state
        WHERE account_id=:a
    """), {"a": account_id}).mappings().first()

    return dict(row) if row else None


def _sync_account(db, account_id, e_packets):
    """
    Account-scoped zero-touch reconciliation.

    Reuses canonical Factory functions only:
      D: bridge.build_one/apply_all -> factory_launch_state
      onboarding enrichment -> factory_launch_state.packet.onboarding
      client launch projection -> factory_client_launch
      G: provisioning.build_plan/upsert_state -> factory_provisioning_state

    No external APIs, publishing, outbound messages, bid or money operations.
    Factory E remains owned by the scheduled Factory contour; its latest
    decision snapshot is read-only input here.
    """
    packet = factory_bridge.build_one(
        db,
        account_id,
    )

    if packet is None:
        raise RuntimeError(
            "account_not_found"
        )

    factory_bridge.apply_all(
        db,
        [packet],
    )

    onboarding_result = (
        onboarding_bridge.refresh_account(
            db,
            account_id,
        )
    )

    account = _account_row(
        db,
        account_id,
    )
    factory_row = _factory_launch_row(
        db,
        account_id,
    )

    if not account or not factory_row:
        raise RuntimeError(
            "factory_state_not_materialized"
        )

    launch_payload = (
        factory_client_launch.build_account_health(
            db,
            account,
            factory_row,
            (e_packets or {}).get(account_id),
        )
    )

    launch_apply = (
        factory_client_launch.apply_all(
            db,
            [launch_payload],
        )
    )

    provisioning_plan = (
        factory_provisioning.build_plan(
            db,
            factory_row,
        )
    )

    provisioning_outcome = (
        factory_provisioning.upsert_state(
            db,
            provisioning_plan,
        )
    )

    return {
        "account_id": account_id,
        "factory_launch_status":
            factory_row.get("launch_status"),
        "readiness_pct":
            int(factory_row.get("readiness_pct") or 0),
        "onboarding": onboarding_result,
        "client_launch_status":
            launch_payload.get("launch_status"),
        "client_launch_ready":
            bool(launch_payload.get("launch_ready")),
        "client_launch_apply":
            launch_apply,
        "provisioning_status":
            provisioning_plan.get(
                "provisioning_status"
            ),
        "provisioning_outcome":
            provisioning_outcome,
        "ready_internal":
            bool(
                provisioning_plan.get(
                    "ready_internal"
                )
            ),
        "ready_schedule":
            bool(
                provisioning_plan.get(
                    "ready_schedule"
                )
            ),
    }


def full_sync(cur):
    """
    Full account-scoped internal Factory reconciliation after onboarding.

    Previous implementation launched Factory F globally and therefore one
    client's click could rewrite Factory state for the entire fleet. This path
    is tenant-scoped and fail-closed: only accounts owned/granted to the current
    user are touched, one savepoint per account.

    Factory E/bootstrap keeps its existing automatic timer ownership. We read
    the latest E decision snapshot but never become a second E writer.
    """
    uid = _user_id(cur)
    db = SessionLocal()

    try:
        accounts = _sync_account_ids_for_user(
            db,
            cur,
        )

        if not accounts:
            return {
                "status": "blocked",
                "mode": "full_account_scoped",
                "user_id": uid,
                "reason": "current_account_required",
                "accounts_requested": 0,
                "updated": 0,
                "results": [],
                "errors": [],
                "global_factory_run": False,
                "external_writes": False,
                "money_actions": False,
                "publish_actions": False,
                "automatic_followup": {
                    "factory_e_owner": "boris-factory.timer",
                    "owner_action_required": True,
                },
            }

        e_packets = (
            factory_client_launch.load_e_packets()
        )

        results = []
        errors = []

        for account_id in accounts:
            try:
                with db.begin_nested():
                    results.append(
                        _sync_account(
                            db,
                            account_id,
                            e_packets,
                        )
                    )
            except Exception as e:
                errors.append({
                    "account_id": account_id,
                    "error": (
                        type(e).__name__
                        + ": "
                        + str(e)[:360]
                    ),
                })

        db.commit()

        return {
            "status": (
                "ok"
                if not errors
                else "error"
                if accounts and not results
                else "degraded"
            ),
            "mode": "full_account_scoped",
            "user_id": uid,
            "accounts_requested": len(accounts),
            "updated": len(results),
            "results": results,
            "errors": errors,
            "global_factory_run": False,
            "external_writes": False,
            "money_actions": False,
            "publish_actions": False,
            "automatic_followup": {
                "factory_e_owner":
                    "boris-factory.timer",
                "owner_action_required":
                    False,
            },
            "readiness": _readiness_for_accounts(
                db,
                accounts,
            ),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def live_launch_contract(account_id):
    """
    Truthful read-only launch contract for UI/API.

    Never reports READY merely because a Python function/table exists. Every
    step is derived from persisted account-specific Factory evidence.
    """
    aid = str(account_id or "").strip()

    if not aid:
        raise ValueError(
            "account_id_required"
        )

    db = SessionLocal()

    try:
        account = db.execute(text("""
            SELECT account_id,owner_user_id,name
            FROM accounts
            WHERE account_id=:a
        """), {"a": aid}).mappings().first()

        if not account:
            return {
                "ok": False,
                "account_id": aid,
                "error": "account_not_found",
            }

        factory = factory_state.get(
            db,
            aid,
        )

        launch = db.execute(text("""
            SELECT
                launch_status,
                launch_ready,
                readiness_pct,
                pending_decisions,
                approved_decisions,
                rejected_decisions,
                required_schedules,
                active_schedules,
                blockers,
                health,
                updated_at
            FROM factory_client_launch
            WHERE account_id=:a
        """), {"a": aid}).mappings().first()

        provisioning = db.execute(text("""
            SELECT
                provisioning_status,
                eligible_zero_touch,
                product_count,
                ready_internal,
                ready_schedule,
                blockers,
                updated_at
            FROM factory_provisioning_state
            WHERE account_id=:a
        """), {"a": aid}).mappings().first()

        bootstrap = db.execute(text("""
            SELECT
                status,
                action_count,
                executable_internal,
                blocked_external,
                updated_at
            FROM factory_bootstrap_state
            WHERE account_id=:a
        """), {"a": aid}).mappings().first()

        factory = dict(factory) if factory else None
        launch = dict(launch) if launch else None
        provisioning = (
            dict(provisioning)
            if provisioning
            else None
        )
        bootstrap = (
            dict(bootstrap)
            if bootstrap
            else None
        )

        packet = (
            (factory or {}).get("packet")
            or {}
        )
        if not isinstance(packet, dict):
            packet = {}

        onboarding = (
            packet.get("onboarding")
            or {}
        )
        products = (
            packet.get("products")
            or []
        )
        if not isinstance(products, list):
            products = []

        launch_blockers = (
            (launch or {}).get("blockers")
            or []
        )
        if not isinstance(
            launch_blockers,
            list,
        ):
            launch_blockers = []

        provisioning_blockers = (
            (provisioning or {}).get(
                "blockers"
            )
            or []
        )
        if not isinstance(
            provisioning_blockers,
            list,
        ):
            provisioning_blockers = []

        human_questions = (
            packet.get("human_questions")
            or []
        )
        if not isinstance(
            human_questions,
            list,
        ):
            human_questions = []

        owner_items = []

        if account["owner_user_id"] is None:
            owner_items.append({
                "code": "NO_OWNER",
                "action":
                    "Назначить владельца аккаунта",
            })

        owner_items.extend(
            {
                "code":
                    str(
                        item.get("code")
                        or item.get("field")
                        or "CLIENT_INPUT"
                    ),
                "action":
                    str(
                        item.get("action")
                        or item.get("title")
                        or "Нужны данные клиента"
                    ),
            }
            for item in launch_blockers
            if isinstance(item, dict)
            and item.get("human_required")
        )

        owner_items.extend(
            {
                "code":
                    str(
                        item.get("field")
                        or item.get("kind")
                        or "CLIENT_INPUT"
                    ),
                "action":
                    "Нужны данные/решение клиента",
            }
            for item in provisioning_blockers
            if isinstance(item, dict)
            and str(
                item.get("kind")
                or ""
            ) in {"CLIENT", "PRODUCT"}
        )

        owner_items.extend(
            {
                "code":
                    str(
                        item.get("field")
                        or "CLIENT_INPUT"
                    ),
                "action":
                    str(
                        item.get("screen")
                        or item.get("action")
                        or "Нужны данные клиента"
                    ),
            }
            for item in human_questions
            if isinstance(item, dict)
            and item.get("severity")
                == "blocker"
        )

        # ZERO_TOUCH_OWNER_ESCALATION_TRUTH_V1
        # Do not turn every durable Factory confirmation into an owner blocker.
        # Real human blockers are already surfaced above from launch blockers,
        # provisioning CLIENT/PRODUCT blockers and blocker-severity questions.
        # Optional preferences and internal BORIS confirmations remain auditable
        # without changing READY_INTERNAL into NEEDS_INPUT.

        # Same business question is often present in Factory C, launch
        # projection and provisioning. Surface one owner action per code,
        # preferring the later/more specific human-facing wording.
        _owner_by_code = {}
        for item in owner_items:
            code = str(
                item.get("code")
                or "CLIENT_INPUT"
            )
            _owner_by_code[code] = item
        owner_items = list(
            _owner_by_code.values()
        )

        steps = []

        def add_step(
            key,
            title,
            status,
            reason,
            data=None,
        ):
            steps.append({
                "key": key,
                "title": title,
                "status": status,
                "reason": reason,
                "data": data or {},
            })

        if account["owner_user_id"] is None:
            add_step(
                "client",
                "Клиент",
                "BLOCKED",
                "Не назначен владелец аккаунта",
            )
        else:
            add_step(
                "client",
                "Клиент",
                "READY",
                "Аккаунт существует и владелец назначен",
                {
                    "owner_user_id":
                        account["owner_user_id"],
                },
            )

        if onboarding.get(
            "ready_for_factory"
        ) is True:
            add_step(
                "onboarding",
                "Онбординг",
                "READY",
                "Обязательные данные для Factory собраны",
                {
                    "stage":
                        onboarding.get("stage"),
                },
            )
        elif factory is None:
            add_step(
                "onboarding",
                "Онбординг",
                "WAITING",
                "Factory ещё не сформировал состояние",
            )
        else:
            add_step(
                "onboarding",
                "Онбординг",
                "BLOCKED",
                "Не хватает обязательных данных для автоматического запуска",
                {
                    "stage":
                        onboarding.get("stage"),
                    "status":
                        onboarding.get("status"),
                },
            )

        factory_status = (
            (factory or {}).get(
                "launch_status"
            )
        )
        if factory_status == (
            "ZERO_TOUCH_READY"
        ):
            add_step(
                "factory",
                "Factory",
                "READY",
                "Factory подтверждает zero-touch готовность",
                {
                    "readiness_pct":
                        int(
                            (factory or {}).get(
                                "readiness_pct"
                            )
                            or 0
                        ),
                },
            )
        elif factory is None:
            add_step(
                "factory",
                "Factory",
                "WAITING",
                "Factory state ещё не создан",
            )
        elif factory_status in {
            "BLOCKED_CORE",
            "BLOCKED_NO_OWNER",
            "NO_PRODUCTS",
        }:
            add_step(
                "factory",
                "Factory",
                "BLOCKED",
                str(factory_status),
                {
                    "readiness_pct":
                        int(
                            (factory or {}).get(
                                "readiness_pct"
                            )
                            or 0
                        ),
                    "products": products,
                },
            )
        else:
            add_step(
                "factory",
                "Factory",
                "WAITING",
                str(
                    factory_status
                    or "PENDING"
                ),
            )

        if (
            launch
            and launch.get("launch_ready")
        ):
            add_step(
                "launch",
                "Внутренний запуск",
                "READY",
                str(
                    launch.get(
                        "launch_status"
                    )
                ),
                {
                    "readiness_pct":
                        int(
                            launch.get(
                                "readiness_pct"
                            )
                            or 0
                        ),
                    "active_schedules":
                        int(
                            launch.get(
                                "active_schedules"
                            )
                            or 0
                        ),
                    "required_schedules":
                        int(
                            launch.get(
                                "required_schedules"
                            )
                            or 0
                        ),
                },
            )
        elif launch:
            add_step(
                "launch",
                "Внутренний запуск",
                (
                    "BLOCKED"
                    if launch_blockers
                    else "WAITING"
                ),
                str(
                    launch.get(
                        "launch_status"
                    )
                    or "PENDING"
                ),
                {
                    "blockers":
                        launch_blockers,
                },
            )
        else:
            add_step(
                "launch",
                "Внутренний запуск",
                "WAITING",
                "Launch projection ещё не создан",
            )

        if (
            provisioning
            and provisioning.get(
                "ready_internal"
            )
        ):
            add_step(
                "provisioning",
                "Внутренняя настройка",
                "READY",
                str(
                    provisioning.get(
                        "provisioning_status"
                    )
                ),
                {
                    "product_count":
                        int(
                            provisioning.get(
                                "product_count"
                            )
                            or 0
                        ),
                },
            )
        elif provisioning:
            pstatus = str(
                provisioning.get(
                    "provisioning_status"
                )
                or "WAITING"
            )
            add_step(
                "provisioning",
                "Внутренняя настройка",
                (
                    "BLOCKED"
                    if pstatus.startswith(
                        "BLOCKED"
                    )
                    else "WAITING"
                ),
                pstatus,
                {
                    "blockers":
                        provisioning_blockers,
                },
            )
        else:
            add_step(
                "provisioning",
                "Внутренняя настройка",
                "WAITING",
                "Provisioning state ещё не создан",
            )

        if (
            provisioning
            and provisioning.get(
                "ready_schedule"
            )
        ):
            add_step(
                "automation",
                "Автоматическая работа",
                "READY",
                "Нужные внутренние расписания активны",
            )
        else:
            add_step(
                "automation",
                "Автоматическая работа",
                "WAITING",
                (
                    "Внутренний запуск ещё не доказал активное расписание"
                ),
            )

        if (
            bootstrap
            and str(
                bootstrap.get("status")
                or ""
            ).startswith("READY")
        ):
            add_step(
                "bootstrap",
                "Автоподготовка",
                "READY",
                str(
                    bootstrap.get(
                        "status"
                    )
                ),
                {
                    "action_count":
                        int(
                            bootstrap.get(
                                "action_count"
                            )
                            or 0
                        ),
                },
            )
        else:
            add_step(
                "bootstrap",
                "Автоподготовка",
                "WAITING",
                (
                    "Factory E обновляется автоматически системным таймером"
                ),
            )

        add_step(
            "publish",
            "Внешняя публикация",
            "GATED",
            "Отдельный write-gate; zero-touch запуск не публикует и не тратит деньги",
            {
                "external_write": False,
                "money_action": False,
            },
        )

        ready_count = sum(
            1 for x in steps
            if x["status"] == "READY"
        )
        blocked_count = sum(
            1 for x in steps
            if x["status"] == "BLOCKED"
        )
        waiting_count = sum(
            1 for x in steps
            if x["status"] == "WAITING"
        )
        gated_count = sum(
            1 for x in steps
            if x["status"] == "GATED"
        )

        owner_action_required = bool(
            owner_items
        )

        if owner_action_required:
            overall = "NEEDS_INPUT"
        elif blocked_count or waiting_count:
            overall = "WORKING"
        else:
            overall = "READY_INTERNAL"

        return {
            "ok": True,
            "account_id": aid,
            "overall_state": overall,
            "owner_action_required":
                owner_action_required,
            "owner_items": owner_items,
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "waiting_count": waiting_count,
            "gated_count": gated_count,
            "steps": steps,
            "write_gate": {
                "required": True,
                "confirmed": False,
                "external_write": False,
                "money_action": False,
            },
            "automatic_followup": {
                "factory_e_timer":
                    "boris-factory.timer",
                "owner_operator_required":
                    False,
            },
            "truth": {
                "factory_launch_status":
                    factory_status,
                "factory_readiness_pct":
                    int(
                        (factory or {}).get(
                            "readiness_pct"
                        )
                        or 0
                    ),
                "client_launch_status":
                    (launch or {}).get(
                        "launch_status"
                    ),
                "provisioning_status":
                    (provisioning or {}).get(
                        "provisioning_status"
                    ),
                "bootstrap_status":
                    (bootstrap or {}).get(
                        "status"
                    ),
                "products": products,
            },
        }
    finally:
        db.close()
