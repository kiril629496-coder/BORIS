"""Ядро Медиатеки ЕЦО.

Права здесь настоящие, а не «роутер без _protected, значит можно всё»:
media_router подключён без общего гейта только потому, что медиатека
owner-level и охватывает несколько аккаунтов сразу, а check_account_access
требует одиночный account_id. Каждая ручка спрашивает разрешение у этого
модуля.

Классификация файлов при бэкфилле опирается на ДОКАЗАННОЕ происхождение
префиксов, ничего не выдумывая:
  up_*      -> real_photo   (загрузил клиент)
  gen_*     -> ai_generated (наши генерации)
  fullai_*  -> banner       (баннерная, banners.py)
  stock_*   -> stock        (search_stock_photos; лицензия неизвестна -> null)
  product_* -> real_photo / site_parser (parser.py:1305, фото с сайта клиента)
  uq_*      -> не импортируется (технические копии, их чистит cleanup_images)
  shot_/report_/ux_/qa_/landing_/ab_ -> не импортируется (QA и служебное)
"""
import json
import re
import os
from datetime import datetime, timezone

from sqlalchemy import text as _t, func

from app.models.media_asset import MediaAsset
from app.models.media_folder import MediaFolder
from app.models.media_link import MediaLink
from app.services.exception_observability import observe_suppressed

# Словари состояний: файлов моделей несколько, а источник истины должен быть один.
MEDIA_TYPES = ("real_photo", "ai_generated", "render", "stock", "banner",
               "drawing", "diagram", "document_preview", "other")
MEDIA_STATUSES = ("draft", "review", "approved", "rejected", "archived")
FILE_STATES = ("present", "missing")
LINK_ROLES = ("hero", "gallery", "reference", "source")
LINK_ENTITIES = ("product", "variant", "campaign_item", "banner", "photo_shot",
                 "post", "folder_cover")
from app.services import media_storage as st
from app.services.action_log import ACTOR_USER, log_action

SKIP_PREFIXES = ("uq_", "shot_", "report_", "ux_", "qa_", "landing_", "ab_",
                 "trimmed_")
TYPE_BY_PREFIX = (
    ("up_", "real_photo", "upload"),
    ("uploaded_", "real_photo", "upload"),
    ("gen_", "ai_generated", "ai"),
    ("fullai_", "banner", "banner"),
    ("carousel_", "banner", "banner"),
    ("showcase_", "banner", "banner"),
    ("infographic_", "banner", "banner"),
    ("stock_", "stock", "stock"),
    ("product_", "real_photo", "site_parser"),
    ("proj_", "ai_generated", "ai"),
)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def classify(filename: str):
    """(media_type, source_type) или (None, None), если файл не импортируется."""
    name = os.path.basename(filename or "")
    low = name.lower()
    if not low.endswith(IMAGE_EXT):
        return None, None
    for p in SKIP_PREFIXES:
        if low.startswith(p):
            return None, None
    for prefix, mtype, stype in TYPE_BY_PREFIX:
        if low.startswith(prefix):
            return mtype, stype
    return "other", "legacy_fs"


def _role(user) -> str:
    if user is None:
        return ""
    return (getattr(user, "role", None) or "") if not isinstance(user, dict) \
        else (user.get("role") or "")


def _uid(user):
    if user is None:
        return None
    return getattr(user, "id", None) if not isinstance(user, dict) else user.get("id")


def allowed_accounts(db, user):
    """Аккаунты, которые пользователь имеет право видеть. owner — все."""
    if _role(user) == "owner":
        rows = db.execute(_t("SELECT account_id FROM accounts")).fetchall()
        return {r[0] for r in rows}
    uid = _uid(user)
    if uid is None:
        return set()
    rows = db.execute(_t(
        "SELECT account_id FROM accounts WHERE owner_user_id = :u"
        " UNION SELECT account_id FROM user_account_access"
        " WHERE user_id = :u AND can_view = TRUE"), {"u": uid}).fetchall()
    return {r[0] for r in rows}


def manage_accounts(db, user):
    """Аккаунты, где пользователь вправе менять материалы."""
    if _role(user) == "owner":
        return allowed_accounts(db, user)
    uid = _uid(user)
    if uid is None:
        return set()
    rows = db.execute(_t(
        "SELECT account_id FROM accounts WHERE owner_user_id = :u"
        " UNION SELECT account_id FROM user_account_access"
        " WHERE user_id = :u AND can_manage_ads = TRUE"), {"u": uid}).fetchall()
    return {r[0] for r in rows}


def owner_scopes(db, user):
    """Владельцы бизнесов, чьи материалы доступны пользователю."""
    accs = allowed_accounts(db, user)
    if not accs:
        return set()
    rows = db.execute(_t(
        "SELECT DISTINCT owner_user_id FROM accounts"
        " WHERE account_id = ANY(:a) AND owner_user_id IS NOT NULL"),
        {"a": list(accs)}).fetchall()
    scopes = {r[0] for r in rows}
    uid = _uid(user)
    if uid is not None:
        scopes.add(uid)
    return scopes


def owner_of_account(db, account_id: str):
    """Владелец бизнеса по аккаунту. None — значит fail-closed."""
    if not account_id:
        return None
    row = db.execute(_t(
        "SELECT owner_user_id FROM accounts WHERE account_id = :a"),
        {"a": account_id}).fetchone()
    return row[0] if row and row[0] is not None else None


def can_read_asset(db, user, asset) -> bool:
    if asset is None:
        return False
    if _role(user) == "owner":
        return True
    if asset.owner_user_id is not None and asset.owner_user_id == _uid(user):
        return True
    return bool(asset.account_id) and asset.account_id in allowed_accounts(db, user)


def can_write_asset(db, user, asset) -> bool:
    if asset is None:
        return False
    if _role(user) == "owner":
        return True
    if asset.owner_user_id is not None and asset.owner_user_id == _uid(user):
        return True
    return bool(asset.account_id) and asset.account_id in manage_accounts(db, user)


def folder_tree(db, user, account_id=None):
    scopes = owner_scopes(db, user)
    if not scopes:
        return []
    q = db.query(MediaFolder).filter(
        MediaFolder.owner_user_id.in_(list(scopes)),
        MediaFolder.deleted_at.is_(None))
    if account_id:
        q = q.filter(MediaFolder.account_id == account_id)
    folders = q.order_by(MediaFolder.path).all()
    counts = {}
    if folders:
        rows = db.execute(_t(
            "SELECT folder_id, count(*) FROM media_assets"
            " WHERE folder_id = ANY(:f) AND deleted_at IS NULL"
            " AND account_id = :a GROUP BY folder_id"),
            {"f": [f.id for f in folders], "a": account_id}).fetchall()
        counts = {r[0]: r[1] for r in rows}
    return [{"id": f.id, "name": f.name, "path": f.path,
             "parent_id": f.parent_id, "account_id": f.account_id,
             "kind": f.kind, "count": counts.get(f.id, 0)} for f in folders]


def ensure_folder(db, owner_user_id, account_id, name, parent_id=None,
                  created_by=None, kind="user"):
    """Идемпотентно: та же папка у того же родителя не создаётся дважды."""
    name = (name or "").strip() or "Без названия"
    parent = db.query(MediaFolder).get(parent_id) if parent_id else None
    if parent is not None and (parent.owner_user_id != owner_user_id or parent.account_id != account_id):
        raise ValueError("parent folder belongs to another account")
    base = parent.path if parent else ""
    path = "%s/%s" % (base, name)
    row = db.query(MediaFolder).filter(
        MediaFolder.owner_user_id == owner_user_id,
        MediaFolder.account_id == account_id,
        MediaFolder.path == path,
        MediaFolder.deleted_at.is_(None)).first()
    if row:
        return row
    row = MediaFolder(owner_user_id=owner_user_id, account_id=account_id,
                      parent_id=parent_id, name=name, path=path, kind=kind)
    db.add(row)
    db.flush()
    return row


def rename_folder(db, folder, new_name):
    old_path = folder.path
    parent = db.query(MediaFolder).get(folder.parent_id) if folder.parent_id else None
    folder.name = (new_name or "").strip() or folder.name
    folder.path = "%s/%s" % (parent.path if parent else "", folder.name)
    db.execute(_t(
        "UPDATE media_folders SET path = :new || substring(path from :cut)"
        " WHERE owner_user_id = :o AND account_id = :a"
        " AND path LIKE :like AND id <> :id"),
        {"new": folder.path, "cut": len(old_path) + 1,
         "o": folder.owner_user_id, "a": folder.account_id,
         "like": old_path + "/%", "id": folder.id})
    return folder


def asset_public(a, with_urls=True):
    """DTO для клиента. Битые ссылки не отдаём: missing -> url = None."""
    present = a.file_state == "present"
    return {
        "id": a.id, "filename": a.filename, "media_type": a.media_type,
        "status": a.status, "file_state": a.file_state,
        "account_id": a.account_id, "folder_id": a.folder_id,
        "width": a.width, "height": a.height, "file_size": a.file_size,
        "is_favorite": bool(a.is_favorite),
        "source_type": a.source_type, "source_provider": a.source_provider,
        "tags": list(a.tags or []),
        "derived_from_id": a.derived_from_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "thumb_url": st.public_url(a.thumb_key) if (with_urls and present) else None,
        "preview_url": st.public_url(a.preview_key) if (with_urls and present) else None,
        "original_url": st.public_url(a.storage_key) if (with_urls and present) else None,
        "unavailable_reason": None if present else
        "Файл недоступен в хранилище — возможно, исходник удалён старым модулем",
    }


LEGACY_AVITO_VISUAL_PROVIDERS = {"boris_deterministic", "boris_unique"}
INVALID_PROVENANCE_PROVIDERS = {"owner_approved", "user_approved", "approved"}
LEGACY_AVITO_VISUAL_SOURCE_REFS = {"campaign_openai_gpt_image_2"}
LEGACY_AVITO_TIMESTAMP_BANNER_RE = re.compile(r"(?:^|/)banners/campaign_openai/campaign_\d+_item_\d+_\d{10,}\.png$")


def _assert_active_visual_registration_allowed(db, account_id, storage_key, source_provider=None, source_ref=None, sha256=None):
    provider = str(source_provider or "").strip().lower()
    ref = str(source_ref or "").strip()
    key = str(storage_key or "").strip()
    digest = str(sha256 or "").strip().lower()
    if provider in INVALID_PROVENANCE_PROVIDERS:
        raise RuntimeError(f"approval state cannot be used as media provenance: {provider}")
    if provider in LEGACY_AVITO_VISUAL_PROVIDERS:
        raise RuntimeError(f"legacy visual provider is forbidden: {provider}")
    if ref in LEGACY_AVITO_VISUAL_SOURCE_REFS:
        raise RuntimeError(f"legacy Avito text-in-image source is forbidden: {ref}")
    if any(marker in key.lower().rsplit("/", 1)[-1] for marker in ("fullai_", "gptimg_")):
        raise RuntimeError("legacy AI-visible-text banner filename is forbidden")
    if LEGACY_AVITO_TIMESTAMP_BANNER_RE.search(key):
        raise RuntimeError("legacy timestamp Avito banner path is forbidden")
    historical = db.query(MediaAsset.id).filter(
        MediaAsset.account_id == account_id,
        MediaAsset.storage_key == key,
        ((func.lower(func.coalesce(MediaAsset.source_provider, "")).in_(tuple(LEGACY_AVITO_VISUAL_PROVIDERS))) |
         (MediaAsset.source_ref.in_(tuple(LEGACY_AVITO_VISUAL_SOURCE_REFS))))
    ).first()
    if historical:
        raise RuntimeError("source-laundered legacy Avito visual is forbidden")
    # A different path/provider label must never resurrect the exact same bytes
    # after the visual was quarantined. Storage-path checks alone allowed a rejected
    # campaign image to reappear as a new `owner_approved` copy with identical SHA.
    if digest:
        byte_history = db.query(MediaAsset.id).filter(
            MediaAsset.account_id == account_id,
            MediaAsset.sha256 == digest,
            ((func.lower(func.coalesce(MediaAsset.source_provider, "")).in_(tuple(LEGACY_AVITO_VISUAL_PROVIDERS))) |
             (MediaAsset.source_ref.in_(tuple(LEGACY_AVITO_VISUAL_SOURCE_REFS))) |
             (MediaAsset.error_text.ilike("%source-laundered%")))
        ).first()
        if byte_history:
            raise RuntimeError("source-laundered Avito visual bytes are quarantined")



def list_assets(db, user, account_id=None, folder_id=None, media_type=None,
                source=None, status=None, usage=None, q=None, favorite=None,
                trashed=False, limit=60, offset=0, sort="new"):
    """Серверные фильтры и пагинация. В память библиотеку не тянем.

    Legacy deterministic/uniqueness renderers are permanently hidden from the active
    library because they are forbidden for Avito publication by the global quality gate.
    They remain visible only in trash/audit mode.
    """
    scopes = owner_scopes(db, user)
    if not scopes:
        return {"items": [], "total": 0}
    limit = max(1, min(int(limit or 60), 200))
    offset = max(0, int(offset or 0))

    query = db.query(MediaAsset).filter(MediaAsset.owner_user_id.in_(list(scopes)))
    if trashed:
        query = query.filter(MediaAsset.deleted_at.isnot(None))
    else:
        query = query.filter(MediaAsset.deleted_at.is_(None))
        query = query.filter(~func.lower(func.coalesce(MediaAsset.source_provider, "")).in_(tuple(LEGACY_AVITO_VISUAL_PROVIDERS)))
        query = query.filter((MediaAsset.source_ref.is_(None)) | (~MediaAsset.source_ref.in_(tuple(LEGACY_AVITO_VISUAL_SOURCE_REFS))))
        query = query.filter(~MediaAsset.storage_key.op("~")(r"banners/campaign_openai/campaign_[0-9]+_item_[0-9]+_[0-9]{10,}\.png$"))
        query = query.filter(_t(
            "NOT EXISTS (SELECT 1 FROM media_assets h WHERE h.account_id=media_assets.account_id "
            "AND h.storage_key=media_assets.storage_key AND h.id<>media_assets.id "
            "AND (lower(coalesce(h.source_provider,'')) IN ('boris_deterministic','boris_unique') "
            "OR coalesce(h.source_ref,'')='campaign_openai_gpt_image_2'))"))
    if account_id:
        if account_id not in allowed_accounts(db, user):
            return {"items": [], "total": 0}
        query = query.filter(MediaAsset.account_id == account_id)
    if folder_id:
        query = query.filter(MediaAsset.folder_id == int(folder_id))
    if media_type and media_type != "all":
        types = [t for t in str(media_type).split(",") if t]
        query = query.filter(MediaAsset.media_type.in_(types))
    if source == "mine":
        query = query.filter(MediaAsset.media_type != "stock")
    elif source == "stock":
        query = query.filter(MediaAsset.media_type == "stock")
    if status and status != "all":
        query = query.filter(MediaAsset.status == status)
    if favorite:
        query = query.filter(MediaAsset.is_favorite.is_(True))
    if q:
        like = "%" + str(q).strip().lower() + "%"
        query = query.filter(_t("lower(media_assets.filename) LIKE :q"
                                " OR lower(coalesce(media_assets.tags::text,'')) LIKE :q")
                             .bindparams(q=like))
    if usage == "used":
        query = query.filter(_t(
            "EXISTS (SELECT 1 FROM media_links l WHERE l.media_id = media_assets.id)"))
    elif usage == "unused":
        query = query.filter(_t(
            "NOT EXISTS (SELECT 1 FROM media_links l WHERE l.media_id = media_assets.id)"))

    total = query.count()
    order = MediaAsset.id.asc() if sort == "old" else MediaAsset.id.desc()
    rows = query.order_by(order).limit(limit).offset(offset).all()
    return {"items": [asset_public(a) for a in rows], "total": total}


def register_asset(db, owner_user_id, account_id, storage_key, filename,
                   media_type="other", source_type="legacy_fs", folder_id=None,
                   sha256=None, file_size=None, created_by=None,
                   source_provider=None, source_ref=None, license_json=None,
                   derived_from_id=None, status="approved"):
    """Регистрация файла. Повтор по storage_key возвращает существующую запись."""
    # Idempotent reuse of an already-active canonical row is always safe: policy
    # checks apply only when somebody is trying to create a NEW active identity.
    row = db.query(MediaAsset).filter(MediaAsset.storage_key == storage_key, MediaAsset.deleted_at.is_(None)).first()
    if row:
        return row, False
    _assert_active_visual_registration_allowed(db, account_id, storage_key, source_provider, source_ref, sha256)
    row = MediaAsset(
        owner_user_id=owner_user_id, account_id=account_id,
        storage_key=storage_key, filename=filename, original_filename=filename,
        media_type=media_type, source_type=source_type, folder_id=folder_id,
        sha256=sha256, file_size=file_size, created_by=created_by,
        source_provider=source_provider, source_ref=source_ref,
        license_json=license_json, derived_from_id=derived_from_id,
        status=status, file_state="present")
    db.add(row)
    db.flush()
    return row, True


def find_duplicate(db, owner_user_id, sha256):
    """Подсказка для загрузки: файл с тем же содержимым уже есть."""
    if not sha256:
        return None
    return db.query(MediaAsset).filter(
        MediaAsset.owner_user_id == owner_user_id,
        MediaAsset.sha256 == sha256,
        MediaAsset.deleted_at.is_(None)).first()


def link(db, asset, entity_type, entity_id, role="gallery", position=0,
         created_by=None):
    provider = str(getattr(asset, "source_provider", "") or "").strip().lower()
    source_ref = str(getattr(asset, "source_ref", "") or "").strip()
    if provider in LEGACY_AVITO_VISUAL_PROVIDERS:
        raise RuntimeError(f"legacy visual provider is forbidden for active media links: {provider}")
    if source_ref in LEGACY_AVITO_VISUAL_SOURCE_REFS:
        raise RuntimeError(f"legacy Avito text-in-image source is forbidden for active media links: {source_ref}")
    if getattr(asset, "deleted_at", None) is not None:
        raise RuntimeError("quarantined/deleted media cannot be linked")
    _assert_active_visual_registration_allowed(
        db, getattr(asset, "account_id", None), getattr(asset, "storage_key", ""),
        getattr(asset, "source_provider", None), getattr(asset, "source_ref", None),
        getattr(asset, "sha256", None),
    )
    entity_id = str(entity_id)
    row = db.query(MediaLink).filter(
        MediaLink.media_id == asset.id, MediaLink.entity_type == entity_type,
        MediaLink.entity_id == entity_id, MediaLink.role == role).first()
    if row:
        row.position = position
        return row, False
    if role == "hero":
        db.query(MediaLink).filter(
            MediaLink.entity_type == entity_type,
            MediaLink.entity_id == entity_id,
            MediaLink.role == "hero").delete(synchronize_session=False)
        # Ensure the DELETE reaches PostgreSQL before an INSERT that may reuse
        # the same unique (media, entity, id, role) key.
        db.flush()
    row = MediaLink(media_id=asset.id, entity_type=entity_type,
                    entity_id=entity_id, role=role, position=position,
                    created_by=created_by)
    db.add(row)
    db.flush()
    return row, True


def usages(db, media_id):
    """«Используется в» — человеческий ответ, а не список id."""
    rows = db.query(MediaLink).filter(MediaLink.media_id == media_id).all()
    out = []
    titles = {"campaign_item": "Объявление", "product": "Товар",
              "variant": "Вариант товара", "banner": "Баннер",
              "photo_shot": "Кадр фотосессии", "post": "Пост",
              "folder_cover": "Обложка папки"}
    for r in rows:
        name = ""
        if r.entity_type == "campaign_item":
            row = db.execute(_t(
                "SELECT payload_json, account_id FROM campaign_items WHERE id = :i"),
                {"i": int(r.entity_id) if str(r.entity_id).isdigit() else -1}).fetchone()
            if row:
                try:
                    name = (json.loads(row[0] or "{}") or {}).get("title") or ""
                except Exception:
                    name = ""
                name = name or ("объявление #" + str(r.entity_id))
        elif r.entity_type == "banner":
            row = db.execute(_t(
                "SELECT theme_label, filename FROM banners WHERE id = :i"),
                {"i": int(r.entity_id) if str(r.entity_id).isdigit() else -1}).fetchone()
            if row:
                name = row[0] or row[1] or ""
        out.append({"entity_type": r.entity_type, "entity_id": r.entity_id,
                    "role": r.role, "title": titles.get(r.entity_type, r.entity_type),
                    "name": name or ("#" + str(r.entity_id))})
    return out


def soft_delete(db, asset, user):
    asset.deleted_at = datetime.now(timezone.utc)
    log_action(account_id=asset.account_id or "", action="Удалил файл в корзину",
               object_kind="media", object_name=asset.filename,
               actor=ACTOR_USER, user_id=_uid(user), source="media.delete")
    return asset


def restore(db, asset, user):
    # Restore is another activation path: never resurrect quarantined legacy bytes.
    _assert_active_visual_registration_allowed(
        db, getattr(asset, "account_id", None), getattr(asset, "storage_key", ""),
        getattr(asset, "source_provider", None), getattr(asset, "source_ref", None),
        getattr(asset, "sha256", None),
    )
    asset.deleted_at = None
    log_action(account_id=asset.account_id or "", action="Восстановил файл из корзины",
               object_kind="media", object_name=asset.filename,
               actor=ACTOR_USER, user_id=_uid(user), source="media.restore")
    return asset



def _published_campaign_media_local_reconcile(db, limit=1000, account_ids=None):
    """PUBLISHED_CAMPAIGN_MEDIA_SELF_HEAL_V1.

    Repair published CampaignItem media integrity using only local durable data.
    No network/provider/paid AI calls happen here.

    Safe local repairs:
      * payload.images is authoritative for exact publication placement;
      * stale MediaLinks are rebuilt only when every referenced local asset exists;
      * same-fingerprint positive evidence is remapped by media id after reorder;
      * stale-fingerprint evidence is reused only when its visible subject clearly
        matches the current semantic family.

    Anything still requiring visual judgement is returned in needs_paid for the
    bounded second stage.
    """
    from collections import Counter
    from app.models.campaign_item import CampaignItem
    from app.models.media_link import MediaLink
    from app.services.campaign_service import content_hash
    from app.services.media_semantic_validator import (
        SEMANTIC_POLICY_VERSION,
        _asset_family_cache,
        _clear_subject_family_match,
        _payload_local_image_keys,
        _semantic_family,
        semantic_content_fingerprint,
        validate_linked_media_semantics,
    )

    q = db.query(CampaignItem).filter(CampaignItem.status == "published")
    if account_ids:
        q = q.filter(CampaignItem.account_id.in_(list(account_ids)))
    rows = q.order_by(CampaignItem.updated_at.desc(), CampaignItem.id.desc()).limit(
        max(1, min(int(limit or 1000), 5000))
    ).all()

    checked = 0
    repaired = 0
    relinked = 0
    evidence_reused = 0
    needs_paid = []
    blocked = []
    before_codes = Counter()
    after_codes = Counter()

    locally_repairable_codes = {
        "media_semantic_link_payload_drift",
        "media_semantic_proof_stale",
        "media_semantic_proof_missing",
        "media_banner_hero_drift",
    }

    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        errors_before = list(validate_linked_media_semantics(db, row, payload) or [])
        if not errors_before:
            continue

        checked += 1
        codes = {str(x.get("code") or "") for x in errors_before}
        for code in codes:
            before_codes[code] += 1

        # PUBLISHED_BANNER_PLACEMENT_REVIEW_RECONCILE_V1:
        # A later QA-approved banner can legitimately expand an already-reviewed
        # gallery by exactly one placement. This is safe to reconcile locally only
        # when the current payload and durable MediaLinks are byte-for-byte aligned,
        # every linked asset is present, and the added first placement is exactly
        # the approved banner. A real shrink/loss (or any ambiguous layout) remains
        # fail-closed and is never "fixed" by changing the expected count.
        if codes == {"media_semantic_placement_count_drift"}:
            drift = next((x for x in errors_before if str(x.get("code") or "") == "media_semantic_placement_count_drift"), {})
            try:
                expected_placements = int(drift.get("expected_placements") or 0)
            except Exception:
                expected_placements = 0
            images = payload.get("images") or []
            image_count = len(images) if isinstance(images, list) else 0
            payload_keys = _payload_local_image_keys(payload)
            banner_url = str(payload.get("banner_url") or "").strip()
            banner_qa = payload.get("banner_qa") or {}
            review = payload.get("media_semantic_review") or {}
            banner_key = str(_payload_local_image_keys({"images": [banner_url]}).get(0) or "") if banner_url else ""
            linked_for_layout = (
                db.query(MediaLink, MediaAsset)
                .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
                .filter(
                    MediaLink.entity_type == "campaign_item",
                    MediaLink.entity_id == str(row.id),
                    MediaAsset.deleted_at.is_(None),
                    MediaAsset.file_state == "present",
                )
                .order_by(MediaLink.position.asc(), MediaLink.id.asc())
                .all()
            )
            current_keys = [
                (int(link.position), str(asset.storage_key or "").lstrip("/"))
                for link, asset in linked_for_layout
            ]
            wanted_keys = [(pos, str(payload_keys.get(pos) or "")) for pos in range(image_count)]
            review_passed = bool(
                isinstance(review, dict)
                and (
                    review.get("passed") is True
                    or str(review.get("status") or "").lower() in {"pass","passed"}
                )
            )
            safe_banner_expansion = bool(
                expected_placements > 0
                and image_count == expected_placements + 1
                and len(payload_keys) == image_count
                and len(linked_for_layout) == image_count
                and current_keys == wanted_keys
                and review_passed
                and isinstance(banner_qa, dict) and banner_qa.get("passed") is True
                and banner_key
                and wanted_keys and wanted_keys[0][1] == banner_key
            )
            if safe_banner_expansion:
                review = dict(review)
                review["placements"] = image_count
                review["placement_count_reconcile"] = {
                    "policy_version": "PUBLISHED_BANNER_PLACEMENT_REVIEW_RECONCILE_V1",
                    "from_placements": expected_placements,
                    "to_placements": image_count,
                    "reason": "qa_approved_banner_added_as_exact_first_placement",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "owner_action_required": False,
                }
                payload["media_semantic_review"] = review
                row.payload_json = json.dumps(payload, ensure_ascii=False)
                row.content_hash = content_hash(payload)
                db.flush()
                errors_before = list(validate_linked_media_semantics(db, row, payload) or [])
                codes = {str(x.get("code") or "") for x in errors_before}
                if not errors_before:
                    repaired += 1
                    continue

        if not codes or not codes.issubset(locally_repairable_codes):
            blocked.append({
                "account_id": str(row.account_id or ""),
                "campaign_id": int(row.campaign_id or 0),
                "item_id": int(row.id),
                "codes": sorted(codes),
                "reason": "non_local_semantic_error",
            })
            continue

        # BANNER_HERO_LOCAL_SELF_HEAL_V1: preserve the current gallery cardinality
        # while restoring an already QA-approved existing banner as image #1.
        # This replaces the drifted pre-banner hero and never buys/regenerates an
        # image. The normal local asset-resolution block below proves the banner
        # exists before anything is persisted.
        if "media_banner_hero_drift" in codes:
            _banner_url = str(payload.get("banner_url") or "").strip()
            _images = [str(x) for x in (payload.get("images") or []) if str(x).strip()]
            if _banner_url and bool((payload.get("banner_qa") or {}).get("passed")):
                _without_banner = [x for x in _images if x != _banner_url]
                _target_count = max(1, len(_images))
                payload["images"] = [_banner_url] + _without_banner[:max(0, _target_count - 1)]
                payload["media_banner_hero_self_heal"] = {
                    "policy_version": "BANNER_HERO_LOCAL_SELF_HEAL_V1",
                    "status": "planned_existing_asset_restore",
                    "owner_action_required": False,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }

        payload_keys = _payload_local_image_keys(payload)
        image_count = len(list(payload.get("images") or []))
        if image_count <= 0 or len(payload_keys) != image_count:
            blocked.append({
                "account_id": str(row.account_id or ""),
                "campaign_id": int(row.campaign_id or 0),
                "item_id": int(row.id),
                "codes": sorted(codes),
                "reason": "payload_images_not_fully_local",
            })
            continue

        linked = (
            db.query(MediaLink, MediaAsset)
            .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
            .filter(
                MediaLink.entity_type == "campaign_item",
                MediaLink.entity_id == str(row.id),
                MediaAsset.deleted_at.is_(None),
            )
            .order_by(MediaLink.position.asc(), MediaLink.id.asc())
            .all()
        )
        current_keys = [
            (int(link.position), str(asset.storage_key or "").lstrip("/"))
            for link, asset in linked
        ]
        wanted_keys = [(pos, str(payload_keys[pos])) for pos in range(image_count)]
        did_relink = current_keys != wanted_keys

        if did_relink:
            desired_assets = []
            unresolved = []
            for pos, key in wanted_keys:
                asset = (
                    db.query(MediaAsset)
                    .filter(
                        MediaAsset.account_id == str(row.account_id or ""),
                        MediaAsset.storage_key == key,
                        MediaAsset.deleted_at.is_(None),
                        MediaAsset.file_state == "present",
                    )
                    .order_by(MediaAsset.id.desc())
                    .first()
                )
                if asset is None:
                    unresolved.append({"position": pos, "storage_key": key})
                else:
                    desired_assets.append(asset)

            if unresolved or len(desired_assets) != image_count:
                blocked.append({
                    "account_id": str(row.account_id or ""),
                    "campaign_id": int(row.campaign_id or 0),
                    "item_id": int(row.id),
                    "codes": sorted(codes),
                    "reason": "payload_media_asset_unresolved",
                    "unresolved": unresolved[:20],
                })
                continue

            desired_ids = [int(asset.id) for asset in desired_assets]
            if len(desired_ids) != len(set(desired_ids)):
                blocked.append({
                    "account_id": str(row.account_id or ""),
                    "campaign_id": int(row.campaign_id or 0),
                    "item_id": int(row.id),
                    "codes": sorted(codes),
                    "reason": "duplicate_media_asset_in_payload",
                })
                continue

            db.query(MediaLink).filter(
                MediaLink.entity_type == "campaign_item",
                MediaLink.entity_id == str(row.id),
            ).delete(synchronize_session=False)
            db.flush()
            for pos, asset in enumerate(desired_assets):
                db.add(MediaLink(
                    media_id=int(asset.id),
                    entity_type="campaign_item",
                    entity_id=str(row.id),
                    role=("hero" if int(pos) == 0 else "gallery"),
                    position=int(pos),
                ))
            row.hero_media_id = int(desired_assets[0].id) if desired_assets else None
            db.flush()
            relinked += 1

        linked = (
            db.query(MediaLink, MediaAsset)
            .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
            .filter(
                MediaLink.entity_type == "campaign_item",
                MediaLink.entity_id == str(row.id),
                MediaAsset.deleted_at.is_(None),
            )
            .order_by(MediaLink.position.asc(), MediaLink.id.asc())
            .all()
        )

        fp = semantic_content_fingerprint(payload)
        family = _semantic_family(payload)
        existing = [
            x for x in (payload.get("media_semantic_evidence") or [])
            if isinstance(x, dict)
        ]
        by_media = {}
        for ev in existing:
            try:
                mid = int(ev.get("media_id") or 0)
            except Exception:
                mid = 0
            if mid:
                by_media.setdefault(mid, []).append(ev)

        fresh_evidence = []
        reused_here = 0
        for link, asset in linked:
            candidates = list(by_media.get(int(asset.id), []))
            chosen = None

            for ev in candidates:
                try:
                    conf = float(ev.get("confidence") or 0)
                except Exception:
                    conf = 0.0
                if (
                    str(ev.get("content_fingerprint") or "") == fp
                    and bool(ev.get("match"))
                    and conf >= 0.80
                ):
                    chosen = dict(ev)
                    break

            if chosen is None and family:
                for ev in candidates:
                    try:
                        conf = float(ev.get("confidence") or 0)
                    except Exception:
                        conf = 0.0
                    subject = str(ev.get("subject") or "")
                    if (
                        bool(ev.get("match"))
                        and conf >= 0.80
                        and _clear_subject_family_match(payload, subject)
                    ):
                        chosen = dict(ev)
                        chosen["checker"] = "post_publish_family_reuse"
                        chosen["policy_version"] = SEMANTIC_POLICY_VERSION
                        chosen["reason"] = (
                            str(chosen.get("reason") or "")
                            + "; existing visible subject still matches current semantic family"
                        )[:320]
                        break

            # PUBLISHED_MEDIA_ASSET_FAMILY_CACHE_REUSE_V1: legacy and current
            # asset-level positive Vision proofs are durable evidence too. Reuse
            # them only for the exact same semantic family at confidence >= 0.80.
            # This is especially important after an item text revision or banner
            # insertion: the gallery must not be repurchased from Vision when the
            # same concrete media asset was already independently reviewed.
            if chosen is None and family:
                cached_family, cached_conf, cached_subject = _asset_family_cache(asset)
                if cached_family == family and float(cached_conf or 0) >= 0.80:
                    chosen = {
                        "match": True,
                        "confidence": float(cached_conf),
                        "subject": str(cached_subject or "")[:240],
                        "reason": "reused durable positive Vision proof for the same semantic family",
                        "checker": "openai_vision_family_cache",
                        "policy_version": SEMANTIC_POLICY_VERSION,
                    }

            if chosen is not None:
                chosen["media_id"] = int(asset.id)
                chosen["position"] = int(link.position)
                chosen["content_fingerprint"] = fp
                chosen["checked_at"] = datetime.now(timezone.utc).isoformat()
                fresh_evidence.append(chosen)
                reused_here += 1

        payload["media_semantic_evidence"] = fresh_evidence
        payload["media_link_reconcile"] = {
            "policy_version": "PUBLISHED_CAMPAIGN_MEDIA_SELF_HEAL_V1",
            "status": "relinked" if did_relink else "links_already_current",
            "placements": len(linked),
            "reused_evidence": reused_here,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "owner_action_required": False,
        }
        row.payload_json = json.dumps(payload, ensure_ascii=False)
        row.content_hash = content_hash(payload)
        db.flush()

        errors_after = list(validate_linked_media_semantics(db, row, payload) or [])
        if not errors_after:
            _banner_heal = payload.get("media_banner_hero_self_heal") or {}
            if isinstance(_banner_heal, dict) and _banner_heal.get("status") == "planned_existing_asset_restore":
                _banner_heal["status"] = "restored_existing_asset"
                _banner_heal["verified_at"] = datetime.now(timezone.utc).isoformat()
                payload["media_banner_hero_self_heal"] = _banner_heal
                row.payload_json = json.dumps(payload, ensure_ascii=False)
                row.content_hash = content_hash(payload)
                db.flush()
            repaired += 1
            evidence_reused += reused_here
            continue

        codes_after = {str(x.get("code") or "") for x in errors_after}
        for code in codes_after:
            after_codes[code] += 1
        if codes_after and codes_after.issubset({
            "media_semantic_proof_missing",
            "media_semantic_proof_stale",
        }):
            needs_paid.append({
                "account_id": str(row.account_id or ""),
                "campaign_id": int(row.campaign_id or 0),
                "item_id": int(row.id),
                "content_fingerprint": fp,
                "codes": sorted(codes_after),
            })
        else:
            blocked.append({
                "account_id": str(row.account_id or ""),
                "campaign_id": int(row.campaign_id or 0),
                "item_id": int(row.id),
                "codes": sorted(codes_after),
                "reason": "local_reconcile_incomplete",
            })

    return {
        "policy_version": "PUBLISHED_CAMPAIGN_MEDIA_SELF_HEAL_V1",
        "scanned_published": len(rows),
        "checked_error_items": checked,
        "repaired_local": repaired,
        "relinked_items": relinked,
        "evidence_reused": evidence_reused,
        "needs_paid": needs_paid,
        "blocked": blocked,
        "before_codes": dict(before_codes),
        "after_codes": dict(after_codes),
        "owner_action_required": False,
    }



def _published_media_provider_cooldown_read(db, account_id: str) -> dict:
    """PUBLISHED_MEDIA_PROVIDER_COOLDOWN_V1: durable self-heal backoff."""
    from app.models.storage import Storage
    row = (
        db.query(Storage)
        .filter(
            Storage.account_id == str(account_id or ""),
            Storage.key == "published_media_provider_cooldown",
        )
        .order_by(Storage.id.desc())
        .first()
    )
    if not row:
        return {"active": False, "remaining_seconds": 0}
    try:
        data = json.loads(row.value or "{}")
    except Exception:
        data = {}
    raw_until = str(data.get("until") or "")
    try:
        until = datetime.fromisoformat(raw_until.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        remaining = max(0, int((until.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        remaining = 0
    return {**data, "active": bool(remaining > 0), "remaining_seconds": remaining}


def _published_media_provider_cooldown_set(db, account_id: str, seconds: int,
                                           reason: str) -> dict:
    from app.models.storage import Storage
    seconds = max(60, min(int(seconds or 0), 21600))
    now = datetime.now(timezone.utc)
    payload = {
        "policy_version": "PUBLISHED_MEDIA_PROVIDER_COOLDOWN_V1",
        "reason": str(reason or "provider_backoff")[:160],
        "seconds": seconds,
        "set_at": now.isoformat(),
        "until": (now + timedelta(seconds=seconds)).isoformat(),
        "owner_action_required": False,
    }
    row = (
        db.query(Storage)
        .filter(
            Storage.account_id == str(account_id or ""),
            Storage.key == "published_media_provider_cooldown",
        )
        .order_by(Storage.id.desc())
        .first()
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    if row:
        row.value = encoded
    else:
        db.add(Storage(
            account_id=str(account_id or ""),
            key="published_media_provider_cooldown",
            value=encoded,
        ))
    db.commit()
    return payload


def _published_media_provider_cooldown_clear(db, account_id: str) -> None:
    from app.models.storage import Storage
    rows = db.query(Storage).filter(
        Storage.account_id == str(account_id or ""),
        Storage.key == "published_media_provider_cooldown",
    ).all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()



def reconcile_published_campaign_media(db, limit=1000, paid_limit=None, account_ids=None):
    """Run local repair, then bounded exactly-once paid proof refresh if needed.

    account_ids optionally narrows both local repair and paid provider calls to an
    explicit tenant set. This is required for owner-directed Campaign repair so
    one client's acceptance cannot consume another client's vision budget.
    """
    import os
    from app.models.campaign_item import CampaignItem
    from app.models.media_link import MediaLink
    from app.services.campaign_service import content_hash
    from app.services.media_semantic_validator import (
        classify_item_media_vision,
        semantic_content_fingerprint,
        stamp_item_semantic_evidence,
        validate_linked_media_semantics,
    )

    local = _published_campaign_media_local_reconcile(db, limit=limit, account_ids=account_ids)
    db.commit()

    if paid_limit is None:
        try:
            paid_limit = int(os.environ.get("BORIS_MEDIA_REPAIR_PAID_LIMIT", "5") or 5)
        except Exception:
            paid_limit = 5
    paid_limit = max(0, min(int(paid_limit or 0), 10))

    paid_candidates = list(local.get("needs_paid") or [])
    paid_results = []
    provider_cooldown = None
    # One account may have several broken cards. If its provider already returned
    # 429, do not reserve budgets or hit the provider again until the durable
    # cooldown expires.
    candidate_accounts = list(dict.fromkeys(
        str(x.get("account_id") or "") for x in paid_candidates
        if str(x.get("account_id") or "")
    ))
    active_cooldowns = []
    for _aid in candidate_accounts:
        _cool = _published_media_provider_cooldown_read(db, _aid)
        if bool(_cool.get("active")):
            active_cooldowns.append({"account_id": _aid, **_cool})
    if active_cooldowns:
        provider_cooldown = active_cooldowns
        paid_limit = 0

    for candidate in paid_candidates[:paid_limit]:
        account_id = str(candidate.get("account_id") or "")
        item_id = int(candidate.get("item_id") or 0)
        row = db.query(CampaignItem).filter(
            CampaignItem.id == item_id,
            CampaignItem.account_id == account_id,
            CampaignItem.status == "published",
        ).first()
        if row is None:
            paid_results.append({
                "account_id": account_id,
                "item_id": item_id,
                "status": "skipped",
                "reason": "published_item_not_found",
            })
            continue

        try:
            payload = json.loads(row.payload_json or "{}")
        except Exception:
            payload = {}
        linked = (
            db.query(MediaLink, MediaAsset)
            .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
            .filter(
                MediaLink.entity_type == "campaign_item",
                MediaLink.entity_id == str(row.id),
                MediaAsset.deleted_at.is_(None),
                MediaAsset.file_state == "present",
            )
            .order_by(MediaLink.position.asc(), MediaLink.id.asc())
            .all()
        )
        if not linked:
            paid_results.append({
                "account_id": account_id,
                "item_id": item_id,
                "status": "blocked",
                "reason": "no_current_media_links",
            })
            continue

        try:
            db.commit()
            verdicts = classify_item_media_vision(
                payload,
                linked,
                account_id=account_id,
                provenance={
                    "source": "media_reconcile_published_self_heal",
                    "campaign_id": int(row.campaign_id or 0),
                    "item_id": item_id,
                },
            )
            original_sig = [(int(l.position), int(a.id)) for l, a in linked]

            db.expire_all()
            fresh = db.query(CampaignItem).filter(
                CampaignItem.id == item_id,
                CampaignItem.account_id == account_id,
                CampaignItem.status == "published",
            ).first()
            if fresh is None:
                paid_results.append({
                    "account_id": account_id,
                    "item_id": item_id,
                    "status": "skipped",
                    "reason": "item_changed_or_unpublished",
                })
                continue
            fresh_payload = json.loads(fresh.payload_json or "{}")
            fresh_linked = (
                db.query(MediaLink, MediaAsset)
                .join(MediaAsset, MediaAsset.id == MediaLink.media_id)
                .filter(
                    MediaLink.entity_type == "campaign_item",
                    MediaLink.entity_id == str(fresh.id),
                    MediaAsset.deleted_at.is_(None),
                    MediaAsset.file_state == "present",
                )
                .order_by(MediaLink.position.asc(), MediaLink.id.asc())
                .all()
            )
            fresh_sig = [(int(l.position), int(a.id)) for l, a in fresh_linked]
            if (
                semantic_content_fingerprint(fresh_payload)
                != str(candidate.get("content_fingerprint") or "")
                or fresh_sig != original_sig
            ):
                db.rollback()
                paid_results.append({
                    "account_id": account_id,
                    "item_id": item_id,
                    "status": "deferred",
                    "reason": "concurrent_media_or_content_change",
                })
                continue

            stamp_item_semantic_evidence(fresh_payload, fresh_linked, verdicts)
            fresh.payload_json = json.dumps(fresh_payload, ensure_ascii=False)
            fresh.content_hash = content_hash(fresh_payload)
            db.commit()
            remaining = list(validate_linked_media_semantics(db, fresh, fresh_payload) or [])
            paid_results.append({
                "account_id": account_id,
                "item_id": item_id,
                "status": "repaired" if not remaining else "blocked",
                "remaining_codes": sorted({
                    str(x.get("code") or "") for x in remaining
                }),
            })
            if not remaining:
                _published_media_provider_cooldown_clear(db, account_id)
        except Exception as exc:
            try:
                db.rollback()
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=1042)
            detail = str(exc)[:180]
            provider_http_status = None
            for _status in (401, 403, 429):
                if f"VISION_PROVIDER_HTTP_ERROR:{_status}" in str(exc):
                    provider_http_status = _status
                    break
            is_429 = bool(
                provider_http_status == 429
                or "VISION_PROVIDER_COOLDOWN" in str(exc)
            )
            # PUBLISHED_MEDIA_PROVIDER_ACCESS_BACKOFF_V1:
            # 401/403 are not transient item failures. Replaying the same paid
            # request for every broken card cannot repair authorization/region
            # access and only burns budget/traffic. Persist a long external
            # dependency backoff and let the normal self-heal retry later.
            is_provider_access_block = provider_http_status in {401, 403}
            if is_429 or is_provider_access_block:
                cooldown_seconds = 900 if is_429 else 21600
                cooldown_reason = (
                    "vision_provider_429"
                    if is_429
                    else f"vision_provider_access_{provider_http_status}"
                )
                try:
                    provider_cooldown = [{
                        "account_id": account_id,
                        **_published_media_provider_cooldown_set(
                            db, account_id, cooldown_seconds, cooldown_reason
                        ),
                        "active": True,
                        "remaining_seconds": cooldown_seconds,
                    }]
                except Exception:
                    try:
                        db.rollback()
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=1062)
            paid_results.append({
                "account_id": account_id,
                "item_id": item_id,
                "status": "deferred",
                "reason": (
                    "provider_access_wait"
                    if is_provider_access_block
                    else ("provider_cooldown" if is_429 else type(exc).__name__)
                ),
                "dependency_state": (
                    "waiting_external"
                    if (is_429 or is_provider_access_block)
                    else None
                ),
                "provider_http_status": provider_http_status,
                "detail": detail,
            })
            # Never turn one provider auth/rate-limit failure into a burst across
            # every broken card. One exact provider result is enough to defer the
            # account until the durable cooldown expires.
            if is_429 or is_provider_access_block:
                break

    repaired_paid = sum(1 for x in paid_results if x.get("status") == "repaired")
    deferred_paid = sum(1 for x in paid_results if x.get("status") == "deferred")
    blocked_paid = sum(1 for x in paid_results if x.get("status") == "blocked")
    remaining_unattempted = max(0, len(paid_candidates) - len(paid_results))

    result = dict(local)
    result.update({
        "paid_limit": paid_limit,
        "paid_candidates": len(paid_candidates),
        "paid_attempted": len(paid_results),
        "repaired_paid": repaired_paid,
        "paid_deferred": deferred_paid,
        "paid_blocked": blocked_paid,
        "paid_unattempted": remaining_unattempted,
        "paid_results": paid_results,
        "provider_cooldown": provider_cooldown,
        "repaired_total": int(local.get("repaired_local") or 0) + repaired_paid,
    })

    try:
        from app.services.reliability import heartbeat
        unresolved = (
            len(local.get("blocked") or [])
            + blocked_paid
            + deferred_paid
            + remaining_unattempted
        )
        heartbeat(
            "media",
            "published_campaign_self_heal",
            state=("ok" if unresolved == 0 else "degraded"),
            details={
                "scanned_published": int(local.get("scanned_published") or 0),
                "repaired_local": int(local.get("repaired_local") or 0),
                "repaired_paid": repaired_paid,
                "unresolved": unresolved,
                "paid_deferred": deferred_paid,
                "paid_blocked": blocked_paid,
                "paid_unattempted": remaining_unattempted,
            },
            min_interval_seconds=5,
        )
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1117)

    return result


def reconcile(db, limit=2000, offset=0):
    """Сверка media_assets с фактическим хранилищем.

    Дёшево: только os.path.isfile, без PIL, без phash. В журнал уходят ТОЛЬКО
    переходы состояния, поэтому ежедневный прогон не засоряет историю.
    Пропавший файл не удаляется, не архивируется и не теряет связи —
    ставится file_state='missing', пользовательский status не трогается.
    """
    rows = db.query(MediaAsset).filter(MediaAsset.deleted_at.is_(None)) \
        .order_by(MediaAsset.id).limit(limit).offset(offset).all()
    became_missing = became_present = 0
    for a in rows:
        present = st.exists(a.storage_key)
        if present and a.file_state != "present":
            a.file_state = "present"
            became_present += 1
            log_action(account_id=a.account_id or "", action="Файл снова доступен",
                       object_kind="media", object_name=a.filename,
                       before_val="missing", after_val="present",
                       source="media.reconcile")
        elif not present and a.file_state != "missing":
            a.file_state = "missing"
            became_missing += 1
            log_action(account_id=a.account_id or "", action="Файл пропал из хранилища",
                       object_kind="media", object_name=a.filename,
                       before_val="present", after_val="missing",
                       reason="файл удалён вне медиатеки",
                       source="media.reconcile")
    db.commit()
    # jobs._run_reconcile scans media in chunks. Run the published-card semantic
    # self-heal only after the final filesystem chunk, otherwise one daily sweep
    # could retry the same paid proof several times when a provider is degraded.
    active_total = db.query(MediaAsset).filter(MediaAsset.deleted_at.is_(None)).count()
    final_chunk = int(offset or 0) + len(rows) >= int(active_total or 0)
    published = reconcile_published_campaign_media(db) if final_chunk else None
    return {"checked": len(rows), "became_missing": became_missing,
            "became_present": became_present,
            "published_campaign_media": published}


def merge_phash_duplicate(db, asset):
    """Collapse a visually identical live asset inside the same account scope.

    Exact pHash equality is deliberately conservative: near-matches are not
    auto-deleted because different product angles can look similar.  Links are
    migrated to the oldest canonical live asset; conflicting duplicate links
    are removed.  Physical files are retained so already-published direct URLs
    remain valid.
    """
    if asset is None or not asset.phash or not asset.account_id:
        return None
    from datetime import datetime, timezone
    from app.models.media_link import MediaLink
    keeper = db.query(MediaAsset).filter(
        MediaAsset.owner_user_id == asset.owner_user_id,
        MediaAsset.account_id == asset.account_id,
        MediaAsset.phash == asset.phash,
        MediaAsset.deleted_at.is_(None),
        MediaAsset.id != asset.id,
    ).order_by(MediaAsset.id.asc()).first()
    if keeper is None:
        return None
    links = db.query(MediaLink).filter(MediaLink.media_id == asset.id).all()
    for link in links:
        exists = db.query(MediaLink).filter(
            MediaLink.media_id == keeper.id,
            MediaLink.entity_type == link.entity_type,
            MediaLink.entity_id == link.entity_id,
            MediaLink.role == link.role,
        ).first()
        if exists:
            db.delete(link)
        else:
            link.media_id = keeper.id
    asset.deleted_at = datetime.now(timezone.utc)
    asset.status = "archived"
    return keeper
