# -*- coding: utf-8 -*-
"""BORIS Video Factory — предполётная проверка demo-контура.

Выполняется ДО chromium.launch(). Любая непройденная проверка означает
CAPTURE BLOCKED: браузер не стартует вовсе.

Проверка идёт через тот же HTTP-контур, которым пользуется браузер, а не
через прямые запросы к базе. Смысл в том, чтобы доказать не «в базе всё
правильно», а «эта сессия физически не видит чужие аккаунты».
"""

import json
import time

CHECK_LOGIN = "login"
CHECK_ROLE = "role_is_client"
CHECK_ALLOWLIST = "allowlist_not_empty"
CHECK_CURRENT = "current_account_in_allowlist"
CHECK_VISIBLE = "no_unexpected_accounts"
CHECK_RUNTIME = "runtime_url_allowed"

DEFAULT_ENDPOINTS = {
    "login": "/api/auth/login",
    "me": "/api/auth/me",
    "accounts": "/api/accounts/list",
}

FORBIDDEN_ROLES = ("owner", "admin", "manager")


class PreflightError(Exception):
    """Предполётная проверка провалена. Chromium не запускается."""


def _pick_accounts(payload):
    """Из ответа /accounts/list достаём список идентификаторов аккаунтов."""
    items = payload
    if isinstance(payload, dict):
        for key in ("accounts", "items", "list", "data"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        else:
            items = []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in ("account_id", "id", "slug", "name"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    out.append(value)
                    break
    return out


def _pick_user(payload):
    if not isinstance(payload, dict):
        return {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    return {
        "user_id": user.get("id") or user.get("user_id"),
        "email": user.get("email"),
        "role": (user.get("role") or "").strip().lower(),
        "account_id": user.get("account_id"),
        "status": user.get("status"),
    }


def run_preflight(http, base_api, email, password, allowed_account_ids,
                  runtime_url, allowed_runtime_hosts, endpoints=None):
    """Возвращает (ok, report). Report целиком уходит в preflight.json.

    http — объект с методом request(method, url, json=None, headers=None),
    возвращающим (status_code, payload). Инъекция сделана ради тестируемости:
    в бою это тонкая обёртка над requests, в тестах — подделка.
    """
    endpoints = dict(DEFAULT_ENDPOINTS, **(endpoints or {}))
    allowlist = [a for a in (allowed_account_ids or []) if a]
    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_api": base_api,
        "runtime_url": runtime_url,
        "email": email,
        "user_id": None,
        "role": None,
        "status": None,
        "current_account_id": None,
        "allowed_account_ids": sorted(allowlist),
        "visible_account_ids": [],
        "unexpected_account_ids": [],
        "isolation_mode": "rbac_shared_db",
        "checks": [],
        "ok": False,
        "blocked_reason": None,
    }

    def add(name, ok, detail):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    # 0. куда вообще разрешено ходить браузеру
    host_ok = any(runtime_url.startswith(h) for h in (allowed_runtime_hosts or []))
    if not add(CHECK_RUNTIME, host_ok,
               "разрешённые адреса: %s" % ", ".join(allowed_runtime_hosts or ["(пусто)"])):
        report["blocked_reason"] = "Адрес video runtime не входит в список разрешённых"
        return False, report

    if not add(CHECK_ALLOWLIST, bool(allowlist), "аккаунтов в allowlist: %d" % len(allowlist)):
        report["blocked_reason"] = "Allowlist demo-аккаунтов пуст"
        return False, report

    # 1. вход
    code, payload = http.request("POST", base_api + endpoints["login"],
                                 json={"email": email, "password": password})
    if not add(CHECK_LOGIN, code == 200, "код ответа %s" % code):
        report["blocked_reason"] = (
            "Вход demo-пользователя не выполнен (%s). Ответ: %s"
            % (code, json.dumps(payload, ensure_ascii=False)[:200])
        )
        return False, report

    token = None
    if isinstance(payload, dict):
        token = (payload.get("access_token") or payload.get("token")
                 or (payload.get("data") or {}).get("access_token"))
    headers = {"Authorization": "Bearer %s" % token} if token else {}

    # 2. кто мы
    code, me = http.request("GET", base_api + endpoints["me"], headers=headers)
    user = _pick_user(me if code == 200 else {})
    report["user_id"] = user.get("user_id")
    report["role"] = user.get("role")
    report["status"] = user.get("status")
    report["current_account_id"] = user.get("account_id")

    role = user.get("role") or ""
    role_ok = role == "client" and role not in FORBIDDEN_ROLES
    if not add(CHECK_ROLE, role_ok, "роль сессии: %r" % (role or None)):
        report["blocked_reason"] = (
            "Demo-пользователь обязан быть role=client. Владелец проходит проверку "
            "доступа насквозь и увидит чужие аккаунты"
        )
        return False, report

    if not add(CHECK_CURRENT, report["current_account_id"] in allowlist,
               "текущий аккаунт: %r" % report["current_account_id"]):
        report["blocked_reason"] = "Текущий аккаунт сессии не входит в demo allowlist"
        return False, report

    # 3. главное доказательство: что эта сессия вообще видит
    code, accounts = http.request("GET", base_api + endpoints["accounts"], headers=headers)
    if code != 200:
        add(CHECK_VISIBLE, False, "код ответа %s" % code)
        report["blocked_reason"] = "Не удалось получить список видимых аккаунтов (%s)" % code
        return False, report

    visible = sorted(set(_pick_accounts(accounts)))
    unexpected = sorted(set(visible) - set(allowlist))
    report["visible_account_ids"] = visible
    report["unexpected_account_ids"] = unexpected

    if not add(CHECK_VISIBLE, not unexpected,
               "видно аккаунтов: %d, посторонних: %d" % (len(visible), len(unexpected))):
        report["blocked_reason"] = (
            "Сессия видит аккаунты вне demo allowlist: %s" % ", ".join(unexpected)
        )
        return False, report

    report["ok"] = True
    return True, report


def assert_preflight(ok, report):
    """Единственная точка, после которой разрешено трогать Chromium."""
    if not ok:
        raise PreflightError("CAPTURE BLOCKED: %s" % (report.get("blocked_reason") or "проверка не пройдена"))
    return True
