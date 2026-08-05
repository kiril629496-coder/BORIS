"""
Универсальный резолвер категорий и обязательных полей Avito.
Источник истины: официальная документация /autoload/documentation/templates/{id}
Кэш: Storage(account_id='_global', key='category_knowledge_base')
"""
import asyncio
import html
import json
import re

async def _block_media_only(route):
    if route.request.resource_type in ("image", "media"):
        await route.abort()
    else:
        await route.continue_()


async def _block_heavy(route):
    if route.request.resource_type in ("image", "stylesheet", "font", "media"):
        await route.abort()
    else:
        await route.continue_()




async def scrape_template_page(template_id: int) -> str:
    """Скрейпит страницу документации шаблона в английском режиме параметров, возвращает сырой текст body.
    Ретраит до 3 раз с НОВЫМ случайным портом резидентного пула на каждую попытку - часть портов
    в пуле всегда мертва, это норма для резидентных прокси, одна неудача не должна ронять всю операцию."""
    from proxy_pool import get_playwright_proxy, get_clean_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url = f"https://www.avito.ru/autoload/documentation/templates/{template_id}"
    stealth = Stealth()
    last_error = None

    async with async_playwright() as p:
        for attempt in range(3):
            proxy = await get_clean_playwright_proxy()
            browser = None
            try:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 1200}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", _block_heavy)

                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

                try:
                    eng_button = page.get_by_text("Английский", exact=True).first
                    await eng_button.click()
                    await page.wait_for_timeout(3000)
                except Exception:
                    pass

                text = await page.inner_text("body")
                await browser.close()
                # проверка: не бан-страница ли (IP протух между проверкой и запросом)
                if "Доступ ограничен" in text or "проблема с IP" in text:
                    import proxy_pool as _pp
                    _pp._clean_port_cache = {"port": None, "ts": 0}  # сбросить протухший порт
                    last_error = Exception(f"бан-страница на попытке {attempt+1}, порт сброшен")
                    continue
                return text
            except Exception as e:
                last_error = e
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                continue

    raise Exception(f"scrape_template_page: не удалось загрузить страницу шаблона {template_id} за 3 попытки: {last_error}")


def structure_fields_via_gpt(raw_text: str, category_name: str) -> list:
    """
    Отдаёт сырой текст документации в GPT-5.4, получает структурированный список полей.
    Возвращает list[dict]: {tag, name_ru, required, depends_on, allowed_values, example}
    """
    import os
    import requests
    from proxy_pool import get_intl_requests_proxies

    api_key = os.environ.get("OPENAI_API_KEY")
    proxies = get_intl_requests_proxies()

    prompt = (
        "Ниже — текст страницы официальной документации Avito Автозагрузка для категории "
        f"'{category_name}' (английские имена тегов, потому что это XML-совместимые технические имена). "
        "Разбери ВСЕ параметры (кроме общих Id/Title/Description/Price/Images/Address/ContactPhone — "
        "их не включай, они уже обрабатываются отдельно) в JSON-список.\n\n"
        "Для каждого параметра верни объект:\n"
        "{\n"
        '  "tag": "точное английское имя тега (например GoodsType)",\n'
        '  "name_ru": "русское название параметра",\n'
        '  "required": true/false,\n'
        '  "depends_on": "текст условия обязательности если оно условное, иначе null",\n'
        '  "allowed_values": ["список допустимых значений"] или null если значение свободное/открытый список,\n'
        '  "format_rule": "если у поля есть требование к ФОРМАТУ значения помимо списка допустимых '
        '(например \\"целое число строкой, без слов\\" для WorkExperience, или \\"строго Есть или Нет\\" '
        'для Guarantee) - опиши коротко своими словами; если формат свободный - null",\n'
        '  "example": "пример значения из документации"\n'
        "}\n\n"
        "Верни ТОЛЬКО чистый JSON-массив, без markdown, без пояснений.\n\n"
        "ТЕКСТ ДОКУМЕНТАЦИИ:\n" + raw_text[:15000]
    )

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        json={
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": 4000,
        },
        proxies=proxies,
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    try:  # учёт расхода: служебный вызов без привязки к клиенту
        from app.usage import log_usage as _lu
        _u = data.get("usage") or {}
        _lu(None, "openai", data.get("model") or "gpt-5.4", "system:category_doc_parse",
            int(_u.get("prompt_tokens") or 0), int(_u.get("completion_tokens") or 0))
    except Exception as _e:
        print("[usage]", str(_e)[:100], flush=True)
    raw = data["choices"][0]["message"]["content"].strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group(0)

    return json.loads(raw)


def _load_knowledge_base(db) -> dict:
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == '_global', Storage.key == 'category_knowledge_base').first()
    if not row:
        return {}
    return json.loads(row.value)


def _save_knowledge_base(db, data: dict):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == '_global', Storage.key == 'category_knowledge_base').first()
    value = json.dumps(data, ensure_ascii=False)
    if row:
        row.value = value
    else:
        row = Storage(account_id='_global', key='category_knowledge_base', value=value)
        db.add(row)
    db.commit()


NAV_SCOPE = "nav[class*='navigation-menu-root']"
NAV_ROW_SEL = f"{NAV_SCOPE} [class*='navigation-menu-item-root']"


async def _get_tree_rows(page) -> list:
    """Плоский список строк дерева в DOM-порядке: {text, href, margin}. Разведка живого DOM
    (outerHTML после клика) показала, что Avito рендерит дерево ОДНИМ плоским списком
    div.navigation-menu-item-root с инлайновым margin-left (18px на уровень) вместо настоящей
    DOM-вложенности - значит родитель/потомок определяется отступом и порядком в списке, а НЕ
    containment и НЕ повторением текста (одинаковые подписи вроде "Продам"/"Сдам" законно
    повторяются под РАЗНЫМИ родителями на одном отступе)."""
    try:
        return await page.evaluate(
            """(scopeSel) => {
                const scope = document.querySelector(scopeSel);
                if (!scope) return [];
                const rows = Array.from(scope.querySelectorAll("[class*='navigation-menu-item-root']"));
                return rows.map(row => {
                    const text = (row.innerText || '').trim();
                    const a = row.querySelector('a');
                    const href = a ? a.getAttribute('href') : null;
                    const style = row.getAttribute('style') || '';
                    const m = style.match(/margin-left:\\s*(\\d+)px/);
                    return {text, href, margin: m ? parseInt(m[1], 10) : 0};
                });
            }""",
            NAV_SCOPE
        )
    except Exception:
        return []


def _pick_row_index(rows: list, text: str, margin: int, skip_count: int):
    """Находит (skip_count+1)-ю по счёту строку с данным (text, margin) в DOM-порядке - нужно,
    чтобы после re-scan снова найти ИМЕННО ТОТ узел, что мы уже начали обрабатывать, даже если
    в дереве есть другие строки с точно такой же подписью на том же отступе (у разных родителей)."""
    seen = 0
    for idx, r in enumerate(rows):
        if r.get("text") == text and r.get("margin") == margin:
            if seen == skip_count:
                return idx
            seen += 1
    return None


async def _crawl_tree_node(page, path_labels: list, text: str, margin: int, consumed: dict, leaves: list, depth: int = 0):
    """Раскрывает ОДИН конкретный узел дерева (заданный своими text/margin, см. _get_tree_rows) и
    рекурсивно обходит его ПРЯМЫХ детей - граница "прямой ребёнок" определяется отступом
    (margin > текущего, до первой строки с margin <= текущего), а НЕ повторением текста.
    `consumed` - общий счётчик {(text, margin): сколько раз уже встречалось} через ВЕСЬ обход -
    раньше вместо этого использовалось глобальное множество "видели ли этот текст", из-за чего
    ВТОРАЯ и все следующие ветки с одинаковой подписью ребёнка (например "Продам"/"Сдам" - общие
    для любого типа недвижимости, "Резюме"/"Вакансии" - общие для любой сферы работы) молча
    пропускались рекурсией: инцидент "Недвижимость" (5 листьев вместо сотен - раскрылась только
    одна ветка типа недвижимости из семи) и "Работа" (1 лист вместо десятков сфер)."""
    import re as _re

    if depth > 8:
        return

    key = (text, margin)
    skip = consumed.get(key, 0)
    consumed[key] = skip + 1

    rows = await _get_tree_rows(page)
    idx = _pick_row_index(rows, text, margin, skip)
    if idx is None:
        return
    row = rows[idx]

    m = _re.search(r"/templates/(\d+)", row.get("href") or "")
    if m:
        leaves.append({
            "top_level": path_labels[0],
            "path": " > ".join(path_labels),
            "leaf_name": text,
            "template_id": m.group(1),
        })
        return

    url_before = page.url
    try:
        await page.locator(NAV_ROW_SEL).nth(idx).locator("a").first.click(timeout=6000)
        await page.wait_for_timeout(1500)
    except Exception:
        return

    # SPA: у некоторых узлов template_id появляется в page.url ПОСЛЕ клика, а не в href
    url_after = page.url
    m2 = _re.search(r"/templates/(\d+)", url_after)
    if m2:
        leaves.append({
            "top_level": path_labels[0] if path_labels else text,
            "path": " > ".join(path_labels),
            "leaf_name": text,
            "template_id": m2.group(1),
        })
        print(f"[crawl] ЛИСТ: {' > '.join(path_labels)} -> {m2.group(1)}", flush=True)
        try:
            await page.go_back()
            await page.wait_for_timeout(1200)
        except Exception:
            pass
        return

    rows2 = await _get_tree_rows(page)
    children = []
    for r in rows2[idx + 1:]:
        if r.get("margin", 0) > margin:
            children.append(r)
        else:
            break

    for child in children:
        child_text = child["text"]
        m3 = _re.search(r"/templates/(\d+)", child.get("href") or "")
        if m3:
            leaves.append({
                "top_level": path_labels[0],
                "path": " > ".join(path_labels + [child_text]),
                "leaf_name": child_text,
                "template_id": m3.group(1),
            })
            continue
        await _crawl_tree_node(page, path_labels + [child_text], child_text, child["margin"], consumed, leaves, depth + 1)

    if page.url != url_before:
        try:
            await page.go_back()
            await page.wait_for_timeout(1200)
        except Exception:
            pass


async def _crawl_category_tree_async() -> list:
    from proxy_pool import get_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url = "https://www.avito.ru/autoload/documentation/templates"
    stealth = Stealth()
    leaves = []
    last_error = None

    async with async_playwright() as p:
        browser = None
        page = None
        for attempt in range(3):
            proxy = get_playwright_proxy()
            try:
                browser = await p.chromium.launch(headless=False, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 1200}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", _block_media_only)
                await page.goto(url, timeout=60000, wait_until="networkidle")
                await page.wait_for_timeout(6000)
                try:
                    await page.wait_for_selector("a[href*='/templates/']", timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(15000)
                _live = await page.evaluate("() => document.body.innerHTML")
                open("/tmp/crawl_live.html", "w", encoding="utf-8").write(_live)
                _txt = await page.evaluate("() => document.body.innerText")
                open("/tmp/crawl_text.txt", "w", encoding="utf-8").write(_txt)
                _hrefs = await page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href')).filter(Boolean)")
                print(f"[crawl] живой DOM={len(_live)} | текст={len(_txt)} | ссылок={len(_hrefs)}", flush=True)
                print(f"[crawl] первые href: {_hrefs[:15]}", flush=True)
                print(f"[crawl] первые 400 симв текста: {_txt[:400]!r}", flush=True)
                await page.screenshot(path="/tmp/crawl_shot.png", full_page=True)
                break
            except Exception as e:
                last_error = e
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                browser = None
                page = None
                continue

        if not page:
            raise Exception(f"crawl_category_tree: не удалось открыть дерево категорий за 3 попытки: {last_error}")

        consumed = {}
        top_rows = await _get_tree_rows(page)
        for r in top_rows:
            if r.get("margin", 0) == 0 and r.get("text"):
                await _crawl_tree_node(page, [r["text"]], r["text"], 0, consumed, leaves, 0)
        await browser.close()

    return leaves


def _upsert_tree_leaves(leaves: list) -> int:
    """Дедуп по template_id (дерево иногда обходится с повтором одного и того же листа по разным
    веткам клика) + ON CONFLICT DO UPDATE вместо query-then-insert - обычный select+add падал
    UniqueViolation, если template_id уже был сохранён предыдущим (например частичным) обходом.
    Общий хелпер для полного и точечного (по конкретным top_level) обхода дерева."""
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTreeLeaf
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    by_template_id = {leaf["template_id"]: leaf for leaf in leaves}
    rows = list(by_template_id.values())

    db = SessionLocal()
    try:
        if rows:
            stmt = _pg_insert(CategoryTreeLeaf).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[CategoryTreeLeaf.template_id],
                set_={
                    "top_level": stmt.excluded.top_level,
                    "path": stmt.excluded.path,
                    "leaf_name": stmt.excluded.leaf_name,
                },
            )
            db.execute(stmt)
        db.commit()
    finally:
        db.close()
    return len(rows)


def crawl_category_tree() -> dict:
    """
    Разовый (админский) обход ВСЕГО дерева документации Автозагрузки Avito
    (.../autoload/documentation/templates) - строит полную карту {путь по дереву -> template_id}
    и сохраняет upsert-ом в CategoryTreeLeaf по template_id. Статический requests тут не годится -
    страница JS SPA и отдаёт один и тот же дефолтный фрагмент независимо от пути (подтверждено
    разведкой), поэтому используется headed-браузер с кликами по accordion.
    Результат живёт в БД и переиспользуется дальше через resolve_template_id_for_niche() -
    сам обход не делается на каждый запрос ниши.
    """
    leaves = asyncio.run(_crawl_category_tree_async())
    _upsert_tree_leaves(leaves)
    return {"status": "ok", "leaves_found": len(leaves)}


async def _crawl_category_tree_partial_async(top_levels: list) -> list:
    """Точечный допрогон: обходит дерево, но раскрывает и рекурсивно обходит ТОЛЬКО указанные
    top_level узлы (например 'Недвижимость', 'Работа', если первый обход прошёл их не полностью) -
    остальные top-level ветки просто не трогаем (см. _crawl_tree_node про margin-based обход)."""
    from proxy_pool import get_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url = "https://www.avito.ru/autoload/documentation/templates"
    stealth = Stealth()
    leaves = []
    last_error = None

    async with async_playwright() as p:
        browser = None
        page = None
        for attempt in range(3):
            proxy = get_playwright_proxy()
            try:
                browser = await p.chromium.launch(headless=False, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 1200}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", _block_media_only)
                await page.goto(url, timeout=60000, wait_until="networkidle")
                await page.wait_for_timeout(6000)
                try:
                    await page.wait_for_selector(NAV_SCOPE, timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)
                break
            except Exception as e:
                last_error = e
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                browser = None
                page = None
                continue

        if not page:
            raise Exception(f"crawl_category_tree_partial: не удалось открыть дерево категорий за 3 попытки: {last_error}")

        consumed = {}
        top_rows = await _get_tree_rows(page)
        for r in top_rows:
            if r.get("margin", 0) == 0 and r.get("text") in top_levels:
                await _crawl_tree_node(page, [r["text"]], r["text"], 0, consumed, leaves, 0)
        await browser.close()

    return leaves


def crawl_category_tree_partial(top_levels: list) -> dict:
    """Апсертит листья ТОЛЬКО из указанных top_level веток (см. _crawl_category_tree_partial_async) -
    остальные ветки дерева не трогает вообще, полный обход не требуется."""
    leaves = asyncio.run(_crawl_category_tree_partial_async(top_levels))
    _upsert_tree_leaves(leaves)
    return {"status": "ok", "leaves_found": len(leaves), "top_levels": top_levels}


def _pick_leaf_via_gigachat(candidates: list, niche: str, account_id: str = None):
    """Просит GigaChat выбрать ОДИН лист дерева категорий (по номеру в списке), максимально
    подходящий нише клиента. ИИ не может придумать вариант вне перечисленного списка - только
    вернуть номер, отсюда исключено вранье в духе выдуманных категорий."""
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole
    import json as _json
    import re as _re

    options_text = "\n".join(f"{i}: {c.path}" for i, c in enumerate(candidates))
    prompt = (
        f"Ниша товара/услуги клиента: '{niche}'\n\n"
        "Ниже список листовых категорий дерева документации Avito Автозагрузка (полный путь через ' > '). "
        "Выбери ОДНУ категорию, максимально точно соответствующую нише клиента.\n\n"
        f"{options_text}\n\n"
        'Верни ТОЛЬКО чистый JSON: {"index": <номер выбранной категории из списка выше>}'
    )
    try:
        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)], temperature=0.1, max_tokens=100, account_id=account_id, operation=("category_leaf_selection" if account_id else "system:category_leaf_selection")).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            raw = match.group(0)
        parsed = _json.loads(raw)
        idx = int(parsed.get("index"))
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except Exception:
        pass
    return None


def resolve_template_id_for_niche(api_category: str, niche: str, account_id: str = None) -> dict:
    """
    api_category - категория, определённая на уровне поиска/API Avito (detect_category_endpoint) -
    в дереве документации это, как правило, ПРОМЕЖУТОЧНЫЙ узел (например "Спорт и отдых"), а не готовая
    категория шаблона - внутри нее может быть несколько листьев с разными template_id. Используем
    api_category как ФИЛЬТР ветки дерева (CategoryTreeLeaf.path), а не как конечный ответ.
    Если под фильтром один лист - берём его; если несколько - GigaChat выбирает конкретный лист
    по нише клиента (по названиям листьев, не выдумывая несуществующий вариант).
    Возвращает {"status": "ok", "template_id": str, "path": str} | {"status": "not_found", "message": str}
    """
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTreeLeaf

    db = SessionLocal()
    try:
        query = db.query(CategoryTreeLeaf)
        candidates = query.filter(CategoryTreeLeaf.path.ilike(f"%{api_category}%")).all() if api_category else []
        if not candidates:
            # api_category не сматчился ни с одним путём в дереве - ищем по всему дереву как фолбэк
            candidates = query.all()

        if not candidates:
            return {"status": "not_found", "message": "дерево категорий пусто - нужно сначала запустить crawl_category_tree()"}

        if len(candidates) == 1:
            leaf = candidates[0]
        else:
            leaf = _pick_leaf_via_gigachat(candidates, niche, account_id=account_id)
            if not leaf:
                return {"status": "not_found", "message": f"GigaChat не смог выбрать лист дерева категорий для ниши '{niche}'"}

        return {"status": "ok", "template_id": leaf.template_id, "path": leaf.path}
    finally:
        db.close()


def pick_value_via_gpt(field: dict, niche: str, allowed_values, format_rule: str = None) -> dict:
    """
    Подбирает значение поля под конкретную нишу клиента.
    Возвращает {"value": ... или None, "clarify_question": ... или None}.
    value=None означает, что поле реально неоднозначно и требует уточнения человека (например разные
    марки бетона у одного клиента - нельзя гадать) - в этом случае clarify_question уже готовый
    вопрос клиенту понятным языком (не тег, не техническое имя поля).
    """
    import os
    import json as _json
    import requests
    from proxy_pool import get_intl_requests_proxies

    api_key = os.environ.get("OPENAI_API_KEY")
    proxies = get_intl_requests_proxies()

    values_hint = ("Допустимые значения: " + ", ".join(allowed_values)) if allowed_values else "Значение свободное (текст/число), допустимых вариантов не задано."
    rule_hint = f"Требование к формату значения: {format_rule}\n" if format_rule else ""

    prompt = (
        f"Поле объявления Avito: '{field.get('name_ru')}' (тег {field.get('tag')}).\n"
        f"{values_hint}\n"
        f"{rule_hint}"
        f"Пример из документации: {field.get('example')}\n"
        f"Ниша товара клиента: '{niche}'\n\n"
        "Если по этой нише можно ОДНОЗНАЧНО и БЕЗОПАСНО определить правильное значение поля "
        "(например 'Состояние' почти всегда 'Новое' для товара от производителя) - верни это значение "
        "СТРОГО в требуемом формате (если указаны допустимые значения - ровно как в списке, если есть "
        "требование к формату - соблюди его точно, например число без слов).\n"
        "Если поле описывает характеристику, которая РЕАЛЬНО РАЗНИТСЯ между конкретными товарами "
        "этой ниши (например конкретная марка/модель/цвет/размер, когда ниша - это целая категория, "
        "а не один товар) - НЕ угадывай, верни value=null и придумай clarify_question - короткий вопрос "
        "клиенту ПОНЯТНЫМ ЖИВЫМ ЯЗЫКОМ (например 'Какой год постройки у домов?'), а НЕ техническое имя поля.\n\n"
        'Верни ТОЛЬКО чистый JSON: {"value": "..." или null, "clarify_question": "..." или null, "reason": "коротко почему"}'
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": 300,
            },
            proxies=proxies,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        try:  # учёт расхода: прямой вызов идёт мимо пула
            from app.usage import log_usage as _lu
            _u = data.get("usage") or {}
            _lu(None, "openai", data.get("model") or "gpt-5.4", "system:category_field_value",
                int(_u.get("prompt_tokens") or 0), int(_u.get("completion_tokens") or 0))
        except Exception as _ue:
            print("[usage]", str(_ue)[:100], flush=True)
        raw = data["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = _json.loads(raw)
        value = parsed.get("value")
        if allowed_values and value is not None and value not in allowed_values:
            # ИИ вернул то, чего нет в списке - не доверяем, просим уточнение у человека вместо вранья
            return {"value": None, "clarify_question": parsed.get("clarify_question") or f"Уточните значение поля «{field.get('name_ru')}»"}
        return {"value": value, "clarify_question": parsed.get("clarify_question")}
    except Exception:
        return {"value": None, "clarify_question": f"Уточните значение поля «{field.get('name_ru')}»"}


# Поля, которые обрабатываются отдельно и не должны попадать в required_fields базы знаний
# (совпадает со списком исключений в самом промпте structure_fields_via_gpt - защита на случай,
# если документация Avito всё же вернёт один из них).
_SKIP_TAGS = {"Id", "Title", "Description", "Price", "Images", "ImageUrls", "Address", "ContactPhone", "Category"}


def _normalize_category_id(text: str) -> str:
    return (text or "").strip().lower()


def _extract_official_field_docs(html_text: str, template_id) -> list:
    """Достаёт официальный структурированный JSON документации Avito (categoryDataCache), который
    страница уже отдаёт SSR прямо в HTML (<script data-mfe-state="true">) - там готовые от Avito
    required/type/values/values_range/dependency для каждого поля, без риска, что GPT ошибётся при
    пересказе сырого текста страницы. Возвращает [] если блок не найден - тогда вызывающий код
    падает на старый способ через Playwright + structure_fields_via_gpt."""
    marker = 'data-mfe-state="true">'
    idx = html_text.find(marker)
    if idx == -1:
        return []
    content_start = idx + len(marker)
    end = html_text.find("</script>", content_start)
    if end == -1:
        return []
    try:
        data = json.loads(html.unescape(html_text[content_start:end]))
    except Exception:
        return []

    cache = data.get("categoryDataCache") or {}
    key = next((k for k in cache.keys() if k.startswith(f"{template_id}-")), None)
    if not key:
        return []

    fields = []
    for grp in cache[key].get("field_groups", []):
        for f in grp.get("fields", []):
            tag = f.get("tag")
            if not tag or tag in _SKIP_TAGS:
                continue
            values = f.get("values")
            dependency = f.get("dependency")
            fields.append({
                "tag": tag,
                "name_ru": f.get("label") or f.get("description"),
                "required": bool(f.get("required")),
                "allowed_values": [v.get("value") for v in values if v.get("value")] if values else None,
                "format_rule": None,
                "example": f.get("example"),
                "depends_on": " | ".join(dependency) if dependency else None,
                "values_range": f.get("values_range"),
            })
    return fields


def fetch_template_fields_via_http(template_id) -> list:
    """Тянет документацию шаблона обычным requests (страница SSR-рендерит нужный JSON сразу в HTML -
    headed-браузер тут не нужен вообще), парсит через _extract_official_field_docs. Резидентный
    прокси-пул ретраит до 3 раз новым портом на попытку - часть портов пула всегда мертва, это норма."""
    import requests
    from proxy_pool import get_intl_requests_proxies

    url = f"https://www.avito.ru/autoload/documentation/templates/{template_id}"
    last_err = None
    for _attempt in range(3):
        try:
            proxies = get_intl_requests_proxies()
            r = requests.get(url, proxies=proxies, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            })
            r.raise_for_status()
            fields = _extract_official_field_docs(r.text, template_id)
            if fields:
                return fields
            last_err = "categoryDataCache не найден на странице"
        except Exception as e:
            last_err = e
            continue
    print(f"[fetch_template_fields_via_http] не удалось получить поля шаблона {template_id} через HTTP ({last_err}) - падаю на Playwright+GPT", flush=True)
    return []


def get_category_template(category_id: str, niche: str = None, category_name_hint: str = None, template_id_hint: int = None, api_category: str = None, account_id: str = None) -> dict:
    """
    База знаний полей категории Avito (модель CategoryTemplate), ленивая загрузка:
    - есть в БД -> возвращает сразу
    - нет -> находит template_id через resolve_template_id_for_niche (дерево документации +
      GigaChat для выбора листа), скрейпит документацию шаблона, структурирует через GPT, сохраняет в БД
    api_category - категория, уже определённая на уровне API/поиска Avito (detect_category_endpoint) -
    используется как фильтр ветки дерева при поиске template_id, см. resolve_template_id_for_niche.
    Возвращает {"status": "ok"|"not_found", "category_id", "category_name", "template_id",
                "required_fields": [...], "field_rules": {...}, "enum_values": {...}}
    "required_fields" - несмотря на имя, здесь ВСЕ поля категории (и обязательные, и нет);
    какое поле обязательно - смотри ключ "required" внутри каждого элемента списка.
    """
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTemplate

    cid = _normalize_category_id(category_id)
    niche = niche or category_id

    db = SessionLocal()
    try:
        row = db.query(CategoryTemplate).filter(CategoryTemplate.category_id == cid).first()
        if row and row.required_fields:
            return {
                "status": "ok",
                "category_id": row.category_id,
                "category_name": row.category_name,
                "template_id": row.template_id,
                "required_fields": json.loads(row.required_fields),
                "field_rules": json.loads(row.field_rules) if row.field_rules else {},
                "enum_values": json.loads(row.enum_values) if row.enum_values else {},
            }

        template_id = template_id_hint
        found_path = category_name_hint or niche
        if not template_id:
            found = resolve_template_id_for_niche(api_category or niche, niche, account_id=account_id)
            if found.get("status") != "ok":
                return {"status": "not_found", "message": found.get("message", "не удалось определить template_id категории")}
            template_id = found.get("template_id")
            found_path = found.get("path", found_path)

        try:
            # Основной путь - официальный JSON прямо из SSR HTML (точные required/type/values/
            # dependency от Avito, без риска пересказа GPT и без headed-браузера). Playwright+GPT -
            # только запасной вариант, если разметка страницы вдруг изменится и JSON не найдётся.
            fields = fetch_template_fields_via_http(template_id)
            if not fields:
                raw_text = asyncio.run(scrape_template_page(template_id))
                fields = structure_fields_via_gpt(raw_text, found_path)
        except Exception as e:
            # Не роняем весь запрос из-за сбоя скрейпинга/структурирования - отдаём тот же
            # понятный "категория не резолвится", что и при провале resolve_template_id_for_niche.
            return {"status": "not_found", "message": f"не удалось загрузить документацию шаблона {template_id}: {e}"}
        fields = [f for f in fields if f.get("tag") not in _SKIP_TAGS]

        field_rules = {f["tag"]: f["format_rule"] for f in fields if f.get("format_rule")}
        enum_values = {f["tag"]: f["allowed_values"] for f in fields if f.get("allowed_values")}

        if row:
            row.category_name = found_path
            row.template_id = str(template_id)
            row.required_fields = json.dumps(fields, ensure_ascii=False)
            row.field_rules = json.dumps(field_rules, ensure_ascii=False)
            row.enum_values = json.dumps(enum_values, ensure_ascii=False)
        else:
            row = CategoryTemplate(
                category_id=cid, category_name=found_path, template_id=str(template_id),
                required_fields=json.dumps(fields, ensure_ascii=False),
                field_rules=json.dumps(field_rules, ensure_ascii=False),
                enum_values=json.dumps(enum_values, ensure_ascii=False),
            )
            db.add(row)
        db.commit()

        return {
            "status": "ok",
            "category_id": cid,
            "category_name": found_path,
            "template_id": str(template_id),
            "required_fields": fields,
            "field_rules": field_rules,
            "enum_values": enum_values,
        }
    finally:
        db.close()


def group_products_by_niche(products: list, account_niche: str = None, account_id: str = None) -> list:
    """
    Группирует товары по нишам через GigaChat - в одном аккаунте могут быть товары РАЗНЫХ
    категорий Avito (пример: дома из бруса + штукатурка фасадов - разные категории с разными
    обязательными полями), хотя продаёт их один и тот же клиент.
    `products` - список dict с ключом "title" (минимум). Возвращает список групп:
    [{"niche": "название ниши", "indices": [0, 3, 5]}, ...] - каждый индекс товара попадает
    РОВНО в одну группу, ни один не теряется (даже если GigaChat недоступен или ответил мимо JSON -
    тогда все товары уходят в одну общую группу, а не пропадают молча).
    """
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole

    if not products:
        return []
    if len(products) == 1:
        return [{"niche": account_niche or products[0].get("title", "товар"), "indices": [0]}]

    titles_text = "\n".join(f"{i}: {p.get('title', '')}" for i, p in enumerate(products))
    context = f"Ниша бизнеса аккаунта: {account_niche}\n\n" if account_niche else ""
    prompt = (
        f"{context}Ниже список товаров с индексами. Сгруппируй их по НИШАМ/КАТЕГОРИЯМ Avito - "
        "то есть по тому, у каких товаров будут РАЗНЫЕ обязательные поля объявления "
        "(например 'дома из бруса' и 'штукатурка фасадов' - разные категории Avito, хотя обе "
        "могут продаваться одной строительной компанией). Не дели по мелким различиям внутри "
        "одной ниши (цвет, размер, модель - это ОДНА ниша, не разные группы).\n\n"
        "Товары:\n" + titles_text + "\n\n"
        'Верни ТОЛЬКО чистый JSON-массив групп: [{"niche": "название ниши", "indices": [0,1,2]}, ...]. '
        "Каждый индекс должен попасть ровно в одну группу, ни один индекс не пропускай."
    )
    try:
        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)], temperature=0.1, max_tokens=1500, account_id=account_id, operation="product_niche_grouping").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        groups = json.loads(raw)
        if not isinstance(groups, list):
            raise ValueError("GigaChat вернул не список")
    except Exception as e:
        print(f"[group_products_by_niche] не удалось сгруппировать через GigaChat ({e}) - одна общая группа")
        return [{"niche": account_niche or "товары", "indices": list(range(len(products)))}]

    seen = set()
    cleaned = []
    for g in groups:
        idxs = [i for i in g.get("indices", []) if isinstance(i, int) and 0 <= i < len(products) and i not in seen]
        if not idxs:
            continue
        seen.update(idxs)
        cleaned.append({"niche": g.get("niche", "товары"), "indices": idxs})
    missing = [i for i in range(len(products)) if i not in seen]
    if missing:
        cleaned.append({"niche": account_niche or "прочее", "indices": missing})
    return cleaned


def resolve_required_fields(category_id: str, niche: str, characteristics: dict = None, template: dict = None, api_category: str = None, account_id: str = None) -> dict:
    """
    Общая точка входа для create_draft_listings (plan_items.py) и parsed_products_to_drafts
    (parser.py): резолвит поля категории (обязательные и необязательные - см. get_category_template)
    под конкретную нишу/товар. НЕ выдумывает значения - то, что ИИ не может определить однозначно
    для ОБЯЗАТЕЛЬНОГО поля, попадает в needs_clarification готовыми вопросами клиенту понятным языком.
    Для необязательных полей неопределённость не блокирует ничего - поле просто остаётся незаполненным.
    api_category - категория, уже определённая на уровне API/поиска Avito (detect_category_endpoint) -
    прокидывается в get_category_template как фильтр ветки дерева при поиске template_id.
    Возвращает {"status": "ok"|"not_found", "message"?, "resolved": {tag: value}, "needs_clarification": [...]}
    """
    template = template or get_category_template(category_id, niche, api_category=api_category, account_id=account_id)
    if template.get("status") != "ok":
        return {"status": "not_found", "message": template.get("message", "категория не определена"), "resolved": {}, "needs_clarification": []}

    niche_context = niche
    if characteristics:
        chars_text = ", ".join(f"{k}: {v}" for k, v in characteristics.items())
        niche_context = f"{niche} (характеристики конкретного товара: {chars_text})"

    resolved = {}
    needs_clarification = []

    all_fields = template.get("required_fields", [])
    independent_fields = [f for f in all_fields if not f.get("depends_on")]
    dependent_fields = [f for f in all_fields if f.get("depends_on")]

    # Первый проход - поля БЕЗ условной зависимости, сюда же попадает Availability (у него самого
    # условия нет) - его значение нужно знать ДО разбора зависимых полей ниже.
    for f in independent_fields:
        tag = f["tag"]
        allowed = f.get("allowed_values")
        picked = pick_value_via_gpt(f, niche_context, allowed, f.get("format_rule"))
        if picked.get("value") is None:
            if f.get("required"):
                needs_clarification.append({
                    "tag": tag, "name_ru": f.get("name_ru"),
                    "question": picked.get("clarify_question") or f"Уточните значение поля «{f.get('name_ru')}»",
                })
            # необязательное поле, ИИ не смог однозначно подобрать значение - просто пропускаем,
            # это не повод ни блокировать создание черновика, ни дёргать клиента вопросом
        else:
            resolved[tag] = picked["value"]

    # Второй проход - условно обязательные поля (Width/Height/Depth/Color и т.п. с dependency вида
    # "Обязательно, если в поле Доступность указано значение 'В наличии'"). Если товар оказался
    # "Под заказ" (изготавливается индивидуально, точного размера/цвета нет в принципе) - Avito сам
    # снимает обязательность этих полей, поэтому НЕ гадаем и НЕ усредняем значение - оставляем пустыми,
    # это честный и официально предусмотренный путь, а не наша самодеятельность.
    # Прочие условные зависимости, не связанные с Availability, оставляем как раньше без изменений -
    # не подставляем автоматически, не спрашиваем (не наш случай, отдельная задача).
    in_stock_dependency = re.compile(r"доступн\w*.{0,60}в\s+наличии", re.IGNORECASE)
    availability = resolved.get("Availability")
    for f in dependent_fields:
        dep_text = f.get("depends_on") or ""
        if not in_stock_dependency.search(dep_text):
            continue  # незнакомая нам условная зависимость - прежнее консервативное поведение
        if availability == "Под заказ":
            continue  # официально необязательно для товаров под заказ - не выдумываем значение
        if f.get("required"):
            needs_clarification.append({
                "tag": f["tag"], "name_ru": f.get("name_ru"),
                "question": f"Уточните значение поля «{f.get('name_ru')}» (обязательно для товара в наличии)",
            })

    return {"status": "ok", "resolved": resolved, "needs_clarification": needs_clarification}


async def _anon_search_category(niche: str, city: str = "moskva") -> dict | None:
    """Анонимный поиск по нише на Avito, клик на первое объявление, категория из URL пути."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    from urllib.parse import quote
    from proxy_pool import get_playwright_proxy, get_clean_playwright_proxy

    stealth = Stealth()
    url = f"https://www.avito.ru/{city}?q={quote(niche)}"

    async with async_playwright() as p:
        for attempt in range(3):
            proxy = await get_clean_playwright_proxy()
            browser = None
            try:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 900}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media") else route.continue_())
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                item = page.locator("[data-marker='item-title']").first
                await item.click(timeout=8000)
                await page.wait_for_timeout(2500)

                final_url = page.url
                await browser.close()

                # путь категории — сегменты URL между доменом/городом и /id_объявления
                # пример: avito.ru/moskva/mebel_i_interer/shkafy_i_bufety/... -> берём читаемые сегменты
                import re
                m = re.search(r"avito\.ru/[^/]+/([a-z0-9_]+(?:/[a-z0-9_]+)*)", final_url)
                if m:
                    path_slug = m.group(1)
                    return {"source": "anonymous_search", "url": final_url, "path_slug": path_slug}
                return None
            except Exception as e:
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                continue
    return None


def resolve_category_for_account(account_id: str, niche: str, city: str = "moskva") -> dict:
    """
    Определяет категорию Avito для аккаунта.
    Приоритет: 1) реальные опубликованные объявления через API, 2) анонимный поиск по нише.
    Возвращает {"category_name": str, "source": "api"|"anonymous_search", ...} или {"error": ...}.
    """
    import httpx
    import asyncio
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.api.avito import get_avito_token

    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"error": f"аккаунт {account_id} не найден"}

        token_data = get_avito_token(account_id)
        token = token_data.get("access_token") if isinstance(token_data, dict) else None
        if not token:
            return {"error": "не удалось получить токен Avito"}

        # метод 1: реальные объявления
        try:
            items_resp = httpx.get(
                "https://api.avito.ru/core/v1/items",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 1, "page": 1, "status": "active"},
                timeout=15
            )
            if items_resp.status_code == 200:
                resources = items_resp.json().get("resources", [])
                if resources:
                    cat = resources[0].get("category", {})
                    cat_name = cat.get("name", "")
                    if cat_name:
                        return {"category_name": cat_name, "source": "api", "category_id": cat.get("id")}
        except Exception:
            pass

        # метод 2: подбор из НАШЕГО справочника категорий (660 полных шаблонов) — без похода в Avito
        from app.models.category_template import CategoryTemplate as _CT
        nm = (niche or "").strip()
        tpl = None
        if nm:
            # сначала по category_id (короткий ключ ниши), потом по category_name (полный путь/имя)
            tpl = db.query(_CT).filter(_CT.category_id.ilike("%" + nm + "%")).first()
            if not tpl:
                tpl = db.query(_CT).filter(_CT.category_name.ilike("%" + nm + "%")).first()
        if tpl:
            # человекочитаемое имя категории — последний сегмент пути category_name
            cat_path = tpl.category_name or nm
            readable = cat_path.split(">")[-1].strip() if ">" in cat_path else cat_path
            return {
                "category_name": readable,
                "source": "local_catalog",
                "category_id": tpl.category_id,
                "template_id": tpl.template_id,
                "path": tpl.category_name
            }

        return {"error": f"категория для ниши '{niche}' не найдена в справочнике"}
    finally:
        db.close()
