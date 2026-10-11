"""Durable references for Social media shared by scheduler, rescue and API."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tenant_root(account_id: str, banner_dir: str | Path) -> Path:
    account = str(account_id or "")
    if not account or account in {".", ".."} or Path(account).name != account or "\\" in account:
        raise ValueError("SOCIAL_MEDIA_ACCOUNT_INVALID")
    return Path(banner_dir).resolve().parent / "images" / account / "banners" / "premium_ai"


def resolve_banner_path(account_id: str, banner: str | None, *, banner_dir: str | Path) -> str | None:
    """Resolve legacy basenames without searching other accounts or arbitrary paths."""
    value = str(banner or "").strip()
    if not value:
        return None
    canonical = Path(banner_dir).resolve()
    premium = _tenant_root(account_id, canonical).resolve()
    supplied = Path(value)
    if supplied.is_absolute():
        path = supplied.resolve()
        if path.parent not in {canonical, premium}:
            raise ValueError("SOCIAL_MEDIA_PATH_OUTSIDE_ACCOUNT")
        return str(path) if path.is_file() else None
    if supplied.name != value or value in {".", ".."} or "\\" in value:
        raise ValueError("SOCIAL_MEDIA_REFERENCE_INVALID")
    candidates = [p for p in (canonical / value, premium / value) if p.is_file()]
    # A basename collision must not silently attach a different picture.
    if len(candidates) > 1 and file_sha256(candidates[0]) != file_sha256(candidates[1]):
        raise ValueError("SOCIAL_MEDIA_BASENAME_COLLISION")
    for candidate in candidates:
        path = candidate.resolve()
        if path.parent not in {canonical, premium}:
            raise ValueError("SOCIAL_MEDIA_PATH_OUTSIDE_ACCOUNT")
    return str(candidates[0]) if candidates else None


def persist_banner_asset(account_id: str, banner_path: str, *, banner_dir: str | Path) -> dict:
    """Keep the accepted bytes in the durable directory used by stored basenames.

    The paid original remains untouched. Atomic copy, content verification and
    collision detection make replay safe after a crash or competing worker.
    """
    resolved = resolve_banner_path(account_id, banner_path, banner_dir=banner_dir)
    if not resolved:
        raise ValueError("SOCIAL_MEDIA_ASSET_MISSING")
    source = Path(resolved)
    target_root = Path(banner_dir).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / source.name
    digest = file_sha256(source)
    if target.exists():
        if target.resolve().parent != target_root or file_sha256(target) != digest:
            raise ValueError("SOCIAL_MEDIA_BASENAME_COLLISION")
    else:
        fd, temporary = tempfile.mkstemp(prefix=".social-media-", suffix=source.suffix, dir=target_root)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as original:
                shutil.copyfileobj(original, output)
                output.flush()
                os.fsync(output.fileno())
            if file_sha256(temporary) != digest:
                raise ValueError("SOCIAL_MEDIA_COPY_MISMATCH")
            os.chmod(temporary, 0o644)
            # link() cannot overwrite a concurrent writer's different asset.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if file_sha256(target) != digest:
                    raise ValueError("SOCIAL_MEDIA_BASENAME_COLLISION")
            directory_fd = os.open(target_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"path": str(target), "filename": target.name, "sha256": digest, "bytes": target.stat().st_size}
