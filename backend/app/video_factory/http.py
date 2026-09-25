# -*- coding: utf-8 -*-
"""BORIS Video Factory — тонкая обёртка над HTTP.

Два отдельных клиента по замыслу, а не по случайности:
  * административный — токеном владельца, только заведение demo-личности;
  * демонстрационный — токеном demo-клиента, всё остальное.
Токен владельца НИКОГДА не попадает в браузер и в артефакты.

Отдельно: `trust_env=False`. В окружении BORIS экспортируется международный
прокси до OpenAI, и без этого флага запрос к 127.0.0.1 уходил бы в прокси
и падал с 407.
"""

import json as _json

DEFAULT_TIMEOUT = 30


class HttpError(Exception):
    pass


class Http(object):
    def __init__(self, timeout=DEFAULT_TIMEOUT, token=None, label="http"):
        import requests
        self._session = requests.Session()
        self._session.trust_env = False        # не ходить через PROXY_INTL
        self._session.proxies = {}
        self.timeout = timeout
        self.token = token
        self.label = label
        self.calls = []

    def _headers(self, extra=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        if extra:
            headers.update(extra)
        return headers

    def request(self, method, url, json=None, params=None, headers=None, data=None):
        """Возвращает (status_code, payload). Payload — распарсенный json либо текст."""
        try:
            resp = self._session.request(
                method.upper(), url, json=json, params=params, data=data,
                headers=self._headers(headers), timeout=self.timeout,
            )
        except Exception as exc:                      # сеть, таймаут, отказ соединения
            self.calls.append({"method": method, "url": url, "error": str(exc)})
            return 0, {"error": str(exc)}

        try:
            payload = resp.json()
        except ValueError:
            payload = {"text": (resp.text or "")[:2000]}

        self.calls.append({"method": method.upper(), "url": url, "code": resp.status_code})
        return resp.status_code, payload

    def login(self, url, email, password, field="email"):
        """Логин и запоминание токена. Возвращает (ok, code, payload)."""
        code, payload = self.request("POST", url, json={field: email, "password": password})
        token = None
        if code == 200 and isinstance(payload, dict):
            token = (payload.get("access_token") or payload.get("token")
                     or (payload.get("data") or {}).get("access_token"))
        if token:
            self.token = token
        return bool(token), code, payload

    def trace(self):
        """Журнал вызовов без единого токена — годится для артефактов."""
        return list(self.calls)


def openapi(http, base_api, path="/openapi.json"):
    """Схема FastAPI — источник истины по телам запросов.

    Нужна, чтобы не угадывать поля при заведении demo-клиента.
    """
    code, payload = http.request("GET", base_api + path)
    if code != 200 or not isinstance(payload, dict):
        raise HttpError("Не удалось получить openapi (%s) с %s" % (code, base_api + path))
    return payload


def _deref(spec, node):
    """Разворачивает $ref на компоненты схемы."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 10:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        cur = spec
        for part in ref[2:].split("/"):
            cur = (cur or {}).get(part, {})
        node = cur
        seen += 1
    return node if isinstance(node, dict) else {}


def request_schema(spec, path, method):
    """(required, properties) для тела запроса конкретной ручки."""
    item = ((spec.get("paths") or {}).get(path) or {}).get(method.lower()) or {}
    body = _deref(spec, (item.get("requestBody") or {}))
    content = (body.get("content") or {})
    media = content.get("application/json") or content.get("application/x-www-form-urlencoded") or {}
    schema = _deref(spec, media.get("schema") or {})
    props = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    return required, props


def query_params(spec, path, method):
    """Имена query-параметров ручки — чтобы не гадать, как называется поиск."""
    item = ((spec.get("paths") or {}).get(path) or {}).get(method.lower()) or {}
    out = []
    for param in item.get("parameters") or []:
        param = _deref(spec, param)
        if param.get("in") == "query":
            out.append({"name": param.get("name"), "required": bool(param.get("required"))})
    return out


def dumps(value):
    return _json.dumps(value, ensure_ascii=False, indent=1, default=str)
