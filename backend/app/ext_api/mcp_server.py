# -*- coding: utf-8 -*-
"""MCP-сервер BORIS для Claude Code.

Claude Code приходит со своим набором инструментов и про базу и службы BORIS
ничего не знает. Без этого ревьюер физически не может проверить то, что ему
показал исполнитель, — и честно уходит в BLOCKED_INFRA. Здесь те же руки,
что есть у агентов в API-режиме, отданы ему через stdio-MCP.

Только чтение: SQL строго SELECT, команды по белому списку, ничего
изменяющего. Запускается самим лидом, вручную дёргать не нужно.
"""
import json, sys, traceback

PROTOCOL_DEFAULT = "2025-06-18"


def _tools():
    import os
    from . import repo, ops
    mode = (os.environ.get("BORIS_MCP_MODE") or "lead").strip().lower()
    actor = (os.environ.get("BORIS_MCP_ACTOR") or
             ("gpt-executor" if mode == "executor" else "claude-lead"))
    key = {"scope_set": ({"read", "write", "tasks"} if mode == "executor"
                         else {"read", "tasks"}),
           "actor_type": ("autonomous-dev" if mode == "executor" else "ai_agent"),
           "actor_name": actor, "name": actor, "id": None,
           "max_items_per_call": 200, "allowed_accounts": None}
    tools = {
        "db_query": {
            "description": "SQL к боевой базе BORIS. Только SELECT и WITH … SELECT, "
                           "одна инструкция, автоматический LIMIT.",
            "schema": {"type": "object",
                       "properties": {"sql": {"type": "string"},
                                      "limit": {"type": "integer"}},
                       "required": ["sql"]},
            "fn": lambda sql=None, limit=100, **_: ops.db_query(key, sql, limit)},
        "repo_tree": {
            "description": "Дерево файлов проекта. Секреты и бэкапы не отдаются.",
            "schema": {"type": "object",
                       "properties": {"paths": {"type": "array",
                                                "items": {"type": "string"}}}},
            "fn": lambda paths=None, **_: repo.tree(paths)},
        "repo_read": {
            "description": "Прочитать файл проекта целиком или диапазоном строк.",
            "schema": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "paths": {"type": "array", "items": {"type": "string"}},
                                      "start": {"type": "integer"},
                                      "end": {"type": "integer"}}},
            "fn": lambda path=None, paths=None, start=None, end=None, **_:
                 (repo.read_many(paths) if paths else repo.read_file(path, start or 1, end))},
        "shell": {
            "description": "Одна команда из белого списка, без оболочки и конвейеров.",
            "schema": {"type": "object",
                       "properties": {"command": {"type": "string"},
                                      "cwd": {"type": "string"}},
                       "required": ["command"]},
            "fn": lambda command=None, cwd=None, **_: ops.shell(key, command, cwd)},
        "git": {
            "description": "Инспекция git: status, log, diff, branch.",
            "schema": {"type": "object", "properties": {"what": {"type": "string"}}},
            "fn": lambda what="status", **_: ops.git_info(key, what)},
        "logs": {
            "description": "Хвост журнала службы (boris-backend и другие).",
            "schema": {"type": "object",
                       "properties": {"unit": {"type": "string"},
                                      "lines": {"type": "integer"}}},
            "fn": lambda unit="boris-backend", lines=100, **_: ops.logs(key, unit, lines)},
        "browser": {
            "description": "Обойти страницы кабинета браузером: коды, пустые экраны, "
                           "редиректы, кнопки, мобильная ширина (width=390).",
            "schema": {"type": "object",
                       "properties": {"paths": {"type": "array", "items": {"type": "string"}},
                                      "width": {"type": "integer"},
                                      "port": {"type": "integer"}},
                       "required": ["paths"]},
            "fn": lambda paths=None, width=1440, port=3002, **_:
                 ops.browser(dict(key, scope_set={"read", "tasks"}), paths, width, 900, port)},
        "http_check": {
            "description": "Runtime-проверка на localhost: код ответа и время.",
            "schema": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "port": {"type": "integer"}}},
            "fn": lambda path="/health", port=8000, **_: ops.http_check(key, path, port)},
    }
    if mode == "executor":
        dev_job_id = os.environ.get("BORIS_DEV_JOB_ID")
        if dev_job_id:
            tools["propose_patch"] = {
                "description": "Предложить patch только для текущего dev_job.",
                "schema": {"type": "object",
                           "properties": {
                               "edits": {"type": "array", "items": {"type": "object"}},
                               "note": {"type": "string"}},
                           "required": ["edits"]},
                "fn": lambda edits=None, note=None, **_: repo.propose(
                    dict(key, actor_type="ai_agent"), dev_job_id, edits or [], note),
            }
            tools["autonomous_apply"] = {
                "description": "Применить собственный patch через autonomous-dev policy; skip_verify отсутствует.",
                "schema": {"type": "object",
                           "properties": {"patch_id": {"type": "integer"}},
                           "required": ["patch_id"]},
                "fn": lambda patch_id=None, **_: repo.autonomous_apply(key, int(patch_id)),
            }
    return tools


def _text(x):
    """Serialize MCP tool output through the external-AI privacy boundary.

    Claude Code is outside the BORIS data perimeter. Even though DB/log tools
    execute locally, their results are sent back to the external model over MCP,
    so raw client identifiers must never be returned here.
    """
    raw = x if isinstance(x, str) else json.dumps(
        x, ensure_ascii=False, indent=1, default=str
    )[:60000]
    try:
        from app.services.ai_privacy import safe_external_ai_payload
        safe, _vault = safe_external_ai_payload(raw)
        return str(safe)
    except Exception:
        # Fail closed: losing one diagnostic is safer than sending a value that
        # the privacy layer could not confidently sanitize.
        return "[BORIS_PRIVACY_BLOCKED_TOOL_OUTPUT]"


def handle(msg, tools):
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_DEFAULT
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": ver,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "boris", "version": "1.0.0"}}}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["schema"]}
            for n, t in tools.items()]}}

    if method == "tools/call":
        p = msg.get("params") or {}
        name = p.get("name")
        args = p.get("arguments") or {}
        t = tools.get(name)
        if not t:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text",
                                            "text": f"инструмента «{name}» нет"}],
                               "isError": True}}
        try:
            out = t["fn"](**args)
            err = False
        except Exception as e:
            from .errors import ApiError
            out = ({"error": e.code, "message": e.message} if isinstance(e, ApiError)
                   else {"error": "INTERNAL", "message": f"{type(e).__name__}: {e}"[:400]})
            err = True
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": _text(out)}],
                           "isError": err}}

    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"метод {method} не поддержан"}}


def main():
    tools = _tools()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            resp = handle(msg, tools)
        except Exception:
            print(traceback.format_exc()[:2000], file=sys.stderr, flush=True)
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": "внутренняя ошибка сервера"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
