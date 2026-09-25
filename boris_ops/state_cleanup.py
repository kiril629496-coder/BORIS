#!/usr/bin/env python3
"""Bounded BORIS Ops runtime retention; never touches BORIS product data."""
import json
import time
from pathlib import Path

NOW = time.time()
JOB_DIR = Path('/var/lib/boris-ops/jobs')
TRASH_DIR = Path('/var/lib/boris-ops/trash')
AGENT_DIR = Path('/root/BORIS/boris_ops')
FINAL = {'success', 'failed', 'cancelled', 'interrupted', 'timeout'}
removed = {'jobs': 0, 'trash': 0, 'agent_backups': 0, 'recovery_packs': 0}

# Completed job receipts: 30 days. Running/queued are never deleted.
for fp in JOB_DIR.glob('job_*.json') if JOB_DIR.exists() else []:
    try:
        data = json.loads(fp.read_text(encoding='utf-8'))
        age = NOW - max(float(data.get('finished_at') or 0), fp.stat().st_mtime)
        if data.get('status') in FINAL and age > 30 * 86400:
            fp.unlink(); removed['jobs'] += 1
    except Exception:
        pass

# Recoverable trash: 30 days after deletion or restoration.
if TRASH_DIR.exists():
    for meta in TRASH_DIR.glob('trash_*.json'):
        try:
            data = json.loads(meta.read_text(encoding='utf-8'))
            ts = float(data.get('restored_at') or data.get('deleted_at') or meta.stat().st_mtime)
            if NOW - ts <= 30 * 86400:
                continue
            stored = Path(str(data.get('stored', '')))
            if stored.exists() and TRASH_DIR in stored.parents:
                if stored.is_dir():
                    import shutil; shutil.rmtree(stored)
                else:
                    stored.unlink()
            meta.unlink(); removed['trash'] += 1
        except Exception:
            pass

# Development-safe-edit backups in the component directory: retain 30 days.
for fp in AGENT_DIR.glob('agent.py.bak.*'):
    try:
        if NOW - fp.stat().st_mtime > 30 * 86400:
            fp.unlink(); removed['agent_backups'] += 1
    except Exception:
        pass



# Recovery archives are immutable evidence but can accumulate during development.
# Keep the newest 12 archives and never delete the archive referenced by the
# current install manifest. This touches BORIS Ops backups only.
BACKUP_DIR = Path('/root/BORIS/backups/boris-ops')
try:
    current_pack = None
    manifest = AGENT_DIR / 'INSTALL_MANIFEST.json'
    if manifest.is_file():
        current_pack = Path(str(json.loads(manifest.read_text(encoding='utf-8')).get('recovery_pack') or ''))
    packs = sorted(BACKUP_DIR.glob('recovery-*.tar.gz'), key=lambda x: x.stat().st_mtime, reverse=True) if BACKUP_DIR.exists() else []
    keep = set(packs[:12])
    if current_pack and current_pack.is_file(): keep.add(current_pack)
    for fp in packs:
        if fp not in keep:
            fp.unlink(); removed['recovery_packs'] += 1
except Exception:
    pass

print(json.dumps({'ok': True, 'removed': removed, 'ts': NOW}, ensure_ascii=False))
