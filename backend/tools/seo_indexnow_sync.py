#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "service_marketplaces" / "indexnow_state.json"
KEY = "A91F6C7E2D4B8A503C6E9F1B7D2A4C8E"
ENDPOINTS = (
    "https://yandex.com/indexnow",
    "https://api.indexnow.org/indexnow",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def _systems_urls() -> list[tuple[str, Path]]:
    root = Path("/var/www/systems-ai-oko/current")
    sitemap = root / "sitemap.xml"
    out: list[tuple[str, Path]] = []
    try:
        tree = ET.parse(sitemap)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [x.text.strip() for x in tree.findall(".//s:loc", ns) if x.text]
    except Exception:
        urls = ["https://systems-ai-oko.ru/"]
    for url in urls:
        path = urlparse(url).path.strip("/")
        file_path = root / ("index.html" if not path else f"{path}/index.html")
        if file_path.exists():
            out.append((url, file_path))
    return out


def _sources() -> dict[str, list[tuple[str, Path]]]:
    return {
        "boris-ai.pro": [
            ("https://boris-ai.pro/", Path("/var/www/boris-home/index.html")),
        ],
        "systems-ai-oko.ru": _systems_urls(),
    }


def _load() -> dict:
    if not STATE.exists():
        return {"version": 1, "hosts": {}}
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"version": 1, "hosts": {}}
    except Exception:
        return {"version": 1, "hosts": {}}


def _post(endpoint: str, host: str, urls: list[str]) -> tuple[int | None, str]:
    body = json.dumps({
        "host": host,
        "key": KEY,
        "keyLocation": f"https://{host}/{KEY}.txt",
        "urlList": urls,
    }).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            return int(response.status), response.read(500).decode("utf-8", "replace")
    except error.HTTPError as exc:
        return int(exc.code), exc.read(500).decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    state = _load()
    hosts = state.setdefault("hosts", {})
    result = {"at": _now(), "hosts": {}}

    for host, sources in _sources().items():
        host_state = hosts.setdefault(host, {"urls": {}})
        url_state = host_state.setdefault("urls", {})
        changed: list[str] = []
        hashes: dict[str, str] = {}
        for url, local_path in sources:
            digest = _sha(local_path)
            if not digest:
                continue
            hashes[url] = digest
            previous = (url_state.get(url) or {}).get("sha256")
            if previous != digest:
                changed.append(url)

        submissions = []
        accepted = True
        if changed:
            for endpoint in ENDPOINTS:
                status, body = _post(endpoint, host, changed)
                ok = status in {200, 202}
                accepted = accepted and ok
                submissions.append({
                    "endpoint": endpoint,
                    "status": status,
                    "accepted": ok,
                    "body_tail": body[-300:],
                })
            # Persist the content hash only after all endpoints accepted the
            # notification. A transient error therefore retries next certifier run.
            if accepted:
                stamp = _now()
                for url in changed:
                    url_state[url] = {
                        "sha256": hashes[url],
                        "last_submitted_at": stamp,
                        "last_status": "INDEXNOW_VERIFIED" if all(x["status"] == 200 for x in submissions) else "INDEXNOW_ACCEPTED",
                    }
        result["hosts"][host] = {
            "tracked_urls": len(hashes),
            "changed_urls": changed,
            "changed_count": len(changed),
            "submission_attempted": bool(changed),
            "accepted": accepted if changed else True,
            "submissions": submissions,
        }
        host_state["last_checked_at"] = _now()

    state["updated_at"] = _now()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(x.get("accepted") for x in result["hosts"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
