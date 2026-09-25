#!/usr/bin/env python3

import json
import os
import sys
import traceback

from app.ext_api import a2a, executor
from app.services.exception_observability import observe_suppressed


PROTOCOL_VERSION = "2024-11-05"

SERVER_INFO = {
    "name": "boris-development",
    "version": "1.0",
}


def _order():
    raw = os.environ.get("BORIS_ORDER_ID", "").strip()

    if not raw:
        raise RuntimeError(
            "BORIS_ORDER_ID is required for BORIS MCP"
        )

    return a2a.get(int(raw))


def _tool_specs():
    # tool_specs historically contained a duplicated submit_review entry.
    # MCP requires one authoritative schema per tool name.
    # Preserve insertion order, but let the final definition replace an
    # earlier duplicate.
    by_name = {}

    for spec in executor.tool_specs():
        if not isinstance(spec, dict):
            continue

        fn = spec.get("function") or {}

        name = fn.get("name")
        if not name:
            continue

        by_name[name] = {
            "name": name,
            "description": fn.get("description") or "",
            "inputSchema": fn.get("parameters") or {
                "type": "object",
                "properties": {},
            },
        }

    return list(by_name.values())


def _response(req_id, result=None, error=None):
    body = {
        "jsonrpc": "2.0",
        "id": req_id,
    }

    if error is not None:
        body["error"] = error
    else:
        body["result"] = result

    sys.stdout.write(
        json.dumps(
            body,
            ensure_ascii=False,
        )
        + "\n"
    )

    sys.stdout.flush()


def _handle(req):
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        _response(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": SERVER_INFO,
            },
        )
        return

    if method in (
        "notifications/initialized",
        "initialized",
    ):
        return

    if method == "ping":
        _response(req_id, {})
        return

    if method == "tools/list":
        _response(
            req_id,
            {
                "tools": _tool_specs(),
            },
        )
        return

    if method == "tools/call":
        params = req.get("params") or {}

        name = params.get("name")
        args = params.get("arguments") or {}

        if not name:
            _response(
                req_id,
                error={
                    "code": -32602,
                    "message": "tool name missing",
                },
            )
            return

        order = _order()

        result = executor.call_tool(
            order,
            name,
            args,
        )

        is_error = (
            isinstance(result, dict)
            and bool(result.get("error"))
        )

        _response(
            req_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            result,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                ],
                "isError": is_error,
            },
        )
        return

    _response(
        req_id,
        error={
            "code": -32601,
            "message": "method not found: %s" % method,
        },
    )


def main():
    for raw in sys.stdin:
        raw = raw.strip()

        if not raw:
            continue

        try:
            req = json.loads(raw)
            _handle(req)

        except Exception as e:
            req_id = None

            try:
                req_id = req.get("id")
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=189)

            _response(
                req_id,
                error={
                    "code": -32000,
                    "message": "%s: %s"
                    % (
                        type(e).__name__,
                        str(e),
                    ),
                    "data": traceback.format_exc()[-3000:],
                },
            )


if __name__ == "__main__":
    main()
