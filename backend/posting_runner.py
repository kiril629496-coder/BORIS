# -*- coding: utf-8 -*-
import os, sys, json
from datetime import date, datetime, timedelta

from app.services.mass_ai_guard import guarded_post as _mass_guarded_post
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.posting_store_lock import (
    load_posting_posts_for_update,
    save_posting_posts_locked,
)

DEFAULT_TIMES = ["09:00", "19:00"]
# Предел давности черновика: старше этого срока автопубликация не трогает.
STALE_AFTER_HOURS = 24
BANNER_DIR = "/root/BORIS/backend/posting_banners"


def _try_post_delivery_lock(account_id, post_id):
    """Cross-process lock for one external Social publication lifecycle."""
    from sqlalchemy import text as _sql_text
    from app.db.session import engine as _engine
    conn = _engine.connect()
    try:
        key = "social-delivery:%s:%s" % (str(account_id), str(post_id))
        acquired = bool(conn.execute(_sql_text(
            "select pg_try_advisory_lock(hashtextextended(:k, 731927))"
        ), {"k": key}).scalar())
        conn.commit()
        if acquired:
            return conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    conn.close()
    return None


def _release_post_delivery_lock(conn, account_id, post_id):
    if conn is None:
        return
    from sqlalchemy import text as _sql_text
    try:
        key = "social-delivery:%s:%s" % (str(account_id), str(post_id))
        conn.execute(_sql_text(
            "select pg_advisory_unlock(hashtextextended(:k, 731927))"
        ), {"k": key})
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _load_post_record(account_id, post_id):
    """Fresh lifecycle record after a distributed delivery lock is acquired."""
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_posts").first()
        posts = _json.loads(row.value) if row and row.value else []
        return next((p for p in posts if str(p.get("id")) == str(post_id)), None)
    except Exception:
        return None
    finally:
        db.close()


def _persist_post_record(account_id, record):
    """Replace exactly one lifecycle record under the canonical posting_posts lock."""
    post_id = str((record or {}).get("id") or "")
    if not post_id:
        return False
    db = SessionLocal()
    try:
        row, posts = load_posting_posts_for_update(db, account_id)
        for idx, current in enumerate(posts):
            if str(current.get("id") or "") == post_id:
                posts[idx] = dict(record)
                save_posting_posts_locked(db, account_id, row, posts)
                return True
        db.rollback()
        return False
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _openai_text(account_id, prompt, operation="текст постинга"):
    """Canonical exactly-once paid text path for Social posting.

    Reuses the Campaign/Feed Factory paid transport: deterministic intent,
    cross-process lock, durable cache, account caps, ai_budget, provider
    Idempotency-Key and exact x-request-id accounting.
    """
    import hashlib
    from app.api.campaigns import _ff_guarded_openai_response

    prompt = str(prompt or "")
    intent = "social-post-text:" + hashlib.sha256(
        (str(account_id) + "\n" + str(operation) + "\n" + prompt).encode("utf-8", "ignore")
    ).hexdigest()
    out = _ff_guarded_openai_response(
        account_id=str(account_id),
        operation=str(operation),
        model="gpt-5.4",
        input_payload=[{"role": "user", "content": prompt}],
        max_output_tokens=700,
        idempotency_key=intent,
        timeout=90,
    )
    text_out = str((out or {}).get("text") or "").strip()
    if not text_out:
        raise RuntimeError("SOCIAL_TEXT_EMPTY")
    return text_out


# ── Справочник праздников РФ (для occasion) ───────────────────────
# Фиксированные: {(месяц, день): "название"}
_RU_HOLIDAYS_FIXED = {
    (1,1):"Новый год", (1,7):"Рождество Христово", (1,25):"Татьянин день (студенчества)",
    (2,8):"День российской науки", (2,14):"День святого Валентина", (2,23):"День защитника Отечества",
    (3,8):"Международный женский день",
    (4,12):"День космонавтики",
    (5,1):"Праздник Весны и Труда", (5,9):"День Победы", (5,24):"День славянской письменности",
    (6,1):"День защиты детей", (6,6):"День русского языка", (6,12):"День России",
    (7,8):"День семьи, любви и верности",
    (8,22):"День флага России",
    (9,1):"День знаний", (9,3):"День солидарности в борьбе с терроризмом", (9,27):"День воспитателя",
    (10,5):"День учителя",
    (11,4):"День народного единства", (11,10):"День сотрудника МВД", (11,21):"День бухгалтера",
    (12,3):"День юриста", (12,12):"День Конституции", (12,22):"День энергетика",
    (12,27):"День спасателя (МЧС)", (12,31):"Новогодняя ночь",
}
# Подвижные: функция (year) -> date. Ключ — название.
def _nth_weekday(year, month, weekday, n):
    import calendar as _cal
    days=[d for d in range(1, _cal.monthrange(year,month)[1]+1)
          if date(year,month,d).weekday()==weekday]
    return date(year,month,days[n-1]) if 0 < n <= len(days) else None
def _last_weekday(year, month, weekday):
    import calendar as _cal
    days=[d for d in range(1, _cal.monthrange(year,month)[1]+1)
          if date(year,month,d).weekday()==weekday]
    return date(year,month,days[-1]) if days else None

def _ru_holidays_movable(year):
    # weekday: Пн=0 … Вс=6
    return {
        "День работника культуры": _last_weekday(year,3,6),
        "День медицинского работника": _nth_weekday(year,6,6,3),
        "День рыбака": _nth_weekday(year,7,6,2),
        "День металлурга": _nth_weekday(year,7,6,3),
        "День системного администратора": _last_weekday(year,7,4),
        "День Военно-морского флота (ВМФ)": _last_weekday(year,7,6),
        "День железнодорожника": _nth_weekday(year,8,6,1),
        "День строителя": _nth_weekday(year,8,6,2),
        "День шахтёра": _last_weekday(year,8,6),
        "День нефтяника": _nth_weekday(year,9,6,1),
        "День работника сельского хозяйства": _nth_weekday(year,10,6,2),
        "День автомобилиста": _last_weekday(year,10,6),
        "День матери": _last_weekday(year,11,6),
    }

def _holiday_in_window(days=7):
    """Возвращает 'Название (дд.мм)' первого праздника в окне [сегодня; сегодня+days], иначе ''."""
    today=date.today()
    hits=[]
    for off in range(days+1):
        d=today+timedelta(days=off)
        name=_RU_HOLIDAYS_FIXED.get((d.month,d.day))
        if name: hits.append((d,name))
        for hn,hd in _ru_holidays_movable(d.year).items():
            if hd==d: hits.append((d,hn))
    if not hits: return ""
    hits.sort(key=lambda x:x[0])
    d,name=hits[0]
    return name+" ("+d.strftime("%d.%m")+")"


def _project_occasion(proj):
    """Return a calendar hook only where it naturally belongs.

    Product/SMM channels must not force unrelated solemn/professional dates
    into evergreen sales copy. Their own project themes may still mention a
    relevant occasion explicitly.
    """
    name = str((proj or {}).get("name") or "").strip().lower()
    if "океан клиент" in name or "от души" in name:
        return ""
    return _holiday_in_window(7)


def _best_examples(account_id, project_id, limit=3):
    """Лучшие посты этого проекта по вовлечению — как образец для копирайтера.
    Берём только те, где есть просмотры и хоть одна реакция."""
    import json as _j
    from app.models.storage import Storage as _St
    db = SessionLocal()
    try:
        row = db.query(_St).filter(_St.account_id == account_id, _St.key == "posting_posts").first()
        posts = _j.loads(row.value) if row and row.value else []
    except Exception:
        return []
    finally:
        db.close()
    ranked = []
    for p in posts:
        if p.get("project_id") != project_id:
            continue
        st = p.get("stats") or {}
        if not st.get("views") or not st.get("reactions"):
            continue
        ranked.append((st.get("er") or 0, p.get("text") or ""))
    ranked.sort(reverse=True)
    return [t for _, t in ranked[:limit] if t]


def _build_text_prompt(theme, client_prompt="", occasion="", examples=None, allow_hashtags=False):
    parts = [f"Напиши пост для соцсетей (ВКонтакте и Telegram) на тему: {theme}.",
             "Живой, цепляющий, короткий — 3-6 предложений. Без канцелярита. Эмодзи уместно."]
    if occasion: parts.append(f"Привяжи пост к поводу/дате: {occasion}.")
    if client_prompt: parts.append(f"Учитывай стиль и пожелания автора канала: {client_prompt}")
    if examples:
        sample = "\n\n---\n\n".join(examples[:5])
        parts.append("Вот примеры уже опубликованных постов этого канала — пиши в ТАКОМ ЖЕ стиле, тоне и формате, но НЕ копируй их дословно:\n\n" + sample)
    parts.append("ВАЖНО: НЕ добавляй в текст никаких ссылок, адресов, @упоминаний и контактов — они подставляются автоматически отдельно. Призыв к действию можно, но БЕЗ конкретной ссылки.")
    parts.append("ВОВЛЕЧЕНИЕ БЕЗ ШАБЛОНА: не заканчивай каждый пост одинаковым вопросом. Чередуй естественные финалы: короткий вывод, наблюдение, полезный совет, спокойный CTA или один живой вопрос, если он действительно просится по смыслу. Не проси лайки и репосты прямым текстом и не повторяй формулы «а у вас как было? / сталкивались с таким? / что выбрали бы вы?» из поста в пост.")
    parts.append("Ссылку на контакт система добавит сама — в тексте её быть не должно.")
    if allow_hashtags:
        parts.append("ХЭШТЕГИ: сам придумай 5–10 релевантных хэштегов строго по теме конкретного поста и поставь их одной последней строкой. Не копируй один и тот же набор из поста в пост. Не пиши служебные заголовки вроде «ХЭШТЕГИ:».")
        parts.append("СЛУЖЕБНЫЕ ПОМЕТКИ ЗАПРЕЩЕНЫ: не пиши ТЕМА:, ТОН:, СТРУКТУРА:, CTA:, ФОРМАТ:, ДЛЯ ИЗОБРАЖЕНИЯ:, ВИЗУАЛЬНЫЙ СТИЛЬ: и пояснения о том, как создавался пост.")
    else:
        parts.append("НИКАКИХ ХЭШТЕГОВ И СЛУЖЕБНЫХ ПОМЕТОК: не пиши #теги, ТЕМА:, ТОН:, СТРУКТУРА:, CTA:, ФОРМАТ:, ДЛЯ ИЗОБРАЖЕНИЯ:, ВИЗУАЛЬНЫЙ СТИЛЬ: и любые пояснения о том, как создавался пост. Читатель должен видеть только готовый человеческий текст.")
    parts.append("НЕ используй одинаковый рекламный скрипт в каждом посте, не перечисляй весь продуктовый каталог и не повторяй один и тот же CTA. Сначала полезная/живая мысль; продажа — максимум одной естественной фразой и не обязана быть в каждом посте.")
    parts.append("РАЗМЕТКА: разрешён ТОЛЬКО HTML-тег <b>жирный</b> — оберни в него заголовок первой строки и одну-две ключевые фразы по смыслу. Больше никаких тегов. Markdown запрещён полностью: никаких **, ##, __, backticks — они будут видны читателю как мусор.")
    _t0 = date.today()
    _win = ", ".join((_t0 + timedelta(days=_i)).strftime("%d.%m.%Y") for _i in range(8))
    parts.append("СЕГОДНЯ " + _t0.strftime("%d.%m.%Y") + ". Упоминать события, праздники и любые даты разрешено ТОЛЬКО из этого списка: " + _win + ".")
    parts.append("Все прочие даты и праздники под строгим запретом — не упоминай их вообще. Если в разрешённом окне праздника нет, пиши обычный пост без привязки к дате и без слов «скоро», «на днях», «приближается».")
    parts.append("НЕ выдумывай цены, телефоны, адреса и график работы. Если в инструкции стоит заглушка вроде «УКАЖИ» — не подставляй цифры и не переноси заглушку в текст.")
    parts.append("Верни только текст поста, без пояснений и кавычек.")
    return "\n".join(parts)

def _build_banner_prompt(theme, client_prompt="", title="", allowed_dates=""):
    p = (
        "Создай ПРЕМИАЛЬНЫЙ рекламный баннер для социальных сетей, квадрат 1:1.\n\n"
        + ("ГЛАВНЫЙ ЗАГОЛОВОК крупным шрифтом, точно этот текст: «" + title + "»\n" if title else "")
        + "ТЕМА БАННЕРА: " + theme + "\n\n"
        "СТИЛЬ: премиальная рекламная графика — насыщенные цвета, динамичная композиция, высокое качество "
        "продакшена. НЕ похоже на дешёвое стоковое фото. Один яркий акцентный цвет плюс тёмные или "
        "нейтральные тона фона, объёмный свет, глубина кадра.\n"
        "ГЛАВНЫЙ ОБЪЕКТ по теме рисуй точно и узнаваемо, не подменяй абстракцией.\n"
        "ВЁРСТКА: чистая, с безопасными отступами от краёв. Весь важный контент — в ЦЕНТРАЛЬНОЙ части кадра, "
        "верх и низ оставляй под фон. Допустимы аккуратные плашки, иконки и короткие подписи, но БЕЗ цен и без дат.\n"
        "ТЕКСТ: весь текст на РУССКОМ языке, без орфографических ошибок, полностью помещается в кадр и НЕ "
        "обрезан краями. Если текста много — уменьшай шрифт, но не обрезай. Никаких выдуманных слов и "
        "бессмысленных букв.\n"
        "ОРИЕНТАЦИЯ: строго ровная, 0 градусов, без наклонов и диагоналей.\n"
        "ЗАПРЕЩЕНО НА БАННЕРЕ: любые цены, суммы, числа с ₽/руб/рублей, ценники и плашки вроде "
        "«специальная цена», «от N ₽», «стоимость», «скидка», прайс-листы, телефоны, адреса, графики. "
        "Цену НЕ рисуй ни при каких условиях.\n"
        + ("РАЗРЕШЁННЫЕ ДАТЫ (только эти, если уместно): " + allowed_dates + ". Любой праздник или дату "
           "вне этого списка НЕ упоминай и НЕ рисуй — ни надписью «в честь праздника», ни числом.\n" if allowed_dates else "ДАТЫ и праздники на баннере НЕ рисуй вообще.\n")
        + "БЕЗ водяных знаков, без чужих логотипов, без рамок интерфейса."
    )
    if client_prompt:
        p += "\n\nПОЖЕЛАНИЯ КЛИЕНТА ПО СТИЛЮ: " + client_prompt
    return p

def _get_showcase_style(account_id):
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "banner_showcase").first()
        items = _json.loads(row.value) if row else []
        crow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_showcase_style").first()
        cache = _json.loads(crow.value) if crow else {}
    finally:
        db.close()
    urls = [it.get("url") for it in items[:3] if it.get("url")]
    if not urls:
        return ""
    if cache.get("urls") == urls and cache.get("style"):
        return cache["style"]
    style = ""
    try:
        from app.api.banners import _describe_reference_style_multi
        style = _describe_reference_style_multi(urls) or ""
    except Exception as _e:
        print("[showcase] стиль не описан: " + str(_e)[:120], flush=True)
    if style:
        db = SessionLocal()
        try:
            crow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_showcase_style").first()
            val = _json.dumps({"urls": urls, "style": style}, ensure_ascii=False)
            if crow:
                crow.value = val
            else:
                db.add(Storage(account_id=account_id, key="posting_showcase_style", value=val))
            db.commit()
        finally:
            db.close()
    return style

def _post_title(text):
    import re as _re
    for line in (text or "").split("\n"):
        st = line.strip()
        st = _re.sub(r"[#*_`]", "", st)
        st = _re.sub(r"[^\w\s\-–—.,!?:%№\"«»()]", "", st, flags=_re.UNICODE).strip()
        if len(st) >= 8:
            st = _re.split(r"(?<=[.!?])\s", st)[0]
            if len(st) > 32:
                st = st[:32].rsplit(" ", 1)[0]
            return st.strip(" —-–,")
    return "Новости"


def _make_title(account_id, text):
    try:
        t = _openai_text(account_id,
            "Придумай короткий рекламный заголовок для баннера к этому посту. "
            "Максимум 5 слов. Обязательно законченная мысль, а не обрывок фразы. "
            "Без точки в конце, без кавычек, без хештегов и эмодзи. Верни только заголовок.\n\nПОСТ:\n"
            + (text or "")[:1500])
        t = (t or "").strip().strip('"\u00ab\u00bb').split("\n")[0].strip()
        if 3 <= len(t) <= 60:
            return t
    except Exception as e:
        print("[title] не удалось: " + str(e)[:120], flush=True)
    return _post_title(text)


def _airify(text):
    import re as _re
    blocks = [b.strip() for b in _re.split(r"\n\s*\n", text or "") if b.strip()]
    out = []
    for b in blocks:
        if b.lstrip().startswith("#"):
            out.append(b.strip())
            continue
        sents = _re.split(r"(?<=[.!?\u2026])\s+", b.replace("\n", " ").strip())
        merged = []
        for x in sents:
            x = x.strip()
            if not x:
                continue
            if len(x) <= 3 and merged:
                merged[-1] = merged[-1] + " " + x
            else:
                merged.append(x)
        out.append("\n".join(merged))
    return "\n\n".join(out)


def generate_social_banner_asset(account_id, theme, title, banner_prompt="", output_path="", operation="banner_social_post"):
    """Canonical OpenAI social-banner generator reused by Social and Avito campaign workflows.

    Invariant: OpenAI gpt-image-2 only; no GigaChat and no silent local fallback.
    A caller-supplied deterministic output_path is also the paid-generation
    idempotency key: an existing non-empty artifact is reused before any API call.
    """
    os.makedirs(BANNER_DIR, exist_ok=True)
    fpath = output_path or os.path.join(BANNER_DIR, f"{account_id}_{datetime.now():%Y%m%d_%H%M%S}.png")
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    if output_path and os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
        print(f"[banner_openai] cache_hit: {fpath} operation={operation}", flush=True)
        return fpath
    _allowed_dates = ", ".join(
        (date.today() + timedelta(days=_i)).strftime("%d.%m.%Y")
        for _i in range(8)
    )
    _prompt = _build_banner_prompt(theme, banner_prompt, title, _allowed_dates)
    from app.db.session import SessionLocal as _SessionLocal
    from app.services import image_service as _IMG
    _db=_SessionLocal()
    try:
        _out=_IMG.generate_banner_financially_guarded(
            _db, account_id, _prompt, fpath, size="1024x1024", quality="medium",
            model=_IMG.PREMIUM_MODEL, operation=operation
        )
    finally:
        _db.close()
    import os as _os
    if not _out.get("ok") or not (_os.path.exists(fpath) and _os.path.getsize(fpath) > 0):
        raise RuntimeError(_out.get("error") or "OPENAI_GPT_IMAGE_2_BANNER_MISSING")
    print(
        f"[banner_openai] ready: {fpath} bytes={_os.path.getsize(fpath)} model=gpt-image-2 quality=medium operation={operation}",
        flush=True,
    )
    return fpath


def _generate_pexels_social_asset(account_id, theme, text):
    """Deterministic free stock-media path for projects explicitly set to Pexels."""
    import hashlib as _hashlib, io as _io, requests as _requests
    from PIL import Image as _Image, ImageOps as _ImageOps
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY_NOT_CONFIGURED")
    intent = _hashlib.sha256((str(account_id) + "\n" + str(theme) + "\n" + str(text) + "\npexels-social-v1").encode("utf-8", "ignore")).hexdigest()
    fpath = os.path.join(BANNER_DIR, f"{account_id}_pexels_{intent}.jpg")
    if os.path.isfile(fpath) and os.path.getsize(fpath) >= 16 * 1024:
        return fpath

    resp = _requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": api_key},
        params={"query": str(theme)[:180], "per_page": 15, "orientation": "square"},
        timeout=25,
    )
    if resp.status_code != 200:
        raise RuntimeError("PEXELS_SEARCH_HTTP_%s" % resp.status_code)
    photos = (resp.json() or {}).get("photos") or []
    if not photos:
        raise RuntimeError("PEXELS_NO_PHOTOS")
    idx = int(intent[:8], 16) % len(photos)
    src = (photos[idx].get("src") or {})
    image_url = src.get("large2x") or src.get("large") or src.get("original")
    if not image_url:
        raise RuntimeError("PEXELS_PHOTO_URL_MISSING")
    image_resp = _requests.get(image_url, timeout=45)
    if image_resp.status_code != 200 or len(image_resp.content or b"") < 16 * 1024:
        raise RuntimeError("PEXELS_IMAGE_DOWNLOAD_FAILED")
    im = _Image.open(_io.BytesIO(image_resp.content)).convert("RGB")
    im = _ImageOps.fit(im, (1024, 1024), method=_Image.Resampling.LANCZOS)
    os.makedirs(BANNER_DIR, exist_ok=True)
    im.save(fpath, "JPEG", quality=88, optimize=True)
    if not (os.path.isfile(fpath) and os.path.getsize(fpath) >= 16 * 1024):
        raise RuntimeError("PEXELS_IMAGE_SAVE_FAILED")
    return fpath



def _extract_public_hashtags(text, limit=10):
    """Extract model-chosen hashtags in stable order, without duplicates."""
    import re as _re_tags
    found = _re_tags.findall(r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+", str(text or ""), flags=_re_tags.U)
    out = []
    seen = set()
    for tag in found:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= int(limit):
            break
    return out


def _append_public_hashtags(text, tags):
    tags = [str(x).strip() for x in (tags or []) if str(x).strip()]
    if not tags:
        return str(text or "").strip()
    return str(text or "").rstrip() + "\n\n" + " ".join(tags)


def _sanitize_public_post_text(text):
    """Final no-leak boundary for user-visible social copy.

    Removes model/prompt artefacts, Markdown markers and hashtag blocks that
    must never be visible as production instructions. Keeps only <b>...</b>
    as optional Telegram formatting; VK strips HTML at transport.
    """
    import re as _re_public
    value = str(text or "").replace("\r", "").strip()
    value = _re_public.sub(r"(?s)```.*?```", "", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    out = []
    meta = _re_public.compile(
        r"(?i)^\s*(?:тема|тон|структура|формат(?:\s+подачи)?|"
        r"призыв(?:\s+к\s+действию)?|cta|хэштеги?|теги?|"
        r"служебн\w*\s+(?:пометк\w*|инструкц\w*)|для\s+изображения|"
        r"визуальн\w*\s+стиль)\s*[:—-]"
    )
    hashtags_only = _re_public.compile(r"^\s*(?:#\w+[\s,]*){2,}$", _re_public.U)
    for line in value.split("\n"):
        stripped = line.strip()
        if meta.match(stripped):
            continue
        if hashtags_only.match(stripped):
            continue
        if _re_public.search(r"(?i)для\s+изображения\s*\(?.*в\s+текст\s+поста\s+не\s+переносить", stripped):
            continue
        out.append(line.rstrip())
    value = "\n".join(out)
    value = _re_public.sub(r"(?m)^#{1,6}\s+", "", value)
    value = _re_public.sub(r"\n{3,}", "\n\n", value).strip()
    return value


def _reuse_existing_ocean_social_banner(account_id, project_id, theme, text_value):
    """Pick an already-created Ocean social banner; never call an image provider."""
    import hashlib as _hash_reuse
    from app.services.prospect_banner import _social_project_paths

    pool = _social_project_paths()
    if not pool:
        raise RuntimeError("OCEAN_REUSE_BANNER_POOL_EMPTY")
    key = "\n".join([
        str(account_id or ""),
        str(project_id or ""),
        str(theme or ""),
        str(text_value or "")[:500],
        "ocean-social-reuse-v1",
    ])
    idx = int(_hash_reuse.sha256(key.encode("utf-8", "ignore")).hexdigest()[:16], 16) % len(pool)
    path = pool[idx]
    if not path.is_file() or path.stat().st_size < 50_000:
        raise RuntimeError("OCEAN_REUSE_BANNER_INVALID")
    return str(path)


def _ocean_uncertain_text_fallback(theme):
    """Deterministic no-provider emergency copy for Ocean paid-AI uncertainty.

    This is deliberately narrow: it is only used when the paid text request is
    quarantined as provider-outcome-uncertain. It avoids a second paid call while
    still keeping the owner autopilot alive.
    """
    value = str(theme or "").strip()
    low = value.lower()
    if "конкурент" in low:
        body = (
            "<b>Конкурентов полезно смотреть не ради копирования</b>\n\n"
            "Сравните заголовок, первое фото, характеристики, условия и аргументы по цене. "
            "Повторы покажут стандарт рынка, а различия — где ваше объявление можно сделать понятнее и заметнее.\n\n"
            "Если хотите, BORIS поможет собрать такую проверку перед тестом."
        )
    elif "заголов" in low:
        body = (
            "<b>Заголовок должен быстро объяснять, что именно вы предлагаете</b>\n\n"
            "Уберите общие слова и проверьте, видно ли из первых строк товар, услугу и главное отличие. "
            "Меняйте один вариант за раз — так проще понять, что реально влияет на обращения.\n\n"
            "Если хотите, BORIS поможет подготовить варианты для проверки."
        )
    elif "фид" in low:
        body = (
            "<b>Фид лучше проверить до загрузки, а не после отклонений</b>\n\n"
            "Сверьте категории, обязательные характеристики, цены, ссылки на изображения и уникальность карточек. "
            "Так ошибки видны до публикации и не приходится чинить десятки объявлений вручную.\n\n"
            "Если хотите, BORIS поможет собрать и проверить фид перед запуском."
        )
    elif "категор" in low:
        body = (
            "<b>Неверная категория портит объявление ещё до работы текста</b>\n\n"
            "Проверьте, где покупатель действительно ищет такую услугу или товар, и заполните обязательные характеристики этой ветки. "
            "Только после этого имеет смысл тестировать заголовок и подачу.\n\n"
            "Если хотите, BORIS поможет пройти эту проверку перед публикацией."
        )
    elif any(token in low for token in ("ответ", "переписк", "клиент уходит")):
        body = (
            "<b>Скорость ответа важна, но одного быстрого сообщения мало</b>\n\n"
            "Нужно понять запрос, задать короткий уточняющий вопрос и довести диалог до следующего действия. "
            "По переписке хорошо видно, на каком шаге клиент перестаёт отвечать.\n\n"
            "Если хотите, BORIS поможет разобрать такие диалоги и найти слабое место."
        )
    elif any(token in low for token in ("соцсет", "контент", "пост")):
        body = (
            "<b>Контент проще вести системой, а не ждать вдохновения</b>\n\n"
            "Заранее соберите темы из вопросов клиентов, ошибок, продукта и реальных рабочих ситуаций. "
            "Тогда публикации можно ставить в очередь и не придумывать всё заново в последний момент.\n\n"
            "Если хотите, BORIS поможет собрать такой рабочий ритм."
        )
    else:
        title = value[:90].strip(" .:-") or "Практическая проверка перед запуском"
        body = (
            "<b>" + title[:1].upper() + title[1:] + "</b>\n\n"
            "Сначала проверьте текущие факты и меняйте один элемент за раз. "
            "Так проще отделить реальное влияние на обращения от случайных изменений и не масштабировать то, что ещё не доказало результат.\n\n"
            "Если хотите, BORIS поможет зафиксировать проверку и сравнить результат."
        )
    return _sanitize_public_post_text(body)


def _social_provider_text_recovery(project_id, theme):
    """No-provider service-continuity text after cooldown/retry-cap."""
    pid=str(project_id or ""); value=str(theme or "").strip(); low=value.lower()
    if pid == "a374ced25745": return _ocean_uncertain_text_fallback(theme)
    if pid == "f938caa3a944":
        if any(x in low for x in ("прощ", "трудно говорить", "слова")):
            body="<b>Когда трудно подобрать слова, важнее всего говорить по-человечески</b>\n\nНе обязательно искать идеальную формулировку. Можно вспомнить человека, назвать то, что было особенно дорого, и сказать простые слова поддержки близким. Искренность обычно звучит точнее любого готового шаблона.\n\nСохраните эту мысль на случай, когда слова будут даваться трудно."
        else:
            title=value[:90].strip(" .:-") or "Тёплые слова в важный момент"
            body="<b>"+title[:1].upper()+title[1:]+"</b>\n\nВ непростой момент лучше опираться на простые и искренние слова. Вспомните конкретного человека, важный эпизод или качество, за которое его ценили. Так текст остаётся личным и не превращается в безликий шаблон."
        return _sanitize_public_post_text(body)
    if pid == "270dbf472f7f":
        title=value.split(". Для ИЗОБРАЖЕНИЯ",1)[0].strip()[:90] or "Идея для следующего маникюра"
        body="<b>"+title[:1].upper()+title[1:]+"</b>\n\nПри выборе дизайна полезно заранее решить, что важнее именно сейчас: спокойный повседневный вариант или заметный акцент. Форма, длина и оттенок лучше работают вместе, когда подходят вашему привычному образу и ритму жизни.\n\nКакой вариант вам ближе — спокойный или более выразительный?"
        return _sanitize_public_post_text(body)
    return None

def _social_reuse_published_banner(account_id, project_id, theme):
    """Reuse a validated exact-project asset without hammering one image forever.

    Selection is deterministic for the same theme (idempotent retries) but is
    spread across the recent unique published asset pool for different themes.
    """
    db=SessionLocal()
    try:
        row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="posting_posts").first()
        try: posts=json.loads(row.value or "[]") if row else []
        except Exception: posts=[]
    finally: db.close()
    candidates=[]; seen=set()
    for rec in posts:
        # SOCIAL_RECOVERY_PARTIAL_ASSET_REUSE_V1: a valid exact-project asset
        # remains safe to reuse even when delivery is partial solely because a
        # platform transport is unavailable. Requiring status=published here
        # created a deadlock for VK-only projects: every good banner stayed
        # partial during cooldown, so recovery could never find any banner.
        if not isinstance(rec,dict) or str(rec.get("project_id") or "") != str(project_id): continue
        if str(rec.get("status") or "") not in {"published", "partial"}: continue
        b=str(rec.get("banner") or ""); path=b if os.path.isabs(b) else os.path.join(BANNER_DIR,b)
        if not (b and os.path.isfile(path) and os.path.getsize(path)>=50000): continue
        real=os.path.realpath(path)
        if real in seen: continue
        seen.add(real); candidates.append((str(rec.get("created_at") or ""),path,str(rec.get("id") or "")))
    if not candidates: raise RuntimeError("SOCIAL_RECOVERY_BANNER_NOT_FOUND")
    candidates.sort(reverse=True)
    # Bound the rotation to recent material so outage recovery stays visually
    # current while avoiding repeated use of one newest image.
    pool=candidates[:min(12,len(candidates))]
    import hashlib as _hashlib
    seed=(str(project_id or "")+"\n"+str(theme or "")).encode("utf-8","ignore")
    idx=int(_hashlib.sha256(seed).hexdigest()[:16],16) % len(pool)
    _,path,rid=pool[idx]
    return {"path":path,"source_record_id":rid,"rotation_pool_size":len(pool),"rotation_index":idx}

def _u42_uncertain_text_fallback(theme):
    """No-provider recovery copy for the active Plan B cabins project.

    Uses only generic operational facts already present in this project's own
    published copy. No prices, dates, guarantees or invented availability.
    """
    low = str(theme or "").lower()
    if any(x in low for x in ("раздевал", "комната отдыха", "рабочих", "бригада")):
        body = (
            "<b>Бытовка для бригады должна решать бытовые задачи, а не добавлять тесноту</b>\n\n"
            "Если рабочим нужно переодеваться и отдыхать между сменами, заранее продумайте место для одежды, обуви, стола и сидений. "
            "Когда всё смешано в одной тесной зоне, порядок быстро теряется.\n\n"
            "Перед выбором бытовки лучше сразу указать количество людей и условия на площадке — так проще подобрать вариант под реальную работу бригады."
        )
    else:
        title = str(theme or "Бытовка под задачу").strip(" .:-")[:90]
        body = (
            "<b>" + (title[:1].upper() + title[1:] if title else "Бытовка под задачу") + "</b>\n\n"
            "Перед арендой лучше сначала определить, кто будет пользоваться бытовкой и для чего она нужна: люди, хранение, охрана или рабочее место. "
            "От этого зависят планировка, мебель и требования к установке.\n\n"
            "Опишите задачу и условия на площадке — так проще подобрать подходящий вариант без лишних переделок."
        )
    return _sanitize_public_post_text(body)


def _u42_uncertain_reuse_banner(account_id, project_id, theme):
    """Pick a semantically related already-published Plan B banner, no provider."""
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == "posting_posts"
        ).first()
        try:
            posts = json.loads(row.value or "[]") if row else []
        except Exception:
            posts = []
    finally:
        db.close()

    low_theme = str(theme or "").lower()
    if any(x in low_theme for x in ("раздевал", "комната отдыха", "рабочих", "бригада")):
        preferred = ("место для отдыха", "для бригады", "переодеться")
    else:
        preferred = tuple(
            x for x in str(theme or "").lower().split()
            if len(x) >= 6
        )[:5]

    best = None
    best_score = -1
    for idx, rec in enumerate(posts):
        if not isinstance(rec, dict) or str(rec.get("project_id") or "") != str(project_id):
            continue
        if str(rec.get("status") or "") != "published":
            continue
        bname = str(rec.get("banner") or "")
        if not bname:
            continue
        path = os.path.join(BANNER_DIR, bname)
        if not (os.path.isfile(path) and os.path.getsize(path) >= 50_000):
            continue
        text_low = str(rec.get("text") or "").lower()
        score = sum(3 for phrase in preferred if phrase and phrase in text_low)
        score += max(0, 3 - idx // 3)
        if score > best_score:
            best = (path, str(rec.get("id") or ""), score)
            best_score = score
    if not best:
        raise RuntimeError("U42_RECOVERY_BANNER_NOT_FOUND")
    return {
        "path": best[0],
        "source_record_id": best[1],
        "score": best[2],
    }


def _scheduled_record_delivery_due_local(rec, proj):
    """Scheduled moderation deadline from generation_key, not recovery created_at."""
    import re as _re_due_record
    key = str((rec or {}).get("generation_key") or "")
    match = _re_due_record.match(r"^social-slot:[^:]+:[^:]+:(\d{8})_(\d{2})$", key)
    if not match:
        return None
    try:
        slot_local = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H")
        delay = (
            int((proj or {}).get("auto_publish_hours") or 0)
            if str((proj or {}).get("mode") or "auto") == "moderate"
            else 0
        )
        return slot_local + timedelta(hours=delay)
    except Exception:
        return None


def _record_social_ai_uncertain_fallback(account_id, project_id, generation_key, theme, error):
    """Durable idempotent evidence that service continued without another paid call."""
    if not generation_key:
        return {"ok": False, "reason": "generation_key_missing"}
    key = "social_ai_uncertain_fallback:" + str(generation_key)
    payload = {
        "generation_key": str(generation_key),
        "project_id": str(project_id or ""),
        "theme": str(theme or ""),
        "reason": str(error or "")[:240],
        "fallback": "deterministic_no_provider",
        "paid_retry_performed": False,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == key
        ).first()
        if row:
            return {"ok": True, "existing": True, "key": key}
        db.add(Storage(
            account_id=account_id, key=key,
            value=json.dumps(payload, ensure_ascii=False),
        ))
        db.commit()
        return {"ok": True, "created": True, "key": key}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def generate_post(account_id, theme, text_prompt="", banner_prompt="", occasion="", channel="", image_source="ai", project_id="", text_prompt_snapshot="", allow_hashtags=False):
    result = {"account_id": account_id, "theme": theme}
    examples = None
    if not str(text_prompt_snapshot or "").strip() and channel:
        try:
            _r = _fetch_channel_posts(channel)
            if _r.get("ok"): examples = _r.get("posts")
        except Exception:
            examples = None
    # Свои лучшие посты по вовлечению приоритетнее чужого канала-образца:
    # копирайтер учится на том, что реально сработало у этого клиента.
    if project_id and not str(text_prompt_snapshot or "").strip():
        try:
            best = _best_examples(account_id, project_id)
            if best:
                examples = best + [e for e in (examples or []) if e not in best]
                print("[autopost] в примеры добавлено своих лучших постов:", len(best), flush=True)
        except Exception as _e:
            print("[autopost] лучшие посты не подобрались:", str(_e)[:80], flush=True)
    _final_text_prompt = (
        str(text_prompt_snapshot or "").strip()
        or _build_text_prompt(theme, text_prompt, occasion, examples, allow_hashtags=allow_hashtags)
    )
    try:
        _txt = _openai_text(account_id, _final_text_prompt)
    except RuntimeError as exc:
        if (
            str(project_id or "") == "a374ced25745"
            and str(exc).startswith("CAMPAIGN_AI_OUTCOME_UNCERTAIN:")
        ):
            _txt = _ocean_uncertain_text_fallback(theme)
            result["text_engine"] = "deterministic_uncertain_fallback"
            result["text_recovery_reason"] = str(exc)[:240]
            print(
                "[autopost] OCEAN_AI_UNCERTAIN_LOCAL_FALLBACK:",
                str(exc)[:180],
                flush=True,
            )
        elif (
            str(project_id or "") == "688556520514"
            and str(exc).startswith("CAMPAIGN_AI_OUTCOME_UNCERTAIN:")
        ):
            _txt = _u42_uncertain_text_fallback(theme)
            result["text_engine"] = "deterministic_uncertain_fallback"
            result["text_recovery_reason"] = str(exc)[:240]
            print(
                "[autopost] U42_AI_UNCERTAIN_LOCAL_FALLBACK:",
                str(exc)[:180],
                flush=True,
            )
        else:
            _reason=str(exc)
            _uncertain=_reason.startswith("CAMPAIGN_AI_OUTCOME_UNCERTAIN:")
            _recoverable=_reason.startswith(("CAMPAIGN_AI_PROVIDER_COOLDOWN:","CAMPAIGN_AI_RELEASED_RETRY_CAP:"))
            _recovered=_social_provider_text_recovery(project_id,theme) if (_recoverable or _uncertain) else None
            if _recovered:
                _txt=_recovered
                result["text_engine"]=("deterministic_uncertain_fallback" if _uncertain else "deterministic_provider_recovery")
                result["text_recovery_reason"]=_reason[:240]
                print(
                    "[autopost] SOCIAL_%s_TEXT_RECOVERY:" % ("UNCERTAIN" if _uncertain else "PROVIDER"),
                    str(project_id),_reason[:180],flush=True,
                )
            else: raise
    import re as _re
    _generated_hashtags = _extract_public_hashtags(_txt, limit=10) if allow_hashtags else []
    _txt = _re.sub(r"\*\*(.+?)\*\*", r"\1", _txt, flags=_re.S)
    _txt = _re.sub(r"(?m)^#{1,6}\s+", "", _txt)
    _txt = _txt.replace("__", "")
    _txt = _airify(_txt)
    _txt = _sanitize_public_post_text(_txt)
    if allow_hashtags and _generated_hashtags:
        _txt = _append_public_hashtags(_txt, _generated_hashtags)
    result["generated_hashtags"] = list(_generated_hashtags)
    result["text"] = _txt
    source = str(image_source or "ai").lower()
    if (
        str(project_id or "") == "688556520514"
        and result.get("text_engine") == "deterministic_uncertain_fallback"
    ):
        try:
            recovery = _u42_uncertain_reuse_banner(
                account_id, project_id, theme
            )
            result["banner"] = recovery["path"]
            result["banner_engine"] = "reuse_published_project_asset"
            result["banner_model"] = "none"
            result["banner_recovery_source_record_id"] = recovery["source_record_id"]
            result["banner_recovery_score"] = recovery["score"]
            print(
                "[banner_reuse] U42 uncertain recovery:",
                recovery["path"],
                "| source_record=", recovery["source_record_id"],
                flush=True,
            )
        except Exception as exc:
            result["banner"] = None
            result["banner_error"] = "U42_RECOVERY_BANNER_FAILED: " + str(exc)[:180]
        return result

    if result.get("text_engine") == "deterministic_uncertain_fallback" and source == "ai":
        # The paid text outcome is quarantined as ambiguous. Never buy another
        # image while recovering that same slot: reuse one already-published,
        # validated asset from the exact project and keep the entire fallback
        # provider-free. Ocean normally uses image_source=reuse and U42 has its
        # own semantic branch above; this path covers Dushi/Manicure and future
        # projects that explicitly provide a deterministic text recovery.
        try:
            recovery=_social_reuse_published_banner(account_id,project_id,theme)
            result["banner"]=recovery["path"]
            result["banner_engine"]="reuse_published_project_asset"
            result["banner_model"]="none"
            result["banner_recovery_source_record_id"]=recovery["source_record_id"]
        except Exception as exc:
            result["banner"]=None
            result["banner_error"]="SOCIAL_UNCERTAIN_RECOVERY_BANNER_FAILED: "+str(exc)[:180]
        return result

    if result.get("text_engine") == "deterministic_provider_recovery" and source == "ai":
        try:
            recovery=_social_reuse_published_banner(account_id,project_id,theme); result["banner"]=recovery["path"]; result["banner_engine"]="reuse_published_project_asset"; result["banner_model"]="none"; result["banner_recovery_source_record_id"]=recovery["source_record_id"]
        except Exception as exc:
            result["banner"]=None; result["banner_error"]="SOCIAL_RECOVERY_BANNER_FAILED: "+str(exc)[:180]
        return result

    if source == "reuse":
        try:
            fpath = _reuse_existing_ocean_social_banner(
                account_id, project_id, theme, result.get("text", "")
            )
            result["banner"] = fpath
            result["banner_engine"] = "reuse_existing_social"
            result["banner_model"] = "none"
            print(f"[banner_reuse] ready: {fpath} bytes={os.path.getsize(fpath)}", flush=True)
        except Exception as exc:
            result["banner"] = None
            result["banner_error"] = "REUSE_BANNER_FAILED: " + str(exc)[:180]
            print("[banner_reuse] failed: " + str(exc)[:180], flush=True)
        return result
    if source == "pexels":
        try:
            fpath = _generate_pexels_social_asset(account_id, theme, result.get("text", ""))
            result["banner"] = fpath
            result["banner_engine"] = "pexels"
            result["banner_model"] = "stock-photo"
            print(f"[banner_pexels] ready: {fpath} bytes={os.path.getsize(fpath)}", flush=True)
        except Exception as exc:
            result["banner"] = None
            result["banner_error"] = "PEXELS_BANNER_FAILED: " + str(exc)[:180]
            print("[banner_pexels] failed: " + str(exc)[:180], flush=True)
        return result
    if source != "ai":
        result["banner"] = None
        result["banner_error"] = "UNKNOWN_IMAGE_SOURCE: " + source[:80]
        return result
    try:
        # Authoritative social banner path: tuned OpenAI gpt-image-2 prompt.
        # GigaChat is not used here. If OpenAI image generation fails, keep
        # fail-closed behavior: no banner -> no social publication.
        os.makedirs(BANNER_DIR, exist_ok=True)
        _title = _make_title(account_id, result.get("text", "") or theme)
        _allowed_dates = ", ".join(
            (date.today() + timedelta(days=_i)).strftime("%d.%m.%Y")
            for _i in range(8)
        )
        _prompt = _build_banner_prompt(
            theme,
            banner_prompt,
            _title,
            _allowed_dates,
        )
        import hashlib as _hashlib
        _image_intent = _hashlib.sha256((
            str(account_id) + "\n" + _prompt + "\n" +
            "gpt-image-2|1024x1024|medium|banner_social_post"
        ).encode("utf-8", "ignore")).hexdigest()
        fpath = os.path.join(BANNER_DIR, f"{account_id}_social_{_image_intent}.png")
        from app.db.session import SessionLocal as _SessionLocal
        from app.services import image_service as _IMG
        _db=_SessionLocal()
        try:
            _out=_IMG.generate_banner_financially_guarded(
                _db, account_id, _prompt, fpath, size="1024x1024", quality="medium",
                model=_IMG.PREMIUM_MODEL, operation="banner_social_post"
            )
        finally:
            _db.close()
        import os as _os
        if not _out.get("ok") or not (_os.path.exists(fpath) and _os.path.getsize(fpath) > 0):
            raise RuntimeError(_out.get("error") or "OPENAI_GPT_IMAGE_2_BANNER_MISSING")
        result["banner"] = fpath
        result["banner_engine"] = "openai_gpt_image_2"
        result["banner_model"] = "gpt-image-2"
        print(
            f"[banner_openai] ready: {fpath} bytes={_os.path.getsize(fpath)} model=gpt-image-2 quality=medium",
            flush=True,
        )
    except Exception as e:
        result["banner"] = None
        result["banner_error"] = "OPENAI_BANNER_FAILED: " + str(e)[:180]
        print("[banner_openai] failed: " + str(e)[:180], flush=True)
    return result


def _banner_delivery_ready(post, image_source="ai"):
    """Social projects configured with media must never degrade to bad/text-only media."""
    if str(image_source or "ai").lower() not in ("ai", "pexels", "reuse"):
        return True, None
    path = (post or {}).get("banner")
    if not (path and os.path.exists(path)):
        err = (post or {}).get("banner_error") or "BANNER_ASSET_MISSING"
        return False, str(err)[:200]
    try:
        size = os.path.getsize(path)
        if size < 16 * 1024:
            return False, "BANNER_ASSET_TOO_SMALL_BYTES:%s" % size
        from PIL import Image as _Image
        with _Image.open(path) as im:
            width, height = im.size
            im.verify()
        if width < 320 or height < 320:
            return False, "BANNER_ASSET_TOO_SMALL_DIMENSIONS:%sx%s" % (width, height)
        return True, None
    except Exception as exc:
        return False, "BANNER_ASSET_INVALID: " + str(exc)[:160]


def _active(sub):
    try: paid = date.fromisoformat(sub.get("paid_at", ""))
    except Exception: return False
    return (date.today() - paid).days < int(sub.get("period_days", 30))

def main():
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "posting_subscription").all()
        print(f"[posting] подписок найдено: {len(rows)}"); active = 0
        for r in rows:
            try: sub = json.loads(r.value)
            except Exception: print(f"[posting] {r.account_id}: битая запись — пропуск"); continue
            if not _active(sub): print(f"[posting] {r.account_id}: подписка истекла — пропуск"); continue
            active += 1
            srow = db.query(Storage).filter(
                Storage.account_id == r.account_id, Storage.key == "posting_settings").first()
            settings = {}
            if srow:
                try: settings = json.loads(srow.value)
                except Exception: settings = {}
            times = settings.get("times", DEFAULT_TIMES)
            print(f"[posting] {r.account_id}: активна ({sub.get('platforms')}), время постов {', '.join(times)}")
        print(f"[posting] активных подписок: {active}")
    finally:
        db.close()

def _demo(theme):
    print(f"[demo] генерирую пост на тему: {theme}\n", flush=True)
    r = generate_post("otdushi", theme)
    print("=== ТЕКСТ ПОСТА ===\n" + r["text"])
    print("\n=== БАННЕР ===")
    print("сохранён: " + r["banner"] if r.get("banner") else "не сгенерился: " + str(r.get("banner_error")))


# --- ОТПРАВКА ПОСТА В TELEGRAM + счётчик лимита ---
DAILY_LIMIT = 3

def _today_key(account_id):
    return "posting_count:" + account_id + ":" + date.today().isoformat()

def _posts_today(db, account_id):
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == _today_key(account_id)).first()
    if row:
        try: return int(row.value)
        except Exception: return 0
    return 0

def _bump_posts_today(db, account_id):
    key = _today_key(account_id)
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    if row:
        try: n = int(row.value)
        except Exception: n = 0
        row.value = str(n + 1)
    else:
        db.add(Storage(account_id=account_id, key=key, value="1"))
    db.commit()

def _tg_safe_html(text):
    """Валидный для Telegram HTML: только разрешённые теги, без вложенности
    одинаковых и без непарных закрывающих. Иначе Telegram отклоняет сообщение."""
    import re as _re_tg
    ALLOWED = ("b", "strong", "i", "em", "u", "s", "code", "pre", "a")
    t = text or ""
    t = _re_tg.sub(r"</?\s*([a-zA-Z0-9]+)[^>]*>",
                   lambda m: m.group(0) if m.group(1).lower() in ALLOWED else "", t)
    out, stack, pos = [], [], 0
    for m in _re_tg.finditer(r"</?\s*([a-zA-Z0-9]+)[^>]*>", t):
        out.append(t[pos:m.start()]); pos = m.end()
        tag = m.group(1).lower()
        if not m.group(0).startswith("</"):
            if tag in stack:
                continue
            stack.append(tag); out.append(m.group(0))
        else:
            if tag not in stack:
                continue
            while stack and stack[-1] != tag:
                out.append("</%s>" % stack.pop())
            stack.pop(); out.append(m.group(0))
    out.append(t[pos:])
    while stack:
        out.append("</%s>" % stack.pop())
    return "".join(out)


def _send_telegram(chat_id, text, banner_path, *, tenant_scope):
    text = _tg_safe_html(text)
    import requests
    from app import telegram_bot
    if not telegram_bot.BOT_TOKEN:
        return {"ok": False, "error": "нет токена бота"}
    if banner_path and os.path.exists(banner_path):
        caption = text if len(text) <= 1024 else text[:1000] + "…"
        send_path = banner_path
        try:
            from PIL import Image as _Img
            _im = _Img.open(banner_path).convert("RGB"); _im.thumbnail((1080, 1080))
            send_path = banner_path + ".jpg"; _im.save(send_path, "JPEG", quality=85)
        except Exception:
            send_path = banner_path
        r = None
        for _attempt in range(3):
            try:
                with open(send_path, "rb") as ph:
                    resp = requests.post(telegram_bot.API_BASE + "/sendPhoto",
                        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"photo": ph}, timeout=120)
                r = resp.json()
                break
            except Exception as _e:
                print("[tg] попытка " + str(_attempt + 1) + "/3 не удалась: " + str(_e)[:120], flush=True)
                import time as _t; _t.sleep(5)
        if r is None:
            return {"ok": False, "error": "telegram недоступен после 3 попыток"}
        if r.get("ok") and len(text) > 1024:
            requests.post(telegram_bot.API_BASE + "/sendMessage",
                data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=60)
        return r
    return telegram_bot.send_telegram_message(chat_id, text)

def send_post(account_id, chat_id, theme, text_prompt="", banner_prompt="", occasion=""):
    db = SessionLocal()
    try:
        today = _posts_today(db, account_id)
    finally:
        db.close()
    if today >= DAILY_LIMIT:
        return {"ok": False, "error": "лимит " + str(DAILY_LIMIT) + " постов на сегодня исчерпан (" + str(today) + ")"}
    post = generate_post(account_id, theme, text_prompt, banner_prompt, occasion)
    _media_ok, _media_err = _banner_delivery_ready(post, "ai")
    if not _media_ok:
        return {"ok": False, "error": "BANNER_REQUIRED: " + str(_media_err), "banner": None}
    res = _send_telegram(chat_id, post["text"], post.get("banner"), tenant_scope=account_id)
    if res.get("ok"):
        db = SessionLocal()
        try: _bump_posts_today(db, account_id)
        finally: db.close()
    return {"ok": bool(res.get("ok")), "send": res, "banner": post.get("banner")}

def _send_demo(theme, chat_id):
    print("[send] генерирую и шлю пост: " + theme + " -> " + chat_id, flush=True)
    r = send_post("otdushi", chat_id, theme)
    print("РЕЗУЛЬТАТ:", "OK отправлено" if r.get("ok") else "FAIL " + str(r.get("error") or r.get("send")))



# --- ЧТЕНИЕ ПОСТОВ КАНАЛА (для генерации «по примеру») ---
def _fetch_channel_posts(channel, n=12):
    import requests, re, html as _html
    ch = channel.lstrip("@")
    from proxy_pool import get_intl_requests_proxies
    try:
        resp = requests.get("https://t.me/s/" + ch, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"},
                            proxies=get_intl_requests_proxies())
        htmltext = resp.text
    except Exception as e:
        return {"ok": False, "error": "не скачал t.me/s/" + ch + ": " + str(e)[:150]}
    blocks = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', htmltext, re.S)
    posts = []
    for b in blocks:
        t = re.sub(r'<br\s*/?>', '\n', b)
        t = re.sub(r'<[^>]+>', '', t)
        t = _html.unescape(t).strip()
        if t:
            posts.append(t)
    return {"ok": True, "posts": posts[-n:]}

def _channel_demo(channel):
    print("[channel] читаю посты канала: " + channel, flush=True)
    r = _fetch_channel_posts(channel)
    if not r.get("ok"):
        print("FAIL:", r.get("error")); return
    posts = r["posts"]
    print("нашёл постов:", len(posts))
    for i, p in enumerate(posts, 1):
        print("--- пост", i, "---"); print(p[:400])



# --- ОТПРАВКА ПОСТА В ВКОНТАКТЕ ---
def _vk_api_failure(payload, stage, prefix=""):
    """Normalize VK API failures and arm a shared live-delivery cooldown.

    Error 9 (Flood control) and 29 (rate limit) are external retryable states.
    They must stop the current call immediately: retrying the same method three
    times in a few seconds only extends anti-spam pressure for every project
    sharing the token/IP.
    """
    err = (payload or {}).get("error") or {}
    try:
        code = int(err.get("error_code") or 0)
    except Exception:
        code = 0
    message = str(err.get("error_msg") or err or payload)[:180]
    result = {
        "ok": False,
        "code": code,
        "stage": str(stage or "vk_api"),
        "error": str(prefix or "") + str(stage or "VK API") + ": " + message,
    }
    if code in {9, 29}:
        retry_seconds = 3600 if code == 9 else 900
        try:
            retry_seconds = int(_set_social_vk_rate_backoff(retry_seconds) or retry_seconds)
        except Exception:
            pass
        result.update({
            "reason": "vk_rate_limited",
            "dependency_state": "waiting_external",
            "retryable_external": True,
            "retry_after_seconds": retry_seconds,
        })
    return result


def _send_vk(owner_id, text, banner_path, *, tenant_scope):
    import os, re as _re_vk, requests
    # VK receives plain text. Keep transport direct: routing image uploads
    # through an international proxy caused intermittent upload-only failures.
    text = _re_vk.sub(r"<[^>]+>", "", text or "")
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    try:
        remaining = int(_social_vk_rate_backoff_remaining() or 0)
    except Exception:
        remaining = 0
    if remaining > 0:
        return {
            "ok": False,
            "code": 0,
            "stage": "rate_backoff",
            "error": "VK cooldown после rate limit: %s сек" % remaining,
            "reason": "vk_rate_limited",
            "dependency_state": "waiting_external",
            "retryable_external": True,
            "retry_after_seconds": remaining,
        }
    V = "5.199"
    group_id = str(owner_id).lstrip("-")
    attachments = None

    if banner_path and os.path.exists(banner_path):
        last_error = None
        for attempt in range(3):
            try:
                send_path = banner_path
                try:
                    from PIL import Image as _Img
                    im = _Img.open(banner_path).convert("RGB")
                    im.thumbnail((1080, 1080))
                    send_path = banner_path + ".vk.jpg"
                    im.save(send_path, "JPEG", quality=85)
                except Exception:
                    send_path = banner_path

                up = requests.get(
                    "https://api.vk.com/method/photos.getWallUploadServer",
                    params={"access_token": token, "v": V, "group_id": group_id},
                    timeout=30,
                ).json()
                if up.get("error"):
                    return _vk_api_failure(
                        up, "photos.getWallUploadServer", "загрузка фото ВК: "
                    )
                if "response" not in up or not (up.get("response") or {}).get("upload_url"):
                    raise RuntimeError("photos.getWallUploadServer: " + str(up)[:180])
                upload_url = up["response"]["upload_url"]

                with open(send_path, "rb") as ph:
                    upr = requests.post(upload_url, files={"photo": ph}, timeout=120).json()
                for key in ("photo", "server", "hash"):
                    if key not in upr:
                        raise RuntimeError("wall upload missing %s: %s" % (key, str(upr)[:180]))

                sv = requests.get(
                    "https://api.vk.com/method/photos.saveWallPhoto",
                    params={"access_token": token, "v": V, "group_id": group_id,
                            "photo": upr["photo"], "server": upr["server"], "hash": upr["hash"]},
                    timeout=30,
                ).json()
                if sv.get("error"):
                    return _vk_api_failure(
                        sv, "photos.saveWallPhoto", "загрузка фото ВК: "
                    )
                if "response" not in sv or not sv.get("response"):
                    raise RuntimeError("photos.saveWallPhoto: " + str(sv)[:180])
                p = sv["response"][0]
                attachments = "photo" + str(p["owner_id"]) + "_" + str(p["id"])
                break
            except Exception as exc:
                last_error = "загрузка фото ВК: " + type(exc).__name__ + " " + str(exc)[:200]
                if attempt < 2:
                    import time as _t2
                    _t2.sleep(3)
                    continue
        if not attachments:
            return {"ok": False, "error": last_error or "загрузка фото ВК не удалась"}

    params = {"access_token": token, "v": V, "owner_id": str(owner_id),
              "from_group": 1, "message": text}
    if attachments:
        params["attachments"] = attachments
    try:
        r = requests.get("https://api.vk.com/method/wall.post", params=params, timeout=30).json()
    except Exception as exc:
        return {"ok": False, "error": "wall.post transport: " + str(exc)[:200]}
    if "response" in r:
        try:
            _clear_social_vk_rate_backoff()
        except Exception:
            pass
        return {"ok": True, "post_id": r["response"].get("post_id"), "attachment": attachments}
    if r.get("error"):
        return _vk_api_failure(r, "wall.post")
    return {"ok": False, "error": "wall.post: " + str(r)[:200]}

def _vk_demo(theme, owner_id):
    print("[vk] генерирую и шлю пост в ВК: " + theme + " -> " + str(owner_id), flush=True)
    post = generate_post("otdushi", theme)
    r = _send_vk(owner_id, post["text"], post.get("banner"), tenant_scope="otdushi")
    print("РЕЗУЛЬТАТ:", "OK опубликовано post_id=" + str(r.get("post_id")) if r.get("ok") else "FAIL " + str(r.get("error")))


def _style_demo(channel, theme):
    print("[style] пост в стиле канала " + channel + " на тему: " + theme, flush=True)
    r = generate_post("otdushi", theme, channel=channel)
    print("=== ТЕКСТ В СТИЛЕ КАНАЛА ===")
    print(r["text"])



# --- АВТОПОСТ: генерит пост в стиле канала и публикует в ТГ+ВК с лимитом ---
_CTA_VARIANTS = [
    "Пишите сюда — отвечу лично:",
    "Задайте вопрос напрямую:",
    "Хотите так же? Напишите:",
    "Как подключить — расскажу здесь:",
    "Спросить про подключение:",
    "Обсудить ваш случай:",
    "Забрать бесплатный тест:",
    "Остались вопросы? Пишите:",
    "Разберём вашу ситуацию — напишите:",
]


def _with_tags(text, tags):
    """Guarantee 5–10 hashtags; prefer the model's own topical choice.

    If the model already supplied 5–10 unique hashtags, keep them exactly.
    Otherwise preserve any useful model tags and top up from the project's
    topic-aware fallback pool.
    """
    import random as _rnd_t
    import re as _re_t

    existing = _extract_public_hashtags(text, limit=10)
    if 5 <= len(existing) <= 10:
        return text

    if not tags:
        return text

    def _norm(items):
        if not items:
            return []
        if isinstance(items, str):
            items = [t.strip() for t in items.replace(",", " ").split() if t.strip()]
        out = []
        seen = set()
        for t in items:
            t = str(t).strip()
            if not t:
                continue
            t = t if t.startswith("#") else "#" + t
            key = t.lower()
            if key not in seen:
                seen.add(key)
                out.append(t)
        return out

    topical = []
    if isinstance(tags, dict):
        base = _norm(tags.get("base") or [])
        hay = (text or "").lower()
        ranked = []
        for grp in tags.get("groups") or []:
            kws = [str(x).lower().strip() for x in (grp.get("keywords") or []) if str(x).strip()]
            score = sum(1 for kw in kws if kw in hay)
            ranked.append((score, _norm(grp.get("tags") or [])))
        ranked.sort(key=lambda x: x[0], reverse=True)
        topical = ranked[0][1] if ranked and ranked[0][0] > 0 else []
        pool = []
        for t in topical + base:
            if t.lower() not in {x.lower() for x in pool}:
                pool.append(t)
    else:
        pool = _norm(tags)

    existing_keys = {x.lower() for x in existing}
    pool = [x for x in pool if x.lower() not in existing_keys]
    available_total = len(existing) + len(pool)
    if available_total < 5:
        return text

    target_n = min(available_total, _rnd_t.randint(5, 10))
    chosen = list(existing)

    topical_pool = [x for x in topical if x.lower() not in existing_keys]
    want_topical = min(len(topical_pool), min(3, max(0, target_n-len(chosen))))
    if want_topical:
        chosen.extend(_rnd_t.sample(topical_pool, want_topical))

    chosen_keys = {x.lower() for x in chosen}
    rest_pool = [x for x in pool if x.lower() not in chosen_keys]
    need = max(0, target_n-len(chosen))
    if need:
        chosen.extend(_rnd_t.sample(rest_pool, min(len(rest_pool), need)))

    # Remove an incomplete model hashtag-only line before appending the final set.
    base_text = _re_t.sub(
        r"(?m)^\s*(?:#[A-Za-zА-Яа-яЁё0-9_]+[\s,]*){1,10}\s*$",
        "",
        str(text or ""),
    )
    base_text = _re_t.sub(r"\n{3,}", "\n\n", base_text).strip()
    return _append_public_hashtags(base_text, chosen[:10])


def _air_blocks(text):
    """Разводит строки поста пустой строкой — «воздух». Если автор уже поставил
    пустые строки (2 и больше), ничего не трогаем."""
    t = (text or "").strip()
    if t.count("\n\n") >= 2:
        return t
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    return "\n\n".join(lines)


def _with_contact(text, contact):
    text = _air_blocks(text)
    contact = (contact or "").strip()
    if not contact:
        return text
    import random as _rnd
    return text.rstrip() + "\n\n" + _rnd.choice(_CTA_VARIANTS) + " " + contact

def autopost(account_id):
    import json as _json, random
    db = SessionLocal()
    try:
        srow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_settings").first()
        if not srow:
            print("[autopost] нет настроек:", account_id); return
        cfg = _json.loads(srow.value)
        if _posts_today(db, account_id) >= DAILY_LIMIT:
            print("[autopost] лимит " + str(DAILY_LIMIT) + " постов на сегодня исчерпан"); return
    finally:
        db.close()
    if _is_nail_ctx(account_id, cfg):
        theme, _tr = _nail_theme(account_id, cfg.get("themes") or [])
    else:
        theme = random.choice(cfg.get("themes") or ["поздравление"])
    print("[autopost] тема:", theme, flush=True)
    post = generate_post(account_id, theme, text_prompt=cfg.get("text_prompt", ""), channel=cfg.get("style_source", ""), image_source=cfg.get("image_source", "ai"), project_id=cfg.get("id", ""), allow_hashtags=bool(cfg.get("allow_hashtags")))
    _media_ok, _media_err = _banner_delivery_ready(post, cfg.get("image_source", "ai"))
    if not _media_ok:
        print("[autopost] BANNER_REQUIRED:", _media_err, flush=True)
        return
    results = {}
    if cfg.get("channel_tg"):
        results["tg"] = _send_telegram(cfg["channel_tg"], _with_contact(post["text"], cfg.get("contact_tg", "")), post.get("banner"), tenant_scope=account_id)
    if cfg.get("vk_owner_id"):
        results["vk"] = _send_vk(int(cfg["vk_owner_id"]), _with_contact(post["text"], cfg.get("contact_vk", "")), post.get("banner"), tenant_scope=account_id)
    if any(r.get("ok") for r in results.values()):
        db = SessionLocal()
        try: _bump_posts_today(db, account_id)
        finally: db.close()
    print("[autopost] итог:", {k: ("OK" if v.get("ok") else str(v.get("error") or v.get("send"))) for k, v in results.items()})


def _sub_active(sub):
    if not sub:
        return False
    # An explicit lifecycle state outranks date arithmetic. This prevents an
    # expired/cancelled service from being resurrected by stale copied dates.
    status = str(sub.get("status") or "").strip().lower()
    if status in {"expired", "cancelled", "canceled", "suspended", "blocked", "expired_owner_confirmed"}:
        return False
    try:
        paid = date.fromisoformat(sub.get("paid_at", ""))
        return (date.today() - paid).days < int(sub.get("period_days", 30))
    except Exception:
        return False


def _social_published_at_utc(rec):
    from datetime import datetime as _dt, timezone as _tz
    raw = str((rec or {}).get("published_at") or "").strip()
    if not raw:
        return None
    try:
        dt = _dt.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_tz.utc)


def _social_delivery_timing(rec, delivery_due_utc, grace_minutes=15):
    """Return deterministic delivery timing from durable published_at."""
    from datetime import timedelta as _td
    published = _social_published_at_utc(rec)
    if published is None or delivery_due_utc is None:
        return {
            "published_at": str((rec or {}).get("published_at") or "") or None,
            "timing_known": False,
            "late": False,
            "delay_minutes": None,
            "late_minutes_after_grace": None,
        }
    nominal_seconds = (published - delivery_due_utc).total_seconds()
    grace_deadline = delivery_due_utc + _td(minutes=int(grace_minutes))
    late_seconds = (published - grace_deadline).total_seconds()
    return {
        "published_at": published.isoformat(),
        "timing_known": True,
        "late": late_seconds > 0,
        "delay_minutes": max(0, int((nominal_seconds + 59) // 60)) if nominal_seconds > 0 else 0,
        "late_minutes_after_grace": max(0, int((late_seconds + 59) // 60)) if late_seconds > 0 else 0,
    }


def persist_social_delivery_sla_breaches(health):
    """Durable idempotent journal of detected Social SLA breaches."""
    from datetime import datetime as _dt, timezone as _tz
    account_id = str((health or {}).get("account_id") or "")
    breaches = [
        x for x in ((health or {}).get("sla_breaches") or [])
        if isinstance(x, dict) and str(x.get("key") or "")
    ]
    if not account_id or not breaches:
        return {"ok": True, "created": 0, "existing": 0, "keys": []}
    db = SessionLocal()
    created = existing = 0
    keys = []
    try:
        for item in breaches:
            generation_key = str(item.get("key") or "")
            storage_key = "social_delivery_sla_breach:" + generation_key
            keys.append(storage_key)
            row = db.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == storage_key,
            ).first()
            payload = {
                "generation_key": generation_key,
                "record_id": item.get("record_id"),
                "project_id": (health or {}).get("project_id"),
                "project_name": (health or {}).get("project_name"),
                "service_date": item.get("service_date"),
                "slot": item.get("slot"),
                "delivery_due_at": item.get("delivery_due_at"),
                "delivery_due_at_utc": item.get("delivery_due_at_utc"),
                "published_at": item.get("published_at"),
                "delay_minutes": item.get("delay_minutes"),
                "late_minutes_after_grace": item.get("late_minutes_after_grace"),
                "owner_action_required": False,
            }
            if row:
                existing += 1
                continue
            payload["detected_at"] = _dt.now(_tz.utc).isoformat()
            db.add(Storage(
                account_id=account_id,
                key=storage_key,
                value=json.dumps(payload, ensure_ascii=False),
            ))
            created += 1
        if created:
            db.commit()
        else:
            db.rollback()
        return {"ok": True, "created": created, "existing": existing, "keys": keys}
    except Exception as exc:
        db.rollback()
        return {
            "ok": False, "created": created, "existing": existing,
            "keys": keys, "reason": type(exc).__name__ + ": " + str(exc)[:180],
        }
    finally:
        db.close()


def social_project_daily_delivery_health(
    account_id="u2",
    project_name="Океан Клиентов и Борис",
    now_utc=None,
    project_id=None,
):
    """Daily plan/fact for one Social project, with no external API calls.

    Counts only slots whose local scheduled time is already due. A slot is done
    only when its durable published record contains both Telegram and VK IDs.
    This lets the canonical worker detect a missed/partial slot and keep
    self-healing without alarming the owner before the slot actually arrives.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    now = now_utc or _dt.now(_tz.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_tz.utc)

    db = SessionLocal()
    try:
        prow = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == "posting_projects"
        ).first()
        srow = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == "posting_posts"
        ).first()
        try:
            projects = _json.loads(prow.value or "[]") if prow else []
        except Exception:
            return {"state":"degraded","ok":False,"reason":"posting_projects_invalid_json"}
        try:
            posts = _json.loads(srow.value or "[]") if srow else []
        except Exception:
            posts = []
    finally:
        db.close()

    target = None
    if project_id not in (None,""):
        target = next((
            p for p in projects
            if isinstance(p,dict)
            and str(p.get("id") or "") == str(project_id)
        ),None)
    if target is None:
        target = next((
            p for p in projects
            if isinstance(p,dict)
            and str(p.get("name") or "") == str(project_name)
        ),None)
    if not target and str(project_name) == "Океан Клиентов и Борис":
        target = next((p for p in projects if isinstance(p,dict) and _ocean_clients_project(p)), None)
    if not target:
        return {"state":"degraded","ok":False,"reason":"project_missing","project_name":project_name}

    # An intentionally disabled autopilot is not a delivery failure.
    # Either explicit expectation OFF or project mode OFF is authoritative.
    if target.get("expected_auto_generation") is False or str(target.get("mode") or "").lower() == "off":
        return {
            "state":"ok","status":"disabled_intentionally","ok":True,
            "account_id":account_id,"project_id":str(target.get("id") or ""),
            "project_name":str(target.get("name") or project_name),
            "planned_total":0,"due_by_now":0,"complete_due":0,
            "required_platforms":str(
                target.get("platforms")
                or ((target.get("subscription") or {}).get("platforms") if isinstance(target.get("subscription"),dict) else "")
                or "both"
            ).lower(),
            "missing_due":0,"partial_due":0,"overdue_due":0,
            "slots":[],"previous_day_missed":0,"previous_day_slots":[],
            "owner_action_required":False,
        }

    target_subscription=target.get("subscription") if isinstance(target.get("subscription"),dict) else {}
    required_platforms=str(
        target.get("platforms")
        or target_subscription.get("platforms")
        or "both"
    ).lower()
    if required_platforms not in {"tg","vk","both"}:
        return {
            "state":"degraded","ok":False,
            "reason":"platforms_invalid",
            "account_id":account_id,
            "project_id":str(target.get("id") or ""),
            "project_name":str(target.get("name") or project_name),
            "required_platforms":required_platforms,
            "owner_action_required":True,
        }
    require_tg=required_platforms in {"tg","both"}
    require_vk=required_platforms in {"vk","both"}

    def _slot_complete(item):
        return bool(
            item.get("published")
            and (item.get("tg") if require_tg else True)
            and (item.get("vk") if require_vk else True)
        )

    try:
        offset = int(target.get("tz_offset") if target.get("tz_offset") not in (None, "") else 3)
    except Exception:
        offset = 0
    local_now = now + _td(hours=offset)
    local_day = local_now.strftime("%Y%m%d")
    # Scheduled hour is the draft-generation slot. In moderate mode a positive
    # auto_publish_hours is an intentional owner review window, so delivery is
    # not overdue until slot + review window (+ 15 min delivery grace).
    delivery_delay_hours = (
        int(target.get("auto_publish_hours") or 0)
        if str(target.get("mode") or "auto") == "moderate"
        else 0
    )
    raw_times = list(target.get("times") or target.get("schedule") or [])
    slots = []
    for raw in raw_times:
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            hour = int(value.split(":",1)[0])
            minute = int(value.split(":",1)[1]) if ":" in value else 0
        except Exception:
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        slots.append((hour, minute, f"{hour:02d}:{minute:02d}"))
    slots = sorted(set(slots))
    pid = str(target.get("id") or "")
    expected = {}
    for hour, minute, label in slots:
        slot_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delivery_local = slot_local + _td(hours=delivery_delay_hours)
        if local_now < delivery_local:
            continue
        key = f"social-slot:{account_id}:{pid}:{local_day}_{hour:02d}"
        delivery_due_utc = delivery_local - _td(hours=offset)
        expected[key] = {
            "slot":label,
            "delivery_due_at":delivery_local.strftime("%Y-%m-%d %H:%M"),
            "delivery_due_at_utc":delivery_due_utc.astimezone(_tz.utc).isoformat(),
            "key":key,"published":False,"tg":False,"vk":False,"record_id":None,
            "published_at":None,"timing_known":False,"late":False,
            "delay_minutes":None,"late_minutes_after_grace":None,
        }

    # New scheduled records have deterministic generation_key. This is the
    # authoritative identity for a slot and prevents duplicates from satisfying
    # another slot's plan/fact.
    slot_records = {}
    tracked_dates = set()
    prefix = f"social-slot:{account_id}:{pid}:"
    for rec in posts:
        if not isinstance(rec,dict) or str(rec.get("project_id") or "") != pid:
            continue
        key = str(rec.get("generation_key") or "")
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            day_part = suffix.split("_",1)[0]
            if len(day_part)==8 and day_part.isdigit():
                tracked_dates.add(day_part)
            slot_records[key] = rec
        if key not in expected:
            continue
        ids = rec.get("ids") or {}
        tg = ids.get("tg") if isinstance(ids,dict) else {}
        vk = ids.get("vk") if isinstance(ids,dict) else {}
        try:
            delivery_due_utc = _dt.fromisoformat(
                str(expected[key].get("delivery_due_at_utc") or "").replace("Z","+00:00")
            )
            if delivery_due_utc.tzinfo is None:
                delivery_due_utc = delivery_due_utc.replace(tzinfo=_tz.utc)
            delivery_due_utc = delivery_due_utc.astimezone(_tz.utc)
        except Exception:
            delivery_due_utc = None
        timing = _social_delivery_timing(rec, delivery_due_utc, grace_minutes=15)
        expected[key].update({
            "published": str(rec.get("status") or "") == "published",
            "tg": bool(isinstance(tg,dict) and tg.get("message_id")),
            "vk": bool(isinstance(vk,dict) and vk.get("post_id")),
            "record_id": rec.get("id"),
            **timing,
        })

    due_count = len(expected)
    complete = [v for v in expected.values() if _slot_complete(v)]
    partial = [
        v for v in expected.values()
        if v["published"] and not _slot_complete(v)
    ]
    missing = [v for v in expected.values() if not v["published"]]
    late_complete = [v for v in complete if v.get("timing_known") and v.get("late")]
    timing_unknown_complete = [v for v in complete if not v.get("timing_known")]

    # 15-minute grace covers normal generation/delivery jitter. Before grace,
    # the worker keeps trying but health is not degraded yet.
    overdue = []
    for v in missing + partial:
        try:
            delivery_local = _dt.strptime(
                str(v.get("delivery_due_at") or ""), "%Y-%m-%d %H:%M"
            ).replace(tzinfo=local_now.tzinfo)
        except Exception:
            hour, minute = [int(x) for x in v["slot"].split(":")]
            delivery_local = (
                local_now.replace(hour=hour,minute=minute,second=0,microsecond=0)
                + _td(hours=delivery_delay_hours)
            )
        if local_now >= delivery_local + _td(minutes=15):
            overdue.append(v)

    # Do not forget a missed previous day after midnight. Enforcement starts
    # only from the first date where deterministic slot keys already existed,
    # so historical legacy posts cannot create false incidents.
    previous_day_slots = []
    previous_day_missed = []
    previous_day_late = []
    previous_day = (local_now.date() - _td(days=1)).strftime("%Y%m%d")
    guard_start = min(tracked_dates) if tracked_dates else None
    if guard_start and previous_day >= guard_start:
        prev_local = local_now - _td(days=1)
        for hour, minute, label in slots:
            key = f"{prefix}{previous_day}_{hour:02d}"
            rec = slot_records.get(key)
            ids = (rec or {}).get("ids") or {}
            tg = ids.get("tg") if isinstance(ids,dict) else {}
            vk = ids.get("vk") if isinstance(ids,dict) else {}
            delivery_local = (
                prev_local.replace(hour=hour,minute=minute,second=0,microsecond=0)
                + _td(hours=delivery_delay_hours)
            )
            delivery_due = local_now >= delivery_local + _td(minutes=15)
            delivery_due_utc = (delivery_local - _td(hours=offset)).astimezone(_tz.utc)
            timing = _social_delivery_timing(rec or {}, delivery_due_utc, grace_minutes=15)
            item = {
                "slot":label,"key":key,
                "service_date":f"{previous_day[:4]}-{previous_day[4:6]}-{previous_day[6:8]}",
                "delivery_due_at":delivery_local.strftime("%Y-%m-%d %H:%M"),
                "delivery_due_at_utc":delivery_due_utc.isoformat(),
                "delivery_due":delivery_due,
                "published": bool(rec and str(rec.get("status") or "")=="published"),
                "tg": bool(isinstance(tg,dict) and tg.get("message_id")),
                "vk": bool(isinstance(vk,dict) and vk.get("post_id")),
                "record_id": (rec or {}).get("id"),
                **timing,
            }
            previous_day_slots.append(item)
            if delivery_due and not _slot_complete(item):
                previous_day_missed.append(item)
            elif (
                delivery_due and _slot_complete(item)
                and item.get("timing_known") and item.get("late")
            ):
                previous_day_late.append(item)

    current_service_date = local_now.date().isoformat()
    for item in expected.values():
        item.setdefault("service_date", current_service_date)
    sla_breaches = [dict(x) for x in late_complete] + [dict(x) for x in previous_day_late]

    # A confirmed retryable provider outage is not an owner task. On the live
    # health path only, classify the exact missing required platforms against
    # the cached read-only transport heartbeat. Explicit historical QA times
    # stay deterministic and do not depend on today's provider state.
    external_wait_platforms = set()
    previous_day_waiting_external = False
    if previous_day_missed and now_utc is None:
        try:
            transport = social_transport_readiness_cached_health()
            if transport.get("fresh"):
                tproject = next((
                    x for x in (transport.get("projects") or [])
                    if str((x or {}).get("account_id") or "u2") == str(account_id)
                    and str((x or {}).get("project_id") or "") == pid
                ), None)
                if isinstance(tproject, dict):
                    for platform, key in (("tg", "telegram"), ("vk", "vk")):
                        part = tproject.get(key) if isinstance(tproject.get(key), dict) else {}
                        if (
                            not part.get("ready")
                            and part.get("retryable_external")
                            and str(part.get("dependency_state") or "") == "waiting_external"
                        ):
                            external_wait_platforms.add(platform)
                    missing_sets = []
                    for item in previous_day_missed:
                        missing_required = set()
                        if require_tg and not item.get("tg"):
                            missing_required.add("tg")
                        if require_vk and not item.get("vk"):
                            missing_required.add("vk")
                        if not item.get("record_id"):
                            missing_required.add("lifecycle")
                        missing_sets.append(missing_required)
                    previous_day_waiting_external = bool(
                        missing_sets
                        and all(ms and ms.issubset(external_wait_platforms) for ms in missing_sets)
                    )
        except Exception:
            previous_day_waiting_external = False

    if previous_day_missed and previous_day_waiting_external:
        state = "degraded"
        owner_action_required = False
        status = "previous_day_waiting_external"
    elif previous_day_missed:
        state = "critical"
        owner_action_required = True
        status = "previous_day_shortfall"
    elif overdue:
        state = "degraded"
        owner_action_required = False
        status = "self_heal_active"
    else:
        state = "ok"
        owner_action_required = False
        status = "on_track_with_sla_breach" if sla_breaches else "on_track"

    return {
        "state":state,"status":status,"ok":state=="ok","account_id":account_id,"project_id":pid,"project_name":str(target.get("name") or project_name),
        "local_date":local_now.date().isoformat(),"local_time":local_now.strftime("%H:%M"),
        "planned_total":len(slots),"delivery_delay_hours":delivery_delay_hours,
        "required_platforms":required_platforms,
        "due_by_now":due_count,"complete_due":len(complete),
        "missing_due":len(missing),"partial_due":len(partial),"overdue_due":len(overdue),
        "late_due":len(late_complete),
        "timing_unknown_due":len(timing_unknown_complete),
        "sla_ok":not bool(sla_breaches),
        "sla_breach_count":len(sla_breaches),
        "sla_breaches":sla_breaches,
        "slots":[v for v in expected.values()],
        "previous_day":previous_day,
        "failed_service_date":(
            f"{previous_day[:4]}-{previous_day[4:6]}-{previous_day[6:8]}"
            if previous_day_missed else None
        ),
        "previous_day_missed":len(previous_day_missed),
        "previous_day_late":len(previous_day_late),
        "previous_day_slots":previous_day_slots,
        "guard_start_date":guard_start,
        "self_heal":"canonical social worker retries generation every 5 minutes and due delivery every minute",
        "dependency_state":"waiting_external" if previous_day_waiting_external else None,
        "external_wait_platforms":sorted(external_wait_platforms) if previous_day_waiting_external else [],
        "owner_action_required":owner_action_required,
    }



def social_active_paid_daily_delivery_health(now_utc=None):
    """Plan/fact/SLA for every project under an active paid Social subscription.

    Client configuration gaps are surfaced separately and never cause a healthy
    sibling client to disappear from the report.
    """
    import json as _json
    db=SessionLocal()
    contours=[]
    gaps=[]
    try:
        subrows=db.query(Storage).filter(Storage.key=="posting_subscription").all()
        for subrow in subrows:
            try:
                sub=_json.loads(subrow.value or "{}")
            except Exception:
                continue
            from app.services.social_entitlement import normalize_posting_subscription
            sub,_changes=normalize_posting_subscription(sub)
            if not _sub_active(sub):
                continue
            acc=str(subrow.account_id or "")
            prow=db.query(Storage).filter(
                Storage.account_id==acc,Storage.key=="posting_projects"
            ).first()
            try:
                projects=_json.loads(prow.value or "[]") if prow else []
            except Exception:
                projects=[]
            if not isinstance(projects,list) or not projects:
                gaps.append({
                    "account_id":acc,
                    "reason":"active_subscription_without_posting_project",
                    "dependency_state":"waiting_external",
                    "owner_action_required":False,
                    "required_external_fields":_paid_social_required_destinations(sub),
                    "self_heal":"posting_onboarding_scaffold_v1_then_auto_materialize",
                })
                continue
            for project in projects:
                if not isinstance(project,dict):
                    continue
                pid=str(project.get("id") or "")
                pname=str(project.get("name") or pid)
                if not pid:
                    continue
                if str(project.get("mode") or "auto").lower()=="off":
                    continue
                contours.append({
                    "account_id":acc,
                    "project_id":pid,
                    "project_name":pname,
                })
    finally:
        db.close()

    projects=[]
    for item in contours:
        health=social_project_daily_delivery_health(
            account_id=item["account_id"],
            project_name=item["project_name"],
            project_id=item["project_id"],
            now_utc=now_utc,
        )
        projects.append(health)

    critical=[x for x in projects if str((x or {}).get("state") or "")=="critical"]
    degraded=[x for x in projects if str((x or {}).get("state") or "")=="degraded"]
    return {
        "ok":not critical and not degraded,
        "state":"critical" if critical else ("degraded" if degraded else "ok"),
        "project_count":len(projects),
        "projects":projects,
        "configuration_gaps":gaps,
        "configuration_gap_count":len(gaps),
        "sla_breach_count":sum(int((x or {}).get("sla_breach_count") or 0) for x in projects),
        "owner_action_required":any(
            bool((x or {}).get("owner_action_required")) for x in projects
        ) or any(bool((x or {}).get("owner_action_required")) for x in gaps),
    }


def ocean_daily_delivery_health(account_id="u2", now_utc=None):
    return social_project_daily_delivery_health(
        account_id=account_id,
        project_name="Океан Клиентов и Борис",
        now_utc=now_utc,
    )


def dushi_daily_delivery_health(account_id="u2", now_utc=None):
    return social_project_daily_delivery_health(
        account_id=account_id,
        project_name="От Души",
        now_utc=now_utc,
    )

def manicure_daily_delivery_health(account_id="u2", now_utc=None):
    return social_project_daily_delivery_health(
        account_id=account_id,
        project_name="Маникюр Евпатория",
        project_id="270dbf472f7f",
        now_utc=now_utc,
    )


def _runtime_secret_present(name):
    """Boolean-only secret readiness from process env or production .env."""
    key = str(name or "").strip()
    if not key:
        return False
    if str(os.environ.get(key) or "").strip():
        return True
    # Runtime services must reflect the environment actually loaded into that
    # process. Falling back to a changed .env here could claim "ready" before
    # systemd has reloaded the secret.
    if str(os.environ.get("BORIS_RUNTIME_ROLE") or "") in {"worker", "social_due_rescue"}:
        return False
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                k, value = line.split("=", 1)
                if k.strip() != key:
                    continue
                value = value.strip().strip('"').strip("'")
                return bool(value)
    except Exception:
        pass
    return False


def social_owner_pre_slot_self_heal(account_id="u2"):
    """Repair only unambiguous durable owner-autopilot drift, no API/AI calls."""
    # All owner projects with expected_auto_generation are production contours.
    db = SessionLocal()
    changed = []
    try:
        row = (
            db.query(Storage)
            .filter(Storage.account_id == account_id, Storage.key == "posting_projects")
            .with_for_update()
            .first()
        )
        if not row:
            return {"ok": False, "changed": [], "reason": "posting_projects_missing"}
        try:
            projects = json.loads(row.value or "[]")
        except Exception:
            return {"ok": False, "changed": [], "reason": "posting_projects_invalid_json"}
        if not isinstance(projects, list):
            return {"ok": False, "changed": [], "reason": "posting_projects_not_list"}

        for project in projects:
            if not isinstance(project, dict) or project.get("expected_auto_generation") is not True:
                continue
            touched = {}
            if project.get("expected_auto_generation") is True and not bool(project.get("paid_auto_generation")):
                touched["paid_auto_generation"] = {
                    "before": project.get("paid_auto_generation"), "after": True
                }
                project["paid_auto_generation"] = True
            if project.get("tz_offset") in (None, ""):
                touched["tz_offset"] = {"before": project.get("tz_offset"), "after": 3}
                project["tz_offset"] = 3
            if touched:
                changed.append({
                    "project_id": str(project.get("id") or ""),
                    "project": str(project.get("name") or ""),
                    "fields": touched,
                })

        if changed:
            row.value = json.dumps(projects, ensure_ascii=False)
            db.commit()
        else:
            db.rollback()
        return {"ok": True, "changed": changed, "changed_count": len(changed)}
    except Exception as exc:
        db.rollback()
        return {"ok": False, "changed": changed, "reason": type(exc).__name__ + ": " + str(exc)[:180]}
    finally:
        db.close()


def social_transport_readiness_cached_health(max_age_seconds=2400):
    """Read the latest external TG/VK permission probe without network calls."""
    from datetime import datetime as _dt_transport, timezone as _tz_transport
    from app.models.reliability import ReliabilityHeartbeat

    db = SessionLocal()
    try:
        hb = (
            db.query(ReliabilityHeartbeat)
            .filter(
                ReliabilityHeartbeat.module == "runtime",
                ReliabilityHeartbeat.worker_id == "social_transport_readiness",
            )
            .order_by(ReliabilityHeartbeat.last_seen_at.desc())
            .first()
        )
        if not hb:
            return {
                "ok": False, "fresh": False, "state": "missing",
                "age_seconds": None, "projects": [],
                "owner_action_required": False,
                "reason": "transport_readiness_missing",
            }
        last = hb.last_seen_at
        now = _dt_transport.now(_tz_transport.utc)
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=_tz_transport.utc)
        age = max(0, int((now - last).total_seconds())) if last else None
        details = dict(hb.details_json or {})
        fresh = age is not None and age <= int(max_age_seconds)
        state = str(hb.state or "")
        ok = bool(fresh and state == "ok" and details.get("ok"))
        return {
            "ok": ok,
            "fresh": fresh,
            "state": state,
            "age_seconds": age,
            "max_age_seconds": int(max_age_seconds),
            "projects": list(details.get("projects") or []),
            "blocked": list(details.get("blocked") or []),
            "blocked_count": int(details.get("blocked_count") or 0),
            "transport_errors": int(details.get("transport_errors") or 0),
            "subscription_gaps": list(details.get("subscription_gaps") or []),
            "subscription_gap_count": int(details.get("subscription_gap_count") or 0),
            "owner_action_required": bool(details.get("owner_action_required")),
            "checked_at": details.get("checked_at"),
            "reason": None if ok else (
                "transport_readiness_stale" if not fresh else
                "transport_readiness_degraded"
            ),
        }
    finally:
        db.close()


def social_owner_pre_slot_readiness_health(account_id="u2"):
    """Static pre-slot readiness for owner Social projects; no external API calls."""
    import re as _re_ready
    from datetime import datetime as _dt_ready, timedelta as _td_ready

    owner_expected = {
        "a374ced25745": "Океан Клиентов и Борис",
        "f938caa3a944": "От Души",
        "270dbf472f7f": "Маникюр Евпатория",
    }
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == "posting_projects"
        ).first()
        try:
            projects = json.loads(row.value or "[]") if row else []
        except Exception:
            return {
                "ok": False, "generation_ready": False, "delivery_ready": False,
                "blocked": [{"reason": "posting_projects_invalid_json"}],
                "projects": [], "owner_action_required": True,
            }
    finally:
        db.close()

    blocked = []
    reports = []
    seen_owner_ids = set()
    now_utc = _dt_ready.now()
    for project in projects if isinstance(projects, list) else []:
        if not isinstance(project, dict):
            continue
        pid = str(project.get("id") or "")
        pname = str(project.get("name") or "")
        if pid not in owner_expected and project.get("expected_auto_generation") is not True:
            continue
        if pid in owner_expected:
            seen_owner_ids.add(pid)
        expected = bool(project.get("expected_auto_generation"))
        mode = str(project.get("mode") or "auto")
        platforms = str(project.get("platforms") or "both").lower()
        try:
            offset = int(project.get("tz_offset") if project.get("tz_offset") not in (None, "") else 3)
            tz_valid = -12 <= offset <= 14
        except Exception:
            offset = 3
            tz_valid = False
        local_now = now_utc + _td_ready(hours=offset)

        hours = []
        for raw in (project.get("times") or project.get("schedule") or []):
            match = _re_ready.search(r"\d{1,2}", str(raw))
            if match and 0 <= int(match.group()) <= 23:
                hours.append(int(match.group()))
        hours = sorted(set(hours))
        next_slot = None
        if hours:
            for hour in hours:
                candidate = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if candidate >= local_now:
                    next_slot = candidate
                    break
            if next_slot is None:
                next_slot = (local_now + _td_ready(days=1)).replace(
                    hour=hours[0], minute=0, second=0, microsecond=0
                )

        project_blocked = []
        def add(reason, kind="generation", dependency_state=None):
            item = {
                "account_id": account_id, "project_id": pid, "project": pname,
                "reason": reason, "kind": kind,
            }
            if dependency_state:
                item["dependency_state"] = dependency_state
            blocked.append(item)
            project_blocked.append(item)

        canonical_name = owner_expected.get(pid)
        if canonical_name and pname != canonical_name:
            add("owner_project_identity_name_drift")

        # Ocean public signature is client-owned configuration, not shared-code
        # content. Never invent or silently replace it in the reliability core.
        if pid == "a374ced25745" and not str(project.get("footer_signature") or "").strip():
            add("ocean_footer_signature_missing")

        intentional_external_hard_stop = bool(
            expected
            and project.get("generation_blocked_until") == "vk_action_delivery_ready"
            and bool(project.get("vk_action_delivery_required"))
            and not bool(project.get("vk_action_delivery_ready"))
        )
        if expected and mode == "off" and not intentional_external_hard_stop:
            add("mode_off_but_autopilot_expected")
        if expected and not hours and not intentional_external_hard_stop:
            add("schedule_missing_or_invalid")
        if not tz_valid:
            add("tz_offset_invalid")
        if platforms not in {"tg", "vk", "both"}:
            add("platforms_invalid", "delivery")
        if expected and not bool(project.get("paid_auto_generation")):
            add("paid_auto_generation_disabled")
        themes = [str(x).strip() for x in (project.get("themes") or []) if str(x).strip()]
        if expected and not themes:
            add("themes_missing")

        tg_required = platforms in {"tg", "both"}
        vk_required = platforms in {"vk", "both"}
        if tg_required and not str(project.get("channel_tg") or "").strip():
            add("telegram_destination_missing", "delivery")
        if tg_required and not _runtime_secret_present("BORIS_TELEGRAM_BOT_TOKEN"):
            add("telegram_token_missing", "delivery")
        if vk_required:
            try:
                vk_owner = int(project.get("vk_owner_id") or 0)
            except Exception:
                vk_owner = 0
            if not vk_owner:
                add("vk_destination_missing", "delivery")
            if not _runtime_secret_present("VK_TOKEN"):
                add("vk_token_missing", "delivery")
            if (
                bool(project.get("vk_action_delivery_required"))
                and not bool(project.get("vk_action_delivery_ready"))
            ):
                add(
                    str(project.get("vk_action_delivery_blocker") or
                        "vk_native_action_unavailable"),
                    "delivery",
                    "waiting_external",
                )

        image_source = str(project.get("image_source") or "ai").lower()
        media = {"source": image_source, "ready": True}
        if image_source == "reuse":
            try:
                from app.services.prospect_banner import _social_project_paths
                pool = [
                    p for p in _social_project_paths()
                    if p.is_file() and p.stat().st_size >= 50_000
                ]
                media["valid_assets"] = len(pool)
                if not pool:
                    media["ready"] = False
                    add("reuse_media_pool_empty")
            except Exception as exc:
                media["ready"] = False
                media["error"] = type(exc).__name__
                add("reuse_media_pool_unavailable")
        elif image_source == "ai":
            ai_key = _runtime_secret_present("OPENAI_API_KEY")
            media["openai_key"] = ai_key
            if not ai_key:
                media["ready"] = False
                add("openai_key_missing")
        elif image_source == "pexels":
            pexels_key = _runtime_secret_present("PEXELS_API_KEY")
            media["pexels_key"] = pexels_key
            if not pexels_key:
                media["ready"] = False
                add("pexels_key_missing")
        else:
            media["ready"] = False
            add("image_source_invalid")

        reports.append({
            "project_id": pid,
            "project": pname,
            "expected_auto_generation": expected,
            "mode": mode,
            "platforms": platforms,
            "tz_offset": offset,
            "times": hours,
            "next_generation_slot_local": (
                next_slot.strftime("%Y-%m-%d %H:%M") if next_slot else None
            ),
            "media": media,
            "blocked_count": len(project_blocked),
            "blocked_reasons": [x["reason"] for x in project_blocked],
        })

    for expected_id, expected_name in owner_expected.items():
        if expected_id in seen_owner_ids:
            continue
        item = {
            "account_id": account_id,
            "project_id": expected_id,
            "project": expected_name,
            "reason": "owner_project_missing",
            "kind": "generation",
        }
        blocked.append(item)
        reports.append({
            "project_id": expected_id,
            "project": expected_name,
            "expected_auto_generation": True,
            "mode": "missing",
            "platforms": "both",
            "tz_offset": 3,
            "times": [],
            "next_generation_slot_local": None,
            "media": {"source": None, "ready": False},
            "blocked_count": 1,
            "blocked_reasons": ["owner_project_missing"],
        })

    generation_blockers = [
        x for x in blocked
        if x.get("kind") != "delivery"
    ]
    delivery_blockers = [
        x for x in blocked
        if x.get("kind") == "delivery"
    ]
    waiting_external = [
        x for x in blocked
        if str(x.get("dependency_state") or "") == "waiting_external"
    ]
    runtime_blockers = [
        x for x in blocked
        if str(x.get("dependency_state") or "") != "waiting_external"
    ]
    return {
        # A deliberate external wait remains visible and delivery stays gated,
        # but it is not a scheduler failure and must not turn the owner into
        # an operator. Internal drift still fails closed.
        "ok": not runtime_blockers,
        "generation_ready": not (generation_blockers or delivery_blockers),
        "delivery_ready": not delivery_blockers,
        "blocked_count": len(blocked),
        "runtime_blocked_count": len(runtime_blockers),
        "waiting_external_count": len(waiting_external),
        "blocked": blocked[:50],
        "waiting_external": waiting_external[:50],
        "generation_blockers": generation_blockers[:50],
        "delivery_blockers": delivery_blockers[:50],
        "projects": reports,
        "owner_action_required": bool(runtime_blockers),
    }


def _paid_social_required_destinations(subscription):
    platforms=str((subscription or {}).get("platforms") or "both").lower()
    required=[]
    if platforms in {"tg","both"}:
        required.append("channel_tg")
    if platforms in {"vk","both"}:
        required.append("vk_owner_id")
    return required


def _paid_social_onboarding_scaffold(db, account_id, subscription):
    """Prepare deterministic paid Social onboarding without network/AI calls.

    External publication destinations are never invented. If legacy
    posting_settings already contains every destination required by the paid
    plan, return a safe auto-project that the caller may materialize.
    """
    import hashlib as _hashlib
    from sqlalchemy import text as _sql_text

    acc=str(account_id or "")
    settings_row=db.query(Storage).filter(
        Storage.account_id==acc,Storage.key=="posting_settings"
    ).first()
    try:
        settings=json.loads(settings_row.value or "{}") if settings_row else {}
    except Exception:
        settings={}
    if not isinstance(settings,dict):
        settings={}

    platforms=str((subscription or {}).get("platforms") or "both").lower()
    if platforms not in {"tg","vk","both"}:
        platforms="both"

    channel_tg=str(settings.get("channel_tg") or "").strip()
    vk_owner_id=settings.get("vk_owner_id")
    try:
        vk_owner_id=int(vk_owner_id) if vk_owner_id not in (None,"") else None
    except Exception:
        vk_owner_id=None

    required=_paid_social_required_destinations({"platforms":platforms})
    missing=[]
    if "channel_tg" in required and not channel_tg:
        missing.append("channel_tg")
    if "vk_owner_id" in required and vk_owner_id is None:
        missing.append("vk_owner_id")

    themes=[
        str(x).strip() for x in (settings.get("themes") or [])
        if str(x).strip()
    ]
    if not themes:
        direction_rows=db.query(Storage).filter(
            Storage.account_id==acc,
            Storage.key.like("feed_direction_profile:%"),
        ).limit(8).all()
        for row in direction_rows:
            try:
                value=json.loads(row.value or "{}")
            except Exception:
                value={}
            direction=str((value or {}).get("direction") or "").strip()
            if direction and direction not in themes:
                themes.append(direction)
    account_name=""
    company_context=""
    try:
        account_row=db.execute(_sql_text(
            "SELECT name,company_website FROM accounts WHERE account_id=:a"
        ),{"a":acc}).mappings().first()
        if account_row:
            account_name=str(account_row.get("name") or "").strip()
            company_context=str(account_row.get("company_website") or "").strip()
    except Exception:
        pass
    if not themes:
        themes=[
            "полезный совет по основной услуге",
            "частый вопрос клиента перед обращением",
            "как выбрать подходящее решение",
        ]

    times=[
        str(x).strip() for x in (settings.get("times") or [])
        if str(x).strip()
    ]
    if not times:
        try:
            posts_per_day=max(1,min(4,int((subscription or {}).get("posts_per_day") or 3)))
        except Exception:
            posts_per_day=3
        times={
            1:["12"],
            2:["9","18"],
            3:["9","14","19"],
            4:["9","12","16","20"],
        }[posts_per_day]

    text_prompt=str(settings.get("text_prompt") or "").strip()
    if not text_prompt and (account_name or company_context):
        text_prompt=(
            "Пиши экспертно и по делу для реального бизнеса клиента. "
            "Не выдумывай цены, гарантии, адреса или факты, которых нет в контексте. "
            "Контекст бизнеса: "
            + " · ".join(x for x in (account_name,company_context[:500]) if x)
        )

    project_id=_hashlib.sha1(
        ("paid-social-onboarding:"+acc).encode("utf-8","ignore")
    ).hexdigest()[:12]
    project={
        "id":project_id,
        "name":account_name or "Social",
        "channel_tg":channel_tg,
        "vk_owner_id":vk_owner_id,
        "style_source":str(settings.get("style_source") or ""),
        "themes":themes[:12],
        "times":times,
        "text_prompt":text_prompt,
        "banner_prompt":str(settings.get("banner_prompt") or ""),
        "image_source":str(settings.get("image_source") or "ai"),
        "platforms":platforms,
        "contact_tg":str(settings.get("contact_tg") or ""),
        "contact_vk":str(settings.get("contact_vk") or ""),
        "mode":"moderate",
        "auto_publish_hours":3,
        "tz_offset":3,
        "paid_auto_generation":True,
        "expected_auto_generation":True,
        "public_quality_guard":True,
        "allow_hashtags":True,
        "subscription":dict(subscription or {}),
        "onboarding_source":"paid_subscription_self_heal_v1",
    }
    scaffold={
        "schema":1,
        "account_id":acc,
        "status":"waiting_external_destinations" if missing else "ready_to_materialize",
        "dependency_state":"waiting_external" if missing else "self_heal_ready",
        "owner_action_required":False,
        "subscription":dict(subscription or {}),
        "platforms":platforms,
        "required_external_fields":required,
        "missing_external_fields":missing,
        "project_id":project_id,
        "project_name":project["name"],
        "themes":themes[:12],
        "times":times,
        "self_heal":"auto_materialize_when_destinations_present",
    }
    return scaffold,(None if missing else project)


def _persist_paid_social_onboarding_scaffold(db, account_id, scaffold):
    row=(db.query(Storage)
         .filter(
             Storage.account_id==str(account_id or ""),
             Storage.key=="posting_onboarding_scaffold_v1",
         )
         .with_for_update().first())
    payload=json.dumps(scaffold,ensure_ascii=False)
    if row:
        changed=str(row.value or "")!=payload
        if changed:
            row.value=payload
        return changed
    db.add(Storage(
        account_id=str(account_id or ""),
        key="posting_onboarding_scaffold_v1",
        value=payload,
    ))
    return True


def social_active_paid_pre_slot_self_heal():
    """Repair deterministic drift for every active paid Social project.

    No network and no AI calls. Canonical posting_subscription is the paid
    entitlement truth. We only restore project fields whose intended value is
    already explicit: embedded subscription and paid_auto_generation when
    expected_auto_generation=True.
    """
    db=SessionLocal()
    changed=[]
    accounts_checked=0
    try:
        subrows=db.query(Storage).filter(Storage.key=="posting_subscription").all()
        for subrow in subrows:
            try:
                sub=json.loads(subrow.value or "{}")
            except Exception:
                continue
            from app.services.social_entitlement import normalize_posting_subscription
            sub, root_entitlement_changes = normalize_posting_subscription(sub)
            if root_entitlement_changes:
                subrow.value=json.dumps(sub,ensure_ascii=False)
                changed.append({
                    "account_id":str(subrow.account_id or ""),
                    "project_id":"",
                    "project":"account_subscription",
                    "fields":{"posting_subscription":root_entitlement_changes},
                })
            if not _sub_active(sub):
                continue
            acc=str(subrow.account_id or "")
            prow=(db.query(Storage)
                  .filter(Storage.account_id==acc,Storage.key=="posting_projects")
                  .with_for_update().first())
            if prow:
                try:
                    projects=json.loads(prow.value or "[]")
                except Exception:
                    continue
                if not isinstance(projects,list):
                    continue
            else:
                projects=[]

            # Count the paid account as inspected before any deliberate
            # waiting_external early-exit. Otherwise supervisor telemetry says
            # accounts_checked=0 even though BORIS inspected the entitlement and
            # prepared the onboarding scaffold.
            accounts_checked+=1

            if not projects:
                scaffold, auto_project = _paid_social_onboarding_scaffold(
                    db, acc, sub
                )
                scaffold_changed = _persist_paid_social_onboarding_scaffold(
                    db, acc, scaffold
                )
                if auto_project is None:
                    if scaffold_changed:
                        changed.append({
                            "account_id":acc,
                            "project_id":str(scaffold.get("project_id") or ""),
                            "project":str(scaffold.get("project_name") or "paid_social_onboarding"),
                            "fields":{
                                "posting_onboarding_scaffold_v1":{
                                    "status":scaffold.get("status"),
                                    "missing_external_fields":list(
                                        scaffold.get("missing_external_fields") or []
                                    ),
                                }
                            },
                        })
                    continue

                projects=[auto_project]
                materialized_scaffold=dict(scaffold)
                materialized_scaffold["status"]="materialized"
                materialized_scaffold["dependency_state"]="resolved"
                materialized_scaffold["missing_external_fields"]=[]
                _persist_paid_social_onboarding_scaffold(
                    db, acc, materialized_scaffold
                )
                payload=json.dumps(projects,ensure_ascii=False)
                if prow:
                    prow.value=payload
                else:
                    prow=Storage(
                        account_id=acc,key="posting_projects",value=payload
                    )
                    db.add(prow)
                changed.append({
                    "account_id":acc,
                    "project_id":str(auto_project.get("id") or ""),
                    "project":str(auto_project.get("name") or "Social"),
                    "fields":{
                        "posting_projects":{
                            "before":"missing_or_empty",
                            "after":"materialized_from_paid_subscription_and_saved_destinations",
                        }
                    },
                })

            account_changed=False
            for project in projects:
                if not isinstance(project,dict):
                    continue
                pid=str(project.get("id") or "")
                touched={}
                nested=project.get("subscription")
                from app.services.social_entitlement import normalize_posting_subscription
                normalized_nested, nested_changes = normalize_posting_subscription(
                    nested if isinstance(nested,dict) else {}
                )
                if not _sub_active(normalized_nested):
                    touched["subscription"]={
                        "before_active":False,
                        "after_active":True,
                        "entitlement_changes":nested_changes,
                    }
                    project["subscription"]=dict(sub)
                else:
                    sync_keys=(
                        "plan","platforms","paid_at","period_days",
                        "posts_per_day","amount_rub","comment"
                    )
                    field_changes={}
                    synced=dict(normalized_nested)
                    if nested_changes:
                        field_changes.update({
                            "normalized_"+str(k):dict(v)
                            for k,v in nested_changes.items()
                        })
                    for skey in sync_keys:
                        if skey not in sub:
                            continue
                        if synced.get(skey)!=sub.get(skey):
                            field_changes[skey]={
                                "before":synced.get(skey),
                                "after":sub.get(skey),
                            }
                            synced[skey]=sub.get(skey)
                    if field_changes:
                        touched["subscription_entitlement"]=field_changes
                        project["subscription"]=synced
                if (
                    project.get("expected_auto_generation") is True
                    and not bool(project.get("paid_auto_generation"))
                ):
                    touched["paid_auto_generation"]={
                        "before":project.get("paid_auto_generation"),
                        "after":True,
                    }
                    project["paid_auto_generation"]=True
                if touched:
                    account_changed=True
                    changed.append({
                        "account_id":acc,
                        "project_id":pid,
                        "project":str(project.get("name") or pid),
                        "fields":touched,
                    })
            if account_changed:
                prow.value=json.dumps(projects,ensure_ascii=False)
        if changed:
            db.commit()
        else:
            db.rollback()
        return {
            "ok":True,
            "accounts_checked":accounts_checked,
            "changed_count":len(changed),
            "changed":changed[:50],
        }
    except Exception as exc:
        db.rollback()
        return {
            "ok":False,
            "accounts_checked":accounts_checked,
            "changed_count":len(changed),
            "changed":changed[:50],
            "reason":type(exc).__name__+": "+str(exc)[:180],
        }
    finally:
        db.close()


def social_active_paid_pre_slot_readiness_health(now_utc=None):
    """Pre-slot readiness for all active paid Social auto projects.

    This is a read-only health function. It intentionally does not opt a manual
    project into paid AI: projects are monitored as automatic only when
    paid_auto_generation or expected_auto_generation is already explicit.
    """
    import re as _re_paid_ready
    from datetime import datetime as _dt_paid_ready, timedelta as _td_paid_ready

    now_utc=now_utc or _dt_paid_ready.now()
    transport=social_transport_readiness_cached_health()
    transport_map={
        (
            str(x.get("account_id") or "u2"),
            str(x.get("project_id") or ""),
        ):dict(x)
        for x in (transport.get("projects") or [])
        if isinstance(x,dict)
    }

    db=SessionLocal()
    blocked=[]
    reports=[]
    account_gaps=[]
    active_accounts=0
    try:
        subrows=db.query(Storage).filter(Storage.key=="posting_subscription").all()
        for subrow in subrows:
            try:
                sub=json.loads(subrow.value or "{}")
            except Exception:
                continue
            from app.services.social_entitlement import posting_entitlement_view
            entitlement=posting_entitlement_view(sub)
            sub=entitlement["normalized"]
            if not _sub_active(sub):
                continue
            acc=str(subrow.account_id or "")
            active_accounts+=1
            prow=db.query(Storage).filter(
                Storage.account_id==acc,Storage.key=="posting_projects"
            ).first()
            try:
                projects=json.loads(prow.value or "[]") if prow else []
            except Exception:
                projects=[]
            if not isinstance(projects,list) or not projects:
                gap={
                    "account_id":acc,
                    "reason":"active_subscription_without_posting_project",
                    "dependency_state":"waiting_external",
                    "owner_action_required":False,
                    "required_external_fields":_paid_social_required_destinations(sub),
                    "self_heal":"posting_onboarding_scaffold_v1_then_auto_materialize",
                }
                account_gaps.append(gap)
                blocked.append({**gap,"kind":"configuration"})
                continue

            posts_row=db.query(Storage).filter(
                Storage.account_id==acc,Storage.key=="posting_posts"
            ).first()
            try:
                account_posts=json.loads(posts_row.value or "[]") if posts_row else []
            except Exception:
                account_posts=[]

            for project in projects:
                if not isinstance(project,dict):
                    continue
                pid=str(project.get("id") or "")
                pname=str(project.get("name") or pid)
                mode=str(project.get("mode") or "auto")
                auto_expected=bool(
                    project.get("paid_auto_generation")
                    or project.get("expected_auto_generation")
                )
                if mode=="off" and not auto_expected:
                    reports.append({
                        "account_id":acc,"project_id":pid,"project":pname,
                        "monitored":False,"mode":"off","blocked_count":0,
                        "blocked_reasons":[],"owner_action_required":False,
                    })
                    continue
                if not auto_expected:
                    reports.append({
                        "account_id":acc,"project_id":pid,"project":pname,
                        "monitored":False,"mode":mode,"blocked_count":0,
                        "blocked_reasons":[],"owner_action_required":False,
                    })
                    continue

                project_blocked=[]
                def add(reason,kind="generation",owner=False):
                    item={
                        "account_id":acc,"project_id":pid,"project":pname,
                        "reason":reason,"kind":kind,
                        "owner_action_required":bool(owner),
                    }
                    project_blocked.append(item)
                    blocked.append(item)

                if mode=="off":
                    add("mode_off_but_autopilot_expected",owner=True)
                if not _sub_active(project.get("subscription")):
                    add("project_subscription_inactive_or_missing")
                if (
                    project.get("expected_auto_generation") is True
                    and not bool(project.get("paid_auto_generation"))
                ):
                    add("paid_auto_generation_disabled")

                try:
                    offset=int(
                        project.get("tz_offset")
                        if project.get("tz_offset") not in (None,"") else 999
                    )
                    tz_valid=-12 <= offset <= 14
                except Exception:
                    offset=999
                    tz_valid=False
                if not tz_valid:
                    add("tz_offset_invalid",owner=True)
                    offset=0
                local_now=now_utc+_td_paid_ready(hours=offset)

                hours=[]
                for raw in (project.get("times") or project.get("schedule") or []):
                    match=_re_paid_ready.search(r"\d{1,2}",str(raw))
                    if match and 0 <= int(match.group()) <= 23:
                        hours.append(int(match.group()))
                hours=sorted(set(hours))
                if not hours:
                    add("schedule_missing_or_invalid",owner=True)

                next_slot=None
                if hours:
                    for hour in hours:
                        candidate=local_now.replace(hour=hour,minute=0,second=0,microsecond=0)
                        if candidate>=local_now:
                            next_slot=candidate
                            break
                    if next_slot is None:
                        next_slot=(local_now+_td_paid_ready(days=1)).replace(
                            hour=hours[0],minute=0,second=0,microsecond=0
                        )

                platforms=str(project.get("platforms") or sub.get("platforms") or "both").lower()
                if platforms not in {"tg","vk","both"}:
                    add("platforms_invalid","delivery",True)
                tg_required=platforms in {"tg","both"}
                vk_required=platforms in {"vk","both"}
                if tg_required and not str(project.get("channel_tg") or "").strip():
                    add("telegram_destination_missing","delivery",True)
                if vk_required:
                    try:
                        vk_owner=int(project.get("vk_owner_id") or 0)
                    except Exception:
                        vk_owner=0
                    if not vk_owner:
                        add("vk_destination_missing","delivery",True)

                themes=[str(x).strip() for x in (project.get("themes") or []) if str(x).strip()]
                if not themes:
                    add("themes_missing","generation",True)

                if tg_required and not _runtime_secret_present("BORIS_TELEGRAM_BOT_TOKEN"):
                    add("telegram_token_missing","delivery",False)
                if vk_required and not _runtime_secret_present("VK_TOKEN"):
                    add("vk_token_missing","delivery",False)

                image_source=str(project.get("image_source") or "ai").lower()
                media={"source":image_source,"ready":True}
                if image_source=="ai":
                    media["openai_key"]=_runtime_secret_present("OPENAI_API_KEY")
                    if not media["openai_key"]:
                        media["ready"]=False
                        add("openai_key_missing","generation",False)
                elif image_source=="pexels":
                    media["pexels_key"]=_runtime_secret_present("PEXELS_API_KEY")
                    if not media["pexels_key"]:
                        media["ready"]=False
                        add("pexels_key_missing","generation",False)
                elif image_source=="reuse":
                    valid_assets=0
                    for rec in account_posts if isinstance(account_posts,list) else []:
                        if not isinstance(rec,dict) or str(rec.get("project_id") or "")!=pid:
                            continue
                        ok,_err=_banner_delivery_ready(
                            {"banner":rec.get("banner")},
                            "reuse",
                        )
                        if ok:
                            valid_assets+=1
                    media["valid_assets"]=valid_assets
                    if not valid_assets:
                        media["ready"]=False
                        add("reuse_media_pool_empty","generation",False)
                else:
                    media["ready"]=False
                    add("image_source_invalid","generation",True)

                titem=transport_map.get((acc,pid))
                transport_ready=bool(
                    transport.get("fresh")
                    and titem
                    and titem.get("ready")
                )
                if not transport.get("fresh"):
                    add("transport_readiness_stale_or_missing","delivery",False)
                elif not titem or not titem.get("ready"):
                    add("transport_permission_not_ready","delivery",True)

                owner_needed=any(bool(x.get("owner_action_required")) for x in project_blocked)
                reports.append({
                    "account_id":acc,
                    "project_id":pid,
                    "project":pname,
                    "monitored":True,
                    "mode":mode,
                    "platforms":platforms,
                    "tz_offset":offset,
                    "times":hours,
                    "configured_slots_per_day":len(hours),
                    "package_posts_per_day_limit":int(
                        entitlement.get("posts_per_day_limit") or 0
                    ),
                    "schedule_vs_package_note":(
                        "posts_per_day_is_package_limit_not_required_slot_count"
                    ),
                    "next_generation_slot_local":(
                        next_slot.strftime("%Y-%m-%d %H:%M") if next_slot else None
                    ),
                    "minutes_to_next_slot":(
                        max(0,int((next_slot-local_now).total_seconds()//60))
                        if next_slot else None
                    ),
                    "media":media,
                    "transport_ready":transport_ready,
                    "blocked_count":len(project_blocked),
                    "blocked_reasons":[x["reason"] for x in project_blocked],
                    "owner_action_required":owner_needed,
                })
    finally:
        db.close()

    monitored=[x for x in reports if x.get("monitored")]
    owner_needed=any(bool(x.get("owner_action_required")) for x in blocked)
    return {
        "ok":not blocked,
        "active_accounts":active_accounts,
        "monitored_projects":len(monitored),
        "blocked_count":len(blocked),
        "blocked":blocked[:100],
        "account_gaps":account_gaps[:50],
        "projects":reports[:100],
        "transport_fresh":bool(transport.get("fresh")),
        "owner_action_required":owner_needed,
    }


def social_scheduler_contract_health():
    """Return end-to-end blockers for scheduled Social projects.

    Besides entitlement/scheduler configuration, this contract protects two
    production invariants that previously reached the owner manually:
      1) an explicitly expected autopilot must not silently lose its paid-auto flag;
      2) the latest public copy for guarded projects must not contain prompt/meta
         leakage, hashtag walls, or an unfinished external quality edit.
    """
    import re as _re_health
    blocked = []
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        from app.models.user import User
        for row in rows:
            is_owner = False
            if str(row.account_id).startswith("u"):
                try:
                    uid = int(str(row.account_id)[1:])
                    usr = db.query(User).filter(User.id == uid).first()
                    is_owner = bool(usr and getattr(usr, "role", None) == "owner")
                except Exception:
                    is_owner = False
            try:
                projects = json.loads(row.value or "[]")
            except Exception:
                blocked.append({"account_id": row.account_id, "reason": "posting_projects_invalid_json"})
                continue
            if not isinstance(projects, list):
                continue

            posts_row = db.query(Storage).filter(
                Storage.account_id == row.account_id,
                Storage.key == "posting_posts",
            ).first()
            try:
                account_posts = json.loads(posts_row.value or "[]") if posts_row else []
            except Exception:
                account_posts = []

            for proj in projects:
                if not isinstance(proj, dict):
                    continue
                pid = str(proj.get("id") or "")
                pname = str(proj.get("name") or pid)
                expected_auto = bool(proj.get("expected_auto_generation"))
                mode = str(proj.get("mode") or "auto")
                has_schedule = bool(proj.get("times") or proj.get("schedule"))
                if mode == "off":
                    if expected_auto:
                        intentional_external_hard_stop = bool(
                            proj.get("generation_blocked_until") == "vk_action_delivery_ready"
                            and bool(proj.get("vk_action_delivery_required"))
                            and not bool(proj.get("vk_action_delivery_ready"))
                        )
                        if intentional_external_hard_stop:
                            blocked.append({
                                "account_id": row.account_id,
                                "project_id": pid,
                                "project": pname,
                                "reason": str(
                                    proj.get("vk_action_delivery_blocker")
                                    or "vk_native_action_unavailable"
                                ),
                                "dependency_state": "waiting_external",
                            })
                        else:
                            blocked.append({
                                "account_id": row.account_id, "project_id": pid,
                                "project": pname,
                                "reason": "mode_off_but_autopilot_expected",
                            })
                    continue
                if not has_schedule:
                    if expected_auto:
                        blocked.append({
                            "account_id": row.account_id, "project_id": pid,
                            "project": pname, "reason": "schedule_missing_or_invalid",
                        })
                    continue

                if (not is_owner) and not _sub_active(proj.get("subscription")):
                    # Expired/cancelled service is expected inactivity, not a
                    # scheduler fault. Runtime generation already skips it via
                    # the same entitlement gate. Reporting it as a contract
                    # blocker made the global Social heartbeat degraded and
                    # invited recovery of a service that must remain stopped.
                    continue

                # Explicit expectation beats role. This is the key distinction
                # between an intentionally disabled paid generator and the old
                # «От Души» silent-stop regression on an owner project.
                if expected_auto and not bool(proj.get("paid_auto_generation")):
                    blocked.append({"account_id": row.account_id, "project_id": pid,
                                    "project": pname,
                                    "reason": "expected_auto_generation_disabled"})
                elif (not is_owner) and not bool(proj.get("paid_auto_generation")):
                    blocked.append({"account_id": row.account_id, "project_id": pid,
                                    "project": pname, "reason": "paid_auto_generation_disabled"})

                if not bool(proj.get("public_quality_guard")):
                    continue
                latest = next((p for p in account_posts
                               if isinstance(p, dict)
                               and str(p.get("project_id") or "") == pid
                               and str(p.get("status") or "") == "published"), None)
                if not latest:
                    continue
                visible = str(latest.get("preview") or latest.get("text") or "")
                bad_meta = bool(_re_health.search(
                    r"(?im)^\s*(?:тема|тон|структура|формат(?:\s+подачи)?|cta|"
                    r"хэштеги?|теги?|для\s+изображения|визуальн\w*\s+стиль)\s*[:—-]",
                    visible,
                ))
                # Hashtag rows are a quality regression only for projects whose
                # owner policy forbids hashtags. Manicure explicitly requires
                # 5-10 topical hashtags; treating that as bad copy made the shared
                # Social supervisor contradict the project contract.
                bad_hash = (not bool(proj.get("allow_hashtags"))) and bool(_re_health.search(
                    r"(?m)^\s*(?:#\w+[\s,]*){2,}\s*$", visible
                ))
                ocean_footer_regression = False
                if _ocean_clients_project(proj):
                    current_footer = str(proj.get("footer_signature") or "").strip()
                    legacy_markers = (
                        "🎥 BORIS в работе",
                        "Бесплатный тест софта BORIS",
                        "Забрать бесплатный тест:",
                    )
                    ocean_footer_regression = bool(
                        any(x in visible for x in legacy_markers)
                        or (
                            current_footer
                            and visible.count(current_footer) != 1
                        )
                    )
                if bad_meta or bad_hash or ocean_footer_regression:
                    blocked.append({"account_id": row.account_id, "project_id": pid,
                                    "project": pname, "reason": "public_copy_quality_regression",
                                    "post_id": str(latest.get("id") or "")})
                if latest.get("external_edit_pending"):
                    blocked.append({"account_id": row.account_id, "project_id": pid,
                                    "project": pname, "reason": "external_quality_edit_pending",
                                    "post_id": str(latest.get("id") or ""),
                                    "pending": latest.get("external_edit_pending")})

        waiting_external=[
            x for x in blocked
            if str(x.get("dependency_state") or "")=="waiting_external"
        ]
        runtime_blockers=[
            x for x in blocked
            if str(x.get("dependency_state") or "")!="waiting_external"
        ]
        return {
            "ok":not runtime_blockers,
            "blocked_projects":runtime_blockers[:50],
            "blocked_count":len(runtime_blockers),
            "waiting_external":waiting_external[:50],
            "waiting_external_count":len(waiting_external),
        }
    finally:
        db.close()

_POST_NOTIFY = {"otdushi": {"chat_id": -1003952038222, "thread_id": 2}}

def _notify_posted(account_id, pname, theme, proj, results, banner_name):
    cfg = _POST_NOTIFY.get(account_id)
    if not cfg:
        return
    try:
        from app.telegram_bot import send_telegram_photo
        chans = []
        if proj.get("channel_tg") and isinstance(results.get("tg"), dict) and results["tg"].get("ok"):
            chans.append("TG " + str(proj.get("channel_tg")))
        if proj.get("vk_owner_id") and isinstance(results.get("vk"), dict) and results["vk"].get("ok"):
            chans.append("VK " + str(proj.get("vk_owner_id")))
        if not chans:
            return
        caption = ("\u2705 <b>\u041e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d \u043f\u043e\u0441\u0442</b>\n"
                   "\U0001F4E2 " + ", ".join(chans) + "\n"
                   "\U0001F4DD " + str(pname) + "\n"
                   "\U0001F4CC " + str(theme))
        bpath = os.path.join(BANNER_DIR, banner_name) if banner_name else None
        send_telegram_photo(str(cfg["chat_id"]), bpath, caption, thread_id=cfg["thread_id"])
    except Exception as _e:
        print("[notify_posted] error:", str(_e)[:150], flush=True)


def _next_slot(proj):
    """Ближайший будущий слот из times проекта, в местном времени проекта.
    Пусто, если расписания нет — тогда работает только auto_publish_hours."""
    import re as _re
    hours = []
    for _t in (proj.get("times") or []):
        _st = str(_t).strip()
        if ":" in _st:
            _h = _st.split(":")[0].strip()
            if _h.isdigit() and 0 <= int(_h) <= 23:
                hours.append(int(_h))
        else:
            for _h in _re.findall(r"\d{1,2}", _st):
                if 0 <= int(_h) <= 23:
                    hours.append(int(_h))
    if not hours:
        return ""
    hours = sorted(set(hours))
    _now = datetime.now() + timedelta(hours=int(proj.get("tz_offset", 3)))
    for _h in hours:
        _cand = _now.replace(hour=_h, minute=0, second=0, microsecond=0)
        if _cand >= _now.replace(minute=0, second=0, microsecond=0):
            return _cand.strftime("%Y-%m-%d %H:%M")
    _nxt = (_now + timedelta(days=1)).replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return _nxt.strftime("%Y-%m-%d %H:%M")


# ==== РОТАЦИЯ ТЕМ И ПРИЗНАКОВ ДЛЯ НИШИ НОГТЕЙ (маникюр/педикюр) ====
# Причина: тема бралась random.choice без памяти о прошлых постах, поэтому
# повторялись темы, цвета и композиции, а педикюр появлялся редко.
# Здесь НЕТ обращений к модели: разнообразие обеспечивается программно.
NAIL_TRAITS = {
    "service_type": ["маникюр", "педикюр", "маникюр и педикюр", "уход за руками", "уход за стопами"],
    "content_type": ["идея дизайна", "подборка", "сравнение двух вариантов", "совет мастера",
                     "ответ на вопрос клиента", "миф и правда", "ошибка домашнего ухода",
                     "мини-тест", "голосование", "юмор", "наблюдение", "сезонная ситуация",
                     "разбор дизайна", "предложение услуги"],
    "nail_shape": ["миндаль", "квадрат", "мягкий квадрат", "овал"],
    "nail_length": ["короткие", "средние", "длинные"],
    "design_type": ["однотонный", "френч", "минимализм", "геометрия", "рисунок", "цветы",
                    "абстракция", "градиент", "кошачий глаз", "металлик", "акцентные ногти",
                    "блеск", "жемчужный"],
    "primary_color": ["нюд", "молочный", "красный", "бордовый", "шоколадный", "розовый",
                      "голубой", "синий", "зелёный", "чёрный", "белый", "пастельный",
                      "неоновый", "серебристый", "золотистый"],
    "visual_style": ["премиальный", "журнальный beauty", "минималистичный", "чистый светлый",
                     "модный", "яркий", "романтичный", "элегантный", "игривый",
                     "сезонный", "люксовый", "живой соцсетевой"],
    "tone": ["экспертный", "тёплый", "лёгкий", "вдохновляющий", "разговорный"],
    "cta_type": ["запись", "вопрос аудитории", "голосование", "сохранить идею",
                 "выбрать цвет", "написать мастеру", "без призыва"],
}
_HAND_COMPS = ["одна рука крупным планом", "две руки", "рука с аксессуаром", "рука на ткани",
               "beauty-композиция", "лак рядом с рукой", "минималистичный фон",
               "интерьерная композиция"]
_FOOT_COMPS = ["стопы крупным планом", "стопы на светлой ткани", "стопы у бортика",
               "минималистичная композиция стоп"]
_NAIL_WORDS = ("маникюр", "педикюр", "ногт", "nail", "ноготк")


def _is_nail_niche(account_id):
    """Справочник применяется только к нише ногтей — другие клиенты работают как раньше."""
    try:
        db = SessionLocal()
        try:
            row = db.query(Account).filter(Account.account_id == account_id).first()
            hay = " ".join(str(getattr(row, f, "") or "") for f in
                           ("company_niche", "company_description", "account_id")).lower()
        finally:
            db.close()
        return any(w in hay for w in _NAIL_WORDS) or any(w in account_id.lower() for w in _NAIL_WORDS)
    except Exception:
        return False


def _is_nail_ctx(account_id, ctx):
    """Ниша определяется по КОНКРЕТНОМУ проекту, а не по аккаунту: у одного
    аккаунта могут соседствовать «Маникюр Евпатория» и маркетинговый канал —
    второй не должен получить ротацию по форме ногтей."""
    try:
        parts = [str(account_id or "")]
        if isinstance(ctx, dict):
            for k in ("name", "project_name", "text_prompt", "banner_prompt", "niche"):
                parts.append(str(ctx.get(k) or ""))
            th = ctx.get("themes")
            if isinstance(th, list):
                parts += [str(x) for x in th[:40]]
        hay = " ".join(parts).lower()
        return any(w in hay for w in _NAIL_WORDS)
    except Exception:
        return False


def _traits_history(account_id, limit=30):
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id,
                                       Storage.key == "posting_traits").first()
        return (_json.loads(row.value) if row else [])[:limit]
    except Exception:
        return []
    finally:
        db.close()


def _traits_remember(account_id, traits):
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id,
                                       Storage.key == "posting_traits").first()
        hist = _json.loads(row.value) if row else []
        hist.insert(0, traits)
        hist = hist[:50]
        val = _json.dumps(hist, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(Storage(account_id=account_id, key="posting_traits", value=val))
        db.commit()
    except Exception as _e:
        print("[traits] не сохранил признаки: " + str(_e)[:100], flush=True)
    finally:
        db.close()


def _least_used(values, history, key, window=20):
    """Значение, которого дольше всего не было; при равенстве — случайное из них."""
    used = [h.get(key) for h in history[:window] if isinstance(h, dict) and h.get(key)]
    rank = {v: (used.index(v) if v in used else 10 ** 6) for v in values}
    top = max(rank.values())
    import random as _rnd   # random в модуле импортируется локально по функциям
    return _rnd.choice([v for v in values if rank[v] == top])


def _pick_nail_traits(account_id):
    hist = _traits_history(account_id)
    t = {}
    for key in ("service_type", "content_type", "design_type", "primary_color",
                "nail_shape", "nail_length", "visual_style", "tone", "cta_type"):
        t[key] = _least_used(NAIL_TRAITS[key], hist, key)
    s = t["service_type"]
    # Композиция обязана соответствовать услуге: пост про педикюр не должен
    # получить руки с маникюром (и наоборот) — это требование ТЗ.
    if "педикюр" in s and "маникюр" in s:
        pool = _HAND_COMPS + _FOOT_COMPS
    elif "педикюр" in s or "стоп" in s:
        pool = _FOOT_COMPS
    else:
        pool = _HAND_COMPS
    t["composition"] = _least_used(pool, hist, "composition")
    return t


def _nail_theme(account_id, base_themes):
    """Возвращает (тема_с_признаками, признаки). Тема идёт И в текст, И в баннер,
    поэтому картинка не может разойтись с постом по услуге и дизайну."""
    t = _pick_nail_traits(account_id)
    import random as _rnd
    base = _rnd.choice(base_themes or ["идея дизайна"])
    theme = (
        "%s. Услуга: %s. Формат подачи: %s. Форма ногтей: %s, длина: %s. "
        "Дизайн: %s, основной цвет: %s. Тон текста: %s. Призыв в конце: %s. "
        "Для ИЗОБРАЖЕНИЯ (в текст поста НЕ переносить): композиция кадра — %s, "
        "визуальный стиль — %s. "
        "В тексте поста не употребляй английские слова и не повторяй эти служебные пометки."
        % (base, t["service_type"], t["content_type"], t["nail_shape"], t["nail_length"],
           t["design_type"], t["primary_color"], t["tone"], t["cta_type"],
           t["composition"], t["visual_style"])
    )
    t["base_theme"] = base
    _traits_remember(account_id, t)
    print("[traits] %s | %s | %s | %s" % (t["service_type"], t["content_type"],
                                          t["primary_color"], t["composition"]), flush=True)
    return theme, t


def _save_post_record(account_id, project_id, project_name, text, banner_path, status, ids=None, preview=None, publish_at=None, error=None, generation_key=None, record_id=None, tracking=None):
    import uuid as _uuid
    banner_name = os.path.basename(banner_path) if banner_path else ""
    _record_id = str(record_id or _uuid.uuid4().hex[:12])
    rec = {"id": _record_id, "project_id": project_id, "project_name": project_name,
           "text": text, "banner": banner_name, "status": status,
           "publication_status": status,
           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ids": ids or {},
           "external_post_id": ids or {}, "preview": preview or "",
           "publish_at": publish_at or None, "error": str(error or "")[:300],
           "published_at": (datetime.now().astimezone().isoformat(timespec="seconds") if status == "published" else None),
           "generation_key": str(generation_key or "")[:160]}
    if isinstance(tracking, dict) and tracking:
        rec.update({k: v for k, v in tracking.items() if v is not None})
    db = SessionLocal()
    try:
        row, posts = load_posting_posts_for_update(db, account_id)
        # Final cross-process lifecycle idempotency barrier. Paid text/image paths
        # are already exactly-once, but worker + rescue may still converge on the
        # same scheduled intent. Under the posting_posts account lock, keep only
        # one materialized draft/published/partial record per generation_key.
        if generation_key:
            existing = next(
                (
                    x for x in posts if isinstance(x, dict)
                    and str(x.get("generation_key") or "") == str(generation_key)
                    and str(x.get("status") or "") in ("draft", "published", "partial")
                ),
                None,
            )
            if existing:
                db.rollback()
                db.info.pop("posting_posts_lock:" + str(account_id), None)
                return str(existing.get("id") or rec["id"])
        # Один recoverable error на проект. Успешный следующий lifecycle удалит его.
        posts = [x for x in posts if not (x.get("project_id") == project_id and x.get("status") == "error")]
        posts.insert(0, rec)
        posts = posts[:50]
        save_posting_posts_locked(db, account_id, row, posts)
    finally:
        db.close()
    return rec["id"]


def _posting_generation_exists(account_id, generation_key):
    """True when this scheduled business intent already produced a lifecycle record."""
    if not generation_key:
        return False
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_posts").first()
        posts = _json.loads(row.value) if row and row.value else []
        return any(
            str(x.get("generation_key") or "") == str(generation_key)
            and str(x.get("status") or "") in ("draft", "published", "partial")
            for x in posts if isinstance(x, dict)
        )
    except Exception:
        return False
    finally:
        db.close()


def _scheduled_due_hour(proj, catchup_hours=6):
    """Return the newest scheduled local hour that is due and still catch-up eligible.

    The scheduler normally runs every few minutes.  If the worker or host was down
    exactly at the configured hour, an exact-hour-only check would silently lose
    the whole slot.  We allow a bounded same-day catch-up window so production
    recovers automatically without bunching stale posts many hours later.
    """
    import re as _re_due
    off = int(proj.get("tz_offset", 3) or 3)
    local_now = datetime.now() + timedelta(hours=off)
    now_hour = int(local_now.strftime("%H"))
    hours = []
    for raw in (proj.get("times") or []):
        text = str(raw).strip()
        match = _re_due.search(r"\d{1,2}", text)
        if match and 0 <= int(match.group()) <= 23:
            hours.append(int(match.group()))
    due = [h for h in sorted(set(hours), reverse=True) if 0 <= now_hour - h <= int(catchup_hours)]
    return due[0] if due else None


def _choose_rotating_project_theme(account_id, proj, random_module, recent_limit=6):
    """Pick from the configured theme pool without repeating recent plan themes.

    Reads only persisted generation plans; no AI/provider call. If every theme
    has appeared in the recent window, fall back to the full pool.
    """
    pool = [str(x).strip() for x in ((proj or {}).get("themes") or []) if str(x).strip()]
    if not pool:
        return "практический совет для клиента без рекламного шаблона"
    pid = str((proj or {}).get("id") or "")
    recent = []
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like("posting_generation_plan:" + pid + ":%"),
        ).order_by(Storage.id.desc()).limit(max(1, int(recent_limit))).all()
        for row in rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                continue
            theme = str((value or {}).get("theme") or "").strip()
            if theme and theme not in recent:
                recent.append(theme)
    finally:
        db.close()
    candidates = [x for x in pool if x not in recent] or pool
    return random_module.choice(candidates)


def _scheduled_generation_plan(account_id, proj, slot_hour=None, service_day=None):
    """Persist theme/occasion BEFORE paid AI so one schedule slot is one intent.

    ``service_day`` is a tightly bounded cross-midnight recovery override.  It may
    name only the current project-local day or the immediately previous day.  This
    lets a missed slot keep its original deterministic generation_key after
    midnight, so paid/provider idempotency remains attached to the original
    business intent instead of creating a new-day duplicate.
    """
    import json as _json, random as _random
    from sqlalchemy import text as _sql_text
    pid = str(proj.get("id") or "")
    off = int(proj.get("tz_offset", 3) or 3)
    local_now = datetime.now() + timedelta(hours=off)
    chosen_hour = int(local_now.strftime("%H")) if slot_hour is None else int(slot_hour)
    local_day = local_now.strftime("%Y%m%d")
    previous_day = (local_now - timedelta(days=1)).strftime("%Y%m%d")
    if service_day is None:
        parsed_day = local_day
    else:
        day = str(service_day or "").strip()
        try:
            parsed_day = datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d")
        except Exception as exc:
            raise ValueError("SOCIAL_INVALID_SERVICE_DAY_OVERRIDE") from exc
        if parsed_day not in {local_day, previous_day}:
            raise ValueError("SOCIAL_SERVICE_DAY_OVERRIDE_OUTSIDE_WINDOW")
    slot = parsed_day + "_" + str(chosen_hour).zfill(2)
    generation_key = "social-slot:%s:%s:%s" % (str(account_id), pid, slot)
    storage_key = "posting_generation_plan:" + pid + ":" + slot
    db = SessionLocal()
    try:
        # Serialize plan creation across cron replicas. The lock is transaction
        # scoped and released by commit/rollback automatically.
        db.execute(_sql_text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": generation_key})
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == storage_key).first()
        if row and row.value:
            try:
                plan = _json.loads(row.value)
                if isinstance(plan, dict) and plan.get("theme"):
                    if not str(plan.get("text_prompt_snapshot") or "").strip():
                        plan["text_prompt_snapshot"] = _build_text_prompt(
                            str(plan.get("theme") or ""),
                            str((proj or {}).get("text_prompt") or ""),
                            str(plan.get("occasion") or ""),
                            examples=None,
                            allow_hashtags=bool((proj or {}).get("allow_hashtags")),
                        )
                        row.value = _json.dumps(plan, ensure_ascii=False)
                        db.commit()
                    return generation_key, plan
            except Exception:
                pass
        if _is_nail_ctx(account_id, proj):
            theme, _traits = _nail_theme(account_id, proj.get("themes") or [])
        elif _ocean_clients_project(proj):
            # Owner policy: every scheduled Ocean slot rotates configured editorial themes.
            theme = _choose_rotating_project_theme(account_id, proj, _random, recent_limit=8)
        else:
            theme = _choose_rotating_project_theme(account_id, proj, _random, recent_limit=6)
        occasion = _project_occasion(proj)
        plan = {
            "generation_key": generation_key,
            "project_id": pid,
            "slot": slot,
            "theme": theme,
            "occasion": occasion or "",
            "text_prompt_snapshot": _build_text_prompt(
                theme,
                str((proj or {}).get("text_prompt") or ""),
                occasion or "",
                examples=None,
                allow_hashtags=bool((proj or {}).get("allow_hashtags")),
            ),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        payload = _json.dumps(plan, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            db.add(Storage(account_id=account_id, key=storage_key, value=payload))
        db.commit()
        return generation_key, plan
    finally:
        db.close()


def _self_heal_expected_auto_generation(account_id, proj):
    """Restore durable owner autopilot intent after legacy/config drift.

    Legacy owner Social projects predate ``expected_auto_generation``. A
    missing flag must not silently disable a production contour forever. For
    the three canonical owner projects we infer the established owner intent
    only when the flag is absent/None; an explicit False remains an intentional
    stop. Once expected intent is present, a lost paid runtime flag is safe to
    restore without issuing any AI/provider call here.
    """
    project = proj or {}
    pid = str(project.get("id") or "")
    if not pid:
        return False

    legacy_owner_expected = False
    if str(account_id) == "u2" and project.get("expected_auto_generation") is None:
        try:
            from app.services.social_owner_policy_v3 import is_ocean, is_dushi, is_manicure
            legacy_owner_expected = bool(
                is_ocean(project) or is_dushi(project) or is_manicure(project)
            )
        except Exception:
            legacy_owner_expected = pid in {"a374ced25745", "f938caa3a944", "270dbf472f7f"}

    if not bool(project.get("expected_auto_generation")) and not legacy_owner_expected:
        return False
    if bool(project.get("paid_auto_generation")) and not legacy_owner_expected:
        return False
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "posting_projects",
        ).first()
        if not row:
            return False
        try:
            projects = json.loads(row.value or "[]")
        except Exception:
            return False
        changed = False
        for stored in projects if isinstance(projects, list) else []:
            if str(stored.get("id") or "") != pid:
                continue
            if legacy_owner_expected and stored.get("expected_auto_generation") is None:
                stored["expected_auto_generation"] = True
                project["expected_auto_generation"] = True
                changed = True
            if not bool(stored.get("paid_auto_generation")):
                stored["paid_auto_generation"] = True
                project["paid_auto_generation"] = True
                changed = True
            break
        if not changed:
            return False
        row.value = json.dumps(projects, ensure_ascii=False)
        db.commit()
        project["paid_auto_generation"] = True
        print("[autopost_projects] SELF_HEALED_PAID_AUTO:", project.get("name") or pid, flush=True)
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _autopost_projects_impl(account_id, owner=False, respect_time=False, only_id=None, scheduled_hour_override=None, pre_generate_only=False, scheduled_service_day_override=None):
    import json as _json, random
    db = SessionLocal()
    try:
        prow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_projects").first()
        projects = _json.loads(prow.value) if prow else []
    finally:
        db.close()
    if not projects:
        print("[autopost_projects] нет проектов:", account_id); return
    for proj in projects:
        pid = proj.get("id", "")
        pname = proj.get("name", pid)
        _project_offset = int(proj.get("tz_offset", 3) or 3)
        _local_now = datetime.now() + timedelta(hours=_project_offset)
        _local_today = _local_now.strftime("%Y%m%d")
        _local_previous_day = (_local_now - timedelta(days=1)).strftime("%Y%m%d")
        today = _local_today
        if scheduled_service_day_override is not None:
            _service_day = str(scheduled_service_day_override or "").strip()
            try:
                _service_day = datetime.strptime(_service_day, "%Y%m%d").strftime("%Y%m%d")
            except Exception as exc:
                raise ValueError("SOCIAL_INVALID_SERVICE_DAY_OVERRIDE") from exc
            if _service_day not in {_local_today, _local_previous_day}:
                raise ValueError("SOCIAL_SERVICE_DAY_OVERRIDE_OUTSIDE_WINDOW")
            today = _service_day
        if only_id and pid != only_id:
            continue
        _scheduled_hour = None
        if respect_time:
            if scheduled_hour_override is not None:
                try:
                    _scheduled_hour = int(scheduled_hour_override)
                except Exception:
                    raise ValueError("SOCIAL_INVALID_SCHEDULED_HOUR_OVERRIDE")
                configured_hours = []
                for _raw_time in (proj.get("times") or []):
                    _match = __import__("re").search(r"\d{1,2}", str(_raw_time))
                    if _match and 0 <= int(_match.group()) <= 23:
                        configured_hours.append(int(_match.group()))
                if _scheduled_hour not in configured_hours:
                    raise ValueError("SOCIAL_SLOT_OVERRIDE_NOT_CONFIGURED")
                if today == _local_today and _scheduled_hour > int(_local_now.strftime("%H")):
                    if not pre_generate_only or (proj.get("mode") or "auto") != "moderate":
                        raise ValueError("SOCIAL_SLOT_OVERRIDE_IN_FUTURE")
            else:
                _scheduled_hour = _scheduled_due_hour(proj)
                if _scheduled_hour is None:
                    continue
        sub = proj.get("subscription")
        from app.services.social_entitlement import normalize_posting_subscription
        sub, _sub_entitlement_changes = normalize_posting_subscription(
            sub if isinstance(sub,dict) else {}
        )
        if not owner and not _sub_active(sub):
            print("[autopost_projects] пропуск (нет активной подписки):", pname); continue
        per_day = int((sub or {}).get("posts_per_day", 3))
        cnt_key = "posting_count:" + pid + ":" + today
        db = SessionLocal()
        try:
            crow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == cnt_key).first()
            used = int(crow.value) if crow else 0
        finally:
            db.close()
        if not owner and used >= per_day:
            print("[autopost_projects] лимит исчерпан:", pname); continue
        # Scheduled cron must never initiate paid AI generation implicitly.
        # Explicit expected_auto_generation is different: it means the owner has
        # already declared this autopilot should be running, so a lost flag is a
        # recoverable runtime drift rather than a reason to silently stop.
        if respect_time and not bool(proj.get("paid_auto_generation")):
            _self_heal_expected_auto_generation(account_id, proj)
        if respect_time and not bool(proj.get("paid_auto_generation")):
            print("[autopost_projects] PAID_AI_AUTO_DISABLED:", pname, flush=True)
            continue
        _generation_key = ""
        if respect_time:
            _generation_key, _plan = _scheduled_generation_plan(
                account_id, proj, slot_hour=_scheduled_hour,
                service_day=today if scheduled_service_day_override is not None else None,
            )
            if _posting_generation_exists(account_id, _generation_key):
                print("[autopost_projects] SLOT_ALREADY_MATERIALIZED:", pname, _generation_key, flush=True)
                continue
            theme = str((_plan or {}).get("theme") or "").strip()
            _occ = str((_plan or {}).get("occasion") or "").strip()
            if not theme:
                raise RuntimeError("SOCIAL_GENERATION_PLAN_EMPTY_THEME")
        else:
            if _is_nail_ctx(account_id, proj):
                theme, _tr = _nail_theme(account_id, proj.get("themes") or [])
            else:
                theme = random.choice(proj.get("themes") or ["поздравление"])
            _occ = _project_occasion(proj)
        print("[autopost_projects]", pname, "тема:", theme, flush=True)
        if _occ:
            print("[autopost_projects]", pname, "повод:", _occ, flush=True)
        post = generate_post(account_id, theme,
                             text_prompt=proj.get("text_prompt", ""),
                             banner_prompt=proj.get("banner_prompt", ""),
                             occasion=_occ,
                             channel=proj.get("style_source", ""),
                             image_source=proj.get("image_source", "ai"),
                             project_id=pid,
                             text_prompt_snapshot=(
                                 str((_plan or {}).get("text_prompt_snapshot") or "")
                                 if respect_time else ""
                             ),
                             allow_hashtags=bool(proj.get("allow_hashtags")))
        if (
            _generation_key
            and post.get("text_engine") in {"deterministic_uncertain_fallback","deterministic_provider_recovery"}
        ):
            _record_social_ai_uncertain_fallback(
                account_id, pid, _generation_key, theme,
                post.get("text_recovery_reason") or "",
            )
        _media_ok, _media_err = _banner_delivery_ready(post, proj.get("image_source", "ai"))
        if not _media_ok:
            _save_post_record(
                account_id, pid, pname, post.get("text", ""), None, "error",
                preview=post.get("text", ""), publish_at=_next_slot(proj),
                error="BANNER_REQUIRED: " + str(_media_err), generation_key=_generation_key,
            )
            print("[autopost_projects] BANNER_REQUIRED:", pname, "|", _media_err, "| status=error", flush=True)
            continue
        _record_id = __import__("uuid").uuid4().hex[:12]
        _tracking = _ocean_tracking_record(post.get("text", ""), proj, _record_id)
        _tracking_id = str(_tracking.get("utm_content") or "")
        if (proj.get("mode") or "auto") == "moderate":
            _plat = proj.get("platforms") or "both"
            _cnt = (proj.get("contact_tg") if _plat in ("tg", "both") else proj.get("contact_vk")) or proj.get("contact_vk") or ""
            _prev = _project_delivery_text_tracked(post["text"], proj, "tg" if _plat in ("tg", "both") else "vk", tracking_id=_tracking_id)
            _delay_hours = int(proj.get("auto_publish_hours") or 0)
            _publish_at = "" if _delay_hours > 0 else _next_slot(proj)
            # For deterministic scheduled moderate slots, persist the real
            # delivery deadline (slot + review window). This keeps pre-generated
            # drafts independent of their early created_at and makes due recovery
            # explicit in durable state.
            if _delay_hours > 0 and _generation_key:
                _scheduled_deadline = _scheduled_record_delivery_due_local(
                    {"generation_key": _generation_key}, proj
                )
                if _scheduled_deadline is not None:
                    _publish_at = _scheduled_deadline.strftime("%Y-%m-%d %H:%M")
            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "draft", preview=_prev, publish_at=_publish_at, generation_key=_generation_key, record_id=_record_id, tracking=_tracking)
            print("[autopost_projects] черновик на утверждение:", pname); continue
        platforms = proj.get("platforms") or "both"
        results = {}
        if proj.get("channel_tg") and platforms in ("tg", "both"):
            results["tg"] = _send_telegram(proj["channel_tg"], _project_delivery_text_tracked(post["text"], proj, "tg", tracking_id=_tracking_id), post.get("banner"), tenant_scope=account_id)
        if proj.get("vk_owner_id") and platforms in ("vk", "both"):
            results["vk"] = _send_vk(int(proj["vk_owner_id"]), _project_delivery_text_tracked(post["text"], proj, "vk", tracking_id=_tracking_id), post.get("banner"), tenant_scope=account_id)
        _complete = _all_required_delivery_ok(proj, results)
        _ids = _merge_delivery_ids({}, proj, results)
        if _complete:
            db = SessionLocal()
            try:
                crow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == cnt_key).first()
                if crow:
                    crow.value = str(int(crow.value) + 1)
                else:
                    db.add(Storage(account_id=account_id, key=cnt_key, value="1"))
                db.commit()
            finally:
                db.close()
            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "published", ids=_ids, generation_key=_generation_key, record_id=_record_id, tracking=_tracking)
            _notify_posted(account_id, pname, theme, proj, results, post.get("banner"))
            try:
                from app.services.action_log import log_action, ACTOR_CRON
                _ch = []
                if isinstance(results.get("tg"), dict) and results["tg"].get("ok"): _ch.append("Telegram")
                if isinstance(results.get("vk"), dict) and results["vk"].get("ok"): _ch.append("ВКонтакте")
                log_action(account_id=account_id, action="Опубликовал пост",
                           object_kind="пост", object_name=str(theme)[:200],
                           after_val=", ".join(_ch) or "канал не определён",
                           reason="проект «%s», публикация сразу" % pname,
                           actor=ACTOR_CRON, source="posting_runner")
            except Exception:
                pass
        else:
            _errs = []
            for _pk in _required_delivery_platforms(proj):
                _rv = results.get(_pk) if isinstance(results.get(_pk), dict) else {}
                if not _rv.get("ok"):
                    _errs.append(_pk + ": " + str(_rv.get("error") or "delivery_failed")[:120])
            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "partial",
                              ids=_ids, publish_at="", error="; ".join(_errs), generation_key=_generation_key, record_id=_record_id, tracking=_tracking)
            print("[autopost_projects] PARTIAL_DELIVERY: повторит due_posts только недостающую площадку", flush=True)
        print("[autopost_projects] итог:", pname, {k: ("OK" if v.get("ok") else "FAIL") for k, v in results.items()})


def autopost_previous_day_slot(account_id, project_id, service_day, slot_hour, owner=False, max_local_hour=6):
    """Bounded cross-midnight compensation for one deterministic Social slot.

    Only the immediately previous project-local calendar day is accepted, and
    only during the first ``max_local_hour`` hours after midnight.  The original
    generation key is preserved, so all paid-AI/provider caches and exactly-once
    barriers stay bound to the missed business intent.  Existing draft/partial/
    published rows are never regenerated; partial transport delivery remains the
    responsibility of ``due_posts_all``.
    """
    import json as _json
    import re as _re

    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("SOCIAL_CROSS_MIDNIGHT_PROJECT_ID_REQUIRED")
    try:
        hour = int(slot_hour)
    except Exception as exc:
        raise ValueError("SOCIAL_INVALID_SCHEDULED_HOUR_OVERRIDE") from exc

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "posting_projects",
        ).first()
        try:
            projects = _json.loads(row.value or "[]") if row else []
        except Exception as exc:
            raise ValueError("SOCIAL_POSTING_PROJECTS_INVALID_JSON") from exc
    finally:
        db.close()
    project = next((
        p for p in projects if isinstance(p, dict) and str(p.get("id") or "") == pid
    ), None)
    if project is None:
        return {"status":"project_missing","materialized":False,"project_id":pid}
    if project.get("expected_auto_generation") is not True or str(project.get("mode") or "auto").lower() == "off":
        return {"status":"autopilot_not_expected","materialized":False,"project_id":pid}

    try:
        offset = int(project.get("tz_offset", 3) or 3)
    except Exception:
        offset = 3
    local_now = datetime.now() + timedelta(hours=offset)
    local_day = local_now.strftime("%Y%m%d")
    previous_day = (local_now - timedelta(days=1)).strftime("%Y%m%d")
    day = str(service_day or "").strip()
    try:
        day = datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d")
    except Exception as exc:
        raise ValueError("SOCIAL_INVALID_SERVICE_DAY_OVERRIDE") from exc
    if day != previous_day:
        raise ValueError("SOCIAL_CROSS_MIDNIGHT_ONLY_PREVIOUS_DAY")

    try:
        window_end = int(max_local_hour)
    except Exception:
        window_end = 6
    window_end = max(1, min(12, window_end))
    if int(local_now.strftime("%H")) >= window_end:
        return {
            "status":"outside_cross_midnight_window","materialized":False,
            "project_id":pid,"service_day":day,"local_day":local_day,
            "local_hour":int(local_now.strftime("%H")),"window_end_hour":window_end,
        }

    configured_hours = []
    for raw in (project.get("times") or project.get("schedule") or []):
        match = _re.search(r"\d{1,2}", str(raw))
        if match and 0 <= int(match.group()) <= 23:
            configured_hours.append(int(match.group()))
    if hour not in configured_hours:
        raise ValueError("SOCIAL_SLOT_OVERRIDE_NOT_CONFIGURED")

    generation_key = "social-slot:%s:%s:%s_%02d" % (str(account_id), pid, day, hour)
    if _posting_generation_exists(account_id, generation_key):
        return {
            "status":"already_materialized","materialized":True,
            "project_id":pid,"service_day":day,"slot_hour":hour,
            "generation_key":generation_key,
        }

    _autopost_projects_impl(
        account_id,
        owner=owner,
        respect_time=True,
        only_id=pid,
        scheduled_hour_override=hour,
        scheduled_service_day_override=day,
    )
    materialized = bool(_posting_generation_exists(account_id, generation_key))
    return {
        "status":"materialized" if materialized else "not_materialized",
        "materialized":materialized,"project_id":pid,"service_day":day,
        "slot_hour":hour,"generation_key":generation_key,
    }


def autopost_projects(account_id, owner=False, respect_time=False, only_id=None, scheduled_hour_override=None, pre_generate_only=False):
    """Project-isolated wrapper around one-project implementation.

    A failure in one project must not prevent later projects in the same account
    bucket from receiving their scheduled service. Targeted calls keep fail-fast
    semantics so due-rescue can record the exact unresolved project.
    """
    if only_id:
        kwargs = {} if not pre_generate_only else {"pre_generate_only": True}
        return _autopost_projects_impl(
            account_id, owner=owner, respect_time=respect_time,
            only_id=only_id, scheduled_hour_override=scheduled_hour_override,
            **kwargs,
        )

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "posting_projects",
        ).first()
        try:
            projects = json.loads(row.value or "[]") if row else []
        except Exception:
            projects = []
    finally:
        db.close()

    if not projects:
        kwargs = {} if not pre_generate_only else {"pre_generate_only": True}
        return _autopost_projects_impl(
            account_id, owner=owner, respect_time=respect_time,
            only_id=None, scheduled_hour_override=scheduled_hour_override,
            **kwargs,
        )

    errors = []
    deferred = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        pid = str(project.get("id") or "")
        if not pid:
            continue
        pname = str(project.get("name") or pid)
        try:
            kwargs = {} if not pre_generate_only else {"pre_generate_only": True}
            _autopost_projects_impl(
                account_id, owner=owner, respect_time=respect_time,
                only_id=pid, scheduled_hour_override=scheduled_hour_override,
                **kwargs,
            )
        except Exception as exc:
            _err_text=str(exc or "")
            if _err_text.startswith(("CAMPAIGN_AI_PROVIDER_COOLDOWN:", "CAMPAIGN_AI_RELEASED_RETRY_CAP:")):
                # Provider/cost guard deliberately refuses another paid AI call.
                # Treat this as a bounded external deferral, not an internal
                # Social project failure. The canonical loop will retry only
                # after the provider guard releases it.
                item={"project_id":pid,"project":pname,"reason":_err_text[:220],"dependency_state":"waiting_external","paid_retry_performed":False}
                deferred.append(item)
                print("[autopost_projects] PROJECT_DEFERRED_EXTERNAL:",pname,"|",item["reason"],flush=True)
                continue
            item = {
                "project_id": pid,
                "project": pname,
                "error": type(exc).__name__ + ": " + _err_text[:220],
            }
            errors.append(item)
            print(
                "[autopost_projects] PROJECT_ISOLATED_ERROR:",
                pname, "|", item["error"],
                flush=True,
            )
            continue

    if errors:
        raise RuntimeError(
            "SOCIAL_PROJECT_ERRORS:" + json.dumps(errors, ensure_ascii=False)[:1200]
        )
    return {"ok": True, "projects_checked": len(projects), "deferred_external": deferred}


def pregenerate_upcoming_all(lead_hours=2):
    """Materialize future MODERATE slots before their scheduled hour.

    This removes paid AI/provider latency from the publication deadline.  It is
    deliberately limited to moderate projects: an auto project must never be
    published early. Existing generation_key/idempotency and provider caches
    make repeated 5-minute checks safe and non-duplicating.
    """
    from app.models.user import User
    import re as _re_pre
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        buckets=[]
        for row in rows:
            try:
                projects=json.loads(row.value or "[]")
            except Exception:
                projects=[]
            is_owner=False
            if str(row.account_id).startswith("u"):
                try:
                    uid=int(str(row.account_id)[1:])
                    user=db.query(User).filter(User.id==uid).first()
                    is_owner=bool(user and getattr(user,"role",None)=="owner")
                except Exception:
                    pass
            buckets.append((str(row.account_id),is_owner,projects))
    finally:
        db.close()
    materialized=[]; deferred=[]; errors=[]
    for account_id,is_owner,projects in buckets:
        for proj in projects if isinstance(projects,list) else []:
            if not isinstance(proj,dict) or (proj.get("mode") or "auto") != "moderate":
                continue
            if not bool(proj.get("paid_auto_generation")) and not bool(proj.get("expected_auto_generation")):
                continue
            if not is_owner:
                from app.services.social_entitlement import normalize_posting_subscription
                sub,_=normalize_posting_subscription(proj.get("subscription") if isinstance(proj.get("subscription"),dict) else {})
                if not _sub_active(sub):
                    continue
            off=int(proj.get("tz_offset",3) or 3)
            local_now=datetime.now()+timedelta(hours=off)
            # Use minute precision, not only the integer hour.  At 18:05 the
            # 18:00 generation slot is already due, but a provider cooldown may
            # have prevented materialization before 18:00.  Keep trying that
            # same deterministic slot until it exists; never move it to a later
            # key and never publish it early (pregeneration is moderate-only).
            configured=[]
            for raw in (proj.get("times") or []):
                m=_re_pre.search(r"\d{1,2}",str(raw))
                if m and 0 <= int(m.group()) <= 23:
                    configured.append(int(m.group()))
            candidates=[]
            for h in sorted(set(configured)):
                slot_local=local_now.replace(hour=h,minute=0,second=0,microsecond=0)
                window_start=slot_local-timedelta(hours=int(lead_hours))
                if window_start <= local_now:
                    candidates.append((slot_local,h))
            if not candidates:
                continue
            pid=str(proj.get("id") or "")
            if not pid:
                continue
            # Skip slots that already have a durable post record BEFORE choosing
            # the target. Otherwise a completed morning slot wins forever and
            # starves the upcoming evening slot inside the same lead window.
            pending=[]
            for slot_local,h in candidates:
                _candidate_key="social-slot:%s:%s:%s_%02d"%(account_id,pid,local_now.strftime("%Y%m%d"),h)
                if _posting_generation_exists(account_id,_candidate_key):
                    continue
                # Once a newer configured slot has entered its preparation
                # window, an older unmaterialized slot must not starve it. The
                # older miss remains visible to SLA/incident history, while the
                # scheduler protects the current service slot.
                newer_window_open=any(
                    other_slot > slot_local
                    and other_slot-timedelta(hours=int(lead_hours)) <= local_now
                    for other_slot,_ in candidates
                )
                if slot_local <= local_now and newer_window_open:
                    continue
                pending.append((slot_local,h))
            if not pending:
                continue
            due=[x for x in pending if x[0] <= local_now]
            target_hour=(max(due,key=lambda x:x[0]) if due else min(pending,key=lambda x:x[0]))[1]
            try:
                autopost_projects(account_id,owner=is_owner,respect_time=True,only_id=pid,
                                  scheduled_hour_override=target_hour,pre_generate_only=True)
                gkey="social-slot:%s:%s:%s_%02d"%(account_id,pid,local_now.strftime("%Y%m%d"),target_hour)
                if _posting_generation_exists(account_id,gkey):
                    materialized.append({"account_id":account_id,"project_id":pid,"slot_hour":target_hour})
            except Exception as exc:
                text=str(exc or "")
                item={"account_id":account_id,"project_id":pid,"slot_hour":target_hour,"reason":text[:220]}
                if text.startswith(("CAMPAIGN_AI_PROVIDER_COOLDOWN:","CAMPAIGN_AI_RELEASED_RETRY_CAP:","CAMPAIGN_AI_OUTCOME_UNCERTAIN:")):
                    deferred.append(item)
                else:
                    errors.append(item)
    return {"ok":not errors,"lead_hours":int(lead_hours),"materialized":materialized,"deferred_external":deferred,"errors":errors}

def autopost_all(respect_time=False, skip_accounts=None):
    from app.models.user import User
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        buckets = [r.account_id for r in rows]
        owners = {}
        for b in buckets:
            is_owner = False
            if str(b).startswith("u"):
                try:
                    uid = int(str(b)[1:])
                    u = db.query(User).filter(User.id == uid).first()
                    is_owner = bool(u and getattr(u, "role", None) == "owner")
                except Exception:
                    is_owner = False
            owners[b] = is_owner
    finally:
        db.close()
    skip_accounts = {str(x) for x in (skip_accounts or set())}
    for b in buckets:
        if str(b) in skip_accounts:
            print("[autopost_all] бакет пропущен по project-isolation:", b, flush=True)
            continue
        print("[autopost_all] бакет:", b, "| owner:", owners[b], flush=True)
        try:
            autopost_projects(b, owner=owners[b], respect_time=respect_time)
        except Exception as e:
            print("[autopost_all] ошибка по", b, ":", str(e)[:200], flush=True)





def edit_telegram_post(chat_id, message_id, text):
    """Edit a BORIS-owned Telegram publication in place, preserving its media."""
    import requests
    from app import telegram_bot
    clean = _tg_safe_html(_sanitize_public_post_text(text))
    try:
        r = requests.post(
            telegram_bot.API_BASE + "/editMessageCaption",
            data={"chat_id": chat_id, "message_id": message_id, "caption": clean, "parse_mode": "HTML"},
            timeout=60,
        ).json()
        if r.get("ok"):
            return {"ok": True, "kind": "caption", "result": r.get("result")}
        if "message is not modified" in str(r.get("description") or "").lower():
            return {"ok": True, "kind": "caption", "already_clean": True}
        # Legacy text-only publication.
        r2 = requests.post(
            telegram_bot.API_BASE + "/editMessageText",
            data={"chat_id": chat_id, "message_id": message_id, "text": clean, "parse_mode": "HTML"},
            timeout=60,
        ).json()
        if r2.get("ok"):
            return {"ok": True, "kind": "text", "result": r2.get("result")}
        if "message is not modified" in str(r2.get("description") or "").lower():
            return {"ok": True, "kind": "text", "already_clean": True}
        return {"ok": False, "error": str(r2.get("description") or r.get("description") or r2)[:200]}
    except Exception as exc:
        return {"ok": False, "error": "telegram edit transport: " + str(exc)[:180]}


def _vk_wall_post_snapshot(owner_id, post_id):
    """Read one VK wall post with bounded structured error reporting."""
    import os, requests
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    try:
        payload = requests.get(
            "https://api.vk.com/method/wall.getById",
            params={
                "posts": f"{int(owner_id)}_{int(post_id)}",
                "access_token": token,
                "v": "5.199",
            },
            timeout=30,
        ).json()
    except Exception as exc:
        return {"ok": False, "error": "wall.getById transport: " + str(exc)[:180]}
    if "error" in payload:
        err = payload.get("error") or {}
        return {
            "ok": False,
            "code": int(err.get("error_code") or 0),
            "error": str(err.get("error_msg") or err)[:180],
        }
    response = payload.get("response") or {}
    items = response.get("items") if isinstance(response, dict) else response
    item = (items or [None])[0] if isinstance(items, list) else None
    if isinstance(item, dict) and item.get("is_deleted"):
        return {"ok": False, "item": item, "missing": True, "is_deleted": True}
    return {"ok": bool(item), "item": item, "missing": not bool(item)}


def _vk_photo_attachment_ids(item):
    values = []
    for attachment in (item or {}).get("attachments") or []:
        if not isinstance(attachment, dict) or attachment.get("type") != "photo":
            continue
        photo = attachment.get("photo") or {}
        if photo.get("owner_id") is None or photo.get("id") is None:
            continue
        value = f"photo{photo['owner_id']}_{photo['id']}"
        if photo.get("access_key"):
            value += "_" + str(photo["access_key"])
        values.append(value)
    return values


def repost_vk_post_preserving_photos(owner_id, old_post_id, text, resume_new_post_id=None):
    """Safely replace an uneditable zero-engagement VK post.

    Create/verify the clean copy first, delete the old post only afterwards.
    If delete fails after creation, return new_post_id so the caller can resume
    deletion without creating another duplicate on the next retry.
    """
    import os, re as _re_vk_repost, requests
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    plain = _re_vk_repost.sub(r"<[^>]+>", "", _sanitize_public_post_text(text)).strip()

    old = _vk_wall_post_snapshot(owner_id, old_post_id)
    if not old.get("ok"):
        if old.get("missing") and resume_new_post_id:
            new = _vk_wall_post_snapshot(owner_id, resume_new_post_id)
            if new.get("ok"):
                return {
                    "ok": True,
                    "old_post_id": int(old_post_id),
                    "new_post_id": int(resume_new_post_id),
                    "old_already_missing": True,
                }
        return {
            "ok": False,
            "stage": "read_old",
            "code": int(old.get("code") or 0),
            "error": str(old.get("error") or "post not found")[:180],
        }

    old_item = old.get("item") or {}
    engagement = {
        "likes": int((old_item.get("likes") or {}).get("count") or 0),
        "comments": int((old_item.get("comments") or {}).get("count") or 0),
        "reposts": int((old_item.get("reposts") or {}).get("count") or 0),
    }
    if sum(engagement.values()) > 0:
        return {
            "ok": False,
            "stage": "engagement_guard",
            "unsafe_engagement": True,
            "engagement": engagement,
            "error": "VK_REPOST_WOULD_LOSE_ENGAGEMENT",
        }

    attachments = _vk_photo_attachment_ids(old_item)
    new_post_id = int(resume_new_post_id) if resume_new_post_id else None
    if new_post_id is None:
        params = {
            "access_token": token,
            "v": "5.199",
            "owner_id": str(int(owner_id)),
            "from_group": 1,
            "message": plain,
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        try:
            posted = requests.get(
                "https://api.vk.com/method/wall.post", params=params, timeout=30
            ).json()
        except Exception as exc:
            return {"ok": False, "stage": "create_new", "error": "wall.post transport: " + str(exc)[:180]}
        if "response" not in posted:
            err = posted.get("error") or {}
            return {
                "ok": False,
                "stage": "create_new",
                "code": int(err.get("error_code") or 0),
                "error": str(err.get("error_msg") or err)[:180],
            }
        new_post_id = int((posted.get("response") or {}).get("post_id") or 0)
        if not new_post_id:
            return {"ok": False, "stage": "create_new", "error": "wall.post missing post_id"}

    new = _vk_wall_post_snapshot(owner_id, new_post_id)
    new_item = new.get("item") or {}
    actual = str(new_item.get("text") or "").strip()
    new_photos = _vk_photo_attachment_ids(new_item)
    if not new.get("ok") or actual != plain or len(new_photos) != len(attachments):
        return {
            "ok": False,
            "stage": "verify_new",
            "new_post_id": new_post_id,
            "text_ok": actual == plain,
            "attachments_expected": len(attachments),
            "attachments_actual": len(new_photos),
            "error": "VK_REPOST_VERIFY_FAILED",
        }

    try:
        deleted = requests.get(
            "https://api.vk.com/method/wall.delete",
            params={
                "owner_id": int(owner_id),
                "post_id": int(old_post_id),
                "access_token": token,
                "v": "5.199",
            },
            timeout=30,
        ).json()
    except Exception as exc:
        return {
            "ok": False,
            "stage": "delete_old",
            "new_post_id": new_post_id,
            "error": "wall.delete transport: " + str(exc)[:180],
        }
    if "response" not in deleted:
        err = deleted.get("error") or {}
        return {
            "ok": False,
            "stage": "delete_old",
            "new_post_id": new_post_id,
            "code": int(err.get("error_code") or 0),
            "error": str(err.get("error_msg") or err)[:180],
        }

    old_after = _vk_wall_post_snapshot(owner_id, old_post_id)
    if old_after.get("ok"):
        return {
            "ok": False,
            "stage": "verify_old_deleted",
            "new_post_id": new_post_id,
            "error": "VK_OLD_POST_STILL_EXISTS",
        }
    return {
        "ok": True,
        "old_post_id": int(old_post_id),
        "new_post_id": int(new_post_id),
        "attachments": len(attachments),
        "engagement": engagement,
    }


def _vk_edit_plain_text(text):
    """Prepare VK edit text without dropping an intentional 5–10 hashtag footer."""
    import re as _re_edit_vk
    tags = _extract_public_hashtags(text, limit=10)
    clean = _sanitize_public_post_text(text)
    if tags:
        clean = _append_public_hashtags(clean, tags)
    return _re_edit_vk.sub(r"<[^>]+>", "", clean).strip()

def edit_vk_post(owner_id, post_id, text):
    """Edit a BORIS-owned VK wall post while preserving photo attachments and hashtags."""
    import os, requests
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    plain = _vk_edit_plain_text(text)
    try:
        current = requests.get(
            "https://api.vk.com/method/wall.getById",
            params={"posts": f"{int(owner_id)}_{int(post_id)}", "access_token": token, "v": "5.199"},
            timeout=30,
        ).json()
        response = current.get("response") or {}
        items = response.get("items") if isinstance(response, dict) else response
        item = (items or [None])[0] if isinstance(items, list) else None
        if not item:
            return {"ok": False, "error": "wall.getById: post not found"}
        attachments = []
        for attachment in item.get("attachments") or []:
            if attachment.get("type") != "photo":
                continue
            photo = attachment.get("photo") or {}
            if photo.get("owner_id") is None or photo.get("id") is None:
                continue
            value = f"photo{photo['owner_id']}_{photo['id']}"
            if photo.get("access_key"):
                value += "_" + str(photo["access_key"])
            attachments.append(value)
        params = {
            "owner_id": int(owner_id), "post_id": int(post_id), "message": plain,
            "access_token": token, "v": "5.199",
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        edited = requests.get(
            "https://api.vk.com/method/wall.edit", params=params, timeout=30
        ).json()
        if "response" not in edited:
            err = edited.get("error") or {}
            return {
                "ok": False, "code": err.get("error_code"),
                "error": str(err.get("error_msg") or edited)[:200],
            }
        verify = requests.get(
            "https://api.vk.com/method/wall.getById",
            params={"posts": f"{int(owner_id)}_{int(post_id)}", "access_token": token, "v": "5.199"},
            timeout=30,
        ).json()
        vr = verify.get("response") or {}
        vi = vr.get("items") if isinstance(vr, dict) else vr
        vitem = (vi or [None])[0] if isinstance(vi, list) else None
        actual = str((vitem or {}).get("text") or "").strip()
        verified_photos = [
            a for a in ((vitem or {}).get("attachments") or [])
            if isinstance(a, dict) and a.get("type") == "photo"
        ]
        text_ok = actual == plain.strip()
        attachments_ok = len(verified_photos) == len(attachments)
        return {
            "ok": bool(text_ok and attachments_ok),
            "verified": True,
            "text_ok": text_ok,
            "attachments_ok": attachments_ok,
            "attachments_expected": len(attachments),
            "attachments_actual": len(verified_photos),
        }
    except Exception as exc:
        return {"ok": False, "error": "VK edit transport: " + str(exc)[:180]}




_SOCIAL_VK_BACKOFF_ACCOUNT = "__system__"
_SOCIAL_VK_BACKOFF_KEY = "social_vk_edit_backoff_until"
_SOCIAL_VK_RATE_BACKOFF_KEY = "social_vk_rate_backoff_until"


def _social_vk_rate_backoff_remaining():
    """Shared cooldown for live VK delivery after API error 9/29."""
    import time as _time
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == _SOCIAL_VK_BACKOFF_ACCOUNT,
            Storage.key == _SOCIAL_VK_RATE_BACKOFF_KEY,
        ).first()
        if not row or not row.value:
            return 0
        try:
            until = float(row.value)
        except Exception:
            return 0
        return max(0, int(until - _time.time()))
    finally:
        db.close()


def _set_social_vk_rate_backoff(seconds):
    import time as _time
    seconds = max(0, int(seconds or 0))
    until = _time.time() + seconds
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == _SOCIAL_VK_BACKOFF_ACCOUNT,
            Storage.key == _SOCIAL_VK_RATE_BACKOFF_KEY,
        ).first()
        if row:
            try:
                current = float(row.value or 0)
            except Exception:
                current = 0
            row.value = str(max(current, until))
        else:
            db.add(Storage(
                account_id=_SOCIAL_VK_BACKOFF_ACCOUNT,
                key=_SOCIAL_VK_RATE_BACKOFF_KEY,
                value=str(until),
            ))
        db.commit()
    finally:
        db.close()
    return seconds


def _clear_social_vk_rate_backoff():
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == _SOCIAL_VK_BACKOFF_ACCOUNT,
            Storage.key == _SOCIAL_VK_RATE_BACKOFF_KEY,
        ).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def _social_vk_live_delivery_pending():
    """True when current Social service still owes a VK publication.

    Historical edit/replacement maintenance must yield completely while a live
    owner or active paid-client draft/partial is waiting for VK. This is a
    local DB-only decision and makes no external API calls.
    """
    import json as _json
    db = SessionLocal()
    try:
        project_rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        project_maps = {}
        for prow in project_rows:
            try:
                projects = _json.loads(prow.value or "[]")
            except Exception:
                projects = []
            pmap = {}
            for project in projects if isinstance(projects, list) else []:
                if not isinstance(project, dict):
                    continue
                if (
                    str(project.get("mode") or "auto").lower() == "off"
                    or project.get("expected_auto_generation") is False
                ):
                    continue
                platforms = str(project.get("platforms") or "both").lower()
                if platforms not in {"vk", "both"} or not project.get("vk_owner_id"):
                    continue
                if str(prow.account_id) != "u2" and not _sub_active(project.get("subscription") or {}):
                    continue
                pmap[str(project.get("id") or "")] = project
            if pmap:
                project_maps[str(prow.account_id)] = pmap
        if not project_maps:
            return False
        for row in db.query(Storage).filter(Storage.key == "posting_posts").all():
            pmap = project_maps.get(str(row.account_id)) or {}
            if not pmap:
                continue
            try:
                posts = _json.loads(row.value or "[]")
            except Exception:
                continue
            for rec in posts if isinstance(posts, list) else []:
                if not isinstance(rec, dict) or rec.get("status") not in {"draft", "partial"}:
                    continue
                if str(rec.get("project_id") or "") not in pmap:
                    continue
                ids = rec.get("ids") if isinstance(rec.get("ids"), dict) else {}
                vk = ids.get("vk") if isinstance(ids.get("vk"), dict) else {}
                if not vk.get("post_id"):
                    return True
        return False
    finally:
        db.close()


def _social_vk_backoff_remaining():
    """Cooldown for low-priority VK maintenance.

    Historical edits/replacements must yield both to an explicit maintenance
    cooldown and to live delivery.  The live-delivery hold is computed from the
    local DB, so no VK call is needed to decide that client publication wins.
    """
    import time as _time
    stored = 0
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == _SOCIAL_VK_BACKOFF_ACCOUNT,
            Storage.key == _SOCIAL_VK_BACKOFF_KEY,
        ).first()
        if row and row.value:
            try:
                until = float(row.value)
                stored = max(0, int(until - _time.time()))
            except Exception:
                stored = 0
    finally:
        db.close()
    try:
        live_rate = int(_social_vk_rate_backoff_remaining() or 0)
    except Exception:
        live_rate = 0
    try:
        live_delivery_hold = 900 if _social_vk_live_delivery_pending() else 0
    except Exception:
        live_delivery_hold = 0
    return max(stored, live_rate, live_delivery_hold)


def _set_social_vk_backoff(seconds):
    import time as _time
    until = _time.time() + max(0, int(seconds or 0))
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == _SOCIAL_VK_BACKOFF_ACCOUNT,
            Storage.key == _SOCIAL_VK_BACKOFF_KEY,
        ).first()
        if row:
            row.value = str(until)
        else:
            db.add(Storage(
                account_id=_SOCIAL_VK_BACKOFF_ACCOUNT,
                key=_SOCIAL_VK_BACKOFF_KEY,
                value=str(until),
            ))
        db.commit()
    finally:
        db.close()
    return int(seconds or 0)


def _clear_social_vk_backoff():
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == _SOCIAL_VK_BACKOFF_ACCOUNT,
            Storage.key == _SOCIAL_VK_BACKOFF_KEY,
        ).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def _social_vk_project_priority(project):
    """Lower number means earlier VK reconciliation priority."""
    return 0 if _ocean_clients_project(project or {}) else 1


def _social_vk_pending_sort_key(item):
    """Project priority first; newest post first inside the same priority."""
    if len(item) >= 8:
        priority = int(item[0] or 0)
        post_id = item[4]
        created_at = item[7]
    else:
        priority = 0
        post_id = item[3] if len(item) > 3 else 0
        created_at = item[6] if len(item) > 6 else ""
    try:
        post_num = int(post_id or 0)
    except Exception:
        post_num = 0
    # Lower priority wins globally. Within one project class, larger/newer
    # post ids drain first so one very old record never starves newer fixes.
    return (priority, -post_num, str(created_at or ""))


def _social_vk_pending_record_retryable(rec):
    """True only for a VK edit that can still meaningfully be retried."""
    if not isinstance(rec, dict) or rec.get("status") != "published":
        return False
    if "vk" not in (rec.get("external_edit_pending") or []):
        return False
    unavailable = rec.get("external_edit_unavailable") or {}
    if isinstance(unavailable, dict) and unavailable.get("vk"):
        return False
    return True


def social_vk_external_unavailable_health():
    """Report VK edits that cannot be self-healed in place and need owner decision."""
    import json as _json

    db = SessionLocal()
    try:
        project_rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        project_map = {}
        for prow in project_rows:
            try:
                projects = _json.loads(prow.value or "[]")
            except Exception:
                projects = []
            for project in projects:
                project_map[(prow.account_id, str(project.get("id") or ""))] = project

        blocked = []
        for row in db.query(Storage).filter(Storage.key == "posting_posts").all():
            try:
                posts = _json.loads(row.value or "[]")
            except Exception:
                continue
            for rec in posts:
                if rec.get("status") != "published":
                    continue
                unavailable = rec.get("external_edit_unavailable") or {}
                vk_unavailable = unavailable.get("vk") if isinstance(unavailable, dict) else None
                if not vk_unavailable:
                    continue
                ids = rec.get("ids") or {}
                vk = ids.get("vk") if isinstance(ids, dict) else {}
                project = project_map.get((row.account_id, str(rec.get("project_id") or ""))) or {}
                blocked.append({
                    "account_id": row.account_id,
                    "record_id": rec.get("id"),
                    "project_id": rec.get("project_id"),
                    "project": str(project.get("name") or rec.get("project_name") or ""),
                    "post_id": (vk or {}).get("post_id") if isinstance(vk, dict) else None,
                    "owner_id": (vk or {}).get("owner_id") if isinstance(vk, dict) else None,
                    "reason": str((vk_unavailable or {}).get("reason") or "VK_EDIT_UNAVAILABLE"),
                    "code": (vk_unavailable or {}).get("code"),
                    "at": (vk_unavailable or {}).get("at"),
                    "is_ocean": _ocean_clients_project(project),
                })
    finally:
        db.close()

    blocked.sort(key=lambda x: (0 if x.get("is_ocean") else 1, -(int(x.get("post_id") or 0))))
    return {
        "ok": not bool(blocked),
        "count": len(blocked),
        "owner_action_required": bool(blocked),
        "blocked": blocked[:20],
        "decision": "delete_repost_or_leave" if blocked else None,
    }


def social_vk_pending_cleanup_once():
    """Retry exactly one persisted VK edit that is explicitly pending.

    Generic across Social projects. Local business truth already contains the
    approved replacement text; this function only reconciles the external VK
    copy. One item per call plus worker backoff prevents anti-spam hammering.
    """
    import json as _json

    db = SessionLocal()
    try:
        project_rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        project_map = {}
        for prow in project_rows:
            try:
                projects = _json.loads(prow.value or "[]")
            except Exception:
                projects = []
            for project in projects:
                project_map[(prow.account_id, str(project.get("id") or ""))] = project

        rows = db.query(Storage).filter(Storage.key == "posting_posts").all()
        pending = []
        for row in rows:
            try:
                posts = _json.loads(row.value or "[]")
            except Exception:
                continue
            for rec in posts:
                if not _social_vk_pending_record_retryable(rec):
                    continue
                ids = rec.get("ids") or {}
                vk = ids.get("vk") if isinstance(ids, dict) else {}
                owner_id = (vk or {}).get("owner_id") if isinstance(vk, dict) else None
                post_id = (vk or {}).get("post_id") if isinstance(vk, dict) else None
                if owner_id is None or post_id is None:
                    continue
                project = project_map.get((row.account_id, str(rec.get("project_id") or ""))) or {}
                # P0: legacy-link cleanup for «Океан Клиентов и Борис».
                # Within the same priority drain newest posts first.
                priority = _social_vk_project_priority(project)
                pending.append((
                    priority,
                    row.account_id,
                    rec.get("id"),
                    int(owner_id),
                    int(post_id),
                    rec.get("text", ""),
                    str(project.get("name") or ""),
                    str(rec.get("created_at") or ""),
                    dict(project),
                ))
        pending.sort(key=_social_vk_pending_sort_key)
    finally:
        db.close()

    if not pending:
        _clear_social_vk_backoff()
        return {"status": "idle", "checked": 0}

    _priority, account_id, record_id, owner_id, post_id, text, project_name, _created_at, project = pending[0]
    remaining = _social_vk_backoff_remaining()
    if remaining > 0:
        return {
            "status": "backoff", "checked": 0,
            "retry_after_seconds": remaining,
            "next_retry_in_seconds": remaining,
            "pending_count": len(pending),
            "next_project": project_name,
            "next_post_id": post_id,
            "owner_action_required": False,
        }

    # Last-mile public policy: backlog records may outlive the policy that
    # created them. Re-render immediately before the external VK mutation.
    # This is deliberately after backoff so a cooldown check never mutates
    # local state, and fail-closed so stale/empty public copy cannot escape.
    try:
        from app.services.social_owner_policy_v3 import render_public_text as _render_social_public_text
        safe_text = str(_render_social_public_text(text, project or {}, "vk") or "").strip()
    except Exception as exc:
        return {
            "status": "error", "stage": "pending_public_policy",
            "post_id": post_id, "checked": 0,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
            "owner_action_required": False,
        }
    if not safe_text:
        return {
            "status": "error", "stage": "pending_public_policy",
            "post_id": post_id, "checked": 0,
            "error": "PUBLIC_COPY_EMPTY_AFTER_SANITIZE",
            "owner_action_required": False,
        }
    if safe_text != str(text or "").strip():
        fresh = _load_post_record(account_id, record_id)
        if fresh:
            fresh["text"] = safe_text
            fresh["preview"] = safe_text
            fresh["editor_reason"] = "VK_PENDING_LAST_MILE_POLICY"
            fresh["edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _persist_post_record(account_id, fresh)
        text = safe_text
    else:
        text = safe_text

    result = edit_vk_post(owner_id, post_id, text)
    if result.get("ok"):
        _clear_social_vk_backoff()
        fresh = _load_post_record(account_id, record_id)
        if fresh:
            left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
            if left:
                fresh["external_edit_pending"] = left
            else:
                fresh.pop("external_edit_pending", None)
            fresh["vk_quality_edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            fresh["external_quality_edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _persist_post_record(account_id, fresh)
        return {"status": "fixed_pending", "post_id": post_id, "checked": 1}

    code = int(result.get("code") or 0)
    if code == 14:
        retry_after = _set_social_vk_backoff(21600)
        return {
            "status": "captcha", "stage": "pending_edit", "post_id": post_id,
            "code": 14, "error": str(result.get("error") or "")[:180], "checked": 1,
            "retry_after_seconds": retry_after,
            "pending_count": len(pending),
        }
    if code == 15:
        # VK no longer allows this historical post to be edited in place.
        # For the owner-approved Ocean legacy cleanup we can self-heal safely
        # only when the old post has zero likes/comments/reposts. Create and
        # verify the clean copy first; delete the old post only afterwards.
        fresh = _load_post_record(account_id, record_id)
        project = None
        db = SessionLocal()
        try:
            prow = db.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == "posting_projects",
            ).first()
            try:
                projects = _json.loads(prow.value or "[]") if prow else []
            except Exception:
                projects = []
            project = next(
                (p for p in projects if str(p.get("id") or "") == str((fresh or {}).get("project_id") or "")),
                None,
            )
        finally:
            db.close()

        if fresh and _ocean_clients_project(project or {}):
            repost_state = dict(fresh.get("vk_repost_state") or {})
            resume_new_post_id = repost_state.get("new_post_id")
            repost = repost_vk_post_preserving_photos(
                owner_id,
                post_id,
                text,
                resume_new_post_id=resume_new_post_id,
            )
            if repost.get("ok"):
                ids = dict(fresh.get("ids") or {})
                vk_ids = dict(ids.get("vk") or {})
                old_id = int(vk_ids.get("post_id") or post_id)
                vk_ids["post_id"] = int(repost.get("new_post_id"))
                ids["vk"] = vk_ids
                fresh["ids"] = ids
                left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
                if left:
                    fresh["external_edit_pending"] = left
                else:
                    fresh.pop("external_edit_pending", None)
                unavailable = dict(fresh.get("external_edit_unavailable") or {})
                unavailable.pop("vk", None)
                if unavailable:
                    fresh["external_edit_unavailable"] = unavailable
                else:
                    fresh.pop("external_edit_unavailable", None)
                fresh.pop("vk_repost_state", None)
                fresh["vk_reposted_from_post_id"] = old_id
                fresh["vk_quality_edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                fresh["external_quality_edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _persist_post_record(account_id, fresh)
                _clear_social_vk_backoff()
                return {
                    "status": "reposted_uneditable",
                    "stage": "pending_edit",
                    "old_post_id": old_id,
                    "post_id": int(repost.get("new_post_id")),
                    "code": 15,
                    "checked": 1,
                    "attachments": int(repost.get("attachments") or 0),
                    "owner_action_required": False,
                }

            if repost.get("unsafe_engagement"):
                left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
                if left:
                    fresh["external_edit_pending"] = left
                else:
                    fresh.pop("external_edit_pending", None)
                unavailable = dict(fresh.get("external_edit_unavailable") or {})
                unavailable["vk"] = {
                    "reason": "VK_EDIT_WINDOW_EXPIRED_WITH_ENGAGEMENT",
                    "code": 15,
                    "engagement": repost.get("engagement") or {},
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                fresh["external_edit_unavailable"] = unavailable
                fresh.pop("vk_repost_state", None)
                _persist_post_record(account_id, fresh)
                _clear_social_vk_backoff()
                return {
                    "status": "skipped_uneditable",
                    "stage": "engagement_guard",
                    "post_id": post_id,
                    "code": 15,
                    "checked": 1,
                    "engagement": repost.get("engagement") or {},
                    "owner_action_required": True,
                    "decision": "delete_repost_or_leave",
                }

            # Partial verified repost state: remember the clean new post so a
            # retry continues deletion instead of creating a duplicate.
            if repost.get("new_post_id"):
                fresh["vk_repost_state"] = {
                    "new_post_id": int(repost.get("new_post_id")),
                    "old_post_id": int(post_id),
                    "stage": str(repost.get("stage") or ""),
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                _persist_post_record(account_id, fresh)
            repost_code = int(repost.get("code") or 0)
            if repost_code == 14:
                retry_after = _set_social_vk_backoff(21600)
                return {
                    "status": "captcha",
                    "stage": "repost_" + str(repost.get("stage") or "unknown"),
                    "post_id": post_id,
                    "code": 14,
                    "checked": 1,
                    "retry_after_seconds": retry_after,
                    "next_retry_in_seconds": retry_after,
                    "owner_action_required": False,
                }
            return {
                "status": "error",
                "stage": "repost_" + str(repost.get("stage") or "unknown"),
                "post_id": post_id,
                "code": repost_code,
                "checked": 1,
                "error": str(repost.get("error") or "")[:180],
                "owner_action_required": False,
            }

        # Non-Ocean historical posts stay fail-safe: never delete/repost
        # automatically because that may discard engagement/context.
        _clear_social_vk_backoff()
        if fresh:
            left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
            if left:
                fresh["external_edit_pending"] = left
            else:
                fresh.pop("external_edit_pending", None)
            unavailable = dict(fresh.get("external_edit_unavailable") or {})
            unavailable["vk"] = {
                "reason": "VK_EDIT_WINDOW_EXPIRED",
                "code": 15,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            fresh["external_edit_unavailable"] = unavailable
            _persist_post_record(account_id, fresh)
        return {
            "status": "skipped_uneditable", "stage": "pending_edit",
            "post_id": post_id, "code": 15, "checked": 1,
            "error": str(result.get("error") or "")[:180],
            "owner_action_required": True,
            "decision": "delete_repost_or_leave",
        }
    return {
        "status": "error", "stage": "pending_edit", "post_id": post_id,
        "code": code, "error": str(result.get("error") or "")[:180], "checked": 1,
    }


def delete_telegram_post(chat_id, message_id):
    import requests
    from app import telegram_bot
    try:
        r = requests.post(telegram_bot.API_BASE + "/deleteMessage",
                          data={"chat_id": chat_id, "message_id": message_id}, timeout=60)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def delete_vk_post(owner_id, post_id):
    import os, requests
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    try:
        r = requests.get(
            "https://api.vk.com/method/wall.delete",
            params={"access_token": token, "v": "5.199",
                    "owner_id": str(owner_id), "post_id": post_id},
            timeout=30,
        ).json()
    except Exception as exc:
        return {"ok": False, "error": "wall.delete transport: " + str(exc)[:200]}
    if "response" in r:
        return {"ok": True, "response": r.get("response")}
    return {"ok": False, "error": "wall.delete: " + str(r.get("error") or r)[:200]}


def _all_user_buckets():
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        return [r.account_id for r in rows]
    finally:
        db.close()

def _social_delivery_external_wait_map():
    """Return fresh per-project external transport waits without API calls."""
    try:
        health = social_transport_readiness_cached_health()
    except Exception:
        return {}
    if not health.get("fresh"):
        return {}
    out = {}
    for item in health.get("projects") or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("account_id") or "u2"), str(item.get("project_id") or ""))
        waits = set()
        for platform in ("telegram", "vk"):
            part = item.get(platform) if isinstance(item.get(platform), dict) else {}
            if (not part.get("ready") and part.get("retryable_external")
                    and str(part.get("dependency_state") or "") == "waiting_external"):
                waits.add("tg" if platform == "telegram" else platform)
        if waits:
            out[key] = waits
    return out


def due_posts_all(dry=False):
    """Publish due Social drafts safely across scheduler/API replicas.

    Candidates come from a cheap snapshot, but every external delivery acquires
    a per-post PostgreSQL advisory lock and re-reads the lifecycle record before
    sending. The result is persisted record-by-record while the lock is held.
    """
    import json as _json
    external_wait = _social_delivery_external_wait_map()
    # VK live delivery is globally paced: all owner Social projects share one
    # token/IP, so at most one real VK publication attempt is made per rescue
    # pass. This prevents a backlog burst from immediately re-triggering Flood
    # control after cooldown expiry.
    vk_attempted = False
    for acc in _all_user_buckets():
        db = SessionLocal()
        try:
            prow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_projects").first()
            projects = _json.loads(prow.value) if prow and prow.value else []
            drow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_posts").first()
            snapshot = _json.loads(drow.value) if drow and drow.value else []
        finally:
            db.close()
        projmap = {p.get("id"): p for p in projects}
        sent_projects = set()

        # Newest VK-only live work gets the first VK chance after a cooldown.
        # Stable two-pass sorting keeps newest intent inside each priority class
        # and prevents a partially delivered TG+VK backlog from starving the
        # manicure VK-only project forever.
        snapshot = sorted(snapshot, key=lambda x: str(x.get("created_at") or ""), reverse=True)
        snapshot = sorted(
            snapshot,
            key=lambda x: (
                0 if str((projmap.get(x.get("project_id")) or {}).get("platforms") or "both").lower() == "vk" else 1
            ),
        )

        for candidate in snapshot:
            if candidate.get("status") not in ("draft", "partial"):
                continue
            project_id = candidate.get("project_id")
            proj = projmap.get(project_id)
            if not proj or project_id in sent_projects:
                continue

            now_local = datetime.now() + timedelta(hours=int(proj.get("tz_offset", 3) or 3))
            now_utc = datetime.now()
            due = candidate.get("status") == "partial"
            reason = "retry_partial_delivery" if due else ""
            pa = str(candidate.get("publish_at") or "").strip()
            if pa:
                try:
                    if now_local >= datetime.strptime(pa[:16], "%Y-%m-%d %H:%M"):
                        due = True
                        reason = "publish_at " + pa
                except Exception:
                    pass
            if not due and not pa:
                scheduled_due_local = _scheduled_record_delivery_due_local(candidate, proj)
                if scheduled_due_local is not None and now_local >= scheduled_due_local:
                    due = True
                    reason = "scheduled_generation_deadline " + scheduled_due_local.strftime("%Y-%m-%d %H:%M")
                aph = int(proj.get("auto_publish_hours") or 0)
                if not due and aph > 0:
                    try:
                        created = datetime.strptime(str(candidate.get("created_at") or "")[:16], "%Y-%m-%d %H:%M")
                        if now_utc >= created + timedelta(hours=STALE_AFTER_HOURS):
                            if not dry:
                                lock = _try_post_delivery_lock(acc, candidate.get("id"))
                                if lock is not None:
                                    try:
                                        fresh = _load_post_record(acc, candidate.get("id"))
                                        if fresh and fresh.get("status") == "draft" and not fresh.get("ids"):
                                            fresh["status"] = "expired"
                                            fresh["error"] = "STALE_DRAFT_EXPIRED"
                                            _persist_post_record(acc, fresh)
                                    finally:
                                        _release_post_delivery_lock(lock, acc, candidate.get("id"))
                            print("[due] ПРОСРОЧЕН, не публикуем:", proj.get("name"),
                                  "| создан", candidate.get("created_at", ""), flush=True)
                            continue
                        if now_utc >= created + timedelta(hours=aph):
                            due = True
                            reason = "auto_publish_hours %sч от %s" % (aph, candidate.get("created_at", ""))
                    except Exception:
                        pass
            if not due:
                continue

            # Do not hammer a platform while a fresh transport probe says its
            # failure is retryable external (for example VK Flood control).
            # Completed platforms stay completed; stale/missing readiness does
            # not suppress normal delivery.
            waits = external_wait.get((str(acc), str(project_id)), set())
            existing_ids = candidate.get("ids") if isinstance(candidate.get("ids"), dict) else {}
            missing_required = {
                platform for platform in _required_delivery_platforms(proj)
                if not isinstance(existing_ids.get(platform), dict)
                or not (existing_ids.get(platform) or {}).get("message_id" if platform == "tg" else "post_id")
            }
            if candidate.get("status") == "partial" and missing_required and missing_required.issubset(waits):
                print("[due] WAITING_EXTERNAL, пропускаем до следующей transport readiness:",
                      proj.get("name"), ",".join(sorted(missing_required)), flush=True)
                continue

            if dry:
                platforms = proj.get("platforms") or "both"
                targets = []
                if proj.get("channel_tg") and platforms in ("tg", "both"):
                    targets.append("TG " + str(proj.get("channel_tg")))
                if proj.get("vk_owner_id") and platforms in ("vk", "both"):
                    targets.append("VK " + str(proj.get("vk_owner_id")))
                print("[due DRY]", proj.get("name"), "|", reason, "| ->",
                      ", ".join(targets) or "НЕТ КАНАЛА", flush=True)
                continue

            delivery_lock = _try_post_delivery_lock(acc, candidate.get("id"))
            if delivery_lock is None:
                print("[due] BUSY, публикацию уже обрабатывает другой процесс:",
                      proj.get("name"), candidate.get("id"), flush=True)
                continue
            try:
                rec = _load_post_record(acc, candidate.get("id"))
                if not rec or rec.get("status") not in ("draft", "partial"):
                    continue

                # Re-evaluate due state from the fresh record after lock acquisition.
                fresh_due = rec.get("status") == "partial"
                fresh_pa = str(rec.get("publish_at") or "").strip()
                if fresh_pa:
                    try:
                        fresh_due = fresh_due or now_local >= datetime.strptime(fresh_pa[:16], "%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                elif not fresh_due:
                    scheduled_due_local = _scheduled_record_delivery_due_local(rec, proj)
                    if scheduled_due_local is not None and now_local >= scheduled_due_local:
                        fresh_due = True
                    else:
                        aph = int(proj.get("auto_publish_hours") or 0)
                        if aph > 0:
                            try:
                                created = datetime.strptime(str(rec.get("created_at") or "")[:16], "%Y-%m-%d %H:%M")
                                fresh_due = now_utc >= created + timedelta(hours=aph)
                            except Exception:
                                pass
                if not fresh_due:
                    continue

                platforms = proj.get("platforms") or "both"
                bname = rec.get("banner") or ""
                bpath = os.path.join(BANNER_DIR, bname) if bname else None
                media_ok, media_err = _banner_delivery_ready({"banner": bpath}, proj.get("image_source", "ai"))
                if not media_ok:
                    rec["error"] = "BANNER_REQUIRED: " + str(media_err)[:220]
                    _persist_post_record(acc, rec)
                    print("[due] BANNER_REQUIRED, не публикуем:", proj.get("name"), "|", media_err, flush=True)
                    continue

                _tracking = _ocean_tracking_record(rec.get("text", ""), proj, rec.get("id"))
                if _tracking:
                    rec.update(_tracking)
                _tracking_id = str(rec.get("utm_content") or "")
                results = {}
                existing_ids = dict(rec.get("ids") or {})
                if proj.get("channel_tg") and platforms in ("tg", "both"):
                    if isinstance(existing_ids.get("tg"), dict) and existing_ids["tg"].get("message_id"):
                        results["tg"] = {"ok": True, "skipped_existing": True,
                                         "message_id": existing_ids["tg"].get("message_id")}
                    else:
                        results["tg"] = _send_telegram(
                            proj["channel_tg"], _project_delivery_text_tracked(rec.get("text", ""), proj, "tg", tracking_id=_tracking_id), bpath,
                            tenant_scope=acc
                        )
                if proj.get("vk_owner_id") and platforms in ("vk", "both"):
                    if isinstance(existing_ids.get("vk"), dict) and existing_ids["vk"].get("post_id"):
                        results["vk"] = {"ok": True, "skipped_existing": True,
                                         "post_id": existing_ids["vk"].get("post_id")}
                    else:
                        if vk_attempted:
                            results["vk"] = {
                                "ok": False,
                                "error": "VK live delivery paced: next rescue pass",
                                "reason": "vk_delivery_paced",
                                "dependency_state": "queued",
                                "owner_action_required": False,
                            }
                        else:
                            vk_attempted = True
                            results["vk"] = _send_vk(
                                int(proj["vk_owner_id"]), _project_delivery_text_tracked(rec.get("text", ""), proj, "vk", tracking_id=_tracking_id), bpath,
                                tenant_scope=acc
                            )

                rec["ids"] = _merge_delivery_ids(existing_ids, proj, results)
                rec["external_post_id"] = dict(rec.get("ids") or {})
                complete = _all_required_delivery_ok(proj, results)
                if complete:
                    _mark_social_delivery_published(
                        rec,
                        proj,
                        preview_text=_project_delivery_text_tracked(
                            rec.get("text", ""), proj, "tg" if proj.get("channel_tg") else "vk", tracking_id=_tracking_id
                        ),
                    )
                    rec.pop("delivery_failures", None)
                else:
                    rec["status"] = "partial"
                    rec["publication_status"] = "partial"
                    errs = []
                    delivery_failures = {}
                    for platform in _required_delivery_platforms(proj):
                        rv = results.get(platform) if isinstance(results.get(platform), dict) else {}
                        if not rv.get("ok"):
                            errs.append(platform + ": " + str(rv.get("error") or "delivery_failed")[:120])
                            delivery_failures[platform] = {
                                key: rv.get(key)
                                for key in (
                                    "error", "reason", "dependency_state", "retryable_external",
                                    "retry_after_seconds", "owner_action_required",
                                )
                                if rv.get(key) is not None
                            }
                    rec["delivery_failures"] = delivery_failures
                    rec["error"] = "; ".join(errs)[:300]

                if not _persist_post_record(acc, rec):
                    raise RuntimeError("SOCIAL_POST_RECORD_DISAPPEARED_DURING_DELIVERY")
                sent_projects.add(project_id)

                if complete:
                    try:
                        count_key = "posting_count:%s:%s" % (str(project_id or ""), now_local.strftime("%Y%m%d"))
                        cdb = SessionLocal()
                        try:
                            crow = cdb.query(Storage).filter(Storage.account_id == acc, Storage.key == count_key).first()
                            if crow:
                                crow.value = str(int(crow.value or 0) + 1)
                            else:
                                cdb.add(Storage(account_id=acc, key=count_key, value="1"))
                            cdb.commit()
                        finally:
                            cdb.close()
                    except Exception as count_exc:
                        print("[due] posting_count update failed:", str(count_exc)[:120], flush=True)
                    try:
                        from app.services.action_log import log_action, ACTOR_CRON
                        channels = []
                        if isinstance(results.get("tg"), dict) and results["tg"].get("ok"):
                            channels.append("Telegram")
                        if isinstance(results.get("vk"), dict) and results["vk"].get("ok"):
                            channels.append("ВКонтакте")
                        log_action(account_id=acc, action="Опубликовал пост по расписанию",
                                   object_kind="пост", object_name=(rec.get("text") or "")[:150],
                                   after_val=", ".join(channels) or "канал не определён",
                                   reason="время выхода %s" % (reason or "по расписанию"),
                                   actor=ACTOR_CRON, source="posting_runner.due")
                    except Exception:
                        pass
                    _notify_posted(acc, proj.get("name") or rec.get("project_name"),
                                   rec.get("text", "")[:60], proj, results, bname)
                    print(
                        "[due] опубликован НА ВСЕХ площадках:",
                        proj.get("name"),
                        "|", reason,
                        "| record_id=", rec.get("id"),
                        "| generation_key=", rec.get("generation_key"),
                        flush=True,
                    )
                else:
                    print("[due] ЧАСТИЧНАЯ доставка, повторим только недостающую площадку:",
                          proj.get("name"), rec.get("error", ""), flush=True)
            finally:
                _release_post_delivery_lock(delivery_lock, acc, candidate.get("id"))


def warn_stale_drafts(dry=False):
    """Одна сводка в сутки: какие проекты ждут утверждения дольше суток.

    Именно молчание, а не механизм автопубликации, привело к лавине 06.08:
    черновики копились две недели, и об этом никто не знал. Сообщение одно
    на все проекты — иначе получится вторая лавина, только в уведомлениях.
    """
    import json as _json
    stale = []
    for acc in _all_user_buckets():
        db = SessionLocal()
        try:
            prow = db.query(Storage).filter(Storage.account_id == acc,
                                            Storage.key == "posting_projects").first()
            projects = _json.loads(prow.value) if prow else []
            drow = db.query(Storage).filter(Storage.account_id == acc,
                                            Storage.key == "posting_posts").first()
            posts = _json.loads(drow.value) if drow else []
        finally:
            db.close()
        projmap = {p.get("id"): p for p in projects}
        by_proj = {}
        for rec in posts:
            if rec.get("status") != "draft":
                continue
            proj = projmap.get(rec.get("project_id"))
            if not proj:
                continue
            created = (rec.get("created_at") or "")[:16]
            try:
                dt = datetime.strptime(created, "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if datetime.now() < dt + timedelta(hours=STALE_AFTER_HOURS):
                continue
            name = proj.get("name") or str(rec.get("project_id"))
            cur = by_proj.setdefault(name, {"count": 0, "oldest": created})
            cur["count"] += 1
            if created < cur["oldest"]:
                cur["oldest"] = created
        for name, info in by_proj.items():
            stale.append((name, info["count"], info["oldest"]))

    if not stale:
        print("[stale] зависших черновиков нет", flush=True)
        return

    lines = ["\u26a0\ufe0f <b>Черновики ждут вашего утверждения</b>", ""]
    for name, count, oldest in sorted(stale, key=lambda x: x[2]):
        lines.append("\u2022 " + str(name) + " — " + str(count) +
                     ", самый старый от " + str(oldest))
    lines.append("")
    lines.append("Старше суток BORIS их публиковать не станет.")
    text = "\n".join(lines)

    if dry:
        print("[stale DRY]\n" + text, flush=True)
        return

    try:
        from app.telegram_bot import send_telegram_message
        for _acc, cfg in _POST_NOTIFY.items():
            send_telegram_message(str(cfg["chat_id"]), text, thread_id=cfg.get("thread_id"))
            print("[stale] сводка отправлена:", len(stale), "проектов", flush=True)
            break
    except Exception as _e:
        print("[stale] ошибка отправки:", str(_e)[:150], flush=True)


# --- OCEAN CLIENTS: deterministic compact footer for one-piece TG/VK media posts ---
def _ocean_clients_project(proj):
    name = str((proj or {}).get("name") or "").strip().lower()
    return "океан клиент" in name


def ensure_ocean_media_reuse(account_id="u2"):
    """Hard safety floor: Ocean auto-posts may reuse existing media only.

    This rule is intentionally independent from the mutable owner-policy layer.
    If a future deploy or manual edit switches Ocean to AI/stock generation,
    self-heal it back to `reuse` before the scheduler is allowed to generate.
    """
    import json as _json
    db = SessionLocal()
    try:
        row = (
            db.query(Storage)
            .filter(Storage.account_id == account_id, Storage.key == "posting_projects")
            .with_for_update()
            .first()
        )
        if not row:
            return {"ok": False, "changed": False, "reason": "posting_projects_missing"}
        try:
            projects = _json.loads(row.value or "[]")
        except Exception as exc:
            db.rollback()
            return {"ok": False, "changed": False, "reason": "invalid_json:" + type(exc).__name__}
        if not isinstance(projects, list):
            db.rollback()
            return {"ok": False, "changed": False, "reason": "posting_projects_not_list"}

        changed = []
        found = 0
        for project in projects:
            if not _ocean_clients_project(project):
                continue
            found += 1
            if str(project.get("image_source") or "").lower() != "reuse":
                changed.append({
                    "project_id": str(project.get("id") or ""),
                    "before": project.get("image_source"),
                    "after": "reuse",
                })
                project["image_source"] = "reuse"

        if found == 0:
            db.rollback()
            return {"ok": False, "changed": False, "reason": "ocean_project_missing"}
        if changed:
            row.value = _json.dumps(projects, ensure_ascii=False)
            db.commit()
        else:
            db.rollback()
        return {"ok": True, "changed": bool(changed), "changes": changed, "image_source": "reuse"}
    except Exception as exc:
        db.rollback()
        return {"ok": False, "changed": False, "reason": type(exc).__name__ + ": " + str(exc)[:160]}
    finally:
        db.close()


def _strip_ocean_links(text):
    """Ocean Clients delivery is link-free; old video footer lines are removed too."""
    import re as _re
    value = str(text or "")
    value = _re.sub(r"(?im)^.*(?:🎥\s*BORIS в работе|YouTube\s*:|RuTube\s*:|Видео BORIS регулярно).*$", "", value)
    value = _re.sub(r"(?i)https?://\S+", "", value)
    value = _re.sub(r"(?i)www\.\S+", "", value)
    value = _re.sub(r"(?i)\b(?:youtu\.be|youtube\.com|rutube\.ru|boris-ai\.pro|t\.me|vk\.com)/\S*", "", value)
    value = _re.sub(r"[ \t]+\n", "\n", value)
    value = _re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _ocean_hashtag_block(text):
    hay = str(text or "").lower()
    if any(w in hay for w in ("авито", "автозагруз", "объявлен", "фид", "авитолог")):
        return "#BORIS #авито #авитолог #объявления #автоматизация"
    if any(w in hay for w in ("продаж", "клиент", "менеджер", "роп", "звонок", "crm")):
        return "#BORIS #продажи #клиенты #отделпродаж #автоматизация"
    if any(w in hay for w in ("реклам", "маркет", "лидоген", "контент", "соцсет")):
        return "#BORIS #маркетинг #реклама #лидогенерация #автоматизация"
    return "#BORIS #ИИ #бизнес #маркетинг #автоматизация"


def _ocean_final_text(text, proj=None, max_chars=1000):
    """Canonical Ocean public copy with client-owned footer in every fallback."""
    from app.services.social_owner_policy_v3 import _ocean_clean
    value = _ocean_clean(text, proj)
    return value[: int(max_chars)].rstrip()


def ocean_vk_link_cleanup_once():
    """Self-heal one legacy Ocean Clients VK post that still contains links/video.

    Runs from the canonical social worker at most once per hour. It edits no more
    than one post per call to avoid triggering VK anti-spam. Captcha/error states
    are returned to the worker heartbeat and retried on a later hour.
    """
    import re as _re
    import requests as _requests

    remaining=_social_vk_backoff_remaining()
    if remaining > 0:
        return {
            "status":"backoff",
            "stage":"legacy_link_cleanup",
            "checked":0,
            "retry_after_seconds":remaining,
            "next_retry_in_seconds":remaining,
            "owner_action_required":False,
        }

    token = os.environ.get("VK_TOKEN", "")
    if not token:
        return {"status": "disabled", "reason": "VK_TOKEN_MISSING"}

    db = SessionLocal()
    try:
        project_rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        candidates = []
        for prow in project_rows:
            try:
                projects = json.loads(prow.value or "[]")
            except Exception:
                continue
            oceans = [p for p in projects if _ocean_clients_project(p) and p.get("vk_owner_id")]
            if not oceans:
                continue
            posts_row = db.query(Storage).filter(
                Storage.account_id == prow.account_id,
                Storage.key == "posting_posts",
            ).first()
            try:
                posts = json.loads(posts_row.value or "[]") if posts_row else []
            except Exception:
                posts = []
            by_project = {str(p.get("id")): p for p in oceans}
            for rec in posts:
                if rec.get("status") != "published":
                    continue
                proj = by_project.get(str(rec.get("project_id") or ""))
                if not proj:
                    continue
                unavailable = rec.get("external_edit_unavailable") or {}
                if isinstance(unavailable, dict) and unavailable.get("vk"):
                    # Permanent in-place edit limitation is surfaced by
                    # social_vk_external_unavailable_health(), not retried here.
                    continue
                ids = rec.get("ids") or {}
                vk = ids.get("vk") if isinstance(ids, dict) else {}
                post_id = (vk or {}).get("post_id") if isinstance(vk, dict) else None
                if not post_id:
                    continue
                candidates.append((prow.account_id, proj, rec, int(post_id)))
    finally:
        db.close()

    if not candidates:
        _clear_social_vk_backoff()
        return {"status": "idle", "checked": 0}

    # Owner-approved quality cleanup takes priority over the older link-only
    # migration. The worker handles at most one pending VK edit per cleanup
    # cycle; its existing CAPTCHA branch backs off for six hours, so this can
    # self-heal without hammering VK anti-spam.
    pending = [
        (acc, proj, rec, post_id)
        for acc, proj, rec, post_id in candidates
        if "vk" in (rec.get("external_edit_pending") or [])
    ]
    if pending:
        account_id, proj, rec, post_id = pending[0]
        result = edit_vk_post(int(proj.get("vk_owner_id")), int(post_id), rec.get("text", ""))
        if result.get("ok"):
            fresh = _load_post_record(account_id, rec.get("id"))
            if fresh:
                left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
                if left:
                    fresh["external_edit_pending"] = left
                else:
                    fresh.pop("external_edit_pending", None)
                fresh["vk_quality_edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _persist_post_record(account_id, fresh)
            _clear_social_vk_backoff()
            return {"status": "fixed_pending", "post_id": int(post_id), "checked": 1}
        code = int(result.get("code") or 0)
        if code == 14:
            retry_after=_set_social_vk_backoff(21600)
            return {
                "status":"captcha",
                "stage":"pending_edit",
                "post_id":int(post_id),
                "code":14,
                "error":str(result.get("error") or "Captcha needed")[:180],
                "checked":1,
                "retry_after_seconds":retry_after,
                "next_retry_in_seconds":retry_after,
                "owner_action_required":False,
            }
        return {
            "status": "error", "stage": "pending_edit", "post_id": int(post_id),
            "code": code, "error": str(result.get("error") or "")[:180], "checked": 1,
        }

    # Group batch reads by VK wall owner to minimize API pressure.
    checked = 0
    for owner_id in sorted({int(p.get("vk_owner_id")) for _, p, _, _ in candidates}):
        group = [(acc, p, rec, pid) for acc, p, rec, pid in candidates
                 if int(p.get("vk_owner_id")) == owner_id]
        ids = ",".join(f"{owner_id}_{pid}" for _, _, _, pid in group[:100])
        payload = _requests.get(
            "https://api.vk.com/method/wall.getById",
            params={"posts": ids, "access_token": token, "v": "5.199"},
            timeout=30,
        ).json()
        if "error" in payload:
            err = payload.get("error") or {}
            return {
                "status": "error",
                "stage": "get",
                "code": err.get("error_code"),
                "error": str(err.get("error_msg") or "")[:180],
            }
        response = payload.get("response") or {}
        items = response.get("items") if isinstance(response, dict) else response
        items = items or []
        actual = {int(x.get("id")): x for x in items if x.get("id") is not None}

        for account_id, proj, rec, post_id in group:
            item = actual.get(post_id)
            if not item:
                continue
            checked += 1
            current = str(item.get("text") or "")
            # Reconcile against the canonical VK rendering instead of treating
            # every URL as forbidden. Ocean intentionally carries two trusted
            # BORIS URLs with short VK UTM tags.
            clean = _vk_edit_plain_text(_project_delivery_text_tracked(rec.get("text", ""), proj, "vk", tracking_id=str(rec.get("utm_content") or _social_utm_content(rec.get("id"), rec.get("text", "")))))
            if current.strip() == clean.strip():
                continue

            photos = []
            for attachment in item.get("attachments") or []:
                if attachment.get("type") != "photo":
                    continue
                ph = attachment.get("photo") or {}
                if ph.get("owner_id") is None or ph.get("id") is None:
                    continue
                value = f"photo{ph['owner_id']}_{ph['id']}"
                if ph.get("access_key"):
                    value += "_" + str(ph["access_key"])
                photos.append(value)

            params = {
                "owner_id": owner_id,
                "post_id": post_id,
                "message": clean,
                "access_token": token,
                "v": "5.199",
            }
            if photos:
                params["attachments"] = ",".join(photos)
            edit = _requests.get(
                "https://api.vk.com/method/wall.edit",
                params=params,
                timeout=30,
            ).json()
            if "response" not in edit:
                err = edit.get("error") or {}
                code = int(err.get("error_code") or 0)
                if code == 14:
                    retry_after=_set_social_vk_backoff(21600)
                    return {
                        "status":"captcha",
                        "stage":"edit",
                        "post_id":post_id,
                        "code":14,
                        "error":str(err.get("error_msg") or "Captcha needed")[:180],
                        "checked":checked,
                        "retry_after_seconds":retry_after,
                        "next_retry_in_seconds":retry_after,
                        "owner_action_required":False,
                    }
                return {
                    "status": "error",
                    "stage": "edit",
                    "post_id": post_id,
                    "code": code,
                    "error": str(err.get("error_msg") or "")[:180],
                    "checked": checked,
                }

            # Verify the mutation before changing local truth.
            verify = _requests.get(
                "https://api.vk.com/method/wall.getById",
                params={
                    "posts": f"{owner_id}_{post_id}",
                    "access_token": token,
                    "v": "5.199",
                },
                timeout=30,
            ).json()
            vr = verify.get("response") or {}
            vi = vr.get("items") if isinstance(vr, dict) else vr
            vitem = (vi or [None])[0] if isinstance(vi, list) else None
            vtext = str((vitem or {}).get("text") or "")
            # The canonical desired text itself now includes trusted BORIS URLs.
            # Verification therefore compares exact public copy instead of
            # declaring every URL invalid.
            still_bad = vtext.strip() != clean.strip()
            if still_bad:
                return {"status": "verify_failed", "post_id": post_id, "checked": checked}

            fresh = _load_post_record(account_id, rec.get("id"))
            if fresh:
                fresh["preview"] = clean
                _persist_post_record(account_id, fresh)
            _clear_social_vk_backoff()
            return {"status": "fixed", "post_id": post_id, "checked": checked}

    _clear_social_vk_backoff()
    return {"status": "clean", "checked": checked}


def _social_utm_content(post_id="", text=""):
    """Short stable UTM id. Production records use their durable post id; fallback is deterministic."""
    import hashlib as _hashlib
    import re as _re
    raw = _re.sub(r"[^a-z0-9]", "", str(post_id or "").lower())
    if raw:
        return "p" + raw[:8]
    digest = _hashlib.sha1(str(text or "").encode("utf-8", errors="ignore")).hexdigest()[:8]
    return "p" + digest


def _ocean_direction(text):
    """Explicit software-development intent wins; generic sales falls back to BORIS."""
    hay = str(text or "").lower().replace("ё", "е")
    dev_markers = (
        "разработк", "программное обеспеч", "по для ", "saas", "сайт", "интернет-магазин",
        "интернет магазин", "мобильн", "приложен", "crm на заказ", "erp", "api",
        "интеграц", "личный кабинет", "личные кабинеты", "внутреннее по", "внутреннего по", "маркетплейс",
        "чат-бот", "чат бот", "telegram-бот", "telegram бот", "телеграм-бот", "телеграм бот",
        "vk-бот", "vk бот", "max-бот", "max бот", "автоматизация бизнеса", "веб-сервис", "веб сервис",
    )
    return "software_dev" if any(marker in hay for marker in dev_markers) else "boris"


def _ocean_tracking_spec(text, platform="tg", tracking_id=""):
    from urllib.parse import urlencode as _urlencode
    source = "vk" if str(platform or "").strip().lower() == "vk" else "tg"
    direction = _ocean_direction(text)
    dev = direction == "software_dev"
    landing = "https://boris-ai.pro/software-dev/" if dev else "https://boris-ai.pro/"
    campaign = "dev" if dev else "ocean"
    utm_content = str(tracking_id or _social_utm_content(text=text)).strip().lower()
    query = _urlencode({
        "utm_source": source,
        "utm_medium": "social",
        "utm_campaign": campaign,
        "utm_content": utm_content,
    })
    return {
        "direction": direction,
        "landing_url": landing,
        "utm_source": source,
        "utm_medium": "social",
        "utm_campaign": campaign,
        "utm_content": utm_content,
        "final_url": landing + "?" + query,
        "cta": "Обсудить разработку:" if dev else "Подробнее:",
    }


def _ocean_tracking_record(text, proj, record_id):
    from app.services.social_owner_policy_v3 import is_ocean as _is_ocean
    if not _is_ocean(proj):
        return {}
    tid = _social_utm_content(record_id, text)
    platforms = _required_delivery_platforms(proj)
    specs = {platform: _ocean_tracking_spec(text, platform, tid) for platform in platforms}
    canonical = next(iter(specs.values()), _ocean_tracking_spec(text, "tg", tid))
    return {
        "channel": str((proj or {}).get("platforms") or "both"),
        "direction": canonical["direction"],
        "landing_url": canonical["landing_url"],
        "utm_medium": "social",
        "utm_campaign": canonical["utm_campaign"],
        "utm_content": tid,
        "utm_sources": {k: v["utm_source"] for k, v in specs.items()},
        "final_urls": {k: v["final_url"] for k, v in specs.items()},
    }


def _ocean_publication_gate(rendered, source_text, platform="tg", tracking_id=""):
    """Fail closed before transport: one allowed BORIS URL with exact landing and UTM contract."""
    import re as _re
    from urllib.parse import parse_qs as _parse_qs, urlsplit as _urlsplit
    expected = _ocean_tracking_spec(source_text, platform, tracking_id)
    urls = _re.findall(r"https?://[^\s<>]+", str(rendered or ""))
    errors = []
    if len(urls) != 1:
        errors.append("url_count")
    url = urls[0].rstrip(".,);]}") if urls else ""
    if url:
        parsed = _urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "boris-ai.pro":
            errors.append("domain")
        expected_path = _urlsplit(expected["landing_url"]).path or "/"
        if parsed.path != expected_path:
            errors.append("landing")
        qs = _parse_qs(parsed.query, keep_blank_values=True)
        checks = {
            "utm_source": expected["utm_source"],
            "utm_medium": "social",
            "utm_campaign": expected["utm_campaign"],
            "utm_content": expected["utm_content"],
        }
        for key, value in checks.items():
            if qs.get(key) != [value]:
                errors.append(key)
        if set(qs) != set(checks):
            errors.append("unexpected_query")
    return {"ok": not errors, "errors": sorted(set(errors)), "expected": expected, "url": url}


def _ocean_finalize_delivery(rendered, source_text, platform="tg", tracking_id=""):
    """Replace legacy Ocean footer links with one intent-specific tracked link and validate it."""
    import re as _re
    value = str(rendered or "")
    value = _re.sub(
        r"(?im)^\s*(?:🤖\s*BORIS:|💻\s*Разработка\s+ПО:|Подробнее:|Обсудить\s+разработку:)\s*"
        r"https://boris-ai\.pro(?:/software-dev/|/)?\?[^\s]+\s*$",
        "",
        value,
    )
    value = _re.sub(r"\n{3,}", "\n\n", value).strip()
    spec = _ocean_tracking_spec(source_text, platform, tracking_id)
    footer = spec["cta"] + " " + spec["final_url"]
    allowed = max(120, 1000 - len(footer) - 2)
    if len(value) > allowed:
        value = value[:allowed].rstrip(" ,;:-") + "…"
    final = (value + "\n\n" + footer).strip()
    gate = _ocean_publication_gate(final, source_text, platform, spec["utm_content"])
    if not gate["ok"]:
        value = _re.sub(r"https?://[^\s<>]+", "", value)
        value = _re.sub(r"\n{3,}", "\n\n", value).strip()
        final = (value + "\n\n" + footer).strip()
        gate = _ocean_publication_gate(final, source_text, platform, spec["utm_content"])
    if not gate["ok"]:
        raise RuntimeError("OCEAN_PUBLICATION_GATE:" + ",".join(gate["errors"]))
    return final


def _project_delivery_text_tracked(text, proj, platform="tg", tracking_id=""):
    """System-owned last mile for Ocean links; not wrapped by the legacy copy guard."""
    from app.services.social_owner_policy_v3 import is_ocean, render_public_text
    if is_ocean(proj):
        tid = str(tracking_id or _social_utm_content(text=text))
        base = render_public_text(text, proj, platform)
        return _ocean_finalize_delivery(base, text, platform, tid)
    return _project_delivery_text(text, proj, platform)


def _project_delivery_text(text, proj, platform="tg"):
    # Canonical legacy delivery renderer. Ocean UTM publication paths call
    # _project_delivery_text_tracked after generation so the AI cannot own URLs.
    from app.services.social_owner_policy_v3 import (
        is_ocean,
        is_dushi,
        is_manicure,
        render_public_text,
    )
    if is_ocean(proj) or is_dushi(proj) or is_manicure(proj):
        return render_public_text(text, proj, platform)

    allow_tags = bool((proj or {}).get("allow_hashtags"))
    generated_tags = _extract_public_hashtags(text, limit=10) if allow_tags else []
    clean = _sanitize_public_post_text(text)
    if allow_tags and generated_tags:
        clean = _append_public_hashtags(clean, generated_tags)

    contact = (proj or {}).get("contact_tg" if platform == "tg" else "contact_vk", "")
    return _with_tags(_with_contact(clean, contact), (proj or {}).get("hashtags"))

def public_copy_runtime_boundary_health():
    """Canonical public-copy probe delegated to the immutable owner policy.

    The local legacy probe used to encode an older hashtag-free/footer-free
    contract and could falsely report production red after the owner explicitly
    changed Social delivery rules. Keep one source of truth.
    """
    from app.services.social_owner_policy_v3 import (
        public_copy_runtime_boundary_health as _canonical_boundary_health,
    )
    return _canonical_boundary_health()


def _required_delivery_platforms(proj):
    mode = str((proj or {}).get("platforms") or "both").lower()
    req = []
    if mode in ("tg", "both") and (proj or {}).get("channel_tg"):
        req.append("tg")
    if mode in ("vk", "both") and (proj or {}).get("vk_owner_id"):
        req.append("vk")
    return req


def _mark_social_delivery_published(rec, project, *, preview_text=None):
    """Canonical successful-delivery lifecycle transition.

    Call only after every required platform is confirmed delivered. This pure
    lifecycle mutation performs no provider/API call.
    """
    rec["status"] = "published"
    rec["publication_status"] = "published"
    rec["external_post_id"] = dict(rec.get("ids") or {})
    rec["published_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    rec["error"] = ""
    rec["publish_at"] = None
    if preview_text is not None:
        rec["preview"] = preview_text
    return rec


def _all_required_delivery_ok(proj, results):
    req = _required_delivery_platforms(proj)
    return bool(req) and all(isinstance((results or {}).get(k), dict) and (results or {}).get(k, {}).get("ok") for k in req)


def _merge_delivery_ids(existing_ids, proj, results):
    ids = dict(existing_ids or {})
    tg = (results or {}).get("tg") if isinstance((results or {}).get("tg"), dict) else {}
    mid = (tg.get("result") or {}).get("message_id") if isinstance(tg.get("result"), dict) else tg.get("message_id")
    if mid:
        ids["tg"] = {"chat_id": (proj or {}).get("channel_tg"), "message_id": mid}
    vk = (results or {}).get("vk") if isinstance((results or {}).get("vk"), dict) else {}
    if vk.get("post_id"):
        ids["vk"] = {"owner_id": (proj or {}).get("vk_owner_id"), "post_id": vk.get("post_id")}
    return ids


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "demo":
        _demo(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "autopost":
        autopost(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "due_posts":
        due_posts_all(dry=("dry" in sys.argv[2:]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "warn_stale":
        warn_stale_drafts(dry=("dry" in sys.argv[2:]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "autopost_all":
        autopost_all(respect_time=("time" in sys.argv[2:]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "autopost_projects":
        autopost_projects(sys.argv[2], owner=("owner" in sys.argv[3:]), respect_time=("time" in sys.argv[3:]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "style":
        _style_demo(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 4 and sys.argv[1] == "vk":
        _vk_demo(sys.argv[2], int(sys.argv[3]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "channel":
        _channel_demo(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "send":
        _send_demo(sys.argv[2], sys.argv[3])
    else:
        main()
