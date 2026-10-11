# -*- coding: utf-8 -*-
import os, sys, json
from app.services.exception_observability import observe_suppressed
from datetime import date, datetime, timedelta, timezone
from contextvars import ContextVar
from app.services.social_editorial_contract import project_local_now, parse_reference_date, planned_content_date
_SOCIAL_EDITORIAL_DATE = ContextVar("social_editorial_date", default=None)

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


def _resolve_banner_path(account_id, banner):
    from app.services.social_media_assets import resolve_banner_path
    return resolve_banner_path(account_id, banner, banner_dir=BANNER_DIR)


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
            observe_suppressed(__name__, sys.exc_info()[1], line=45)
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
            observe_suppressed(__name__, sys.exc_info()[1], line=64)
    finally:
        try:
            conn.close()
        except Exception:
            observe_suppressed(__name__, sys.exc_info()[1], line=69)


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


def _social_openai_requests_post(*args, **kwargs):
    """Social OpenAI Responses transport through the explicit international proxy.

    Global HTTP(S)_PROXY is deliberately disabled for BORIS long-lived services;
    Social must therefore opt into the same explicit proxy pool used by Images.
    Never fall back to a direct OpenAI request from a geo-blocked host.
    """
    import requests
    from proxy_pool import get_intl_requests_proxies

    proxies = get_intl_requests_proxies() or {}
    if not proxies:
        raise RuntimeError("SOCIAL_OPENAI_PROXY_UNAVAILABLE")
    session = requests.Session()
    session.trust_env = False
    session.proxies.update(proxies)
    try:
        return session.post(*args, **kwargs)
    finally:
        session.close()


def _social_secondary_ai_text(account_id, prompt, operation, intent):
    """Secondary AI only after a proven non-ambiguous primary provider failure.

    OpenAI is deliberately skipped here: the canonical Social paid contour has
    already attempted it with the same business intent. The shared router may
    use the next configured provider (for example Gemini) or local recovery.
    """
    from app.services.sales_ai_router import (
        generate_text,
        SalesAIProviderUnavailable,
        SalesAIUnavailable,
    )

    def _skip_openai(**_kwargs):
        raise SalesAIProviderUnavailable("social canonical OpenAI attempt already failed")

    try:
        out = generate_text(
            account_id=str(account_id),
            operation=str(operation) + "_secondary",
            prompt=str(prompt or ""),
            module="social",
            idempotency_key="social-secondary:" + str(intent),
            max_output_tokens=700,
            timeout=75,
            openai_call=_skip_openai,
        )
    except SalesAIUnavailable as exc:
        raise RuntimeError("SOCIAL_AI_ROUTER_UNAVAILABLE:" + str(exc)[:220]) from exc
    text_out = str((out or {}).get("text") or "").strip()
    if not text_out:
        raise RuntimeError("SOCIAL_TEXT_EMPTY")
    return text_out


def _openai_text(account_id, prompt, operation="текст постинга"):
    """Canonical Social paid contour with safe secondary provider recovery.

    The first attempt always stays on Campaign/Feed Factory's exactly-once
    _ff_guarded_openai_response boundary. A secondary provider is allowed only
    after a deterministic provider rejection/cooldown where the primary call is
    known not to have produced a paid 2xx result. Ambiguous transport outcomes
    remain fail-closed and never trigger another paid remote attempt.
    """
    import hashlib
    import os as _os_social
    import requests as _requests_social
    from app.api.campaigns import _ff_guarded_openai_response

    prompt = str(prompt or "")
    intent = "social-post-text:" + hashlib.sha256(
        (str(account_id) + "\n" + str(operation) + "\n" + prompt).encode("utf-8", "ignore")
    ).hexdigest()
    model = str(
        _os_social.getenv("BORIS_SOCIAL_OPENAI_MODEL")
        or _os_social.getenv("BORIS_SALES_OPENAI_MODEL")
        or "gpt-5.4-mini"
    )
    try:
        out = _ff_guarded_openai_response(
            account_id=str(account_id),
            operation=str(operation),
            model=model,
            input_payload=[{"role": "user", "content": prompt}],
            max_output_tokens=700,
            idempotency_key=intent,
            timeout=90,
            requests_post=_social_openai_requests_post,
        )
    except _requests_social.HTTPError as exc:
        status = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
        if status in {401, 403, 429, 500, 502, 503, 504}:
            return _social_secondary_ai_text(account_id, prompt, operation, intent)
        raise
    except RuntimeError as exc:
        reason = str(exc)
        # These states prove the primary provider did not return a usable paid
        # result. Do not include OUTCOME_UNCERTAIN here.
        if reason.startswith((
            "CAMPAIGN_AI_PROVIDER_COOLDOWN:",
            "CAMPAIGN_AI_RELEASED_RETRY_CAP:",
        )):
            return _social_secondary_ai_text(account_id, prompt, operation, intent)
        raise

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

def _holiday_in_window(days=7, reference_date=None):
    """Возвращает 'Название (дд.мм)' первого праздника в окне [сегодня; сегодня+days], иначе ''."""
    today = parse_reference_date(reference_date) if reference_date is not None else _project_clock({"tz_offset": 0}).date()
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


def _project_occasion(proj, reference_date=None):
    """Return a calendar hook only where it naturally belongs.

    Product/SMM channels must not force unrelated solemn/professional dates
    into evergreen sales copy. Their own project themes may still mention a
    relevant occasion explicitly.
    """
    name = str((proj or {}).get("name") or "").strip().lower()
    if "океан клиент" in name or "от души" in name:
        return ""
    return _holiday_in_window(7, reference_date=reference_date or _SOCIAL_EDITORIAL_DATE.get() or _project_clock(proj).date())


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


def _build_text_prompt(theme, client_prompt="", occasion="", examples=None, allow_hashtags=False, reference_date=None):
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
    parts.append("ЛОГИКА ПОСТА: выбери ОДНУ конкретную мысль и доведи её до понятного вывода. Не смешивай несколько услуг, новостей и офферов в одном тексте. Схема: понятный контекст/проблема → 1 полезная мысль или пример → естественный вывод/CTA.")
    parts.append("ФАКТЫ: опирайся только на тему, контекст бизнеса и подтверждённые примеры. Если конкретного факта нет — не додумывай. Не пиши общие фразы вроде «индивидуальный подход», «качество и надёжность», «лучшие решения», если они не раскрыты конкретикой.")
    parts.append("РАЗНООБРАЗИЕ: каждый новый пост должен отличаться углом, первой фразой, структурой и финалом от предыдущих. Не используй один и тот же шаблон, не перечисляй весь продуктовый каталог и не повторяй один и тот же CTA. Сначала полезная/живая мысль; продажа — максимум одной естественной фразой и не обязана быть в каждом посте.")
    parts.append("РАЗМЕТКА: разрешён ТОЛЬКО HTML-тег <b>жирный</b> — оберни в него заголовок первой строки и одну-две ключевые фразы по смыслу. Больше никаких тегов. Markdown запрещён полностью: никаких **, ##, __, backticks — они будут видны читателю как мусор.")
    _ref = reference_date if reference_date is not None else _SOCIAL_EDITORIAL_DATE.get()
    _t0 = parse_reference_date(_ref) if _ref is not None else _project_clock({"tz_offset": 0}).date()
    _win = ", ".join((_t0 + timedelta(days=_i)).strftime("%d.%m.%Y") for _i in range(8))
    parts.append("СЕГОДНЯ " + _t0.strftime("%d.%m.%Y") + ". Упоминать события, праздники и любые даты разрешено ТОЛЬКО из этого списка: " + _win + ".")
    parts.append("Все прочие даты и праздники под строгим запретом — не упоминай их вообще. Если в разрешённом окне праздника нет, пиши обычный пост без привязки к дате и без слов «скоро», «на днях», «приближается».")
    parts.append("НЕ выдумывай цены, телефоны, адреса и график работы. Если в инструкции стоит заглушка вроде «УКАЖИ» — не подставляй цифры и не переноси заглушку в текст.")
    parts.append("Верни только текст поста, без пояснений и кавычек.")
    return "\n".join(parts)

def _build_banner_prompt(theme, client_prompt="", title="", allowed_dates="", post_text=""):
    import re as _re
    _semantic = str(post_text or "")
    _semantic = _re.sub(r"<[^>]+>", " ", _semantic)
    _semantic = _re.sub(r"https?://\S+|@[A-Za-zА-Яа-яЁё0-9_]+|(?<!\w)#[A-Za-zА-Яа-яЁё0-9_]+", " ", _semantic, flags=_re.U)
    _semantic = " ".join(_semantic.split())[:1400]
    p = (
        "Создай ПРЕМИАЛЬНЫЙ рекламный баннер для социальных сетей, квадрат 1:1.\n\n"
        + ("ГЛАВНЫЙ ЗАГОЛОВОК крупным шрифтом, точно этот текст: «" + title + "»\n" if title else "")
        + "ТЕМА БАННЕРА: " + theme + "\n\n"
        + (
            "СМЫСЛ ГОТОВОГО ПОСТА (только для выбора визуальной сцены; НЕ переписывай весь текст на баннер):\n"
            + _semantic + "\n\n"
            "СМЫСЛОВАЯ СВЯЗЬ: заголовок, главный объект и сцена должны передавать ОДНУ и ту же конкретную мысль готового поста. "
            "Показывай узнаваемый предмет, действие, интерфейсную метафору или ситуацию именно из текста. "
            "Не подменяй конкретную тему случайным роботом, абстрактным AI, рукопожатием или универсальным офисом. "
            "Не делай коллаж из нескольких несвязанных услуг. Один баннер = одна мысль.\n\n"
            if _semantic else ""
        )
        + "СТИЛЬ: премиальная рекламная графика — насыщенные цвета, динамичная композиция, высокое качество "
        "продакшена. НЕ похоже на дешёвое стоковое фото. Один яркий акцентный цвет плюс тёмные или "
        "нейтральные тона фона, объёмный свет, глубина кадра.\n"
        "ГЛАВНЫЙ ОБЪЕКТ по теме рисуй точно и узнаваемо, не подменяй абстракцией.\n"
        "ВЁРСТКА: чистая, с безопасными отступами от краёв. Весь важный контент — в ЦЕНТРАЛЬНОЙ части кадра, "
        "верх и низ оставляй под фон. По умолчанию НЕ добавляй декоративные плашки, иконки, подписи, вопросы и CTA. "
        "Дополнительный текст разрешён только если клиентский промпт явно требует его.\n"
        "ТЕКСТ: если задан ГЛАВНЫЙ ЗАГОЛОВОК, это ЕДИНСТВЕННЫЙ текст на баннере, если клиентский промпт прямо не требует иного. "
        "Не копируй вопрос, CTA или предложения из готового поста. Заголовок на РУССКОМ языке, без орфографических ошибок, "
        "полностью помещается в кадр и НЕ обрезан краями. Никаких выдуманных слов и бессмысленных букв.\n"
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
    import re as _re_tags
    tags = [str(x).strip() for x in (tags or []) if str(x).strip()]
    value = str(text or "")
    if tags:
        value = _re_tags.sub(r"(?<!\w)#[A-Za-zА-Яа-яЁё0-9_]+", "", value, flags=_re_tags.U)
        value = _re_tags.sub(r"[ \t]+\n", "\n", value)
        value = _re_tags.sub(r"\n{3,}", "\n\n", value).strip()
        return value + "\n\n" + " ".join(tags)
    return value.strip()


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


def _manicure_banner_title(theme, text_value=""):
    """Short complete 3-5 word headline for manicure/pedicure banners."""
    base = str(theme or "").split(". Услуга:", 1)[0].strip(" .:-")
    low = base.lower().replace("ё", "е")
    if ("длин" in low and "ног" in low) or "ритм" in low:
        return "Удобная длина ногтей"
    if "педикюр" in low:
        if "осен" in low or "не только лет" in low or "закрыт" in low:
            return "Педикюр нужен и осенью"
        if "уход" in low or "стоп" in low:
            return "Уход за стопами осенью"
        return "Аккуратный педикюр каждый день"
    if "кутикул" in low or "сух" in low:
        return "Уход за сухой кутикулой"
    if "молоч" in low:
        return "Молочный маникюр без перегруза"
    if any(x in low for x in ("бордо", "шоколад", "тёмн", "оттен")):
        return "Глубокие осенние оттенки"
    if "френч" in low:
        return "Современный френч без скуки"
    if "кошачий глаз" in low:
        return "Эффект кошачьего глаза"
    if any(x in low for x in ("форма", "миндаль", "квадрат")):
        return "Форма и длина ногтей"
    if "коротк" in low:
        return "Короткие ногти каждый день"
    if "дизайн" in low:
        return "Идея маникюра на сегодня"
    return "Маникюр для реальной жизни"


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
        base=value.split(". Услуга:",1)[0].split(". Для ИЗОБРАЖЕНИЯ",1)[0].strip(" .:-")
        base_low=base.lower()
        if any(x in base_low for x in ("педикюр осенью", "не только лет", "закрыт")):
            body=(
                "<b>Педикюр не заканчивается вместе с открытой обувью</b>\n\n"
                "Осенью смысл процедуры меняется: уже не нужно подбирать покрытие под босоножки, "
                "зато аккуратные стопы и ногти остаются частью обычного ухода за собой.\n\n"
                "Если яркий цвет сейчас не нужен, можно выбрать спокойное покрытие или аккуратный вариант без него."
            )
        elif any(x in base_low for x in ("кутикул", "уход за руками", "сух")):
            body=(
                "<b>Сухая кутикула быстро портит впечатление даже от аккуратного покрытия</b>\n\n"
                "В прохладный сезон рукам чаще нужен обычный домашний уход между визитами. "
                "Лучше регулярно использовать привычное средство для увлажнения и не пытаться глубоко срезать кожу самостоятельно.\n\n"
                "Так аккуратный вид маникюра сохраняется дольше без лишних экспериментов дома."
            )
        elif any(x in base_low for x in ("бордо", "шоколад", "тёмн", "осенний оттен")):
            body=(
                "<b>Тёмный маникюр не обязан выглядеть тяжело</b>\n\n"
                "Бордо, шоколадный и другие глубокие оттенки сильно зависят от формы и длины ногтей. "
                "На короткой или средней длине спокойная форма часто делает насыщенный цвет более собранным.\n\n"
                "Если хочется выразительности без сложного дизайна, можно начать именно с оттенка."
            )
        elif any(x in base_low for x in ("коротк", "форма", "миндаль", "квадрат", "длин")):
            body=(
                "<b>Форму ногтей лучше выбирать не только по фотографии</b>\n\n"
                "Важно учитывать длину, привычную нагрузку на руки и то, насколько удобно вам с этой формой каждый день. "
                "Один и тот же дизайн на коротком мягком квадрате и на миндале будет выглядеть совершенно по-разному.\n\n"
                "Сначала определитесь с удобной формой — и уже под неё подбирайте цвет и дизайн."
            )
        elif any(x in base_low for x in ("френч", "дизайн", "акцент", "геометр", "абстрак", "кошачий глаз")):
            title=base[:72] or "Один акцент вместо перегруженного дизайна"
            body=(
                "<b>"+title[:1].upper()+title[1:]+"</b>\n\n"
                "Чтобы дизайн выглядел собранно, ему не обязательно занимать каждый ноготь. "
                "Часто достаточно одного повторяющегося элемента, пары акцентов или одной понятной палитры.\n\n"
                "Так идея остаётся заметной, но не спорит с формой и длиной ногтей."
            )
        else:
            title=base[:72] or "Маникюр лучше выбирать под реальную жизнь"
            body=(
                "<b>"+title[:1].upper()+title[1:]+"</b>\n\n"
                "Перед выбором стоит решить, что для вас сейчас важнее: практичность, спокойный цвет или заметный акцент. "
                "Когда форма, длина и оттенок отвечают одной задаче, результат выглядит гораздо цельнее.\n\n"
                "Сохраните идею и сравните её с тем, что вам удобно носить каждый день."
            )
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


def _social_payload_digest(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _social_copy_deletion_checkpoint(previous, result, *, expected_result_sha256=None):
    """CAS a single deletion; later checkpoints cannot restore stale copy/cost."""
    import math
    old = (previous or {}).get("copy_deletion") or {}
    new = (result or {}).get("copy_deletion") or {}
    if expected_result_sha256 is not None:
        if (not isinstance(previous, dict) or _social_payload_digest(previous) != expected_result_sha256
                or previous.get("final_payload_stage") != "qa_accounted"
                or old.get("attempts") != 0 or new.get("attempts") != 1
                or (new.get("original") or {}).get("text") != previous.get("text")
                or (new.get("original") or {}).get("delivery_texts") != previous.get("delivery_texts")):
            raise RuntimeError("SOCIAL_COPY_DELETION_CAS_CONFLICT")
        mutable = {"text", "delivery_texts", "copy_quality", "delivery_copy_quality", "copy_deletion", "final_payload_stage"}
        if ({k: v for k, v in previous.items() if k not in mutable}
                != {k: v for k, v in result.items() if k not in mutable}):
            raise RuntimeError("SOCIAL_COPY_DELETION_FROZEN_FIELDS_CHANGED")
    elif old.get("attempts") == 1:
        if (new.get("attempts") != 1 or previous.get("text") != result.get("text")
                or previous.get("delivery_texts") != result.get("delivery_texts")
                or any(old.get(k) != new.get(k) for k in ("version", "manifest", "original", "proposal", "image_sha256"))
                or any((result.get("delivery_qa_attempts") or {}).get(k) != v
                       for k, v in (previous.get("delivery_qa_attempts") or {}).items())):
            raise RuntimeError("SOCIAL_COPY_DELETION_STALE_CHECKPOINT")
        for key in ("banner_attempt_costs", "banner_vision_qa_costs"):
            prior_costs, fresh_costs = previous.get(key) or [], result.get(key) or []
            if (not isinstance(fresh_costs, list) or fresh_costs[:len(prior_costs)] != prior_costs
                    or any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in fresh_costs)):
                raise RuntimeError("SOCIAL_COPY_DELETION_COST_HISTORY_CHANGED")
        known, total = result.get("banner_cost_known"), result.get("banner_cost_rub")
        if known is True:
            aggregate = round(sum(result.get("banner_attempt_costs") or [])
                              + sum(result.get("banner_vision_qa_costs") or []), 6)
            if (previous.get("banner_cost_known") is not True or type(total) not in (int, float)
                    or not math.isfinite(total) or total < previous.get("banner_cost_rub", 0)
                    or total != aggregate):
                raise RuntimeError("SOCIAL_COPY_DELETION_COST_REGRESSED")
        elif (known is not False or (result.get("final_payload_stage") != "qa_reconciliation"
                                    and result.get("final_payload_reconciliation_required") is not True)
              or (total is not None and (type(total) not in (int, float) or not math.isfinite(total)
                                        or total < (previous.get("banner_cost_rub") or 0)))):
            raise RuntimeError("SOCIAL_COPY_DELETION_COST_UNVERIFIED")
        prior_native = ((previous.get("july_premium") or {}).get("social_native_v2") or {})
        fresh_native = ((result.get("july_premium") or {}).get("social_native_v2") or {})
        if prior_native:
            prior_budget = prior_native.get("native_budget") or {}
            fresh_budget = fresh_native.get("native_budget") or {}
            if (any(prior_native.get(k) != fresh_native.get(k) for k in
                    ("version", "context", "generation_key", "image_intent", "identity"))
                    or any(fresh_native.get(k, 0) < prior_native.get(k, 0) for k in
                           ("native_total_rub", "social_total_rub"))
                    or any((fresh_native.get(journal) or {}).get(k) != v
                           for journal in ("native_receipts", "social_receipts")
                           for k, v in (prior_native.get(journal) or {}).items())
                    or not set(prior_budget.get("request_ids") or []).issubset(set(fresh_budget.get("request_ids") or []))
                    or (prior_budget.get("known") is True and fresh_budget.get("known") is not True)):
                raise RuntimeError("SOCIAL_COPY_DELETION_NATIVE_HISTORY_CHANGED")


def _social_final_payload_state(account_id, context, action, result=None, *, expected_result_sha256=None):
    """Durable single producer for NEW opted-in plans; no expired-lease rebuy.

    A process lost during paid work leaves a running claim for reconciliation.
    A completed result can be recovered unchanged before post materialization.
    Never backfill this marker onto an existing plan or historical paid intent.
    """
    import copy
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == str(account_id),
            Storage.key == context["storage_key"]).with_for_update().first()
        if row is None:
            raise RuntimeError("SOCIAL_FINAL_PAYLOAD_PLAN_MISSING")
        plan = json.loads(row.value)
        if not isinstance(plan, dict) or plan.get("final_payload_qa") != context:
            raise RuntimeError("SOCIAL_FINAL_PAYLOAD_IDENTITY_MISMATCH")
        # The protected owner plan wrapper may rewrite a plan snapshot. Keep
        # the paid claim in a separate row so a stale wrapper snapshot cannot
        # erase a running claim, paid checkpoint or completed result.
        state_key = "social_final_payload_generation:" + context["record_id"]
        state_row = db.query(Storage).filter(Storage.account_id == str(account_id),
            Storage.key == state_key).with_for_update().first()
        state = json.loads(state_row.value) if state_row is not None else None
        if state is not None and (not isinstance(state, dict) or state.get("context") != context):
            raise RuntimeError("SOCIAL_FINAL_PAYLOAD_CLAIM_IDENTITY_MISMATCH")
        if action in {"claim", "recover"}:
            if isinstance(state, dict) and state.get("state") == "complete" and isinstance(state.get("result"), dict):
                if state["result"].get("final_payload_qa") != context:
                    raise RuntimeError("SOCIAL_FINAL_PAYLOAD_RESULT_IDENTITY_MISMATCH")
                return copy.deepcopy(state["result"])
            if state is not None:
                raise RuntimeError("SOCIAL_FINAL_PAYLOAD_RECONCILIATION_REQUIRED")
            if action == "recover":
                raise RuntimeError("SOCIAL_FINAL_PAYLOAD_PAST_CLAIM_FORBIDDEN")
            saved = {"context": context, "state": "running",
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        elif action in {"checkpoint", "complete", "copy_delete"}:
            if not isinstance(state, dict) or state.get("state") != "running":
                raise RuntimeError("SOCIAL_FINAL_PAYLOAD_CLAIM_MISSING")
            if not isinstance(result, dict) or result.get("final_payload_qa") != context:
                raise RuntimeError("SOCIAL_FINAL_PAYLOAD_RESULT_IDENTITY_MISMATCH")
            if action == "copy_delete" and not expected_result_sha256:
                raise RuntimeError("SOCIAL_COPY_DELETION_CAS_REQUIRED")
            _social_copy_deletion_checkpoint(state.get("result"), result,
                expected_result_sha256=expected_result_sha256 if action == "copy_delete" else None)
            saved = {**state,
                "state": "complete" if action == "complete" else "running",
                "result": copy.deepcopy(result),
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        else:
            raise ValueError("SOCIAL_FINAL_PAYLOAD_STATE_ACTION_INVALID")
        payload = json.dumps(saved, ensure_ascii=False)
        if state_row is None:
            db.add(Storage(account_id=str(account_id), key=state_key, value=payload))
        else:
            state_row.value = payload
        db.commit()
        return None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _social_generate_final_payload(account_id, theme, options):
    """Own one new generation before any paid call, retaining paid failures."""
    import copy
    import re
    context = copy.deepcopy(options.get("final_payload_context"))
    project = options.get("project") or {}
    today = _project_clock(project).date()
    permitted_days = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    if (not isinstance(context, dict)
            or context.get("version") != "SOCIAL_FINAL_PAYLOAD_QA_V1"
            or str(account_id) != "u2" or context.get("account_id") != str(account_id)
            or str(project.get("id")) not in {"a374ced25745", "f938caa3a944", "270dbf472f7f"}
            or context.get("project_id") != str(project.get("id"))
            or not re.fullmatch(r"[0-9a-f]{12}", str(context.get("record_id") or ""))
            or (project.get("mode") or "auto") != "auto"
            or context.get("local_date") not in permitted_days):
        raise RuntimeError("SOCIAL_FINAL_PAYLOAD_CONTEXT_INVALID")
    slot = re.fullmatch(r"social-slot:([^:]+):([^:]+):(\d{8})_([01]\d|2[0-3])",
                        str(context.get("generation_key") or ""))
    if (slot is None or slot.group(1) != str(account_id)
            or slot.group(2) != str(project.get("id"))
            or slot.group(3) != context["local_date"].replace("-", "")
            or context.get("storage_key") != "posting_generation_plan:" + slot.group(2) + ":" + slot.group(3) + "_" + slot.group(4)):
        raise RuntimeError("SOCIAL_FINAL_PAYLOAD_CONTEXT_INVALID")
    # Cross-midnight compensation may materialize the exact already-completed
    # result. It never starts a new past-day claim or repeats uncertain work.
    action = "claim" if context["local_date"] == today.isoformat() else "recover"
    recovered = _social_final_payload_state(account_id, context, action)
    if recovered is not None:
        return recovered
    result = {"account_id": account_id, "theme": theme, "final_payload_qa": context,
              "generation_key": context["generation_key"], "final_payload_stage": "copy"}
    try:
        result = generate_post(account_id, theme, **options, _final_payload_result=result)
    except Exception as exc:
        if str(exc).startswith(("SOCIAL_COPY_DELETION_CAS_", "SOCIAL_COPY_DELETION_STALE_", "SOCIAL_COPY_DELETION_FROZEN_",
                                "SOCIAL_COPY_DELETION_COST_", "SOCIAL_COPY_DELETION_NATIVE_HISTORY_")):
            # A stale producer cannot replace a newer durable result with its
            # local snapshot, even while reporting a blocked generation.
            raise
        # The result object is shared with image/QA accounting. A caught error
        # cannot erase already-received costs, verdicts or request identifiers.
        result.update(banner=None, quality_status="banner_blocked",
                      banner_error="FINAL_PAYLOAD_GENERATION_BLOCKED:" + type(exc).__name__ + ":" + str(exc)[:180])
        if str(result.get("final_payload_stage") or "").endswith("_request"):
            result["banner_cost_known"] = False
            result["final_payload_reconciliation_required"] = True
    _social_final_payload_state(account_id, context, "complete", result)
    return result


def _social_prepare_final_payload(result, project):
    """Freeze every connected native caption before buying its image."""
    context = result["final_payload_qa"]
    tracking = _ocean_tracking_record(result.get("text", ""), project, context["record_id"])
    result.update(tracking)
    platforms = _required_delivery_platforms(project)
    if not platforms:
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:no_connected_delivery_channel")
    result["delivery_texts"] = {platform: _project_delivery_text_tracked(
        result.get("text", ""), project, platform,
        tracking_id=str(tracking.get("utm_content") or ""), final_payload=True,
    ) for platform in platforms}
    from app.services import social_quality as sq
    recent = sq.recent_project_posts(str(result["account_id"]), str(project.get("id") or ""))
    checks = {p: sq.evaluate_copy(t, result.get("content_brief") or {}, project, recent_posts=recent)
              for p, t in result["delivery_texts"].items()}
    result["delivery_copy_quality"] = checks
    if any(check.get("pass") is not True for check in checks.values()):
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:final_public_copy")
    from app.services.social_editorial_contract import public_text_for_qa
    # Carry the approved body's actual objects, colours, shapes and materials
    # into image generation, not just into the later rejecting inspector.
    result["visual_brief"]["post_visual_source_text"] = public_text_for_qa(result.get("text", "")).strip()
    # Enrollment happens once inside a NEW running claim, before any image or
    # QA charge. An existing paid/historical result never receives this marker.
    if (not result.get("banner") and not result.get("banner_cost_rub")
            and not result.get("banner_attempts") and not result.get("premium_attempts")
            and not result.get("delivery_qa_attempts") and not result.get("copy_deletion")):
        manifest = sq.copy_deletion_manifest(result.get("text", ""), result["delivery_texts"],
                                             result.get("content_brief") or {}, result["visual_brief"])
        if manifest:
            result["copy_deletion"] = {"version": sq.COPY_DELETION_VERSION, "attempts": 0,
                                       "state": "eligible", "manifest": manifest}
    _social_final_payload_state(result["account_id"], context, "checkpoint", result)


def _social_generation_final_qa_once(account_id, result, project, *, path, visual_brief):
    """The delivery gate is also the FIRST inspector, with its one cost ledger."""
    import copy
    result["visual_brief"] = copy.deepcopy(visual_brief)
    result["final_payload_stage"] = "qa_request"
    _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)
    record = {**copy.deepcopy(result), "id": result["final_payload_qa"]["record_id"],
              "project_id": str(project.get("id") or ""), "ids": {},
              "delivery_protocol": "SOCIAL_RECEIPT_V1"}
    gate = _social_delivery_quality_gate(account_id, record, project, path,
                                       public_texts=result["delivery_texts"])
    result.update(gate.get("record_updates") or {})
    verdict = gate.get("qa_result")
    if not isinstance(verdict, dict) and gate.get("pass") is True:
        proofs = (gate.get("delivery_quality") or {}).values()
        verdict = next((p.get("verdict") for p in proofs if isinstance(p, dict) and isinstance(p.get("verdict"), dict)), None)
    verdict = copy.deepcopy(verdict) if isinstance(verdict, dict) else {"pass": False,
        "issues": list(gate.get("blockers") or ["FINAL_PAYLOAD_QA_NOT_REACHED"]),
        "image_repair_allowed": False, "repair_scope": "qa"}
    if gate.get("pass") is not True:
        verdict["pass"] = False
        verdict["issues"] = list(dict.fromkeys(list(verdict.get("issues") or []) + list(gate.get("blockers") or [])))
    result["vision_qa"] = verdict
    result["final_payload_stage"] = "qa_accounted" if result.get("banner_cost_known") is True else "qa_reconciliation"
    _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)
    return verdict


def _social_remove_copy_units(value, spans):
    """Delete exact complete paragraphs and their following separator only."""
    import hashlib
    cuts = []
    for span in spans:
        start, end = span["start"], span["end"]
        if (type(start) is not int or type(end) is not int or not 0 < start < end <= len(value)
                or hashlib.sha256(value[start:end].encode("utf-8")).hexdigest() != span["unit_sha256"]):
            raise RuntimeError("SOCIAL_COPY_DELETION_SPAN_CHANGED")
        while value[end:end + 2] == "\n\n":
            end += 2
        cuts.append((start, end))
    cuts.sort()
    if any(left[1] > right[0] for left, right in zip(cuts, cuts[1:])):
        raise RuntimeError("SOCIAL_COPY_DELETION_SPAN_OVERLAP")
    for start, end in reversed(cuts):
        value = value[:start] + value[end:]
    return value


def _social_generation_final_qa(account_id, result, project, *, path, visual_brief):
    """At most one copy-only deletion plus actual QA of the SAME paid image.

    All paid results are checkpointed before a repair decision. Neither old
    failures, image defects, native rejections nor unknown cost enter this path.
    """
    import copy
    import hashlib
    import math
    from pathlib import Path
    from app.services import social_quality as sq
    state = result.get("copy_deletion") or {}
    if (state.get("version") == sq.COPY_DELETION_VERSION and state.get("attempts") == 0
            and not state.get("image_sha256") and not result.get("delivery_qa_attempts")):
        state["image_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    first = _social_generation_final_qa_once(account_id, result, project, path=path, visual_brief=visual_brief)
    if first.get("pass") is True or state.get("version") != sq.COPY_DELETION_VERSION or state.get("attempts") != 0:
        return first

    def stop(reason):
        state.update(state="not_repaired", blocked_reason=reason)
        _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)
        return first

    context = result["final_payload_qa"]
    required = sq.vision_required_flags(project, result.get("content_brief") or {}, visual_brief)
    copy_flags = {"copy_season_ok", "occasion_ok"}
    attempts = result.get("delivery_qa_attempts") or {}
    if (first.get("pass") is not False or len(attempts) != 1
            or any(type(first.get(k)) is not bool for k in ("pass", *required))
            or any(first.get(k) is not True for k in required if k not in copy_flags)
            or not any(first.get(k) is False for k in copy_flags)
            or first.get("repair_scope") != "copy" or first.get("image_repair_allowed") is not False
            or first.get("qa_schema_repair_required") is True or first.get("cost_guard_ok") is not True
            or result.get("banner_cost_known") is not True or result.get("ids")
            or result.get("delivery_receipts") or result.get("delivery_quality")):
        return stop("COPY_DELETION_NOT_A_FIRST_COPY_ONLY_FAILURE")
    if (_project_clock(project).date().isoformat() != context.get("local_date")
            or first.get("context_date") != context.get("local_date")):
        return stop("COPY_DELETION_DAY_CHANGED")
    image_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    qa_context = sq.copy_deletion_qa_context(result, image_sha)
    if (qa_context is None or first.get("qa_input_contract") != sq.final_payload_qa_contract(project, qa_context)
            or not re.fullmatch(r"[0-9a-f]{64}", str(first.get("qa_input_sha256") or ""))):
        return stop("COPY_DELETION_QA_CONTRACT_UNVERIFIED")
    proposal, problem = sq.normalize_copy_deletion_proposal(first.get("copy_deletion_proposal"), qa_context)
    if not proposal:
        return stop(problem or first.get("copy_deletion_proposal_error") or "COPY_DELETION_NO_PROPOSAL")
    if any(first.get("occasion_ok" if unit["kind"] == "date" else "copy_season_ok") is not False for unit in proposal["units"]):
        return stop("COPY_DELETION_FAILURE_KIND_MISMATCH")
    attempt = next(iter(attempts.values()))
    evidence = attempt.get("cost_evidence") or {}
    if (evidence.get("account_id") != str(account_id) or evidence.get("operation") != "social_banner_vision_qa_v1"
            or not evidence.get("request_id") or attempt.get("accounted_cost_rub") != attempt.get("cost_rub")
            or attempt.get("cost_rub") != first.get("qa_cost_rub")
            or (attempt.get("verdict") or {}).get("qa_input_sha256") != first.get("qa_input_sha256")
            or not first.get("qa_input_sha256")):
        return stop("COPY_DELETION_RECEIPT_UNVERIFIED")
    selected = {unit["unit_sha256"] for unit in proposal["units"]}
    manifest = state["manifest"]
    units = [unit for unit in manifest["units"] if unit["unit_sha256"] in selected]
    if len(units) != len(selected):
        return stop("COPY_DELETION_UNITS_CHANGED")
    edited = _social_remove_copy_units(result["text"], units)
    deleted_size = len(result["text"]) - len(edited)
    if (deleted_size > manifest["body_size"] * .25 or manifest["body_size"] - deleted_size < 120):
        return stop("COPY_DELETION_TOO_MUCH_COPY")
    captions = {platform: _social_remove_copy_units(caption, [
        {**unit["caption_spans"][platform], "unit_sha256": unit["unit_sha256"]} for unit in units
    ]) for platform, caption in result["delivery_texts"].items()}
    tracking = _ocean_tracking_record(edited, project, context["record_id"])
    before_tracking = _ocean_tracking_record(result["text"], project, context["record_id"])
    if tracking != before_tracking:
        return stop("COPY_DELETION_TRACKING_OR_DIRECTION_CHANGED")
    rendered = {platform: _project_delivery_text_tracked(edited, project, platform,
        tracking_id=str(before_tracking.get("utm_content") or ""), final_payload=True) for platform in captions}
    if rendered != captions:
        return stop("COPY_DELETION_PUBLIC_POLICY_CHANGED")
    recent = sq.recent_project_posts(str(account_id), str(project.get("id") or ""))
    checked = {p: sq.evaluate_copy(t, result["content_brief"], project, recent_posts=recent) for p, t in captions.items()}
    body_checked = sq.evaluate_copy(edited, result["content_brief"], project, recent_posts=recent)
    if body_checked.get("pass") is not True or any(c.get("pass") is not True for c in checked.values()):
        return stop("COPY_DELETION_COPY_STILL_BLOCKED")
    reference = sq.vision_reference_context(project, result["content_brief"], visual_brief)
    if (reference.get("required") and (reference.get("ok") is not True
            or any(reference.get(k) != (first.get("reference") or {}).get(k) for k in ("niche", "version", "sha256")))):
        return stop("COPY_DELETION_REFERENCE_CHANGED")
    from app.services.media_semantic_validator import _vision_estimate_kopeks
    reservation = float(_vision_estimate_kopeks()) / 100.0
    total = result.get("banner_cost_rub")
    if (type(total) not in (int, float) or not math.isfinite(total) or total <= 0
            or not math.isfinite(reservation) or reservation <= 0
            or total + reservation > float(sq.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB)):
        return stop("COPY_DELETION_REMAINING_BUDGET_BLOCKED")
    if _project_clock(project).date().isoformat() != context.get("local_date"):
        return stop("COPY_DELETION_DAY_CHANGED")
    expected = _social_payload_digest(result)
    corrected = copy.deepcopy(result)
    corrected.update(text=edited, delivery_texts=captions, copy_quality=body_checked,
                     delivery_copy_quality=checked, final_payload_stage="copy_deleted")
    corrected["copy_deletion"].update(attempts=1, state="qa_pending", proposal=proposal,
        original={"text": result["text"], "delivery_texts": copy.deepcopy(result["delivery_texts"]),
                  "manifest": copy.deepcopy(manifest), "failed_verdict": copy.deepcopy(first),
                  "delivery_qa_attempts": copy.deepcopy(attempts), "banner_cost_rub": total,
                  "claim_result_sha256": expected},
        manifest={"mode": "verify_only", "body_sha256": hashlib.sha256(edited.encode("utf-8")).hexdigest(),
                  "public_texts_sha256": sq._copy_deletion_digest(captions), "units": []},
        reserved_qa_rub=reservation, deleted_characters=deleted_size)
    _social_final_payload_state(account_id, context, "copy_delete", corrected, expected_result_sha256=expected)
    # Native orchestration holds references to its own nested ledger/state.
    # Replace only copy fields; replacing the entire deep-copied result would
    # detach the native412 state object and lose later accounting metadata.
    for key in ("text", "delivery_texts", "copy_quality", "delivery_copy_quality", "copy_deletion", "final_payload_stage"):
        result[key] = corrected[key]
    second = _social_generation_final_qa_once(account_id, result, project, path=path, visual_brief=visual_brief)
    if _project_clock(project).date().isoformat() != context.get("local_date"):
        second = {**second, "pass": False, "issues": list(second.get("issues") or []) + ["COPY_DELETION_DAY_CHANGED"]}
    if second.get("pass") is not True:
        second = {**second, "image_repair_allowed": False, "repair_scope": "qa", "copy_deletion_exhausted": True}
    result["vision_qa"] = second
    result["copy_deletion"].update(state="passed" if second.get("pass") is True else "failed",
                                   final_cost_rub=result.get("banner_cost_rub"))
    _social_final_payload_state(account_id, context, "checkpoint", result)
    return second


def generate_post(account_id, theme, text_prompt="", banner_prompt="", occasion="", channel="", image_source="ai", project_id="", text_prompt_snapshot="", allow_hashtags=False, project=None, content_brief=None, reference_date=None, occasion_context=None, final_payload_context=None, _final_payload_result=None):
    if final_payload_context is not None and _final_payload_result is None:
        options = {k: v for k, v in locals().items() if k not in {"account_id", "theme", "_final_payload_result"}}
        return _social_generate_final_payload(account_id, theme, options)
    result = _final_payload_result if _final_payload_result is not None else {"account_id": account_id, "theme": theme}
    _project = dict(project or {})
    _quality_enabled = bool(_project)
    _recent_quality_posts = []
    _SQ = None
    if _quality_enabled:
        from app.services import social_quality as _SQ
        _recent_quality_posts = _SQ.recent_project_posts(account_id, project_id, limit=60)
        _content_brief = dict(content_brief or _SQ.build_content_brief(
            account_id, _project, theme, recent_posts=_recent_quality_posts,
            reference_date=reference_date, occasion=occasion_context,
        ))
        if not isinstance(_content_brief.get("occasion"), dict) or _content_brief["occasion"].get("kind") == "evergreen":
            _content_brief["occasion"] = dict(occasion_context or _project_occasion_context(
                _project, theme, occasion, _content_brief.get("current_date") or _project_clock(_project).date()))
        result["content_brief"] = _content_brief
        result["quality_policy"] = _SQ.POLICY_VERSION
        if final_payload_context is not None:
            _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)
    else:
        _content_brief = {}
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
        or _build_text_prompt(theme, text_prompt, occasion, examples, allow_hashtags=allow_hashtags,
                              reference_date=_content_brief.get("current_date") or reference_date or _project_clock(_project).date())
    )
    if _quality_enabled:
        _final_text_prompt += _SQ.content_brief_prompt(_content_brief)
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
            _recoverable=_reason.startswith((
                "CAMPAIGN_AI_PROVIDER_COOLDOWN:",
                "CAMPAIGN_AI_RELEASED_RETRY_CAP:",
                "SOCIAL_AI_ROUTER_UNAVAILABLE:",
            ))
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

    def _prepare_public_text(_raw):
        _tags = _extract_public_hashtags(_raw, limit=10) if allow_hashtags else []
        _value = _re.sub(r"\*\*(.+?)\*\*", r"\1", str(_raw or ""), flags=_re.S)
        _value = _re.sub(r"(?m)^#{1,6}\s+", "", _value)
        _value = _re.sub(r"(?<!\w)#[A-Za-zА-Яа-яЁё0-9_]+", "", _value, flags=_re.U)
        _value = _value.replace("__", "")
        _value = _airify(_value)
        _value = _sanitize_public_post_text(_value)
        if allow_hashtags and _tags:
            _value = _append_public_hashtags(_value, _tags)
        return _value, list(_tags)

    _txt, _generated_hashtags = _prepare_public_text(_txt)

    if _quality_enabled:
        _verdict = _SQ.evaluate_copy(
            _txt, _content_brief, _project, recent_posts=_recent_quality_posts
        )
        _copy_attempts = 1
        # SOCIAL_QUALITY_BEATS_SCHEDULE: a deterministic emergency copy is never
        # followed by another paid text request. It either passes the same gate
        # or the slot is blocked instead of publishing weak/repetitive copy.
        _can_repair = result.get("text_engine") not in {
            "deterministic_uncertain_fallback",
            "deterministic_provider_recovery",
        }
        if (not _verdict.get("pass")) and _can_repair:
            for _repair_no in range(1, int(_SQ.COPY_REGEN_MAX) + 1):
                _repair_prompt = _SQ.copy_repair_prompt(
                    _final_text_prompt, _txt, _verdict, _content_brief, _repair_no
                )
                _candidate = _openai_text(
                    account_id,
                    _repair_prompt,
                    operation="social_copy_quality_repair_%s" % _repair_no,
                )
                _copy_attempts += 1
                _candidate, _candidate_tags = _prepare_public_text(_candidate)
                _candidate_verdict = _SQ.evaluate_copy(
                    _candidate, _content_brief, _project,
                    recent_posts=_recent_quality_posts,
                )
                _txt = _candidate
                _generated_hashtags = _candidate_tags
                _verdict = _candidate_verdict
                if _verdict.get("pass"):
                    break
        result["copy_attempts"] = _copy_attempts
        result["copy_quality"] = _verdict
        if not _verdict.get("pass"):
            _problems = list(_verdict.get("blockers") or []) + list(_verdict.get("issues") or [])
            result["quality_status"] = "copy_blocked"
            raise RuntimeError(
                "SOCIAL_COPY_QUALITY_BLOCKED:" + ",".join(str(x) for x in _problems[:8])
            )
        result["quality_status"] = "copy_pass"

    result["generated_hashtags"] = list(_generated_hashtags)
    result["text"] = _txt
    from app.services.social_owner_policy_v3 import is_ocean as _is_ocean_copy
    if _is_ocean_copy(_project) and final_payload_context is None:
        # All connected variants must fit before any paid banner generation.
        # Same fixed-width tracking id as the eventual durable record id.
        for _copy_platform in _required_delivery_platforms(_project):
            _project_delivery_text_tracked(_txt, _project, _copy_platform)
    if _quality_enabled:
        _visual_brief = _SQ.build_visual_brief(_content_brief, _txt, _project)
        _headline_gate = _SQ.headline_before_image(_visual_brief, _txt)
        if _headline_gate.get("pass") is not True:
            raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:" + ",".join(_headline_gate.get("issues") or []))
        result["visual_brief"] = _visual_brief
    else:
        _visual_brief = {}
    if final_payload_context is not None:
        _social_prepare_final_payload(result, _project)
    source = str(image_source or "ai").lower()
    if (
        not _quality_enabled
        and str(project_id or "") == "688556520514"
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
        if _quality_enabled:
            # QUALITY_FIRST: never attach an unrelated historical creative to a
            # new public idea. Text-provider ambiguity does not authorize another
            # text call, but a separate content-addressed image intent is safe.
            result["banner_recovery_mode"] = "fresh_semantic_ai"
        else:
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
        if _quality_enabled:
            result["banner_recovery_mode"] = "fresh_semantic_ai"
        elif str(project_id or "") != "270dbf472f7f":
            try:
                recovery=_social_reuse_published_banner(account_id,project_id,theme); result["banner"]=recovery["path"]; result["banner_engine"]="reuse_published_project_asset"; result["banner_model"]="none"; result["banner_recovery_source_record_id"]=recovery["source_record_id"]
            except Exception as exc:
                result["banner"]=None; result["banner_error"]="SOCIAL_RECOVERY_BANNER_FAILED: "+str(exc)[:180]
            return result
        else:
            result["banner_recovery_mode"]="fresh_semantic_ai"

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
    if _quality_enabled:
        return _generate_social_quality_banner(
            account_id=account_id,
            project_id=project_id,
            theme=theme,
            banner_prompt=banner_prompt,
            result=result,
            project=_project,
            content_brief=_content_brief,
            visual_brief=_visual_brief,
            quality_module=_SQ,
        )
    try:
        # Authoritative social banner path: tuned OpenAI gpt-image-2 prompt.
        # GigaChat is not used here. If OpenAI image generation fails, keep
        # fail-closed behavior: no banner -> no social publication.
        os.makedirs(BANNER_DIR, exist_ok=True)
        _title = _make_title(account_id, result.get("text", "") or theme)
        if str(project_id or "") == "270dbf472f7f":
            _title = _manicure_banner_title(theme, result.get("text", ""))
        _allowed_dates = ", ".join(
            (parse_reference_date(_content_brief.get("current_date") or reference_date or _project_clock(_project).date()) + timedelta(days=_i)).strftime("%d.%m.%Y")
            for _i in range(8)
        )
        _prompt = _build_banner_prompt(
            theme,
            banner_prompt,
            _title,
            _allowed_dates,
            post_text=result.get("text", ""),
        )
        import hashlib as _hashlib
        _image_intent = _hashlib.sha256((
            str(account_id) + "\n" + _prompt + "\n" +
            "gpt-image-2|1024x1024|low|banner_social_post_v2"
        ).encode("utf-8", "ignore")).hexdigest()
        fpath = os.path.join(BANNER_DIR, f"{account_id}_social_{_image_intent}.png")
        from app.db.session import SessionLocal as _SessionLocal
        from app.services import image_service as _IMG
        _db=_SessionLocal()
        try:
            _out=_IMG.generate_banner_financially_guarded(
                _db, account_id, _prompt, fpath, size="1024x1024", quality="low",
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
            f"[banner_openai] ready: {fpath} bytes={_os.path.getsize(fpath)} model=gpt-image-2 quality=low semantic_link=v2",
            flush=True,
        )
    except Exception as e:
        result["banner"] = None
        result["banner_error"] = "OPENAI_BANNER_FAILED: " + str(e)[:180]
        print("[banner_openai] failed: " + str(e)[:180], flush=True)
    return result







def _generate_social_quality_banner(
    *,
    account_id,
    project_id,
    theme,
    banner_prompt,
    result,
    project,
    content_brief,
    visual_brief,
    quality_module,
):
    """Generate at most two semantically verified social banners.

    All image and actual Vision QA attempts share the owner's 4 RUB ceiling.
    Provider ambiguity and missing actual cost stop the intent for reconciliation.
    """
    import hashlib as _hashlib
    import math as _math
    import os as _os
    from app.db.session import SessionLocal as _SessionLocal
    from app.services import image_service as _IMG
    from app.services.media_semantic_validator import _vision_estimate_kopeks
    from app.services.social_image_costs import image_provenance, reconcile_image_cost

    _SQ = quality_module
    _final_payload = isinstance(result.get("final_payload_qa"), dict) and result["final_payload_qa"].get("version") == "SOCIAL_FINAL_PAYLOAD_QA_V1"
    from app.services.social_owner_policy_v3 import is_manicure
    if is_manicure(project):
        from app.services.social_premium_banner import generate_premium_social_banner
        return generate_premium_social_banner(
            account_id=account_id, project_id=project_id, theme=theme,
            result=result, project=project, content_brief=content_brief,
            visual_brief=visual_brief, quality_module=_SQ, banner_dir=BANNER_DIR,
            **({"final_qa_callback": lambda **kwargs: _social_generation_final_qa(account_id, result, project, **kwargs),
                "final_payload_checkpoint": lambda: _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)} if _final_payload else {}),
        )
    _title = str((visual_brief or {}).get("headline") or "").strip()
    if not _title:
        _title = _make_title(account_id, result.get("text", "") or theme)
    if not _title or len(_title) > 80:
        result.update(banner=None, quality_status="banner_blocked", banner_error="BANNER_COMPLETE_HEADLINE_REQUIRED")
        return result
    from app.services.social_editorial_contract import project_local_now
    _reference_date = date.fromisoformat(str(content_brief.get("current_date"))) if content_brief.get("current_date") else project_local_now(project).date()
    _allowed_dates = ", ".join(
        (_reference_date + timedelta(days=_i)).strftime("%d.%m.%Y")
        for _i in range(8)
    )
    _client_banner_prompt = (
        str(banner_prompt or "")
        + _SQ.visual_brief_prompt(visual_brief or {})
    )
    _attempt_costs = []
    _qa_costs = []
    _cost_known = True
    _total_banner_cost = 0.0
    _last_vision = None
    _last_error = ""
    _accepted_path = None
    _max_attempts = int(_SQ.BANNER_REGEN_MAX)
    _qa_reservation = float(_vision_estimate_kopeks()) / 100.0
    if not _math.isfinite(_qa_reservation) or _qa_reservation <= 0:
        raise ValueError("SOCIAL_QA_RESERVATION_INVALID")

    for _banner_attempt in range(1, _max_attempts + 1):
        _predicted_image = float(_IMG.estimate_kopeks(1, model=_IMG.PREMIUM_MODEL, quality="low", with_reference=False)) / 100.0
        if not _math.isfinite(_predicted_image) or _total_banner_cost + max(_predicted_image, 1.5) + _qa_reservation > float(_SQ.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB):
            _last_error = "BANNER_COST_GUARD_BLOCKED"
            break
        _repair_suffix = ""
        if _banner_attempt > 1 and isinstance(_last_vision, dict):
            _repair_suffix = (
                "\n\nVISION_QA_REPAIR: предыдущий вариант отклонён. "
                "Исправь именно эти проблемы, сохранив тот же смысл: "
                + ", ".join(
                    str(x) for x in (_last_vision.get("issues") or [])[:8]
                )
                + ". Не повторяй предыдущую композицию."
            )
        _prompt = _build_banner_prompt(
            theme,
            _client_banner_prompt + _repair_suffix,
            _title,
            _allowed_dates,
            post_text=result.get("text", ""),
        )
        _image_intent = _hashlib.sha256((
            str(account_id)
            + "\n" + _prompt
            + "\n"
            + "gpt-image-2|1024x1024|low|banner_social_post_v3_quality"
        ).encode("utf-8", "ignore")).hexdigest()
        _path = _os.path.join(
            BANNER_DIR, f"{account_id}_social_{_image_intent}.png"
        )
        if _final_payload:
            result.update(banner_cost_rub=_total_banner_cost, banner_cost_known=_cost_known,
                          banner_attempt_costs=list(_attempt_costs), banner_vision_qa_costs=list(_qa_costs),
                          banner_attempts=len(_attempt_costs), final_payload_stage="image_request")
            _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)
        _db = _SessionLocal()
        try:
            _out = _IMG.generate_banner_financially_guarded(
                _db,
                account_id,
                _prompt,
                _path,
                size="1024x1024",
                quality="low",
                model=_IMG.PREMIUM_MODEL,
                operation="banner_social_post",
                provenance=image_provenance(account_id, project_id, _path, _image_intent),
            )
        finally:
            _db.close()

        if _out.get("ok") is True and _os.path.isfile(_path):
            try:
                _cost_receipt = reconcile_image_cost(
                    account_id=account_id, project_id=project_id, path=_path,
                    intent=_image_intent, response=_out,
                )
                _out["cost_rub"] = _cost_receipt["cost_rub"]
                _out["request_id"] = _cost_receipt["request_id"]
            except (ValueError, TypeError, OSError) as _cost_error:
                _cost_known = False
                _last_error = "BANNER_COST_RECONCILIATION_REQUIRED:" + type(_cost_error).__name__
                break
        try:
            _attempt_cost = float(_out.get("cost_rub"))
            _cost_known = not isinstance(_out.get("cost_rub"), bool) and _math.isfinite(_attempt_cost) and _attempt_cost > 0
        except (TypeError, ValueError):
            _cost_known = False
        if not _cost_known:
            _last_error = str(_out.get("error") or "BANNER_COST_RECONCILIATION_REQUIRED")[:180]
            break
        _attempt_costs.append(round(_attempt_cost, 4))
        _total_banner_cost = round(
            _total_banner_cost + _attempt_cost, 4
        )
        if _final_payload:
            result.update(banner_cost_rub=_total_banner_cost, banner_cost_known=_cost_known,
                          banner_attempt_costs=list(_attempt_costs), banner_vision_qa_costs=list(_qa_costs),
                          banner_attempts=len(_attempt_costs), final_payload_stage="image_accounted")
            result.setdefault("banner_attempt_artifacts", []).append({
                "path": _path, "intent": _image_intent, "request_id": _out.get("request_id"),
                "cost_rub": _attempt_cost, "ok": _out.get("ok") is True,
            })
            _social_final_payload_state(account_id, result["final_payload_qa"], "checkpoint", result)
        if not _out.get("ok") or not (_os.path.exists(_path) and _os.path.getsize(_path) > 0):
            _last_error = str(_out.get("error") or "OPENAI_GPT_IMAGE_2_BANNER_MISSING")[:180]
            break
        if _total_banner_cost + _qa_reservation > float(
            _SQ.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB
        ):
            _last_error = "BANNER_COST_GUARD_BLOCKED"
            _last_vision = {
                "policy_version": _SQ.POLICY_VERSION,
                "pass": False,
                "issues": ["accepted_banner_total_cost_cap_exceeded"],
            }
            break

        if _final_payload:
            _last_vision = _social_generation_final_qa(account_id, result, project, path=_path, visual_brief=visual_brief)
            _cost_known = result.get("banner_cost_known") is True
            _total_banner_cost = float(result.get("banner_cost_rub") or _total_banner_cost)
            _qa_costs = list(result.get("banner_vision_qa_costs") or [])
        else:
            _last_vision = _SQ.validate_banner_vision(
                account_id, _path, content_brief, visual_brief, result.get("text", ""), project,
            )
            try:
                _qa_cost = float(_last_vision.get("qa_cost_rub"))
                _cost_known = not isinstance(_last_vision.get("qa_cost_rub"), bool) and _math.isfinite(_qa_cost) and _qa_cost > 0
            except (TypeError, ValueError):
                _cost_known = False
            if _cost_known:
                _qa_costs.append(round(_qa_cost, 4))
                _total_banner_cost = round(_total_banner_cost + _qa_cost, 4)
        if not _cost_known:
            _last_error = "BANNER_QA_COST_RECONCILIATION_REQUIRED"
            break
        if _total_banner_cost > float(_SQ.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB):
            _last_error = "BANNER_COST_GUARD_BLOCKED"
            _last_vision = dict(_last_vision, **{"pass": False})
            _last_vision["issues"] = list(_last_vision.get("issues") or []) + ["accepted_banner_total_cost_cap_exceeded"]
            break
        if _last_vision.get("pass") is True:
            _accepted_path = _path
            break

        if _last_vision.get("copy_season_ok") is False or _last_vision.get("occasion_ok") is False or _last_vision.get("copy_repair_required") is True:
            _last_error = "BANNER_COPY_REPAIR_REQUIRED"
            break
        if _last_vision.get("image_repair_allowed") is False or _last_vision.get("qa_schema_repair_required") is True or _last_vision.get("repair_scope") == "qa":
            _last_error = "BANNER_QA_REPAIR_REQUIRED"
            break
        _issues = [
            str(x) for x in (_last_vision.get("issues") or [])
        ]
        if any(
            x.startswith("vision_qa_unavailable:")
            for x in _issues
        ):
            _last_error = "BANNER_VISION_QA_UNAVAILABLE"
            break

        if _banner_attempt < _max_attempts:
            # Cost prediction is intentionally conservative. If another image
            # would likely cross the total 4 RUB ceiling, quality wins over the
            # schedule and the slot is blocked instead.
            _predicted_next = max(_attempt_cost * 1.25, 1.50) + _qa_reservation
            if _total_banner_cost + _predicted_next > float(
                _SQ.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB
            ):
                _last_error = "BANNER_COST_GUARD_BLOCKED"
                break

    result["banner_attempts"] = len(_attempt_costs)
    result["banner_attempt_costs"] = list(_attempt_costs)
    _cost_precision = 6 if (result.get("copy_deletion") or {}).get("attempts") == 1 else 4
    result["banner_cost_rub"] = round(_total_banner_cost, _cost_precision) if _cost_known else None
    result["banner_cost_known"] = _cost_known
    if _final_payload and not _cost_known:
        result["final_payload_reconciliation_required"] = True
    result["banner_vision_qa_costs"] = list(_qa_costs)
    result["vision_qa"] = _last_vision or {
        "policy_version": _SQ.POLICY_VERSION,
        "pass": False,
        "issues": ["vision_qa_not_reached"],
    }

    if _accepted_path:
        result["banner"] = _accepted_path
        result["banner_engine"] = "openai_gpt_image_2"
        result["banner_model"] = "gpt-image-2"
        result["quality_status"] = "pass"
        print(
            "[banner_openai] quality PASS: %s bytes=%s attempts=%s cost=%.4f"
            % (
                _accepted_path,
                _os.path.getsize(_accepted_path),
                len(_attempt_costs),
                _total_banner_cost,
            ),
            flush=True,
        )
        return result

    result["banner"] = None
    result["quality_status"] = "banner_blocked"
    result["banner_error"] = (
        "BANNER_QUALITY_BLOCKED: "
        + (
            _last_error
            or ",".join(
                str(x)
                for x in (
                    (result.get("vision_qa") or {}).get("issues")
                    or []
                )[:6]
            )
            or "vision_qa_failed"
        )
    )[:220]
    print(
        "[banner_openai] quality blocked: "
        + result["banner_error"],
        flush=True,
    )
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


def _send_telegram(chat_id, text, banner_path, *, tenant_scope, delivery_context=None):
    from app.services.social_delivery_transport import send_telegram
    return send_telegram(chat_id, _tg_safe_html(text), banner_path,
                         tenant_scope=tenant_scope, delivery_context=delivery_context)

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
            observe_suppressed(__name__, sys.exc_info()[1], line=1718)
        result.update({
            "reason": "vk_rate_limited",
            "dependency_state": "waiting_external",
            "retryable_external": True,
            "retry_after_seconds": retry_seconds,
        })
    return result


def _social_vk_wall_post_once(session, params):
    """Execute one VK publication request under the canonical Social write owner."""
    return session.get(
        "https://api.vk.com/method/wall.post",
        params=params, timeout=(5, 30),
    ).json()


def _send_vk(owner_id, text, banner_path, *, tenant_scope, delivery_context=None):
    import re as _re_vk
    from app.services.social_delivery_transport import send_vk
    return send_vk(
        owner_id, _re_vk.sub(r"<[^>]+>", "", text or ""), banner_path,
        tenant_scope=tenant_scope, delivery_context=delivery_context,
        api_failure=_vk_api_failure,
        publish_wall=_social_vk_wall_post_once,
        cooldown_remaining=_social_vk_rate_backoff_remaining,
        clear_cooldown=_clear_social_vk_rate_backoff,
    )

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
        observe_suppressed(__name__, sys.exc_info()[1], line=2503)
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
        observe_suppressed(__name__, sys.exc_info()[1], line=2934)
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
    _now = _project_clock(proj)
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



def _align_nail_traits_to_base(account_id, base, traits):
    """Semantic topic wins over random nail rotation."""
    t = dict(traits or {})
    low = str(base or "").lower().replace("ё", "е")
    pedicure_topic = ("педикюр" in low or "стоп" in low)

    if pedicure_topic:
        t["service_type"] = "уход за стопами" if "уход" in low and "педикюр" not in low else "педикюр"
    elif any(x in low for x in ("маникюр", "кутикул", "рук")):
        t["service_type"] = "уход за руками" if "уход" in low and "маникюр" not in low else "маникюр"

    if "коротк" in low:
        t["nail_length"] = "короткие"
    elif "длинн" in low:
        t["nail_length"] = "длинные"
    elif "средн" in low:
        t["nail_length"] = "средние"

    if "мягкий квадрат" in low:
        t["nail_shape"] = "мягкий квадрат"
    elif "миндал" in low:
        t["nail_shape"] = "миндаль"
    elif "квадрат" in low:
        t["nail_shape"] = "квадрат"
    elif "овал" in low:
        t["nail_shape"] = "овал"

    color_map = (
        ("бордо","бордовый"),("бордов","бордовый"),("шоколад","шоколадный"),
        ("молоч","молочный"),("красн","красный"),("нюд","нюд"),
        ("черн","чёрный"),("бел","белый"),("розов","розовый"),("син","синий"),
        ("голуб","голубой"),("зелен","зелёный"),("зелён","зелёный"),
        ("сереб","серебристый"),("золот","золотистый"),("пастел","пастельный"),
        ("неон","неоновый"),
    )
    design_map = (
        ("кошачий глаз","кошачий глаз"),("френч","френч"),("геометр","геометрия"),
        ("абстрак","абстракция"),("градиент","градиент"),("метал","металлик"),
        ("жемчуж","жемчужный"),("минимал","минимализм"),("акцент","акцентные ногти"),
        ("однотон","однотонный"),
    )
    explicit_color = False
    explicit_design = False
    for needle, value in color_map:
        if needle in low:
            t["primary_color"] = value
            explicit_color = True
            break
    for needle, value in design_map:
        if needle in low:
            t["design_type"] = value
            explicit_design = True
            break

    if "миф" in low:
        t["content_type"] = "миф и правда"
    elif "разбор" in low:
        t["content_type"] = "разбор дизайна"
    elif "два вариант" in low or "сравн" in low:
        t["content_type"] = "сравнение двух вариантов"
    elif "ошиб" in low:
        t["content_type"] = "ошибка домашнего ухода"
    elif "уход" in low or "как " in low or "что " in low or "почему " in low:
        t["content_type"] = "совет мастера"

    s = str(t.get("service_type") or "")
    pedicure_topic = pedicure_topic or "педикюр" in s or "стоп" in s
    if pedicure_topic:
        t["nail_shape"] = "естественная"
        t["nail_length"] = "короткие"
        if not explicit_color:
            t["primary_color"] = "нюд"
        if not explicit_design:
            t["design_type"] = "однотонный"
        t["visual_style"] = "журнальный beauty"
        t["tone"] = "экспертный"
        if not any(x in low for x in ("миф", "разбор", "сравн", "ошиб", "юмор")):
            t["content_type"] = "совет мастера"
        t["cta_type"] = "сохранить идею"
        pool = _FOOT_COMPS
    else:
        pool = _HAND_COMPS

    if str(t.get("composition") or "") not in pool:
        t["composition"] = _least_used(pool, _traits_history(account_id), "composition")
    return t


def _nail_theme(account_id, base_themes):
    """Возвращает (тема_с_признаками, признаки). Тема идёт И в текст, И в баннер,
    поэтому картинка не может разойтись с постом по услуге и дизайну."""
    t = _pick_nail_traits(account_id)
    import random as _rnd
    base = _rnd.choice(base_themes or ["идея дизайна"])
    t = _align_nail_traits_to_base(account_id, base, t)
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


def _posting_generation_record(posts, generation_key):
    """A persisted lifecycle owns its scheduled intent independently of status.

    Even an error without cost metadata may follow a paid or ambiguous provider
    result. None of the scheduled record writers proves such an error free to
    retry. Recovery must therefore repair/reconcile the existing record instead
    of purchasing another post and resetting its visible cost/history.
    """
    if not generation_key:
        return None
    if not isinstance(posts, list):
        raise RuntimeError("SOCIAL_GENERATION_STATE_INVALID")
    return next((
        rec for rec in posts if isinstance(rec, dict)
        and str(rec.get("generation_key") or "") == str(generation_key)
    ), None)


def _save_post_record(account_id, project_id, project_name, text, banner_path, status, ids=None, preview=None, publish_at=None, error=None, generation_key=None, record_id=None, tracking=None):
    db = SessionLocal()
    try:
        row, posts = load_posting_posts_for_update(db, account_id)
        # Final cross-process materialization barrier. This does not claim to
        # serialize concurrent provider calls before the first record exists.
        # A status change must never mint a second identity or replace paid
        # evidence with a new per-record budget. Keep the stored row untouched.
        existing = _posting_generation_record(posts, generation_key)
        if existing is not None:
            existing_id = str(existing.get("id") or "").strip()
            if not existing_id:
                raise RuntimeError("SOCIAL_GENERATION_RECORD_ID_MISSING")
            db.rollback()
            db.info.pop("posting_posts_lock:" + str(account_id), None)
            return existing_id

        import uuid as _uuid
        from app.services.social_media_assets import persist_banner_asset
        banner_asset = persist_banner_asset(account_id, banner_path, banner_dir=BANNER_DIR) if banner_path else None
        banner_name = banner_asset["filename"] if banner_asset else ""
        _record_id = str(record_id or _uuid.uuid4().hex[:12])
        rec = {"id": _record_id, "project_id": project_id, "project_name": project_name,
               "text": text, "banner": banner_name, "status": status,
               "publication_status": status,
               "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ids": ids or {},
               "external_post_id": ids or {}, "preview": preview or "",
               "publish_at": publish_at or None, "error": str(error or "")[:300],
               "published_at": (datetime.now().astimezone().isoformat(timespec="seconds") if status == "published" else None),
               "generation_key": str(generation_key or "")[:160],
               "delivery_protocol": "SOCIAL_RECEIPT_V1"}
        if isinstance(tracking, dict) and tracking:
            rec.update({k: v for k, v in tracking.items() if v is not None})
        if banner_asset:
            rec["banner_asset"] = banner_asset
        # Other project/history rows remain intact. QA reads a bounded newest
        # 60 per project rather than truncating the entire account to 80.
        posts.insert(0, rec)
        save_posting_posts_locked(db, account_id, row, posts)
    finally:
        db.close()
    return rec["id"]


def _posting_generation_exists(account_id, generation_key):
    """A saved intent or unreadable state must never authorize fresh paid work."""
    if not generation_key:
        return False
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_posts").first()
        posts = _json.loads(row.value) if row is not None else []
        return _posting_generation_record(posts, generation_key) is not None
    except Exception as exc:
        raise RuntimeError("SOCIAL_GENERATION_STATE_UNAVAILABLE") from exc
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
    local_now = _project_clock(proj)
    now_hour = int(local_now.strftime("%H"))
    hours = []
    for raw in (proj.get("times") or []):
        text = str(raw).strip()
        match = _re_due.search(r"\d{1,2}", text)
        if match and 0 <= int(match.group()) <= 23:
            hours.append(int(match.group()))
    due = [h for h in sorted(set(hours), reverse=True) if 0 <= now_hour - h <= int(catchup_hours)]
    return due[0] if due else None



def _social_theme_bucket(proj, theme):
    """Stable editorial bucket used for project-specific diversity rules."""
    name = str((proj or {}).get("name") or "").lower().replace("ё", "е")
    t = str(theme or "").lower().replace("ё", "е")

    if "океан клиент" in name or "boris" in name or "борис" in name:
        groups = (
            ("avito", ("авито", "объявлен", "каталог", "фид", "категор", "характеристик", "дубл")),
            ("creative", ("баннер", "фотограф", "изображен", "бренд")),
            ("ab_tests", ("a/b", "аб-тест", "гипотез", "масштаб")),
            ("sales_manager", ("менеджер", "переписк", "ответить", "ответ", "продаж")),
            ("crm_reactivation", ("crm", "срм", "реактива", "воронк", "лид")),
            ("rop_control", ("роп", "контрол", "слабые места")),
            ("calls_telephony", ("звон", "телефон", "телефони")),
            ("analytics", ("аналит", "цифр", "стоимост", "деньг", "бюджет")),
            ("social_content", ("телеграм", "telegram", "вконтакт", "vk", "соцсет", "контент")),
            ("market_growth", ("новую ниш", "новый город", "сезон", "конкурент", "цены конкурент")),
            ("automation_ai", ("автоматизац", "ии", "искусственн", "руками")),
        )
        for key, needles in groups:
            if any(x in t for x in needles):
                return key
        return "business_process"

    if "от души" in name:
        groups = (
            ("colleague", ("коллег",)),
            ("boss", ("начальник", "руководител")),
            ("profession", ("строител", "медик", "учител", "професси")),
            ("team", ("коллектив", "корпоратив")),
            ("anniversary", ("юбиле", "день рождения")),
            ("wedding", ("свадьб",)),
            ("parents", ("мам", "пап", "родител")),
            ("farewell", ("прощан",)),
            ("song", ("песн",)),
            ("toast_speech", ("тост", "речь", "слово")),
            ("anti_template", ("шаблон", "интернет", "дежурн")),
        )
        for key, needles in groups:
            if any(x in t for x in needles):
                return key
        return "personal_story"

    return ""


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
    recent_buckets = []
    name = str((proj or {}).get("name") or "").lower().replace("ё", "е")
    bucket_window = 10 if "от души" in name else (3 if ("океан клиент" in name or "boris" in name or "борис" in name) else 0)
    query_limit = max(int(recent_limit), bucket_window, 1)
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like("posting_generation_plan:" + pid + ":%"),
        ).order_by(Storage.id.desc()).limit(query_limit).all()
        for row in rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                continue
            theme = str((value or {}).get("theme") or "").strip()
            if theme and theme not in recent and len(recent) < int(recent_limit):
                recent.append(theme)
            if theme and bucket_window and len(recent_buckets) < bucket_window:
                bucket = _social_theme_bucket(proj, theme)
                if bucket:
                    recent_buckets.append(bucket)
    finally:
        db.close()

    candidates = [x for x in pool if x not in recent] or list(pool)
    if bucket_window and recent_buckets:
        diversified = [
            x for x in candidates
            if _social_theme_bucket(proj, x) not in set(recent_buckets)
        ]
        if diversified:
            candidates = diversified
        else:
            # Ocean may reuse a bucket after 3 other modules; Dushi reuses an
            # emotional construction only after 10 planned posts when possible.
            relaxed_window = 2 if bucket_window <= 3 else 6
            recent_relaxed = set(recent_buckets[:relaxed_window])
            relaxed = [
                x for x in candidates
                if _social_theme_bucket(proj, x) not in recent_relaxed
            ]
            if relaxed:
                candidates = relaxed
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
    local_now = _project_clock(proj)
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
        if row:
            try:
                plan = _json.loads(row.value)
                if isinstance(plan, dict) and plan.get("theme"):
                    reference_day = _plan_reference_date(plan, proj, generation_key)
                    _SOCIAL_EDITORIAL_DATE.set(reference_day)
                    plan.setdefault("reference_date", reference_day.isoformat())
                    plan.setdefault("occasion_context", _project_occasion_context(
                        proj, plan.get("theme"), plan.get("occasion"), reference_day))
                    if not str(plan.get("text_prompt_snapshot") or "").strip():
                        plan["text_prompt_snapshot"] = _build_text_prompt(
                            str(plan.get("theme") or ""),
                            str((proj or {}).get("text_prompt") or ""),
                            str(plan.get("occasion") or ""),
                            examples=None,
                            allow_hashtags=bool((proj or {}).get("allow_hashtags")),
                            reference_date=reference_day,
                        )
                        row.value = _json.dumps(plan, ensure_ascii=False)
                        db.commit()
                    return generation_key, plan
                raise RuntimeError("SOCIAL_GENERATION_PLAN_INVALID")
            except Exception as exc:
                # A corrupt/unknown existing paid plan is not permission to
                # mint a fresh identity or another paid request for its slot.
                raise RuntimeError("SOCIAL_GENERATION_PLAN_UNAVAILABLE") from exc
        if _is_nail_ctx(account_id, proj):
            theme, _traits = _nail_theme(account_id, proj.get("themes") or [])
        elif _ocean_clients_project(proj):
            # Owner policy: every scheduled Ocean slot rotates configured editorial themes.
            theme = _choose_rotating_project_theme(account_id, proj, _random, recent_limit=8)
        else:
            theme = _choose_rotating_project_theme(account_id, proj, _random, recent_limit=6)
        reference_day = planned_content_date(proj, generation_key=generation_key, now=datetime.now(timezone.utc))
        _SOCIAL_EDITORIAL_DATE.set(reference_day)
        final_payload_enrolled = (
            str(account_id) == "u2" and pid in {"a374ced25745", "f938caa3a944", "270dbf472f7f"}
            and (proj.get("mode") or "auto") == "auto"
            and reference_day == local_now.date() and parsed_day == local_day
        )
        # New owner auto plans take a calendar occasion only from the chosen
        # topic itself. An unrelated next holiday is not part of an evergreen
        # service post, even when the optional in-memory owner guard is absent.
        occasion = "" if final_payload_enrolled else _project_occasion(proj)
        plan = {
            "generation_key": generation_key,
            "project_id": pid,
            "slot": slot,
            "theme": theme,
            "occasion": occasion or "",
            "reference_date": reference_day.isoformat(),
            "occasion_context": _project_occasion_context(proj, theme, occasion, reference_day),
            "text_prompt_snapshot": _build_text_prompt(
                theme,
                str((proj or {}).get("text_prompt") or ""),
                occasion or "",
                examples=None,
                allow_hashtags=bool((proj or {}).get("allow_hashtags")),
                reference_date=reference_day,
            ),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        if final_payload_enrolled:
            plan["final_payload_qa"] = {
                "version": "SOCIAL_FINAL_PAYLOAD_QA_V1",
                "record_id": __import__("uuid").uuid4().hex[:12],
                "account_id": str(account_id), "project_id": pid,
                "generation_key": generation_key, "storage_key": storage_key,
                "local_date": reference_day.isoformat(),
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
    blocked_results = []
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
        _local_now = _project_clock(proj)
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
        _final_payload_context = None
        _final_payload_due = ""
        if respect_time:
            _generation_key, _plan = _scheduled_plan_with_date_context(
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
            if (_plan or {}).get("final_payload_qa") is not None:
                _final_payload_context = dict(_plan["final_payload_qa"])
                _due = _scheduled_record_delivery_due_local({"generation_key": _generation_key}, proj)
                if _due is None:
                    raise RuntimeError("SOCIAL_FINAL_PAYLOAD_SLOT_UNVERIFIED")
                _final_payload_due = _due.strftime("%Y-%m-%d %H:%M")
        else:
            if _is_nail_ctx(account_id, proj):
                theme, _tr = _nail_theme(account_id, proj.get("themes") or [])
            else:
                theme = random.choice(proj.get("themes") or ["поздравление"])
            _occ = _project_occasion(proj)
        print("[autopost_projects]", pname, "тема:", theme, flush=True)
        if _occ:
            print("[autopost_projects]", pname, "повод:", _occ, flush=True)
        try:
            post = generate_post(
                account_id, theme,
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
                allow_hashtags=bool(proj.get("allow_hashtags")),
                project=proj,
                reference_date=(_plan or {}).get("reference_date") if respect_time else _project_clock(proj).date(),
                occasion_context=(_plan or {}).get("occasion_context") if respect_time else _project_occasion_context(
                    proj, theme, _occ, _project_clock(proj).date()),
                **({"final_payload_context": _final_payload_context} if _final_payload_context is not None else {}),
            )
        except RuntimeError as _quality_exc:
            _quality_reason = str(_quality_exc or "")
            if (_final_payload_context is not None and _quality_reason in {
                    "SOCIAL_FINAL_PAYLOAD_RECONCILIATION_REQUIRED",
                    "SOCIAL_FINAL_PAYLOAD_PAST_CLAIM_FORBIDDEN",
            }):
                # Expected durable no-rebuy state is local to this project.
                # Targeted owner-ready loops must still reach healthy projects.
                # Do not create a post, rewrite the claim, or discard its costs.
                blocked = {"status": "generation_blocked", "generated": False,
                    "materialized": False, "project_id": pid,
                    "generation_key": _generation_key,
                    "record_id": _final_payload_context["record_id"],
                    "reason": _quality_reason, "reconciliation_required": True,
                    "paid_work_started_in_this_call": False}
                print("[autopost_projects] FINAL_PAYLOAD_GENERATION_BLOCKED:",
                      _json.dumps(blocked, ensure_ascii=False, sort_keys=True), flush=True)
                if only_id:
                    return blocked
                blocked_results.append(blocked)
                continue
            if _quality_reason.startswith("SOCIAL_COPY_QUALITY_BLOCKED:"):
                from app.services import social_quality as _SQ_block
                _brief = _SQ_block.build_content_brief(
                    account_id, proj, theme
                )
                _save_post_record(
                    account_id, pid, pname, "", None,
                    "quality_blocked",
                    preview="",
                    publish_at=_final_payload_due or _next_slot(proj),
                    error=_quality_reason[:300],
                    generation_key=_generation_key,
                    record_id=(_final_payload_context or {}).get("record_id"),
                    tracking={
                        "quality_policy": _SQ_block.POLICY_VERSION,
                        "quality_status": "copy_blocked",
                        "content_brief": _brief,
                    },
                )
                print(
                    "[autopost_projects] SOCIAL_COPY_QUALITY_BLOCKED:",
                    pname, "|", _quality_reason[:220],
                    "| quality_beats_schedule=1",
                    flush=True,
                )
                continue
            raise
        if (
            _generation_key
            and post.get("text_engine") in {"deterministic_uncertain_fallback","deterministic_provider_recovery"}
        ):
            _record_social_ai_uncertain_fallback(
                account_id, pid, _generation_key, theme,
                post.get("text_recovery_reason") or "",
            )
        from app.services import social_quality as _SQ_track
        _quality_tracking = _SQ_track.quality_tracking(post)
        _media_ok, _media_err = _banner_delivery_ready(
            post, proj.get("image_source", "ai")
        )
        if not _media_ok:
            _quality_blocked = (
                str(post.get("quality_status") or "") == "banner_blocked"
                or str(_media_err or "").startswith("BANNER_QUALITY_BLOCKED:")
            )
            _status = "quality_blocked" if _quality_blocked else "error"
            _save_post_record(
                account_id, pid, pname, post.get("text", ""), None, _status,
                preview=post.get("text", ""),
                publish_at=_final_payload_due or _next_slot(proj),
                error=(
                    str(_media_err)
                    if _quality_blocked
                    else "BANNER_REQUIRED: " + str(_media_err)
                ),
                generation_key=_generation_key,
                record_id=(_final_payload_context or {}).get("record_id"),
                tracking=_quality_tracking,
            )
            print(
                "[autopost_projects] BANNER_REQUIRED:",
                pname, "|", _media_err, "| status=" + _status,
                flush=True,
            )
            continue
        _record_id = str((_final_payload_context or {}).get("record_id") or __import__("uuid").uuid4().hex[:12])
        _tracking = _ocean_tracking_record(
            post.get("text", ""), proj, _record_id
        )
        _tracking.update(_quality_tracking)
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
        # SOCIAL_DURABLE_DRAFT_BEFORE_SEND_V1: the canonical due/rescue
        # owns all external delivery. Persist ID, generation key, tracking and
        # quality before any provider call, so a worker crash cannot mint a new
        # post/UTM and replay the same business intent.
        _queue_due = _final_payload_due or _project_clock(proj).strftime("%Y-%m-%d %H:%M")
        _preview_platform = "tg" if proj.get("channel_tg") and (proj.get("platforms") or "both") in ("tg", "both") else "vk"
        _queued_record_id = _save_post_record(
            account_id, pid, pname, post["text"], post.get("banner"), "draft",
            preview=(post.get("delivery_texts") or {}).get(_preview_platform) or _project_delivery_text_tracked(post["text"], proj, _preview_platform, tracking_id=_tracking_id),
            publish_at=_queue_due, generation_key=_generation_key,
            record_id=_record_id, tracking=_tracking,
        )
        print("[autopost_projects] готов к штатной доставке:", pname,
              "| record_id=", _queued_record_id, flush=True)
    if blocked_results:
        return {"status": "projects_blocked", "blocked_projects": blocked_results}


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

    local_now = _project_clock(project)
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
                    observe_suppressed(__name__, sys.exc_info()[1], line=4743)
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
            local_now=_project_clock(proj)
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


def _social_vk_rate_backoff_session():
    """Use the receipt connection while its two-connection delivery lock is held."""
    from app.services.social_delivery_receipts import current_receipt_connection
    conn = current_receipt_connection()
    return SessionLocal(bind=conn) if conn is not None else SessionLocal()


def _social_vk_rate_backoff_remaining():
    """Shared cooldown for live VK delivery after API error 9/29."""
    import time as _time
    db = _social_vk_rate_backoff_session()
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
    db = _social_vk_rate_backoff_session()
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
    db = _social_vk_rate_backoff_session()
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


def _vk_text_only_fallback_threshold(project):
    try:
        return max(1, min(int((project or {}).get("vk_text_only_fallback_after_cycles") or 3), 10))
    except Exception:
        return 3


def _vk_text_only_fallback_enabled(project, record):
    if not bool((project or {}).get("vk_text_only_fallback_enabled")):
        return False
    try:
        cycles = int((record or {}).get("vk_media_failure_cycles") or 0)
    except Exception:
        cycles = 0
    return cycles >= _vk_text_only_fallback_threshold(project)


def _vk_media_upload_failure(result):
    if not isinstance(result, dict) or result.get("ok"):
        return False
    stage = str(result.get("stage") or "")
    error = str(result.get("error") or "")
    if stage == "rate_backoff":
        return False
    return (
        stage.startswith("photos.")
        or "загрузка фото ВК" in error
        or "photos.getWallUploadServer" in error
        or "photos.saveWallPhoto" in error
    )


def _apply_vk_delivery_outcome(record, project, result, used_text_only=False):
    """Persist media-failure cycles without converting cooldown polls into failures."""
    if not isinstance(record, dict) or not isinstance(result, dict):
        return record
    if result.get("ok"):
        record["vk_media_failure_cycles"] = 0
        record.pop("vk_last_media_failure_at", None)
        record.pop("vk_text_only_fallback_ready", None)
        if used_text_only:
            record["vk_text_only_fallback_used_at"] = datetime.now(timezone.utc).isoformat()
        return record
    if not used_text_only and _vk_media_upload_failure(result):
        try:
            cycles = int(record.get("vk_media_failure_cycles") or 0) + 1
        except Exception:
            cycles = 1
        record["vk_media_failure_cycles"] = cycles
        record["vk_last_media_failure_at"] = datetime.now(timezone.utc).isoformat()
        record["vk_text_only_fallback_ready"] = bool(
            (project or {}).get("vk_text_only_fallback_enabled")
            and cycles >= _vk_text_only_fallback_threshold(project)
        )
    return record


def _social_vk_day_evidence_rejected_without_io(result, record):
    """Only a proven admission-only refusal may yield this rescue's VK turn."""
    return (
        isinstance(record, dict)
        and record.get("delivery_protocol") == "SOCIAL_RECEIPT_V1"
        and isinstance(result, dict)
        and result.get("ok") is False
        and result.get("stage") == "destination_capacity"
        and result.get("reason") == "SOCIAL_DAY_EVIDENCE_REQUIRED"
        and result.get("dependency_state") == "queued"
        and result.get("receipt_status") == "planned"
        and result.get("delivery_outcome_uncertain") is False
        and result.get("provider_io_started") is False
        and str(result.get("receipt_key") or "").startswith("social_delivery:v1:")
    )


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

        # Destination cap and fresh-slot reservations are checked atomically
        # in the shared receipt boundary, for both scheduled and API delivery.

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

            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            now_local = _project_clock(proj, now_utc)
            due = candidate.get("status") == "partial"
            reason = "retry_partial_delivery" if due else ""
            pa = str(candidate.get("publish_at") or "").strip()
            if pa:
                try:
                    if now_local >= datetime.strptime(pa[:16], "%Y-%m-%d %H:%M"):
                        due = True
                        reason = "publish_at " + pa
                except Exception:
                    observe_suppressed(__name__, sys.exc_info()[1], line=5923)
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
                        observe_suppressed(__name__, sys.exc_info()[1], line=5952)
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
            _vk_cooldown_expired = False
            if missing_required == {"vk"}:
                try:
                    _vk_cooldown_expired = int(_social_vk_rate_backoff_remaining() or 0) <= 0
                except Exception:
                    _vk_cooldown_expired = False
            if (candidate.get("status") == "partial" and missing_required
                    and missing_required.issubset(waits) and not _vk_cooldown_expired):
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
                        observe_suppressed(__name__, sys.exc_info()[1], line=6007)
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
                                observe_suppressed(__name__, sys.exc_info()[1], line=6019)
                if not fresh_due:
                    continue

                platforms = proj.get("platforms") or "both"
                bname = rec.get("banner") or ""
                try:
                    bpath = _resolve_banner_path(acc, bname)
                except (ValueError, OSError) as asset_exc:
                    rec["error"] = "SOCIAL_MEDIA_REFERENCE_INVALID:" + type(asset_exc).__name__
                    _persist_post_record(acc, rec)
                    print("[due]", rec["error"], rec.get("id"), flush=True)
                    continue
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
                _delivery_public_texts = None
                from app.services.social_owner_policy_v3 import is_guarded as _is_social_guarded
                if str(rec.get("quality_policy") or "") == "SOCIAL_QUALITY_V1" or _is_social_guarded(proj):
                    _gate = _social_delivery_quality_gate(acc, rec, proj, bpath)
                    rec.update(_gate.get("record_updates") or {})
                    if _gate.get("pass") is not True:
                        rec["quality_status"] = "delivery_recheck_blocked"
                        rec["error"] = "SOCIAL_DELIVERY_QUALITY_BLOCKED:" + ",".join(_gate.get("blockers") or [])[:230]
                        _persist_post_record(acc, rec)
                        print("[due]", rec["error"], rec.get("id"), flush=True)
                        continue
                    _delivery_public_texts = _gate["public_texts"]
                    _saved_delivery_texts = dict(rec.get("delivery_texts") or {})
                    _saved_delivery_texts.update(_delivery_public_texts)
                    rec["delivery_texts"] = _saved_delivery_texts
                    if not _persist_post_record(acc, rec):
                        raise RuntimeError("SOCIAL_QA_EVIDENCE_PERSIST_FAILED")
                results = {}
                _tg_attempted_here = False
                _vk_admission_only = False
                existing_ids = dict(rec.get("ids") or {})
                if _delivery_public_texts is None:
                    _delivery_texts = (
                        dict(rec.get("delivery_texts"))
                        if isinstance(rec.get("delivery_texts"), dict)
                        else {}
                    )
                    if (
                        proj.get("channel_tg")
                        and platforms in ("tg", "both")
                        and "tg" not in _delivery_texts
                    ):
                        _delivery_texts["tg"] = _project_delivery_text_tracked(
                            rec.get("text", ""), proj, "tg",
                            tracking_id=_tracking_id,
                        )
                    if (
                        proj.get("vk_owner_id")
                        and platforms in ("vk", "both")
                        and "vk" not in _delivery_texts
                    ):
                        _delivery_texts["vk"] = _project_delivery_text_tracked(
                            rec.get("text", ""), proj, "vk",
                            tracking_id=_tracking_id,
                        )
                    if _delivery_texts:
                        rec["delivery_texts"] = dict(_delivery_texts)
                if proj.get("channel_tg") and platforms in ("tg", "both"):
                    if isinstance(existing_ids.get("tg"), dict) and existing_ids["tg"].get("message_id"):
                        results["tg"] = {"ok": True, "skipped_existing": True,
                                         "message_id": existing_ids["tg"].get("message_id")}
                    else:
                        _tg_attempted_here = True
                        results["tg"] = _send_telegram(
                            proj["channel_tg"], (_delivery_public_texts["tg"] if _delivery_public_texts is not None else _project_delivery_text_tracked(rec.get("text", ""), proj, "tg", tracking_id=_tracking_id)), bpath,
                            tenant_scope=acc, delivery_context=_social_delivery_context(rec, proj, "tg")
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
                            _vk_text_only = _vk_text_only_fallback_enabled(proj, rec)
                            _vk_banner = None if _vk_text_only else bpath
                            results["vk"] = _send_vk(
                                int(proj["vk_owner_id"]), (_delivery_public_texts["vk"] if _delivery_public_texts is not None else _project_delivery_text_tracked(rec.get("text", ""), proj, "vk", tracking_id=_tracking_id)), _vk_banner,
                                tenant_scope=acc, delivery_context=_social_delivery_context(rec, proj, "vk")
                            )
                            if _social_vk_day_evidence_rejected_without_io(results.get("vk"), rec):
                                # No provider-capable callback ran. Let the
                                # older receipt reach its native reconciliation
                                # instead of being hidden by this newer refusal.
                                vk_attempted = False
                                _vk_admission_only = True
                            if isinstance(results.get("vk"), dict):
                                if _vk_text_only:
                                    results["vk"]["fallback_mode"] = "text_only"
                                _apply_vk_delivery_outcome(rec, proj, results["vk"], used_text_only=_vk_text_only)

                rec["ids"] = _merge_delivery_ids(existing_ids, proj, results)
                rec["external_post_id"] = dict(rec.get("ids") or {})
                complete = _all_required_delivery_ok(proj, results)
                if complete:
                    _preview_sources = (
                        _delivery_public_texts if isinstance(_delivery_public_texts, dict) else {},
                        rec.get("delivery_texts") if isinstance(rec.get("delivery_texts"), dict) else {},
                    )
                    _preview_text = next((payload
                        for platform in _required_delivery_platforms(proj)
                        for source in _preview_sources
                        for payload in [source.get(platform)]
                        if isinstance(payload, str) and payload.strip()), None)
                    _mark_social_delivery_published(rec, proj, preview_text=_preview_text)
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
                                    "error", "stage", "reason", "dependency_state", "retryable_external",
                                    "retry_after_seconds", "owner_action_required", "fallback_mode",
                                    "receipt_key", "receipt_status", "delivery_outcome_uncertain",
                                    "reconciliation_required", "review_after", "review_overdue",
                                    "owner_action", "photo_message_id", "provider_io_started",
                                )
                                if rv.get(key) is not None
                            }
                    rec["delivery_failures"] = delivery_failures
                    rec["error"] = "; ".join(errs)[:300]

                if not _persist_post_record(acc, rec):
                    raise RuntimeError("SOCIAL_POST_RECORD_DISAPPEARED_DURING_DELIVERY")
                # A TG invocation may have sent or reconciled even when its
                # final result is cached/uncertain. Only the preexisting-ID
                # skip (or absent TG branch) leaves this project turn unused.
                if not (_vk_admission_only and not _tg_attempted_here):
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
                        observe_suppressed(__name__, sys.exc_info()[1], line=6184)
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

    if not dry:
        try:
            _verify = social_post_publish_verify_once(limit=12, min_age_seconds=60)
            if _verify.get("checked"):
                print(
                    "[social_verify]",
                    _verify.get("status"),
                    "checked=", _verify.get("checked"),
                    "failed=", _verify.get("failed"),
                    flush=True,
                )
        except Exception as _verify_exc:
            print(
                "[social_verify] error:",
                type(_verify_exc).__name__,
                str(_verify_exc)[:180],
                flush=True,
            )
        try:
            _metrics = social_metrics_collect_once(limit=8)
            if _metrics.get("count"):
                print(
                    "[social_metrics] checkpoints updated=",
                    _metrics.get("count"),
                    flush=True,
                )
        except Exception as _metrics_exc:
            print(
                "[social_metrics] error:",
                type(_metrics_exc).__name__,
                str(_metrics_exc)[:180],
                flush=True,
            )



def _social_delivery_context(record, project, platform):
    """Stable, non-secret publication identity from the persisted lifecycle."""
    failure = ((record.get("delivery_failures") or {}).get(platform) or {})
    error = str(failure.get("error") or record.get("error") or "")
    # Retain only known classifier markers, never raw provider URL/token errors.
    markers = (
        "photos.getWallUploadServer:", "photos.saveWallPhoto:",
        "VK live delivery paced: next rescue pass",
        "VK cooldown после rate limit:", "BANNER_ASSET_MISSING",
    )
    marker = next((m for m in markers if m.lower() in error.lower()), "")
    stage = str(failure.get("stage") or "")
    if stage not in {"rate_backoff", "photos.getWallUploadServer", "photos.saveWallPhoto"}:
        stage = ""
    return {
        "post_id": str(record.get("id") or ""),
        "project_id": str(record.get("project_id") or project.get("id") or ""),
        "generation_key": str(record.get("generation_key") or ""),
        "created_at": record.get("created_at"),
        "protocol": record.get("delivery_protocol"),
        "legacy_error": marker,
        "legacy_stage": stage,
    }

def _social_plain_compare(value):
    import re as _re_verify
    text = _re_verify.sub(r"<[^>]+>", " ", str(value or ""))
    text = _re_verify.sub(r"\s+", " ", text).strip().lower().replace("ё", "е")
    return text


def social_post_publish_verify_once(limit=20, min_age_seconds=60, account_id=None):
    """Verify actual external Social publications and persist durable evidence.

    VK is read back via wall.getById and checked against the exact expected
    outgoing text plus media presence. Telegram Bot API has no arbitrary
    getMessage endpoint, so the successful send receipt with a real message_id
    is the strongest provider-side proof available and is recorded explicitly
    as such instead of pretending a readback happened.
    """
    import json as _json_verify
    checked = []
    failures = []
    remaining = max(1, int(limit or 20))

    _buckets = (
        [str(account_id)]
        if str(account_id or "").strip()
        else _all_user_buckets()
    )
    for acc in _buckets:
        if remaining <= 0:
            break
        db = SessionLocal()
        try:
            prow = db.query(Storage).filter(
                Storage.account_id == acc,
                Storage.key == "posting_projects",
            ).first()
            postrow = db.query(Storage).filter(
                Storage.account_id == acc,
                Storage.key == "posting_posts",
            ).first()
            try:
                projects = _json_verify.loads(prow.value or "[]") if prow else []
            except Exception:
                projects = []
            try:
                posts = _json_verify.loads(postrow.value or "[]") if postrow else []
            except Exception:
                posts = []
        finally:
            db.close()
        pmap = {
            str(p.get("id") or ""): p
            for p in projects if isinstance(p, dict)
        }

        candidates = []
        now = datetime.now().astimezone()
        for post in posts:
            if not isinstance(post, dict) or post.get("status") != "published":
                continue
            # Strict external payload verification belongs to Quality V1 posts.
            # Legacy records did not durably store the exact rendered payload,
            # so reconstructing it after publication can create false failures.
            if str(post.get("quality_policy") or "") != "SOCIAL_QUALITY_V1":
                continue
            existing_ver = (
                post.get("post_publish_verification")
                if isinstance(post.get("post_publish_verification"), dict)
                else {}
            )
            if existing_ver.get("status") == "pass":
                continue
            published_raw = str(post.get("published_at") or "").strip()
            if published_raw:
                try:
                    pdt = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                    if pdt.tzinfo is None:
                        pdt = pdt.replace(tzinfo=now.tzinfo)
                    if (now - pdt.astimezone(now.tzinfo)).total_seconds() < int(min_age_seconds):
                        continue
                except Exception:
                    observe_suppressed(__name__, sys.exc_info()[1], line=6335)
            candidates.append(post)
        candidates.sort(
            key=lambda x: str(x.get("published_at") or x.get("created_at") or ""),
            reverse=True,
        )

        for candidate in candidates[:remaining]:
            post_id = str(candidate.get("id") or "")
            proj = pmap.get(str(candidate.get("project_id") or ""), {})
            if not post_id or not proj:
                continue
            lock = _try_post_delivery_lock(acc, post_id)
            if lock is None:
                continue
            try:
                fresh = _load_post_record(acc, post_id)
                if not fresh or fresh.get("status") != "published":
                    continue
                ids = fresh.get("ids") if isinstance(fresh.get("ids"), dict) else {}
                required = _required_delivery_platforms(proj)
                platform_checks = {}
                all_ok = bool(required)
                delivery_texts = (
                    dict(fresh.get("delivery_texts"))
                    if isinstance(fresh.get("delivery_texts"), dict)
                    else {}
                )
                quality_v1 = str(fresh.get("quality_policy") or "") == "SOCIAL_QUALITY_V1"

                if "tg" in required:
                    tg = ids.get("tg") if isinstance(ids.get("tg"), dict) else {}
                    tg_payload_saved = bool(str(delivery_texts.get("tg") or "").strip())
                    tg_ok = bool(
                        tg.get("chat_id")
                        and tg.get("message_id")
                        and (tg_payload_saved or not quality_v1)
                    )
                    platform_checks["tg"] = {
                        "ok": tg_ok,
                        "mode": "provider_send_receipt",
                        "chat_id": tg.get("chat_id"),
                        "message_id": tg.get("message_id"),
                        "payload_saved": tg_payload_saved,
                        "note": (
                            "Telegram Bot API send receipt with real message_id; "
                            "no arbitrary getMessage readback endpoint exists"
                        ),
                    }
                    all_ok = all_ok and tg_ok

                if "vk" in required:
                    vk = ids.get("vk") if isinstance(ids.get("vk"), dict) else {}
                    owner_id = vk.get("owner_id")
                    external_id = vk.get("post_id")
                    vk_check = {
                        "ok": False,
                        "mode": "wall.getById",
                        "owner_id": owner_id,
                        "post_id": external_id,
                    }
                    if owner_id and external_id:
                        snap = _vk_wall_post_snapshot(owner_id, external_id)
                        item = snap.get("item") if isinstance(snap.get("item"), dict) else {}
                        if snap.get("ok") and item:
                            expected = str(delivery_texts.get("vk") or "").strip()
                            expected_source = "saved_delivery_payload"
                            if not expected and not quality_v1:
                                try:
                                    expected = _social_delivery_public_texts(fresh, proj, ["vk"])["vk"]
                                    expected_source = "legacy_reconstructed"
                                except RuntimeError as payload_exc:
                                    expected = ""
                                    vk_check["error"] = str(payload_exc)[:180]
                            actual_plain = _social_plain_compare(item.get("text") or "")
                            expected_plain = _social_plain_compare(expected)
                            text_ok = bool(expected_plain) and actual_plain == expected_plain
                            if not text_ok and actual_plain and expected_plain:
                                # VK may normalize whitespace; allow only a very
                                # tight containment after normalization.
                                text_ok = (
                                    actual_plain in expected_plain
                                    or expected_plain in actual_plain
                                ) and min(len(actual_plain), len(expected_plain)) >= int(
                                    0.96 * max(len(actual_plain), len(expected_plain))
                                )
                            banner_expected = bool(fresh.get("banner"))
                            photos = _vk_photo_attachment_ids(item)
                            media_ok = (not banner_expected) or bool(photos)
                            vk_ok = bool(text_ok and media_ok)
                            vk_check.update({
                                "ok": vk_ok,
                                "text_ok": bool(text_ok),
                                "media_ok": bool(media_ok),
                                "expected_source": expected_source,
                                "payload_saved": bool(str(delivery_texts.get("vk") or "").strip()),
                                "photo_attachments": photos[:5],
                            })
                            if quality_v1 and not expected:
                                vk_check["error"] = "delivery_payload_missing"
                            views = item.get("views") if isinstance(item.get("views"), dict) else {}
                            likes = item.get("likes") if isinstance(item.get("likes"), dict) else {}
                            comments = item.get("comments") if isinstance(item.get("comments"), dict) else {}
                            reposts = item.get("reposts") if isinstance(item.get("reposts"), dict) else {}
                            fresh["engagement"] = {
                                "provider": "vk",
                                "views": int(views.get("count") or 0),
                                "likes": int(likes.get("count") or 0),
                                "reactions": int(likes.get("count") or 0),
                                "comments": int(comments.get("count") or 0),
                                "reposts": int(reposts.get("count") or 0),
                                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            }
                        else:
                            vk_check["error"] = str(
                                snap.get("error") or "SOCIAL_POST_VERIFY_FAIL"
                            )[:180]
                    else:
                        vk_check["error"] = "external_post_id_missing"
                    platform_checks["vk"] = vk_check
                    all_ok = all_ok and bool(vk_check.get("ok"))

                verification = {
                    "policy_version": "SOCIAL_POST_VERIFY_V1",
                    "status": "pass" if all_ok else "fail",
                    "code": None if all_ok else "SOCIAL_POST_VERIFY_FAIL",
                    "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "platforms": platform_checks,
                }
                fresh["post_publish_verification"] = verification
                if not all_ok:
                    fresh["verification_error"] = "SOCIAL_POST_VERIFY_FAIL"
                else:
                    fresh.pop("verification_error", None)
                if not _persist_post_record(acc, fresh):
                    raise RuntimeError("SOCIAL_POST_VERIFY_RECORD_DISAPPEARED")
                row = {
                    "account_id": acc,
                    "project_id": str(fresh.get("project_id") or ""),
                    "post_id": post_id,
                    "status": verification["status"],
                    "platforms": platform_checks,
                }
                checked.append(row)
                if not all_ok:
                    failures.append(row)
                remaining -= 1
                if remaining <= 0:
                    break
            finally:
                _release_post_delivery_lock(lock, acc, post_id)

    return {
        "status": "pass" if not failures else "fail",
        "checked": len(checked),
        "failed": len(failures),
        "failures": failures[:10],
        "code": None if not failures else "SOCIAL_POST_VERIFY_FAIL",
    }



def social_metrics_collect_once(limit=10, account_id=None):
    """Capture real VK engagement near 24h/72h/7d checkpoints.

    Historical metrics are never invented: a missed checkpoint stays missing.
    Telegram metrics are intentionally absent unless a provider collector can
    prove them.
    """
    import json as _json_metrics

    windows = (
        ("24h", 23.5, 36.0),
        ("72h", 70.0, 96.0),
        ("7d", 164.0, 216.0),
    )
    remaining = max(1, int(limit or 10))
    updated = []
    buckets = (
        [str(account_id)]
        if str(account_id or "").strip()
        else _all_user_buckets()
    )
    now = datetime.now().astimezone()

    for acc in buckets:
        if remaining <= 0:
            break
        db = SessionLocal()
        try:
            prow = db.query(Storage).filter(
                Storage.account_id == acc,
                Storage.key == "posting_projects",
            ).first()
            postrow = db.query(Storage).filter(
                Storage.account_id == acc,
                Storage.key == "posting_posts",
            ).first()
            try:
                projects = _json_metrics.loads(prow.value or "[]") if prow else []
            except Exception:
                projects = []
            try:
                posts = _json_metrics.loads(postrow.value or "[]") if postrow else []
            except Exception:
                posts = []
        finally:
            db.close()
        pmap = {
            str(p.get("id") or ""): p
            for p in projects if isinstance(p, dict)
        }

        for post in posts:
            if remaining <= 0:
                break
            if not isinstance(post, dict) or post.get("status") != "published":
                continue
            proj = pmap.get(str(post.get("project_id") or ""), {})
            if "vk" not in _required_delivery_platforms(proj):
                continue
            ids = post.get("ids") if isinstance(post.get("ids"), dict) else {}
            vk = ids.get("vk") if isinstance(ids.get("vk"), dict) else {}
            if not vk.get("owner_id") or not vk.get("post_id"):
                continue
            raw = str(post.get("published_at") or "").strip()
            if not raw:
                continue
            try:
                pdt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if pdt.tzinfo is None:
                    pdt = pdt.replace(tzinfo=now.tzinfo)
                age_hours = (
                    now - pdt.astimezone(now.tzinfo)
                ).total_seconds() / 3600.0
            except Exception:
                continue
            history = (
                dict(post.get("engagement_history"))
                if isinstance(post.get("engagement_history"), dict)
                else {}
            )
            due_labels = [
                label for label, low, high in windows
                if low <= age_hours <= high and label not in history
            ]
            if not due_labels:
                continue

            snap = _vk_wall_post_snapshot(vk["owner_id"], vk["post_id"])
            item = snap.get("item") if isinstance(snap.get("item"), dict) else {}
            if not snap.get("ok") or not item:
                continue
            views = item.get("views") if isinstance(item.get("views"), dict) else {}
            likes = item.get("likes") if isinstance(item.get("likes"), dict) else {}
            comments = item.get("comments") if isinstance(item.get("comments"), dict) else {}
            reposts = item.get("reposts") if isinstance(item.get("reposts"), dict) else {}
            sample = {
                "provider": "vk",
                "views": int(views.get("count") or 0),
                "likes": int(likes.get("count") or 0),
                "reactions": int(likes.get("count") or 0),
                "comments": int(comments.get("count") or 0),
                "reposts": int(reposts.get("count") or 0),
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "age_hours": round(age_hours, 2),
            }

            lock = _try_post_delivery_lock(acc, post.get("id"))
            if lock is None:
                continue
            try:
                fresh = _load_post_record(acc, post.get("id"))
                if not fresh or fresh.get("status") != "published":
                    continue
                fresh_history = (
                    dict(fresh.get("engagement_history"))
                    if isinstance(fresh.get("engagement_history"), dict)
                    else {}
                )
                for label in due_labels:
                    if label not in fresh_history:
                        fresh_history[label] = dict(sample)
                fresh["engagement_history"] = fresh_history
                fresh["engagement"] = dict(sample)
                if _persist_post_record(acc, fresh):
                    updated.append({
                        "account_id": acc,
                        "post_id": fresh.get("id"),
                        "checkpoints": due_labels,
                    })
                    remaining -= 1
            finally:
                _release_post_delivery_lock(lock, acc, post.get("id"))

    return {"status": "ok", "updated": updated, "count": len(updated)}


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
    """Hard safety floor: Ocean auto-posts must generate fresh AI media.

    The legacy name is kept for compatibility with the scheduler, but the
    owner requirement is now fresh OpenAI visual generation for every new slot.
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
            if str(project.get("image_source") or "").lower() != "ai":
                changed.append({
                    "project_id": str(project.get("id") or ""),
                    "before": project.get("image_source"),
                    "after": "ai",
                })
                project["image_source"] = "ai"

        if found == 0:
            db.rollback()
            return {"ok": False, "changed": False, "reason": "ocean_project_missing"}
        if changed:
            row.value = _json.dumps(projects, ensure_ascii=False)
            db.commit()
        else:
            db.rollback()
        return {"ok": True, "changed": bool(changed), "changes": changed, "image_source": "ai"}
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
            # Existing Ocean publications retain their exact frozen payload.
            # A renderer upgrade is never permission to rewrite public history.
            try:
                clean = _vk_edit_plain_text(_social_delivery_public_texts(rec, proj, ["vk"])["vk"])
            except RuntimeError as payload_exc:
                return {"status": "blocked", "stage": "legacy_payload", "post_id": post_id,
                        "reason": str(payload_exc)[:180], "checked": checked}
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
    landing = "https://boris-ai.pro/software-dev/" if dev else "https://boris-ai.pro/audit"
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
        "cta": "Обсудить разработку:" if dev else "🎁 Получите бесплатно аудит за пару минут + тест BORIS для повышения эффективности рекламы:",
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
        r"(?im)^\s*(?:🤖\s*BORIS:|💻\s*Разработка\s+ПО:|Подробнее:|Обсудить\s+разработку:|"
        r"🎁\s*Получите\s+бесплатно\s+аудит.*?рекламы:)\s*"
        r"https://boris-ai\.pro(?:/audit|/software-dev/|/)?\?[^\s]+\s*$",
        "",
        value,
    )
    # The project authority may still contain the plain offer in its legacy
    # signature. The tracked server CTA below is the single canonical copy.
    value = _re.sub(
        r"(?im)^\s*🎁?\s*Получите\s+бесплатно\s+аудит\s+за\s+пару\s+минут\s*"
        r"\+\s*тест\s+BORIS\s+для\s+повышения\s+эффективности\s+рекламы\s*$",
        "",
        value,
    )
    value = _re.sub(r"\n{3,}", "\n\n", value).strip()
    spec = _ocean_tracking_spec(source_text, platform, tracking_id)
    footer = spec["cta"] + " " + spec["final_url"]
    if len(value) + 2 + len(footer) > 1000:
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_complete_delivery_does_not_fit")
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


def _ocean_final_payload_text(text, proj, platform="tg", tracking_id=""):
    """OVF03 for new records: useful body, topical tags, one approved CTA/URL.

    The old signature belongs to frozen historical payloads, never to the new
    renderer. Reject an overlong complete post instead of cutting useful copy.
    """
    import re
    from app.services.social_owner_policy_v3 import (
        _sanitize, _strip_forced_occasion_lines, _model_hashtag_block,
        _ocean_hashtag_block, _URL,
    )
    value = str(text or "").strip()
    signature = str((proj or {}).get("footer_signature") or "").strip()
    if signature:
        value = value.replace(signature, "")
        for block in signature.split("\n\n"):
            if block.strip().startswith("🤖 BORIS:"):
                value = value.replace(block.strip(), "")
    tags = _model_hashtag_block(value) or _ocean_hashtag_block(value)
    value = re.sub(r"<[^>]+>", "", value)
    value = _strip_forced_occasion_lines(_sanitize(value))
    value = _URL.sub("", value)
    value = re.sub(r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+", "", value)
    value = re.sub(r"(?im)^\s*🎁?\s*Получите\s+бесплатно\s+аудит\s+за\s+пару\s+минут\s*\+\s*тест\s+BORIS\s+для\s+повышения\s+эффективности\s+рекламы\s*:?\s*$", "", value)
    value = re.sub(r"(?im)^\s*(?:Обсудить\s+разработку|Подробнее|💻\s*Разработка\s+ПО|🤖\s*BORIS)\s*:\s*$", "", value)
    value = re.sub(r"(?im)^\s*Напишите\s+в\s+MAX\s+или\s+WhatsApp\s*:.*$", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if (not value or re.search(r"(?i)(?:🤖\s*BORIS:\s*AI-Авитолог|напишите.{0,40}(?:whatsapp|max)|оставьте.{0,30}заявк|свяжитесь\s+с\s+нами)", value)):
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_secondary_commercial_cta")
    tid = str(tracking_id or _social_utm_content(text=text))
    spec = _ocean_tracking_spec(text, platform, tid)
    final = "\n\n".join((value, tags, spec["cta"] + " " + spec["final_url"]))
    if len(final) > 1000:
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_complete_delivery_does_not_fit")
    count = len(re.findall(r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+", final))
    if not 5 <= count <= 10 or final.count(spec["cta"]) != 1:
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_final_payload_contract")
    gate = _ocean_publication_gate(final, text, platform, tid)
    if not gate["ok"]:
        raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_final_payload_url:" + ",".join(gate["errors"]))
    return final


def _project_delivery_text_tracked(text, proj, platform="tg", tracking_id="", final_payload=False):
    """Keep owner copy policy; fit a complete source prefix before it can cut."""
    import re as _re
    from app.services.social_owner_policy_v3 import (
        is_ocean, render_public_text, _sanitize, _strip_forced_occasion_lines,
        _model_hashtag_block, _ocean_hashtag_block as _owner_tags,
        _ocean_tracking_links, _URL,
    )
    if final_payload and is_ocean(proj):
        return _ocean_final_payload_text(text, proj, platform, tracking_id)
    if is_ocean(proj):
        tid = str(tracking_id or _social_utm_content(text=text))
        spec = _ocean_tracking_spec(text, platform, tid)
        signature = str((proj or {}).get("footer_signature") or "").strip()
        tags = _model_hashtag_block(text) or _owner_tags(text)
        owner_footer = "\n\n".join(x for x in (
            signature, _ocean_tracking_links(platform), tags,
        ) if x).strip()
        # Apply the existing legacy-CTA deduplication to the footer itself.
        # A signature word may also occur in body, CTA or tags; it is a block,
        # not a global word-count constraint.
        final_footer = _ocean_finalize_delivery(owner_footer, text, platform, tid)
        # Honour both limits: the protected owner renderer still runs below.
        # Reserve whole signature/tags/URL, never shorten any of those fields.
        allowed = min(1000 - len(owner_footer) - 2, 1000 - len(final_footer) - 2)
        value = str(text or "").strip()
        if value.endswith("\n\n" + final_footer):
            # A canonical complete footer from a prior render is not body copy.
            value = value[:-len(final_footer)].rstrip()
        value = _re.sub(r"<[^>]+>", "", value)
        value = _strip_forced_occasion_lines(_sanitize(value))
        value = _URL.sub("", value)
        value = _re.sub(r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+", "", value)
        value = _re.sub(r"\n{3,}", "\n\n", value).strip()
        if allowed <= 0 or not value:
            raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_complete_copy_does_not_fit")
        if len(value) > allowed:
            # Only an actual sentence end is admissible. A newline or a word
            # boundary alone cannot prove a complete thought; no ellipsis added.
            ends = []
            abbreviations = {"т", "е", "г", "гг", "руб", "тыс", "млн", "млрд", "см", "стр", "рис", "ул", "д", "п", "им"}
            for match in _re.finditer(r'(?<![.])[.!?][»”"\)\]]*(?=\s|$)', value):
                if match.end() > allowed:
                    break
                tail = value[match.end():].lstrip()
                token = _re.search(r"([А-Яа-яЁёA-Za-z]+)$", value[:match.start()])
                if tail and (tail[0].islower() or (value[match.start()] == "." and token and token.group(1).lower() in abbreviations)):
                    continue
                ends.append(match.end())
            if not ends:
                raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_complete_sentence_does_not_fit")
            value = value[:ends[-1]].rstrip()
        base = render_public_text(value + "\n\n" + tags, proj, platform)
        if not base.endswith(owner_footer) or not base[:-len(owner_footer)].strip():
            raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_owner_footer_or_body_incomplete")
        final = _ocean_finalize_delivery(base, text, platform, tid)
        final_tags = _re.findall(r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+", final)
        if (not 5 <= len(final_tags) <= 10
                or not final.endswith("\n\n" + final_footer)
                or not final[:-len(final_footer)].strip()
                or final.count(spec["cta"]) != 1):
            raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:ocean_footer_contract")
        return final
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


def _merge_delivery_ids(existing, proj, results):
    ids = dict(existing or {})
    for platform, identity, target in (("tg", "message_id", "chat_id"), ("vk", "post_id", "owner_id")):
        rv = results.get(platform) if isinstance(results.get(platform), dict) else {}
        previous = ids.get(platform) if isinstance(ids.get(platform), dict) else {}
        if rv.get("skipped_existing"):
            continue
        nested = rv.get("result") if isinstance(rv.get("result"), dict) else rv
        external_id = nested.get(identity)
        if not external_id:
            continue
        dest = nested.get("chat", {}).get("id") if platform == "tg" else rv.get("owner_id")
        dest = dest or (proj or {}).get("channel_tg" if platform == "tg" else "vk_owner_id")
        same = previous.get(identity) == external_id and str(previous.get(target)) == str(dest)
        part = dict(previous) if same else {}
        part.update({target: dest, identity: external_id})
        if nested.get("date") is not None:
            part["date"] = nested["date"]
        ids[platform] = part
    return ids


def _project_clock(project, now=None):
    instant = now if now is not None else datetime.now(timezone.utc)
    return project_local_now(project, instant).replace(tzinfo=None)


def _scheduled_plan_with_date_context(account_id, project, **kwargs):
    # The protected owner wrapper may call _build_text_prompt after the original
    # plan function returns. Keep its exact plan date until the wrapper finishes,
    # then restore the calling thread/task's previous context. No guard mutation.
    token = _SOCIAL_EDITORIAL_DATE.set(None)
    try:
        return _scheduled_generation_plan(account_id, project, **kwargs)
    finally:
        _SOCIAL_EDITORIAL_DATE.reset(token)


def _plan_reference_date(plan, project, generation_key):
    if (plan or {}).get("reference_date"):
        return parse_reference_date(plan["reference_date"])
    # Recover metadata from the frozen paid prompt; never rewrite that prompt
    # merely because recovery happens on a later local day.
    match = __import__("re").search(r"СЕГОДНЯ\s+(\d{2})\.(\d{2})\.(\d{4})", str((plan or {}).get("text_prompt_snapshot") or ""))
    if match:
        day, month, year = (int(x) for x in match.groups())
        return date(year, month, day)
    return planned_content_date(project, generation_key=generation_key, now=datetime.now(timezone.utc))


def _project_occasion_context(project, theme, occasion, reference_date):
    """Only explicit selected topics/calendar hooks get dated metadata.

    No automatic holidays are added to Ocean/Manicure/Dushi. Generic birthday,
    anniversary and wedding examples remain evergreen; they do not assert that
    the reader's event happens today. A selected named holiday resolves against
    the existing calendar, retaining its year/date after generation.
    """
    import re
    def normal(text):
        value = str(text or "").lower().replace("ё", "е")
        value = re.sub(r"\bдн(?:я|ю|ем)\b", "день", value)
        return re.sub(r"\s+", " ", value).strip()
    reference = parse_reference_date(reference_date)
    selected = normal(str(theme or "") + " " + str(occasion or ""))
    hits = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        events = [(date(year, month, day), name) for (month, day), name in _RU_HOLIDAYS_FIXED.items()]
        events += [(day, name) for name, day in _ru_holidays_movable(year).items() if day]
        for day, name in events:
            if normal(name) in selected:
                hits.append((abs((day - reference).days), day, name))
    if hits:
        _, day, name = min(hits)
        return {"kind": "dated", "name": name, "date": day.isoformat(),
                "source": "posting_runner_existing_ru_calendar"}
    if str(occasion or "").strip():
        return {"kind": "unverified", "name": str(occasion)}
    return {"kind": "evergreen"}


def _social_delivery_public_texts(record, project, platforms, public_texts=None):
    """Keep already-frozen payloads across renderer upgrades and retries."""
    from app.services.social_owner_policy_v3 import is_ocean
    freeze_payload = is_ocean(project)
    stored = record.get("delivery_texts")
    stored = stored if isinstance(stored, dict) else {}
    legacy = bool(freeze_payload and record.get("id") and record.get("delivery_protocol") != "SOCIAL_RECEIPT_V1")
    texts = {}
    for platform in platforms:
        saved = stored.get(platform)
        if freeze_payload and isinstance(saved, str) and saved.strip():
            if public_texts is not None and public_texts.get(platform) != saved:
                raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:delivery_payload_changed:" + platform)
            texts[platform] = saved
        elif legacy:
            # New rendering cannot establish what a legacy provider saw.
            # Never manufacture 'absent' by comparing against a new payload.
            raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:legacy_delivery_payload_unverified:" + platform)
        elif public_texts is not None:
            texts[platform] = public_texts.get(platform, "")
        else:
            tracking = _ocean_tracking_record(record.get("text", ""), project, record.get("id"))
            tid = str((tracking or {}).get("utm_content") or record.get("utm_content") or "")
            texts[platform] = _project_delivery_text_tracked(
                record.get("text", ""), project, platform, tracking_id=tid,
                **({"final_payload": True} if isinstance(record.get("final_payload_qa"), dict) and record["final_payload_qa"].get("version") == "SOCIAL_FINAL_PAYLOAD_QA_V1" else {}),
            )
    return texts


def _social_delivery_quality_gate(account_id, record, project, bpath, *, public_texts=None, now=None):
    """Real temporal re-QA of the same asset, once per exact context/output.

    This helper sends no public messages and mutates no post fields in place.
    After the per-post advisory lock/read, the caller persists record_updates
    even on QA failure, then uses returned public_texts for external sends.
    Pass bpath through the root-owned canonical asset resolver before calling.
    """
    import copy
    import hashlib
    import math
    from pathlib import Path
    from app.services import social_quality as sq
    from app.services.social_editorial_contract import ensure_delivery_qa, pending_platforms

    instant = now if now is not None else datetime.now(timezone.utc)
    updates = {}
    final_context = record.get("final_payload_qa")
    final_flow = isinstance(final_context, dict) and final_context.get("version") == "SOCIAL_FINAL_PAYLOAD_QA_V1"
    if final_context is not None and (not final_flow or final_context.get("record_id") != record.get("id")):
        return {"pass": False, "blockers": ["FINAL_PAYLOAD_IDENTITY_UNVERIFIED"], "record_updates": {}}
    missing = pending_platforms(record, project)
    if not missing:
        return {"pass": True, "public_texts": {}, "pending_platforms": [], "record_updates": {}}
    try:
        brief = sq.refresh_temporal_brief(record.get("content_brief") or {}, project, now=instant)
        visual = copy.deepcopy(record.get("visual_brief") or {})
        if not visual.get("headline"):
            return {"pass": False, "blockers": ["SOCIAL_VISUAL_BRIEF_MISSING"], "record_updates": {}}
        reference = sq.vision_reference_context(project, brief, visual)
        if reference.get("required") and not reference.get("ok"):
            return {"pass": False, "blockers": [reference.get("error")], "record_updates": {}}
        # Fingerprint includes the actual reference bytes; style-file changes
        # invalidate an old proof without changing the post or the sent channel.
        if reference.get("required"):
            visual["owner_reference"] = {
                k: reference.get(k) for k in ("niche", "version", "sha256")
            }
        old_forbidden = list((record.get("content_brief") or {}).get("forbidden_mentions") or [])
        visual["forbidden_objects"] = list(dict.fromkeys(
            [x for x in visual.get("forbidden_objects") or [] if x not in old_forbidden]
            + list(brief.get("forbidden_mentions") or [])
        ))
        visual["current_date"] = brief["current_date"]
        if final_flow:
            visual.pop("copy_deletion_context", None)
            visual["final_payload_qa_contract"] = sq.final_payload_qa_contract(project)
        if not bpath or not Path(bpath).is_file():
            return {"pass": False, "blockers": ["BANNER_ASSET_MISSING"], "record_updates": {}}
        digest = hashlib.sha256(Path(bpath).read_bytes()).hexdigest()
        if final_flow:
            deletion_context = sq.copy_deletion_qa_context(record, digest)
            if deletion_context is not None:
                visual["copy_deletion_context"] = deletion_context
            visual["final_payload_qa_contract"] = sq.final_payload_qa_contract(project, deletion_context)
        public_texts = _social_delivery_public_texts(record, project, missing, public_texts)
        if final_flow:
            from app.services.social_owner_policy_v3 import is_ocean
            if is_ocean(project):
                tid = str(_ocean_tracking_record(record.get("text", ""), project, record["id"]).get("utm_content") or "")
                for platform, public in public_texts.items():
                    public_gate = _ocean_publication_gate(public, record.get("text", ""), platform, tid)
                    if (not public_gate["ok"] or public.count(public_gate["expected"]["cta"]) != 1
                            or "🤖 BORIS: AI-Авитолог" in public
                            or __import__("re").search(r"(?i)напишите.{0,40}(?:whatsapp|max)", public)):
                        return {"pass": False, "blockers": ["OCEAN_FINAL_PAYLOAD_PUBLIC_CONTRACT:" + platform], "record_updates": {}}
        recent = [p for p in sq.recent_project_posts(account_id, str(project.get("id") or ""))
                  if str(p.get("id") or "") != str(record.get("id") or "")]
        copy_checks = {p: sq.evaluate_copy(public_texts[p], brief, project, recent_posts=recent, now=instant)
                       for p in missing}
        updates["delivery_copy_quality"] = {**copy.deepcopy(record.get("delivery_copy_quality") or {}), **copy_checks}
        if any(check.get("pass") is not True for check in copy_checks.values()):
            return {"pass": False, "blockers": ["SOCIAL_DELIVERY_COPY_BLOCKED"],
                    "copy_quality": copy_checks, "record_updates": updates}
        known = record.get("banner_cost_known") is True
        total_raw = record.get("banner_cost_rub")
        if type(total_raw) not in (int, float) or not math.isfinite(total_raw) or total_raw <= 0 or not known:
            return {"pass": False, "blockers": ["BANNER_ACTUAL_COST_UNVERIFIED"], "record_updates": {}}
        total = float(total_raw)
        cap = float(sq.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB)
        if total > cap:
            return {"pass": False, "blockers": ["BANNER_TOTAL_COST_CAP_EXCEEDED"], "record_updates": {}}
        attempts = copy.deepcopy(record.get("delivery_qa_attempts") or {})
        flags = sq.vision_required_flags(project, brief, visual)

        def actual_qa(**request):
            nonlocal total
            intent = hashlib.sha256(json.dumps({
                "context": request["context_sha256"], "outputs": request["public_texts"],
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            previous = attempts.get(intent) if isinstance(attempts.get(intent), dict) else {}
            original_cost = previous.get("cost_rub")
            if (type(original_cost) in (int, float) and math.isfinite(original_cost)
                    and original_cost > 0 and isinstance(previous.get("verdict"), dict)):
                # Same actual proof and positive known original charge. No new
                # provider call and no additional cost, including failed proof.
                return copy.deepcopy(previous["verdict"])
            from app.services.media_semantic_validator import _vision_estimate_kopeks
            if ((record.get("copy_deletion") or {}).get("state") == "qa_pending"
                    and _project_clock(project).date().isoformat() != (record.get("final_payload_qa") or {}).get("local_date")):
                return {"pass": False, "cost_guard_ok": False, "image_repair_allowed": False,
                        "repair_scope": "qa", "issues": ["COPY_DELETION_DAY_CHANGED"]}
            # Canonical financially guarded reserve is per request, including
            # all its images. Do not change the global guard or invent a factor.
            reservation = float(_vision_estimate_kopeks()) / 100.0
            if not math.isfinite(reservation) or reservation <= 0 or total + reservation > cap:
                return {"pass": False, "cost_guard_ok": False,
                        "issues": ["DELIVERY_QA_REMAINING_BUDGET_BLOCKED"]}
            verdict = sq.validate_banner_vision(
                account_id, bpath, request["brief"], request["visual_brief"],
                (request["public_texts"] if final_flow else json.dumps(request["public_texts"], ensure_ascii=False, sort_keys=True)), project,
            )
            charge = verdict.get("qa_cost_rub") if isinstance(verdict, dict) else None
            new_charge = verdict.get("qa_new_charge_rub") if isinstance(verdict, dict) else None
            if (type(charge) not in (int, float) or not math.isfinite(charge) or charge <= 0
                    or verdict.get("qa_cost_known") is not True
                    or type(new_charge) not in (int, float) or not math.isfinite(new_charge)
                    or new_charge < 0
                    or (new_charge == 0 and verdict.get("cache_hit") is not True)):
                # SQ recovers a cache's original cost only from the exact
                # tenant/operation/provider-request usage receipt. No receipt
                # means unknown, never a free QA or a fabricated PASS.
                verdict = {**(verdict if isinstance(verdict, dict) else {}), "pass": False,
                           "cost_guard_ok": False, "issues": ["DELIVERY_QA_ACTUAL_COST_UNVERIFIED"]}
                attempts[intent] = {"cost_rub": charge, "new_charge_rub": new_charge,
                                    "verdict": copy.deepcopy(verdict)}
                updates["delivery_qa_attempts"] = attempts
                updates["banner_cost_known"] = False
                return verdict
            # Charge the original proof once to this post. A cache hit carries
            # zero NEW spend, but without a stored attempt it is not evidence
            # that the original cost was already in the post total.
            accounted_charge = float(charge)
            if final_flow:
                receipt = verdict.get("qa_cost_evidence") or {}
                receipt_identity = tuple(receipt.get(k) for k in ("account_id", "operation", "request_id"))
                if (receipt_identity[0] == str(account_id) and receipt_identity[1] == "social_banner_vision_qa_v1"
                        and isinstance(receipt_identity[2], str) and receipt_identity[2]):
                    matching = []
                    for item in attempts.values():
                        if not isinstance(item, dict):
                            continue
                        old_receipt = item.get("cost_evidence") or (item.get("verdict") or {}).get("qa_cost_evidence") or {}
                        if tuple(old_receipt.get(k) for k in ("account_id", "operation", "request_id")) == receipt_identity:
                            matching.append((item, old_receipt))
                    if matching:
                        amount = float(charge)
                        consistent = all(
                            type(item.get("cost_rub")) in (int, float)
                            and item["cost_rub"] == amount
                            and type(old_receipt.get("cost_rub")) in (int, float)
                            and old_receipt["cost_rub"] == amount
                            and type(item.get("accounted_cost_rub")) in (int, float)
                            and item["accounted_cost_rub"] in (0.0, amount)
                            for item, old_receipt in matching
                        )
                        proven = consistent and sum(
                            item.get("accounted_cost_rub") == amount for item, _ in matching
                        ) == 1
                        input_sha = str(verdict.get("qa_input_sha256") or "")
                        same_input = bool(__import__("re").fullmatch(r"[0-9a-f]{64}", input_sha)) and all(
                            isinstance(item.get("verdict"), dict)
                            and item["verdict"].get("qa_input_sha256") == input_sha
                            and item["verdict"].get("qa_input_contract") == verdict.get("qa_input_contract")
                            for item, _ in matching
                        )
                        if verdict.get("cache_hit") is True and new_charge == 0 and receipt.get("cost_rub") == float(charge) and proven and same_input:
                            # A URL-only normalization change may alias the same
                            # exact paid request. Retain its positive original
                            # cost but never book this SAME receipt twice.
                            accounted_charge = 0.0
                        else:
                            verdict = {**verdict, "pass": False, "cost_guard_ok": False,
                                "image_repair_allowed": False, "repair_scope": "qa",
                                "issues": list(verdict.get("issues") or []) + ["DELIVERY_QA_RECEIPT_CONFLICT"]}
                            attempts[intent] = {"cost_rub": float(charge), "new_charge_rub": float(new_charge),
                                "accounted_cost_rub": None, "cost_evidence": copy.deepcopy(receipt),
                                "verdict": copy.deepcopy(verdict)}
                            updates.update(delivery_qa_attempts=attempts, banner_cost_known=False,
                                           final_payload_reconciliation_required=True)
                            return verdict
            total = round(total + accounted_charge, 6)
            verdict = {**verdict, "cost_guard_ok": total <= cap}
            if total > cap:
                verdict["pass"] = False
                verdict["issues"] = list(verdict.get("issues") or []) + ["BANNER_TOTAL_COST_CAP_EXCEEDED"]
            attempts[intent] = {"cost_rub": float(charge), "new_charge_rub": float(new_charge),
                                "cost_evidence": copy.deepcopy(verdict.get("qa_cost_evidence")),
                                "verdict": copy.deepcopy(verdict)}
            if final_flow:
                attempts[intent]["accounted_cost_rub"] = accounted_charge
            updates["delivery_qa_attempts"] = attempts
            updates["banner_cost_rub"] = total
            updates["banner_cost_known"] = True
            if final_flow:
                updates["banner_vision_qa_costs"] = list(record.get("banner_vision_qa_costs") or []) + ([accounted_charge] if accounted_charge > 0 else [])
            # Account for the received paid proof BEFORE any fallible asset or
            # native postcheck. A read failure cannot erase this actual charge.
            try:
                image_same = hashlib.sha256(Path(bpath).read_bytes()).hexdigest() == request["banner_sha256"]
            except OSError:
                image_same = False
            if not image_same:
                verdict = {**verdict, "pass": False,
                           "issues": list(verdict.get("issues") or []) + ["BANNER_CHANGED_DURING_QA"],
                           "image_repair_allowed": False, "repair_scope": "qa"}
            if (reference.get("required") and
                    (verdict.get("reference") or {}).get("sha256") != reference.get("sha256")):
                verdict = {**verdict, "pass": False,
                           "issues": list(verdict.get("issues") or []) + ["REFERENCE_CHANGED_DURING_QA"],
                           "image_repair_allowed": False, "repair_scope": "qa"}
            if final_flow and (verdict.get("final_payload_qa_version") != sq.FINAL_PAYLOAD_QA_VERSION
                    or verdict.get("qa_input_contract") != visual["final_payload_qa_contract"]
                    or not __import__("re").fullmatch(r"[0-9a-f]{64}", str(verdict.get("qa_input_sha256") or ""))):
                verdict = {**verdict, "pass": False,
                           "issues": list(verdict.get("issues") or []) + ["FINAL_PAYLOAD_QA_INPUT_UNVERIFIED"],
                           "image_repair_allowed": False, "repair_scope": "qa"}
            attempts[intent]["verdict"] = copy.deepcopy(verdict)
            return verdict

        result = ensure_delivery_qa(
            record=record, project=project, brief=brief, visual_brief=visual,
            banner_sha256=digest, public_texts=public_texts, required_flags=flags,
            qa_callback=actual_qa, now=instant,
        )
        updates["delivery_quality"] = result["delivery_quality"]
        if result.get("pass") is True:
            updates["delivery_texts"] = {**copy.deepcopy(record.get("delivery_texts") or {}), **public_texts}
        result["public_texts"] = public_texts
        result["record_updates"] = updates
        return result
    except Exception as exc:
        reason = str(exc)
        blocker = reason if reason.startswith("SOCIAL_COPY_QUALITY_BLOCKED:") else "SOCIAL_DELIVERY_QA_ERROR:" + type(exc).__name__
        return {"pass": False, "blockers": [blocker], "record_updates": updates}

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


