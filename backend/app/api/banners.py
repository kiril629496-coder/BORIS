from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import os
import json as _json

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



def _generate_marketing_copy(raw_description: str) -> dict:
    """Через GigaChat превращает сырое описание бизнеса от клиента в короткие
    продающие заголовок/подзаголовок/цену/преимущества для баннера."""
    try:
        from gigachat_pool import chat_with_fallback
        from gigachat.models import Messages, MessagesRole

        prompt = f'''Ты опытный маркетолог-копирайтер рекламных баннеров. На основе описания бизнеса от клиента придумай КОРОТКИЙ текст для рекламного баннера.
ВАЖНО: используй ТОЛЬКО факты, явно упомянутые в описании клиента. НИКОГДА не выдумывай гарантии/скидки/бесплатные услуги, которых там нет.
Ответь СТРОГО в формате JSON без markdown-разметки, без пояснений:
{{"title": "короткий цепляющий заголовок, до 4 слов, без капса", "subtitle": "короткое уточнение до 5 слов или пустая строка", "price_text": "краткая цена-предложение если есть в описании, иначе пустая строка", "advantages": ["преимущество 1", "преимущество 2", "преимущество 3"]}}

Описание бизнеса от клиента: {raw_description}'''

        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)]).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = _json.loads(raw)
        return {
            "title": data.get("title", raw_description[:40]),
            "subtitle": data.get("subtitle", ""),
            "price_text": data.get("price_text", ""),
            "advantages": data.get("advantages", [])[:3],
        }
    except Exception as e:
        print(f"[_generate_marketing_copy] Ошибка, используем сырой текст: {e}", flush=True)
        return {"title": raw_description[:40], "subtitle": "", "price_text": "", "advantages": []}

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
    ai_model: str = "gpt-image-2"


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
def create_infographic(req: InfographicRequest):
    from banner_generator import generate_infographic_banner
    import time as _time

    folder = _account_banner_dir(req.account_id, "infographic")
    fname = f"infographic_{int(_time.time())}.png"
    fpath = f"{folder}/{fname}"

    # Если заголовок пришёл как сырое длинное описание бизнеса (а не готовый короткий текст) —
    # прогоняем через копирайтера GigaChat, чтобы получить продающий текст, а не сырой набор фактов
    title = req.title
    subtitle = req.subtitle
    price_text = req.price_text
    if len(req.title) > 45 and not req.subtitle:
        copy = _generate_marketing_copy(req.title)
        title = copy["title"]
        subtitle = copy["subtitle"]
        price_text = copy["price_text"] or req.price_text

    own_photo_path = None
    if req.own_photo_url:
        try:
            _rel = req.own_photo_url.split("/images/")[-1]
            _candidate = f"{IMAGES_DIR}/{_rel}"
            if os.path.isfile(_candidate):
                own_photo_path = _candidate
        except Exception:
            own_photo_path = None

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
        photo_source=req.photo_source,
        ai_quality=req.ai_quality,
        ai_model=req.ai_model,
        own_photo_path=own_photo_path,
    )

    _theme = _slugify_theme(req.title)
    _save_banner_record(req.account_id, fname, "infographic", req.title, f"{req.title} | {req.subtitle or ''}", "pillow", "infographic")
    return {"status": "ok", "url": f"{BASE_URL}/{req.account_id}/banners/infographic/{fname}"}


@router.post("/diagonal")
def create_diagonal(req: DiagonalRequest):
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
        photo_source=req.photo_source,
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
    count: int = 1  # сколько РАЗНЫХ вариантов баннера сгенерировать за один запрос (макс 5)
    vary_colors: bool = False    # варьировать цветовую палитру между вариантами
    vary_image: bool = False     # варьировать фоновую картинку/сцену между вариантами
    vary_icons: bool = False     # варьировать иконки/бейджи между вариантами
    vary_all: bool = False       # варьировать полностью всё (композиция, текст, цвет, сцена)


def _describe_reference_style(image_base64: str) -> str:
    """Через OpenAI Vision описывает стиль/композицию референс-фото словами,
    чтобы использовать это описание как стилевой ориентир для генерации."""
    try:
        import requests
        import os
        from proxy_pool import get_intl_requests_proxies

        api_key = os.getenv("OPENAI_API_KEY")

        for _attempt in range(1, 4):
            proxies = get_intl_requests_proxies()
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Опиши визуальный стиль, композицию, цветовую палитру и расположение элементов на этом рекламном изображении, чтобы использовать это описание как ориентир для генерации похожего дизайна другой картинки. Пиши на английском, компактно, только по делу, без лишних слов."},
                                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_base64}},
                            ],
                        }],
                        "max_tokens": 300,
                    },
                    proxies=proxies,
                    timeout=60,
                )
                if resp.status_code != 200:
                    print("[_describe_reference_style] Попытка " + str(_attempt) + ": статус " + str(resp.status_code), flush=True)
                    continue
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as _e:
                print("[_describe_reference_style] Попытка " + str(_attempt) + " не удалась: " + str(_e), flush=True)
                continue
        return ""
    except Exception as e:
        print("[_describe_reference_style] Ошибка: " + str(e), flush=True)
        return ""


def _describe_reference_style_multi(image_urls: list) -> str:
    """Как _describe_reference_style, но принимает до 3 прямых URL уже готовых баннеров
    (без base64) и просит модель выделить ОБЩИЙ стиль между ними как единый ориентир."""
    try:
        import requests
        import os
        from proxy_pool import get_intl_requests_proxies

        api_key = os.getenv("OPENAI_API_KEY")
        # OpenAI Vision скачивает картинку САМ по этому URL со своих серверов - относительный
        # путь вида "/images/..." (как отдают browse_all_banners/showcase) для него не URL,
        # а мусор: "Failed to download image ... Image URL is invalid." Достраиваем домен.
        def _absolute_url(u: str) -> str:
            if u.startswith("http://") or u.startswith("https://"):
                return u
            return "https://boris-ai.pro" + (u if u.startswith("/") else "/" + u)

        content_blocks = [{"type": "text", "text": "Ниже приведено до 3 примеров рекламных баннеров, которые нравятся владельцу как эталон стиля. Опиши ОБЩИЙ визуальный стиль, композицию, цветовую палитру и расположение элементов, объединяющий эти примеры, чтобы использовать это описание как ориентир для генерации похожего дизайна другого баннера. Пиши на английском, компактно, только по делу, без лишних слов."}]
        for url in image_urls[:3]:
            content_blocks.append({"type": "image_url", "image_url": {"url": _absolute_url(url)}})

        for _attempt in range(1, 4):
            proxies = get_intl_requests_proxies()
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": content_blocks}], "max_tokens": 300},
                    proxies=proxies,
                    timeout=60,
                )
                if resp.status_code != 200:
                    print("[_describe_reference_style_multi] Попытка " + str(_attempt) + ": статус " + str(resp.status_code) + " - " + resp.text[:500], flush=True)
                    continue
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as _e:
                print("[_describe_reference_style_multi] Попытка " + str(_attempt) + " не удалась: " + str(_e), flush=True)
                continue
        return ""
    except Exception as e:
        print("[_describe_reference_style_multi] Ошибка: " + str(e), flush=True)
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


@router.post("/full_ai")
def create_full_ai_banner(req: FullAiRequest):
    from banner_generator import generate_ai_image
    import time as _time

    size_map = {
        "infographic": ("1024x1024", "infographic"),
        "extended_pc": ("1536x1024", "extended"),
        "extended_mobile": ("1024x1536", "extended"),
        "max_pc": ("1536x1024", "max_carousel"),
        "max_mobile": ("1024x1536", "max_carousel"),
    }
    size, subfolder = size_map.get(req.format, ("1024x1024", "infographic"))

    target_dims = {
        "extended_pc": (1202, 436),
        "extended_mobile": (1242, 936),
        "max_pc": (1304, 480),
        "max_mobile": (1420, 960),
    }

    def _safe_zone_percent(fmt, gen_size_str):
        """Реальный % высоты, переживающий smart-crop (вместо угадывания 55% для всех форматов).
        Мобильные форматы обрезаются сильнее (портретный источник -> альбомная цель), поэтому
        считаем честно под каждый конкретный формат, с запасом 15% на неточность ИИ-художника."""
        if fmt not in target_dims:
            return 55
        target_w, target_h = target_dims[fmt]
        src_w, src_h = [int(x) for x in gen_size_str.split("x")]
        scale = max(target_w / src_w, target_h / src_h)
        new_h = src_h * scale
        kept_fraction = target_h / new_h
        safe_percent = int(kept_fraction * 100 * 0.85)
        return max(30, min(safe_percent, 90))

    safe_percent = _safe_zone_percent(req.format, size)

    if req.exact_text:
        copy = {"title": req.exact_text, "subtitle": "", "price_text": "", "advantages": []}
    else:
        copy = _generate_marketing_copy(req.raw_description)

    style_reference_text = ""
    if req.reference_image_urls:
        style_desc = _describe_reference_style_multi(req.reference_image_urls)
        if style_desc:
            style_reference_text = "\n\nStyle reference (match this visual style/layout/palette as closely as possible, based on chosen example banners): " + style_desc
    elif req.reference_image_base64:
        style_desc = _describe_reference_style(req.reference_image_base64)
        if style_desc:
            style_reference_text = "\n\nStyle reference (match this visual style/layout/palette as closely as possible): " + style_desc

    advantages_text = ""
    if copy["advantages"]:
        adv_lines = []
        for a in copy["advantages"]:
            adv_lines.append("- circular icon with label: \"" + a + "\"")
        adv_joined = "\n".join(adv_lines)
        advantages_text = "\nBelow the subtitle, show a row of small circular icon badges with short labels, one per line:\n" + adv_joined

    is_wide_crop_format = req.format in ("extended_pc", "extended_mobile", "max_pc", "max_mobile")

    price_block = ""
    if copy["price_text"]:
        if is_wide_crop_format:
            price_block = "\nWithin the central horizontal band (NOT at the extreme top edge), a rounded badge with the exact text: \"" + copy["price_text"] + "\""
        else:
            price_block = "\nIn the top corner, a rounded badge with the exact text: \"" + copy["price_text"] + "\""

    subtitle_block = ""
    if copy["subtitle"]:
        subtitle_block = "\nBelow it, smaller, write this Russian subtitle: \"" + copy["subtitle"] + "\""

    contact_block = ""
    if req.include_contacts and (req.phone or req.address):
        contact_parts = " | ".join([p for p in [req.phone, req.address] if p])
        if is_wide_crop_format:
            contact_block = "\nWithin the central horizontal band (NOT at the extreme bottom edge), a solid color contact bar with phone icon and exact text: \"" + contact_parts + "\""
        else:
            contact_block = "\nAt the very bottom, a solid color contact bar with phone icon and exact text: \"" + contact_parts + "\""

    def _make_title_line(text, wide_crop):
        if wide_crop:
            return ("In bold modern font, write exactly this Russian text as the main headline: \"" + text + "\". "
                    "CRITICAL: this banner will be cropped to a WIDE strip — the LEFT and RIGHT edges of the square image WILL BE CUT OFF. "
                    "Therefore place ALL headline text strictly in the CENTRAL area, both vertically AND horizontally, "
                    "keeping a wide safe margin (at least 20% of width) from the left and right edges, so NO letter or word gets cropped. "
                    "Keep the entire headline on as few lines as possible and never let any word touch or exceed the central safe zone.")
        return "At the top, in bold modern font, write exactly this Russian text as the main headline: \"" + text + "\""

    title_line = _make_title_line(copy["title"], is_wide_crop_format)

    full_prompt = (
        "Create a professional, vivid advertising banner in Russian, in the style of premium modern advertising campaigns "
        "(bold colors, dynamic composition, high production value, NOT a plain stock photo).\n\n"
        "Main accent color theme: " + req.accent_color + " and complementary dark/neutral tones.\n\n"
        + title_line
        + subtitle_block
        + advantages_text
        + price_block
        + contact_block
        + "\n\nMAIN SUBJECT — render the key object EXACTLY as described, matching type, color, size, number of axles/wheels and any special equipment mentioned; do NOT substitute a generic stock version. Business/product description: " + req.raw_description
        + style_reference_text
        + "\n\nStyle: clean layout, safe margins from all edges, no watermarks, no random extra text, "
        "professional advertising photography lighting, composition matching a " + size + " canvas. "
        "IMPORTANT LAYOUT CONSTRAINT: since this image will later be cropped to a wide/tall banner strip, "
        "keep ALL important content (text, logo, icons, key visual elements) strictly within the CENTRAL "
        "horizontal band (middle " + str(safe_percent) + "% of the image height), leaving the top and bottom margins as simple "
        "background/scene continuation with no critical text or logo elements there.\n\n"
        "CRITICAL TEXT RULES: Every word of text must be COMPLETELY visible and fully readable, with generous "
        "padding on all sides — NEVER let any letter, word, or line get cut off, cropped, or run off the edge "
        "of the image. If the text seems too long, make the font smaller rather than letting it overflow. "
        "Leave clear empty space around every text block so nothing touches the image border.\n\n"
        "CRITICAL ORIENTATION RULE: The entire banner must be composed perfectly level and straight — "
        "horizontal lines (text baselines, horizon, table edges, badge rows) must be exactly horizontal "
        "(0 degrees), and vertical elements must be exactly vertical (90 degrees). Do NOT tilt, rotate, "
        "or skew the overall composition or the camera angle even slightly — no Dutch angle, no diagonal framing."
    )

    is_carousel = req.format in ("max_pc", "max_mobile")

    if is_carousel:
        # У каждого слайда СВОЙ набор текстовых блоков — карусель не должна показывать
        # один и тот же заголовок трижды, иначе слайды выглядят как копии друг друга.
        subtitle_for_slide2 = subtitle_block if copy["subtitle"] else ""

        slide1_text = title_line + price_block
        slide1_focus = "\n\nThis is slide 1 of 3 in a carousel: the MAIN OFFER slide, headline and price badge are the visual center of attention."

        slide2_text = advantages_text + subtitle_for_slide2
        slide2_focus = "\n\nThis is slide 2 of 3 in a carousel: the ADVANTAGES/BENEFITS slide — do NOT repeat the main headline text from slide 1, instead show only the benefit icon badges prominently, slightly different camera angle or scene detail than a generic hero shot."

        slide3_text = contact_block if contact_block else _make_title_line(copy["title"], is_wide_crop_format)
        slide3_focus = "\n\nThis is slide 3 of 3 in a carousel: a CALL TO ACTION / trust-building close-up slide — do NOT repeat the exact headline text from slide 1, use a warmer, more personal framing of the scene, still matching the same brand style and colors."

        base_prefix = (
            "Create a professional, vivid advertising banner in Russian, in the style of premium modern advertising campaigns "
            "(bold colors, dynamic composition, high production value, NOT a plain stock photo).\n\n"
            "Main accent color theme: " + req.accent_color + " and complementary dark/neutral tones.\n\n"
        )
        base_suffix = (
            "\n\nBackground scene: relevant photorealistic imagery matching this business description: " + req.raw_description
            + style_reference_text
            + "\n\nStyle: clean layout, safe margins from all edges, no watermarks, no random extra text, "
            "professional advertising photography lighting, composition matching a " + size + " canvas. "
            "IMPORTANT LAYOUT CONSTRAINT: since this image will later be cropped to a wide/tall banner strip, "
            "keep ALL important content (text, logo, icons, key visual elements) strictly within the CENTRAL "
            "horizontal band (middle " + str(safe_percent) + "% of the image height), leaving the top and bottom margins as simple "
            "background/scene continuation with no critical text or logo elements there.\n\n"
            "CRITICAL TEXT RULES: Every word of text must be COMPLETELY visible and fully readable, with generous "
            "padding on all sides — NEVER let any letter, word, or line get cut off, cropped, or run off the edge "
            "of the image. If the text seems too long, make the font smaller rather than letting it overflow. "
            "Leave clear empty space around every text block so nothing touches the image border.\n\n"
            "CRITICAL ORIENTATION RULE: The entire banner must be composed perfectly level and straight — "
            "horizontal lines (text baselines, horizon, table edges, badge rows) must be exactly horizontal "
            "(0 degrees), and vertical elements must be exactly vertical (90 degrees). Do NOT tilt, rotate, "
            "or skew the overall composition or the camera angle even slightly — no Dutch angle, no diagonal framing."
        )

        slide_prompts = [
            base_prefix + slide1_text + base_suffix + slide1_focus,
            base_prefix + slide2_text + base_suffix + slide2_focus,
            base_prefix + slide3_text + base_suffix + slide3_focus,
        ]
    else:
        variant_count = max(1, min(req.count, 5))
        if variant_count == 1:
            slide_prompts = [full_prompt]
        else:
            # цветовые темы и сцены для вариаций - используются только если соответствующая галочка включена
            color_themes = ["warm orange/red tones", "cool blue/teal tones", "vibrant purple/pink tones",
                             "fresh green/lime tones", "elegant gold/black tones"]
            scene_variants = ["close-up product-centered shot", "wide establishing shot with more environment",
                               "dynamic action/movement shot", "minimalist clean studio shot", "lifestyle in-use shot"]
            icon_variants = ["circular badge icons", "square rounded-corner icons", "outline/line-style icons",
                              "filled solid-color icons", "no icons, text-only labels"]

            slide_prompts = []
            for i in range(variant_count):
                parts = ["\n\nVARIANT {idx} of {n}:".format(idx=i+1, n=variant_count)]
                if req.vary_all:
                    parts.append(" Make this variant COMPLETELY different from the others: different composition/camera angle ("
                                 + scene_variants[i % len(scene_variants)] + "), different color palette ("
                                 + color_themes[i % len(color_themes)] + "), different icon style ("
                                 + icon_variants[i % len(icon_variants)] + "), and rephrase all text with fresh wording (do not repeat exact phrasing from other variants).")
                else:
                    if req.vary_colors:
                        parts.append(" Use this color palette: " + color_themes[i % len(color_themes)] + ".")
                    if req.vary_image:
                        parts.append(" Use this scene/composition: " + scene_variants[i % len(scene_variants)] + ".")
                    if req.vary_icons:
                        parts.append(" Use this icon style: " + icon_variants[i % len(icon_variants)] + ".")
                    if not (req.vary_colors or req.vary_image or req.vary_icons):
                        # ничего не выбрано, но count>1 - минимальная защита от полных дублей
                        parts.append(" Use a slightly different composition/angle than the other variants (variant #" + str(i+1) + ").")
                slide_prompts.append(full_prompt + "".join(parts))

    folder = _account_banner_dir(req.account_id, subfolder)
    urls = []
    for idx, slide_prompt in enumerate(slide_prompts):
        suffix = ("_slide" + str(idx + 1)) if is_carousel else ""
        fname = "fullai_" + req.format + suffix + "_" + str(int(_time.time())) + "_" + str(idx) + ".png"
        fpath = folder + "/" + fname

        result = generate_ai_image(slide_prompt, fpath, size=size, quality=req.quality, model="gpt-image-2")
        if not result:
            if urls:
                break
            return {"status": "error", "detail": "Генерация не удалась, попробуйте ещё раз"}

        if req.format in target_dims:
            from PIL import Image as _Image
            target_w, target_h = target_dims[req.format]
            img = _Image.open(fpath).convert("RGB")
            src_w, src_h = img.size
            scale = max(target_w / src_w, target_h / src_h)
            new_w, new_h = int(src_w * scale), int(src_h * scale)
            img = img.resize((new_w, new_h), _Image.LANCZOS)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img = img.crop((left, top, left + target_w, top + target_h))
            img.save(fpath, "PNG", quality=95)

        urls.append(BASE_URL + "/" + req.account_id + "/banners/" + subfolder + "/" + fname)
        _theme_text = (copy.get("title") if isinstance(copy, dict) else None) or req.raw_description[:60]
        _save_banner_record(req.account_id, fname, subfolder, _theme_text, req.raw_description[:300], "full_ai", req.format)
        _theme_text = (copy.get("title") if isinstance(copy, dict) else None) or req.raw_description[:60]
        _save_banner_record(req.account_id, fname, subfolder, _theme_text, req.raw_description[:300], "full_ai", req.format)

    if not urls:
        return {"status": "error", "detail": "Генерация не удалась, попробуйте ещё раз"}

    if is_carousel or len(urls) > 1:
        return {"status": "ok", "urls": urls, "url": urls[0], "generated_copy": copy}
    return {"status": "ok", "url": urls[0], "generated_copy": copy}


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
        b64 = base64.b64encode(img.read()).decode()

    result = None
    for _attempt in range(1, 4):
        proxies = get_intl_requests_proxies()
        try:
            resp = _requests_cb.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-5.4",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Что рекламирует этот баннер? Ответь одним-двумя словами: конкретный товар или услуга (например 'дорожные плиты', 'бетон', 'шкафы-купе', 'ремонт стиральных машин' и т.п.), без лишних слов."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                        ]
                    }],
                    "max_completion_tokens": 30
                },
                proxies=proxies, timeout=60
            )
            result = resp.json()
            try:
                from app.usage import log_usage as _log
                _u = (result or {}).get("usage", {}) or {}
                _log(req.account_id, "openai", "gpt-5.4", "распознавание баннера",
                     _u.get("prompt_tokens", 0), _u.get("completion_tokens", 0))
            except Exception as _ue:
                print("[usage]", str(_ue)[:100], flush=True)
            break
        except Exception as _e:
            print("[classify_banner] Попытка " + str(_attempt) + " не удалась: " + str(_e), flush=True)
            continue

    if result is None:
        return {"status": "error", "message": "Не удалось связаться с сервисом распознавания (проблема с прокси)"}
    try:
        topic = result["choices"][0]["message"]["content"].strip().lower()
    except Exception:
        return {"status": "error", "message": f"Не удалось распознать: {result}"}

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
