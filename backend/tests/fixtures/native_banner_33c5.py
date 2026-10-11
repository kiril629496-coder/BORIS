# -*- coding: utf-8 -*-
"""Restored July-2026 FULL_AI premium banner engine.

This is intentionally separate from Banner V4:
- V4 remains deterministic/local-text.
- Premium restores full AI art direction with gpt-image-2.
- one explicit click = one paid image generation;
- hard estimated cost cap <= 4 RUB;
- preview first; media approval is a separate action;
- idempotency and version history are account-scoped.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.image_service import estimate_kopeks

MODEL = "gpt-image-2"
QUALITY = "low"
APPROVED_PAID_PRESET = ("gpt-image-2", "low")
APPROVED_SOURCE_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
TARGET_COST_RUB = 3.0
MAX_COST_RUB = 4.0
ENGINE = "full_ai_premium"
ENGINE_SOURCE = "july_2026_restored"
META_PREFIX = "premium_banner_versions:"
LOCK_ROOT = Path("/root/BORIS/backend/run/banner_premium_locks")
IMAGES_ROOT = Path("/root/BORIS/backend/images")
CIRCUIT_PATH = Path("/root/BORIS/backend/run/premium_banner_cost_circuit.json")
VISION_QA_OPERATION = "banner_premium_vision_qa"
VISION_QA_BATCH_OPERATION = "banner_premium_vision_qa_batch_v1"
VISION_QA_BATCH_MAX = 4

FORMAT_PRESETS = {
    "1:1": {"source_size": "1024x1024", "target": (1024, 1024), "label": "1:1"},
    "4:5": {"source_size": "1024x1536", "target": (1080, 1350), "label": "4:5"},
    "16:9": {"source_size": "1536x1024", "target": (1280, 720), "label": "16:9"},
    "avito_pc": {"source_size": "1536x1024", "target": (1202, 436), "label": "Avito PC"},
    "avito_mobile": {"source_size": "1536x1024", "target": (1242, 936), "label": "Avito Mobile"},
    "vk": {"source_size": "1024x1024", "target": (1024, 1024), "label": "VK"},
    "telegram": {"source_size": "1024x1024", "target": (1024, 1024), "label": "Telegram"},
}

HYPOTHESES = {
    "product_hero": (
        "PRODUCT HERO: make the product/service itself the dominant visual object. "
        "Show the real result, interface, object or service context clearly. "
        "Avoid generic stock people unless the brief explicitly requires them."
    ),
    "pain_solution": (
        "PAIN TO SOLUTION: create a clear visual contrast between the customer's problem "
        "and the offered solution, but keep the layout premium, clean and commercially credible."
    ),
    "result_analytics": (
        "RESULT / ANALYTICS: make the measurable result, dashboard, workflow, comparison or "
        "business outcome the visual center of attention. Avoid fake numbers unless supplied."
    ),
    "dashboard_control": (
        "CONTROL CENTER: show a sophisticated command-center/product-dashboard visual language, "
        "realistic data flows and business control, not science-fiction hologram clutter."
    ),
    "people_product": (
        "PEOPLE + PRODUCT: use a believable person only when it strengthens trust; the product "
        "or service result must remain primary. Avoid generic smiling stock-photo poses."
    ),
}

COMPOSITION_FAMILIES = {
    "left": (
        "LEFT COMPOSITION: keep the main text block on the left and the hero object/scene on the right. "
        "Use strong negative space and one dominant focal point; do not build an app-like white card."
    ),
    "right": (
        "RIGHT COMPOSITION: keep the hero object/scene on the left and the main text block on the right. "
        "Use clear hierarchy and premium spacing; avoid mirrored repetition of the left layout."
    ),
    "split": (
        "SPLIT COMPOSITION: create two intentional visual zones for image and typography, with a designed transition. "
        "Do not make it look like a generic HTML card or a simple 50/50 template."
    ),
    "editorial": (
        "EDITORIAL COMPOSITION: let the photography/art direction dominate like a premium magazine advertisement; "
        "integrate typography into the scene with elegant hierarchy and generous negative space."
    ),
    "diagonal": (
        "DIAGONAL COMPOSITION: build visual movement on a diagonal using subject placement, lighting, depth and type. "
        "Keep the hierarchy clean and commercial rather than decorative or chaotic."
    ),
    "callout": (
        "CALLOUT COMPOSITION: use one or at most two strong callouts for the verified offer/benefit/CTA. "
        "The hero scene remains primary; never turn the banner into a table, dashboard or pile of badges."
    ),
}
DEFAULT_COMPOSITION_FAMILY = "left"

DEFAULT_HYPOTHESIS = "product_hero"

OWNER_REFERENCE_VERSION = "owner_visual_reference_v3_20261005"
OWNER_REFERENCE_IMAGES = {
    "manicure": "/root/BORIS/backend/images/social-reference-library/manicure-owner-approved-v1.jpg",
    "pedicure": "/root/BORIS/backend/images/social-reference-library/pedicure-owner-approved-v1.jpg",
    "windows": "/root/BORIS/backend/images/social-reference-library/windows-july-approved-v1.png",
    "software": "/root/BORIS/backend/images/social-reference-library/software-july-approved-v1.png",
    "kitchen": "/root/BORIS/backend/images/social-reference-library/kitchen-july-approved-v1.png",
}
OWNER_STYLE_CONTRACTS = {
    "manicure": {
        "keywords": ("маникюр", "ногти", "nail"),
        "palette": "deep black + electric violet + clean ivory, optional chrome highlights",
        "typography": "selling bold modern grotesk / geometric sans; very large confident headline; 3-level hierarchy; no classical serif headline",
        "composition": "premium beauty campaign; large hero hand/nails; asymmetrical commercial layout; strong headline block; 2-3 compact benefit icons",
        "reference_notes": "Owner-approved benchmark: black/violet/ivory beauty ad, hero hand on the right, massive bold sans headline on the left, purple accent word, three circular premium icons, cinematic glossy lighting.",
        "microcopy": ("Больше чем маникюр", "Уверенность в деталях", "Красота работает на тебя"),
        "richness": "editorial richness: 1-3 short secondary microcopy accents, thin rules/markers, one optional handwritten-style accent, layered but clean visual hierarchy",
        "avoid": "beige salon card, spa-template look, weak thin headline, classical serif headline, empty photo+text layout, candles/flowers/wine/spa clutter",
    },
    "pedicure": {
        "keywords": ("педикюр", "стоп", "feet", "foot"),
        "palette": "deep emerald + cream + restrained gold + charcoal",
        "typography": "bold premium sans/grotesk; high-contrast headline; clean compact subhead; premium icon labels",
        "composition": "luxury wellness/beauty commercial; hero feet; large top-left headline; three clean benefit icons; rich material textures but controlled",
        "reference_notes": "Owner-approved benchmark: emerald/cream/gold pedicure ad, hero feet on the right, huge bold sans headline, three gold-outline icons, premium marble/textile atmosphere.",
        "microcopy": ("Забота в любое время года", "Уход сегодня — уверенность завтра", "Комфорт каждый день"),
        "richness": "editorial richness: 1-3 compact secondary phrases, refined gold rules/markers, small premium labels integrated into the scene, never clutter",
        "avoid": "cheap spa flyer, muddy brown, weak beige background, too many candles/props, serif-heavy salon typography",
    },
    "roofing": {
        "keywords": ("металлочереп", "кровл", "grand line", "фасад", "забор", "стройматериал"),
        "palette": "deep navy + steel graphite + vivid orange + clean white",
        "typography": "heavy selling industrial sans/grotesk; oversized product headline; orange emphasis; clean technical hierarchy",
        "composition": "premium building-material campaign; strong house/roof or product-profile hero; one dominant product story; 3 compact benefits/icons; no catalogue clutter",
        "reference_notes": "Owner quality bar for construction materials: premium dark navy/graphite advertising creative with vivid orange accent, architectural/product hero, strong bold headline and clear benefit icons.",
        "microcopy": ("Материал, который работает на дом", "Надёжность в каждой детали", "От склада до объекта"),
        "richness": "editorial richness: 1-3 technical-commercial microcopy accents, thin construction-grid lines, compact material/spec callout, one confident accent label",
        "avoid": "windows-family lifestyle benchmark, generic construction collage, tiny catalogue cards, muddy brown palette, weak technical flyer",
    },
    "windows": {
        "keywords": ("окна", "окно", "стеклопакет", "оконн"),
        "palette": "vivid cobalt + deep navy + white",
        "typography": "very bold selling sans/grotesk; oversized headline; blue emphasis word; clean supporting line",
        "composition": "bright premium interior with large windows and believable family/lifestyle hero; strong upper-left headline; bottom benefit row with 3 icons",
        "reference_notes": "Benchmark: bright blue/white premium windows campaign with large headline, family by floor-to-ceiling windows, three large benefit icons in a blue lower rail.",
        "microcopy": ("Больше света — больше жизни", "Дом начинается с хороших окон", "Комфорт надолго"),
        "richness": "editorial richness: 1-3 lifestyle microcopy phrases, small corner label, optional handwritten emotional accent, thin rules and spacious magazine-style hierarchy",
        "avoid": "construction flyer clutter, tiny text, stock-only image without hierarchy, grey generic corporate palette",
    },
    "software": {
        "keywords": ("разработка", "crm", "software", "программ", "автоматизац", "сайт"),
        "palette": "deep navy + black + electric blue + subtle violet + clean white",
        "typography": "heavy conversion-oriented grotesk; huge white headline with electric-blue emphasis; crisp B2B hierarchy",
        "composition": "premium B2B tech campaign with monitor/dashboard hero, dark cinematic city/office environment, 3 clean benefit icons, disciplined editorial depth",
        "reference_notes": "Benchmark: deep navy/black software ad, massive white and electric-blue headline, dashboard/monitor hero, three blue-violet benefit icons, premium nocturnal tech atmosphere.",
        "microcopy": ("От идеи до результата", "Больше возможностей для роста", "Надёжный технологический партнёр"),
        "richness": "editorial richness: 1-3 B2B microcopy accents, small metric/label zones, refined grid lines, one strong CTA area, cinematic but disciplined",
        "avoid": "random robots, hologram clutter, generic blue SaaS gradient, tiny dashboard text as main visual",
    },
    "kitchen": {
        "keywords": ("кухн", "мебел", "шкаф", "гарнитур"),
        "palette": "deep black + warm orange + white",
        "typography": "bold commercial sans; oversized headline; strong orange emphasis; premium clean labels",
        "composition": "cinematic premium interior hero with strong lighting, headline upper-left, three benefit icons, dark luxury material feel",
        "reference_notes": "Benchmark: black/orange premium kitchen campaign with dramatic interior, bold headline, three clean orange-outline icons.",
        "microcopy": ("Качество, которое видно", "Интерьер под ваш ритм", "Материалы. Размеры. Детали."),
        "richness": "editorial richness: 1-3 premium interior microcopy accents, fine rules, compact material callout and one small signature-style phrase where appropriate",
        "avoid": "catalogue grid, low-contrast beige furniture ad, too many small product cards",
    },
}

def _owner_style_contract(description: str, exact_title: str = "", exact_subtitle: str = "") -> tuple[str, dict]:
    hay = " ".join([_clean(description, 3000), _clean(exact_title, 300), _clean(exact_subtitle, 300)]).lower().replace("ё", "е")
    priority = ("pedicure", "manicure", "roofing", "windows", "software", "kitchen")
    for name in priority:
        profile = OWNER_STYLE_CONTRACTS[name]
        if any(str(k).lower().replace("ё", "е") in hay for k in profile.get("keywords") or ()):
            contract = (
                f"OWNER-APPROVED VISUAL BENCHMARK [{name}] / {OWNER_REFERENCE_VERSION}: "
                f"Palette: {profile['palette']}. "
                f"Typography: {profile['typography']}. "
                f"Composition: {profile['composition']}. "
                f"Reference: {profile['reference_notes']} "
                f"Editorial richness: {profile.get('richness') or 'clean layered commercial hierarchy'}. "
                f"Allowed secondary microcopy: {' | '.join(profile.get('microcopy') or ())}. "
                f"Reject/avoid: {profile['avoid']}."
            )
            return name, {"name": name, **profile, "contract": contract}
    return "generic", {
        "name": "generic",
        "palette": "strong niche-appropriate commercial palette with one dominant accent and clear contrast",
        "typography": "selling modern sans/grotesk with strong hierarchy; no weak thin headline",
        "composition": "complete advertising creative, not stock photo with text",
        "reference_notes": "Use the owner quality bar: bold, clean, expensive, conversion-focused.",
        "avoid": "cheap template look, weak typography, muddy palette, clutter",
        "contract": (
            f"OWNER-APPROVED VISUAL BENCHMARK [generic] / {OWNER_REFERENCE_VERSION}: "
            "Use selling modern sans/grotesk typography, decisive color system, one clear hero and a complete commercial layout. "
            "Reject cheap template styling, muddy colors and ordinary photo-plus-text layouts."
        ),
    }

# Diversity contract: a new paid intent must not merely repaint the same layout.
# Profiles are deterministic per intent/version so idempotent replays remain exact,
# while regenerations rotate through materially different compositions.
COMPOSITION_PROFILES = (
    (
        "hero_left_text_right",
        "HERO LEFT / TEXT RIGHT: put the dominant product/scene on the left 55-65% of the canvas; "
        "build the text hierarchy on the right with generous negative space. Do not center everything.",
    ),
    (
        "text_left_hero_right",
        "TEXT LEFT / HERO RIGHT: keep headline/subtitle/CTA in a strong left column; place the hero object "
        "or service scene on the right with depth and a clean visual counterweight.",
    ),
    (
        "split_screen",
        "SPLIT SCREEN: use a deliberate two-zone composition with a strong visual divide, contrast, before/after "
        "or offer/result logic. Keep the split premium, not a cheap collage.",
    ),
    (
        "centered_editorial",
        "CENTERED EDITORIAL: use one monumental centered hero with editorial typography around or above it, "
        "large breathing room and only a few carefully placed support elements.",
    ),
    (
        "diagonal_dynamic",
        "DIAGONAL DYNAMIC: create controlled diagonal visual flow using architecture, furniture lines, light, "
        "perspective or shapes while keeping all text perfectly level and readable.",
    ),
    (
        "side_rail",
        "SIDE RAIL: use a narrow vertical information rail on one side and a large cinematic hero scene on the other. "
        "Benefits should read as a compact editorial sequence, not repeated circular badges.",
    ),
    (
        "floating_badges",
        "LIFESTYLE + FLOATING CALLOUTS: use a believable in-context scene with 2-3 spatially separated callouts "
        "anchored to relevant details. Avoid a straight row of identical benefit badges.",
    ),
    (
        "product_closeup",
        "PRODUCT CLOSE-UP: crop tightly around one tactile product/detail/result, using oversized typography and "
        "small supporting labels around the perimeter. Make the object feel premium and dimensional.",
    ),
)

ICON_STYLE_PROFILES = (
    (
        "outline_circle",
        "OUTLINE ICONS: thin custom line pictograms in restrained circular frames; vary each pictogram by benefit.",
    ),
    (
        "rounded_square",
        "ROUNDED-SQUARE ICONS: compact pictograms inside rounded squares with subtle depth; do not use circles.",
    ),
    (
        "solid_symbol",
        "SOLID SYMBOLS: simple filled pictograms with no enclosing shape; use strong silhouette and spacing.",
    ),
    (
        "editorial_line",
        "EDITORIAL LINE ICONS: minimal standalone line drawings with a small rule/divider and text; no badge containers.",
    ),
    (
        "mini_pictogram",
        "MINI PICTOGRAMS: small bespoke pictograms integrated next to each benefit label, each visually different.",
    ),
    (
        "numbered_markers",
        "NUMBERED MARKERS: use 01/02/03-style editorial markers with tiny supporting symbols instead of repeated icon badges.",
    ),
)


def _select_diversity_profiles(seed: str, version: int = 1) -> tuple[tuple[str, str], tuple[str, str]]:
    digest = hashlib.sha256(_clean(seed, 1000).encode("utf-8", "ignore")).digest()
    base_comp = int.from_bytes(digest[:4], "big") % len(COMPOSITION_PROFILES)
    # Explicit version rotation guarantees a regeneration of the same banner changes layout.
    comp_idx = (base_comp + max(0, int(version or 1) - 1)) % len(COMPOSITION_PROFILES)
    icon_idx = (
        int.from_bytes(digest[4:8], "big")
        + comp_idx
        + max(0, int(version or 1) - 1)
    ) % len(ICON_STYLE_PROFILES)
    return COMPOSITION_PROFILES[comp_idx], ICON_STYLE_PROFILES[icon_idx]



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]



def _load_cost_circuit() -> dict:
    try:
        if not CIRCUIT_PATH.is_file():
            return {"tripped": False, "state": "closed"}
        data = json.loads(CIRCUIT_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"tripped": True, "state": "invalid", "reason": "circuit_state_invalid"}
        return {"tripped": bool(data.get("tripped")), "state": "open" if data.get("tripped") else "closed", **data}
    except Exception as exc:
        # Cost control is fail-closed: an unreadable circuit must never permit a paid call.
        return {
            "tripped": True,
            "state": "unreadable",
            "reason": "circuit_state_unavailable",
            "error": type(exc).__name__,
        }


def _trip_cost_circuit(*, actual_cost_rub: float, total_cost_rub: float, banner_id: str, version: int) -> dict:
    payload = {
        "tripped": True,
        "state": "open",
        "reason": "actual_cost_limit_exceeded",
        "model": MODEL,
        "quality": QUALITY,
        "actual_cost_rub": round(float(actual_cost_rub or 0), 4),
        "total_cost_rub": round(float(total_cost_rub or 0), 4),
        "max_cost_rub": MAX_COST_RUB,
        "banner_id": str(banner_id or ""),
        "version": int(version or 0),
        "tripped_at": _now_iso(),
    }
    CIRCUIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CIRCUIT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, CIRCUIT_PATH)
    return payload


def _artifact_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _perceptual_hash(path: Path) -> str:
    with Image.open(path) as img:
        img = img.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
        values = list(img.getdata())
    avg = sum(values) / max(len(values), 1)
    bits = "".join("1" if value >= avg else "0" for value in values)
    return f"{int(bits, 2):016x}"


def _hash_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def _technical_image_qa(path: Path, expected_size: tuple[int, int], previous_versions: list[dict]) -> dict:
    issues: list[str] = []
    width = height = 0
    dynamic_range = 0
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            width, height = rgb.size
            extrema = rgb.convert("L").getextrema()
            dynamic_range = int(extrema[1] - extrema[0]) if extrema else 0
    except Exception as exc:
        return {
            "passed": False,
            "issues": ["image_decode_failed:" + type(exc).__name__],
            "checked_at": _now_iso(),
        }

    if (width, height) != tuple(expected_size):
        issues.append(f"unexpected_dimensions:{width}x{height}")
    if path.stat().st_size <= 1024:
        issues.append("artifact_too_small")
    if dynamic_range < 6:
        issues.append("image_nearly_blank")

    artifact_hash = _artifact_sha256(path)
    phash = _perceptual_hash(path)
    duplicate_of = None
    for row in previous_versions:
        previous_sha = str(row.get("artifact_sha256") or "")
        previous_phash = str(row.get("perceptual_hash") or "")
        if previous_sha and previous_sha == artifact_hash:
            duplicate_of = int(row.get("version") or 0)
            issues.append("exact_duplicate")
            break
        if previous_phash and _hash_distance(previous_phash, phash) <= 2:
            duplicate_of = int(row.get("version") or 0)
            issues.append("near_duplicate")
            break

    return {
        "passed": not issues,
        "issues": issues,
        "width": width,
        "height": height,
        "dynamic_range": dynamic_range,
        "artifact_sha256": artifact_hash,
        "perceptual_hash": phash,
        "duplicate_of_version": duplicate_of,
        "checked_at": _now_iso(),
    }


def _image_data_uri(path: Path, max_side: int = 768) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((int(max_side), int(max_side)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=86, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _vision_contract(
    *,
    description: str,
    exact_title: str,
    exact_subtitle: str,
    exact_cta: str,
    exact_price: str,
    advantages: list[str] | None,
    hypothesis: str,
    fmt: str,
    composition_family: str = "",
    composition_profile: tuple[str, str] | None = None,
    icon_profile: tuple[str, str] | None = None,
) -> dict:
    family = composition_family if composition_family in COMPOSITION_FAMILIES else DEFAULT_COMPOSITION_FAMILY
    owner_niche, owner_style = _owner_style_contract(description, exact_title, exact_subtitle)
    return {
        "description": _clean(description, 2200),
        "headline": _clean(exact_title, 180),
        "subtitle": _clean(exact_subtitle, 180),
        "cta": _clean(exact_cta, 120),
        "price": _clean(exact_price, 120),
        "advantages": [_clean(x, 100) for x in (advantages or []) if _clean(x, 100)][:3],
        "hypothesis": hypothesis,
        "format": fmt,
        "composition_family": family,
        "composition_profile": _clean((composition_profile or ("", ""))[0], 80),
        "icon_profile": _clean((icon_profile or ("", ""))[0], 80),
        "owner_niche": owner_niche,
        "owner_reference_version": OWNER_REFERENCE_VERSION,
        "owner_palette": owner_style.get("palette"),
        "owner_typography": owner_style.get("typography"),
        "owner_composition": owner_style.get("composition"),
        "owner_reference_notes": owner_style.get("reference_notes"),
        "owner_allowed_secondary_microcopy": list(owner_style.get("microcopy") or ()),
        "owner_editorial_richness": owner_style.get("richness"),
        "owner_avoid": owner_style.get("avoid"),
    }


def _normalize_vision_result(out: dict, meta: dict | None = None) -> dict:
    meta = dict(meta or {})
    if not isinstance(out, dict):
        return {
            "passed": False,
            "issues": ["vision_qa_invalid_envelope"],
            "qa_cost_rub": round(float(meta.get("cost_rub") or 0), 4),
            "cache_hit": bool(meta.get("cache_hit")),
            "provider_request_id": meta.get("provider_request_id"),
            "request_id": meta.get("request_id"),
            "checked_at": _now_iso(),
        }
    required = (
        "semantic_match", "headline_ok", "exact_text_ok", "spelling_ok",
        "forbidden_claims_ok", "composition_ok", "crop_safe_ok",
        "professional_ok", "mobile_ok", "selling_typography_ok",
        "palette_ok", "reference_style_match", "editorial_richness_ok",
        "secondary_copy_density_ok",
    )
    booleans = {key: bool(out.get(key)) for key in required}
    raw_issues = out.get("issues")
    if isinstance(raw_issues, list):
        issues = [str(x)[:180] for x in raw_issues if str(x).strip()][:12]
    elif raw_issues is None or str(raw_issues).strip().lower() in {"", "none", "null"}:
        issues = []
    else:
        issues = [str(raw_issues)[:180]]
    passed = bool(out.get("pass")) and all(booleans.values())
    return {
        "passed": passed,
        **booleans,
        "issues": issues,
        "qa_cost_rub": round(float(meta.get("cost_rub") or 0), 4),
        "cache_hit": bool(meta.get("cache_hit")),
        "provider_request_id": meta.get("provider_request_id"),
        "request_id": meta.get("request_id"),
        "checked_at": _now_iso(),
    }


def _run_vision_qa(
    *,
    account_id: str,
    banner_path: Path,
    description: str,
    exact_title: str,
    exact_subtitle: str,
    exact_cta: str,
    exact_price: str,
    advantages: list[str] | None,
    hypothesis: str,
    fmt: str,
    composition_family: str = "",
    composition_profile: tuple[str, str] | None = None,
    icon_profile: tuple[str, str] | None = None,
    provenance: dict | None = None,
) -> dict:
    from app.services.media_semantic_validator import _vision_chat_json

    contract = _vision_contract(
        description=description,
        exact_title=exact_title,
        exact_subtitle=exact_subtitle,
        exact_cta=exact_cta,
        exact_price=exact_price,
        advantages=advantages,
        hypothesis=hypothesis,
        fmt=fmt,
        composition_family=composition_family,
        composition_profile=composition_profile,
        icon_profile=icon_profile,
    )
    prompt = (
        "You are the final fail-closed QA inspector for one Russian commercial advertising banner. "
        "Return strict JSON only with keys: pass, semantic_match, headline_ok, exact_text_ok, spelling_ok, "
        "forbidden_claims_ok, composition_ok, crop_safe_ok, professional_ok, mobile_ok, selling_typography_ok, "
        "palette_ok, reference_style_match, editorial_richness_ok, secondary_copy_density_ok, issues. "
        "Every boolean must be true for pass=true. Reject generic or unrelated imagery, malformed or invented "
        "Russian text, any price/discount/phone/address/guarantee not present in the contract, or secondary text "
        "outside owner_allowed_secondary_microcopy, clipped text, unsafe crop, broken anatomy, low-quality template "
        "clutter, unreadable mobile typography, weak/thin non-selling headline styling, a palette that misses the "
        "owner niche benchmark, or a composition that does not make one dominant commercial idea obvious. "
        "The banner must visibly respect owner typography/palette/hierarchy and the requested composition profile. "
        "The image may omit optional supporting text if "
        "the composition is cleaner, but any text it renders must exactly match supplied text. "
        "The composition_family is intentional; composition_ok must also be false if the banner collapses into a "
        "generic repeated card/template instead of that family. CONTRACT: "
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
    )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_data_uri(banner_path), "detail": "high"}},
    ]
    try:
        out, meta = _vision_chat_json(
            str(account_id),
            VISION_QA_OPERATION,
            content,
            max_tokens=420,
            timeout=90,
            return_meta=True,
            provenance={
                "source": "premium_banner",
                "engine": ENGINE,
                "model": MODEL,
                "quality": QUALITY,
                **{str(k)[:64]: v for k, v in (provenance or {}).items()
                   if isinstance(v, (str, int, float, bool)) or v is None},
            },
        )
    except Exception as exc:
        return {
            "passed": False,
            "issues": ["vision_qa_unavailable:" + type(exc).__name__],
            "qa_cost_rub": 0.0,
            "cache_hit": False,
            "checked_at": _now_iso(),
        }
    if bool((meta or {}).get("cache_hit")) and not float((meta or {}).get("cost_rub") or 0):
        _rid=(meta or {}).get("provider_request_id") or (meta or {}).get("request_id")
        _cached_cost=_usage_cost_by_request(account_id,VISION_QA_OPERATION,_rid)
        if _cached_cost>0:
            meta=dict(meta or {}); meta["cost_rub"]=_cached_cost
    return _normalize_vision_result(out, meta)


def _generate_image_core(
    *,
    account_id: str,
    prompt: str,
    save_path: str,
    size: str,
    operation: str,
    reference_path: str | None = None,
    burst_override: bool | str = False,
    provenance: dict | None = None,
) -> dict:
    from app.services.image_service import generate

    db = SessionLocal()
    try:
        result = generate(
            db=db,
            account_id=account_id,
            prompt=prompt,
            save_path=save_path,
            model=MODEL,
            quality=QUALITY,
            size=size,
            operation=operation,
            reference_path=reference_path,
            burst_override=burst_override,
            provenance=provenance,
        )
        return result if isinstance(result, dict) else {
            "ok": False,
            "path": None,
            "error": "INVALID_IMAGE_CORE_RESULT",
        }
    finally:
        db.close()


def _safe_zone_percent(fmt: str, source_size: str) -> int:
    preset = FORMAT_PRESETS.get(fmt) or FORMAT_PRESETS["1:1"]
    target_w, target_h = preset["target"]
    src_w, src_h = [int(x) for x in source_size.split("x")]
    scale = max(target_w / src_w, target_h / src_h)
    new_h = src_h * scale
    kept_fraction = target_h / max(new_h, 1)
    return max(30, min(int(kept_fraction * 100 * 0.85), 90))


def estimate_cost_rub() -> float:
    return round(float(estimate_kopeks(1, model=MODEL, quality=QUALITY, with_reference=False)) / 100.0, 4)


def _paid_preset_guard(source_size: str = "1024x1024") -> dict:
    """Fail closed before OpenAI if Premium drifts or the cost circuit is open."""
    est = estimate_cost_rub()
    preset_ok = (MODEL, QUALITY) == APPROVED_PAID_PRESET
    size_ok = str(source_size or "") in APPROVED_SOURCE_SIZES
    cost_ok = est <= MAX_COST_RUB
    circuit = _load_cost_circuit()
    circuit_ok = not bool(circuit.get("tripped"))
    return {
        "allowed": bool(preset_ok and size_ok and cost_ok and circuit_ok),
        "model": MODEL,
        "quality": QUALITY,
        "source_size": str(source_size or ""),
        "estimated_cost_rub": est,
        "max_cost_rub": MAX_COST_RUB,
        "preset_ok": preset_ok,
        "size_ok": size_ok,
        "cost_ok": cost_ok,
        "circuit_ok": circuit_ok,
        "cost_circuit": circuit,
    }


def status_payload() -> dict:
    guard = _paid_preset_guard("1024x1024")
    est = float(guard["estimated_cost_rub"])
    return {
        "enabled": bool(guard["allowed"]),
        "production_preset_locked": True,
        "approved_paid_preset": {"model": APPROVED_PAID_PRESET[0], "quality": APPROVED_PAID_PRESET[1]},
        "engine": ENGINE,
        "engine_source": ENGINE_SOURCE,
        "model": MODEL,
        "quality": QUALITY,
        "target_cost_rub": TARGET_COST_RUB,
        "max_cost_rub": MAX_COST_RUB,
        "estimated_cost_rub": est,
        "formats": [
            {"key": key, "label": row["label"], "source_size": row["source_size"], "target": list(row["target"])}
            for key, row in FORMAT_PRESETS.items()
        ],
        "hypotheses": [{"key": k, "label": k.replace("_", " ")} for k in HYPOTHESES],
        "preview_first": True,
        "single_paid_call": True,
        "vision_qa_batch_max": VISION_QA_BATCH_MAX,
        "owner_reference_version": OWNER_REFERENCE_VERSION,
        "owner_style_niches": sorted(OWNER_STYLE_CONTRACTS.keys()),
        "owner_style_learning": True,
        "composition_families": sorted(COMPOSITION_FAMILIES.keys()),
        "composition_profiles": [x[0] for x in COMPOSITION_PROFILES],
        "icon_profiles": [x[0] for x in ICON_STYLE_PROFILES],
        "cost_circuit": guard.get("cost_circuit") or {"tripped": False, "state": "closed"},
    }


def _metadata_key(banner_id: str) -> str:
    return META_PREFIX + str(banner_id)


def _load_versions(account_id: str, banner_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        raw = db.execute(
            text("SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
            {"a": account_id, "k": _metadata_key(banner_id)},
        ).scalar()
    finally:
        db.close()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _save_versions(account_id: str, banner_id: str, versions: list[dict]) -> None:
    encoded = json.dumps(versions, ensure_ascii=False, sort_keys=True)
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT id FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
            {"a": account_id, "k": _metadata_key(banner_id)},
        ).fetchone()
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:i"), {"v": encoded, "i": row[0]})
        else:
            db.execute(
                text("INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"),
                {"a": account_id, "k": _metadata_key(banner_id), "v": encoded},
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_versions(account_id: str, banner_id: str) -> list[dict]:
    return _load_versions(str(account_id), str(banner_id))


def _latest_actual_cost(account_id: str, operation: str, started_at: datetime) -> float:
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT cost_rub FROM api_usage "
                "WHERE account_id=:a AND operation=:o AND created_at>=:t "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"a": account_id, "o": operation, "t": started_at},
        ).fetchone()
        return round(float((row[0] if row else 0) or 0), 4)
    except Exception:
        return 0.0
    finally:
        db.close()



def _usage_cost_by_request(account_id: str, operation: str, request_id: str | None) -> float:
    rid = str(request_id or "").strip()
    if not rid:
        return 0.0
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT cost_rub FROM api_usage "
                "WHERE account_id=:a AND operation=:o AND request_id=:r "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"a": str(account_id), "o": str(operation), "r": rid},
        ).fetchone()
        return round(float((row[0] if row else 0) or 0), 4)
    except Exception:
        return 0.0
    finally:
        db.close()


def _crop_to_target(path: str, target: tuple[int, int]) -> None:
    target_w, target_h = target
    img = Image.open(path).convert("RGB")
    if img.size == (target_w, target_h):
        return
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    img = img.crop((left, top, left + target_w, top + target_h))
    img.save(path, "PNG", optimize=True)


def _text_block(label: str, value: str, instruction: str) -> str:
    value = _clean(value, 180)
    if not value:
        return ""
    return f'\n{instruction} {label}: «{value}»'


def build_prompt(
    *,
    description: str,
    exact_title: str = "",
    exact_subtitle: str = "",
    exact_cta: str = "",
    exact_price: str = "",
    advantages: list[str] | None = None,
    accent_color: str = "#F97316",
    fmt: str = "1:1",
    hypothesis: str = DEFAULT_HYPOTHESIS,
    composition_family: str = DEFAULT_COMPOSITION_FAMILY,
    reference_style: str = "",
    edit_instruction: str = "",
    composition_profile: tuple[str, str] | None = None,
    icon_profile: tuple[str, str] | None = None,
) -> str:
    description = _clean(description, 3000)
    hypothesis = hypothesis if hypothesis in HYPOTHESES else DEFAULT_HYPOTHESIS
    composition_family = composition_family if composition_family in COMPOSITION_FAMILIES else DEFAULT_COMPOSITION_FAMILY
    preset = FORMAT_PRESETS.get(fmt) or FORMAT_PRESETS["1:1"]
    safe = _safe_zone_percent(fmt, preset["source_size"])
    advantages = [_clean(x, 100) for x in (advantages or []) if _clean(x, 100)][:3]
    owner_niche, owner_style = _owner_style_contract(description, exact_title, exact_subtitle)
    owner_style_block = "\n\n" + owner_style["contract"]
    owner_style_block += "\nTYPOGRAPHY QUALITY BAR: use a bold modern sales-oriented sans/grotesk headline with strong mobile readability and clear hierarchy."
    owner_style_block += "\nCOLOR QUALITY BAR: use the niche palette as a full intentional color system across background, hero lighting, icons and text accents."
    owner_style_block += "\nREFERENCE LEARNING: match the owner-approved benchmark hierarchy, confidence, icon discipline and commercial density without copying logos or exact artwork."

    diversity_block = ""
    if composition_profile:
        diversity_block += (
            "\n\nCOMPOSITION DIVERSITY CONTRACT [" + _clean(composition_profile[0], 80) + "]: "
            + _clean(composition_profile[1], 900)
        )
    if icon_profile and advantages:
        diversity_block += (
            "\nICON DIVERSITY CONTRACT [" + _clean(icon_profile[0], 80) + "]: "
            + _clean(icon_profile[1], 700)
        )
    if diversity_block:
        diversity_block += (
            "\nDo not fall back to the default centered hero + identical circular-benefit row. "
            "The selected composition/icon language is mandatory unless it would break exact-text legibility or crop safety."
        )

    text_instructions = ""
    text_instructions += _text_block(
        "MAIN HEADLINE", exact_title,
        "Render EXACTLY this Russian text, large, crisp and highly readable.",
    )
    text_instructions += _text_block(
        "SUBTITLE", exact_subtitle,
        "Render EXACTLY this Russian supporting line, smaller than the headline.",
    )
    if advantages:
        text_instructions += "\nRender these exact benefit labels only if they fit cleanly: " + " | ".join(
            f"«{x}»" for x in advantages
        )
    text_instructions += _text_block(
        "PRICE", exact_price,
        "Render EXACTLY this price/offer in a clear badge only if supplied.",
    )
    text_instructions += _text_block(
        "CTA", exact_cta,
        "Render EXACTLY this CTA in a strong contrasting badge/button.",
    )

    ref_block = ""
    if _clean(reference_style, 2500):
        ref_block = (
            "\n\nSTYLE REFERENCE: use the following high-level composition/palette/style as inspiration. "
            "Do not copy logos, brand names, protected marks, personal data or exact third-party artwork:\n"
            + _clean(reference_style, 2500)
        )

    edit_block = ""
    if _clean(edit_instruction, 1200):
        edit_block = (
            "\n\nOWNER REVISION INSTRUCTION: " + _clean(edit_instruction, 1200)
            + "\nPreserve good parts of the previous concept conceptually, but obey this revision."
        )

    return (
        "Create one premium, high-conversion advertising banner in the style of a strong modern advertising campaign. "
        "This is a complete advertising creative, NOT just a stock-photo background. "
        "Use bold premium art direction, one dominant expressive hero scene, dynamic but clean asymmetrical composition, "
        "strong depth, sophisticated commercial lighting, clear hierarchy and high production value. "
        "Prefer one powerful visual idea over many small decorative elements. "
        "Keep the main headline visually dominant, the supporting line compact, and use at most one strong CTA/badge. "
        "Avoid clutter, rows of tiny pills, excessive stickers, decorative UI noise and generic template-card composition. "
        "Avoid generic stock-photo cliches, random smiling office people, handshakes, blue-eyed robots, "
        "circuit-board brains, meaningless neon holograms and visual clutter unless explicitly required by the brief.\n\n"
        f"BUSINESS / OFFER BRIEF:\n{description}\n\n"
        f"CREATIVE HYPOTHESIS:\n{HYPOTHESES[hypothesis]}\n\n"
        f"COMPOSITION FAMILY:\n{COMPOSITION_FAMILIES[composition_family]}\n\n"
        f"ACCENT COLOR: {accent_color}. Use complementary dark/neutral tones when appropriate.\n"
        f"CANVAS: {preset['source_size']}. Final preset: {preset['label']}. "
        f"Keep all critical text and key visual elements inside the central safe region (~{safe}% of height) "
        "so the later deterministic crop does not cut them."
        + text_instructions
        + owner_style_block
        + diversity_block
        + ref_block
        + edit_block
        + "\n\nTEXT RULES: no extra random words, numbers, badges, fake metrics, fake prices, fake discounts, "
          "fake guarantees, phone numbers or claims not supplied above. Russian Cyrillic must be clean and legible. "
          "If visible text is requested, render only the supplied text blocks. No watermark."
    )


def _banner_identity(account_id: str, idempotency_key: str, parent_banner_id: str = "") -> str:
    seed = f"{account_id}|premium-ai|{parent_banner_id}|{idempotency_key}".encode("utf-8", "ignore")
    return hashlib.sha256(seed).hexdigest()[:24]


def _valid_png(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return False
        with path.open("rb") as fh:
            return fh.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _premium_image_provenance(provenance: dict | None, banner_id: str, version: int) -> dict:
    out = dict(provenance or {})
    if out.get("item_id") and not out.get("campaign_item_id"):
        try:
            out["campaign_item_id"] = int(out["item_id"])
        except (TypeError, ValueError):
            out.pop("campaign_item_id", None)
    out.setdefault("banner_id", str(banner_id or ""))
    out.setdefault("stage", "banners")
    out.setdefault("source", "premium_banner")
    out.setdefault("generation_version", int(version or 0))
    return out


def create_preview(
    *,
    account_id: str,
    description: str,
    exact_title: str = "",
    exact_subtitle: str = "",
    exact_cta: str = "",
    exact_price: str = "",
    advantages: list[str] | None = None,
    accent_color: str = "#F97316",
    fmt: str = "1:1",
    hypothesis: str = DEFAULT_HYPOTHESIS,
    composition_family: str = DEFAULT_COMPOSITION_FAMILY,
    reference_style: str = "",
    idempotency_key: str,
    parent_banner_id: str = "",
    edit_instruction: str = "",
    reference_path: str | None = None,
    burst_override: bool | str = False,
    defer_vision_qa: bool = False,
    provenance: dict | None = None,
) -> dict:
    account_id = _clean(account_id, 200)
    idem = _clean(idempotency_key, 300)
    if not account_id:
        return {"status": "invalid_account", "code": "account_required"}
    if not idem:
        return {"status": "idempotency_required", "code": "paid_idempotency_required"}
    if not _clean(description, 3000):
        return {"status": "invalid_request", "code": "description_required"}

    fmt = fmt if fmt in FORMAT_PRESETS else "1:1"
    hypothesis = hypothesis if hypothesis in HYPOTHESES else DEFAULT_HYPOTHESIS
    composition_family = composition_family if composition_family in COMPOSITION_FAMILIES else DEFAULT_COMPOSITION_FAMILY
    preset = FORMAT_PRESETS[fmt]
    guard = _paid_preset_guard(str(preset["source_size"]))
    estimate = float(guard["estimated_cost_rub"])
    if not guard["allowed"]:
        circuit_open = not bool(guard.get("circuit_ok", True))
        return {
            "status": "cost_cap_exceeded",
            "code": "COST_CIRCUIT_OPEN" if circuit_open else "COST_CAP_EXCEEDED",
            "detail": (
                "Платная генерация остановлена cost-circuit после фактического превышения лимита 4 ₽."
                if circuit_open
                else "Premium AI заблокирован до платного вызова: разрешён только gpt-image-2/low "
                     "в утверждённых размерах и с прогнозом не выше 4 ₽."
            ),
            **guard,
        }

    banner_id = _clean(parent_banner_id, 80) or _banner_identity(account_id, idem)
    versions = _load_versions(account_id, banner_id)
    version = max([int(v.get("version") or 0) for v in versions], default=0) + 1

    # Exact replay of the same paid intent returns the same artifact.
    for row in versions:
        if str(row.get("idempotency_key") or "") == idem:
            existing = Path(str(row.get("absolute_path") or ""))
            if _valid_png(existing):
                return {**row, "status": "ok", "idempotency_replay": True}

    folder = IMAGES_ROOT / account_id / "banners" / "premium_ai"
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"premium_{banner_id}_v{version}.png"
    output = folder / fname

    LOCK_ROOT.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(f"{account_id}|{banner_id}|{idem}".encode()).hexdigest()[:32] + ".lock"
    with (LOCK_ROOT / lock_name).open("a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        versions = _load_versions(account_id, banner_id)
        for row in versions:
            if str(row.get("idempotency_key") or "") == idem:
                existing = Path(str(row.get("absolute_path") or ""))
                if _valid_png(existing):
                    return {**row, "status": "ok", "idempotency_replay": True}

        # Re-check the global circuit under the paid-intent lock.
        guard = _paid_preset_guard(str(preset["source_size"]))
        if not guard["allowed"]:
            return {
                "status": "cost_cap_exceeded",
                "code": "COST_CIRCUIT_OPEN" if not guard.get("circuit_ok", True) else "COST_CAP_EXCEEDED",
                **guard,
            }

        diversity_seed = f"{account_id}|{banner_id}|{idem}"
        composition_profile, icon_profile = _select_diversity_profiles(
            diversity_seed,
            version=version,
        )
        owner_niche, owner_style = _owner_style_contract(description, exact_title, exact_subtitle)

        prompt = build_prompt(
            description=description,
            exact_title=exact_title,
            exact_subtitle=exact_subtitle,
            exact_cta=exact_cta,
            exact_price=exact_price,
            advantages=advantages,
            accent_color=accent_color,
            fmt=fmt,
            hypothesis=hypothesis,
            composition_family=composition_family,
            reference_style=reference_style,
            edit_instruction=edit_instruction,
            composition_profile=composition_profile,
            icon_profile=icon_profile,
        )

        operation = "banner_premium_ai"
        started = datetime.now(timezone.utc)
        image_provenance = _premium_image_provenance(provenance, banner_id, version)
        core = _generate_image_core(
            account_id=account_id,
            prompt=prompt,
            save_path=str(output),
            size=str(preset["source_size"]),
            operation=operation,
            reference_path=reference_path,
            burst_override=burst_override,
            provenance=image_provenance,
        )
        if not core.get("ok") or not _valid_png(output):
            return {
                "status": "error",
                "code": str(core.get("error") or "PREMIUM_IMAGE_GENERATION_FAILED"),
                "detail": "OpenAI не создал пригодный баннер",
                "estimated_cost_rub": estimate,
                "provider_request_id": core.get("provider_request_id") or core.get("request_id"),
                "ambiguous_provider_outcome": bool(core.get("ambiguous_provider_outcome")),
            }

        _crop_to_target(str(output), tuple(preset["target"]))

        raw_actual = core.get("cost_rub")
        actual = round(float(raw_actual or 0), 4)
        if actual <= 0:
            actual = _latest_actual_cost(account_id, operation, started)
        actual_known = actual > 0

        technical_qa = _technical_image_qa(output, tuple(preset["target"]), versions)
        qa_contract = _vision_contract(
            description=description,
            exact_title=exact_title,
            exact_subtitle=exact_subtitle,
            exact_cta=exact_cta,
            exact_price=exact_price,
            advantages=advantages,
            hypothesis=hypothesis,
            fmt=fmt,
            composition_family=composition_family,
            composition_profile=composition_profile,
            icon_profile=icon_profile,
        )
        vision_qa = {
            "passed": False,
            "issues": ["vision_qa_pending" if defer_vision_qa else "vision_qa_not_run"],
            "qa_cost_rub": 0.0,
            "cache_hit": False,
            "checked_at": _now_iso(),
        }

        # Never add more paid QA spend to an already over-budget or unreconciled image.
        # Campaign batch mode durably stores the image first and finalizes Vision
        # in groups of up to four; direct API callers retain the single-image path.
        if actual_known and actual <= MAX_COST_RUB and bool(technical_qa.get("passed")) and not defer_vision_qa:
            vision_qa = _run_vision_qa(
                account_id=account_id,
                banner_path=output,
                description=description,
                exact_title=exact_title,
                exact_subtitle=exact_subtitle,
                exact_cta=exact_cta,
                exact_price=exact_price,
                advantages=advantages,
                hypothesis=hypothesis,
                fmt=fmt,
                composition_family=composition_family,
                composition_profile=composition_profile,
                icon_profile=icon_profile,
                provenance=provenance,
            )

        qa_cost = round(float(vision_qa.get("qa_cost_rub") or 0), 4)
        total_cost = round(actual + qa_cost, 4) if actual_known else 0.0
        cost_sla_pass = bool(actual_known and total_cost <= MAX_COST_RUB)
        qa_passed = bool(technical_qa.get("passed")) and bool(vision_qa.get("passed"))

        circuit = None
        if actual_known and total_cost > MAX_COST_RUB:
            circuit = _trip_cost_circuit(
                actual_cost_rub=actual,
                total_cost_rub=total_cost,
                banner_id=banner_id,
                version=version,
            )

        if not actual_known:
            preview_status = "COST_RECONCILIATION_REQUIRED"
        elif not cost_sla_pass:
            preview_status = "BLOCKED_COST"
        elif not bool(technical_qa.get("passed")):
            preview_status = "QA_FAILED"
        elif defer_vision_qa:
            preview_status = "VISION_QA_PENDING"
        elif not qa_passed:
            preview_status = "QA_FAILED"
        else:
            preview_status = "DRAFT_PREVIEW"

        text_ok = bool(
            vision_qa.get("headline_ok")
            and vision_qa.get("exact_text_ok")
            and vision_qa.get("spelling_ok")
        )
        prompt_hash = hashlib.sha256(prompt.encode("utf-8", "ignore")).hexdigest()

        row = {
            "status": "ok",
            "preview_status": preview_status,
            "banner_id": banner_id,
            "version": version,
            "parent_version": (version - 1) if version > 1 else None,
            "account_id": account_id,
            "filename": fname,
            "absolute_path": str(output),
            "url": "/images/" + str(output.relative_to(IMAGES_ROOT)).replace(os.sep, "/"),
            "engine": ENGINE,
            "engine_source": ENGINE_SOURCE,
            "provider": "openai",
            "model": MODEL,
            "quality": QUALITY,
            "format": fmt,
            "source_size": preset["source_size"],
            "target_size": list(preset["target"]),
            "creative_hypothesis": hypothesis,
            "composition_family": composition_family,
            "composition_profile": composition_profile[0],
            "icon_profile": icon_profile[0],
            "diversity_contract_version": "banner_diversity_v1",
            "owner_reference_version": OWNER_REFERENCE_VERSION,
            "owner_style_niche": owner_niche,
            "owner_style_palette": owner_style.get("palette"),
            "owner_style_typography": owner_style.get("typography"),
            "owner_reference_image": OWNER_REFERENCE_IMAGES.get(owner_niche),
            "owner_reference_image_present": bool(OWNER_REFERENCE_IMAGES.get(owner_niche) and Path(OWNER_REFERENCE_IMAGES.get(owner_niche)).is_file()),
            "reference_style_used": bool(_clean(reference_style)),
            "reference_edit_used": bool(reference_path),
            "prompt_sha256": prompt_hash,
            "prompt": prompt,
            "idempotency_key": idem,
            "estimated_cost_rub": estimate,
            "actual_cost_rub": actual if actual_known else None,
            "qa_cost_rub": qa_cost,
            "total_cost_rub": total_cost if actual_known else None,
            "cost_cap_rub": MAX_COST_RUB,
            "cost_reconciled": actual_known,
            "cost_sla_pass": cost_sla_pass,
            "cost_circuit": circuit,
            "technical_qa": technical_qa,
            "qa_contract": qa_contract,
            "vision_qa": vision_qa,
            "vision_qa_deferred": bool(defer_vision_qa),
            "qa_passed": qa_passed,
            "text_qa_status": ("PENDING" if defer_vision_qa and bool(technical_qa.get("passed")) else ("PASS" if text_ok else "FAILED")),
            "visual_qa_status": ("PENDING" if defer_vision_qa and bool(technical_qa.get("passed")) else ("PASS" if qa_passed else "FAILED")),
            "artifact_sha256": technical_qa.get("artifact_sha256"),
            "perceptual_hash": technical_qa.get("perceptual_hash"),
            "duplicate_of_version": technical_qa.get("duplicate_of_version"),
            "provider_request_id": core.get("provider_request_id") or core.get("request_id"),
            "request_id_exact": core.get("request_id_exact"),
            "single_paid_call": True,
            "paid_image_attempts": 1,
            "burst_override_mode": ("burst_only" if str(burst_override).strip().lower()=="burst_only" else ("all" if burst_override is True else "none")),
            "auto_retry": False,
            "approved": False,
            "rejected": False,
            "created_at": _now_iso(),
            "user_instruction": _clean(edit_instruction, 1200),
        }
        versions.append(row)
        _save_versions(account_id, banner_id, versions)
        return row



def _version_row(account_id: str, banner_id: str, version: int) -> tuple[list[dict], dict | None]:
    versions = _load_versions(str(account_id), str(banner_id))
    chosen = next((x for x in versions if int(x.get("version") or 0) == int(version)), None)
    return versions, chosen


def _apply_vision_verdict(
    *,
    account_id: str,
    banner_id: str,
    version: int,
    vision_qa: dict,
    qa_mode: str,
    batch_size: int = 1,
) -> dict | None:
    versions, chosen = _version_row(account_id, banner_id, version)
    if chosen is None:
        return None
    actual = round(float(chosen.get("actual_cost_rub") or 0), 4)
    actual_known = bool(chosen.get("cost_reconciled")) and actual > 0
    qa_cost = round(float((vision_qa or {}).get("qa_cost_rub") or 0), 4)
    total_cost = round(actual + qa_cost, 4) if actual_known else 0.0
    technical_ok = bool((chosen.get("technical_qa") or {}).get("passed"))
    qa_passed = technical_ok and bool((vision_qa or {}).get("passed"))
    cost_sla_pass = bool(actual_known and total_cost <= MAX_COST_RUB)
    circuit = chosen.get("cost_circuit")
    if actual_known and total_cost > MAX_COST_RUB:
        circuit = _trip_cost_circuit(
            actual_cost_rub=actual,
            total_cost_rub=total_cost,
            banner_id=banner_id,
            version=version,
        )
    if not actual_known:
        preview_status = "COST_RECONCILIATION_REQUIRED"
    elif not cost_sla_pass:
        preview_status = "BLOCKED_COST"
    elif not technical_ok:
        preview_status = "QA_FAILED"
    elif not qa_passed:
        preview_status = "QA_FAILED"
    else:
        preview_status = "DRAFT_PREVIEW"
    text_ok = bool(
        (vision_qa or {}).get("headline_ok")
        and (vision_qa or {}).get("exact_text_ok")
        and (vision_qa or {}).get("spelling_ok")
    )
    chosen.update({
        "preview_status": preview_status,
        "qa_cost_rub": qa_cost,
        "total_cost_rub": total_cost if actual_known else None,
        "cost_sla_pass": cost_sla_pass,
        "cost_circuit": circuit,
        "vision_qa": dict(vision_qa or {}),
        "vision_qa_deferred": False,
        "vision_qa_mode": str(qa_mode),
        "vision_qa_batch_size": int(batch_size or 1),
        "qa_passed": qa_passed,
        "text_qa_status": "PASS" if text_ok else "FAILED",
        "visual_qa_status": "PASS" if qa_passed else "FAILED",
        "vision_qa_finalized_at": _now_iso(),
    })
    _save_versions(str(account_id), str(banner_id), versions)
    return dict(chosen)


def finalize_preview_vision_batch(
    *,
    account_id: str,
    entries: list[dict],
    provenance: dict | None = None,
) -> dict:
    """Finalize up to four durable Premium previews with one Vision request.

    Valid results are persisted independently. If a successful batch response
    omits/mangles one slot, only that unresolved preview falls back to its
    exactly-once single QA intent; already-resolved previews are never bought
    again.
    """
    from app.services.media_semantic_validator import _vision_chat_json

    account_id = _clean(account_id, 200)
    rows = list(entries or [])
    if not account_id:
        return {"ok": False, "code": "account_required", "previews": {}, "unresolved": []}
    if not rows:
        return {"ok": True, "previews": {}, "unresolved": [], "provider_calls": 0, "batch_provider_calls": 0}
    if len(rows) > VISION_QA_BATCH_MAX:
        raise ValueError("premium vision QA batch max is 4")

    resolved: dict[str, dict] = {}
    pending: list[tuple[str, int, dict]] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    for entry in rows:
        banner_id = _clean(entry.get("banner_id"), 80)
        try:
            version = int(entry.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        key = f"{banner_id}:v{version}"
        if not banner_id or version <= 0 or key in seen:
            if key and key != ":v0":
                unresolved.append(key)
            continue
        seen.add(key)
        _versions, chosen = _version_row(account_id, banner_id, version)
        if chosen is None:
            unresolved.append(key)
            continue
        path = Path(str(chosen.get("absolute_path") or ""))
        if not _valid_png(path):
            unresolved.append(key)
            continue
        if (
            not bool(chosen.get("vision_qa_deferred"))
            and str(chosen.get("preview_status") or "") != "VISION_QA_PENDING"
            and isinstance(chosen.get("vision_qa"), dict)
            and "passed" in (chosen.get("vision_qa") or {})
        ):
            resolved[key] = dict(chosen)
            continue
        if (
            not bool(chosen.get("cost_reconciled"))
            or float(chosen.get("actual_cost_rub") or 0) <= 0
            or float(chosen.get("actual_cost_rub") or 0) > MAX_COST_RUB
            or not bool((chosen.get("technical_qa") or {}).get("passed"))
        ):
            unresolved.append(key)
            continue
        if not isinstance(chosen.get("qa_contract"), dict):
            unresolved.append(key)
            continue
        pending.append((banner_id, version, dict(chosen)))

    if not pending:
        return {
            "ok": not bool(unresolved),
            "previews": resolved,
            "unresolved": sorted(set(unresolved)),
            "provider_calls": 0,
            "batch_provider_calls": 0,
            "batch_size": 0,
        }

    prompt = (
        "BATCH PREMIUM BANNER QA. Inspect every banner independently. Return ONLY one JSON object "
        "with key results. results must be an array in the same SLOT order. Each result must contain "
        "slot, banner_id, version, pass, semantic_match, headline_ok, exact_text_ok, spelling_ok, "
        "forbidden_claims_ok, composition_ok, crop_safe_ok, professional_ok, mobile_ok, selling_typography_ok, "
        "palette_ok, reference_style_match, editorial_richness_ok, secondary_copy_density_ok, issues. "
        "All booleans must be explicit. Reject invented text/claims, clipped or unreadable Russian text, "
        "bad anatomy, weak/template-like composition, unsafe crop, unrelated imagery, weak selling typography, "
        "palette drift, excessive or invented secondary copy, or a failure to respect composition_family, "
        "composition_profile and owner visual benchmark. Judge each SLOT separately."
    )
    content = [{"type": "text", "text": prompt}]
    for slot, (banner_id, version, chosen) in enumerate(pending, 1):
        contract = dict(chosen.get("qa_contract") or {})
        content.append({
            "type": "text",
            "text": (
                f"SLOT {slot}; BANNER_ID={banner_id}; VERSION={version}; CONTRACT="
                + json.dumps(contract, ensure_ascii=False, sort_keys=True)
            ),
        })
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_data_uri(Path(str(chosen.get("absolute_path")))), "detail": "high"},
        })

    batch_prov = {
        "source": "premium_banner_batch",
        "engine": ENGINE,
        "model": MODEL,
        "quality": QUALITY,
        "batch_size": len(pending),
        **{str(k)[:64]: v for k, v in (provenance or {}).items()
           if isinstance(v, (str, int, float, bool)) or v is None},
    }
    try:
        raw, meta = _vision_chat_json(
            account_id,
            VISION_QA_BATCH_OPERATION,
            content,
            max_tokens=max(700, min(1800, 420 * len(pending))),
            timeout=120,
            return_meta=True,
            provenance=batch_prov,
        )
    except Exception as exc:
        return {
            "ok": False,
            "previews": resolved,
            "unresolved": sorted(set(unresolved + [f"{b}:v{v}" for b, v, _ in pending])),
            "provider_calls": 1,
            "batch_provider_calls": 1,
            "batch_size": len(pending),
            "error": "batch_provider_error:" + type(exc).__name__,
        }

    meta = dict(meta or {})
    if bool(meta.get("cache_hit")) and not float(meta.get("cost_rub") or 0):
        rid = meta.get("provider_request_id") or meta.get("request_id")
        recovered_cost = _usage_cost_by_request(account_id, VISION_QA_BATCH_OPERATION, rid)
        if recovered_cost > 0:
            meta["cost_rub"] = recovered_cost
    total_batch_cost = round(float(meta.get("cost_rub") or 0), 4)
    share = round(total_batch_cost / max(1, len(pending)), 4)
    raw_results = raw.get("results") if isinstance(raw, dict) else None
    by_slot: dict[int, dict] = {}
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                slot = int(item.get("slot") or 0)
            except (TypeError, ValueError):
                slot = 0
            if 1 <= slot <= len(pending) and slot not in by_slot:
                by_slot[slot] = item

    missing: list[tuple[str, int, dict]] = []
    for slot, (banner_id, version, chosen) in enumerate(pending, 1):
        raw_verdict = by_slot.get(slot)
        required = (
            "semantic_match", "headline_ok", "exact_text_ok", "spelling_ok",
            "forbidden_claims_ok", "composition_ok", "crop_safe_ok",
            "professional_ok", "mobile_ok", "selling_typography_ok",
            "palette_ok", "reference_style_match", "editorial_richness_ok",
            "secondary_copy_density_ok",
        )
        if not isinstance(raw_verdict, dict) or any(k not in raw_verdict for k in required):
            missing.append((banner_id, version, chosen))
            continue
        verdict_meta = {
            **meta,
            "cost_rub": share,
        }
        verdict = _normalize_vision_result(raw_verdict, verdict_meta)
        verdict["batch_qa"] = True
        verdict["batch_size"] = len(pending)
        verdict["batch_slot"] = slot
        updated = _apply_vision_verdict(
            account_id=account_id,
            banner_id=banner_id,
            version=version,
            vision_qa=verdict,
            qa_mode="batch_v1",
            batch_size=len(pending),
        )
        if updated is None:
            unresolved.append(f"{banner_id}:v{version}")
        else:
            resolved[f"{banner_id}:v{version}"] = updated

    recovery_calls = 0
    for banner_id, version, chosen in missing:
        contract = dict(chosen.get("qa_contract") or {})
        single = _run_vision_qa(
            account_id=account_id,
            banner_path=Path(str(chosen.get("absolute_path"))),
            description=str(contract.get("description") or ""),
            exact_title=str(contract.get("headline") or ""),
            exact_subtitle=str(contract.get("subtitle") or ""),
            exact_cta=str(contract.get("cta") or ""),
            exact_price=str(contract.get("price") or ""),
            advantages=list(contract.get("advantages") or []),
            hypothesis=str(contract.get("hypothesis") or DEFAULT_HYPOTHESIS),
            fmt=str(contract.get("format") or "1:1"),
            composition_family=str(contract.get("composition_family") or DEFAULT_COMPOSITION_FAMILY),
            composition_profile=next(
                (x for x in COMPOSITION_PROFILES if x[0] == str(contract.get("composition_profile") or "")),
                None,
            ),
            icon_profile=next(
                (x for x in ICON_STYLE_PROFILES if x[0] == str(contract.get("icon_profile") or "")),
                None,
            ),
            provenance={**batch_prov, "recovery": True, "banner_id": banner_id, "version": version},
        )
        recovery_calls += 0 if bool(single.get("cache_hit")) else 1
        if any(str(x).startswith("vision_qa_unavailable:") for x in (single.get("issues") or [])):
            unresolved.append(f"{banner_id}:v{version}")
            continue
        updated = _apply_vision_verdict(
            account_id=account_id,
            banner_id=banner_id,
            version=version,
            vision_qa=single,
            qa_mode="single_recovery_v1",
            batch_size=1,
        )
        if updated is None:
            unresolved.append(f"{banner_id}:v{version}")
        else:
            resolved[f"{banner_id}:v{version}"] = updated

    unresolved = sorted(set(unresolved))
    return {
        "ok": not bool(unresolved),
        "previews": resolved,
        "unresolved": unresolved,
        "provider_calls": 1 + recovery_calls,
        "batch_provider_calls": 1,
        "recovery_calls": recovery_calls,
        "batch_size": len(pending),
        "cache_hit": bool(meta.get("cache_hit")),
        "provider_request_id": meta.get("provider_request_id"),
        "batch_cost_rub": total_batch_cost,
    }


def approve_preview(account_id: str, banner_id: str, version: int | None = None) -> dict:
    versions = _load_versions(account_id, banner_id)
    if not versions:
        return {"status": "not_found", "code": "PREMIUM_BANNER_NOT_FOUND"}
    if version is None:
        chosen = max(versions, key=lambda x: int(x.get("version") or 0))
    else:
        chosen = next((x for x in versions if int(x.get("version") or 0) == int(version)), None)
    if not chosen:
        return {"status": "not_found", "code": "PREMIUM_VERSION_NOT_FOUND"}
    path = Path(str(chosen.get("absolute_path") or ""))
    if not _valid_png(path):
        return {"status": "invalid_artifact", "code": "PREMIUM_ARTIFACT_INVALID"}
    if bool(chosen.get("rejected")):
        return {"status": "blocked", "code": "PREMIUM_VERSION_REJECTED"}
    if not bool(chosen.get("cost_reconciled")):
        return {"status": "blocked", "code": "COST_RECONCILIATION_REQUIRED"}
    if not bool(chosen.get("cost_sla_pass")):
        return {"status": "blocked", "code": "COST_SLA_FAILED"}
    if not bool(chosen.get("qa_passed")):
        return {"status": "blocked", "code": "QA_FAILED"}

    from app.services.banner_media import register_banner
    media = register_banner(
        account_id=account_id,
        absolute_path=str(path),
        source_provider="openai_full_ai_premium",
        source_ref=f"premium:{banner_id}:v{chosen.get('version')}",
    )
    accepted_at = _now_iso()
    for row in versions:
        if int(row.get("version") or 0) == int(chosen.get("version") or 0):
            row["approved"] = True
            row["rejected"] = False
            row["preview_status"] = "AVAILABLE_FOR_USE"
            row["approved_at"] = accepted_at
            row["accepted_at"] = accepted_at
            row["media"] = media
    _save_versions(account_id, banner_id, versions)
    return {
        "status": "ok",
        "banner_id": banner_id,
        "version": chosen.get("version"),
        "preview_status": "AVAILABLE_FOR_USE",
        "url": chosen.get("url"),
        "media": media,
    }


def reject_preview(
    account_id: str,
    banner_id: str,
    version: int | None = None,
    reason: str = "",
) -> dict:
    versions = _load_versions(account_id, banner_id)
    if not versions:
        return {"status": "not_found", "code": "PREMIUM_BANNER_NOT_FOUND"}
    if version is None:
        chosen = max(versions, key=lambda x: int(x.get("version") or 0))
    else:
        chosen = next((x for x in versions if int(x.get("version") or 0) == int(version)), None)
    if not chosen:
        return {"status": "not_found", "code": "PREMIUM_VERSION_NOT_FOUND"}
    rejected_at = _now_iso()
    for row in versions:
        if int(row.get("version") or 0) == int(chosen.get("version") or 0):
            if row.get("approved"):
                return {"status": "blocked", "code": "APPROVED_VERSION_CANNOT_BE_REJECTED"}
            row["rejected"] = True
            row["preview_status"] = "REJECTED"
            row["rejected_at"] = rejected_at
            row["rejection_reason"] = _clean(reason, 500)
    _save_versions(account_id, banner_id, versions)
    return {
        "status": "ok",
        "banner_id": banner_id,
        "version": chosen.get("version"),
        "preview_status": "REJECTED",
        "rejected_at": rejected_at,
    }
