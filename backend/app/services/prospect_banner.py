from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

OUTREACH_DIR = Path("/root/BORIS/backend/generated/outreach_banners")
SOCIAL_DIR = Path("/root/BORIS/backend/posting_banners")
POSTING_LOG = Path("/root/BORIS/backend/posting.log")
DEFAULT_SOCIAL_PROJECT = "Океан Клиентов и Борис"


def _valid_png(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower() == ".png" and path.stat().st_size >= 50_000
    except OSError:
        return False


def _social_project_paths() -> list[Path]:
    """Recover only banners actually generated for the BORIS owner social project.

    The posting directory is owner-scoped, not project-scoped: the same u2_ prefix
    is also used by unrelated projects. The production log is the canonical
    provenance trail linking a generated banner to its project.
    """
    target = (
        os.getenv("PROSPECT_REUSE_SOCIAL_PROJECT")
        or DEFAULT_SOCIAL_PROJECT
    ).strip()
    if not target or not POSTING_LOG.exists():
        return []

    try:
        lines = POSTING_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    out: list[Path] = []
    waiting_for_target_banner = False
    theme_prefix = "[autopost_projects] "
    ready_re = re.compile(r"\[banner_openai\] ready:\s+(\S+\.png)\b")

    for line in lines:
        if line.startswith(theme_prefix) and " тема:" in line:
            project = line[len(theme_prefix):].split(" тема:", 1)[0].strip()
            waiting_for_target_banner = project == target
            continue

        if waiting_for_target_banner and "[banner_openai] ready:" in line:
            match = ready_re.search(line)
            if match:
                path = Path(match.group(1))
                if path.parent == SOCIAL_DIR and _valid_png(path):
                    out.append(path)
            waiting_for_target_banner = False

    return out


def _candidate_paths() -> list[Path]:
    """Existing BORIS-owned banners only. Never generates a new image."""
    candidates: list[Path] = []

    # Existing banners already made specifically for cold outreach.
    if OUTREACH_DIR.exists():
        candidates.extend(p for p in OUTREACH_DIR.glob("*.png") if _valid_png(p))

    # Existing social banners proven by posting.log to belong to
    # "Океан Клиентов и Борис", never merely by owner filename prefix.
    candidates.extend(_social_project_paths())

    unique: dict[str, Path] = {}
    for path in candidates:
        try:
            unique[str(path.resolve())] = path
        except OSError:
            continue

    return sorted(unique.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def banner_pool_stats() -> dict:
    pool = _candidate_paths()
    return {
        "mode": "reuse_only",
        "project": os.getenv("PROSPECT_REUSE_SOCIAL_PROJECT") or DEFAULT_SOCIAL_PROJECT,
        "count": len(pool),
        "outreach_existing": sum(1 for p in pool if p.parent == OUTREACH_DIR),
        "social_existing": sum(1 for p in pool if p.parent == SOCIAL_DIR),
        "paid_generation_enabled": False,
    }


def render_banner(
    *,
    company: str,
    niche: str,
    city: str = "",
    campaign_id: int | None = None,
    member_id: int | None = None,
) -> str:
    """Return an existing BORIS banner; never call an image generator.

    Selection is deterministic per recipient, giving stable retries and natural
    creative rotation without any new AI/image spend.
    """
    pool = _candidate_paths()
    if not pool:
        raise FileNotFoundError("PROSPECT_REUSE_BANNER_POOL_EMPTY")

    key = f"{campaign_id}:{member_id}:{company}:{niche}:{city}"
    idx = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16) % len(pool)
    return str(pool[idx])
