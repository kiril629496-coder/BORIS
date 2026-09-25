#!/usr/bin/env python3
"""Bounded retention for immutable BORIS frontend build artifacts.

Safety rules:
- never remove current/LATEST_ARTIFACT/LATEST_READY or explicit protected releases;
- always keep N newest artifacts regardless of age;
- never remove artifacts younger than min_age_seconds (protects parallel build/deploy handoff);
- operate only inside <store>/artifacts after realpath containment checks.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _remove_immutable_tree(path: Path) -> None:
    """Delete one already-unprotected immutable artifact safely.

    Build artifacts are deliberately chmod a-w after creation. Retention runs only
    after containment/protection checks, so it may restore owner-write on that one
    obsolete tree before removing it. Never follow symlinks while changing modes.
    """
    import os
    for item in [path, *path.rglob("*")]:
        try:
            if item.is_symlink():
                continue
            mode=item.stat().st_mode
            os.chmod(item, mode | 0o200)
        except FileNotFoundError:
            continue
    shutil.rmtree(path)


def _marker_target(store: Path, name: str) -> Path | None:
    marker = store / name
    try:
        if marker.is_symlink():
            return marker.resolve()
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
            return Path(raw).resolve() if raw else None
    except Exception:
        return None
    return None


def _maybe_runtime_watch(store: Path, *, dry_run: bool) -> int:
    """Reuse the existing 15-minute frontend maintenance timer as a runtime
    detector without adding another privileged systemd unit.

    The browser watch itself is limited to roughly every 25 minutes. It retries,
    can restart only boris-frontend once, verifies again, and returns non-zero if
    the client mobile/campaign path is still broken.
    """
    default_store = Path("/root/BORIS/frontend/.boris-builds").resolve()
    if dry_run or store != default_store:
        return 0
    status_file = store / "mobile-campaign-watch.status"
    current_release = str((store / "current").resolve()) if (store / "current").exists() else ""
    try:
        age = max(0, int(time.time() - status_file.stat().st_mtime))
        fields = {}
        for line in status_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key.strip()] = value.strip()
        # Skip only a recent PASS for the exact release that is serving now.
        # A recent SKIP/FAIL or a PASS for an older build must be re-verified.
        if (
            age < 1500
            and fields.get("status") == "PASS"
            and fields.get("release") == current_release
        ):
            print(f"RUNTIME_WATCH=SKIP recent_pass_same_release age={age}s")
            return 0
        print(
            "RUNTIME_WATCH=RUN "
            f"reason=status_or_release_changed status={fields.get('status','missing')} "
            f"status_release={fields.get('release','missing')} current_release={current_release}"
        )
    except FileNotFoundError:
        print("RUNTIME_WATCH=RUN reason=status_missing")
    watch = Path("/root/BORIS/frontend/scripts/boris-mobile-campaign-runtime-watch.sh")
    if not watch.is_file():
        print("RUNTIME_WATCH=FAIL reason=watch_script_missing")
        return 3
    # The watcher may need: first full browser pass -> retry -> one safe
    # frontend restart -> final browser pass. In degraded network conditions that
    # is legitimately longer than the previous 230s supervisor budget.
    watch_timeout_seconds = 420
    try:
        proc = subprocess.run([str(watch)], timeout=watch_timeout_seconds, check=False)
        print(f"RUNTIME_WATCH_RC={proc.returncode}")
        return int(proc.returncode)
    except subprocess.TimeoutExpired:
        # Do not leave a stale PASS after the supervisor itself killed a long
        # watch. Persist a machine-readable failure so the very next timer tick
        # re-runs the real user path instead of treating the old PASS as fresh.
        current = store / "current"
        release = str(current.resolve()) if current.exists() else ""
        build_id = ""
        try:
            build_id = (current / "BUILD_ID").read_text(encoding="utf-8").strip()
        except Exception:
            pass
        tmp = status_file.with_suffix(status_file.suffix + ".tmp")
        tmp.write_text(
            "status=FAIL\n"
            "detail=watch_supervisor_timeout\n"
            f"at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"build_id={build_id}\n"
            f"release={release}\n",
            encoding="utf-8",
        )
        tmp.replace(status_file)
        print(f"RUNTIME_WATCH=FAIL reason=watch_supervisor_timeout timeout={watch_timeout_seconds}s")
        return 4


def _run_retention(args: argparse.Namespace) -> tuple[int, Path]:
    """Run artifact housekeeping and return (rc, resolved_store).

    Runtime health checking is intentionally NOT inside this function: a
    housekeeping failure must never suppress the client-facing runtime watch.
    """
    store = Path(args.store).resolve()
    artifacts = (store / "artifacts").resolve()
    if not artifacts.is_dir() or not _inside(artifacts, store):
        print(json.dumps({
            "status": "FAIL",
            "phase": "retention",
            "reason": "invalid_artifact_store",
            "store": str(store),
        }, ensure_ascii=False, indent=2))
        return 5, store

    try:
        protected: set[Path] = set()
        for name in ("current", "LATEST_ARTIFACT", "LATEST_READY"):
            target = _marker_target(store, name)
            if target and _inside(target, artifacts):
                protected.add(target)
        for raw in args.protect:
            if not raw:
                continue
            p = Path(raw).resolve()
            if _inside(p, artifacts):
                protected.add(p)

        rows = [p for p in artifacts.iterdir() if p.is_dir()]
        rows.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        newest = set(rows[: max(1, int(args.keep))])
        now = time.time()
        removed: list[dict] = []
        kept = {"protected": 0, "newest": 0, "recent": 0}

        for path in rows:
            rp = path.resolve()
            if not _inside(rp, artifacts):
                continue
            if rp in protected:
                kept["protected"] += 1
                continue
            if path in newest:
                kept["newest"] += 1
                continue
            age = max(0, int(now - path.stat().st_mtime))
            if age < max(0, int(args.min_age_seconds)):
                kept["recent"] += 1
                continue
            size = 0
            try:
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            except Exception:
                pass
            removed.append({"path": str(path), "age_seconds": age, "bytes": size})
            if not args.dry_run:
                _remove_immutable_tree(path)

        report = {
            "status": "PASS",
            "dry_run": bool(args.dry_run),
            "store": str(store),
            "artifacts_before": len(rows),
            "removed": len(removed),
            "removed_bytes": sum(x["bytes"] for x in removed),
            "kept": kept,
            "protected": sorted(str(p) for p in protected),
            "removed_examples": removed[:20],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0, store
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "phase": "retention",
            "store": str(store),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 5, store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="/root/BORIS/frontend/.boris-builds")
    ap.add_argument("--keep", type=int, default=12)
    ap.add_argument("--min-age-seconds", type=int, default=3600)
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Housekeeping and client runtime supervision are deliberately independent.
    # Even if retention fails, run the mobile/campaign watch and preserve the
    # retention failure in the service exit code.
    retention_rc, store = _run_retention(args)
    watch_rc = _maybe_runtime_watch(store, dry_run=bool(args.dry_run))

    if retention_rc:
        print(f"RETENTION_RC={retention_rc}")
    if watch_rc:
        print(f"RUNTIME_WATCH_FINAL_RC={watch_rc}")

    return retention_rc or watch_rc

if __name__ == "__main__":
    raise SystemExit(main())
