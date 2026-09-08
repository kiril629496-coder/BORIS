#!/usr/bin/env python3
"""Move forbidden legacy text-in-image files out of the public image tree.

This is a filesystem last-line guard for stale workers: DB triggers prevent
registration, while this tool closes the short race where bytes could be
written to /images before a rejected DB mutation. It never deletes evidence.
"""
from __future__ import annotations
import hashlib, json, os, shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/BORIS/backend/images').resolve()
QUARANTINE = Path('/root/BORIS/backend/quarantine/auto_legacy_visual').resolve()
MARKERS = ('fullai_', 'gptimg_')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    batch = QUARANTINE / stamp
    moved = []
    if not ROOT.exists():
        print(json.dumps({'status':'PASS','moved':0,'files':[]}, ensure_ascii=False))
        return 0
    for path in ROOT.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name.lower()
        if not any(marker in name for marker in MARKERS):
            continue
        real = path.resolve()
        try:
            rel = real.relative_to(ROOT)
        except ValueError:
            continue
        size = real.stat().st_size
        digest = sha256(real)
        target = batch / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite evidence if an unlikely same-name collision occurs.
        if target.exists():
            target = target.with_name(target.name + '.' + digest[:12])
        os.replace(real, target)
        moved.append({'source':str(real),'quarantine':str(target),'size':size,'sha256':digest})
    if moved:
        batch.mkdir(parents=True, exist_ok=True)
        manifest = batch / 'manifest.json'
        manifest.write_text(json.dumps({
            'schema':1,'created_at':datetime.now(timezone.utc).isoformat(),
            'reason':'legacy_ai_visible_text_filename','markers':list(MARKERS),
            'moved':moved,
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status':'QUARANTINED','moved':len(moved),'manifest':str(manifest)}, ensure_ascii=False))
    else:
        print(json.dumps({'status':'PASS','moved':0,'files':[]}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
