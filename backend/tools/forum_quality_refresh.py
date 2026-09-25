#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/root/BORIS/backend")

from app.services import forum_discovery
from app.services import forum_quality
from app.services import service_marketplace as marketplace


def main() -> int:
    rows = []
    existing = forum_quality.load()
    for item in existing.get("items") or []:
        rows.append({
            "domain": item.get("domain"),
            "platforms": item.get("platforms") or [],
        })
    for platform in marketplace.all_platform_objects():
        rows.append({
            "url": getattr(platform, "url", ""),
            "platform": getattr(platform, "key", ""),
        })
    for candidate in forum_discovery.list_candidates():
        rows.append({
            "domain": candidate.get("domain"),
            "url": candidate.get("url"),
            "platform": candidate.get("key"),
        })

    result = forum_quality.refresh_domains(rows, workers=6)
    print(json.dumps({
        "measured_at": result.get("measured_at"),
        "domains_total": result.get("domains_total"),
        "high": result.get("high"),
        "medium": result.get("medium"),
        "low": result.get("low"),
        "unknown": result.get("unknown"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
