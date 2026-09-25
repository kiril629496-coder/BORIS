# -*- coding: utf-8 -*-
"""BORIS Video Factory — demo-личность, идемпотентно.

Правила:
  * demo-клиент заводится один раз и переиспользуется. Мусорных клиентов
    на каждый прогон не появляется;
  * найденная личность ПРОВЕРЯЕТСЯ, а не чинится молча. Не сходится роль
    или аккаунт — отказ, а не автоправка;
  * поля запроса на создание берутся из openapi бэкенда, а не из головы.
    Не хватает данных — отказ до вызова, без пробных POST'ов;
  * административный токен используется только здесь и в браузер не уходит.
"""

DEMO_PREFIX = "boris_video_demo"
REQUIRED_ROLE = "client"

ACTION_FOUND = "found"
ACTION_CREATED = "created"
ACTION_BLOCKED = "blocked"


class IdentityError(Exception):
    pass


def _as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("clients", "items", "results", "list", "data", "users"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _record_of(item, email):
    if not isinstance(item, dict):
        return None
    found = (item.get("email") or "").strip().lower()
    if found != email.strip().lower():
        return None
    return {
        "user_id": item.get("id") or item.get("user_id"),
        "email": item.get("email"),
        "role": (item.get("role") or "").strip().lower(),
        "status": item.get("status"),
        "account_id": item.get("account_id"),
        "account_ids": [a for a in (
            item.get("account_ids") or ([item.get("account_id")] if item.get("account_id") else [])
        ) if a],
    }


def find_demo_client(http, base_api, search_path, email, param_names=None):
    """Ищет клиента по почте. Пробует известные имена query-параметра."""
    names = [p["name"] for p in (param_names or []) if p.get("name")] or ["q"]
    tried = []
    for name in names:
        code, payload = http.request("GET", base_api + search_path, params={name: email})
        tried.append({"param": name, "code": code})
        if code != 200:
            continue
        for item in _as_list(payload):
            record = _record_of(item, email)
            if record:
                return record, tried
    return None, tried


def validate_identity(record, demo_prefix=DEMO_PREFIX):
    """Проверки существующей личности. Возвращает список проблем."""
    problems = []
    if not record:
        return ["личность не найдена"]
    if record.get("role") != REQUIRED_ROLE:
        problems.append(
            "роль %r вместо %r — владелец видит все аккаунты и для демо непригоден"
            % (record.get("role"), REQUIRED_ROLE)
        )
    accounts = record.get("account_ids") or []
    if not accounts:
        problems.append("к личности не привязан ни один аккаунт")
    foreign = [a for a in accounts if not str(a).startswith(demo_prefix)]
    if foreign:
        problems.append("привязаны аккаунты вне demo-контура: %s" % ", ".join(map(str, foreign)))
    if record.get("status") and record["status"] not in ("active",):
        problems.append("статус %r — вход может не пройти" % record["status"])
    return problems


def build_create_payload(required, properties, values):
    """Тело запроса строго по схеме openapi.

    Возвращает (payload, missing). Непустой missing означает: не вызываем
    создание вовсе — данных не хватает, а угадывать запрещено.
    """
    payload, missing = {}, []
    for field in required:
        if field in values and values[field] not in (None, ""):
            payload[field] = values[field]
        else:
            missing.append(field)
    # необязательные поля добавляем только если бэкенд про них знает
    for field, value in values.items():
        if field in payload or value in (None, ""):
            continue
        if field in properties:
            payload[field] = value
    return payload, missing


def ensure_demo_identity(admin_http, base_api, paths, spec, values,
                         demo_prefix=DEMO_PREFIX, allow_create=True):
    """Главная функция. Возвращает (ok, report).

    paths: {"search": "/api/admin/clients/search", "create": "/api/admin/clients/create"}
    values: {"email": ..., "password": ..., "account_id": ..., ...}
    spec: разобранный openapi бэкенда.
    """
    from . import http as H

    email = (values or {}).get("email")
    report = {
        "email": email,
        "demo_prefix": demo_prefix,
        "action": ACTION_BLOCKED,
        "identity": None,
        "problems": [],
        "search_attempts": [],
        "create_payload_fields": [],
        "reason": None,
    }
    if not email:
        report["reason"] = "не задана почта demo-пользователя"
        return False, report

    search_path = paths.get("search")
    if search_path:
        params = H.query_params(spec, search_path, "get")
        record, tried = find_demo_client(admin_http, base_api, search_path, email, params)
        report["search_attempts"] = tried
    else:
        record = None

    if record:
        problems = validate_identity(record, demo_prefix)
        report["identity"] = record
        report["problems"] = problems
        if problems:
            report["reason"] = (
                "Существующая demo-личность не проходит проверку: %s. "
                "Чинить вручную — автоправка личности запрещена" % "; ".join(problems)
            )
            return False, report
        report["action"] = ACTION_FOUND
        return True, report

    if not allow_create:
        report["reason"] = "Личность не найдена, а создание в этом прогоне запрещено"
        return False, report

    create_path = paths.get("create")
    if not create_path:
        report["reason"] = "Не найден маршрут создания клиента"
        return False, report

    required, properties = H.request_schema(spec, create_path, "post")
    payload, missing = build_create_payload(required, properties, values)
    report["create_payload_fields"] = sorted(payload.keys())
    if missing:
        report["reason"] = (
            "Для создания demo-клиента не хватает обязательных полей: %s. "
            "Значения не подставляются наугад" % ", ".join(missing)
        )
        return False, report

    code, payload_out = admin_http.request("POST", base_api + create_path, json=payload)
    if code not in (200, 201):
        report["reason"] = "Создание demo-клиента отклонено (%s): %s" % (
            code, H.dumps(payload_out)[:400])
        return False, report

    created = payload_out if isinstance(payload_out, dict) else {}
    body = created.get("client") if isinstance(created.get("client"), dict) else created
    record = {
        "user_id": body.get("id") or body.get("user_id"),
        "email": body.get("email") or email,
        "role": (body.get("role") or REQUIRED_ROLE).strip().lower(),
        "status": body.get("status"),
        "account_id": body.get("account_id") or values.get("account_id"),
        "account_ids": [a for a in [body.get("account_id") or values.get("account_id")] if a],
    }
    problems = validate_identity(record, demo_prefix)
    report["identity"] = record
    report["problems"] = problems
    if problems:
        report["reason"] = ("Созданная личность не проходит проверку: %s"
                            % "; ".join(problems))
        return False, report

    report["action"] = ACTION_CREATED
    return True, report
