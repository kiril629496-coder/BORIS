#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = [
    'package.json', 'package-lock.json', 'next.config.ts', 'tsconfig.json',
    'postcss.config.mjs', 'eslint.config.mjs', 'deploy.sh',
]
# FRONTEND_SOURCE_HASH_RUNTIME_ONLY_V1: scripts/ contains build/deploy/QA helpers
# that are copied into the isolated workspace to run checks, but are not shipped
# inside the published .next runtime artifact. Their edits must not create a fake
# frontend generation or trigger a full Next build. Runtime source remains the
# application/component/library/public surface plus the root build configuration.
SOURCE_DIRS = ['app', 'components', 'lib', 'public']
SKIP_PARTS = {'node_modules', '.git', '.boris-builds', '_ux_recovery_backup_20260827', '__pycache__'}
SKIP_MARKERS = ('.bak', '.before', '.backup', '~', '.pyc')
BACKUP_NAME_RE = re.compile(r'(?:\.broken_[^/]*|\.step\d+|\.old(?:\.\d+)?|\.orig|\.tmp(?:\.\d+)?)$', re.I)

def wanted(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    # FRONTEND_SOURCE_HASH_EXCLUDES_GENERATED_GATEWAY_ZIP_V1: the downloadable
    # ZIP is derived from the four Browser Gateway source files. ZIP container
    # metadata may change on a rebuild even when those source bytes do not; it
    # must not invalidate an otherwise identical immutable frontend artifact.
    if rel.as_posix() == "public/boris-browser-gateway.zip":
        return False
    # FRONTEND_SOURCE_HASH_EXCLUDES_NONRUNTIME_QA_V1: QA/probe scripts do not
    # ship inside the Next.js runtime artifact. Their edits must not create a
    # fake frontend generation and trigger an otherwise unnecessary rebuild.
    if rel.parts and rel.parts[0] == "scripts" and path.name.startswith("qa-"):
        return False
    if any(part in SKIP_PARTS or part.startswith('.next') for part in rel.parts):
        return False
    name = path.name
    if any(marker in name for marker in SKIP_MARKERS):
        return False
    if BACKUP_NAME_RE.search(name):
        return False
    return path.is_file()

paths: list[Path] = []
for name in ROOT_FILES:
    p = ROOT / name
    if p.is_file():
        paths.append(p)
for dirname in SOURCE_DIRS:
    base = ROOT / dirname
    if base.is_dir():
        paths.extend(p for p in base.rglob('*') if wanted(p))

h = hashlib.sha256()
for p in sorted(set(paths), key=lambda x: x.relative_to(ROOT).as_posix()):
    rel = p.relative_to(ROOT).as_posix().encode()
    h.update(len(rel).to_bytes(4, 'big'))
    h.update(rel)
    data = p.read_bytes()
    h.update(len(data).to_bytes(8, 'big'))
    h.update(data)
print(h.hexdigest())
