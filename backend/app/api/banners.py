from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import os
import json as _json

def _is_legacy_fullai_banner_url(value: str) -> bool:
    """Old text-in-image renderer (including trimmed/uploaded copies) is banned."""
    s = str(value or "").strip().lower().split("?", 1)[0]
    name = s.rsplit("/", 1)[-1]
    return any(marker in name for marker in ("fullai_", "gptimg_"))


def _slugify_theme(text: str) -> str:
    import re
    text = (text or "bez_temy").strip().lower()
    text = re.sub(r"[^a-zа-я0-9]+", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:40] or "bez_temy"


def _save_banner_record(account_id, filename, folder, theme_label, prompt, source, fmt):
    from app.db.session import SessionLocal
    from app.models.banner import Banner
    try:
        db = SessionLocal()
        db.add(Banner(
            account_id=account_id, filename=filename, folder=folder,
            theme_label=theme_label, prompt=prompt, source=source, format=fmt,
        ))
        db.commit()
        db.close()
    except Exception as e:
        print("banner record save failed:", e)



def _generate_marketing_copy(
    raw_description: str,
    account_id: str = None,
    operation: str = "marketing_copy_full_banner",
) -> dict:
    """
    OpenAI-копирайтер баннерного контура.

    Вход:
        сырое описание бизнеса клиента.

    Выход:
        title
        subtitle
        price_text
        advantages

    Правила:
        - только факты из исходного описания;
        - никаких выдуманных скидок;
        - никаких выдуманных гарантий;
        - никаких выдуманных сроков;
        - короткий текст для дизайнерского макета;
        - преимущества пригодны для отдельных визуальных элементов.
    """

    import json
    import os

    source = " ".join(
        (raw_description or "").strip().split()
    )

    if not source:
        return {
            "title": "",
            "subtitle": "",
            "price_text": "",
            "advantages": [],
        }

    fallback = {
        "title": source[:70],
        "subtitle": "",
        "price_text": "",
        "advantages": [],
    }

    try:
        import hashlib
        from app.api.campaigns import _ff_guarded_openai_response

        # Reuse the existing canonical paid Responses contour used by
        # Campaign/Feed Factory. One source text is one durable economic intent:
        # no OpenAI SDK retries, exact provider request id, ai_budget, account
        # burst/daily caps, cross-process lock and durable paid-result cache.
        prompt = (
            "Ты арт-директор и рекламный копирайтер BORIS. Создай короткий текст для рекламного баннера. "
            "Работай ТОЛЬКО с фактами из входного описания. Ничего не придумывай. "
            "Не добавляй скидки, гарантии, сроки, цены, подарки или преимущества, которых нет во входных данных. "
            "Не используй пустые клише, если они явно не подтверждены. Заголовок до 4 слов, подзаголовок до 7 слов, "
            "максимум 3 коротких преимущества. Если цены нет — price_text пустой. "
            "Верни только JSON вида {\"title\":\"\",\"subtitle\":\"\",\"price_text\":\"\",\"advantages\":[]}.\n\n"
            "Описание бизнеса клиента:\n" + source
        )
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        paid = _ff_guarded_openai_response(
            str(account_id or ""), operation, "gpt-5-mini",
            [{"role": "user", "content": prompt}], 800,
            "banner-copy:" + source_hash, timeout=120,
        )
        raw = str((paid or {}).get("text") or "").strip()
        data = json.loads(raw)

        title = str(
            data.get("title") or ""
        ).strip()

        subtitle = str(
            data.get("subtitle") or ""
        ).strip()

        price_text = str(
            data.get("price_text") or ""
        ).strip()

        advantages = [
            str(x).strip()
            for x in (
                data.get("advantages")
                or []
            )
            if str(x).strip()
        ][:3]

        if not title:
            title = fallback["title"]

        result = {
            "title": title[:90],
            "subtitle": subtitle[:140],
            "price_text": price_text[:60],
            "advantages": [
                x[:80]
                for x in advantages
            ],
        }

        print(
            "[BANNER COPY] provider=openai"
            " model=gpt-5-mini"
            f" operation={operation}"
            f" account={account_id}",
            flush=True,
        )

        return result

    except Exception as exc:
        print(
            "[BANNER COPY] OpenAI failed; safe fallback; "
            f"error={repr(exc)[:300]}",
            flush=True,
        )

        return fallback


router = APIRouter(prefix="/api/banners", tags=["banners"])

IMAGES_DIR = "/root/BORIS/backend/images"
BASE_URL = "/images"


class InfographicRequest(BaseModel):
    account_id: str
    title: str
    subtitle: str = ""
    subtitle_color: str = "#F0F0F0"
    price_text: str = ""
    icon_name: str = "star.svg"
    photo_query: str = ""
    bg_color_top: str = "#FF6B35"
    bg_color_bottom: str = "#F7931E"
    accent_color: str = "#FFFFFF"
    photo_source: str = "ai"
    ai_quality: str = "medium"
    own_photo_url: str = ""
    ai_model: str = "gpt-image-1-mini"


class DiagonalRequest(BaseModel):
    account_id: str
    variant: str = "pc"  # "pc" | "mobile"
    title: str
    company_name: str = ""
    phone: str = ""
    address: str = ""
    ribbon_text: str = ""
    photo_query: str = ""
    advantages: List[str] = []
    price_text: str = ""
    icon_name: str = "star.svg"
    accent_color: str = "#1A56DB"
    bg_color: str = "#0B0F1A"
    ribbon_color: str = "#DC2626"
    ai_quality: str = "low"


class CarouselRequest(BaseModel):
    account_id: str
    variant: str = "pc"  # "pc" | "mobile"
    slide1_text: str
    slide1_photo_query: str
    slide2_text: str
    slide2_photo_query: str
    slide3_text: str
    slide3_photo_query: str
    price_text: str = ""
    icon_name: str = "star.svg"
    accent_color: str = "#FF6B35"
    bg_color_top: str = "#FF6B35"
    bg_color_bottom: str = "#F7931E"
    photo_source: str = "ai"
    ai_quality: str = "medium"


def _account_banner_dir(account_id: str, subfolder: str) -> str:
    path = f"{IMAGES_DIR}/{account_id}/banners/{subfolder}"
    os.makedirs(path, exist_ok=True)
    return path


from fastapi import Depends as _DepSec
from app.api.auth import require_owner as _ReqOwner, get_current_user as _CurUser
@router.post("/infographic")
def create_infographic(req: InfographicRequest, current_user=_DepSec(_CurUser)):
    from banner_generator import generate_infographic_banner
    import time as _time

    # This legacy-compatible own-photo route is still used by the dashboard.
    # Enforce the same account authorization as V4 before touching media or paid AI.
    account_id = _banner_v2_account_access(req.account_id, current_user)
    # The dashboard uses this endpoint only when the owner selected an existing
    # account photo. AI-background infographic mode is retired: new generated
    # advertising visuals must use the canonical local-text compositor.
    if not str(req.own_photo_url or "").strip():
        return {
            "status": "error", "code": "legacy_infographic_ai_mode_retired",
            "detail": "Для нового баннера используйте безопасный генератор: визуал без AI-текста + локальная типографика.",
            "safe_endpoint": "/api/banners/v2/create-one",
        }
    folder = _account_banner_dir(account_id, "infographic")
    fname = f"infographic_{int(_time.time())}.png"
    fpath = f"{folder}/{fname}"

    # Если заголовок пришёл как сырое длинное описание бизнеса (а не готовый короткий текст) —
    # прогоняем через копирайтера GigaChat, чтобы получить продающий текст, а не сырой набор фактов
    title = req.title
    subtitle = req.subtitle
    price_text = req.price_text
    # Own-photo mode is explicitly local-only: choosing an existing client image
    # must never trigger a hidden paid copy/image request. Long owner text is
    # normalized by the deterministic mobile contract below instead.
    if len(req.title) > 45 and not req.subtitle and not req.own_photo_url:
        copy = _generate_marketing_copy(req.title, account_id=account_id, operation="marketing_copy_infographic")
        title = copy["title"]
        subtitle = copy["subtitle"]
        price_text = copy["price_text"] or req.price_text

    # Mobile Avito contract for the still-active own-photo compositor. Never
    # shrink a long paragraph into unreadable microcopy: keep one short hero
    # statement and a short optional subtitle.
    title = " ".join(str(title or "").split()[:6])[:70].strip()
    subtitle = " ".join(str(subtitle or "").split()[:8])[:90].strip()
    if not title:
        raise HTTPException(400, "Для баннера нужен короткий заголовок")

    own_photo_path = None
    if req.own_photo_url:
        try:
            from pathlib import Path as _Path
            _root = _Path(IMAGES_DIR).resolve()
            _rel = str(req.own_photo_url).split("?", 1)[0].split("/images/", 1)[-1].lstrip("/")
            _candidate = (_root / _rel).resolve()
            # No traversal and no cross-account media reuse through this endpoint.
            _candidate.relative_to(_root / account_id)
            if _candidate.is_file():
                own_photo_path = str(_candidate)
        except Exception:
            own_photo_path = None
        if not own_photo_path:
            raise HTTPException(400, "Выбранное фото недоступно в медиатеке этого аккаунта")

    generate_infographic_banner(
        title=title,
        subtitle=subtitle,
        subtitle_color=req.subtitle_color,
        price_text=price_text,
        icon_name=req.icon_name,
        photo_query=req.photo_query,
        bg_color_top=req.bg_color_top,
        bg_color_bottom=req.bg_color_bottom,
        accent_color=req.accent_color,
        output_path=fpath,
        # Own-photo mode is local-only: never buy/generate a replacement when the
        # owner explicitly selected an existing image. AI remains background-only
        # only when no owner photo was selected.
        photo_source="ai",
        ai_quality=req.ai_quality,
        ai_model=req.ai_model,
        own_photo_path=own_photo_path, account_id=account_id, operation="banner_infographic",
    )

    _theme = _slugify_theme(req.title)
    _save_banner_record(account_id, fname, "infographic", req.title, f"{req.title} | {req.subtitle or ''}", "pillow", "infographic")
    # Canonical media registration: the gallery, feed validators and later reuse
    # must see the same approved artifact the dashboard just created.
    from app.services.banner_media import register_banner as _register_banner
    _media = _register_banner(
        account_id=account_id, absolute_path=fpath,
        source_provider=("boris_local" if own_photo_path else "openai_visual_local_text"),
        source_ref=("own_photo_local_text_v1" if own_photo_path else "legacy_infographic_visual_local_text_v1"),
    )
    _canonical_url = "/images/" + str(_media["storage_key"]).lstrip("/")
    return {
        "status": "ok", "url": _canonical_url, "urls": [_canonical_url],
        "media_id": _media["id"], "storage_key": _media["storage_key"],
        "avito_safe": True, "layout": "own_photo_local_text" if own_photo_path else "visual_local_text",
    }


@router.post("/diagonal")
def create_diagonal(req: DiagonalRequest):
    # Retired legacy paid layout. No active production UI calls this route.
    # Return the migration target before any provider/budget logic executes.
    return {
        "status": "error", "code": "legacy_banner_layout_retired",
        "detail": "Старый диагональный генератор отключён. Используйте V4 с локальной типографикой.",
        "safe_endpoint": "/api/banners/v4/design-one",
    }

    from banner_generator import _generate_diagonal_slide
    import time as _time

    slide_w, slide_h = (1420, 960) if req.variant == "mobile" else (1304, 480)

    slide = _generate_diagonal_slide(
        photo_query=req.photo_query,
        main_text=req.title,
        slide_w=slide_w,
        slide_h=slide_h,
        icon_name=req.icon_name,
        price_text=req.price_text,
        accent_color=req.accent_color,
        bg_color=req.bg_color,
        ai_quality=req.ai_quality,
        advantages=req.advantages,
        company_name=req.company_name,
        phone=req.phone,
        address=req.address,
        ribbon_text=req.ribbon_text,
        ribbon_color=req.ribbon_color,
    )

    folder = _account_banner_dir(req.account_id, "extended")
    fname = f"diagonal_{req.variant}_{int(_time.time())}.png"
    fpath = f"{folder}/{fname}"
    slide.convert("RGB").save(fpath, "PNG", quality=95)

    _save_banner_record(req.account_id, fname, "extended", req.title, req.title, "pillow", "extended")
    return {"status": "ok", "url": f"{BASE_URL}/{req.account_id}/banners/extended/{fname}"}


@router.post("/carousel")
def create_carousel(req: CarouselRequest):
    # Retired legacy paid layout; stale callers get a deterministic migration
    # response before any provider/budget logic executes.
    return {
        "status": "error", "code": "legacy_banner_layout_retired",
        "detail": "Старый карусельный генератор отключён. Используйте V4 с локальной типографикой.",
        "safe_endpoint": "/api/banners/v4/design-one",
    }

    from banner_generator import generate_carousel_banner_v2
    import time as _time

    folder = _account_banner_dir(req.account_id, "max_carousel")
    ts = int(_time.time())

    paths = generate_carousel_banner_v2(
        slide1_text=req.slide1_text,
        slide1_photo_query=req.slide1_photo_query,
        slide2_text=req.slide2_text,
        slide2_photo_query=req.slide2_photo_query,
        slide3_text=req.slide3_text,
        slide3_photo_query=req.slide3_photo_query,
        price_text=req.price_text,
        icon_name=req.icon_name,
        accent_color=req.accent_color,
        bg_color_top=req.bg_color_top,
        bg_color_bottom=req.bg_color_bottom,
        variant=req.variant,
        output_dir=folder,
        photo_source="ai",  # клиентский баннер всегда рисует OpenAI
        ai_quality=req.ai_quality,
    )

    urls = []
    for p in paths:
        fname = os.path.basename(p)
        new_path = f"{folder}/carousel_{ts}_{fname}"
        os.rename(p, new_path)
        urls.append(f"{BASE_URL}/{req.account_id}/banners/max_carousel/carousel_{ts}_{fname}")
        _save_banner_record(req.account_id, f"carousel_{ts}_{fname}", "max_carousel", req.slide1_text, req.slide1_text, "pillow", "max_carousel")

    return {"status": "ok", "urls": urls}


class FullAiRequest(BaseModel):
    account_id: str
    operation: str = "banner_full_ai"
    raw_description: str
    format: str = "infographic"
    accent_color: str = "#FF6B35"
    quality: str = "medium"
    include_contacts: bool = False
    phone: str = ""
    address: str = ""
    reference_image_base64: str = ""
    reference_image_urls: list = []
    exact_text: str = ""
    exact_subtitle: str = ""
    exact_price: str = ""
    count: int = 1  # сколько РАЗНЫХ вариантов баннера сгенерировать за один запрос (макс 5)
    vary_colors: bool = False    # варьировать цветовую палитру между вариантами
    vary_image: bool = False     # варьировать фоновую картинку/сцену между вариантами
    vary_icons: bool = False     # варьировать иконки/бейджи между вариантами
    vary_all: bool = False       # варьировать полностью всё (композиция, текст, цвет, сцена)
    template_id: str = ""               # id шаблона из banner_library аккаунта
    use_template_library: bool = False  # opt-in: текст и стиль из библиотеки, фон рисует OpenAI
    idempotency_key: str = ""  # stable key for safe retry of the same paid generation


def _describe_reference_style(image_base64: str, account_id: str = None, operation: str = "reference_style_analysis") -> str:
    """One paid vision intent per reference image; no automatic paid retries."""
    try:
        import base64, hashlib
        from app.api.campaigns import _ff_guarded_openai_response
        from app.services.ai_privacy import sanitize_external_reference_image_bytes
        raw = str(image_base64 or "").strip()
        if not raw:
            return ""
        encoded = raw.split(",", 1)[1] if raw.startswith("data:image/") and "," in raw else raw
        source_bytes = base64.b64decode(encoded, validate=False)
        safe_bytes, safe_mime = sanitize_external_reference_image_bytes(
            source_bytes, max_side=384, blur_radius=3.5
        )
        safe_b64 = base64.b64encode(safe_bytes).decode("ascii")
        fp = hashlib.sha256(safe_bytes).hexdigest()
        content = [
            {"type": "input_text", "text": "Опиши только общую визуальную композицию, цветовую палитру и расположение крупных элементов. Не пытайся читать или восстанавливать имена, телефоны, адреса, номера, лица или другой идентифицирующий текст. Пиши на английском, компактно."},
            {"type": "input_image", "image_url": "data:" + safe_mime + ";base64," + safe_b64},
        ]
        paid = _ff_guarded_openai_response(
            str(account_id or ""), operation, "gpt-5-mini",
            [{"role": "user", "content": content}], 500,
            "banner-ref-style:" + fp, timeout=90,
        )
        return str((paid or {}).get("text") or "").strip()
    except Exception as exc:
        print("[_describe_reference_style] guarded vision failed: " + str(exc)[:240], flush=True)
        return ""


def _describe_reference_style_multi(image_urls: list, account_id: str = None, operation: str = "reference_style_analysis_multi") -> str:
    """One paid vision intent for the selected reference set; only sanitized local media leaves BORIS."""
    try:
        import base64, hashlib, json, os
        from urllib.parse import urlparse
        from app.api.campaigns import _ff_guarded_openai_response
        from app.services.ai_privacy import sanitize_external_reference_image_bytes

        safe_images = []
        for raw_url in (image_urls or [])[:3]:
            u = str(raw_url or "").strip()
            if not u:
                continue
            source_bytes = None
            if u.startswith("data:image/") and ";base64," in u[:120]:
                try:
                    source_bytes = base64.b64decode(u.split(",", 1)[1], validate=False)
                except Exception:
                    source_bytes = None
            else:
                parsed = urlparse(u if "://" in u else "https://boris-ai.pro/" + u.lstrip("/"))
                if parsed.hostname in {"boris-ai.pro", "www.boris-ai.pro", "193.160.209.44", "127.0.0.1", "localhost"} and parsed.path.startswith("/images/"):
                    local_path = os.path.realpath("/root/BORIS/backend" + parsed.path)
                    images_root = os.path.realpath("/root/BORIS/backend/images")
                    if local_path.startswith(images_root + os.sep) and os.path.isfile(local_path):
                        with open(local_path, "rb") as fh:
                            source_bytes = fh.read()
            if not source_bytes:
                # Never give an external model a private/signed/arbitrary URL.
                continue
            safe_bytes, safe_mime = sanitize_external_reference_image_bytes(
                source_bytes, max_side=384, blur_radius=3.5
            )
            safe_images.append(
                "data:" + safe_mime + ";base64," + base64.b64encode(safe_bytes).decode("ascii")
            )

        if not safe_images:
            return ""
        fp = hashlib.sha256(json.dumps(safe_images, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        content = [{"type": "input_text", "text": "Ниже до 3 обезличенных рекламных референсов. Опиши только ОБЩИЙ визуальный стиль, композицию, цветовую палитру и расположение крупных элементов. Не восстанавливай имена, телефоны, адреса, номера, лица или другой идентифицирующий текст. Пиши на английском, компактно."}]
        content.extend({"type": "input_image", "image_url": u} for u in safe_images)
        paid = _ff_guarded_openai_response(
            str(account_id or ""), operation, "gpt-5-mini",
            [{"role": "user", "content": content}], 500,
            "banner-ref-style-multi:" + fp, timeout=90,
        )
        return str((paid or {}).get("text") or "").strip()
    except Exception as exc:
        print("[_describe_reference_style_multi] guarded vision failed: " + str(exc)[:240], flush=True)
        return ""


@router.get("/browse_all")
def browse_all_banners(limit: int = 200, user=_DepSec(_CurUser)):
    """Показывает ВСЕ когда-либо сгенерированные баннеры по всем клиентам - владелец
    просматривает их и выбирает, что добавить в курируемую витрину примеров (showcase)."""
    import os as _os_ex
    _allowed = None
    if getattr(user, "role", "") != "owner":
        from app.db.session import SessionLocal as _SLb
        from app.models.account import Account as _AccB
        _dbb = _SLb()
        try:
            _allowed = {a.account_id for a in _dbb.query(_AccB).filter(
                _AccB.owner_user_id == user.id).all()}
        finally:
            _dbb.close()
    results = []
    if _os_ex.path.isdir(IMAGES_DIR):
        for account_folder in _os_ex.listdir(IMAGES_DIR):
            if _allowed is not None and account_folder not in _allowed:
                continue
            banners_root = _os_ex.path.join(IMAGES_DIR, account_folder, "banners")
            if not _os_ex.path.isdir(banners_root):
                continue
            for root, _dirs, files in _os_ex.walk(banners_root):
                for fname in files:
                    # Quarantined legacy text-in-image banners stay on disk only as
                    # recovery evidence; never expose them as reusable UI choices.
                    if fname.lower().startswith(("fullai_", "gptimg_")):
                        continue
                    if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                        full_path = _os_ex.path.join(root, fname)
                        rel = _os_ex.path.relpath(full_path, IMAGES_DIR)
                        try:
                            mtime = _os_ex.path.getmtime(full_path)
                        except Exception:
                            mtime = 0
                        results.append({
                            "url": "/images/" + rel.replace(_os_ex.sep, "/"),
                            "account": account_folder,
                            "mtime": mtime,
                        })
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return {"status": "ok", "banners": results[:limit], "total": len(results)}


@router.get("/showcase")
def get_banner_showcase(account_id: str, user=_DepSec(_CurUser)):
    """Курируемая витрина баннеров-образцов, вручную отобранных владельцем."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_sc
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "banner_showcase").first()
        items = _json_sc.loads(row.value) if row else []
        # Never return quarantined legacy full-AI text banners from a showcase,
        # even if an old storage snapshot still contains their URL.
        items = [it for it in items if not _is_legacy_fullai_banner_url(
            (it.get("url") if isinstance(it, dict) else str(it)) or ""
        )]
        if getattr(user, "role", "") != "owner":
            from app.models.account import Account as _AccSc
            _allowed = {a.account_id for a in db.query(_AccSc).filter(
                _AccSc.owner_user_id == user.id).all()}

            def _is_own(it):
                u = (it.get("url") if isinstance(it, dict) else str(it)) or ""
                if u.startswith("http://") or u.startswith("https://"):
                    return True
                if not u.startswith("/images/"):
                    return False
                seg = u.split("/", 3)
                return len(seg) > 2 and seg[2] in _allowed

            items = [it for it in items if _is_own(it)]
        return {"status": "ok", "showcase": items}
    finally:
        db.close()


class ShowcaseUrlRequest(BaseModel):
    url: str
    account_id: str = "otdushi"


class ShowcaseUploadRequest(BaseModel):
    image_base64: str
    filename: str = "upload.jpg"
    account_id: str = "otdushi"


@router.post("/showcase/upload")
def upload_to_banner_showcase(req: ShowcaseUploadRequest):
    """Загружает СВОЁ изображение (например, скриншот удачного баннера конкурента)
    прямо в витрину примеров - с автообрезкой чёрных полей."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_up
    import datetime as _dt_up
    import os as _os_up
    import base64 as _b64_up
    import io as _io_up
    from PIL import Image as _Image_up

    try:
        raw = req.image_base64
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        img_bytes = _b64_up.b64decode(raw)
        img = _Image_up.open(_io_up.BytesIO(img_bytes))
        trimmed = _auto_trim_borders(img)

        showcase_dir = _os_up.path.join(IMAGES_DIR, "_showcase")
        _os_up.makedirs(showcase_dir, exist_ok=True)
        safe_name = "".join(c for c in req.filename if c.isalnum() or c in "._-") or "upload.jpg"
        new_name = f"uploaded_{int(_dt_up.datetime.utcnow().timestamp())}_{safe_name}"
        if not new_name.lower().endswith((".jpg", ".jpeg", ".png")):
            new_name += ".jpg"
        new_path = _os_up.path.join(showcase_dir, new_name)
        trimmed.convert("RGB").save(new_path, "JPEG", quality=92)
        final_url = "/images/_showcase/" + new_name
    except Exception as e:
        return {"status": "error", "message": "Не удалось обработать изображение: " + str(e)[:200]}

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "banner_showcase").first()
        items = _json_up.loads(row.value) if row else []
        items.insert(0, {"url": final_url, "added_at": _dt_up.datetime.utcnow().isoformat()})
        if row:
            row.value = _json_up.dumps(items, ensure_ascii=False)
        else:
            row = Storage(account_id=req.account_id, key="banner_showcase", value=_json_up.dumps(items, ensure_ascii=False))
            db.add(row)
        db.commit()
        return {"status": "ok", "count": len(items), "url": final_url}
    finally:
        db.close()


def _auto_trim_borders(img):
    """Обрезает тёмные (обычно чёрные) поля по краям изображения - типичный артефакт
    скриншотов с телефона (статус-бар с белыми иконками/цифрами поверх чёрного фона,
    чёрные полосы сверху/снизу). Строка/столбец считается "границей", если ДОЛЯ тёмных
    пикселей в ней высокая (>= 85%), а не по средней яркости - иначе тонкие белые иконки
    статус-бара (занимающие мало места, но дающие заметный вклад в среднее) ошибочно
    останавливают обрезку на первой же строке."""
    w, h = img.size
    rgb = img.convert("RGB")
    px = rgb.load()
    dark_pixel_threshold = 60
    dark_fraction_threshold = 0.85

    def row_is_border(y):
        samples = [px[x, y] for x in range(0, w, max(1, w // 80))]
        dark_count = sum(1 for c in samples if (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) < dark_pixel_threshold)
        return (dark_count / len(samples)) >= dark_fraction_threshold

    def col_is_border(x):
        samples = [px[x, y] for y in range(0, h, max(1, h // 80))]
        dark_count = sum(1 for c in samples if (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) < dark_pixel_threshold)
        return (dark_count / len(samples)) >= dark_fraction_threshold

    def scan_forward(is_border_fn, start, end, step):
        """Идёт от start к end с шагом step, толерантна к нескольким подряд
        "не-граничным" пикселям (шум сжатия JPEG на стыке предыдущей обрезки) -
        запоминает последнюю подтверждённую границу и не сдаётся сразу же."""
        gap_tolerance = 8
        last_border = start - step
        i = start
        gap = 0
        while i != end:
            if is_border_fn(i):
                last_border = i
                gap = 0
            else:
                gap += 1
                if gap > gap_tolerance:
                    break
            i += step
        return last_border + step

    top = scan_forward(row_is_border, 0, h // 2, 1)
    bottom = scan_forward(row_is_border, h - 1, h // 2, -1)
    left = scan_forward(col_is_border, 0, w // 2, 1)
    right = scan_forward(col_is_border, w - 1, w // 2, -1)

    top = min(top, h // 2 - 1)
    bottom = max(bottom, h // 2)
    left = min(left, w // 2 - 1)
    right = max(right, w // 2)

    if bottom > top and right > left and (bottom - top) > h * 0.15 and (right - left) > w * 0.15:
        return img.crop((left, top, right + 1, bottom + 1))
    return img


@router.post("/showcase/add")
def add_to_banner_showcase(req: ShowcaseUrlRequest):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_sc
    import datetime as _dt_sc
    import os as _os_sc
    from PIL import Image as _Image_sc

    final_url = req.url
    if _is_legacy_fullai_banner_url(final_url):
        return {"status": "error", "message": "Этот старый тип баннера запрещён для повторного использования в Avito."}
    try:
        if req.url.startswith("/images/") or req.url.startswith("http://193.160.209.44:8000/images/") or req.url.startswith("https://boris-ai.pro/images/"):
            rel = req.url.split("/images/", 1)[1]
            local_path = _os_sc.path.join(IMAGES_DIR, rel)
            if _os_sc.path.isfile(local_path):
                img = _Image_sc.open(local_path)
                trimmed = _auto_trim_borders(img)
                if trimmed.size != img.size:
                    showcase_dir = _os_sc.path.join(IMAGES_DIR, "_showcase")
                    _os_sc.makedirs(showcase_dir, exist_ok=True)
                    new_name = "trimmed_" + _os_sc.path.basename(local_path)
                    new_path = _os_sc.path.join(showcase_dir, new_name)
                    trimmed.convert("RGB").save(new_path, "JPEG", quality=92)
                    final_url = "/images/_showcase/" + new_name
    except Exception as _trim_e:
        print("[showcase/add] auto-trim не удался: " + str(_trim_e), flush=True)

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "banner_showcase").first()
        items = _json_sc.loads(row.value) if row else []
        if not any(it.get("url") == final_url for it in items):
            items.insert(0, {"url": final_url, "original_url": req.url, "added_at": _dt_sc.datetime.utcnow().isoformat()})
        if row:
            row.value = _json_sc.dumps(items, ensure_ascii=False)
        else:
            row = Storage(account_id=req.account_id, key="banner_showcase", value=_json_sc.dumps(items, ensure_ascii=False))
            db.add(row)
        db.commit()
        return {"status": "ok", "count": len(items), "url": final_url}
    finally:
        db.close()


@router.post("/showcase/remove")
def remove_from_banner_showcase(req: ShowcaseUrlRequest):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_sc
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "banner_showcase").first()
        items = _json_sc.loads(row.value) if row else []
        items = [it for it in items if it.get("url") != req.url]
        if row:
            row.value = _json_sc.dumps(items, ensure_ascii=False)
            db.commit()
        return {"status": "ok", "count": len(items)}
    finally:
        db.close()


def _read_storage_json(account_id: str, key: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        return _json.loads(row.value) if row else None
    except Exception:
        return None
    finally:
        db.close()


def _load_template(account_id: str, template_id: str) -> dict:
    """Шаблон строго из библиотеки ЭТОГО аккаунта. Не найден - 404, повреждён - 422."""
    from fastapi import HTTPException
    lib = _read_storage_json(account_id, "banner_library")
    if not isinstance(lib, list) or not lib:
        raise HTTPException(status_code=404, detail="Библиотека шаблонов не найдена для этого аккаунта")
    found = [t for t in lib if isinstance(t, dict) and t.get("id") == template_id]
    if len(found) != 1:
        raise HTTPException(status_code=404, detail="Шаблон %s не найден" % template_id)
    t = found[0]
    if not isinstance(t.get("title"), str) or not t["title"].strip():
        raise HTTPException(status_code=422, detail="Шаблон повреждён: пустой заголовок")
    if not isinstance(t.get("subtitle"), str):
        raise HTTPException(status_code=422, detail="Шаблон повреждён: подзаголовок не строка")
    if not isinstance(t.get("has_price"), bool):
        raise HTTPException(status_code=422, detail="Шаблон повреждён: has_price не булево")
    price = t.get("price") or ""
    if not isinstance(price, str):
        raise HTTPException(status_code=422, detail="Шаблон повреждён: цена не строка")
    if not t["has_price"] and price:
        raise HTTPException(status_code=422, detail="has_price=false, но цена заполнена")
    return t


def _template_background_query(tpl: dict, style: dict) -> str:
    """Запрос на ФОН для OpenAI: сцена без единой надписи, текст накладываем сами."""
    photo = (tpl.get("photo") or "").strip() or (tpl.get("subtype") or "объект")
    niche = (style or {}).get("niche", "")
    scene = (style or {}).get("scene") or (
        "строительная бытовка, блок-контейнер" if niche == "bytovki" else "мебель на заказ в интерьере")
    return (
        "Photorealistic advertising background photo, square. Scene: " + photo + " (" + scene + "). "
        "Professional commercial photography, natural lighting, clean uncluttered composition, "
        "main object slightly left of center, calm neutral background, plenty of empty space "
        "in the lower third for text overlay. "
        "ABSOLUTELY NO TEXT: no letters, no words, no numbers, no captions, no signs, no logos, "
        "no watermarks, no badges, no stars, no stickers anywhere in the image. "
        "No collage, no frames, no clipart."
    )


@router.post("/full_ai")
def create_full_ai_banner(req: FullAiRequest):
    import time as _time

    # Reject the retired unsafe mode BEFORE any paid-generation/idempotency logic.
    # Old callers therefore get a deterministic migration answer without being
    # mistaken for a paid request and without any provider/budget side effects.
    if not req.use_template_library:
        return {
            "status": "error",
            "code": "legacy_text_in_image_forbidden",
            "detail": "Старая генерация текста внутри AI-изображения отключена. Используйте безопасный V4: визуал без текста + локальная типографика.",
            "safe_endpoint": "/api/banners/v2/create-one",
        }

    # Only the safe template-library branch is paid; it requires one stable caller identity.
    _idem = str(getattr(req, "idempotency_key", "") or "").strip()
    if not _idem:
        return {"status": "idempotency_required", "code": "paid_idempotency_required",
                "detail": "Для платной генерации нужен ключ безопасного повтора. Обновите страницу и запустите ещё раз."}

    if req.use_template_library:
        from fastapi import HTTPException as _HTTPExc
        import time as _t2, json as _j2, hashlib as _h2
        from banner_generator import generate_infographic_banner as _gen_info
        if not req.template_id:
            raise _HTTPExc(status_code=422, detail="use_template_library=true, но template_id не передан")
        _tpl = _load_template(req.account_id, req.template_id)
        _style = _read_storage_json(req.account_id, "brand_style") or {}
        _colors = _style.get("colors", {}) or {}
        _scheme = (_style.get("schemes", {}) or {}).get(_tpl.get("scheme", ""), {}) or {}
        _primary = _scheme.get("primary") or _colors.get("brand") or "#1B4FD8"
        _accent = _colors.get("accent") or "#FF4D2E"
        _price = (_tpl.get("price") or "") if _tpl.get("has_price") else ""
        _q = _template_background_query(_tpl, _style)
        _folder = _account_banner_dir(req.account_id, "infographic")
        _tpl_intent = _h2.sha256((str(req.account_id) + "|" + _idem + "|" + str(req.template_id)).encode("utf-8")).hexdigest()[:24]
        _fname = "tpl_" + str(req.template_id) + "_idem_" + _tpl_intent + ".png"
        _fpath = _folder + "/" + _fname
        _gen_info(
            title=_tpl["title"], subtitle=_tpl.get("subtitle", ""), price_text=_price,
            icon_name="circle-check.svg", bg_color_top=_primary, bg_color_bottom=_primary,
            accent_color=_accent, output_path=_fpath, photo_query=_q,
            photo_source="ai", ai_quality=req.quality, ai_model="gpt-image-1-mini",
            own_photo_path=None, account_id=req.account_id, operation="banner_from_template",
        )
        _meta = {"template_id": _tpl.get("id"), "template_type": _tpl.get("type"),
                 "template_subtype": _tpl.get("subtype"), "template_block": _tpl.get("block"),
                 "brand_style_niche": _style.get("niche"), "use_template_library": True,
                 "provider": "openai", "background_prompt_sha256": _h2.sha256(_q.encode()).hexdigest()[:16]}
        _save_banner_record(req.account_id, _fname, "infographic", _tpl["title"],
                            _j2.dumps(_meta, ensure_ascii=False), "template", "infographic")
        print("[banner] шаблон %s: текст дословно, фон от OpenAI, GigaChat не вызывался" % req.template_id)
        return {"status": "ok", "url": BASE_URL + "/" + req.account_id + "/banners/infographic/" + _fname,
                "urls": [BASE_URL + "/" + req.account_id + "/banners/infographic/" + _fname],
                "template_id": _tpl.get("id")}

    # Both branches above return. Legacy AI-visible-text implementation was removed
    # rather than merely left unreachable, so future refactors cannot accidentally revive it.


@router.delete("/delete")
def delete_banner(account_id: str, subfolder: str, filename: str):
    path = f"{IMAGES_DIR}/{account_id}/banners/{subfolder}/{filename}"
    if os.path.isfile(path):
        os.remove(path)
        return {"status": "ok"}
    return {"status": "error", "detail": "Файл не найден"}


@router.get("/list")
def list_banners(account_id: str):
    base = f"{IMAGES_DIR}/{account_id}/banners"
    result = {"infographic": [], "extended": [], "max_carousel": []}
    for subfolder in result.keys():
        path = f"{base}/{subfolder}"
        if os.path.isdir(path):
            files = sorted(os.listdir(path), reverse=True)
            files = [f for f in files if not f.lower().startswith(("fullai_", "gptimg_"))]
            result[subfolder] = [f"{BASE_URL}/{account_id}/banners/{subfolder}/{f}" for f in files]
    return {"status": "ok", "banners": result}


class ClassifyBannerRequest(BaseModel):
    account_id: str
    banner_path: str  # относительный путь вида /images/{account_id}/banners/infographic/xxx.png

@router.post("/classify")
def classify_banner(req: ClassifyBannerRequest):
    """Определяет тему баннера через GPT-зрение и подсказывает, к какому направлению
    объявлений (по существующим id_prefix в feed_items) его можно применить."""
    import base64, os as _os_cb, requests as _requests_cb, json as _json_cb
    from proxy_pool import get_intl_requests_proxies

    api_key = _os_cb.getenv("OPENAI_API_KEY")

    local_path = req.banner_path.lstrip("/")
    if not _os_cb.path.exists(local_path):
        return {"status": "error", "message": "Файл баннера не найден"}

    with open(local_path, "rb") as img:
        raw_image = img.read()
    from app.services.ai_privacy import sanitize_external_reference_image_bytes
    safe_image, safe_mime = sanitize_external_reference_image_bytes(
        raw_image, max_side=384, blur_radius=3.5
    )
    b64 = base64.b64encode(safe_image).decode()

    try:
        import hashlib
        from app.api.campaigns import _ff_guarded_openai_response
        fp = hashlib.sha256(b64.encode("ascii", errors="ignore")).hexdigest()
        content = [
            {"type": "input_text", "text": "Что рекламирует этот баннер? Ответь одним-двумя словами: конкретный товар или услуга, без лишних слов."},
            {"type": "input_image", "image_url": "data:" + safe_mime + ";base64," + b64},
        ]
        paid = _ff_guarded_openai_response(
            str(req.account_id or ""), "banner_classify", "gpt-5-mini",
            [{"role": "user", "content": content}], 80,
            "banner-classify:" + fp, timeout=90,
        )
        topic = str((paid or {}).get("text") or "").strip().lower()
        if not topic:
            return {"status": "error", "message": "Не удалось распознать баннер"}
    except Exception as exc:
        print("[classify_banner] guarded vision failed: " + str(exc)[:240], flush=True)
        return {"status": "error", "message": "Не удалось выполнить распознавание"}

    from app.db.session import SessionLocal
    from app.models.storage import Storage

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "feed_items").first()
        feed_items = _json_cb.loads(row.value) if row else []
    finally:
        db.close()

    prefixes = {}
    for it in feed_items:
        parts = it.get("id", "").split("-")
        if len(parts) >= 2:
            prefix = f"{parts[0]}-{parts[1]}"
            prefixes[prefix] = prefixes.get(prefix, 0) + 1

    topic_words = [w for w in topic.split() if len(w) > 3]
    matching_prefixes = [p for p in prefixes if any(w[:4] in p.lower() for w in topic_words)]

    return {
        "status": "ok",
        "detected_topic": topic,
        "existing_directions": prefixes,
        "recommended_prefixes": matching_prefixes,
        "message": (
            f"На баннере: {topic}. Подходит для направлений: {', '.join(matching_prefixes)}"
            if matching_prefixes else
            f"На баннере: {topic}. Совпадений с существующими направлениями не найдено — либо это новая тема, либо название направления отличается по формулировке."
        )
    }

# ---- массовое скачивание баннеров архивом ----
from fastapi import Body
from fastapi.responses import StreamingResponse
import io as _io
import zipfile as _zipfile
import os as _os


@router.post("/download_zip")
def download_selected(payload: dict = Body(...)):
    """Принимает {"paths": ["/abs/path/a.png", ...]} -> отдаёт zip-архив."""
    paths = payload.get("paths") or []
    buf = _io.BytesIO()
    added = 0
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        for web_path in paths:
            try:
                if not web_path:
                    continue
                # веб-путь /images/acc/banners/... -> диск /root/BORIS/backend/images/acc/banners/...
                rel = str(web_path).lstrip("/")
                if rel.startswith("images/"):
                    rel = rel[len("images/"):]
                disk_path = _os.path.join(IMAGES_DIR, rel)
                if not _os.path.isfile(disk_path):
                    continue
                zf.write(disk_path, arcname=_os.path.basename(disk_path))
                added += 1
            except Exception:
                continue
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="boris_banners.zip"'},
    )

# ===== BORIS BANNER V2 SAFE API =====


# ===== BORIS BANNER V2 SAFE API =====

class BannerV2CreateOneRequest(BaseModel):
    account_id: str
    description: str
    title: str = ""
    subtitle: str = ""
    price_text: str = ""
    photo_url: str = ""
    accent_color: str = "#2F6FED"
    idempotency_key: str = ""


@router.post("/v2/create-one")
def banner_v2_create_one(
    req: BannerV2CreateOneRequest,
    current_user=_DepSec(_CurUser),
):
    """
    Один контрольный баннер.

    Всегда count=1.
    Клиент не выбирает AI-модель/quality.
    Результат сохраняется legacy-совместимо и копируется в Единую медиатеку.
    """
    from sqlalchemy import text as _sql_text
    from app.db.session import SessionLocal as _BannerSession
    from app.services.banner_media import register_banner as _register_banner

    account_id = (req.account_id or "").strip()

    if not account_id:
        raise HTTPException(400, "Не выбран аккаунт")

    # Проверяем, что текущий пользователь имеет доступ к аккаунту.
    db = _BannerSession()

    try:
        uid = getattr(current_user, "id", None)

        if uid is None and isinstance(current_user, dict):
            uid = current_user.get("id") or current_user.get("user_id")

        if not uid:
            raise HTTPException(401, "Требуется авторизация")

        row = db.execute(
            _sql_text("""
                SELECT 1
                FROM accounts a
                WHERE a.account_id=:a
                  AND (
                       a.owner_user_id=:u
                       OR EXISTS (
                           SELECT 1
                           FROM user_account_access x
                           WHERE x.account_id=a.account_id
                             AND x.user_id=:u
                             AND coalesce(x.can_manage_ads, false)=true
                       )
                  )
                LIMIT 1
            """),
            {"a": account_id, "u": int(uid)},
        ).fetchone()

        if not row:
            raise HTTPException(
                403,
                "Нет прав на этот аккаунт",
            )

    finally:
        db.close()

    raw = (req.description or "").strip()

    if not raw:
        raise HTTPException(
            400,
            "Опишите, что нужно показать на баннере",
        )

    idem = str(req.idempotency_key or "").strip()
    if not idem:
        return {
            "status": "idempotency_required",
            "code": "paid_idempotency_required",
            "detail": "Для платной генерации нужен ключ безопасного повтора. Обновите экран и запустите ещё раз.",
        }

    # Пользовательские точные тексты имеют приоритет.
    # Если заголовок не задан — существующий copy-контур предложит его сам.
    exact_title = (req.title or "").strip()
    exact_subtitle = (req.subtitle or "").strip()
    exact_price = (req.price_text or "").strip()

    result = create_avito_safe_banner(
        account_id=account_id,
        description=raw,
        direction="architecture",
        accent_color=req.accent_color or "#2F6FED",
        exact_title=exact_title,
        exact_subtitle=exact_subtitle,
        exact_price=exact_price,
        idempotency_key=idem,
    )
    if result.get("status") != "ok":
        return result
    return {
        "status": "ok",
        "url": result.get("url"),
        "generated_copy": result.get("copy"),
        "media": {"id": result.get("media_id"), "storage_key": result.get("storage_key")},
        "layout": result.get("layout"),
        "avito_safe": True,
        "message": "Баннер готов",
    }



# ===== BORIS BANNER V2 STAGE C =====

# ===== BORIS BANNER V2 STAGE C =====

class BannerV2CopyRequest(BaseModel):
    account_id: str
    goal: str = "offer"
    description: str


class BannerV2DesignRequest(BaseModel):
    account_id: str
    goal: str = "offer"
    description: str
    title: str
    subtitle: str = ""
    price_text: str = ""
    accent_color: str = "#2F6FED"


def _banner_v2_uid(current_user):
    uid = getattr(current_user, "id", None)

    if uid is None and isinstance(current_user, dict):
        uid = (
            current_user.get("id")
            or current_user.get("user_id")
        )

    try:
        return int(uid)
    except Exception:
        raise HTTPException(
            401,
            "Требуется авторизация",
        )


def _banner_v2_account_access(account_id, current_user):
    from sqlalchemy import text as _sql_text
    from app.db.session import SessionLocal as _BannerSession

    account_id = (account_id or "").strip()

    if not account_id:
        raise HTTPException(
            400,
            "Не выбран аккаунт",
        )

    uid = _banner_v2_uid(current_user)

    db = _BannerSession()

    try:
        row = db.execute(
            _sql_text("""
                SELECT 1
                FROM accounts a
                WHERE a.account_id=:a
                  AND (
                    a.owner_user_id=:u
                    OR EXISTS (
                        SELECT 1
                        FROM user_account_access x
                        WHERE x.account_id=a.account_id
                          AND x.user_id=:u
                          AND coalesce(
                              x.can_manage_ads,
                              false
                          )=true
                    )
                  )
                LIMIT 1
            """),
            {
                "a": account_id,
                "u": uid,
            },
        ).fetchone()

        if not row:
            raise HTTPException(
                403,
                "Нет прав на этот аккаунт",
            )

    finally:
        db.close()

    return account_id


@router.post("/v2/copy")
def banner_v2_copy(
    req: BannerV2CopyRequest,
    current_user=_DepSec(_CurUser),
):
    # Retired stage endpoint. Active UI uses V4 copy; return before any paid text call.
    return {
        "status": "error", "code": "legacy_banner_stage_retired",
        "detail": "Старый этап копирайтинга отключён. Используйте V4.",
        "safe_endpoint": "/api/banners/v4/copy",
    }
    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (
        req.description or ""
    ).strip()

    if not description:
        raise HTTPException(
            400,
            "Расскажите о предложении",
        )

    goal_labels = {
        "offer": "главное коммерческое предложение",
        "price": "акцент на цене или акции",
        "benefit": "ключевое преимущество",
    }

    context = (
        "Цель баннера: "
        + goal_labels.get(
            req.goal,
            goal_labels["offer"],
        )
        + ". "
        + description
    )

    copy = _generate_marketing_copy(
        context,
        account_id=account_id,
        operation="banner_v2_copy",
    )

    title = (
        copy.get("title")
        or description[:48]
    ).strip()

    subtitle = (
        copy.get("subtitle")
        or ""
    ).strip()

    price = (
        copy.get("price_text")
        or ""
    ).strip()

    # Защита от служебного текста.
    banned = (
        "показать предложение",
        "сделать акцент на цене",
        "показать преимущества",
        "главное коммерческое предложение",
    )

    if title.lower() in banned:
        title = description[:48].strip()

    return {
        "status": "ok",
        "title": title[:85],
        "subtitle": subtitle[:130],
        "price_text": price[:35],
    }


@router.post("/v2/design-one")
def banner_v2_design_one(
    req: BannerV2DesignRequest,
    current_user=_DepSec(_CurUser),
):
    # Retired paid compositor. Active UI uses V4; stale callers cannot spend here.
    return {
        "status": "error", "code": "legacy_banner_stage_retired",
        "detail": "Старый дизайн баннера отключён. Используйте V4.",
        "safe_endpoint": "/api/banners/v4/design-one",
    }
    import time as _time

    from app.services.banner_design_v2 import (
        compose_banner as _compose_banner,
    )
    from app.services.banner_media import (
        register_banner as _register_banner,
    )

    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (
        req.description or ""
    ).strip()

    title = (
        req.title or ""
    ).strip()

    if not description:
        raise HTTPException(
            400,
            "Расскажите о предложении",
        )

    if not title:
        raise HTTPException(
            400,
            "Проверьте заголовок",
        )

    folder = _account_banner_dir(
        account_id,
        "infographic",
    )

    fname = (
        "design_v2_"
        + str(int(_time.time()))
        + ".png"
    )

    fpath = folder + "/" + fname

    try:
        design = _compose_banner(
            account_id=account_id,
            description=description,
            goal=req.goal,
            title=title,
            subtitle=req.subtitle,
            price_text=req.price_text,
            accent_color=req.accent_color,
            output_path=fpath,
        )

    except Exception as exc:
        print(
            "[banner_v2_design]",
            repr(exc)[:300],
            flush=True,
        )

        raise HTTPException(
            500,
            "Не удалось создать баннер",
        )

    _save_banner_record(
        account_id,
        fname,
        "infographic",
        title[:60],
        description[:300],
        "design_v2",
        "infographic",
    )

    media_result = None

    try:
        media_result = _register_banner(
            account_id,
            fpath,
        )
    except Exception as exc:
        print(
            "[banner_v2_design] media registration failed:",
            repr(exc)[:300],
            flush=True,
        )

    url = (
        BASE_URL
        + "/"
        + account_id
        + "/banners/infographic/"
        + fname
    )

    return {
        "status": "ok",
        "url": url,
        "media": media_result,
        "art_direction": design.get(
            "art_direction"
        ),
        "message": "Баннер готов",
    }


# ===== BORIS BANNER V3 STAGE D =====

# ===== BORIS BANNER V3 STAGE D =====

class BannerV3CopyRequest(BaseModel):
    account_id: str
    goal: str = "offer"
    description: str


class BannerV3DesignRequest(BaseModel):
    account_id: str
    goal: str = "offer"
    description: str
    title: str
    subtitle: str = ""
    advantages: list[str] = []
    price_text: str = ""
    accent_color: str = "#2F6FED"


@router.post("/v3/copy")
def banner_v3_copy(
    req: BannerV3CopyRequest,
    current_user=_DepSec(_CurUser),
):
    # Retired stage endpoint. Active UI uses V4 copy; return before any paid text call.
    return {
        "status": "error", "code": "legacy_banner_stage_retired",
        "detail": "Старый этап копирайтинга отключён. Используйте V4.",
        "safe_endpoint": "/api/banners/v4/copy",
    }
    from app.services.banner_design_v3 import (
        normalize_copy as _v3_normalize_copy,
    )

    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (
        req.description or ""
    ).strip()

    if not description:
        raise HTTPException(
            400,
            "Расскажите о предложении",
        )

    goal_labels = {
        "offer": "коммерческое предложение",
        "price": "акцент на цене",
        "benefit": "главное преимущество",
    }

    prompt_context = (
        "Задача: "
        + goal_labels.get(
            req.goal,
            "коммерческое предложение",
        )
        + ". "
        + description
    )

    generated = _generate_marketing_copy(
        prompt_context,
        account_id=account_id,
        operation="banner_v3_copy",
    )

    result = _v3_normalize_copy(
        description=description,
        goal=req.goal,
        generated=generated,
    )

    return {
        "status": "ok",
        **result,
    }


@router.post("/v3/design-one")
def banner_v3_design_one(
    req: BannerV3DesignRequest,
    current_user=_DepSec(_CurUser),
):
    # Retired paid compositor. V4 is the only interactive design surface.
    return {
        "status": "error", "code": "legacy_banner_stage_retired",
        "detail": "Старый дизайн баннера отключён. Используйте V4.",
        "safe_endpoint": "/api/banners/v4/design-one",
    }
    import time as _time

    from app.services.banner_design_v3 import (
        compose_banner_v3 as _v3_compose,
    )

    from app.services.banner_media import (
        register_banner as _register_banner,
    )

    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (
        req.description or ""
    ).strip()

    title = (
        req.title or ""
    ).strip()

    if not description:
        raise HTTPException(
            400,
            "Расскажите о предложении",
        )

    if not title:
        raise HTTPException(
            400,
            "Проверьте заголовок",
        )

    # Fail-closed лимиты клиентского текста.
    if len(title.split()) > 7:
        raise HTTPException(
            422,
            "Заголовок слишком длинный",
        )

    if len(title) > 62:
        raise HTTPException(
            422,
            "Заголовок слишком длинный",
        )

    subtitle = (
        req.subtitle or ""
    ).strip()

    if len(subtitle.split()) > 9:
        raise HTTPException(
            422,
            "Дополнительная строка слишком длинная",
        )

    advantages = [
        str(x).strip()
        for x in (
            req.advantages or []
        )
        if str(x).strip()
    ][:3]

    folder = _account_banner_dir(
        account_id,
        "infographic",
    )

    fname = (
        "design_v3_"
        + str(
            int(
                _time.time()
            )
        )
        + ".png"
    )

    fpath = (
        folder
        + "/"
        + fname
    )

    try:
        result = _v3_compose(
            account_id=account_id,
            description=description,
            goal=req.goal,
            title=title,
            subtitle=subtitle,
            advantages=advantages,
            price_text=req.price_text,
            accent_color=req.accent_color,
            output_path=fpath,
        )

    except Exception as exc:
        print(
            "[banner_v3]",
            repr(exc)[:400],
            flush=True,
        )

        raise HTTPException(
            500,
            "Не удалось создать баннер",
        )

    _save_banner_record(
        account_id,
        fname,
        "infographic",
        title[:60],
        description[:300],
        "design_v3",
        "infographic",
    )

    media_result = None

    try:
        media_result = _register_banner(
            account_id,
            fpath,
        )

    except Exception as exc:
        print(
            "[banner_v3] media registration failed:",
            repr(exc)[:300],
            flush=True,
        )

    url = (
        BASE_URL
        + "/"
        + account_id
        + "/banners/infographic/"
        + fname
    )

    return {
        "status": "ok",
        "url": url,
        "media": media_result,
        "layout": result.get(
            "layout"
        ),
        "message": "Баннер готов",
    }


# ===== BORIS BANNER V4 STAGE E =====

# ===== BORIS BANNER V4 STAGE E =====

class BannerV4CopyRequest(BaseModel):
    account_id: str
    goal: str = "offer"
    description: str


class BannerV4DesignRequest(BaseModel):
    account_id: str
    goal: str = "offer"
    description: str
    title: str
    subtitle: str = ""
    advantages: list[str] = []
    price_text: str = ""
    accent_color: str = "#2F6FED"
    idempotency_key: str = ""


@router.post("/v4/copy")
def banner_v4_copy(
    req: BannerV4CopyRequest,
    current_user=_DepSec(_CurUser),
):
    from app.services.banner_design_v4 import (
        normalize_copy_v4 as _normalize_v4,
    )

    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (
        req.description
        or ""
    ).strip()

    if not description:
        raise HTTPException(
            400,
            "Расскажите о предложении",
        )

    goal_labels = {
        "offer": "главное предложение",
        "price": "акцент на цене",
        "benefit": "главное преимущество",
    }

    context = (
        "Подготовь очень короткий текст для рекламного баннера. "
        "Не повторяй всё описание. "
        "Заголовок максимум 6 слов. "
        "Уточнение максимум 8 слов. "
        "Преимущества только из фактов пользователя. "
        "Не придумывай цены, сроки, гарантии и факты. "
        "Задача: "
        + goal_labels.get(
            req.goal,
            "главное предложение",
        )
        + ". Исходные факты: "
        + description
    )

    generated = _generate_marketing_copy(
        context,
        account_id=account_id,
        operation="banner_v4_copy",
    )

    normalized = _normalize_v4(
        description=description,
        goal=req.goal,
        generated=generated,
    )

    return {
        "status": "ok",
        **normalized,
    }


@router.post("/v4/design-one")
def banner_v4_design_one(
    req: BannerV4DesignRequest,
    current_user=_DepSec(_CurUser),
):
    import time as _time

    from app.services.banner_design_v4 import (
        compose_banner_v4 as _compose_v4,
    )

    from app.services.banner_media import (
        register_banner as _register_banner,
    )

    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (
        req.description
        or ""
    ).strip()

    title = (
        req.title
        or ""
    ).strip()

    subtitle = (
        req.subtitle
        or ""
    ).strip()

    if not description:
        raise HTTPException(
            400,
            "Расскажите о предложении",
        )

    if not title:
        raise HTTPException(
            400,
            "Проверьте заголовок",
        )

    if len(
        title.split()
    ) > 6:
        raise HTTPException(
            422,
            "Заголовок должен быть короче",
        )

    if len(title) > 60:
        raise HTTPException(
            422,
            "Заголовок должен быть короче",
        )

    if len(
        subtitle.split()
    ) > 8:
        raise HTTPException(
            422,
            "Уточнение должно быть короче",
        )

    advantages = [
        str(x).strip()
        for x
        in (
            req.advantages
            or []
        )
        if str(x).strip()
    ][:3]

    idem = str(req.idempotency_key or "").strip()
    if not idem:
        return {
            "status": "idempotency_required",
            "code": "paid_idempotency_required",
            "detail": "Для платной генерации нужен ключ безопасного повтора. Обновите экран и запустите ещё раз.",
        }

    folder = _account_banner_dir(
        account_id,
        "infographic",
    )

    import hashlib as _hashlib_v4
    _intent = _hashlib_v4.sha256((account_id + "|v4-design|" + idem).encode("utf-8", "ignore")).hexdigest()[:24]
    fname = "design_v4_idem_" + _intent + ".png"
    fpath = folder + "/" + fname

    # Exact replay of a completed request returns the already-created artifact
    # before any paid background generation. This is the last-line browser retry
    # guard above ImageGenerationService's own provider idempotency/cache.
    try:
        from pathlib import Path as _V4Path
        _existing = _V4Path(fpath)
        if _existing.is_file() and _existing.stat().st_size > 1024:
            with _existing.open("rb") as _fh:
                _sig = _fh.read(8)
            if _sig == b"\x89PNG\r\n\x1a\n":
                _media_existing = _register_banner(
                    account_id=account_id,
                    absolute_path=fpath,
                    source_provider="openai_visual_local_text",
                    source_ref="banner_v4_design_idempotent",
                )
                _url_existing = "/images/" + str(_media_existing["storage_key"]).lstrip("/")
                return {
                    "status": "ok", "url": _url_existing,
                    "media": _media_existing, "layout": "v4_local_text",
                    "message": "Баннер готов", "idempotency_replay": True,
                }
    except OSError:
        pass

    try:
        result = _compose_v4(
            account_id=account_id,
            description=description,
            goal=req.goal,
            title=title,
            subtitle=subtitle,
            advantages=advantages,
            price_text=req.price_text,
            accent_color=req.accent_color,
            output_path=fpath,
        )

    except Exception as exc:
        print(
            "[banner_v4]",
            repr(exc)[:500],
            flush=True,
        )

        raise HTTPException(
            500,
            "Не удалось создать баннер",
        )

    _save_banner_record(
        account_id,
        fname,
        "infographic",
        title[:60],
        description[:300],
        "design_v4",
        "infographic",
    )

    try:
        media_result = _register_banner(
            account_id=account_id,
            absolute_path=fpath,
            source_provider="openai_visual_local_text",
            source_ref="banner_v4_design_idempotent",
        )
    except Exception as exc:
        print(
            "[banner_v4] media registration failed:",
            repr(exc)[:400],
            flush=True,
        )
        raise HTTPException(500, "Баннер создан, но не прошёл регистрацию в медиатеке")

    url = "/images/" + str(media_result["storage_key"]).lstrip("/")

    return {
        "status": "ok",
        "url": url,
        "media": media_result,

        # Внутреннее значение:
        # текущий frontend его не показывает.
        "design_reference": result.get(
            "reference"
        ),

        "message": "Баннер готов",
    }


# ============================================================================
# BANNER V4 / STAGE E
# Клиентский поток:
#   1. prepare — только бесплатная подготовка концепций;
#   2. create-one — одна реальная генерация выбранного варианта.
#
# Установочный код ничего не генерирует.
# ============================================================================

class BannerV4PrepareRequest(BaseModel):
    account_id: str
    description: str


class BannerV4CreateRequest(BaseModel):
    account_id: str
    description: str
    direction: str = "architecture"
    accent_color: str = "#2F6FED"
    idempotency_key: str = ""


_BANNER_V4_DIRECTIONS = {
    "architecture": {
        "title": "Архитектурный стиль",
        "subtitle": "Чисто, дорого и профессионально",
        "description": (
            "Светлая современная композиция, аккуратная архитектурная "
            "сцена, ощущение качества и порядка. Главный акцент — "
            "профессиональный результат услуги."
        ),
    },
    "result": {
        "title": "Результат крупным планом",
        "subtitle": "Показываем качество работы",
        "description": (
            "Крупный выразительный кадр результата работы, фактура и "
            "качество поверхности на первом плане. Минимум отвлекающих "
            "деталей, сильный визуальный акцент."
        ),
    },
    "interior": {
        "title": "Современный интерьер",
        "subtitle": "Услуга в реальной жизни",
        "description": (
            "Стильный современный интерьер, естественный свет, "
            "реалистичная квартира или дом. Услуга органично показана "
            "в готовом пространстве."
        ),
    },
}


def avito_safe_banner_replay_available(account_id: str, idempotency_key: str, direction: str = "architecture") -> bool:
    """Cheap/no-provider preflight: is this exact child intent already materialized?

    This never trusts the presence of a DB row alone: the deterministic final file
    must exist and have a real PNG signature. Payload conflicts are still checked
    by create_avito_safe_banner under the cross-process intent lock.
    """
    import hashlib as _hashlib
    from pathlib import Path as _Path
    account_id = str(account_id or "").strip()
    idem = str(idempotency_key or "").strip()
    if not account_id or not idem:
        return False
    chosen = direction if direction in _BANNER_V4_DIRECTIONS else "architecture"
    intent = _hashlib.sha256((account_id + "|" + idem).encode("utf-8", "ignore")).hexdigest()[:24]
    path = _Path(IMAGES_DIR) / account_id / "banners" / "v4" / ("banner_v4_avito_idem_%s_%s.png" % (intent, chosen))
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return False
        with path.open("rb") as fh:
            return fh.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def create_avito_safe_banner(account_id: str, description: str, direction: str = "architecture", accent_color: str = "#2F6FED", exact_title: str = "", exact_subtitle: str = "", exact_price: str = "", idempotency_key: str = "") -> dict:
    """Canonical internal Avito banner producer.

    Avito must never depend on an AI model to render visible copy.  The paid
    image layer generates only a photographic background; all Russian text is
    composed locally by banner_design_v4 with deterministic fonts, wrapping,
    safe zones and mobile-readable sizing.  Internal workers use this helper
    instead of the legacy /full_ai route.
    """
    import time as _time
    from pathlib import Path as _Path
    from app.services.banner_design_v4 import compose_banner_v4 as _compose_banner
    from app.services.banner_media import register_banner as _register_banner

    account_id = str(account_id or "").strip()
    description = str(description or "").strip()[:4000]
    if not account_id:
        raise ValueError("account_id required")
    if not description:
        raise ValueError("description required")

    chosen = direction if direction in _BANNER_V4_DIRECTIONS else "architecture"
    art = _BANNER_V4_DIRECTIONS[chosen]

    # Stable caller idempotency for browser/network retries. The final local
    # artifact path is deterministic; a completed valid PNG is returned before
    # any copy/image provider call. Internal workers may omit the key and keep
    # their existing explicit-operation semantics.
    import hashlib as _hashlib
    idem = str(idempotency_key or "").strip()
    root = _Path(IMAGES_DIR) / account_id / "banners" / "v4"
    root.mkdir(parents=True, exist_ok=True)
    _idem_lock_fh = None
    if idem:
        import fcntl as _fcntl, json as _idem_json, os as _idem_os
        intent = _hashlib.sha256((account_id + "|" + idem).encode("utf-8", "ignore")).hexdigest()[:24]
        fname = "banner_v4_avito_idem_%s_%s.png" % (intent, chosen)
        _intent_dir = _Path("/root/BORIS/backend/run/banner_idempotency")
        _intent_dir.mkdir(parents=True, exist_ok=True)
        _idem_lock_fh = (_intent_dir / (intent + ".lock")).open("a+")
        _fcntl.flock(_idem_lock_fh.fileno(), _fcntl.LOCK_EX)
        _request_hash = _hashlib.sha256(_idem_json.dumps({
            "account_id": account_id, "description": description, "direction": chosen,
            "accent_color": str(accent_color or ""), "exact_title": str(exact_title or ""),
            "exact_subtitle": str(exact_subtitle or ""), "exact_price": str(exact_price or ""),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        _intent_meta = _intent_dir / (intent + ".json")
        _intent_record = {}
        if _intent_meta.is_file():
            try:
                _intent_record = _idem_json.loads(_intent_meta.read_text(encoding="utf-8"))
            except Exception:
                _intent_record = {}
            if str(_intent_record.get("request_hash") or "") != _request_hash:
                _fcntl.flock(_idem_lock_fh.fileno(), _fcntl.LOCK_UN); _idem_lock_fh.close()
                return {
                    "status": "error", "code": "idempotency_conflict",
                    "detail": "Этот ключ безопасного повтора уже использован для другого содержимого баннера.",
                }
        else:
            _tmp_meta = _intent_dir / (intent + ".json.tmp." + str(_idem_os.getpid()))
            _intent_record = {"request_hash": _request_hash, "created_at": _time.time()}
            _tmp_meta.write_text(_idem_json.dumps(_intent_record, ensure_ascii=False), encoding="utf-8")
            _idem_os.replace(_tmp_meta, _intent_meta)
    else:
        fname = "banner_v4_avito_%s_%s.png" % (int(_time.time() * 1000), chosen)
    output_path = root / fname

    if idem and output_path.is_file() and output_path.stat().st_size > 1024:
        try:
            with output_path.open("rb") as _fh:
                _sig = _fh.read(8)
            if _sig == b"\x89PNG\r\n\x1a\n":
                media_result = _register_banner(
                    account_id=account_id,
                    absolute_path=str(output_path),
                    source_provider="openai_visual_local_text",
                    source_ref="avito_safe_v4_local_text",
                )
                canonical_url = "/images/" + str(media_result["storage_key"]).lstrip("/")
                if _idem_lock_fh is not None:
                    _fcntl.flock(_idem_lock_fh.fileno(), _fcntl.LOCK_UN); _idem_lock_fh.close()
                return {
                    "status": "ok", "url": canonical_url, "urls": [canonical_url],
                    "media_id": media_result["id"], "storage_key": media_result["storage_key"],
                    "layout": "v4_local_text", "avito_safe": True,
                    "copy": (_intent_record.get("copy") or {"title": str(exact_title or ""), "subtitle": str(exact_subtitle or ""), "advantages": [], "price_text": str(exact_price or "")}),
                    "idempotency_replay": True,
                }
        except OSError:
            pass

    copy = _generate_marketing_copy(
        description,
        account_id=account_id,
        operation="banner_avito_safe_copy",
    )
    title = str(exact_title or copy.get("title") or description[:60]).strip()[:70]
    subtitle = str(exact_subtitle or copy.get("subtitle") or "").strip()[:90]
    advantages = copy.get("advantages") or []
    if not isinstance(advantages, list):
        advantages = []
    advantages = [str(x).strip()[:55] for x in advantages[:3] if str(x).strip()]
    price_text = str(exact_price or copy.get("price_text") or "").strip()[:35]
    design_description = (
        description
        + "\n\nВЫБРАННАЯ КОНЦЕПЦИЯ:\n"
        + art["description"]
        + "\n\nAVITO MOBILE CONTRACT: один главный визуальный объект; крупная типографика; "
          "не более трёх коротких преимуществ; никаких мелких таблиц, UI-карточек, "
          "коллажей, псевдо-скриншотов, наклонного текста и текста, встроенного AI в фон."
    )
    result = _compose_banner(
        account_id=account_id,
        description=design_description,
        goal="offer",
        title=title,
        subtitle=subtitle,
        advantages=advantages,
        price_text=price_text,
        accent_color=accent_color,
        output_path=str(output_path),
    )
    result_path = result.get("path") if isinstance(result, dict) else result
    if not result_path:
        raise RuntimeError("safe Avito banner generation failed")
    media_result = _register_banner(
        account_id=account_id,
        absolute_path=str(result_path),
        source_provider="openai_visual_local_text",
        source_ref="avito_safe_v4_local_text",
    )
    canonical_url = "/images/" + str(media_result["storage_key"]).lstrip("/")
    if idem:
        _intent_record.update({
            "request_hash": _request_hash, "completed_at": _time.time(),
            "copy": {"title": title, "subtitle": subtitle, "advantages": advantages, "price_text": price_text},
            "storage_key": str(media_result["storage_key"]), "media_id": media_result["id"],
        })
        _tmp_meta = _intent_dir / (intent + ".json.tmp." + str(_idem_os.getpid()))
        _tmp_meta.write_text(_idem_json.dumps(_intent_record, ensure_ascii=False), encoding="utf-8")
        _idem_os.replace(_tmp_meta, _intent_meta)
    if _idem_lock_fh is not None:
        _fcntl.flock(_idem_lock_fh.fileno(), _fcntl.LOCK_UN); _idem_lock_fh.close()
    return {
        "status": "ok",
        "url": canonical_url,
        "urls": [canonical_url],
        "media_id": media_result["id"],
        "storage_key": media_result["storage_key"],
        "layout": "v4_local_text",
        "avito_safe": True,
        "copy": {"title": title, "subtitle": subtitle, "advantages": advantages, "price_text": price_text},
    }


@router.post("/v4/prepare")
def banner_v4_prepare(
    req: BannerV4PrepareRequest,
    current_user=_DepSec(_CurUser),
):
    """
    Бесплатная подготовка баннера.

    Никакой генерации изображения здесь нет.
    Возвращаются только три дизайнерские концепции.
    """

    account_id = _banner_v2_account_access(
        req.account_id,
        current_user,
    )

    description = (req.description or "").strip()

    if not description:
        raise HTTPException(
            status_code=400,
            detail="Добавьте описание услуги",
        )

    if len(description) > 4000:
        description = description[:4000]

    return {
        "status": "ok",
        "account_id": account_id,
        "directions": [
            {
                "code": code,
                "title": data["title"],
                "subtitle": data["subtitle"],
                "description": data["description"],
            }
            for code, data in _BANNER_V4_DIRECTIONS.items()
        ],
    }


@router.post("/v4/create-one")
def banner_v4_create_one(
    req: BannerV4CreateRequest,
    current_user=_DepSec(_CurUser),
):
    """Canonical Stage E: one paid banner with durable caller idempotency."""
    account_id = _banner_v2_account_access(req.account_id, current_user)
    description = str(req.description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Добавьте описание услуги")

    idem = str(req.idempotency_key or "").strip()
    if not idem:
        return {
            "status": "idempotency_required",
            "code": "paid_idempotency_required",
            "detail": "Для платной генерации нужен ключ безопасного повтора. Обновите экран и запустите ещё раз.",
        }

    result = create_avito_safe_banner(
        account_id=account_id,
        description=description,
        direction=str(req.direction or "architecture"),
        accent_color=str(req.accent_color or "#2F6FED"),
        idempotency_key=idem,
    )
    if result.get("status") == "ok":
        result["direction"] = str(req.direction or "architecture")
    return result
