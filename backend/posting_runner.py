# -*- coding: utf-8 -*-
import os, sys, json
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db.session import SessionLocal
from app.models.storage import Storage

DEFAULT_TIMES = ["09:00", "19:00"]
BANNER_DIR = "/root/BORIS/backend/posting_banners"

def _openai_text(account_id, prompt, operation="текст постинга"):
    import requests
    from proxy_pool import get_intl_requests_proxies
    api_key = os.environ.get("OPENAI_API_KEY")
    proxies = get_intl_requests_proxies()
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        json={"model": "gpt-5.4",
              "messages": [{"role": "user", "content": prompt}],
              "max_completion_tokens": 700},
        proxies=proxies, timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    usage = data.get("usage", {})
    try:
        from app.usage import log_usage
        log_usage(account_id, "openai", "gpt-5.4", operation,
                  usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    except Exception as e:
        print("[usage]", str(e)[:120], flush=True)
    return data["choices"][0]["message"]["content"].strip()

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

def _build_text_prompt(theme, client_prompt="", occasion="", examples=None):
    parts = [f"Напиши пост для соцсетей (ВКонтакте и Telegram) на тему: {theme}.",
             "Живой, цепляющий, короткий — 3-6 предложений. Без канцелярита. Эмодзи уместно."]
    if occasion: parts.append(f"Привяжи пост к поводу/дате: {occasion}.")
    if client_prompt: parts.append(f"Учитывай стиль и пожелания автора канала: {client_prompt}")
    if examples:
        sample = "\n\n---\n\n".join(examples[:5])
        parts.append("Вот примеры уже опубликованных постов этого канала — пиши в ТАКОМ ЖЕ стиле, тоне и формате, но НЕ копируй их дословно:\n\n" + sample)
    parts.append("ВАЖНО: НЕ добавляй в текст никаких ссылок, адресов, @упоминаний и контактов — они подставляются автоматически отдельно. Призыв к действию можно, но БЕЗ конкретной ссылки.")
    parts.append("НЕ заканчивай пост призывом писать в комментарии или ставить реакции. Финальный призыв со ссылкой на контакт добавляется автоматически системой — просто заверши мысль, вопрос читателю допустим.")
    parts.append("НЕ используй markdown-разметку: никаких **, ##, __, backticks — это обычный текстовый пост, звёздочки будут видны читателю как мусор. Выделяй смысл словами и абзацами, а не форматированием.")
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


def generate_post(account_id, theme, text_prompt="", banner_prompt="", occasion="", channel="", image_source="ai"):
    result = {"account_id": account_id, "theme": theme}
    examples = None
    if channel:
        try:
            _r = _fetch_channel_posts(channel)
            if _r.get("ok"): examples = _r.get("posts")
        except Exception:
            examples = None
    _txt = _openai_text(account_id, _build_text_prompt(theme, text_prompt, occasion, examples))
    import re as _re
    _txt = _re.sub(r"\*\*(.+?)\*\*", r"\1", _txt, flags=_re.S)
    _txt = _re.sub(r"(?m)^#{1,6}\s+", "", _txt)
    _txt = _txt.replace("__", "")
    _txt = _airify(_txt)
    result["text"] = _txt
    try:
        from banner_generator import generate_ai_image
        os.makedirs(BANNER_DIR, exist_ok=True)
        fpath = os.path.join(BANNER_DIR, f"{account_id}_{datetime.now():%Y%m%d_%H%M%S}.png")
        got_stock = False
        _title = _make_title(account_id, result.get("text", "") or theme)
        if image_source == "pexels":
            try:
                from banner_generator import get_photo_stock
                got_stock = bool(get_photo_stock(theme, fpath, "square"))
            except Exception as _se:
                print("[banner] сток не сработал: " + str(_se)[:120], flush=True)
                got_stock = False
        if not got_stock:
            generate_ai_image(_build_banner_prompt(theme, banner_prompt, _title, ", ".join((date.today()+timedelta(days=_i)).strftime("%d.%m.%Y") for _i in range(8))), fpath,
                              size="1024x1024", quality="medium", model="gpt-image-2")
        import os as _os
        if not (_os.path.exists(fpath) and _os.path.getsize(fpath) > 0):
            print(f"[banner] НЕ создан (таймаут/ошибка генерации): {fpath}")
            fpath = None
        result["banner"] = fpath
    except Exception as e:
        result["banner"] = None; result["banner_error"] = str(e)[:200]
    return result

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

def _send_telegram(chat_id, text, banner_path):
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
                        data={"chat_id": chat_id, "caption": caption}, files={"photo": ph}, timeout=120)
                r = resp.json()
                break
            except Exception as _e:
                print("[tg] попытка " + str(_attempt + 1) + "/3 не удалась: " + str(_e)[:120], flush=True)
                import time as _t; _t.sleep(5)
        if r is None:
            return {"ok": False, "error": "telegram недоступен после 3 попыток"}
        if r.get("ok") and len(text) > 1024:
            requests.post(telegram_bot.API_BASE + "/sendMessage",
                data={"chat_id": chat_id, "text": text}, timeout=60)
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
    res = _send_telegram(chat_id, post["text"], post.get("banner"))
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
def _send_vk(owner_id, text, banner_path):
    import os, requests
    from proxy_pool import get_intl_requests_proxies
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    proxies = get_intl_requests_proxies()
    V = "5.199"
    group_id = str(owner_id).lstrip("-")
    attachments = None
    if banner_path and os.path.exists(banner_path):
        try:
            send_path = banner_path
            try:
                from PIL import Image as _Img
                _im = _Img.open(banner_path).convert("RGB"); _im.thumbnail((1080, 1080))
                send_path = banner_path + ".vk.jpg"; _im.save(send_path, "JPEG", quality=85)
            except Exception:
                send_path = banner_path
            up = requests.get("https://api.vk.com/method/photos.getWallUploadServer",
                params={"access_token": token, "v": V, "group_id": group_id},
                proxies=proxies, timeout=30).json()
            upload_url = up["response"]["upload_url"]
            with open(send_path, "rb") as ph:
                upr = requests.post(upload_url, files={"photo": ph}, proxies=proxies, timeout=120).json()
            sv = requests.get("https://api.vk.com/method/photos.saveWallPhoto",
                params={"access_token": token, "v": V, "group_id": group_id,
                        "photo": upr["photo"], "server": upr["server"], "hash": upr["hash"]},
                proxies=proxies, timeout=30).json()
            p = sv["response"][0]
            attachments = "photo" + str(p["owner_id"]) + "_" + str(p["id"])
        except Exception as e:
            return {"ok": False, "error": "загрузка фото ВК: " + str(e)[:200]}
    params = {"access_token": token, "v": V, "owner_id": str(owner_id),
              "from_group": 1, "message": text}
    if attachments:
        params["attachments"] = attachments
    try:
        r = requests.get("https://api.vk.com/method/wall.post", params=params,
                         proxies=proxies, timeout=30).json()
    except Exception as e:
        return {"ok": False, "error": "wall.post: " + str(e)[:200]}
    if "response" in r:
        return {"ok": True, "post_id": r["response"].get("post_id")}
    return {"ok": False, "error": r.get("error")}

def _vk_demo(theme, owner_id):
    print("[vk] генерирую и шлю пост в ВК: " + theme + " -> " + str(owner_id), flush=True)
    post = generate_post("otdushi", theme)
    r = _send_vk(owner_id, post["text"], post.get("banner"))
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


def _with_contact(text, contact):
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
    theme = random.choice(cfg.get("themes") or ["поздравление"])
    print("[autopost] тема:", theme, flush=True)
    post = generate_post(account_id, theme, text_prompt=cfg.get("text_prompt", ""), channel=cfg.get("style_source", ""), image_source=cfg.get("image_source", "ai"))
    results = {}
    if cfg.get("channel_tg"):
        results["tg"] = _send_telegram(cfg["channel_tg"], _with_contact(post["text"], cfg.get("contact_tg", "")), post.get("banner"))
    if cfg.get("vk_owner_id"):
        results["vk"] = _send_vk(int(cfg["vk_owner_id"]), _with_contact(post["text"], cfg.get("contact_vk", "")), post.get("banner"))
    if any(r.get("ok") for r in results.values()):
        db = SessionLocal()
        try: _bump_posts_today(db, account_id)
        finally: db.close()
    print("[autopost] итог:", {k: ("OK" if v.get("ok") else str(v.get("error") or v.get("send"))) for k, v in results.items()})


def _sub_active(sub):
    if not sub:
        return False
    try:
        paid = date.fromisoformat(sub.get("paid_at", ""))
        return (date.today() - paid).days < int(sub.get("period_days", 30))
    except Exception:
        return False

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


def _save_post_record(account_id, project_id, project_name, text, banner_path, status, ids=None, preview=None):
    import json as _json, uuid as _uuid
    banner_name = os.path.basename(banner_path) if banner_path else ""
    rec = {"id": _uuid.uuid4().hex[:12], "project_id": project_id, "project_name": project_name,
           "text": text, "banner": banner_name, "status": status,
           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ids": ids or {}, "preview": preview or ""}
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_posts").first()
        posts = _json.loads(row.value) if row else []
        posts.insert(0, rec)
        posts = posts[:50]
        if row:
            row.value = _json.dumps(posts, ensure_ascii=False)
        else:
            db.add(Storage(account_id=account_id, key="posting_posts", value=_json.dumps(posts, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    return rec["id"]

def autopost_projects(account_id, owner=False, respect_time=False, only_id=None):
    import json as _json, random
    db = SessionLocal()
    try:
        prow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_projects").first()
        projects = _json.loads(prow.value) if prow else []
    finally:
        db.close()
    if not projects:
        print("[autopost_projects] нет проектов:", account_id); return
    today = datetime.now().strftime("%Y%m%d")
    for proj in projects:
        pid = proj.get("id", "")
        pname = proj.get("name", pid)
        if only_id and pid != only_id:
            continue
        if respect_time:
            import re as _re2
            proj_hours = []
            for _t in (proj.get("times") or []):
                _st = str(_t).strip()
                if ":" in _st:
                    _h = _st.split(":")[0].strip()
                    if _h.isdigit() and 0 <= int(_h) <= 23:
                        proj_hours.append(str(int(_h)).zfill(2))
                else:
                    for _h in _re2.findall(r"\d{1,2}", _st):
                        if 0 <= int(_h) <= 23:
                            proj_hours.append(str(int(_h)).zfill(2))
            _off = int(proj.get("tz_offset", 3))
            if (datetime.now() + timedelta(hours=_off)).strftime("%H") not in proj_hours:
                continue
        sub = proj.get("subscription")
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
        theme = random.choice(proj.get("themes") or ["поздравление"])
        print("[autopost_projects]", pname, "тема:", theme, flush=True)
        _occ = _holiday_in_window(7)
        if _occ:
            print("[autopost_projects]", pname, "повод:", _occ, flush=True)
        post = generate_post(account_id, theme,
                             text_prompt=proj.get("text_prompt", ""),
                             banner_prompt=proj.get("banner_prompt", ""),
                             occasion=_occ,
                             channel=proj.get("style_source", ""),
                             image_source=proj.get("image_source", "ai"))
        if (proj.get("mode") or "auto") == "moderate":
            _plat = proj.get("platforms") or "both"
            _cnt = (proj.get("contact_tg") if _plat in ("tg", "both") else proj.get("contact_vk")) or proj.get("contact_vk") or ""
            _prev = _with_contact(post["text"], _cnt)
            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "draft", preview=_prev)
            print("[autopost_projects] черновик на утверждение:", pname); continue
        platforms = proj.get("platforms") or "both"
        results = {}
        if proj.get("channel_tg") and platforms in ("tg", "both"):
            results["tg"] = _send_telegram(proj["channel_tg"], _with_contact(post["text"], proj.get("contact_tg", "")), post.get("banner"))
        if proj.get("vk_owner_id") and platforms in ("vk", "both"):
            results["vk"] = _send_vk(int(proj["vk_owner_id"]), _with_contact(post["text"], proj.get("contact_vk", "")), post.get("banner"))
        if any(r.get("ok") for r in results.values()):
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
        if any(r.get("ok") for r in results.values()):
            _ids = {}
            _tg = results.get("tg") if isinstance(results.get("tg"), dict) else {}
            _mid = (_tg.get("result") or {}).get("message_id") if isinstance(_tg.get("result"), dict) else None
            if _mid:
                _ids["tg"] = {"chat_id": proj.get("channel_tg"), "message_id": _mid}
            _vk = results.get("vk") if isinstance(results.get("vk"), dict) else {}
            if _vk.get("post_id"):
                _ids["vk"] = {"owner_id": proj.get("vk_owner_id"), "post_id": _vk.get("post_id")}
            _save_post_record(account_id, pid, pname, post["text"], post.get("banner"), "published", ids=_ids)
            _notify_posted(account_id, pname, theme, proj, results, post.get("banner"))
        print("[autopost_projects] итог:", pname, {k: ("OK" if v.get("ok") else "FAIL") for k, v in results.items()})


def autopost_all(respect_time=False):
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
    for b in buckets:
        print("[autopost_all] бакет:", b, "| owner:", owners[b], flush=True)
        try:
            autopost_projects(b, owner=owners[b], respect_time=respect_time)
        except Exception as e:
            print("[autopost_all] ошибка по", b, ":", str(e)[:200], flush=True)




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
    from proxy_pool import get_intl_requests_proxies
    token = os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    try:
        r = requests.get("https://api.vk.com/method/wall.delete",
                         params={"access_token": token, "v": "5.199",
                                 "owner_id": str(owner_id), "post_id": post_id},
                         proxies=get_intl_requests_proxies(), timeout=30).json()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": "response" in r, "error": r.get("error")}


def _all_user_buckets():
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "posting_projects").all()
        return [r.account_id for r in rows]
    finally:
        db.close()

def due_posts_all(dry=False):
    import json as _json
    for acc in _all_user_buckets():
        db = SessionLocal()
        try:
            prow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_projects").first()
            projects = _json.loads(prow.value) if prow else []
            drow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_posts").first()
            posts = _json.loads(drow.value) if drow else []
        finally:
            db.close()
        projmap = {p.get("id"): p for p in projects}
        changed = False
        for rec in posts:
            if rec.get("status") != "draft":
                continue
            proj = projmap.get(rec.get("project_id"))
            if not proj:
                continue
            now_local = datetime.now() + timedelta(hours=int(proj.get("tz_offset", 3)))  # время по поясу проекта
            due = False
            reason = ""
            pa = (rec.get("publish_at") or "").strip()
            if pa:
                try:
                    if now_local >= datetime.strptime(pa[:16], "%Y-%m-%d %H:%M"):
                        due = True; reason = "publish_at " + pa
                except Exception:
                    pass
            if not due:
                aph = int(proj.get("auto_publish_hours") or 0)
                if aph > 0:
                    try:
                        created = datetime.strptime((rec.get("created_at") or "")[:16], "%Y-%m-%d %H:%M")
                        if now_local >= created + timedelta(hours=aph):
                            due = True; reason = "auto_publish_hours " + str(aph) + "ч от " + rec.get("created_at","")
                    except Exception:
                        pass
            if not due:
                continue
            platforms = proj.get("platforms") or "both"
            bname = rec.get("banner") or ""
            bpath = os.path.join(BANNER_DIR, bname) if bname else None
            if dry:
                tgt = []
                if proj.get("channel_tg") and platforms in ("tg","both"): tgt.append("TG " + str(proj.get("channel_tg")))
                if proj.get("vk_owner_id") and platforms in ("vk","both"): tgt.append("VK " + str(proj.get("vk_owner_id")))
                print("[due DRY]", proj.get("name"), "|", reason, "| ->", ", ".join(tgt) or "НЕТ КАНАЛА", "| банер:", bname or "нет", flush=True)
                continue
            results = {}
            if proj.get("channel_tg") and platforms in ("tg","both"):
                results["tg"] = _send_telegram(proj["channel_tg"], _with_contact(rec["text"], proj.get("contact_tg","")), bpath)
            if proj.get("vk_owner_id") and platforms in ("vk","both"):
                results["vk"] = _send_vk(int(proj["vk_owner_id"]), _with_contact(rec["text"], proj.get("contact_vk","")), bpath)
            if any(r.get("ok") for r in results.values() if isinstance(r, dict)):
                _ids = {}
                _tg = results.get("tg") if isinstance(results.get("tg"), dict) else {}
                _mid = (_tg.get("result") or {}).get("message_id") if isinstance(_tg.get("result"), dict) else None
                if _mid:
                    _ids["tg"] = {"chat_id": proj.get("channel_tg"), "message_id": _mid}
                _vk = results.get("vk") if isinstance(results.get("vk"), dict) else {}
                if _vk.get("post_id"):
                    _ids["vk"] = {"owner_id": proj.get("vk_owner_id"), "post_id": _vk.get("post_id")}
                rec["status"] = "published"; rec["ids"] = _ids
                changed = True
                _notify_posted(acc, proj.get("name") or rec.get("project_name"), rec.get("text","")[:60], proj, results, bname)
                print("[due] опубликован:", proj.get("name"), "|", reason, flush=True)
            else:
                print("[due] ОШИБКА публикации:", proj.get("name"), results, flush=True)
        if changed:
            db = SessionLocal()
            try:
                drow2 = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_posts").first()
                if drow2:
                    drow2.value = _json.dumps(posts, ensure_ascii=False); db.commit()
            finally:
                db.close()

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "demo":
        _demo(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "autopost":
        autopost(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "due_posts":
        due_posts_all(dry=("dry" in sys.argv[2:]))
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
