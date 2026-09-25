# -*- coding: utf-8 -*-
"""BORIS Video Factory — резолвер фактических маршрутов бэкенда.

Правило проекта: смотреть источник истины, а не вспоминать. Пути ручек
угадывались трижды подряд, поэтому Video Factory их не помнит вовсе —
она читает `app/api/*.py` и собирает реальные маршруты из
`APIRouter(prefix=...)` и декораторов `@router.<метод>("...")`.

Результат кладётся в preflight.json и metadata.json как resolved_endpoints
вместе с указанием файла и строки — чтобы через месяц было видно, откуда
взялся каждый путь.
"""

import os
import re

ROUTER_RE = re.compile(r"""APIRouter\s*\(""")
PREFIX_RE = re.compile(r"""prefix\s*=\s*["']([^"']+)["']""")
DECORATOR_RE = re.compile(
    r"""^\s*@(?P<var>\w+)\.(?P<method>get|post|put|patch|delete)\s*\(\s*["'](?P<path>[^"']*)["']"""
)
ASSIGN_RE = re.compile(r"""^\s*(?P<var>\w+)\s*=\s*APIRouter\s*\(""")

SKIP_MARKERS = (".bak", ".backup", ".before", ".broken", ".orig", "~")

# что именно ищем. Порядок вариантов = порядок предпочтения
WANTED = {
    "login": {
        "methods": ("post",),
        "patterns": (r"/auth/login$", r"/login$", r"/auth/signin$"),
        "why": "вход demo-пользователя",
    },
    "me": {
        "methods": ("get",),
        "patterns": (r"/auth/me$", r"/me$", r"/auth/whoami$"),
        "why": "роль и текущий аккаунт сессии",
    },
    "accounts": {
        "methods": ("get",),
        "patterns": (r"/accounts/list$", r"/accounts$", r"/account/list$"),
        "why": "какие аккаунты видит сессия",
    },
}


def _readable_files(api_dir):
    if not os.path.isdir(api_dir):
        return []
    out = []
    for name in sorted(os.listdir(api_dir)):
        if not name.endswith(".py"):
            continue
        low = name.lower()
        if any(marker in low for marker in SKIP_MARKERS):
            continue
        out.append(os.path.join(api_dir, name))
    return out


def _join(prefix, path):
    prefix = (prefix or "").rstrip("/")
    if not path or path == "/":
        return prefix or "/"
    if not path.startswith("/"):
        path = "/" + path
    return prefix + path


def scan_routes(api_dir):
    """Все маршруты, объявленные в каталоге. Список словарей с источником."""
    routes = []
    for full in _readable_files(api_dir):
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue

        prefixes = {}
        for line in lines:
            assign = ASSIGN_RE.match(line)
            if assign and ROUTER_RE.search(line):
                found = PREFIX_RE.search(line)
                prefixes[assign.group("var")] = found.group(1) if found else ""

        for number, line in enumerate(lines, start=1):
            hit = DECORATOR_RE.match(line)
            if not hit:
                continue
            var = hit.group("var")
            if var not in prefixes:
                continue
            routes.append({
                "method": hit.group("method").lower(),
                "path": _join(prefixes[var], hit.group("path")),
                "raw_path": hit.group("path"),
                "prefix": prefixes[var],
                "file": os.path.basename(full),
                "line": number,
            })
    return routes


def resolve_endpoints(api_dir, wanted=None):
    """Ищет нужные ручки среди реальных маршрутов.

    Возвращает (resolved, report). resolved — {имя: путь} для найденного.
    Ненайденное в resolved НЕ попадает: предположительных путей не бывает.
    """
    wanted = wanted or WANTED
    routes = scan_routes(api_dir)
    resolved = {}
    report = {
        "api_dir": api_dir,
        "routes_scanned": len(routes),
        "resolved": {},
        "unresolved": [],
    }

    for name, spec in wanted.items():
        chosen = None
        for pattern in spec["patterns"]:
            matches = [
                r for r in routes
                if r["method"] in spec["methods"] and re.search(pattern, r["path"])
            ]
            if matches:
                matches.sort(key=lambda r: (len(r["path"]), r["file"], r["line"]))
                chosen = matches[0]
                chosen_alternatives = [m["path"] for m in matches[1:]]
                break
        if chosen is None:
            report["unresolved"].append({"name": name, "why": spec["why"],
                                         "tried": list(spec["patterns"])})
            continue
        resolved[name] = chosen["path"]
        report["resolved"][name] = {
            "path": chosen["path"],
            "method": chosen["method"].upper(),
            "source": "%s:%d" % (chosen["file"], chosen["line"]),
            "why": spec["why"],
            "alternatives": chosen_alternatives,
        }
    return resolved, report


def describe_failure(report):
    """Человеческое объяснение, если что-то не нашлось. Идёт в blocked_reason."""
    if not report.get("unresolved"):
        return None
    parts = []
    for item in report["unresolved"]:
        parts.append("%s (%s): не найдено ни одного маршрута по шаблонам %s"
                     % (item["name"], item["why"], ", ".join(item["tried"])))
    return ("Не удалось определить фактические маршруты бэкенда. "
            + "; ".join(parts)
            + ". Пути не подставляются по памяти — исправьте шаблоны в endpoints.WANTED")
