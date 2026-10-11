"""Read-only Campaign content diversity scorecard."""
from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher


_GEO_WORDS = re.compile(
    r"\b(?:москва|санкт[- ]?петербург|спб|уфа|ижевск|стерлитамак|казань|"
    r"екатеринбург|новосибирск|ростов(?:-на-дону)?)\b",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_NONWORD = re.compile(r"[^0-9a-zа-яё]+", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def normalize_copy(value: str) -> str:
    text = _TAGS.sub(" ", str(value or "")).lower()
    text = _GEO_WORDS.sub(" город ", text)
    text = _DIGITS.sub(" N ", text)
    text = _NONWORD.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def hypothesis_family(payload: dict, row_hypothesis_id: str = "") -> str:
    for key in ("creative_angle", "angle"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    declared = str(payload.get("hypothesis_id") or row_hypothesis_id or "").strip()
    if declared:
        if declared.startswith("formula:"):
            parts = declared.split(":")
            return parts[1] if len(parts) > 1 else declared
        return declared.split(":", 1)[0]
    text = str(payload.get("hypothesis") or "").strip()
    if ":" in text:
        return text.split(":", 1)[0].strip().lower()
    return "unspecified"


def analyze_content_diversity(items: list[dict]) -> dict:
    total = len(items)
    titles = [str(x.get("title") or "").strip() for x in items]
    descriptions = [str(x.get("description") or "").strip() for x in items]
    norm_titles = [normalize_copy(x) for x in titles]
    norm_desc = [normalize_copy(x)[:700] for x in descriptions]
    families = [str(x.get("hypothesis_family") or "unspecified") for x in items]

    title_exact = Counter(titles)
    desc_exact = Counter(descriptions)
    title_norm = Counter(norm_titles)
    desc_norm = Counter(norm_desc)
    family_counts = Counter(families)

    title_family_limit = max(2, math.ceil(total * 0.10)) if total else 0
    desc_family_limit = max(1, math.ceil(total * 0.05)) if total else 0
    max_family_limit = max(1, math.ceil(total * 0.60)) if total else 0

    near_pairs = []
    if total <= 250:
        for idx, left in enumerate(norm_titles):
            if not left:
                continue
            for j in range(idx + 1, total):
                right = norm_titles[j]
                if left == right:
                    continue
                ratio = SequenceMatcher(None, left, right).ratio()
                if ratio >= 0.92:
                    near_pairs.append((int(items[idx].get("id") or 0),
                                       int(items[j].get("id") or 0),
                                       round(ratio, 3)))

    checks = {
        "titles_present": all(bool(x) for x in titles) if total else False,
        "descriptions_present": all(bool(x) for x in descriptions) if total else False,
        "title_family_concentration": max(title_norm.values(), default=0) <= title_family_limit,
        "description_family_concentration": max(desc_norm.values(), default=0) <= desc_family_limit,
        "hypothesis_family_count": (len(family_counts) >= min(3, total)) if total >= 3 else bool(total),
        "hypothesis_monoculture": max(family_counts.values(), default=0) <= max_family_limit,
    }
    warnings = []
    if max(title_exact.values(), default=0) > 1:
        warnings.append("title_exact_repeats")
    if max(desc_exact.values(), default=0) > 1:
        warnings.append("description_exact_repeats")
    if near_pairs:
        warnings.append("near_title_pairs")
    blockers = [key for key, ok in checks.items() if not ok]
    status = "FAIL" if blockers else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    return {
        "status": status,
        "diversity_contract": 1,
        "items": total,
        "checks": checks,
        "warnings": warnings,
        "blockers": blockers,
        "titles": {
            "unique_exact": len(title_exact),
            "max_exact_repeat": max(title_exact.values(), default=0),
            "unique_normalized": len(title_norm),
            "max_normalized_repeat": max(title_norm.values(), default=0),
            "normalized_repeat_limit": title_family_limit,
            "near_pairs_92pct": len(near_pairs),
            "near_examples": near_pairs[:20],
        },
        "descriptions": {
            "unique_exact": len(desc_exact),
            "max_exact_repeat": max(desc_exact.values(), default=0),
            "unique_normalized": len(desc_norm),
            "max_normalized_repeat": max(desc_norm.values(), default=0),
            "normalized_repeat_limit": desc_family_limit,
        },
        "hypotheses": {
            "families": dict(family_counts),
            "family_count": len(family_counts),
            "largest_family": max(family_counts.values(), default=0),
            "largest_family_limit": max_family_limit,
        },
    }
