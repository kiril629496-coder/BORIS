#!/usr/bin/env python3
from pathlib import Path
import os
ROOT = Path('/root/BORIS/frontend')
unsafe=[]
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        raw=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode('utf-8','ignore').strip()
        if 'next build' not in raw:
            continue
        cwd=(entry/'cwd').resolve()
        env=(entry/'environ').read_bytes().split(b'\0')
        isolated=b'BORIS_ISOLATED_BUILD=1' in env
        in_workspace=str(cwd).startswith(str(ROOT/'.boris-builds'/'work') + '/')
        if not (isolated and in_workspace):
            unsafe.append((int(entry.name),str(cwd),raw))
    except Exception:
        continue
for pid,cwd,cmd in sorted(unsafe):
    print(f'{pid}\t{cwd}\t{cmd}')
raise SystemExit(1 if unsafe else 0)
