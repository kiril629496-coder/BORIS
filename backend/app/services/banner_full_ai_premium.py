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
from app.services.campaign_owner_banner_examples import (
    DEFAULT_ACCENT, combine_reference_style, owner_variant,
)

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
VISION_QA_VERSION = "premium_vision_identity_bool_v2_20261008"
VISIBLE_QA_METADATA_POLICY = "visible_pixels_machine_identity_v1"

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


def _money(value):
    """None is unknown; a proved zero is a number, never a coercion default."""
    from decimal import Decimal, InvalidOperation
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        return amount.quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _budget_provenance(provenance, banner_id):
    prov = dict(provenance or {})
    canonical = str(prov.get("budget_intent_key") or "").strip()
    legacy = bool(prov.get("budget_legacy_scope"))
    if not canonical:
        campaign = str(prov.get("campaign_id") or "")
        item = str(prov.get("campaign_item_id") or prov.get("item_id") or "")
        canonical = f"campaign:{campaign}:item:{item}" if campaign and item else f"premium:{banner_id}"
        legacy = bool(campaign and item)
    return {"budget_intent_key": canonical,
            "budget_intent_hash": hashlib.sha256(canonical.encode()).hexdigest()[:32],
            "budget_legacy_scope": legacy}


def _storage_json(account_id, key, value=None):
    """Reuse existing account-scoped storage; no schema change or upsert assumption."""
    db = SessionLocal()
    try:
        if value is not None:
            lock = int.from_bytes(hashlib.blake2b((str(account_id)+"|"+key).encode(), digest_size=8).digest(), "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock})
        row = db.execute(text("SELECT id,value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
                         {"a": str(account_id), "k": key}).fetchone()
        if value is None:
            return json.loads(row[1]) if row else None
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:i AND account_id=:a"), {"v": encoded, "i": row[0], "a": str(account_id)})
        else:
            db.execute(text("INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"), {"a": str(account_id), "k": key, "v": encoded})
        db.commit()
        return value
    except Exception:
        if value is not None:
            db.rollback()
        raise
    finally:
        db.close()


def _known_usage(row):
    amount = _money(row.get("cost_rub")) if isinstance(row, dict) else None
    if not (amount is not None and row.get("rate_source") in {"table", "explicit"}
            and str(row.get("pricing_version") or "").strip()):
        return False
    provenance = row.get("provenance_json") or {}
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except (TypeError, ValueError):
            return False
    if not isinstance(provenance, dict) or provenance.get("vision_usage_known") is False:
        return False
    if row.get("operation") in {VISION_QA_OPERATION, VISION_QA_BATCH_OPERATION} and row.get("rate_source") == "table":
        if not any(type(row.get(k)) is int and row[k] > 0 for k in ("prompt_tokens", "completion_tokens")):
            return False
    return True


def _exact_usage_receipt(account_id, operation, request_id):
    rid = str(request_id or "").strip()
    unknown = {"cost_known": False, "cost_rub": None, "request_id": rid or None,
               "operation": operation, "reason": "usage_receipt_missing"}
    if not rid:
        return unknown
    db = SessionLocal()
    try:
        raw = db.execute(text("SELECT request_id,provider,model,operation,cost_rub,rate_source,pricing_version,"
            "provenance_json,prompt_tokens,completion_tokens FROM api_usage "
            "WHERE account_id=:a AND operation=:o AND request_id=:r"),
            {"a": str(account_id), "o": str(operation), "r": rid}).mappings().first()
        if raw is None:
            return unknown
        row = dict(raw)
        known = _known_usage(row)
        return {**row, "cost_known": known, "cost_rub": float(_money(row["cost_rub"])) if known else None,
                "reason": None if known else "usage_rate_or_cost_unknown"}
    except Exception as exc:
        return {**unknown, "reason": "usage_receipt_unavailable:" + type(exc).__name__}
    finally:
        db.close()


def _allocate_cost(total, slot, size):
    """Conserve all 0.0001 RUB units, including a non-divisible batch remainder."""
    amount = _money(total)
    if amount is None or type(slot) is not int or type(size) is not int or not 1 <= slot <= size:
        return None
    units = int(amount * 10000)
    base, extra = divmod(units, size)
    return (base + (1 if slot <= extra else 0)) / 10000.0


def _allocated_usage(account_id, operation, meta, slot=1, size=1):
    meta = dict(meta or {})
    receipt = _exact_usage_receipt(account_id, operation, meta.get("request_id") or meta.get("provider_request_id"))
    total = receipt.get("cost_rub") if receipt.get("cost_known") is True else None
    return {**meta, **receipt, "provider_request_id": meta.get("provider_request_id"),
            "cost_rub": _allocate_cost(total, slot, size), "request_cost_rub": total,
            "allocation_slot": slot, "allocation_size": size,
            "cache_hit": meta.get("cache_hit") is True,
            "incremental_cost_rub": 0.0 if meta.get("cache_hit") is True else _allocate_cost(total, slot, size)}


def _budget_scope_cost(account_id, provenance, banner_id):
    """Sum journal attempts across all banner IDs in one logical item order.

    A batch debit exists once in api_usage. Its durable allocation dictionary
    assigns exact shares to item scopes; an absent dictionary is unknown.
    """
    prov = dict(provenance or {})
    scope = _budget_provenance(prov, banner_id)
    digest = scope["budget_intent_hash"]
    unknown = {"known": False, "total_cost_rub": None, "duplicate_paid_calls": 0,
               "budget_intent_hash": digest, "reason": "budget_history_unavailable"}
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT request_id,provider,model,operation,cost_rub,rate_source,pricing_version,provenance_json,prompt_tokens,completion_tokens "
            "FROM api_usage WHERE account_id=:a AND operation IN (:im,:sq,:bq) AND ("
            "provenance_json->>'budget_intent_hash'=:s OR "
            ":s=ANY(string_to_array(coalesce(provenance_json->>'budget_intent_hashes',''),',')) OR "
            "(:legacy AND provenance_json->>'campaign_id'=:c AND "
            "(coalesce(provenance_json->>'campaign_item_id',provenance_json->>'item_id')=:i OR operation=:bq))) ORDER BY id"),
            {"a": str(account_id), "im": "banner_premium_ai", "sq": VISION_QA_OPERATION,
             "bq": VISION_QA_BATCH_OPERATION, "s": digest, "legacy": scope["budget_legacy_scope"],
             "c": str(prov.get("campaign_id") or ""),
             "i": str(prov.get("campaign_item_id") or prov.get("item_id") or "")}).mappings().all()
        total_units = 0
        intents = {}
        contributing_request_ids = set()
        for raw in rows:
            row = dict(raw)
            if not _known_usage(row):
                return {**unknown, "reason": "budget_attempt_cost_unknown", "request_id": row.get("request_id")}
            row_prov = row.get("provenance_json") or {}
            if isinstance(row_prov, str):
                row_prov = json.loads(row_prov)
            if not isinstance(row_prov, dict):
                return {**unknown, "reason": "budget_attempt_provenance_invalid"}
            intent = str(row_prov.get("intent_key") or "")
            if not intent and row_prov.get("banner_id") and row_prov.get("generation_version"):
                intent = f"premium-image:{row_prov['banner_id']}:v{row_prov['generation_version']}"
            if not intent:
                return {**unknown, "reason": "budget_attempt_intent_missing"}
            intents.setdefault((row["operation"], intent), set()).add(row["request_id"])
            if row["operation"] == VISION_QA_BATCH_OPERATION:
                key = str(row_prov.get("qa_allocation_key") or "")
                allocation = _storage_json(account_id, key) if key.startswith("premium_banner_qa_allocation:") else None
                entries = (allocation or {}).get("entries") if isinstance(allocation, dict) else None
                if not isinstance(entries, list) or not 1 <= len(entries) <= VISION_QA_BATCH_MAX:
                    return {**unknown, "reason": "batch_allocation_missing"}
                matching = [(slot, item) for slot, item in enumerate(entries, 1)
                            if item.get("budget_intent_hash") == digest]
                # A legacy unscoped batch cannot be silently attributed to zero.
                if not matching:
                    if scope["budget_legacy_scope"]:
                        return {**unknown, "reason": "legacy_batch_allocation_unattributed"}
                    continue
                for slot, item in matching:
                    total_units += int(_money(_allocate_cost(row["cost_rub"], slot, len(entries))) * 10000)
            else:
                total_units += int(_money(row["cost_rub"]) * 10000)
            contributing_request_ids.add(str(row["request_id"]))
        duplicates = sum(max(0, len(requests)-1) for requests in intents.values())
        return {"known": True, "total_cost_rub": total_units / 10000.0,
                "duplicate_paid_calls": duplicates, "budget_intent_hash": digest,
                "request_ids": sorted(contributing_request_ids),
                "request_count": len(rows), "reason": "duplicate_paid_intent" if duplicates else None}
    except Exception as exc:
        return {**unknown, "reason": "budget_history_unavailable:" + type(exc).__name__}
    finally:
        db.close()


def _qa_reserve_rub(batch_size):
    from app.services.media_semantic_validator import _vision_estimate_kopeks
    return _vision_estimate_kopeks() / 100.0 / max(1, int(batch_size))


def _qa_identity(account_id, row):
    contract = json.dumps(row.get("qa_contract") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"account_id": str(account_id), "banner_id": str(row.get("banner_id") or ""),
            "version": int(row.get("version") or 0), "asset_sha256": str(row.get("artifact_sha256") or ""),
            "qa_version": VISION_QA_VERSION, "contract_sha256": hashlib.sha256(contract.encode()).hexdigest()}


def _qa_receipt_key(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _artifact_error(account_id, row):
    if str(row.get("account_id") or "") != str(account_id):
        return "PREMIUM_ACCOUNT_MISMATCH"
    path = Path(str(row.get("absolute_path") or ""))
    try:
        path.resolve().relative_to((IMAGES_ROOT / str(account_id)).resolve())
        if not _valid_png(path):
            return "PREMIUM_ARTIFACT_INVALID"
        with Image.open(path) as image:
            image.verify()
        expected = str(row.get("artifact_sha256") or "")
        if len(expected) != 64 or _artifact_sha256(path) != expected:
            return "PREMIUM_ARTIFACT_HASH_MISMATCH"
        if (row.get("technical_qa") or {}).get("artifact_sha256") != expected:
            return "PREMIUM_TECHNICAL_PROOF_MISMATCH"
    except (OSError, ValueError, TypeError):
        return "PREMIUM_ARTIFACT_INVALID"
    return None


def _current_qa_receipt(account_id, row):
    if _artifact_error(account_id, row):
        return None
    identity = _qa_identity(account_id, row)
    receipt = (row.get("qa_receipts") or {}).get(_qa_receipt_key(identity))
    if not isinstance(receipt, dict) or receipt.get("identity") != identity:
        return None
    verdict = receipt.get("verdict")
    if not isinstance(verdict, dict) or verdict.get("qa_identity") != identity:
        return None
    return receipt


def _metadata_only_false_reject(receipt: dict | None) -> bool:
    """Recognize a narrow Vision false-negative caused only by machine identity fields."""
    if not isinstance(receipt, dict):
        return False
    verdict = receipt.get("verdict")
    if not isinstance(verdict, dict):
        return False
    if verdict.get("qa_terminal") is not True or str(verdict.get("qa_status") or "") != "REJECT":
        return False
    visible_checks = (
        "semantic_match", "headline_ok", "exact_text_ok", "spelling_ok",
        "forbidden_claims_ok", "composition_ok", "crop_safe_ok",
        "professional_ok", "mobile_ok", "selling_typography_ok", "palette_ok",
        "reference_style_match", "editorial_richness_ok", "secondary_copy_density_ok",
    )
    if any(verdict.get(key) is not True for key in visible_checks):
        return False
    issues = [str(x or "").strip().lower() for x in (verdict.get("issues") or []) if str(x or "").strip()]
    if not issues:
        return False
    machine_fields = ("banner_id", "asset_sha256", "contract_sha256", "qa_version")
    return all(any(field in issue for field in machine_fields) for issue in issues)


def _upgrade_metadata_false_reject_contract(account_id: str, banner_id: str, version: int, receipt: dict) -> bool:
    """Invalidate only a proven metadata-only false REJECT while preserving its receipt."""
    if not _metadata_only_false_reject(receipt):
        return False
    versions, chosen = _version_row(str(account_id), str(banner_id), int(version))
    if chosen is None or _current_qa_receipt(str(account_id), chosen) != receipt:
        return False
    contract = dict(chosen.get("qa_contract") or {})
    if not contract or contract.get("machine_identity_policy") == VISIBLE_QA_METADATA_POLICY:
        return False
    old_identity = dict(receipt.get("identity") or {})
    contract["machine_identity_policy"] = VISIBLE_QA_METADATA_POLICY
    chosen["qa_contract"] = contract
    history = [dict(x) for x in (chosen.get("qa_policy_upgrades") or []) if isinstance(x, dict)]
    history.append({
        "from_contract_sha256": str(old_identity.get("contract_sha256") or ""),
        "reason": "machine_identity_fields_are_not_visible_banner_copy",
        "issues": list((receipt.get("verdict") or {}).get("issues") or [])[:8],
        "at": _now_iso(),
    })
    chosen["qa_policy_upgrades"] = history[-8:]
    _save_versions(str(account_id), str(banner_id), versions)
    return True


def _provider_failure_verdict(exc, operation, identity, *, intent_key=None, slot=1, size=1):
    """Only trusted provider-layer proof can turn a failed attempt into known zero."""
    meta = dict(getattr(exc, "vision_meta", {}) or {})
    cost_state = meta.get("provider_cost_state")
    no_charge = (cost_state in {"not_requested", "known_not_billed"}
                 and meta.get("cost_known") is True and _money(meta.get("cost_rub")) == _money(0))
    return {"passed": False, "qa_status": "PROVIDER_ERROR", "qa_terminal": False,
        "qa_cost_rub": 0.0 if no_charge else None, "qa_cost_known": no_charge,
        "qa_cost_status": cost_state if no_charge else "unknown",
        "provider_retryable": no_charge and meta.get("retryable") is True,
        "operation": operation, "issues": ["vision_qa_unavailable:" + str(exc)[:300]],
        "request_id": meta.get("request_id"), "provider_request_id": meta.get("provider_request_id"),
        "intent_key": meta.get("intent_key") or intent_key, "qa_identity": identity,
        "allocation_slot": slot, "allocation_size": size,
        "provider_calls": meta.get("provider_calls"),
        "provider_call_count_known": meta.get("provider_call_count_known") is True,
        "cache_hit": meta.get("cache_hit") is True, "checked_at": _now_iso()}


def _with_retry_state(row):
    out = dict(row)
    vision = out.get("vision_qa") or {}
    status = str(vision.get("qa_status") or "PENDING")
    out["qa_terminal"] = vision.get("qa_terminal") is True
    out["qa_rejection_reason"] = "; ".join(vision.get("issues") or []) if status == "REJECT" else None
    out.setdefault("image_outcome", "succeeded")
    out["retry_action"], out["retry_allowed"] = "blocked", False
    if out.get("image_outcome") == "unknown":
        out["retry_action"] = "reconcile_image"
    elif (status == "PROVIDER_ERROR" and vision.get("provider_retryable") is True
          and vision.get("qa_cost_status") in {"not_requested", "known_not_billed"}
          and out.get("cost_reconciled") is True and out.get("cost_sla_pass") is True):
        out["retry_action"], out["retry_allowed"] = "retry_qa", True
    elif out.get("preview_status") in {"COST_RECONCILIATION_REQUIRED", "QA_RECEIPT_UPGRADE_REQUIRED", "QA_INVALID_RESPONSE", "VISION_QA_PROVIDER_FAILED"}:
        out["retry_action"] = "retry_qa" if out.get("actual_cost_rub") is not None else "reconcile_image"
    elif status == "PENDING" and out.get("cost_reconciled") is True:
        out["retry_action"], out["retry_allowed"] = "retry_qa", True
    elif status == "REJECT" and out.get("qa_terminal") is True and out.get("cost_reconciled") is True and out.get("cost_sla_pass") is True and (not out.get("rejected") or out.get("quality_rejection") is True):
        out["retry_action"] = "regenerate_quality"
        spent = _money(out.get("total_cost_rub"))
        reserve = _money(estimate_cost_rub() + _qa_reserve_rub(1))
        out["retry_allowed"] = bool(spent is not None and reserve is not None and spent + reserve <= _money(MAX_COST_RUB))
    return out


def _checked_preview_replay(account_id, row):
    out = {**row, "status": "ok", "idempotency_replay": True, "image_outcome": "succeeded"}
    error = _artifact_error(account_id, row)
    if error:
        return {**out, "status": "invalid_artifact", "code": error, "qa_passed": False,
                "preview_status": "ARTIFACT_INVALIDATED", "artifact_current": False,
                "retry_action": "blocked", "retry_allowed": False}
    out["artifact_current"] = True
    receipt = _current_qa_receipt(account_id, row)
    if receipt is None and not row.get("vision_qa_deferred"):
        out.update({"preview_status": "QA_RECEIPT_UPGRADE_REQUIRED", "code": "QA_RECEIPT_UPGRADE_REQUIRED",
                    "receipt_current": False, "qa_passed": False})
    else:
        out["receipt_current"] = receipt is not None
    return _with_retry_state(out)


def read_current_preview(account_id, banner_id, version):
    """No paid calls or writes: central predicate for adapter/worker/media guards."""
    _, row = _version_row(str(account_id), str(banner_id), int(version))
    if row is None:
        return {"status": "not_found", "receipt_current": False, "artifact_current": False, "qa_passed": False,
                "retry_action": "blocked", "retry_allowed": False}
    out = _checked_preview_replay(str(account_id), row)
    receipt = _current_qa_receipt(str(account_id), row)
    if out.get("artifact_current") is not True or not receipt:
        out["qa_passed"] = False
        return out
    verdict = receipt["verdict"]
    costs = _budget_scope_cost(str(account_id), row.get("provenance"), banner_id)
    image_cost = _exact_usage_receipt(str(account_id), "banner_premium_ai", row.get("request_id") or row.get("provider_request_id"))
    attempts = row.get("qa_cost_attempts") or []
    qa_known = bool(attempts) or (verdict.get("qa_cost_status") in {"not_requested", "known_not_billed"}
        and (verdict.get("proof_kind") == "local_diversity" or verdict.get("qa_status") in {"PENDING", "PROVIDER_ERROR"}))
    qa_units = 0
    for attempt in attempts:
        usage = _exact_usage_receipt(str(account_id), attempt.get("operation"), attempt.get("request_id"))
        share = _allocate_cost(usage.get("cost_rub"), attempt.get("allocation_slot"), attempt.get("allocation_size")) if usage.get("cost_known") is True else None
        if share is None:
            qa_known = False
        else:
            qa_units += int(_money(share) * 10000)
    qa_cost = qa_units / 10000.0 if qa_known else None
    reconciled = costs.get("known") is True and image_cost.get("cost_known") is True and qa_known
    # Reconciliation may resolve individual receipts without restoring their
    # logical-order provenance. An omitted debit must never produce a false PASS.
    required_requests = {str(image_cost.get("request_id") or "")} | {str(x.get("request_id") or "") for x in attempts}
    if reconciled and (not required_requests.issubset(set(costs.get("request_ids") or []))
            or _money(costs.get("total_cost_rub")) < _money(image_cost.get("cost_rub")) + _money(qa_cost)):
        reconciled = False
        costs = {**costs, "known": False, "reason": "budget_journal_incomplete"}
    out.update({"receipt_current": True, "qa_terminal": verdict.get("qa_terminal") is True,
                "qa_version": VISION_QA_VERSION, "cost_reconciled": reconciled,
                "actual_cost_rub": image_cost.get("cost_rub"), "qa_cost_rub": qa_cost, "budget": costs,
                "total_cost_rub": costs.get("total_cost_rub") if reconciled else None,
                "cost_sla_pass": reconciled and costs.get("duplicate_paid_calls", 0) == 0 and costs["total_cost_rub"] <= MAX_COST_RUB,
                "qa_passed": verdict.get("qa_status") == "PASS" and verdict.get("passed") is True
                    and (row.get("technical_qa") or {}).get("passed") is True and row.get("rejected") is not True})
    out["qa_passed"] = out["qa_passed"] and out["cost_sla_pass"]
    if not reconciled:
        out["preview_status"] = "COST_RECONCILIATION_REQUIRED"
    return _with_retry_state(out)


def _qa_identity_locks(account_id, entries):
    from contextlib import contextmanager
    @contextmanager
    def locked():
        from app.services.media_semantic_validator import _vision_advisory_engine
        keys = sorted({int.from_bytes(hashlib.blake2b(
            ("premium-qa-v2|"+str(account_id)+"|"+str(x.get("banner_id"))+"|"+str(x.get("version"))).encode(),
            digest_size=8).digest(), "big", signed=True) for x in entries})
        db = _vision_advisory_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
        held = []
        try:
            for key in keys:
                db.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
                held.append(key)
            yield
        finally:
            try:
                for key in reversed(held):
                    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            finally:
                db.close()
    return locked()


def _budget_scope_locks(account_id, hashes):
    return _qa_identity_locks("budget:"+str(account_id),
                              [{"banner_id": x, "version": 0} for x in sorted(set(hashes))])


def _legacy_batch_parser_invalid(receipt) -> bool:
    """One-time recovery candidate for receipts rejected by the old identity parser."""
    receipt=receipt if isinstance(receipt,dict) else {}
    verdict=receipt.get("verdict") or {}
    if str(verdict.get("qa_status") or "")!="INVALID_RESPONSE":
        return False
    issues=[str(x or "").lower() for x in (verdict.get("issues") or [])]
    return any(
        "batch_identity_mismatch" in x or "vision_qa_invalid_schema" in x
        for x in issues
    )


def _matched_batch_results(raw, identities):
    """Malformed or duplicate slots invalidate that item, preserving good neighbours."""
    by_slot = {}
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        return {}, {slot: "batch_invalid_envelope" for slot in range(1, len(identities)+1)}
    for item in results:
        if isinstance(item, dict) and type(item.get("slot")) is int and 1 <= item["slot"] <= len(identities):
            by_slot.setdefault(item["slot"], []).append(item)
    matched, errors = {}, {}
    for slot, identity in enumerate(identities, 1):
        items = by_slot.get(slot, [])
        if len(items) != 1:
            errors[slot] = "batch_duplicate_slot" if items else "batch_missing_slot"
            continue
        item = items[0]
        # BATCH_SLOT_IS_SERVER_IDENTITY_V1:
        # Slot membership is server-authored and unique inside this request.
        # Vision evaluates pixels only; machine identity is never trusted from
        # model output and is rebound server-side after slot matching.
        matched[slot] = item
    return matched, errors


def _normalize_vision_result(out: dict, meta: dict | None = None) -> dict:
    meta = dict(meta or {})
    required = (
        "semantic_match", "headline_ok", "exact_text_ok", "spelling_ok",
        "forbidden_claims_ok", "composition_ok", "crop_safe_ok",
        "professional_ok", "mobile_ok", "selling_typography_ok", "palette_ok",
        "reference_style_match", "editorial_richness_ok", "secondary_copy_density_ok",
    )
    envelope_ok = isinstance(out, dict)
    raw = out if envelope_ok else {}
    invalid = [key for key in ("pass",) + required if type(raw.get(key)) is not bool]
    raw_issues = raw.get("issues")
    if not isinstance(raw_issues, list) or any(not isinstance(x, str) for x in raw_issues):
        invalid.append("issues")
    issues = [x[:180] for x in raw_issues[:12]] if isinstance(raw_issues, list) and all(isinstance(x, str) for x in raw_issues) else []
    if invalid:
        issues = ["vision_qa_invalid_schema:" + ",".join(invalid)] + issues
    booleans = {key: raw.get(key) is True for key in required}
    valid = envelope_ok and not invalid
    passed = valid and raw.get("pass") is True and all(booleans.values())
    cost = _money(meta.get("cost_rub"))
    cost_known = meta.get("cost_known") is True and cost is not None
    return {
        "passed": passed, **booleans, "issues": issues,
        "qa_status": ("PASS" if passed else "REJECT") if valid else "INVALID_RESPONSE",
        "qa_terminal": valid, "qa_version": VISION_QA_VERSION,
        "qa_cost_known": cost_known, "qa_cost_rub": float(cost) if cost_known else None,
        "qa_cost_status": "known" if cost_known else "unknown",
        "cache_hit": meta.get("cache_hit") is True,
        "provider_request_id": meta.get("provider_request_id"), "request_id": meta.get("request_id"),
        "provider": meta.get("provider"), "model": meta.get("model"),
        "latency_ms": meta.get("latency_ms"), "usage": meta.get("usage"),
        "intent_key": meta.get("intent_key"), "pricing_version": meta.get("pricing_version"),
        "rate_source": meta.get("rate_source"), "operation": meta.get("operation"),
        "estimated_cost_rub": meta.get("estimated_cost_rub"),
        "request_cost_rub": meta.get("request_cost_rub"),
        "allocation_slot": meta.get("allocation_slot", 1), "allocation_size": meta.get("allocation_size", 1),
        "checked_at": _now_iso(),
    }



def _run_vision_qa(
    *, account_id: str, banner_path: Path, description: str, exact_title: str,
    exact_subtitle: str, exact_cta: str, exact_price: str, advantages: list[str] | None,
    hypothesis: str, fmt: str, composition_family: str = "",
    composition_profile: tuple[str, str] | None = None, icon_profile: tuple[str, str] | None = None,
    provenance: dict | None = None, qa_identity: dict | None = None,
) -> dict:
    from app.services.media_semantic_validator import _vision_chat_json
    contract = _vision_contract(description=description, exact_title=exact_title,
        exact_subtitle=exact_subtitle, exact_cta=exact_cta, exact_price=exact_price,
        advantages=advantages, hypothesis=hypothesis, fmt=fmt, composition_family=composition_family,
        composition_profile=composition_profile, icon_profile=icon_profile)
    identity = dict(qa_identity or {})
    if not identity or _artifact_sha256(banner_path) != identity.get("asset_sha256"):
        return {"passed": False, "qa_status": "INVALIDATED", "qa_terminal": False,
                "qa_cost_rub": 0.0, "qa_cost_known": True, "qa_cost_status": "not_requested",
                "issues": ["qa_identity_required_or_artifact_changed"], "qa_identity": identity}
    prompt = (
        "You are the final fail-closed QA inspector for one Russian commercial banner. Return strict JSON only. "
        "IDENTITY is opaque MACHINE METADATA used only to bind your result to the exact file. "
        "Fields banner_id, version, asset_sha256, qa_version and contract_sha256 are NOT visible banner copy: "
        "do not judge their spelling, phone/ID format, semantics, claims or typography, and never mention them in issues. "
        "Echo those IDENTITY fields exactly as supplied. Evaluate ONLY visible pixels against CONTRACT. "
        "Include explicit JSON booleans: pass, semantic_match, headline_ok, exact_text_ok, spelling_ok, "
        "forbidden_claims_ok, composition_ok, crop_safe_ok, professional_ok, mobile_ok, selling_typography_ok, "
        "palette_ok, reference_style_match, editorial_richness_ok, secondary_copy_density_ok. Include issues as an array of strings. "
        "Every boolean must be true for pass=true. Reject invented or malformed Russian text, unsupported claims, "
        "clipped text, unsafe crop, broken anatomy, unreadable mobile typography, weak/template-like composition, "
        "palette drift and text outside supplied copy or owner_allowed_secondary_microcopy. Respect the requested "
        "composition family/profile and owner reference hierarchy. IDENTITY="
        + json.dumps(identity, sort_keys=True) + "; CONTRACT=" + json.dumps(contract, ensure_ascii=False, sort_keys=True)
    )
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": _image_data_uri(banner_path), "detail": "high"}}]
    try:
        raw, meta = _vision_chat_json(str(account_id), VISION_QA_OPERATION, content,
            max_tokens=650, timeout=90, return_meta=True,
            provenance={"source": "premium_banner", "engine": ENGINE, "qa_version": VISION_QA_VERSION,
                        **dict(provenance or {})})
    except Exception as exc:
        return _provider_failure_verdict(exc, VISION_QA_OPERATION, identity)
    meta = _allocated_usage(account_id, VISION_QA_OPERATION, meta)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or any(
            raw.get(k) != identity[k] for k in ("banner_id", "version", "asset_sha256", "qa_version", "contract_sha256")):
        raw = {"issues": ["vision_qa_identity_mismatch"]}
    result = _normalize_vision_result(raw, meta)
    result["qa_identity"] = identity
    return result



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
    from app.services.job_execution_clock import provider_call

    db = SessionLocal()
    try:
        result = provider_call(generate,
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


def _create_preview_budget_locked(
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
    banner_id = _clean(parent_banner_id, 80) or _banner_identity(account_id, idem)
    versions = _load_versions(account_id, banner_id)
    for row in versions:
        if str(row.get("idempotency_key") or "") == idem:
            return _checked_preview_replay(account_id, row)
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
            return _checked_preview_replay(account_id, row)

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
                return _checked_preview_replay(account_id, row)

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
        # Owner-approved examples are style references, never product photo proofs.
        # Select only after exact paid-intent replay and cost gates above.
        owner_example = owner_variant(
            account_id, f"{banner_id}|{idem}", version,
            previous_palettes=tuple(str(row.get("owner_banner_example_palette") or "") for row in versions),
        )
        if owner_example is not None:
            reference_style = combine_reference_style(owner_example, reference_style)
            if str(accent_color).upper() == DEFAULT_ACCENT:
                accent_color = owner_example["accent"]

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
        image_provenance.update(_budget_provenance(image_provenance, banner_id))
        image_provenance.setdefault("intent_key", f"premium-image:{banner_id}:v{version}")
        prior_cost = _budget_scope_cost(account_id, image_provenance, banner_id)
        if not prior_cost["known"] or prior_cost.get("duplicate_paid_calls", 0) or prior_cost["total_cost_rub"] + estimate + _qa_reserve_rub(1) > MAX_COST_RUB:
            return {"status": "blocked", "code": "BANNER_COST_LIMIT" if prior_cost["known"] else "COST_RECONCILIATION_REQUIRED",
                    "retry_action": "blocked", "retry_allowed": False, "image_outcome": "not_started",
                    "budget": prior_cost, "banner_id": banner_id, "version": version}
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

        image_receipt = _exact_usage_receipt(account_id, operation, core.get("request_id") or core.get("provider_request_id"))
        actual_known = image_receipt["cost_known"]
        actual = image_receipt["cost_rub"] if actual_known else 0.0

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
            "qa_cost_known": True,
            "qa_cost_status": "not_requested",
            "qa_terminal": False,
            "qa_status": "PENDING",
            "cache_hit": False,
            "checked_at": _now_iso(),
        }

        # Never add more paid QA spend to an already over-budget or unreconciled image.
        # Campaign batch mode durably stores the image first and finalizes Vision
        # in groups of up to four; direct API callers retain the single-image path.
        after_image_cost = _budget_scope_cost(account_id, image_provenance, banner_id)
        qa_admitted = (actual_known and after_image_cost.get("known") is True
            and after_image_cost.get("duplicate_paid_calls", 0) == 0
            and after_image_cost["total_cost_rub"] + _qa_reserve_rub(1) <= MAX_COST_RUB)
        if qa_admitted and technical_qa.get("passed") is True and not defer_vision_qa:
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
                provenance=image_provenance,
                qa_identity=_qa_identity(account_id, {"banner_id": banner_id, "version": version,
                    "artifact_sha256": technical_qa.get("artifact_sha256"), "qa_contract": qa_contract}),
            )

        qa_cost = vision_qa.get("qa_cost_rub") if vision_qa.get("qa_cost_known") is True else None
        current_budget = _budget_scope_cost(account_id, image_provenance, banner_id)
        costs_known = actual_known and qa_cost is not None and current_budget.get("known") is True
        total_cost = current_budget.get("total_cost_rub") if costs_known else None
        cost_sla_pass = bool(costs_known and current_budget.get("duplicate_paid_calls", 0) == 0 and total_cost <= MAX_COST_RUB)
        qa_passed = bool(technical_qa.get("passed")) and bool(vision_qa.get("passed"))

        circuit = None
        if costs_known and total_cost > MAX_COST_RUB:
            circuit = _trip_cost_circuit(
                actual_cost_rub=actual,
                total_cost_rub=total_cost,
                banner_id=banner_id,
                version=version,
            )

        if not costs_known:
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
            "owner_banner_example_contract": owner_example["contract"] if owner_example else None,
            "owner_banner_example_palette": owner_example["palette_key"] if owner_example else None,
            "owner_banner_example_accent": owner_example["accent"] if owner_example else None,
            "owner_banner_example_library_file": owner_example["owner_library_file"] if owner_example else None,
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
            "total_cost_rub": total_cost,
            "budget": current_budget,
            "cost_cap_rub": MAX_COST_RUB,
            "cost_reconciled": costs_known,
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
            "provenance": image_provenance,
            "image_usage_receipt": image_receipt,
            "request_id": core.get("request_id") or core.get("provider_request_id"),
            "image_outcome": "succeeded",
            "artifact_current": True,
            "qa_version": VISION_QA_VERSION,
            "user_instruction": _clean(edit_instruction, 1200),
        }
        versions.append(row)
        _save_versions(account_id, banner_id, versions)
        if not defer_vision_qa:
            return _apply_vision_verdict(account_id=account_id, banner_id=banner_id, version=version,
                vision_qa={**vision_qa, "qa_identity": _qa_identity(account_id, row)}, qa_mode="single_v2") or row
        return _with_retry_state(row)



def _version_row(account_id: str, banner_id: str, version: int) -> tuple[list[dict], dict | None]:
    versions = _load_versions(str(account_id), str(banner_id))
    chosen = next((x for x in versions if int(x.get("version") or 0) == int(version)), None)
    return versions, chosen


def _apply_vision_verdict(
    *, account_id: str, banner_id: str, version: int, vision_qa: dict,
    qa_mode: str, batch_size: int = 1,
) -> dict | None:
    versions, chosen = _version_row(account_id, banner_id, version)
    if chosen is None:
        return None
    error = _artifact_error(account_id, chosen)
    identity = _qa_identity(account_id, chosen)
    incoming = dict(vision_qa or {})
    if error or incoming.get("qa_identity") != identity:
        return {**chosen, "qa_passed": False, "preview_status": "ARTIFACT_INVALIDATED",
                "code": error or "QA_IDENTITY_MISMATCH", "retry_action": "blocked", "retry_allowed": False}

    key = _qa_receipt_key(identity)
    receipts = dict(chosen.get("qa_receipts") or {})
    existing = receipts.get(key)
    # Keep the first terminal quality proof. A late error cannot erase PASS/REJECT.
    verdict = dict(existing["verdict"] if isinstance(existing, dict)
        and existing.get("identity") == identity and existing.get("verdict", {}).get("qa_terminal") is True else incoming)
    attempts = {str(x.get("attempt_key") or ""): dict(x) for x in (chosen.get("qa_cost_attempts") or [])}
    no_charge_state = incoming.get("qa_cost_status") if incoming.get("qa_cost_status") in {"not_requested", "known_not_billed"} else None
    no_charge = no_charge_state is not None and incoming.get("qa_cost_known") is True
    if not no_charge:
        request_id = incoming.get("request_id") or incoming.get("provider_request_id")
        operation = incoming.get("operation") or (VISION_QA_BATCH_OPERATION if batch_size > 1 else VISION_QA_OPERATION)
        attempt_key = f"{operation}:{request_id or incoming.get('intent_key') or key}"
        attempts[attempt_key] = {**attempts.get(attempt_key, {}), "attempt_key": attempt_key,
            "request_id": request_id, "provider_request_id": incoming.get("provider_request_id"),
            "intent_key": incoming.get("intent_key"), "operation": operation,
            "allocation_slot": int(incoming.get("allocation_slot") or 1),
            "allocation_size": int(incoming.get("allocation_size") or batch_size or 1)}
    qa_units = 0
    qa_known = bool(attempts) or no_charge
    for attempt in attempts.values():
        usage = _exact_usage_receipt(account_id, attempt["operation"], attempt.get("request_id"))
        share = _allocate_cost(usage.get("cost_rub"), attempt["allocation_slot"], attempt["allocation_size"]) if usage.get("cost_known") is True else None
        attempt.update({"cost_known": share is not None, "cost_rub": share,
                        "request_cost_rub": usage.get("cost_rub"),
                        "pricing_version": usage.get("pricing_version"), "rate_source": usage.get("rate_source")})
        if share is None:
            qa_known = False
        else:
            qa_units += int(_money(share) * 10000)
    qa_cost = qa_units / 10000.0 if qa_known else None
    image = _exact_usage_receipt(account_id, "banner_premium_ai", chosen.get("request_id") or chosen.get("provider_request_id"))
    image_known = image.get("cost_known") is True
    actual = image.get("cost_rub") if image_known else None
    budget = _budget_scope_cost(account_id, chosen.get("provenance"), banner_id)
    known = image_known and qa_known and budget.get("known") is True
    required_requests = {str(image.get("request_id") or "")} | {str(x.get("request_id") or "") for x in attempts.values()}
    if known and (not required_requests.issubset(set(budget.get("request_ids") or []))
            or _money(budget.get("total_cost_rub")) < _money(actual) + _money(qa_cost)):
        known = False
        budget = {**budget, "known": False, "reason": "budget_journal_incomplete"}
    total = budget.get("total_cost_rub") if known else None
    cost_ok = known and budget.get("duplicate_paid_calls", 0) == 0 and total <= MAX_COST_RUB
    technical_ok = (chosen.get("technical_qa") or {}).get("passed") is True
    qa_passed = technical_ok and verdict.get("qa_status") == "PASS" and verdict.get("passed") is True
    circuit = chosen.get("cost_circuit")
    if known and total > MAX_COST_RUB:
        circuit = _trip_cost_circuit(actual_cost_rub=actual, total_cost_rub=total, banner_id=banner_id, version=version)
    if not known:
        preview_status = "COST_RECONCILIATION_REQUIRED"
    elif not cost_ok:
        preview_status = "BLOCKED_COST"
    elif chosen.get("rejected"):
        preview_status = "REJECTED"
    elif not technical_ok:
        preview_status = "QA_FAILED"
    elif verdict.get("qa_status") == "INVALID_RESPONSE":
        preview_status = "QA_INVALID_RESPONSE"
    elif verdict.get("qa_status") == "PROVIDER_ERROR":
        preview_status = "VISION_QA_PROVIDER_FAILED"
    elif verdict.get("qa_status") == "PENDING":
        preview_status = "VISION_QA_PENDING"
    elif not qa_passed:
        preview_status = "QA_FAILED"
    else:
        preview_status = "AVAILABLE_FOR_USE" if chosen.get("approved") else "DRAFT_PREVIEW"
    verdict.update({"qa_identity": identity, "qa_cost_known": qa_known,
                    "qa_cost_rub": qa_cost, "qa_cost_status": no_charge_state if no_charge and not attempts else ("known" if qa_known else "unknown")})
    receipts[key] = {"schema": 1, "identity": identity, "verdict": verdict,
                     "persisted_at": (existing or {}).get("persisted_at") or _now_iso()}
    chosen.update({"preview_status": preview_status, "actual_cost_rub": actual,
        "cost_reconciled": known, "qa_cost_rub": qa_cost, "total_cost_rub": total,
        "cost_sla_pass": cost_ok, "cost_circuit": circuit, "budget": budget,
        "image_outcome": "succeeded", "vision_qa": verdict, "qa_receipts": receipts,
        "qa_cost_attempts": list(attempts.values()), "qa_version": VISION_QA_VERSION,
        "vision_qa_deferred": False, "vision_qa_mode": str(qa_mode),
        "vision_qa_batch_size": int(batch_size or 1), "qa_passed": qa_passed and not chosen.get("rejected", False),
        "text_qa_status": "PASS" if all(verdict.get(k) is True for k in ("headline_ok", "exact_text_ok", "spelling_ok")) else "FAILED",
        "visual_qa_status": "PASS" if qa_passed else "FAILED", "vision_qa_finalized_at": _now_iso()})
    _save_versions(str(account_id), str(banner_id), versions)
    return _with_retry_state(chosen)



def finalize_preview_vision_batch(*, account_id: str, entries: list[dict], provenance: dict | None = None) -> dict:
    """Lock overlapping asset scopes, then reuse or persist individual receipts."""
    account_id = _clean(account_id, 200)
    rows = list(entries or [])
    if not account_id:
        return {"ok": False, "code": "account_required", "previews": {}, "unresolved": []}
    if len(rows) > VISION_QA_BATCH_MAX:
        raise ValueError("premium vision QA batch max is 4")
    if not rows:
        return {"ok": True, "previews": {}, "unresolved": [], "provider_calls": 0, "batch_provider_calls": 0}
    if any(not isinstance(x, dict) for x in rows):
        return {"ok": False, "code": "batch_entry_invalid", "previews": {}, "unresolved": [], "provider_calls": 0}
    with _qa_identity_locks(account_id, rows):
        scopes = []
        for entry in rows:
            if type(entry.get("version")) is not int:
                continue
            _, row = _version_row(account_id, str(entry.get("banner_id") or ""), entry["version"])
            if row:
                scopes.append(_budget_provenance(row.get("provenance"), row.get("banner_id"))["budget_intent_hash"])
        with _budget_scope_locks(account_id, scopes):
            return _finalize_preview_vision_batch_locked(account_id, rows, provenance)


def _finalize_preview_vision_batch_locked(account_id, rows, provenance):
    from app.services.media_semantic_validator import _vision_chat_json
    resolved, pending, unresolved = {}, [], []
    counts = {}
    for entry in rows:
        raw_version = entry.get("version")
        key = f"{_clean(entry.get('banner_id'), 80)}:v{raw_version}"
        counts[key] = counts.get(key, 0) + 1
    for entry in rows:
        banner_id = _clean(entry.get("banner_id"), 80)
        version = entry.get("version")
        key = f"{banner_id}:v{version}"
        if not banner_id or type(version) is not int or version <= 0 or counts[key] != 1:
            unresolved.append(key)
            continue
        _versions, chosen = _version_row(account_id, banner_id, version)
        if chosen is None:
            unresolved.append(key)
            continue
        replay = _checked_preview_replay(account_id, chosen)
        receipt = _current_qa_receipt(account_id, chosen)
        _metadata_policy_upgraded=False
        if receipt and _metadata_only_false_reject(receipt):
            # MACHINE_METADATA_FALSE_REJECT_UPGRADE_V1:
            # preserve the old terminal receipt for audit, change only the local
            # QA contract identity, and re-run Vision against the SAME PNG. This
            # must never regenerate the paid image or auto-upgrade a real visual
            # rejection.
            _metadata_policy_upgraded=_upgrade_metadata_false_reject_contract(
                account_id,banner_id,version,receipt
            )
            if _metadata_policy_upgraded:
                _,chosen=_version_row(account_id,banner_id,version)
                replay=_checked_preview_replay(account_id,chosen)
                receipt=_current_qa_receipt(account_id,chosen)
        if replay.get("artifact_current") is not True:
            resolved[key] = replay
            unresolved.append(key)
            continue
        if replay.get("preview_status") == "QA_RECEIPT_UPGRADE_REQUIRED" and not _metadata_policy_upgraded:
            resolved[key] = replay
            unresolved.append(key)
            continue
        verdict = (receipt or {}).get("verdict") or {}
        _slot_parser_retry=bool(
            receipt and _legacy_batch_parser_invalid(receipt)
            and not bool(chosen.get("slot_binding_retry_used"))
        )
        safe_unpaid_retry = (verdict.get("qa_cost_status") in {"not_requested", "known_not_billed"}
            and (verdict.get("qa_status") == "PENDING" or
                 verdict.get("qa_status") == "PROVIDER_ERROR" and verdict.get("provider_retryable") is True))
        if _slot_parser_retry:
            # BATCH_SLOT_PARSER_UPGRADE_RETRY_V1:
            # One bounded QA-only retry on the same immutable PNG after changing
            # result binding from model-echoed identity to server-authored slot.
            # Existing QA spend remains in the journal and the normal MAX_COST_RUB
            # reserve gate below must admit this retry. The durable marker prevents loops.
            chosen["slot_binding_retry_used"]=True
            chosen["slot_binding_retry_at"]=_now_iso()
            _save_versions(account_id,banner_id,_versions)
        if receipt and not (safe_unpaid_retry or _slot_parser_retry):
            # Includes terminal PASS/REJECT waiting only for accounting. No provider replay.
            updated = _apply_vision_verdict(account_id=account_id, banner_id=banner_id, version=version,
                vision_qa=receipt["verdict"], qa_mode=str(chosen.get("vision_qa_mode") or "receipt_reuse"),
                batch_size=int(chosen.get("vision_qa_batch_size") or 1))
            resolved[key] = updated or replay
            if not updated or updated.get("qa_terminal") is not True or updated.get("cost_reconciled") is not True:
                unresolved.append(key)
            continue
        if (chosen.get("technical_qa") or {}).get("passed") is not True or not isinstance(chosen.get("qa_contract"), dict):
            resolved[key] = {**replay, "qa_passed": False, "retry_action": "blocked", "retry_allowed": False}
            unresolved.append(key)
            continue
        image = _exact_usage_receipt(account_id, "banner_premium_ai", chosen.get("request_id") or chosen.get("provider_request_id"))
        if image.get("cost_known") is not True:
            resolved[key] = {**replay, "cost_reconciled": False, "qa_passed": False,
                "preview_status": "COST_RECONCILIATION_REQUIRED", "retry_action": "reconcile_image", "retry_allowed": False}
            unresolved.append(key)
            continue
        pending.append((banner_id, version, dict(chosen)))

    # Removing a blocked member changes the per-image reserve. Recompute to a fixed point.
    while pending:
        kept = []
        reserve_by_scope = {}
        for slot, (banner_id, _, chosen) in enumerate(pending, 1):
            digest = _budget_provenance(chosen.get("provenance"), banner_id)["budget_intent_hash"]
            share = _allocate_cost(_qa_reserve_rub(1), slot, len(pending))
            reserve_by_scope[digest] = reserve_by_scope.get(digest, 0) + int(_money(share) * 10000)
        for banner_id, version, chosen in pending:
            scope = _budget_scope_cost(account_id, chosen.get("provenance"), banner_id)
            preset_guard = _paid_preset_guard(str(chosen.get("source_size") or FORMAT_PRESETS["1:1"]["source_size"]))
            allowed = preset_guard.get("allowed") is True and scope.get("known") is True and scope.get("duplicate_paid_calls", 0) == 0
            reserve = reserve_by_scope[scope["budget_intent_hash"]] / 10000.0
            allowed = allowed and _money(scope["total_cost_rub"]) + _money(reserve) <= _money(MAX_COST_RUB)
            if allowed:
                kept.append((banner_id, version, chosen))
            else:
                key = f"{banner_id}:v{version}"
                resolved[key] = {**chosen, "qa_passed": False, "budget": scope,
                    "paid_admission": preset_guard,
                    "code": ("COST_CIRCUIT_OPEN" if preset_guard.get("circuit_ok") is False else "COST_CAP_EXCEEDED") if not preset_guard.get("allowed") else None,
                    "preview_status": "BLOCKED_COST" if scope.get("known") else "COST_RECONCILIATION_REQUIRED",
                    "retry_action": "blocked", "retry_allowed": False}
                unresolved.append(key)
        if len(kept) == len(pending):
            break
        pending = kept
    if not pending:
        return {"ok": not unresolved and all(x.get("qa_passed") is True and x.get("cost_sla_pass") is True for x in resolved.values()),
                "previews": resolved, "unresolved": sorted(set(unresolved)), "provider_calls": 0,
                "batch_provider_calls": 0, "recovery_calls": 0, "batch_size": 0}

    identities = [_qa_identity(account_id, chosen) for _, _, chosen in pending]
    batch_id = hashlib.sha256(json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    allocation_key = "premium_banner_qa_allocation:" + batch_id
    allocations = [{"identity": identity, **_budget_provenance(chosen.get("provenance"), banner_id)}
                   for identity, (banner_id, _, chosen) in zip(identities, pending)]
    # Persist allocation before the paid request; no result can exist without its cost mapping.
    _storage_json(account_id, allocation_key, {"schema": 1, "batch_id": batch_id, "entries": allocations})
    prompt = (
        "BATCH PREMIUM BANNER QA. Inspect each SLOT independently. Return one JSON object with results array. "
        "IDENTITY fields are opaque MACHINE METADATA used only for server-side binding. Do not judge their spelling, "
        "phone/ID format, semantics, claims or typography, and never mention them in issues. "
        "Return the numeric slot for each result; identity echoes are ignored by the server. "
        "Evaluate ONLY visible pixels against that SLOT's CONTRACT. "
        "Include explicit JSON booleans pass, semantic_match, headline_ok, exact_text_ok, spelling_ok, forbidden_claims_ok, "
        "composition_ok, crop_safe_ok, professional_ok, mobile_ok, selling_typography_ok, palette_ok, reference_style_match, "
        "editorial_richness_ok, secondary_copy_density_ok, and issues as array of strings. Every check must be true for PASS. "
        "Reject invented claims or text, malformed Cyrillic, unreadable mobile text, awkward crop, poor anatomy, "
        "cheap repetitive templates, palette drift, extra secondary copy, and failure to respect composition and owner style."
    )
    content = [{"type": "text", "text": prompt}]
    for slot, (identity, (_, _, chosen)) in enumerate(zip(identities, pending), 1):
        content.append({"type": "text", "text": "SLOT " + str(slot) + "; IDENTITY=" + json.dumps(identity, sort_keys=True)
                        + "; CONTRACT=" + json.dumps(chosen["qa_contract"], ensure_ascii=False, sort_keys=True)})
        content.append({"type": "image_url", "image_url": {"url": _image_data_uri(Path(chosen["absolute_path"])), "detail": "high"}})
    batch_prov = {**dict(provenance or {}), "source": "premium_banner_batch", "engine": ENGINE,
        "qa_version": VISION_QA_VERSION, "batch_size": len(pending), "qa_allocation_key": allocation_key,
        "budget_intent_hashes": ",".join(sorted({x["budget_intent_hash"] for x in allocations}))}
    try:
        raw, meta = _vision_chat_json(account_id, VISION_QA_BATCH_OPERATION, content,
            max_tokens=max(900, min(2600, 620*len(pending))), timeout=120, return_meta=True, provenance=batch_prov)
    except Exception as exc:
        failure_meta = dict(getattr(exc, "vision_meta", {}) or {})
        for slot, (identity, (banner_id, version, _)) in enumerate(zip(identities, pending), 1):
            verdict = _provider_failure_verdict(exc, VISION_QA_BATCH_OPERATION, identity,
                intent_key=batch_id, slot=slot, size=len(pending))
            key = f"{banner_id}:v{version}"
            updated = _apply_vision_verdict(account_id=account_id, banner_id=banner_id, version=version,
                vision_qa=verdict, qa_mode="batch_v2", batch_size=len(pending))
            if updated:
                resolved[key] = updated
            unresolved.append(key)
        calls = failure_meta.get("provider_calls")
        return {"ok": False, "previews": resolved, "unresolved": sorted(set(unresolved)), "provider_calls": calls,
                "provider_call_count_known": failure_meta.get("provider_call_count_known") is True,
                "batch_provider_calls": calls, "recovery_calls": 0,
                "batch_size": len(pending), "error": "batch_provider_error:"+type(exc).__name__}

    meta = dict(meta or {})
    matched, errors = _matched_batch_results(raw, identities)
    for slot, (identity, (banner_id, version, _)) in enumerate(zip(identities, pending), 1):
        allocated = _allocated_usage(account_id, VISION_QA_BATCH_OPERATION, meta, slot, len(pending))
        allocated["intent_key"] = meta.get("intent_key") or batch_id
        verdict = _normalize_vision_result(matched.get(slot, {"issues": [errors.get(slot, "batch_invalid_result")]}), allocated)
        verdict.update({"qa_identity": identity, "batch_qa": True, "batch_id": batch_id,
                        "batch_size": len(pending), "batch_slot": slot, "qa_allocation_key": allocation_key})
        key = f"{banner_id}:v{version}"
        updated = _apply_vision_verdict(account_id=account_id, banner_id=banner_id, version=version,
            vision_qa=verdict, qa_mode="batch_v2", batch_size=len(pending))
        if updated:
            resolved[key] = updated
        if not updated or updated.get("qa_terminal") is not True or updated.get("cost_reconciled") is not True:
            unresolved.append(key)
    calls = 0 if meta.get("cache_hit") is True else int(meta.get("provider_calls") or 1)
    return {"ok": not unresolved and all(x.get("qa_passed") is True and x.get("cost_sla_pass") is True for x in resolved.values()),
        "previews": resolved, "unresolved": sorted(set(unresolved)), "provider_calls": calls,
        "batch_provider_calls": calls, "recovery_calls": 0, "batch_size": len(pending),
        "cache_hit": meta.get("cache_hit") is True, "provider_request_id": meta.get("provider_request_id"),
        "batch_id": batch_id, "batch_cost_rub": _exact_usage_receipt(account_id, VISION_QA_BATCH_OPERATION,
            meta.get("request_id") or meta.get("provider_request_id")).get("cost_rub")}



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
    artifact_error = _artifact_error(account_id, chosen)
    if artifact_error:
        return {"status": "invalid_artifact", "code": artifact_error, "retry_action": "blocked", "retry_allowed": False}
    receipt = _current_qa_receipt(account_id, chosen)
    if not receipt or receipt.get("verdict", {}).get("qa_status") != "PASS":
        return {"status": "blocked", "code": "QA_RECEIPT_REQUIRED", "retry_action": "retry_qa", "retry_allowed": False}
    chosen = _apply_vision_verdict(account_id=account_id, banner_id=banner_id, version=int(chosen["version"]),
        vision_qa=receipt["verdict"], qa_mode=str(chosen.get("vision_qa_mode") or "receipt_reuse"))
    if chosen is None:
        return {"status": "blocked", "code": "PREMIUM_VERSION_NOT_FOUND"}
    versions = _load_versions(account_id, banner_id)
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


def reject_preview(account_id: str, banner_id: str, version: int | None = None,
                   reason: str = "", *, quality_rejection: bool = False) -> dict:
    versions = _load_versions(account_id, banner_id)
    if not versions:
        return {"status": "not_found", "code": "PREMIUM_BANNER_NOT_FOUND"}
    chosen = max(versions, key=lambda x: int(x.get("version") or 0)) if version is None else next(
        (x for x in versions if int(x.get("version") or 0) == int(version)), None)
    if chosen is None:
        return {"status": "not_found", "code": "PREMIUM_VERSION_NOT_FOUND"}
    if chosen.get("approved"):
        return {"status": "blocked", "code": "APPROVED_VERSION_CANNOT_BE_REJECTED"}
    if quality_rejection:
        if not str(reason).startswith("BANNER_DUPLICATE:"):
            return {"status": "blocked", "code": "QUALITY_REJECTION_PROOF_REQUIRED"}
        error = _artifact_error(account_id, chosen)
        if error:
            return {"status": "blocked", "code": error, "retry_action": "blocked", "retry_allowed": False}
        identity = _qa_identity(account_id, chosen)
        prior = _current_qa_receipt(account_id, chosen)
        verdict = dict((prior or {}).get("verdict") or {})
        if prior is None and not chosen.get("vision_qa_deferred"):
            return {"status": "blocked", "code": "QA_RECEIPT_UPGRADE_REQUIRED", "retry_action": "blocked", "retry_allowed": False}
        verdict.update({"passed": False, "qa_terminal": True, "qa_status": "REJECT", "qa_identity": identity,
            "qa_version": VISION_QA_VERSION, "proof_kind": "local_diversity", "issues": [_clean(reason, 500)]})
        if not chosen.get("qa_cost_attempts") and prior is None:
            verdict.update({"qa_cost_rub": 0.0, "qa_cost_known": True, "qa_cost_status": "not_requested"})
        key = _qa_receipt_key(identity)
        receipts = dict(chosen.get("qa_receipts") or {})
        if prior:
            chosen.setdefault("qa_history", []).append(json.loads(json.dumps(prior)))
        receipts[key] = {"schema": 1, "identity": identity, "verdict": verdict, "persisted_at": _now_iso()}
        chosen.update({"qa_receipts": receipts, "vision_qa": verdict, "quality_rejection": True})
    chosen.update({"rejected": True, "preview_status": "REJECTED", "rejected_at": _now_iso(),
                   "rejection_reason": _clean(reason, 500), "qa_passed": False})
    _save_versions(account_id, banner_id, versions)
    if quality_rejection:
        return _apply_vision_verdict(account_id=account_id, banner_id=banner_id, version=int(chosen["version"]),
            vision_qa=chosen["vision_qa"], qa_mode="local_diversity_reject") or chosen
    return {"status": "ok", "banner_id": banner_id, "version": chosen["version"], "preview_status": "REJECTED",
            "rejected_at": chosen["rejected_at"], "retry_action": "blocked", "retry_allowed": False}



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
    parameters = dict(locals())
    account = _clean(account_id, 200)
    banner = _clean(parent_banner_id, 80) or _banner_identity(account, _clean(idempotency_key, 300))
    if not account or not _clean(idempotency_key, 300):
        return _create_preview_budget_locked(**parameters)
    with _budget_scope_locks(account, [_budget_provenance(provenance, banner)["budget_intent_hash"]]):
        return _create_preview_budget_locked(**parameters)
