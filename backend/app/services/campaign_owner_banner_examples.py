"""Owner-approved stylistic examples for Campaign Premium banners (2026-10-11).

Pure deterministic guidance only. It never uploads, generates, bills or publishes.
Full-resolution references are in the owner's private ChatGPT Library, not in
Avito media storage. Generated example interiors are illustrative, not stock
or verified photographs of clients' real objects.
"""
from __future__ import annotations

import hashlib

CONTRACT_VERSION = "owner_banner_examples_20261011_v1"
DEFAULT_ACCENT = "#F97316"
PROFILES = {
    "mbroker_27859": {
        "niche": "real_estate",
        "owner_library_file": "/BORIS/Эталоны баннеров/Мария-Новостройки-20261011.png",
        "owner_library_ref": "libfile_8487d880359c8191b96b5e38d1be461a",
        "style": "modern apartment lifestyle, landscaped architecture, warm natural light, expensive editorial spacing",
        "subject": "new-build residences as clearly illustrative architecture; do not depict an invented listing as a factual property",
        "palettes": (
            ("emerald_gold", "#315B38", "deep emerald + warm gold + ivory"),
            ("navy_cream", "#243B63", "deep navy + cream + restrained champagne"),
            ("sage_graphite", "#5B7460", "sage green + graphite + ivory"),
            ("terracotta_sand", "#A85C42", "warm terracotta + soft sand + charcoal"),
            ("charcoal_bronze", "#454545", "charcoal + brushed bronze + clean white"),
        ),
    },
    "albertardashov_79016": {
        "niche": "interior_partitions",
        "owner_library_file": "/BORIS/Эталоны баннеров/Альберт-Перегородки-20261011.png",
        "owner_library_ref": "libfile_461108c1897c8191a72574dca3d19bd7",
        "style": "luxury interiors with black-framed glass partitions, premium natural materials, architectural depth",
        "subject": "glass/metal partition concept, faithful to confirmed material/type; illustrative not a real customer installation",
        "palettes": (
            ("charcoal_champagne", "#33383A", "charcoal + champagne gold + warm white"),
            ("navy_copper", "#263748", "dark navy + copper + cream"),
            ("walnut_ivory", "#70513A", "walnut brown + ivory + gold"),
            ("slate_silver", "#50606D", "slate + silver + pure white"),
            ("forest_cream", "#234D41", "forest green + cream + restrained brass"),
        ),
    },
    "planetazayavki_65985": {
        "niche": "made_to_measure_furniture",
        "owner_library_file": "/BORIS/Эталоны баннеров/Планета-Мебели-20261011.png",
        "owner_library_ref": "libfile_4cc4eb2b96dc81919512a1189392da7f",
        "style": "premium bespoke kitchen/wardrobe interiors, warm light, clean stone and wood, spacious editorial hierarchy",
        "subject": "furniture categories shown illustratively; do not invent exact catalog SKU, materials, installation offers or prices",
        "palettes": (
            ("ivory_gold", "#AE8653", "ivory + matte gold + warm charcoal"),
            ("olive_cream", "#597049", "olive + cream + warm oak"),
            ("graphite_oak", "#424749", "graphite + natural oak + white"),
            ("clay_sand", "#A86850", "clay terracotta + sand + black"),
            ("midnight_beige", "#252E3A", "midnight blue + beige + champagne"),
        ),
    },
}


def owner_variant(account_id: str, identity: str, version: int = 1,
                  previous_palettes: tuple[str, ...] = ()) -> dict | None:
    """Return one stable style for one NEW generation intent; no spend or writes.

    User-provided exact text always supersedes reference sample typography.
    Existing previews are replayed by the caller BEFORE this selector runs.
    """
    profile = PROFILES.get(str(account_id or "").strip())
    if not profile:
        return None
    seed = f"{account_id}|{identity}|{max(1,int(version))}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    palettes = profile["palettes"]
    index = int.from_bytes(digest[:8], "big") % len(palettes)
    previous = set(str(x) for x in previous_palettes if x)
    if len(previous) < len(palettes):
        for offset in range(len(palettes)):
            trial = palettes[(index + offset) % len(palettes)]
            if trial[0] not in previous:
                index = (index + offset) % len(palettes)
                break
    family, accent, palette = palettes[index]
    layout_seed = int.from_bytes(digest[8:12], "big")
    angles = ("wide architectural hero", "close-up of real category materials",
              "bright lifestyle interior", "editorial foreground/background contrast",
              "detail-led scene with spacious typography")
    angle = angles[layout_seed % len(angles)]
    guidance = (
        f"OWNER-APPROVED OCT-2026 REFERENCE FOR {profile['niche']}; style inspiration ONLY, "
        f"not a product photo or permission to publish. Visual direction: {profile['style']}. "
        f"Color variant {family}: {palette}. Hero angle: {angle}. "
        f"Subject rule: {profile['subject']}. "
        "Make the Russian main headline unusually LARGE, bold and mobile-readable, ideally "
        "one or two strong lines; prioritize one factual offer, up to three SHORT proof-safe "
        "benefits and ONE prominent Avito chat CTA. Use generous contrast and negative space. "
        "Create a visibly original design; change camera/viewpoint, color and composition across "
        "new ads, never cloning the reference or another generated banner. "
        "No made-up prices, claims, discounts, guarantees, logos, property photos or phone numbers. "
        "Only render provided, confirmed offer text; no invented fine print. "
        "Maintain the preselected campaign composition and icon diversification rules."
    )
    return {
        "contract": CONTRACT_VERSION, "niche": profile["niche"],
        "palette_key": family, "accent": accent, "palette": palette,
        "owner_library_file": profile["owner_library_file"],
        "owner_library_ref": profile["owner_library_ref"],
        "reference_style": guidance,
    }


def combine_reference_style(variant: dict | None, existing_style: str) -> str:
    current = str(existing_style or "").strip()[:1000]
    if not variant:
        return current
    # Keep the owner benchmark within the existing prompt's 2500-char limit.
    return (str(variant["reference_style"]) + ("\nCUSTOMER STYLE: " + current if current else ""))[:2350]
