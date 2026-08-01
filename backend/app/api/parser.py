import os as _os_gc
_GC_MODEL = _os_gc.environ.get("GIGACHAT_MODEL", "GigaChat" + "-Pro")
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


def check_proxy_health() -> dict:
    """Проверяет живость RU-прокси (для мониторинга). Резидентный пул считается мёртвым, только
    если ВСЕ проверенные порты не отвечают - часть портов резидентного пула мертва почти всегда,
    это норма, а не повод объявлять весь пул нерабочим (раньше проверка 3 фиксированных портов
    могла попасть на 3 мёртвых из большого пула и ложно сигналить "пул мёртв").
    Возвращает {"alive": bool, "checked_ports": int, "working_ports": int}."""
    import requests as _requests
    import random as _random_ph
    from proxy_pool import PROXY_HOST, PROXY_USER, PROXY_PASS, PROXY_PORT_MIN, PROXY_PORT_MAX
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        return {"alive": False, "checked_ports": 0, "working_ports": 0, "reason": "не настроен"}
    port_range = list(range(PROXY_PORT_MIN, PROXY_PORT_MAX + 1))
    test_ports = _random_ph.sample(port_range, min(10, len(port_range)))
    working = 0
    for port in test_ports:
        px = {"http": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{port}",
              "https": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{port}"}
        try:
            r = _requests.get("https://api.ipify.org?format=json", proxies=px, timeout=10)
            if r.status_code == 200:
                working += 1
        except Exception:
            pass
    return {"alive": working > 0, "checked_ports": len(test_ports), "working_ports": working}


def check_proxy_and_alert():
    """Проверяет прокси, шлёт алерт директору в Telegram если мёртв. Вызывается по расписанию."""
    import os
    from app.telegram_bot import send_telegram_message
    result = check_proxy_health()
    chat_id = os.environ.get("DIRECTOR_CHAT_ID")
    if not result["alive"] and chat_id:
        send_telegram_message(chat_id,
            f"🔴 <b>ПРОКСИ МЁРТВ!</b>\n\n"
            f"RU-прокси (networkpw) не отвечает — проверено {result['checked_ports']} портов, "
            f"живых: {result['working_ports']}.\n\n"
            f"Анализ конкурентов и парсинг Avito сейчас НЕ РАБОТАЮТ.\n"
            f"Проверь баланс/статус на pool.networkpw.com")
    return result


def _fetch_rendered_html(url, wait_ms=3000):
    """Открывает страницу в headless-браузере, прокручивает её донизу (чтобы Tilda и подобные догрузили ленивые фото), затем возвращает HTML."""
    from proxy_pool import get_playwright_proxy
    with sync_playwright() as p:
        # ВАЖНО: без явного proxy= браузер наследует глобальные HTTP_PROXY/HTTPS_PROXY из .env
        # (это INTL-прокси для OpenAI-фолбэка, gigachat_pool.py) - Chromium не проходит его
        # basic-auth через переменные окружения и получает 407/пустую страницу на ЛЮБОМ сайте.
        # Явно даём RU-прокси (тот же, что для Avito) через Playwright-нативный proxy-конфиг.
        # RU-прокси нужен ТОЛЬКО для Avito (антибан). Обычные сайты клиентов (Tilda и пр.)
        # тянем напрямую — иначе жжём дорогой резидентский трафик и падаем, когда он кончился.
        _need_proxy = "avito.ru" in (url or "").lower()
        if _need_proxy:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox"], proxy=get_playwright_proxy()
            )
        else:
            # обычный сайт клиента — идём НАПРЯМУЮ. Глушим возможные env-прокси
            # флагом --no-proxy-server, иначе Chromium подхватит их из окружения.
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--no-proxy-server"]
            )
        page = browser.new_page(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", viewport={"width": 1366, "height": 900})
        # networkidle ненадёжен - на многих сайтах (аналитика/чат-виджеты) сеть никогда не "затихает"
        # и goto просто падает по таймауту 25с. domcontentloaded + ручной скролл ниже надёжнее.
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        # ПРОКРУТКА: Tilda грузит фото лениво — только когда товар попадает в область видимости.
        # Скроллим порциями до конца страницы, давая время догрузиться картинкам.
        try:
            # Скроллим фиксированное число шагов небольшими порциями — надёжнее чем проверка высоты
            # (у длинных Tilda-каталогов высота страницы часто не меняется, хотя контент грузится)
            for _ in range(40):
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(350)
            # ещё один проход медленнее, чтобы точно догрузить всё
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            for _ in range(25):
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(500)
        except Exception:
            pass
        html = page.content()
        browser.close()
        return html

router = APIRouter(prefix="/api/parser", tags=["parser"])

class ParseRequest(BaseModel):
    url: str
    account_id: str = "default"
    limit: int = 0  # сколько товаров выгрузить (0 = все)

def _is_tilda(soup, html_text):
    return "tildacdn.com" in html_text or soup.find(class_=lambda c: c and "t-store__card" in c)


_PRICE_CURRENCY_RE = None


def _clean_price(raw):
    """Извлекает валидную цену (число + валюта) из сырого текста карточки.
    Ищет число вплотную к символу валюты - если рядом нет такой пары (например, весь
    текст оказался виджетом рассрочки Tilda вида "р.р. NaN ₽ × 4 платежа", где "4" не
    привязано к валюте), считаем цену непарсибельной и возвращаем None, а не мусор."""
    global _PRICE_CURRENCY_RE
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return raw
    import re as _re_price
    if _PRICE_CURRENCY_RE is None:
        # 7000,00 ₽ / 7 000.00 руб / 7000₽ — берём ЦЕЛУЮ часть, копейки после , или . отбрасываем
        _PRICE_CURRENCY_RE = _re_price.compile(r"(\d[\d\s\u00a0]*\d|\d)(?:[.,]\d{2})?\s*(₽|руб\.?|\$|€)", _re_price.IGNORECASE)
    m = _PRICE_CURRENCY_RE.search(str(raw))
    if not m:
        return None
    number = _re_price.sub(r"\s+", " ", m.group(1)).strip()
    currency = "₽" if m.group(2).lower().startswith("руб") else m.group(2)
    return f"{number} {currency}"


def _find_repeating_cards(soup):
    """Ищет группу повторяющихся однотипных соседних элементов — это надёжнее, чем искать один 'подходящий' тег."""
    from collections import defaultdict
    candidates = soup.find_all(["div", "li", "article"])
    groups = defaultdict(list)
    for tag in candidates:
        classes = tag.get("class")
        if not classes:
            continue
        key = (tag.name, tuple(sorted(classes)))
        groups[key].append(tag)
    # берём самую большую группу с разумным размером карточки (не вся страница, не пустышка)
    best_group = []
    for key, items in groups.items():
        if len(items) >= 3 and len(items) > len(best_group):
            # отсекаем пустые обёртки (мало текста) и слишком большие блоки (весь layout, не карточка)
            sample_text_len = len(items[0].get_text(strip=True))
            if 20 < sample_text_len < 2000:
                best_group = items
    return best_group


class DeleteProductsRequest(BaseModel):
    account_id: str
    indices: list[int]  # индексы товаров для удаления

@router.post("/parsed_products/delete")
def delete_parsed_products(req: DeleteProductsRequest):
    """Удаляет выбранные карточки (по индексам) из parsed_products."""
    import json as _json
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "parsed_products").first()
        if not row:
            return {"status": "ok", "deleted": 0}
        data = _json.loads(row.value)
        prods = data.get("products", [])
        remaining = [p for i, p in enumerate(prods) if i not in req.indices]
        deleted_count = len(prods) - len(remaining)
        data["products"] = remaining
        data["count"] = len(remaining)
        row.value = _json.dumps(data, ensure_ascii=False)
        db.commit()
        return {"status": "ok", "deleted": deleted_count, "remaining": len(remaining)}
    finally:
        db.close()


class ProductsToDraftsRequest(BaseModel):
    account_id: str
    indices: list[int]
    address: str = ""
    topic: str = ""          # ниша для определения категории - если не задана, берём из профиля компании
    batch_label: str = ""    # название партии - чтобы потом apply_banner_to_batch нашёл её по имени

@router.post("/parsed_products/to_drafts")
def parsed_products_to_drafts(req: ProductsToDraftsRequest):
    """Превращает выбранные выгруженные карточки в черновики объявлений (готовые для фида)."""
    import json as _json, re as _re, time as _time
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.api.avito import _load_drafts, _save_drafts, detect_category_endpoint, spin

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "parsed_products").first()
        if not row:
            return {"status": "error", "message": "Нет выгруженных карточек"}
        data = _json.loads(row.value)
        products = data.get("products", [])
        selected = [(idx, products[idx]) for idx in req.indices if 0 <= idx < len(products)]
        if not selected:
            return {"status": "error", "message": "Нет валидных индексов среди выбранных карточек"}

        # Ниша аккаунта - используется как контекст группировки и как фолбэк, если явная тема (topic) не задана.
        account_niche = None
        from app.models.account import Account
        acc = db.query(Account).filter(Account.account_id == req.account_id).first()
        if acc and acc.company_niche:
            account_niche = acc.company_niche

        override_niche = req.topic.strip()
        batch_label = req.batch_label.strip() or override_niche[:60] or account_niche or "С сайта"
        batch_id = f"batch-parsed-{int(_time.time())}"

        # ГРУППИРОВКА ПО НИШАМ: в одном аккаунте могут быть товары РАЗНЫХ категорий Avito
        # (например дома из бруса + штукатурка фасадов - разные категории с разными обязательными
        # полями), хотя продаёт их один и тот же клиент. Раньше категория определялась ОДИН раз
        # на всю партию (как в create_draft_listings) - если явная тема (topic) не задана явно,
        # группируем товары по нишам через GigaChat вместо этого.
        from app.services.category_resolver import group_products_by_niche, resolve_required_fields

        selected_products = [p for _, p in selected]
        if override_niche:
            groups = [{"niche": override_niche, "indices": list(range(len(selected_products)))}]
        else:
            groups = group_products_by_niche(selected_products, account_niche)

        _TAG_TO_SNAKE = {
            "ServiceType": "service_type", "ServiceSubtype": "service_subtype",
            "WorkExperience": "work_experience", "Guarantee": "guarantee",
            "GoodsType": "goods_type", "GoodsSubType": "goods_subtype",
            "Condition": "condition", "KitchenType": "kitchen_type",
            "PriceType": "price_type", "Color": "color", "AdType": "ad_type",
        }

        new_drafts = []
        clarifications_needed = []
        categories_used = []
        category_errors = []

        for group in groups:
            group_niche = group["niche"]
            group_items = [selected[i] for i in group["indices"]]  # список (idx, p)

            cat_data = detect_category_endpoint({"account_id": req.account_id, "niche": group_niche})
            category = cat_data.get("category") if cat_data.get("status") == "ok" else "Предложение услуг"

            req_fields = resolve_required_fields(category_id=group_niche, niche=group_niche, api_category=category)
            if req_fields.get("status") != "ok":
                # Категория/шаблон не резолвится (например недоступен парсер документации) -
                # НЕ создаём черновики вслепую с пустыми обязательными полями, как раньше.
                category_errors.append({
                    "niche": group_niche,
                    "items": [p.get("title", "товар") for _, p in group_items],
                    "message": req_fields.get("message", "не удалось определить обязательные поля категории"),
                })
                continue
            if req_fields.get("needs_clarification"):
                clarifications_needed.append({
                    "niche": group_niche,
                    "items": [p.get("title", "товар") for _, p in group_items],
                    "questions": [q["question"] for q in req_fields["needs_clarification"]],
                })
                continue  # эту группу не создаём вслепую - ждём уточнения от клиента

            categories_used.append(category)
            extra_fields = {}
            extra_params = {}
            for tag, val in req_fields.get("resolved", {}).items():
                snake = _TAG_TO_SNAKE.get(tag)
                if snake:
                    extra_fields[snake] = val
                else:
                    extra_params[tag] = val

            for idx, p in group_items:
                title = (p.get("title") or "Товар")[:100]
                price_raw = p.get("price") or "0"
                price_digits = _re.sub(r"[^\d]", "", price_raw.split("×")[0])  # отсекаем рассрочку "NaN ₽ × 4"
                price = int(price_digits) if price_digits.isdigit() else 0

                # Предпочитаем сгенерированное ИИ-описание (уже со спинтакс-уникализацией) -
                # раньше оно копилось в карточке кнопкой "Сгенерировать ИИ-описание", но сюда
                # никогда не попадало, и весь труд уникализации терялся при отправке в черновики.
                ai_desc = p.get("ai_description")
                if ai_desc:
                    try:
                        description = spin(ai_desc)
                    except Exception:
                        description = ai_desc
                else:
                    chars = p.get("characteristics") or {}
                    chars_text = "\n".join([f"{k}: {v}" for k, v in chars.items()])
                    description = (p.get("description") or "") + ("\n\n" + chars_text if chars_text else "")
                if not description.strip():
                    description = title

                images = [p["image"]] if p.get("image") else []
                images += [im for im in p.get("images", []) if im not in images]

                new_drafts.append({
                    "id": f"parsed_{req.account_id}_{idx}_{int(_time.time())}",
                    "batch_id": batch_id,
                    "batch_label": batch_label,
                    "title": title,
                    "description": description[:3000],
                    "price": price,
                    "category": category,
                    "category_id": group_niche,
                    **extra_fields,
                    "address": req.address or "",
                    "images": images[:10],
                    "params": dict(extra_params),
                    "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                })

        if not new_drafts:
            if clarifications_needed:
                status, message = "needs_clarification", "Нужны уточнения от клиента перед созданием черновиков"
            elif category_errors:
                status, message = "error", "Не удалось определить обязательные поля категории - черновики не созданы"
            else:
                status, message = "error", "Не удалось создать ни одного черновика"
            return {
                "status": status,
                "message": message,
                "clarifications_needed": clarifications_needed,
                "category_errors": category_errors,
            }

        # добавляем к существующим черновикам аккаунта
        existing = _load_drafts(req.account_id)
        combined = existing + new_drafts
        _save_drafts(req.account_id, combined)

        return {
            "status": "ok", "created": len(new_drafts), "total_drafts": len(combined),
            "batch_label": batch_label, "categories": categories_used,
            "clarifications_needed": clarifications_needed,
            "category_errors": category_errors,
        }
    finally:
        db.close()


class FetchGalleryRequest(BaseModel):
    account_id: str
    product_idx: int

@router.post("/parsed_products/fetch_gallery")
def fetch_gallery_for_product(req: FetchGalleryRequest):
    """Собирает полную галерею фото ОДНОГО товара (по кнопке на карточке) и сохраняет в parsed_products."""
    import json as _json
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "parsed_products").first()
        if not row:
            return {"status": "error", "message": "Нет карточек"}
        data = _json.loads(row.value)
        prods = data.get("products", [])
        if req.product_idx < 0 or req.product_idx >= len(prods):
            return {"status": "error", "message": "Неверный индекс"}
        prod = prods[req.product_idx]
        gallery_urls, authoritative_price = _fetch_product_gallery(prod.get("product_url", ""), max_photos=10)
        downloaded = []
        for g_idx, g_url in enumerate(gallery_urls):
            local_path = _download_product_photo(req.account_id, g_url, f"{req.product_idx}_g{g_idx}")
            downloaded.append(local_path)
        changed = bool(downloaded) or bool(authoritative_price)
        if downloaded:
            prod["images"] = downloaded
        if authoritative_price:
            prod["price"] = authoritative_price
        if changed:
            prods[req.product_idx] = prod
            data["products"] = prods
            row.value = _json.dumps(data, ensure_ascii=False)
            db.commit()
        return {"status": "ok", "count": len(downloaded), "images": downloaded, "price": prod.get("price")}
    finally:
        db.close()


@router.get("/parsed_products")
def get_parsed_products(account_id: str = "default"):
    """Отдаёт выгруженные с сайта карточки по аккаунту."""
    import json as _json
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "parsed_products").first()
        if not row:
            return {"status": "ok", "products": [], "source_url": None, "parsed_at": None}
        data = _json.loads(row.value)
        return {"status": "ok", "products": data.get("products", []), "source_url": data.get("source_url"),
                "source_urls": data.get("source_urls") or ([data["source_url"]] if data.get("source_url") else []),
                "parsed_at": data.get("parsed_at"), "count": data.get("count", 0)}
    finally:
        db.close()


@router.post("/parse")
def parse_site(req: ParseRequest):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        response = requests.get(req.url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "lxml")

        products = []
        platform = "tilda" if _is_tilda(soup, response.text) else "generic"

        if platform == "tilda":
            # Tilda: известные классы карточек магазина
            candidate_tags = soup.find_all(lambda t: t.get("class") and (
                ("t-store__card" in t.get("class") or "js-product" in t.get("class"))
            ))
        else:
            # CS-Cart / Битрикс-подобные: явный класс карточки товара
            cs_cards = soup.find_all(class_="products-view-item")
            cs_cards = [c for c in cs_cards if "products-view-block--is-related" not in (c.get("class") or [])]
            if cs_cards:
                candidate_tags = cs_cards
                platform = "cscart"
            else:
                candidate_tags = _find_repeating_cards(soup)
                if not candidate_tags:
                    # запасной вариант — старая эвристика, если повторяющихся карточек не нашлось
                    candidate_tags = soup.find_all(["article", "div", "li", "section"])

        for tag in candidate_tags:
            title = None
            price = None
            image = None
            description = None

            # Сначала пробуем Schema.org микроразметку (itemprop) — надёжнее всего
            name_el = tag.find(attrs={"itemprop": lambda v: v and "name" in v})
            if name_el and name_el.text.strip():
                title = name_el.text.strip()

            product_url = None
            def _good_href(h):
                if not h or not isinstance(h, str):
                    return False
                h = h.strip()
                if h.startswith("#") or h.lower().startswith("javascript:") or h.lower().startswith("mailto:") or h.lower().startswith("tel:"):
                    return False
                return h.startswith("http") or h.startswith("/")

            url_el = tag.find(attrs={"itemprop": "url"})
            if url_el and _good_href(url_el.get("href")):
                product_url = url_el.get("href")
            else:
                for a in tag.find_all("a", href=True):
                    if _good_href(a.get("href")):
                        product_url = a.get("href")
                        break

            price_el = tag.find(attrs={"itemprop": "price"})
            if price_el:
                # content="7000.00" — приоритет; текст берём только если content пуст
                price_val = (price_el.get("content") or "").strip() or price_el.text.strip()
                low = price_val.lower()
                if price_val and "nan" not in low and "платеж" not in low and "рассрочк" not in low:
                    # отбрасываем копейки: 7000.00 / 7000,00 -> 7000
                    import re as _rp
                    m_int = _rp.match(r"\s*(\d[\d\s\u00a0]*)(?:[.,]\d{1,2})?\s*$", price_val)
                    if m_int:
                        price_val = m_int.group(1).strip()
                    price = price_val + " ₽"
            img_el = tag.find(attrs={"itemprop": "image"})
            if img_el:
                _cand = img_el.get("src") or img_el.get("data-src")
                if _cand and "/-/empty/" not in _cand:
                    image = _cand

            # Запасной вариант — старая эвристика, если Schema.org не нашлось
            if not title:
                for h in tag.find_all(["h1","h2","h3","h4"]):
                    if h.text.strip() and len(h.text.strip()) > 3:
                        title = h.text.strip()
                        break
            if not price:
                for p in tag.find_all(["span","div","p"]):
                    text = p.text.strip()
                    if any(c in text for c in ["₽","руб","$","€"]) and len(text) < 50 and "платеж" not in text.lower() and "рассрочк" not in text.lower():
                        price = text
                        break
            
            # Ищем ВСЕ картинки карточки (не только первую)
            images = []
            # Tilda: САМЫЙ надёжный источник — data-product-img на контейнере карточки (сразу реальный URL, без /-/empty/)
            dpi_tag = tag if tag.get("data-product-img") else tag.find(attrs={"data-product-img": True})
            if dpi_tag:
                dpi = dpi_tag.get("data-product-img")
                if dpi and "/-/empty/" not in dpi:
                    if dpi.startswith("//"): dpi = "https:" + dpi
                    images.append(dpi)
            for bg in tag.find_all(attrs={"data-original": True}):
                bgsrc = bg.get("data-original")
                if bgsrc and not bgsrc.startswith("data:") and "/-/empty/" not in bgsrc:
                    if bgsrc.startswith("//"): bgsrc = "https:" + bgsrc
                    if bgsrc not in images: images.append(bgsrc)
            for img in tag.find_all("img"):
                src = (img.get("src") or img.get("data-src") or img.get("data-lazy")
                       or img.get("data-original") or img.get("data-tu-lazy"))
                # Tilda часто прячет реальное фото в data-original или в родительском div с background-image
                if not src:
                    parent = img.find_parent(attrs={"data-original": True})
                    if parent:
                        src = parent.get("data-original")
                if src and not src.startswith("data:") and "/-/empty/" not in src and src not in images:
                    if src.startswith("//"):
                        src = "https:" + src
                    images.append(src)
            _real = [im for im in images if "/-/empty/" not in im]
            if _real:
                image = _real[0]
            elif not image or "/-/empty/" in (image or ""):
                image = images[0] if images else None

            # Ищем описание
            for p in tag.find_all("p"):
                if p.text.strip() and len(p.text.strip()) > 20:
                    description = p.text.strip()[:300]
                    break

            # Ищем СТРУКТУРИРОВАННЫЕ характеристики: таблицы, dl/dt/dd, li с двоеточием
            characteristics = {}
            for table in tag.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["td", "th"])
                    if len(cells) == 2:
                        key = cells[0].text.strip()
                        val = cells[1].text.strip()
                        if key and val and len(key) < 60 and len(val) < 200:
                            characteristics[key] = val
            for dl in tag.find_all("dl"):
                dts = dl.find_all("dt")
                dds = dl.find_all("dd")
                for dt, dd in zip(dts, dds):
                    key = dt.text.strip()
                    val = dd.text.strip()
                    if key and val and len(key) < 60 and len(val) < 200:
                        characteristics[key] = val
            for li in tag.find_all("li"):
                text = li.text.strip()
                if ":" in text and len(text) < 150:
                    key, _, val = text.partition(":")
                    key, val = key.strip(), val.strip()
                    if key and val and len(key) < 60:
                        characteristics[key] = val

            # CS-Cart: если стандартные селекторы не дали результат — берём из его классов
            if platform == "cscart":
                if not title:
                    _n = tag.find(class_="products-view-name")
                    if _n: title = _n.get_text(strip=True)
                if not price:
                    _p = tag.find(class_="products-view-price")
                    if _p: price = _p.get_text(strip=True)
                if not image:
                    _img = tag.find("img")
                    if _img: image = _img.get("src") or _img.get("data-src")
                if not product_url:
                    _a = tag.find("a", href=True)
                    if _a: product_url = _a.get("href")

            price = _clean_price(price)
            if title and (price or image):
                if product_url and product_url.startswith("/"):
                    from urllib.parse import urljoin
                    product_url = urljoin(req.url, product_url)
                # дедупликация по URL (CS-Cart дублирует карточки в table+tile виде)
                if product_url and any(x.get("product_url") == product_url for x in products):
                    continue
                products.append({
                    "title": title,
                    "price": price,
                    "image": image,
                    "images": images[:10],
                    "description": description,
                    "characteristics": characteristics,
                    "product_url": product_url
                })
                # лимит выгрузки — стоп после N товаров
                if req.limit and len(products) >= req.limit:
                    break
        
        # Убираем дубли
        seen = set()
        unique = []
        for p in products:
            if p["title"] not in seen:
                seen.add(p["title"])
                unique.append(p)

        rendered = False
        # Считаем, у скольких товаров реальное фото (не плейсхолдер Tilda)
        _real_photos = sum(1 for p in unique if p.get("image") and "/-/empty/" not in (p.get("image") or ""))
        _need_browser_for_photos = (platform == "tilda" and len(unique) > 0 and _real_photos < len(unique) * 0.6)
        # Браузерный рендер: если статика ничего не дала ИЛИ Tilda-каталог с ленивыми фото (нужен скролл)
        if len(unique) == 0 or _need_browser_for_photos:
            try:
                rendered_html = _fetch_rendered_html(req.url)
                soup2 = BeautifulSoup(rendered_html, "lxml")
                platform2 = "tilda" if _is_tilda(soup2, rendered_html) else "generic"
                if platform2 == "tilda":
                    candidate_tags2 = soup2.find_all(lambda t: t.get("class") and (
                "t-store__card" in t.get("class") or "js-product" in t.get("class") or "js-store-prod-wrapper" in t.get("class")
            ))
                else:
                    candidate_tags2 = _find_repeating_cards(soup2)

                for tag in candidate_tags2:
                    title = None
                    price = None
                    image = None
                    name_el = tag.find(attrs={"itemprop": lambda v: v and "name" in v})
                    if name_el and name_el.text.strip():
                        title = name_el.text.strip()
                    price_el = tag.find(attrs={"itemprop": "price"})
                    if price_el:
                        price_val = price_el.get("content") or price_el.text.strip()
                        if price_val and "nan" not in price_val.lower() and "платеж" not in price_val.lower() and "рассрочк" not in price_val.lower():
                            price = price_val.strip() + " ₽"
                    # Tilda: сначала пробуем самый надёжный источник — data-product-img на контейнере
                    dpi_tag2 = tag if tag.get("data-product-img") else tag.find(attrs={"data-product-img": True})
                    if dpi_tag2 and dpi_tag2.get("data-product-img"):
                        _dpi = dpi_tag2.get("data-product-img")
                        if "/-/empty/" not in _dpi:
                            image = ("https:" + _dpi) if _dpi.startswith("//") else _dpi
                    img_el = tag.find(attrs={"itemprop": "image"}) or tag.find("img")
                    if img_el and not image:
                        _cand = img_el.get("src") or img_el.get("data-src")
                        if _cand and "/-/empty/" not in _cand:
                            image = _cand
                    product_url2 = None
                    url_el2 = tag.find(attrs={"itemprop": "url"})
                    if url_el2 and url_el2.get("href"):
                        product_url2 = url_el2.get("href")
                    elif tag.find("a", href=True):
                        product_url2 = tag.find("a", href=True).get("href")
                    if product_url2 and product_url2.startswith("/"):
                        from urllib.parse import urljoin
                        product_url2 = urljoin(req.url, product_url2)

                    # Tilda-специфичные классы карточки магазина (когда itemprop не используется)
                    if not title:
                        name_el2 = tag.find(class_=lambda c: c and ("t-store__card__title" in c or "js-product-name" in c))
                        if name_el2 and name_el2.text.strip():
                            title = name_el2.text.strip()
                    if not price:
                        price_el2 = tag.find(class_=lambda c: c and "t-store__card__price-value" in c)
                        if price_el2 and price_el2.text.strip():
                            price = price_el2.text.strip() + " ₽"
                    if not title:
                        for h in tag.find_all(["h1","h2","h3","h4"]):
                            if h.text.strip() and len(h.text.strip()) > 3:
                                title = h.text.strip()
                                break
                    if not price:
                        for p2 in tag.find_all(["span","div","p"]):
                            text = p2.text.strip()
                            if any(c in text for c in ["₽","руб","$","€"]) and len(text) < 50:
                                price = text
                                break
                    price = _clean_price(price)
                    if title and (price or image):
                        images_all = [img.get("src") or img.get("data-src") for img in tag.find_all("img") if img.get("src") or img.get("data-src")]
                        images_all = [im for im in images_all if "/-/empty/" not in im]
                        if not image and images_all:
                            image = images_all[0]
                        unique.append({"title": title, "price": price, "image": image, "images": images_all[:10], "description": None, "characteristics": {}, "product_url": product_url2})
                seen2 = set()
                dedup = []
                for p3 in unique:
                    if p3["title"] not in seen2:
                        seen2.add(p3["title"])
                        dedup.append(p3)
                unique = dedup
                platform = platform2
                rendered = True
            except Exception as _render_e:
                # Раньше ошибка глушилась молча - found:0 выглядел как "сайт пустой",
                # хотя браузерный рендер мог падать по совсем другой причине (прокси, таймаут).
                print(f"[parse] браузерный рендер {req.url} не удался: {_render_e}", flush=True)

        # Скачиваем главное фото на диск (быстро). Полную галерею (неск. фото) собираем ПО ЗАПРОСУ — кнопкой на карточке,
        # т.к. требует браузер на каждый товар и для всех 50 сразу заняло бы 5-8 минут.
        if unique:
            for idx, prod in enumerate(unique[:50]):
                if prod.get("image"):
                    prod["image"] = _download_product_photo(req.account_id, prod["image"], idx)
                    prod["images"] = [prod["image"]]
            _save_parsed_products(req.account_id, req.url, unique[:50])
        return {
            "url": req.url,
            "platform": platform,
            "rendered_with_browser": rendered,
            "found": len(unique),
            "products": unique[:50]
        }
    
    except Exception as e:
        return {"error": str(e), "url": req.url}


def _collect_product_links(html, page_url, max_links=60):
    """Со страницы РАЗДЕЛА собирает ссылки на карточки товаров."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse

    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(page_url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(page_url, href)
        if urlparse(full).netloc != base_host:
            continue
        # признак карточки товара: /view/<id> или /product/<id>
        low = full.lower()
        if ("/view/" in low or "/product/" in low or "/tovar/" in low) and full not in links:
            links.append(full)
        if len(links) >= max_links:
            break
    return links


def _is_catalog_page(url):
    low = (url or "").lower()
    return ("/index/" in low or "/catalog/" in low) and "/view/" not in low


def _parse_via_generic(url: str):
    """Фолбэк для сайтов вне Tilda/Schema.org. Понимает и карточку товара, и раздел каталога."""
    html, final_url = _fetch_html_tolerant(url)
    if not html:
        return None

    # Если это раздел — обходим все товары внутри
    if _is_catalog_page(final_url or url):
        base = final_url or url
        links = _collect_product_links(html, base)

        # Мало товаров на странице? Значит это раздел с ПОДРАЗДЕЛАМИ — спускаемся глубже
        if len(links) < 10:
            from bs4 import BeautifulSoup as _BS
            from urllib.parse import urljoin as _uj, urlparse as _up
            _soup = _BS(html, "html.parser")
            _host = _up(base).netloc
            subs = []
            for a in _soup.find_all("a", href=True):
                full = _uj(base, a["href"].strip())
                if _up(full).netloc != _host:
                    continue
                if "/index/" in full.lower() and full != base and full not in subs:
                    subs.append(full)
            print(f"[generic] подразделов найдено: {len(subs)}")
            for sub in subs[:25]:
                try:
                    hs, us = _fetch_html_tolerant(sub)
                    if not hs:
                        continue
                    for l in _collect_product_links(hs, us or sub):
                        if l not in links:
                            links.append(l)
                except Exception as e:
                    print(f"[generic] подраздел {sub} упал: {e}")

        print(f"[generic] раздел: итого ссылок на товары: {len(links)}")
        products = []
        for link in links:
            try:
                h2, u2 = _fetch_html_tolerant(link)
                if not h2:
                    continue
                p = _extract_generic_product(h2, u2 or link)
                if p and p.get("title"):
                    products.append(p)
            except Exception as e:
                print(f"[generic] товар {link} упал: {e}")
        return products if products else None

    # Иначе — обычная карточка товара
    return _extract_generic_product(html, final_url or url)

class CityAnalysisRequest(BaseModel):
    query: str
    cities: list[str]


async def _fetch_avito_page_headed(url: str, timeout_ms: int = 25000, wait_ms: int = 5000):
    """
    Загружает страницу Avito через headed-Chromium в Xvfb + резидентный прокси + stealth.
    Это единственный проверенно рабочий способ обойти firewallCaptcha (см. память проекта, 03.07.2026).
    Требует, чтобы процесс был запущен под xvfb-run (см. systemd unit).
    """
    from proxy_pool import get_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    proxy = get_playwright_proxy()
    stealth = Stealth()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"], proxy=proxy)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        await stealth.apply_stealth_async(context)
        page = await context.new_page()

        # Блокируем картинки/стили/шрифты/медиа - нам нужен только текст объявлений
        # (заголовки, цены), это снижает трафик в 5-10 раз и себестоимость парсинга.
        async def _block_heavy_resources(route):
            if route.request.resource_type in ("image", "stylesheet", "font", "media", "script"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_heavy_resources)

        _traffic = {"bytes": 0, "requests": 0}
        async def _ct(response):
            try:
                b = await response.body()
                _traffic["bytes"] += len(b)
                _traffic["requests"] += 1
            except Exception:
                pass
        page.on("response", lambda r: __import__("asyncio").ensure_future(_ct(r)))
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        await page.wait_for_timeout(wait_ms)
        html = await page.content()
        final_url = page.url
        _mb = _traffic["bytes"] / 1024 / 1024
        _rub = _mb / 1024 * 4 * 95
        print(f"[ТРАФИК] Парсинг выдачи: {_mb:.2f} МБ, {_traffic['requests']} запросов, ~{_rub:.2f} руб (RU прокси $4/ГБ)", flush=True)
        await browser.close()
        return {"html": html, "final_url": final_url}


def _extract_total_count(html: str):
    """Достаёт общее число объявлений в выдаче (счётчик Avito), не только топ-выборку."""
    import re
    m = re.search(r'data-marker="page-title/count">(.*?)<', html)
    if not m:
        return None
    raw = m.group(1)
    digits = re.sub(r'&nbsp;|\xa0|\s', '', raw)
    digits = re.sub(r'[^0-9]', '', digits)
    return int(digits) if digits else None


def _save_market_stat(query: str, city: str, total_count: int):
    """Сохраняет точку истории: сколько объявлений было в выдаче по нише/городу на эту дату.
    Копится по месяцам - используется для отслеживания динамики конкуренции
    (рост числа = усиление конкуренции, может объяснять падение лидов у клиента)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import datetime

    month_key = datetime.now().strftime("%Y-%m")
    date_key = datetime.now().strftime("%Y-%m-%d")
    key = f"market_stats:{city}:{query}"
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.key == key).first()
        history = _json.loads(row.value) if row else []
        # одна точка на месяц - если уже есть запись за этот месяц, обновляем её (свежее число)
        existing = next((h for h in history if h.get("month") == month_key), None)
        if existing:
            existing["total_count"] = total_count
            existing["date"] = date_key
        else:
            history.append({"month": month_key, "date": date_key, "total_count": total_count})
        history = sorted(history, key=lambda h: h["month"])[-24:]  # храним максимум 2 года
        raw = _json.dumps(history, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            row = Storage(account_id="_global_parser", key=key, value=raw)
            db.add(row)
        db.commit()
    finally:
        db.close()


def _extract_listing_items(html: str) -> list:
    """Извлекает название/цену/ссылку каждого объявления со страницы поиска."""
    soup = BeautifulSoup(html, "lxml")
    items = []

    title_els = soup.find_all(attrs={"data-marker": "item-title"})
    for t in title_els:
        title = t.get_text(strip=True)
        link = t.get("href")
        if link and link.startswith("/"):
            link = "https://www.avito.ru" + link

        # Цена ищется в ближайшем родительском блоке объявления
        price = None
        parent = t.find_parent(attrs={"data-marker": "item"})
        if parent:
            price_el = parent.find(attrs={"data-marker": "item-price"})
            if price_el:
                nums = "".join(c for c in price_el.get_text() if c in "0123456789")
                if nums:
                    price = int(nums)

        if title:
            items.append({"title": title, "price": price, "url": link})

    return items


async def _fetch_item_detail_headed(url: str, max_attempts: int = 3):
    """Заходит на страницу объявления той же рабочей связкой, вытаскивает описание и фото.
    При капче или пустом результате повторяет с новым IP из пула (до max_attempts раз)."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            page_data = await _fetch_avito_page_headed(url, timeout_ms=20000, wait_ms=3000)
            html = page_data["html"]

            # Если поймали капчу на этом IP — пробуем другой
            if "firewallCaptcha" in html or "geetest_captcha" in html:
                last_error = "captcha"
                continue

            soup = BeautifulSoup(html, "lxml")

            # Количество фото — по превью галереи (data-marker="image-preview/item"),
            # это надёжнее, чем itemprop=image (который на части объявлений не срабатывает)
            preview_count = len(soup.find_all(attrs={"data-marker": "image-preview/item"}))
            if preview_count == 0:
                seen = set()
                for img_el in soup.find_all(attrs={"itemprop": "image"}):
                    src = img_el.get("src") or img_el.get("content") or img_el.get("data-src")
                    if src:
                        seen.add(src)
                preview_count = len(seen)

            description = None
            best_len = 0
            for p in soup.find_all(["p", "div"], attrs={"itemprop": "description"}):
                text = p.get_text(strip=True)
                if len(text) > best_len:
                    description = text[:2000]
                    best_len = len(text)

            # Если описание не извлеклось, но и капчи нет — возможно, у объявления
            # правда нет текста; отдаём что есть, не тратим лишние попытки
            return {"images_count": preview_count, "description": description}

        except Exception as e:
            last_error = str(e)[:150]
            continue

    return {"images_count": None, "description": None, "error": last_error}


def _summarize_advantages(title: str, description: str) -> str:
    """Через GigaChat кратко выделяет ключевые преимущества из описания конкурента."""
    if not description:
        return "Описание недоступно"
    try:
        from gigachat_pool import chat_with_fallback
        from gigachat.models import Messages, MessagesRole
        from app.api.chat import GIGACHAT_KEY

        prompt = (
            f"Товар: {title}\n\nОписание продавца:\n{description[:1500]}\n\n"
            "В 1-2 коротких предложениях, что продавец подаёт как главные преимущества "
            "этого товара/услуги? Только суть, без вступлений."
        )
        return chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            model=_GC_MODEL, credentials=GIGACHAT_KEY
        ).strip()
    except Exception:
        return (description[:200] + "...") if len(description) > 200 else description


def _fetch_product_gallery(product_url: str, max_photos: int = 10) -> tuple:
    """Заходит на страницу товара ЧЕРЕЗ БРАУЗЕР (страница на JS, requests её не видит) и собирает
    полную галерею фото + (для Tilda) авторитетную цену из той же JS-переменной.
    Возвращает (gallery: list, price: str|None)."""
    from bs4 import BeautifulSoup as _BS
    # защита: product_url может прийти списком или мусорным якорем (#order, javascript:void)
    if isinstance(product_url, (list, tuple)):
        product_url = next((u for u in product_url if isinstance(u, str) and u.startswith("http")), "")
    if not isinstance(product_url, str):
        product_url = str(product_url or "")
    product_url = product_url.strip()
    if not product_url or not product_url.startswith("http"):
        print(f"[gallery] пропуск: некорректный product_url = {product_url!r}", flush=True)
        return [], None

    def _is_junk(url: str) -> bool:
        junk_markers = ["tildacopy", "mc.yandex.ru", "yastatic", "google-analytics", "facebook.com/tr",
                        "logo", "favicon", "pixel", "watch/"]
        low = url.lower()
        return any(m in low for m in junk_markers)

    try:
        html = _fetch_rendered_html(product_url, wait_ms=2500)

        # ПРИОРИТЕТНЫЙ ИСТОЧНИК для Tilda: у товара в HTML есть встроенная JS-переменная
        # var product = {...,"price":"...",...,"gallery":[{"img":"url"},...],"sort":...} - это
        # структурированные данные ИМЕННО этого товара, изолированно от блоков "рекомендуем"/
        # "похожие товары" на той же странице (у них своя, отдельная структура данных) и без
        # риска зацепить текст виджета рассрочки, который загрязняет цену при скрейпинге DOM.
        import re as _re_gallery, json as _json_gallery
        price = None
        price_match = _re_gallery.search(
            r'"price"\s*:\s*"?(\d+)(?:\.\d+)?"?[^{}]*?"gallery"\s*:\s*(\[.*?\])\s*,\s*"sort"',
            html, _re_gallery.DOTALL,
        )
        gmatch = price_match or _re_gallery.search(r'"gallery"\s*:\s*(\[.*?\])\s*,\s*"sort"', html)
        if price_match:
            price = _clean_price(price_match.group(1) + " ₽")
        if gmatch:
            try:
                gallery_json = gmatch.group(2) if price_match else gmatch.group(1)
                items = _json_gallery.loads(gallery_json)
                gallery = []
                for it in items:
                    src = it.get("img") if isinstance(it, dict) else None
                    if src and src not in gallery:
                        gallery.append(src)
                if gallery:
                    return gallery[:max_photos], price
            except Exception as _ge:
                print(f"[parse] не удалось разобрать JSON-галерею Tilda {product_url}: {_ge}")

        soup = _BS(html, "lxml")
        gallery = []

        # CS-Cart / Битрикс: реальные фото товара лежат в /pictures/product/.
        # Собираем их, приводим к крупному размеру и дедупим по номеру фото
        # (86664_xsmall и 86664_middle — одно и то же фото в разных размерах).
        import re as _re_cs
        cs_seen = set()
        cs_gallery = []
        # ограничиваем поиск галереей самого товара, а не всей страницей
        # (чтобы не зацепить фото из блоков "рекомендуем"/"похожие товары")
        cs_scope = (soup.find(class_="gallery-photos") or soup.find(class_="products-view-pictures")
                    or soup.find(class_=_re_cs.compile("product.*gallery|gallery.*product", _re_cs.I))
                    or soup.find(attrs={"class": _re_cs.compile("detailed|product-image", _re_cs.I)}))
        cs_search_root = cs_scope if cs_scope else soup
        for tag in cs_search_root.find_all(["img", "a"]):
            src = tag.get("src") or tag.get("data-src") or tag.get("href") or ""
            if "/pictures/product/" not in src:
                continue
            if src.startswith("//"): src = "https:" + src
            elif src.startswith("/"):
                from urllib.parse import urljoin as _uj
                src = _uj(product_url, src)
            # приводим к крупному размеру
            big = _re_cs.sub(r"/(xsmall|small|middle|thumbnails)/", "/big/", src)
            big = _re_cs.sub(r"_(xsmall|small|middle)\.", "_big.", big)
            # ключ дедупа — номер фото без размера
            m_num = _re_cs.search(r"/(\d+)_[a-z]+\.", src)
            key = m_num.group(1) if m_num else big
            if key in cs_seen:
                continue
            cs_seen.add(key)
            cs_gallery.append(big)
        if cs_gallery:
            return cs_gallery[:max_photos], price

        # ФОЛБЭК для не-Tilda сайтов (или если JSON-галерея выше не нашлась): реальные фото
        # товара всегда на optim.tildacdn.com с /-/contain/ (превью нужного размера);
        # плейсхолдеры /-/empty/ и мусорные иконки/трекеры отсекаем
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original")
            if not src or "/-/empty/" in src or src.startswith("data:") or _is_junk(src):
                continue
            if src.startswith("//"): src = "https:" + src
            if src not in gallery:
                gallery.append(src)
        return gallery[:max_photos], price
    except Exception as e:
        print(f"[parse] ошибка сбора галереи {product_url}: {e}")
        return [], None


class ValidateFeedRequest(BaseModel):
    feed_url: str

@router.post("/validate_feed")
async def validate_feed_endpoint(req: ValidateFeedRequest):
    """Прогоняет фид через официальный валидатор Avito и возвращает результат."""
    result = await validate_feed_xmlcheck(req.feed_url)
    return result


async def validate_feed_xmlcheck(feed_url: str) -> dict:
    """Автоматически проверяет фид через официальный валидатор Avito (autoload.avito.ru/format/xmlcheck/)."""
    from playwright.async_api import async_playwright

    result = {"status": "unknown", "errors": [], "raw_text": ""}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
            await page.goto("https://autoload.avito.ru/format/xmlcheck/", timeout=40000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # переключаемся на вкладку "По ссылке"
            await page.click("text=По ссылке")
            await page.wait_for_timeout(500)
            debug_step0 = await page.inner_text("body")

            # вводим URL фида в поле
            input_field = page.locator("input[placeholder*='your_site'], input[type='text']").first
            await input_field.click()
            await input_field.fill(feed_url)
            await input_field.press("Tab")  # снимаем фокус, чтобы форма поняла что ввод завершён
            await page.wait_for_timeout(500)
            debug_filled_value = await input_field.input_value()

            # диагностика: смотрим ВСЕ кнопки/элементы с текстом "Проверить" и их видимость
            all_check_btns = page.get_by_text("Проверить", exact=True)
            btn_count = await all_check_btns.count()
            btn_info = []
            visible_idx = None
            for i in range(btn_count):
                el = all_check_btns.nth(i)
                vis = await el.is_visible()
                el_id = await el.get_attribute("id") or ""
                btn_info.append(f"[{i}] visible={vis} id={el_id}")
                if vis and visible_idx is None:
                    visible_idx = i
            debug_btn_state = f"count={btn_count} | " + " | ".join(btn_info)

            if visible_idx is not None:
                await all_check_btns.nth(visible_idx).click(force=True)
            elif btn_count > 0:
                await all_check_btns.first.click(force=True)
            await page.wait_for_timeout(3000)
            debug_step2 = await page.inner_text("body")

            # ждём появления отчёта, проверяя каждую секунду видимый текст страницы (надёжнее чем wait_for_selector,
            # т.к. на странице есть скрытый дубль этого текста на неактивной вкладке)
            report_appeared = False
            for _ in range(60):
                cur_text = await page.inner_text("body")
                if "Отчёт о проверке" in cur_text and ("Общий статус" in cur_text):
                    report_appeared = True
                    break
                await page.wait_for_timeout(1500)
            if not report_appeared:
                result["raw_text"] = f"[поле после fill: {debug_filled_value}]\n[кнопка: {debug_btn_state}]\n\n[текст на момент таймаута]:\n{(await page.inner_text('body'))[:1500]}"
                result["errors"].append("report did not appear within 90s")
                await browser.close()
                return result
            await page.wait_for_timeout(1000)

            body_text = await page.inner_text("body")
            result["raw_text"] = body_text

            if "XML соответствует формату" in body_text:
                result["status"] = "ok"
            elif "не соответствует формату" in body_text:
                result["status"] = "errors"
            else:
                result["status"] = "unknown"

            await browser.close()
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))

    return result


def _download_product_photo(account_id: str, image_url: str, product_idx: int) -> str:
    """Скачивает фото товара на диск в IMAGES_DIR/{account_id}/parsed/ и возвращает локальный URL."""
    import requests as _requests
    import hashlib, os
    if not image_url or not image_url.startswith("http"):
        return image_url
    try:
        folder = f"/root/BORIS/backend/images/{account_id}/parsed"
        os.makedirs(folder, exist_ok=True)
        ext = ".jpg"
        for e in [".png", ".jpeg", ".webp", ".jpg"]:
            if e in image_url.lower():
                ext = e if e != ".jpeg" else ".jpg"
                break
        name_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
        filename = f"product_{product_idx}_{name_hash}{ext}"
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            resp = _requests.get(image_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            else:
                return image_url
        return f"/images/{account_id}/parsed/{filename}"
    except Exception as e:
        print(f"[parse] ошибка скачивания фото: {e}")
        return image_url


def _generate_product_description(title: str, price: str, characteristics: dict, sample: str = "") -> str:
    """Генерирует продающее описание товара для Avito через GigaChat, на основе выгруженных данных."""
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole
    import os

    chars_text = "\n".join([f"- {k}: {v}" for k, v in list(characteristics.items())[:8]]) if characteristics else ""

    sample_block = ""
    _smp = (sample or "").strip()
    if _smp:
        sample_block = (
            "\nОБРАЗЕЦ ОТ КЛИЕНТА — бери отсюда ОБЩУЮ информацию о компании (условия доставки, "
            "гарантии, преимущества, контакты) и МАНЕРУ ПИСЬМА. Конкретику по товару, характеристики "
            "и ключевые слова — пиши СВОИ под этот товар, СКОПИРУЙ структуру эталона один в один, меняя только товарную часть:\n"
            + _smp[:8000] + "\n"
        )

    if _smp:
        _rules = (
            "ГЛАВНОЕ И ЕДИНСТВЕННОЕ ПРАВИЛО: воспроизведи структуру ЭТАЛОНА выше ОДИН В ОДИН.\n"
            "- Повтори порядок блоков, эмодзи, разделители, переносы строк, форматирование — дословно.\n"
            "- Общие блоки (доставка, контакты, условия заказа, сертификаты, призывы, гарантии) "
            "перенеси из эталона БЕЗ ИЗМЕНЕНИЙ.\n"
            "- Меняй ТОЛЬКО товарную часть: название, артикул, характеристики, преимущества, ключевые слова.\n"
            "- Длина — как в эталоне. Не сокращай, не добавляй своих блоков.\n"
            "- НЕ придумывай свою структуру. НЕ пиши 'крючок' или короткое описание — копируй эталон.\n"
            "- ЗАПРЕЩЕНА markdown-разметка: никаких ###, **, ---. Только обычный текст.\n"
            "- ЗАПРЕЩЕНО писать служебные заголовки и мета-текст: 'Варианты названия:', 'Заголовок:', "
            "'Описание:', нумерованные списки вариантов названий. Выдавай ТОЛЬКО готовый текст описания, "
            "без пояснений и без перечисления вариантов заголовка.\n"
            "- В САМОМ КОНЦЕ обязательно добавь уникализацию: строку вида {{{{в чёрном|в тёмном|в базовом}}}} цвете "
            "и отдельным абзацем спинтакс-группу из 10 поисковых формулировок в {{{{...|...}}}}."
        )
    else:
        _rules = (
            "Напиши продающее описание (120-200 слов) для объявления на Avito:\n"
            "- Начни с сильного заголовка-крючка (боль или выгода клиента)\n"
            "- Опиши товар живым языком, не сухим перечислением характеристик\n"
            "- Упомяни 3-4 ключевые характеристики естественно, в контексте пользы\n"
            "- Заверши призывом к действию (напишите, звоните)\n"
            "- БЕЗ markdown-разметки, звёздочек, решёток\n"
            "- Пиши по-русски, дружелюбно, без канцелярита"
        )

    prompt = f"""Ты — копирайтер, пишешь продающее описание товара для объявления на Avito.

Товар: {title}
Цена: {price}
Характеристики:
{chars_text}
{sample_block}

{_rules}

УНИКАЛИЗАЦИЯ ТЕКСТА (важно, Avito ранжирует хуже дубли).

Правила спинтакса:
- Формат: ОДНА фигурная скобка с каждой стороны, варианты через вертикальную черту. НЕ используй двойные скобки.
- Вплетай спинтакс ВНУТРЬ строк описания, а не отдельным абзацем в конце.
- Бери варианты ТОЛЬКО из реальных характеристик товара выше. НИЧЕГО НЕ ВЫДУМЫВАЙ.

Что можно варьировать: размеры и толщину (реальные значения и близкие формулировки), цвета из характеристик, материал (переформулировки), комплектацию, состояние, назначение.

Пример правильной уникализации внутри строк:
Ультратонкий неопрен {{1 мм|толщиной 1 мм|1.0 мм}} — лёгкость и тепло;
Доступны {{в красном|в алом|красного цвета}} исполнении;
Размеры {{S|M|L}} в наличии;

В САМОМ КОНЦЕ описания, отдельным абзацем, добавь ОДНУ спинтакс-группу из РОВНО 10 синонимичных поисковых формулировок этого товара (как реально ищут в Avito/Яндексе), в ОДИНАРНЫХ скобках. Каждый вариант с заглавной буквы.
Пример формата: {{Тротуарная плитка|Плитка тротуарная|Плитка для дорожек|Брусчатка тротуарная|Плитка садовая|Плитка для двора|Уличная плитка|Плитка для мощения|Плитка для укладки|Тротуарная плитка недорого}}

КРИТИЧЕСКИ ВАЖНЫЙ ЗАПРЕТ: если товар — конкретная модель конкретного производителя, НИКОГДА не создавай спинтакс-группу для марки/бренда/модели с ДРУГИМИ производителями — это недостоверная реклама. Марка и модель ФИКСИРОВАНЫ. Варьировать можно только объективно переменные характеристики самого товара."""

    print(f"[desc] образец получен: {len(_smp)} симв.", flush=True)
    print(f"[desc] === ПРОМПТ (первые 900 симв) ===\n{prompt[:900]}\n=== КОНЕЦ ===", flush=True)
    try:
        return chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.8, max_tokens=2500
        ).strip()
    except Exception as e:
        print(f"[parse] ошибка генерации описания: {e}")
        return f"{title}\n\n{chars_text}"  # fallback — сырой текст, если ИИ недоступен


def _save_parsed_products(account_id: str, url, products: list):
    """Сохраняет выгруженные с сайта карточки в Storage по account_id.
    `url` - одна ссылка (str) или список ссылок (list), если карточки собраны с нескольких сайтов подряд."""
    import json as _json
    from datetime import datetime as _dt
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    urls = url if isinstance(url, list) else [url]
    db = SessionLocal()
    try:
        key = "parsed_products"
        payload = _json.dumps({
            "source_url": urls[0] if urls else None,
            "source_urls": urls,
            "parsed_at": _dt.now().isoformat(),
            "count": len(products),
            "products": products
        }, ensure_ascii=False)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        if row:
            row.value = payload
        else:
            row = Storage(account_id=account_id, key=key, value=payload)
            db.add(row)
        db.commit()
        return True
    except Exception as e:
        print(f"[parse] ошибка сохранения: {e}")
        return False
    finally:
        db.close()


def _save_city_analysis(query: str, city: str, data: dict):
    """Сохраняет результат парсинга в Storage, чтобы не парсить повторно и хранить историю."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import datetime

    date_key = datetime.now().strftime("%Y-%m-%d")
    key = f"city_analysis:{city}:{query}:{date_key}"
    db = SessionLocal()
    try:
        raw = _json.dumps(data, ensure_ascii=False)
        row = db.query(Storage).filter(Storage.key == key).first()
        if row:
            row.value = raw
        else:
            row = Storage(account_id="_global_parser", key=key, value=raw)
            db.add(row)
        db.commit()
    finally:
        db.close()


def _load_city_analysis_if_fresh(query: str, city: str):
    """Проверяет, есть ли уже сегодняшний результат по этому городу/запросу — не парсим повторно."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import datetime

    date_key = datetime.now().strftime("%Y-%m-%d")
    key = f"city_analysis:{city}:{query}:{date_key}"
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.key == key).first()
        if row:
            return _json.loads(row.value)
    finally:
        db.close()
    return None



CITY_MAP = {
    "москва": "moskva", "санкт-петербург": "sankt-peterburg", "спб": "sankt-peterburg",
    "питер": "sankt-peterburg", "новосибирск": "novosibirsk", "екатеринбург": "ekaterinburg",
    "казань": "kazan", "нижний новгород": "nizhniy_novgorod", "челябинск": "chelyabinsk",
    "самара": "samara", "омск": "omsk", "ростов-на-дону": "rostov-na-donu",
    "уфа": "ufa", "красноярск": "krasnoyarsk", "воронеж": "voronezh", "пермь": "perm",
    "волгоград": "volgograd", "краснодар": "krasnodar", "саратов": "saratov",
    "тюмень": "tyumen", "тольятти": "tolyatti", "ижевск": "izhevsk", "барнаул": "barnaul",
    "ульяновск": "ulyanovsk", "иркутск": "irkutsk", "хабаровск": "habarovsk",
    "ярославль": "yaroslavl", "владивосток": "vladivostok", "махачкала": "mahachkala",
    "томск": "tomsk", "оренбург": "orenburg", "кемерово": "kemerovo", "пенза": "penza",
    "рязань": "ryazan", "липецк": "lipetsk", "тула": "tula", "киров": "kirov",
    "чебоксары": "cheboksary", "калининград": "kaliningrad", "брянск": "bryansk",
    "курск": "kursk", "сочи": "sochi",
}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", " ": "-", "-": "-",
}


def _normalize_city(city: str) -> str:
    """Приводит название города к формату URL Avito (латиница).
    Если уже латиница — оставляет как есть. Русское — по словарю или транслитерацией."""
    c = city.strip().lower()
    # Уже латиница (нет кириллицы) — возвращаем как есть
    if not any("а" <= ch <= "я" or ch == "ё" for ch in c):
        return c
    if c in CITY_MAP:
        return CITY_MAP[c]
    # Fallback: транслитерация
    return "".join(_TRANSLIT.get(ch, ch) for ch in c)


async def _fetch_item_full_info(item_id, max_attempts: int = 3):
    """По одному только item_id (без готового URL) заходит на avito.ru/items/{id},
    получает город из финального URL после редиректа, плюс название/описание/фото.
    Работает для ЛЮБОГО объявления на Avito, не только опубликованных через Бориса."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            page_data = await _fetch_avito_page_headed(f"https://www.avito.ru/items/{item_id}", timeout_ms=20000, wait_ms=3000)
        except Exception as e:
            last_error = str(e)[:150]
            await asyncio.sleep(2)
            continue

        html = page_data["html"]
        final_url = page_data.get("final_url") or ""

        # Город — в первую очередь из финального URL после редиректа (avito.ru/{city}/{category}/{slug}),
        # это надёжно работает всегда. Fallback — canonical/og:url из html.
        soup = BeautifulSoup(html, "lxml")

        city = None
        if final_url and "avito.ru/" in final_url:
            parts = final_url.split("avito.ru/", 1)[1].split("/")
            if parts and parts[0]:
                city = parts[0]

        if not city:
            canonical = soup.find("link", attrs={"rel": "canonical"})
            og_url = soup.find("meta", attrs={"property": "og:url"})
            url_str = None
            if canonical and canonical.get("href"):
                url_str = canonical["href"]
            elif og_url and og_url.get("content"):
                url_str = og_url["content"]
            if url_str:
                parts = url_str.replace("https://www.avito.ru/", "").split("/")
                if parts:
                    city = parts[0]

        title_el = soup.find(attrs={"data-marker": "item-view/title-info"}) or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else None

        if not title:
            last_error = "empty_title"
            await asyncio.sleep(2)
            continue

        description = None
        best_len = 0
        for p in soup.find_all(["p", "div"], attrs={"itemprop": "description"}):
            text = p.get_text(strip=True)
            if len(text) > best_len:
                description = text[:2000]
                best_len = len(text)

        photos_count = len(soup.find_all(attrs={"data-marker": "image-preview/item"}))

        return {
            "item_id": item_id,
            "city": city,
            "title": title,
            "description": description,
            "photos_count": photos_count,
        }

    return {"error": last_error or "unknown", "item_id": item_id}


@router.post("/city_analysis")
def city_analysis(req: CityAnalysisRequest):
    import asyncio

    async def run_analysis():
        results = []
        for city in req.cities:
            cached = _load_city_analysis_if_fresh(req.query, city)
            if cached:
                cached["from_cache"] = True
                results.append(cached)
                continue

            try:
                search_query = req.query.replace(" ", "+")
                city_slug = _normalize_city(city)
                url = f"https://www.avito.ru/{city_slug}?q={search_query}"
                # Retry на уровне списка: маленькие/большие города иногда ловят капчу
                # на конкретном IP — пробуем до 3 раз с новым IP, пока не получим объявления
                items = []
                for _list_attempt in range(4):
                    try:
                        _page_data = await _fetch_avito_page_headed(url)
                        html = _page_data["html"]
                    except Exception:
                        # Битый прокси-порт (ERR_TUNNEL_CONNECTION_FAILED и т.п.) — новый IP
                        continue
                    if "firewallCaptcha" in html or "geetest_captcha" in html:
                        continue
                    items = _extract_listing_items(html)
                    if items:
                        break

                total_count = _extract_total_count(html) if items else None
                prices = [it["price"] for it in items if it["price"]]

                # Топ-5 по возрастанию цены — самые конкурентные предложения
                top5_base = sorted([it for it in items if it["price"]], key=lambda x: x["price"])[:5]

                top5 = []
                for it in top5_base:
                    detail = await _fetch_item_detail_headed(it["url"]) if it["url"] else {}
                    advantages = _summarize_advantages(it["title"], detail.get("description"))
                    top5.append({
                        "title": it["title"],
                        "price": it["price"],
                        "url": it["url"],
                        "photos_count": detail.get("images_count"),
                        "advantages": advantages,
                    })

                city_result = {
                    "city": city,
                    "query": req.query,
                    "count_found": len(items),
                    "total_count": total_count,
                    "min_price": min(prices) if prices else None,
                    "max_price": max(prices) if prices else None,
                    "avg_price": sum(prices) // len(prices) if prices else None,
                    "top5": top5,
                    "from_cache": False,
                }
                if total_count:
                    _save_market_stat(req.query, city, total_count)
                # Не кэшируем пустой результат - 0 объявлений почти всегда означает
                # провал парсинга (капча/мёртвый прокси), а не реальное отсутствие
                # конкурентов. Кэшируя 0, мы залипаем на весь день даже после починки.
                if items:
                    _save_city_analysis(req.query, city, city_result)
                results.append(city_result)

            except Exception as e:
                results.append({"city": city, "error": str(e)[:200]})

        return results

    results = asyncio.run(run_analysis())
    return {"query": req.query, "results": results}





@router.post("/city_analysis_async")
def city_analysis_async(req: CityAnalysisRequest):
    """Создаёт фоновую задачу парсинга и сразу возвращает task_id.
    Фронтенд опрашивает /api/tasks/status/{task_id} для прогресса."""
    import json as _json
    from app.db.session import SessionLocal
    from app.models.task import Task

    db = SessionLocal()
    try:
        payload = _json.dumps({"query": req.query, "cities": req.cities}, ensure_ascii=False)
        task = Task(account_id="_global_parser", task_type="city_analysis", status="queued", payload=payload)
        db.add(task)
        db.commit()
        db.refresh(task)
        return {"status": "ok", "task_id": task.id}
    finally:
        db.close()


import zipfile
import io
import re
from fastapi.responses import StreamingResponse

def safe_filename(name: str) -> str:
    name = re.sub(r'[^\w\s-]', '', name).strip()
    name = re.sub(r'[\s]+', '_', name)
    return name[:60] if name else "товар"

class DownloadPhotosRequest(BaseModel):
    products: list

@router.post("/download_photos")
def download_photos(req: DownloadPhotosRequest):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, product in enumerate(req.products):
            title = product.get("title", f"товар_{idx}")
            image_url = product.get("image")
            
            if not image_url:
                continue
            
            try:
                if image_url.startswith("//"):
                    image_url = "https:" + image_url
                
                img_response = requests.get(image_url, headers=headers, timeout=10)
                if img_response.status_code == 200:
                    folder_name = safe_filename(title)
                    ext = image_url.split(".")[-1].split("?")[0][:4]
                    if ext not in ["jpg","jpeg","png","webp","gif"]:
                        ext = "jpg"
                    file_path = f"{folder_name}/{folder_name}_1.{ext}"
                    zip_file.writestr(file_path, img_response.content)
            except Exception:
                continue
    
    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=boris_photos.zip"}
    )


class ParseDetailRequest(BaseModel):
    url: str

@router.post("/parse_detail")
def parse_detail(req: ParseDetailRequest):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        response = requests.get(req.url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "lxml")

        # Все фото товара (обычно itemprop=image повторяется в галерее)
        images = []
        for img_el in soup.find_all(attrs={"itemprop": "image"}):
            src = img_el.get("src") or img_el.get("content") or img_el.get("data-src")
            if src and src not in images:
                images.append(src)
        if not images:
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if src and src not in images:
                    images.append(src)

        # Характеристики: таблицы, dl/dt/dd, li с двоеточием — по всей странице товара
        characteristics = {}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True)
                    val = cells[1].get_text(strip=True)
                    if key and val and len(key) < 60 and len(val) < 200:
                        characteristics[key] = val
        for dl in soup.find_all("dl"):
            dts = dl.find_all("dt")
            dds = dl.find_all("dd")
            for dt, dd in zip(dts, dds):
                key = dt.get_text(strip=True)
                val = dd.get_text(strip=True)
                if key and val and len(key) < 60 and len(val) < 200:
                    characteristics[key] = val
        for li in soup.find_all("li"):
            text = li.get_text(strip=True)
            if ":" in text and len(text) < 150:
                key, _, val = text.partition(":")
                key, val = key.strip(), val.strip()
                if key and val and len(key) < 60:
                    characteristics[key] = val

        # Полное описание — самый длинный текстовый блок на странице
        description = None
        best_len = 0
        for p in soup.find_all(["p", "div"], attrs={"itemprop": "description"}):
            text = p.get_text(strip=True)
            if len(text) > best_len:
                description = text[:2000]
                best_len = len(text)
        if not description:
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > best_len and len(text) > 50:
                    description = text[:2000]
                    best_len = len(text)

        return {
            "url": req.url,
            "images": images[:15],
            "characteristics": characteristics,
            "description": description
        }
    except Exception as e:
        return {"error": str(e), "url": req.url}

# ===== ЛЕГАСИ-SSL + ЭКСТРАКТОР ТАБЛИЧНЫХ САЙТОВ (Sakura и подобные) =====
import ssl as _ssl
from requests.adapters import HTTPAdapter as _HTTPAdapter


class _LegacySSLAdapter(_HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = _ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        ctx.options |= 0x4
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _fetch_html_tolerant(url, timeout=25):
    import requests as _rq
    import urllib3 as _u3
    _u3.disable_warnings()
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    try:
        r = _rq.get(url, timeout=timeout, headers=headers)
        if r.status_code == 200:
            r.encoding = r.apparent_encoding
            return r.text, r.url
    except Exception:
        pass
    try:
        s = _rq.Session()
        s.mount("https://", _LegacySSLAdapter())
        r = s.get(url, timeout=timeout, headers=headers, verify=False)
        if r.status_code == 200:
            r.encoding = r.apparent_encoding
            return r.text, r.url
    except Exception:
        pass
    try:
        r = _rq.get(url.replace("https://", "http://", 1), timeout=timeout, headers=headers)
        if r.status_code == 200:
            r.encoding = r.apparent_encoding
            return r.text, r.url
    except Exception:
        pass
    return None, None


def _extract_generic_product(html, page_url):
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    title = None
    breadcrumbs = None
    for t in soup.find_all("table"):
        txt = t.get_text(" | ", strip=True)
        if "\u00bb" in txt and len(txt) < 300:
            parts = [p.strip() for p in txt.split("|") if p.strip()]
            if parts:
                title = parts[0]
                breadcrumbs = " > ".join(p for p in parts[1:] if p != "\u00bb")
            break
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    if not title:
        return None
    characteristics = {}
    for t in soup.find_all("table"):
        pairs = 0
        tmp = {}
        for tr in t.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                k = tds[0].get_text(" ", strip=True).rstrip(":").strip()
                v = tds[1].get_text(" ", strip=True)
                if k and v and len(k) < 60:
                    tmp[k] = v
                    pairs += 1
        if pairs >= 3:
            characteristics.update(tmp)
    description = ""
    for h in soup.find_all(["h1", "h2", "h3", "b", "strong"]):
        if "\u043e\u043f\u0438\u0441\u0430\u043d" in h.get_text(strip=True).lower():
            chunks = []
            for sib in h.find_all_next(string=True, limit=60):
                s_ = str(sib).strip()
                if not s_:
                    continue
                if "\u0445\u0430\u0440\u0430\u043a\u0442\u0435\u0440\u0438\u0441\u0442\u0438\u043a" in s_.lower():
                    break
                if len(s_) > 25:
                    chunks.append(s_)
                if len(" ".join(chunks)) > 900:
                    break
            description = " ".join(chunks)[:2000]
            break
    if not description and characteristics:
        description = ". ".join("%s: %s" % (k, v) for k, v in characteristics.items())[:2000]
    BAD = ("yandex", "logo", "icon", "spacer", "pixel", "counter", "banner", "button")
    images = []
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or any(b in src.lower() for b in BAD):
            continue
        full = urljoin(page_url, src)
        if full not in images:
            images.append(full)
    _imgs = images[:10]
    return {
        "title": title,
        "description": description,
        "price": None,
        "image": _imgs[0] if _imgs else None,
        "images": _imgs,
        "characteristics": characteristics,
        "product_url": page_url,
        "category_path": breadcrumbs or "",
        "price_missing": True,
    }
