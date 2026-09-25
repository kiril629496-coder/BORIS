#!/usr/bin/env python3
"""Safe retention for immutable BORIS frontend artifacts.

Keeps the active release, newest N artifacts and everything younger than the
minimum age. Only directories containing BORIS_ARTIFACT.json are eligible.
Default is dry-run; --apply is required for deletion.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

ROOT = Path('/root/BORIS/frontend/.boris-builds')
ARTIFACTS = ROOT / 'artifacts'
CURRENT = ROOT / 'current'


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob('*'):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--keep-newest', type=int, default=80)
    ap.add_argument('--min-age-hours', type=float, default=48.0)
    ap.add_argument('--target-free-pct', type=float, default=15.0)
    args = ap.parse_args()

    active = CURRENT.resolve(strict=True)
    if active.parent != ARTIFACTS.resolve():
        raise SystemExit('REFUSE: current release is outside artifacts directory')

    dirs = sorted(
        [p for p in ARTIFACTS.iterdir() if p.is_dir() and not p.is_symlink()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    keep = set(dirs[:max(1, args.keep_newest)])
    keep.add(active)
    cutoff = time.time() - max(1.0, args.min_age_hours) * 3600
    candidates = []
    skipped_unverified = 0
    for p in dirs:
        if p in keep or p.stat().st_mtime >= cutoff:
            continue
        manifest = p / 'BORIS_ARTIFACT.json'
        if not manifest.is_file():
            skipped_unverified += 1
            continue
        try:
            meta = json.loads(manifest.read_text(errors='strict'))
            if meta.get('kind') != 'BORIS_FRONTEND_ARTIFACT' or meta.get('status') != 'READY':
                skipped_unverified += 1
                continue
        except Exception:
            skipped_unverified += 1
            continue
        candidates.append(p)

    sizes = {p: dir_size(p) for p in candidates}
    preview_bytes = sum(sizes.values())
    before = shutil.disk_usage('/')
    before_free_pct = before.free / before.total * 100
    deleted = []
    deleted_bytes = 0

    if args.apply:
        # Oldest first. Stop once the disk has comfortable headroom; this avoids
        # deleting history merely because it is eligible.
        for p in sorted(candidates, key=lambda x: x.stat().st_mtime):
            if p.resolve() == active:
                raise SystemExit('REFUSE: active release reached deletion set')
            now_usage = shutil.disk_usage('/')
            if (now_usage.free / now_usage.total * 100) >= args.target_free_pct:
                break
            shutil.rmtree(p)
            deleted.append(p.name)
            deleted_bytes += sizes[p]

    after = shutil.disk_usage('/')
    result = {
        'mode': 'apply' if args.apply else 'dry_run',
        'active_release': active.name,
        'artifacts_total': len(dirs),
        'keep_newest': args.keep_newest,
        'min_age_hours': args.min_age_hours,
        'eligible_count': len(candidates),
        'eligible_gb': round(preview_bytes / 1024**3, 2),
        'skipped_unverified': skipped_unverified,
        'deleted_count': len(deleted),
        'deleted_gb': round(deleted_bytes / 1024**3, 2),
        'free_pct_before': round(before_free_pct, 1),
        'free_pct_after': round(after.free / after.total * 100, 1),
        'free_gb_after': round(after.free / 1024**3, 1),
        'target_free_pct': args.target_free_pct,
        'active_still_exists': active.is_dir(),
        'current_still_points_active': CURRENT.resolve(strict=True) == active,
        'deleted': deleted,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['active_still_exists'] or not result['current_still_points_active']:
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
