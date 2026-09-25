#!/root/BORIS/backend/venv/bin/python
from __future__ import annotations

import compileall
import fcntl
import hashlib
import json
import os
import py_compile
import sys
import tempfile
from pathlib import Path

ROOT=Path("/root/BORIS/backend")
APP=ROOT/"app"
MARKER=Path("/run/boris-python-bytecode.json")
LOCK=Path("/run/boris-python-bytecode.lock")
ROOT_FILES=("telephony_runtime_runner.py","telephony_guardian_runner.py")


def source_files():
    files=sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)
    files.extend(ROOT/name for name in ROOT_FILES if (ROOT/name).exists())
    return files


def source_hash(files):
    h=hashlib.sha256()
    for p in files:
        rel=str(p.relative_to(ROOT)).encode()
        data=p.read_bytes()
        h.update(len(rel).to_bytes(4,"big")); h.update(rel)
        h.update(len(data).to_bytes(8,"big")); h.update(data)
    h.update(sys.version.encode())
    return h.hexdigest()


def read_marker():
    try:
        x=json.loads(MARKER.read_text("utf-8"))
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}


def write_marker(payload):
    MARKER.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".boris-bytecode.",dir=str(MARKER.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(payload,f,sort_keys=True); f.write("\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp,MARKER)
    finally:
        try:
            if os.path.exists(tmp): os.unlink(tmp)
        except Exception:
            pass


def main():
    LOCK.parent.mkdir(parents=True,exist_ok=True)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        files=source_files()
        digest=source_hash(files)
        marker=read_marker()
        if marker.get("source_hash")==digest and marker.get("mode")=="checked-hash":
            print("BORIS_BYTECODE_PREFLIGHT=cache-hit")
            return 0
        ok=compileall.compile_dir(
            str(APP),quiet=1,force=True,
            invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        )
        for name in ROOT_FILES:
            p=ROOT/name
            if p.exists():
                try:
                    py_compile.compile(
                        str(p),doraise=True,
                        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
                    )
                except py_compile.PyCompileError:
                    ok=False
        if not ok:
            print("BORIS_BYTECODE_PREFLIGHT=compile-failed",file=sys.stderr)
            return 2
        write_marker({"source_hash":digest,"mode":"checked-hash","python":sys.version.split()[0]})
        print("BORIS_BYTECODE_PREFLIGHT=compiled")
        return 0


if __name__=="__main__":
    raise SystemExit(main())
