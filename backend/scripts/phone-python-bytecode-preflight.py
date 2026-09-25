#!/root/BORIS/backend/venv/bin/python
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import py_compile
import sys
import tempfile
from pathlib import Path

ROOT = Path("/root/BORIS/backend")
MARKER = Path("/run/boris-phone-bytecode.json")
LOCK = Path("/run/boris-phone-bytecode.lock")

EXACT_FILES = (
    "telephony_guardian_runner.py",
    "telephony_runtime_runner.py",
    "asterisk_bridge_runner.py",
    "app/services/mcn_core.py",
    "app/services/mcn_network_guard.py",
    "app/services/asterisk_gateway.py",
    "app/services/phone_media_gateway.py",
    "app/services/telphin_sip_trunk.py",
    "app/api/mcn_phone.py",
    "app/api/calltracking.py",
)

GLOBS = (
    "app/services/telephony*.py",
    "app/api/telephony*.py",
    "scripts/phone-*.py",
)


def source_files(root: Path = ROOT) -> list[Path]:
    files: set[Path] = set()
    for rel in EXACT_FILES:
        p = root / rel
        if p.exists() and p.is_file():
            files.add(p)
    for pattern in GLOBS:
        for p in root.glob(pattern):
            if p.is_file() and "__pycache__" not in p.parts:
                files.add(p)
    return sorted(files)


def source_hash(files: list[Path], root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for p in files:
        rel = str(p.relative_to(root)).encode()
        data = p.read_bytes()
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    h.update(sys.version.encode())
    return h.hexdigest()


def _read_marker(path: Path = MARKER) -> dict:
    try:
        x = json.loads(path.read_text("utf-8"))
        return x if isinstance(x, dict) else {}
    except Exception:
        return {}


def _write_marker(payload: dict, path: Path = MARKER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".boris-phone-bytecode.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def check_files(files: list[Path]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for p in files:
        try:
            py_compile.compile(
                str(p),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
        except py_compile.PyCompileError as exc:
            errors.append(f"{p}:{exc}")
        except Exception as exc:
            errors.append(f"{p}:{type(exc).__name__}:{exc}")
    return not errors, errors


def run(root: Path = ROOT, marker: Path = MARKER, lock_path: Path = LOCK) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        files = source_files(root)
        if not files:
            print("BORIS_PHONE_BYTECODE_PREFLIGHT=no-files", file=sys.stderr)
            return 2
        digest = source_hash(files, root)
        cached = _read_marker(marker)
        if cached.get("source_hash") == digest and cached.get("mode") == "checked-hash":
            print(f"BORIS_PHONE_BYTECODE_PREFLIGHT=cache-hit files={len(files)}")
            return 0
        ok, errors = check_files(files)
        if not ok:
            for err in errors[:20]:
                print(err, file=sys.stderr)
            print(
                f"BORIS_PHONE_BYTECODE_PREFLIGHT=compile-failed files={len(files)} errors={len(errors)}",
                file=sys.stderr,
            )
            return 2
        _write_marker(
            {
                "source_hash": digest,
                "mode": "checked-hash",
                "python": sys.version.split()[0],
                "files": len(files),
            },
            marker,
        )
        print(f"BORIS_PHONE_BYTECODE_PREFLIGHT=compiled files={len(files)}")
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
