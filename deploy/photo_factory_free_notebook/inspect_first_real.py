#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


ROOT = Path("/root/BORIS")

DATA = ROOT / "data" / "photo_factory"

IMAGES = (
    ROOT
    / "backend"
    / "images"
    / "photo_factory"
)


def load(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None


def walk_json():
    if not DATA.exists():
        return

    for path in DATA.rglob("*.json"):
        if "runtime" in path.parts:
            continue

        yield path


def walk_images():
    if not IMAGES.exists():
        return []

    result = []

    for path in IMAGES.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }:
            continue

        result.append(path)

    return sorted(
        result,
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


print()
print("=" * 70)
print(" BORIS PHOTO FACTORY — READ ONLY STATE")
print("=" * 70)

json_files = list(
    walk_json() or []
)

print()
print("JSON files:", len(json_files))

interesting = []

for path in json_files:

    data = load(path)

    if data is None:
        continue

    raw = json.dumps(
        data,
        ensure_ascii=False,
    )

    score = 0

    for word in (
        "queued",
        "processing",
        "completed",
        "reference",
        "camry",
        "output",
    ):
        if word.lower() in raw.lower():
            score += 1

    if score:
        interesting.append(
            (
                path.stat().st_mtime,
                score,
                path,
                data,
            )
        )

interesting.sort(
    reverse=True,
    key=lambda x: x[0],
)

print()
print("--- recent job/object state ---")

for _, score, path, data in interesting[:15]:

    print()
    print(path)
    print("score:", score)

    if isinstance(data, dict):

        for key in (
            "id",
            "job_id",
            "object_id",
            "object_type",
            "status",
            "reference_count",
            "created_at",
            "updated_at",
        ):
            if key in data:
                print(
                    f"{key}:",
                    data[key],
                )

        scenarios = data.get(
            "scenarios"
        )

        if isinstance(
            scenarios,
            list,
        ):

            counts = {}

            for item in scenarios:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                status = item.get(
                    "status",
                    "unknown",
                )

                counts[status] = (
                    counts.get(
                        status,
                        0,
                    )
                    + 1
                )

            print(
                "scenarios:",
                counts,
            )

images = walk_images()

print()
print("--- generated images ---")
print("count:", len(images))

for path in images[:20]:

    stat = path.stat()

    print(
        time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(
                stat.st_mtime
            ),
        ),
        stat.st_size,
        path,
    )

print()
print("=" * 70)
