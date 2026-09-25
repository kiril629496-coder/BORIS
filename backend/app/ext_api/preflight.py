# -*- coding: utf-8 -*-
"""Пред-полётные проверки. Ни одно production-действие не запускается,
пока весь список не зелёный. FAIL → job.status = blocked.
"""
from . import db, auth, registry, discovery, costs
from .errors import ApiError


def _ok(name, detail=None):
    return {"check": name, "ok": True, "detail": detail}


def _fail(name, code, detail):
    return {"check": name, "ok": False, "code": code, "detail": detail}


def executor_state(op_name):
    """(available, executor_spec, отчёт). native-операции доступны без внешнего
    исполнителя, но и они помечают, какой штатный сервис нашли."""
    spec = registry.get(op_name)
    if not spec:
        return False, None, []
    fn, target, report = discovery.resolve(spec.get("executors") or [])
    if spec.get("native"):
        return True, target, report
    return (fn is not None), target, report


def run(key, op_name, account_ref, params, confirm=False, allow_paid=False):
    """Возвращает (checks, account_row|None, первая ошибка ApiError|None)."""
    checks = []
    spec = registry.get(op_name)
    if not spec:
        return [_fail("operation_known", "UNKNOWN_OPERATION", op_name)], None, \
               ApiError("UNKNOWN_OPERATION", f"операция «{op_name}» не зарегистрирована")
    checks.append(_ok("operation_known", op_name))

    available, target, _rep = executor_state(op_name)
    if not available:
        checks.append(_fail("executor_available", "NOT_IMPLEMENTED",
                            "штатный исполнитель не найден: " + ", ".join(spec.get("executors") or [])))
        return checks, None, ApiError("NOT_IMPLEMENTED",
                                      f"для «{op_name}» в этой сборке BORIS нет штатного исполнителя")
    checks.append(_ok("executor_available", target or "native"))

    if spec["cls"] in registry.PHASE1_BLOCKED_CLASSES and not allow_paid:
        code = ("EXTERNAL_PUBLISH_DISABLED" if spec["cls"] == registry.EXTERNAL_PUBLISH
                else "PAID_ACTION_DISABLED")
        checks.append(_fail("class_allowed", code, spec["cls"]))
        return checks, None, ApiError(code, f"операция класса {spec['cls']} выключена в этой версии шлюза")
    checks.append(_ok("class_allowed", spec["cls"]))

    try:
        auth.need_scope(key, spec["scope"])
        checks.append(_ok("scope", spec["scope"]))
    except Exception:
        checks.append(_fail("scope", "FORBIDDEN_SCOPE", spec["scope"]))
        return checks, None, ApiError("FORBIDDEN_SCOPE", f"нужен scope «{spec['scope']}»")

    if spec.get("confirm") and not confirm:
        checks.append(_fail("confirmation", "CONFIRMATION_REQUIRED", op_name))
        return checks, None, ApiError("CONFIRMATION_REQUIRED",
                                      f"«{op_name}» выполняется только с confirm=true")
    checks.append(_ok("confirmation", "не требуется" if not spec.get("confirm") else "получено"))

    missing = [k for k, r in (spec.get("params") or {}).items()
               if r.get("required") and params.get(k) in (None, "", [], {})]
    if missing:
        checks.append(_fail("required_params", "MISSING_INPUT", ", ".join(missing)))
        return checks, None, ApiError("MISSING_INPUT", "не хватает параметров: " + ", ".join(missing))
    checks.append(_ok("required_params"))

    for k, r in (spec.get("params") or {}).items():
        v = params.get(k)
        if v is not None and r.get("type") and not isinstance(v, r["type"]):
            checks.append(_fail("param_types", "VALIDATION_ERROR",
                                f"{k} должен быть {r['type'].__name__}"))
            return checks, None, ApiError("VALIDATION_ERROR",
                                          f"параметр «{k}» должен быть типа {r['type'].__name__}")
    checks.append(_ok("param_types"))

    items = params.get("items")
    if isinstance(items, list) and len(items) > auth.max_items(key):
        checks.append(_fail("items_limit", "TOO_MANY_ITEMS", f"{len(items)} > {auth.max_items(key)}"))
        return checks, None, ApiError("TOO_MANY_ITEMS",
                                      f"за один вызов не больше {auth.max_items(key)} элементов")
    checks.append(_ok("items_limit", len(items) if isinstance(items, list) else 0))

    acc = None
    if account_ref is not None:
        try:
            acc = db.find_account(account_ref)
        except ValueError as e:
            checks.append(_fail("account_exists", "ACCOUNT_AMBIGUOUS", str(e)))
            return checks, None, ApiError("ACCOUNT_AMBIGUOUS", str(e))
        if not acc:
            checks.append(_fail("account_exists", "ACCOUNT_NOT_FOUND", str(account_ref)))
            return checks, None, ApiError("ACCOUNT_NOT_FOUND", f"аккаунт «{account_ref}» не найден")
        checks.append(_ok("account_exists", acc["id"]))
        try:
            auth.need_account(key, acc["id"])
            checks.append(_ok("account_allowed", acc["id"]))
        except Exception:
            checks.append(_fail("account_allowed", "FORBIDDEN_ACCOUNT", acc["id"]))
            return checks, acc, ApiError("FORBIDDEN_ACCOUNT", f"ключу не разрешён аккаунт {acc['id']}")

    if spec["cls"] == registry.EXTERNAL_PUBLISH and acc:
        cols = db.columns("accounts")
        need = [c for c in ("avito_client_id", "avito_client_secret") if c in cols]
        gone = [c for c in need if not acc.get(c)]
        if gone:
            checks.append(_fail("credentials", "AVITO_CREDENTIALS_MISSING", ", ".join(gone)))
            return checks, acc, ApiError("AVITO_CREDENTIALS_MISSING",
                                         "у аккаунта не заполнены: " + ", ".join(gone))
        checks.append(_ok("credentials"))

    est = costs.estimate(op_name, params or {})
    if est["kind"] == "cash" and est["amount_rub"] > 0:
        appr = costs.find_approval(op_name, (acc or {}).get("id"), est["amount_rub"])
        if not appr:
            checks.append(_fail("payment_approval", "PAYMENT_APPROVAL_REQUIRED",
                                f"{est['units']} × {est['unit_price']} ₽ = {est['amount_rub']} ₽"))
            return checks, acc, ApiError(
                "PAYMENT_APPROVAL_REQUIRED",
                f"операция потребует {est['amount_rub']} ₽ реальных денег "
                f"({est['units']} × {est['unit_price']} ₽ за «{est['code']}»). "
                "Работа агентов внутри подписки бесплатна, но это — платное действие. "
                "Нужно явное разрешение: POST /api/v1/costs/approvals ключом человека.",
                details={"estimate": est})
        checks.append(_ok("payment_approval",
                          f"разрешение #{appr['id']} на {float(appr['limit_rub'])} ₽"))
    else:
        checks.append(_ok("payment_approval", "внутри подписки, реальных денег не тратит"))

    try:
        db.one("SELECT 1 AS ok")
        checks.append(_ok("database"))
    except Exception as e:
        checks.append(_fail("database", "DEPENDENCY_UNAVAILABLE", str(e)[:200]))
        return checks, acc, ApiError("DEPENDENCY_UNAVAILABLE", "база недоступна")

    return checks, acc, None
