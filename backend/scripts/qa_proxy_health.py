#!/usr/bin/env python3
import os
import tempfile

import requests
import proxy_pool
from app.api import parser


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "{}"


def main():
    orig = {
        "port": proxy_pool.PROXY_PORT,
        "pmin": proxy_pool.PROXY_PORT_MIN,
        "pmax": proxy_pool.PROXY_PORT_MAX,
        "host": proxy_pool.PROXY_HOST,
        "user": proxy_pool.PROXY_USER,
        "pwd": proxy_pool.PROXY_PASS,
        "get": requests.get,
        "health": parser.check_proxy_health,
        "find": proxy_pool.find_clean_port,
        "route_health": proxy_pool.get_avito_proxy_health,
    }
    try:
        proxy_pool.PROXY_HOST = "proxy.example"
        proxy_pool.PROXY_USER = "u"
        proxy_pool.PROXY_PASS = "p"
        proxy_pool.PROXY_PORT = 12345
        proxy_pool.PROXY_PORT_MIN = 10000
        proxy_pool.PROXY_PORT_MAX = 10999

        seen = []
        def ok_get(url, **kwargs):
            seen.append(kwargs.get("proxies"))
            return _Resp(200)
        requests.get = ok_get
        r = parser.check_proxy_health()
        assert r["alive"] is True and r["state"] == "ready"
        assert r["checked_ports"] == 1 and r["configured_endpoints"] == 1
        assert proxy_pool._avito_candidate_ports() == [12345]
        assert seen and all(":12345" in p["https"] for p in seen)

        requests.get = lambda *a, **k: _Resp(407)
        r = parser.check_proxy_health()
        assert r["alive"] is False and r["state"] == "auth_failed"
        assert r["auth_failures"] == 1

        async def blocked_find(*args, **kwargs):
            assert kwargs.get("force_refresh") is True
            return None
        parser.check_proxy_health = lambda: {
            "alive": True, "state": "ready", "checked_ports": 1,
            "working_ports": 1, "configured_endpoints": 1, "explicit_port": True,
            "auth_failures": 0, "transport_failures": 0, "upstream_failures": 0,
            "reason": None,
        }
        proxy_pool.find_clean_port = blocked_find
        proxy_pool.get_avito_proxy_health = lambda: {
            "state": "avito_blocked", "detail": "no_avito_usable_exit",
            "blocked_attempts": 1, "transport_errors": 0, "auth_errors": 0,
        }
        rr = parser.check_avito_proxy_route_health()
        assert rr["alive"] is False and rr["state"] == "avito_blocked"
        assert rr["transport_state"] == "ready" and rr["route_validated"] is False

        fd, path = tempfile.mkstemp(prefix="boris-proxy-health-", suffix=".json")
        os.close(fd)
        os.unlink(path)
        try:
            bad = {"alive": False, "state": "avito_blocked"}
            good = {"alive": True, "state": "ready"}
            a = parser._proxy_health_monitor_update(bad, path, 1000)
            b = parser._proxy_health_monitor_update(bad, path, 1001)
            c = parser._proxy_health_monitor_update(bad, path, 1002)
            d = parser._proxy_health_monitor_update(bad, path, 1003)
            e = parser._proxy_health_monitor_update(good, path, 1004)
            assert not a["should_alert"] and a["consecutive_failures"] == 1
            assert not b["should_alert"] and b["consecutive_failures"] == 2
            assert c["should_alert"] and c["consecutive_failures"] == 3
            assert not d["should_alert"] and d["consecutive_failures"] == 4
            assert e["consecutive_failures"] == 0 and e["recovered"]
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        print("PROXY_HEALTH_SOURCE_OF_TRUTH=PASS")
        print("PROXY_AVITO_ROUTE_CLASSIFICATION=PASS")
        print("PROXY_ALERT_DEBOUNCE=PASS")
    finally:
        proxy_pool.PROXY_PORT = orig["port"]
        proxy_pool.PROXY_PORT_MIN = orig["pmin"]
        proxy_pool.PROXY_PORT_MAX = orig["pmax"]
        proxy_pool.PROXY_HOST = orig["host"]
        proxy_pool.PROXY_USER = orig["user"]
        proxy_pool.PROXY_PASS = orig["pwd"]
        requests.get = orig["get"]
        parser.check_proxy_health = orig["health"]
        proxy_pool.find_clean_port = orig["find"]
        proxy_pool.get_avito_proxy_health = orig["route_health"]


if __name__ == "__main__":
    main()
