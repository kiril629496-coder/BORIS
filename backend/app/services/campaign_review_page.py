from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.media_asset import MediaAsset
from app.models.media_link import MediaLink
from app.services.exception_observability import observe_suppressed


PUBLIC_IMAGE_BASE = "https://boris-ai.pro/images/"
REVIEW_ROOT = "/root/BORIS/backend/images/reviews"


def _payload(row: CampaignItem) -> dict:
    try:
        return json.loads(row.payload_json or "{}")
    except Exception:
        return {}


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _asset_url(asset: MediaAsset) -> str:
    return PUBLIC_IMAGE_BASE + quote(str(asset.storage_key or ""), safe="/")


def _browser_url(raw: str) -> str:
    """Encode legacy Cyrillic/space image paths for safe HTML navigation."""
    value=str(raw or "").strip()
    if not value:
        return ""
    parsed=urlparse(value)
    safe_path=quote(unquote(parsed.path), safe="/:@-._~!$&'()*+,;=%")
    return parsed._replace(path=safe_path).geturl()


def _assert_local_review_media(cards: list[dict]) -> None:
    """Fail closed when a BORIS-hosted review image is missing on disk."""
    images_root=Path("/root/BORIS/backend/images").resolve()
    missing=[]
    for card in cards:
        for raw in card.get("images") or []:
            parsed=urlparse(str(raw or ""))
            if parsed.netloc != "boris-ai.pro" or not parsed.path.startswith("/images/"):
                continue
            local=(images_root / unquote(parsed.path[len("/images/"):])).resolve()
            try:
                local.relative_to(images_root)
            except Exception:
                missing.append(str(raw)); continue
            if not local.is_file():
                missing.append(str(raw))
    if missing:
        raise RuntimeError(f"campaign review: {len(missing)} local media files are missing")


def _submitted_xml_cards(submitted: dict, expected_sha: str, validated_artifact: str | None = None) -> list[dict]:
    """Render exactly the immutable XML bytes that were validated and handed to Avito.

    ``feed_url`` is the live public Autoload endpoint and is intentionally NOT a
    review source: its bytes can change on a later revision. Publication therefore
    persists ``validated_artifact`` in submitted_snapshot. For legacy snapshots we
    accept an exact-SHA recovery artifact supplied by the caller.
    """
    artifact=str(submitted.get("validated_artifact") or validated_artifact or "").strip()
    if not artifact:
        raise RuntimeError("campaign review: submitted snapshot has no immutable validated artifact")
    if artifact.startswith("/images/"):
        artifact="https://boris-ai.pro"+artifact
    parsed=urlparse(artifact)
    if parsed.scheme != "https" or parsed.netloc != "boris-ai.pro" or not parsed.path.startswith("/images/"):
        raise RuntimeError("campaign review: submitted validated artifact is outside BORIS images")
    images_root=Path("/root/BORIS/backend/images").resolve()
    local=(images_root / unquote(parsed.path[len("/images/"):])).resolve()
    try:
        local.relative_to(images_root)
    except Exception:
        raise RuntimeError("campaign review: submitted feed path escaped images root")
    data=local.read_bytes()
    actual=hashlib.sha256(data).hexdigest()
    if not expected_sha or actual != expected_sha:
        raise RuntimeError("campaign review: submitted XML file SHA differs from submitted snapshot")
    root=ET.fromstring(data)
    cards=[]
    for ad in root.findall("./Ad"):
        def txt(name):
            node=ad.find(name)
            return str(node.text or "").strip() if node is not None else ""
        images=[str(node.attrib.get("url") or "").strip() for node in ad.findall("./Images/Image") if str(node.attrib.get("url") or "").strip()]
        category=txt("Category")
        if not category:
            parts=[x for x in (txt("ServiceType"),txt("ServiceSubtype"),txt("GoodsType"),txt("GoodsSubType")) if x]
            category=" → ".join(parts)
        cards.append({
            "id": txt("Id") or "—",
            "title": txt("Title") or "Без названия",
            "description": txt("Description"),
            "city": txt("Address"),
            "category": category,
            "images": images,
            "status": "submitted_snapshot",
        })
    if not cards:
        raise RuntimeError("campaign review: submitted XML contains no Ad nodes")
    return cards


def generate_campaign_review_page(db, campaign_id: int, account_id: str, expected_xml_sha: str | None = None) -> dict:
    """Materialize a stable no-auth review page from canonical CampaignItem+MediaLink state.

    This function is read-only with respect to campaign/media content. It emits a
    static artifact atomically and returns metadata for feed_factory.review_page.
    It never approves or publishes anything to Avito.
    """
    campaign = db.query(Campaign).filter(Campaign.id == int(campaign_id)).first()
    if not campaign:
        raise RuntimeError("campaign review: campaign not found")
    rows = (db.query(CampaignItem)
            .filter(CampaignItem.campaign_id == int(campaign_id),
                    CampaignItem.account_id == str(account_id))
            .order_by(CampaignItem.id.asc()).all())
    # REVIEW_PAGE_CURRENT_SCOPE_V1: CampaignItem history can contain a full
    # superseded generation beside the currently published/ready generation.
    # Public review must never mix obsolete cards into the owner's current scope.
    rows = [row for row in rows if str(row.status or "").strip().lower() != "superseded"]
    if not rows:
        raise RuntimeError("campaign review: no account-scoped items")

    try:
        root = json.loads(campaign.settings_json or "{}")
    except Exception:
        root = {}
    ff = root.get("feed_factory") or {}
    check = ff.get("official_xml_check") or ff.get("official_xmlcheck") or {}
    check_status = str(check.get("status") or "").lower()
    if check_status not in {"pass", "ok"}:
        raise RuntimeError("campaign review: official Avito XML check is not PASS")
    official_sha = str(check.get("sha256") or "").strip()
    expected_sha = str(expected_xml_sha or "").strip()
    if expected_sha and (not official_sha or expected_sha != official_sha):
        raise RuntimeError("campaign review: current XML SHA differs from official Avito-validated SHA")

    activation = dict(ff.get("owner_activation") or {})
    activation_status = str(activation.get("status") or "").strip().lower()
    submitted = dict(activation.get("submitted_snapshot") or {})
    submitted_sha = str(submitted.get("sha256") or "").strip()
    publication_snapshot = bool(
        activation_status in {"processing","submitted","scheduled","scheduled_waiting","scheduled_pending_review","pending_review","partial","completed"}
        and submitted_sha and submitted_sha == official_sha
    )

    # REVIEW_PAGE_SUBMITTED_XML_SOURCE_V1: REVIEW_PAGE_SUBMITTED_XML_SOURCE_V2: once publication started, DB cards/media
    # may continue evolving for later work. The public result must instead render
    # the exact immutable XML bytes whose SHA was submitted to Avito. Older
    # submitted_snapshot rows predate validated_artifact; recover only from an
    # owner_ready_xml / official-check artifact whose declared SHA is identical.
    if publication_snapshot:
        _legacy_artifact=None
        if not str(submitted.get("validated_artifact") or "").strip():
            _owner_ready=dict(ff.get("owner_ready_xml") or {})
            if str(_owner_ready.get("sha256") or "").strip()==submitted_sha:
                _legacy_artifact=str(_owner_ready.get("artifact") or "").strip() or None
            if not _legacy_artifact and official_sha==submitted_sha:
                _check_url=str(check.get("url") or "").strip()
                if _check_url.startswith("https://boris-ai.pro/images/") or _check_url.startswith("/images/"):
                    _legacy_artifact=_check_url
        cards = _submitted_xml_cards(submitted, submitted_sha, validated_artifact=_legacy_artifact)
    else:
        cards = []
        for row in rows:
            p = _payload(row)
            links = (db.query(MediaLink, MediaAsset)
                     .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
                     .filter(MediaLink.entity_type == "campaign_item",
                             MediaLink.entity_id == str(row.id),
                             MediaAsset.deleted_at.is_(None))
                     .order_by(MediaLink.position.asc(), MediaLink.id.asc()).all())
            urls = [_asset_url(asset) for _, asset in links]
            cards.append({
                "id": int(row.id),
                "title": str(p.get("title") or "Без названия"),
                "description": str(p.get("description") or ""),
                "city": str(p.get("city") or p.get("location") or ""),
                "category": str(p.get("category_path") or p.get("category") or ""),
                "images": urls,
                "status": str(row.status or ""),
            })
    _assert_local_review_media(cards)
    media_total = sum(len(card.get("images") or []) for card in cards)

    style = """*{box-sizing:border-box}body{margin:0;background:#f4f7fb;color:#111827;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{max-width:1280px;margin:auto;padding:28px 18px 64px}.hero{background:linear-gradient(135deg,#0f172a,#1d4ed8);color:#fff;padding:30px;border-radius:26px;box-shadow:0 18px 50px rgba(15,23,42,.18)}h1{font-size:clamp(30px,4vw,46px);margin:0 0 8px;font-weight:900}.sub{opacity:.9;font-size:17px;font-weight:650}.chips{display:flex;gap:9px;flex-wrap:wrap;margin-top:20px}.chip{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.24);padding:9px 12px;border-radius:999px;font-weight:850;font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px;margin-top:22px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:22px;padding:17px;box-shadow:0 9px 30px rgba(15,23,42,.065)}.head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}h2{font-size:22px;line-height:1.18;margin:5px 0 7px;font-weight:900}.num{font-size:12px;color:#64748b;font-weight:750}.geo{font-size:14px;color:#475569;font-weight:700}.ok{background:#ecfdf3;color:#067647;padding:7px 10px;border-radius:999px;font-size:12px;font-weight:900;white-space:nowrap}.gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:15px}.gallery img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:13px;background:#eef2f7}.gallery img:first-child{grid-column:span 2;grid-row:span 2}.facts{display:flex;flex-wrap:wrap;gap:7px;margin-top:13px}.facts span{font-size:12px;background:#f1f5f9;border-radius:9px;padding:7px 9px;color:#334155;font-weight:750}details{margin-top:13px;border-top:1px solid #eef2f7;padding-top:12px}summary{cursor:pointer;font-weight:850;color:#1d4ed8}.desc{font-size:14px;line-height:1.62;color:#334155;margin-top:11px;font-weight:560;white-space:pre-wrap}.note{margin-top:22px;background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:15px;font-size:13px;color:#475569;font-weight:700;overflow-wrap:anywhere;word-break:break-word}.technical{margin-top:9px}.technical summary{font-size:12px;color:#64748b}.technical code{display:block;margin-top:8px;font-size:11px;line-height:1.45;white-space:normal;overflow-wrap:anywhere;word-break:break-all}@media(max-width:520px){.wrap{padding:14px 10px 40px}.grid{grid-template-columns:1fr;min-width:0}.card{min-width:0}.hero{padding:22px}.gallery{grid-template-columns:repeat(2,1fr)}.gallery img:first-child{grid-column:span 2;grid-row:span 1;aspect-ratio:16/10}.facts span{max-width:100%;overflow-wrap:anywhere}}"""

    card_html = []
    for card in cards:
        imgs = "".join(
            f'<img src="{_esc(_browser_url(url))}" loading="lazy" alt="Фото объявления {card["id"]}">'
            for url in card["images"]
        )
        card_html.append(
            '<article class="card"><div class="head"><div>'
            f'<div class="num">Объявление #{card["id"]} · {len(card["images"])} фото</div>'
            f'<h2>{_esc(card["title"])}</h2><div class="geo">{_esc(card["city"])}</div>'
            '</div><span class="ok">✓ PASS</span></div>'
            f'<div class="gallery">{imgs}</div>'
            f'<div class="facts"><span>{_esc(card["category"])}</span></div>'
            '<details><summary>Показать полный текст объявления</summary>'
            f'<div class="desc">{_esc(card["description"])}</div></details></article>'
        )

    campaign_name = str(campaign.name or f"Кампания {campaign.id}")
    xml_sha = str(check.get("sha256") or "")
    activation = dict(ff.get("owner_activation") or {})
    activation_status = str(activation.get("status") or "").strip().lower()
    submitted = dict(activation.get("submitted_snapshot") or {})
    submitted_sha = str(submitted.get("sha256") or "").strip()
    publication_snapshot = bool(activation_status in {"processing","submitted","scheduled","scheduled_waiting","scheduled_pending_review","pending_review","partial","completed"} and submitted_sha and submitted_sha == xml_sha)
    status_chip = (f"✓ Отправленный снимок: {len(cards)} объявлений" if publication_snapshot else f"✓ {len(cards)}/{len(cards)} подготовлено")
    publication_chip = (f"Avito: {_esc(activation_status)}" if publication_snapshot else "⏸ Публикация только отдельной командой владельца")
    basis_note = ("Страница соответствует неизменяемому submitted snapshot, который был передан Avito." if publication_snapshot else "Страница создана из текущего ready-for-review состояния.")
    doc = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_esc(campaign_name)} · BORIS Feed Factory</title><style>{style}</style></head><body>'
        '<main class="wrap"><section class="hero">'
        f'<h1>{_esc(campaign_name)}</h1><div class="sub">Кампания {campaign.id} · результат BORIS Feed Factory</div>'
        '<div class="chips">'
        f'<span class="chip">{status_chip}</span>'
        '<span class="chip">✓ Avito XML check PASS</span>'
        f'<span class="chip">✓ {media_total} изображений</span>'
        f'<span class="chip">{publication_chip}</span>'
        '</div></section><section class="grid">' + "".join(card_html) + '</section>'
        f'<div class="note">{basis_note} Создание страницы само по себе не публикует объявления в Avito.'
        f'<details class="technical"><summary>Технические данные проверки</summary><code>XML SHA: {_esc(xml_sha)}</code></details></div>'
        '</main></body></html>'
    )

    os.makedirs(REVIEW_ROOT, exist_ok=True)
    filename = f"campaign_{int(campaign.id)}_review.html"
    path = os.path.join(REVIEW_ROOT, filename)
    fd, tmp = tempfile.mkstemp(prefix=f".campaign_{campaign.id}_review_", suffix=".html", dir=REVIEW_ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(doc)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=261)
        raise

    return {
        "url": f"https://boris-ai.pro/images/reviews/{filename}",
        "artifact": f"/images/reviews/{filename}",
        "items": len(cards),
        "media_placements": media_total,
        "official_xml_sha256": xml_sha or None,
        "page_sha256": hashlib.sha256(doc.encode("utf-8")).hexdigest(),
        "public": True,
        "no_auth": True,
        "external_upload": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "campaign_review_page_v2_snapshot_exact",
        "source_basis": "submitted_xml" if publication_snapshot else "current_ready_state",
    }
