import re, json, time, asyncio
import requests
from bs4 import BeautifulSoup

ROOT_URL = "https://autoload.avito.ru/format/xmlcheck"
BASE_AUTOLOAD = "https://autoload.avito.ru/format"


def fetch_top_level_categories():
    """Получает список категорий верхнего уровня с корневой страницы (простой requests)."""
    r = requests.get(ROOT_URL, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")
    menu = soup.find(class_="helpdesk-list")
    categories = []
    if not menu:
        return categories
    for a in menu.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href.startswith("../") and href != "../":
            slug = href.replace("../", "")
            categories.append({"name": text, "slug": slug})
    return categories


def fetch_subcategory_template_ids(slug: str):
    """Для категории верхнего уровня получает список ссылок на templates/{id} (простой requests, с прокси и retry на случай rate-limit)."""
    from proxy_pool import get_requests_proxies
    import time as _time

    url = f"{BASE_AUTOLOAD}/{slug}"
    r = None
    for attempt in range(3):
        try:
            proxies = get_requests_proxies()
            r = requests.get(url, timeout=15, proxies=proxies, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            })
            if r.status_code == 200:
                break
        except Exception:
            r = None
        _time.sleep(1.5)
    if r is None or r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    links = soup.find_all("a", href=re.compile(r"/autoload/documentation/templates/\d+"))
    result = []
    seen_ids = set()
    for a in links:
        href = a.get("href")
        m = re.search(r"/templates/(\d+)", href)
        if not m:
            continue
        tid = m.group(1)
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        result.append({"template_id": tid, "label": a.get_text(strip=True)})
    return result


async def fetch_template_params_headed(template_id: str):
    """Рендерит страницу параметров конкретного шаблона через headed-Chromium (JS SPA, но без антибота/прокси)."""
    from playwright.async_api import async_playwright

    url = f"https://www.avito.ru/autoload/documentation/templates/{template_id}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        page = await browser.new_page()
        # Блокируем только медиа/картинки/шрифты - script НЕ трогаем, страница JS SPA
        # и может не отрендериться без выполнения скриптов (в отличие от выдачи Avito).
        async def _block_media(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_media)
        try:
            await page.goto(url, timeout=25000, wait_until="networkidle")
            await page.wait_for_timeout(1500)
            html = await page.content()
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "lxml")
    containers = soup.find_all(class_=re.compile(r"^field-info-info-container"))
    params = []
    for c in containers:
        main_info = c.find(class_=re.compile(r"^field-main-info-root"))
        desc = c.find(class_=re.compile(r"^field-description-description"))
        if not main_info:
            continue
        parts = main_info.get_text(separator="|", strip=True).split("|")
        name = parts[0] if parts else None
        required_text = parts[1] if len(parts) > 1 else None
        params.append({
            "name": name,
            "required": required_text == "Обязательный",
            "description": desc.get_text(separator=" ", strip=True) if desc else None,
        })
    return params


# ==================== API роутер ====================
from fastapi import APIRouter
from app.db.session import SessionLocal
from app.models.avito_category import AvitoCategory, AvitoCategoryParam
from app.models.task import Task
import json as _json_api

router = APIRouter(prefix="/api/avito_categories", tags=["avito_categories"])


@router.post("/sync_start")
def sync_start():
    """Запускает полный обход категорий Avito в фоне через очередь задач."""
    db = SessionLocal()
    try:
        task = Task(task_type="avito_categories_sync", payload="{}", status="queued")
        db.add(task)
        db.commit()
        db.refresh(task)
        return {"status": "ok", "task_id": task.id}
    finally:
        db.close()


@router.get("/tree")
def get_tree():
    """Возвращает сохранённое дерево категорий + количество параметров у каждой."""
    db = SessionLocal()
    try:
        categories = db.query(AvitoCategory).all()
        result = []
        for c in categories:
            params_count = db.query(AvitoCategoryParam).filter(
                AvitoCategoryParam.template_id == c.template_id
            ).count()
            result.append({
                "slug": c.slug,
                "name": c.name,
                "template_id": c.template_id,
                "template_label": c.template_label,
                "params_count": params_count,
            })
        return {"status": "ok", "categories": result, "total": len(result)}
    finally:
        db.close()


@router.get("/params")
def get_params(template_id: str):
    """Возвращает параметры конкретного шаблона."""
    db = SessionLocal()
    try:
        params = db.query(AvitoCategoryParam).filter(
            AvitoCategoryParam.template_id == template_id
        ).all()
        return {
            "status": "ok",
            "template_id": template_id,
            "params": [
                {"name": p.name, "required": p.required, "description": p.description}
                for p in params
            ],
        }
    finally:
        db.close()


def save_category_params_to_db(slug: str, name: str, template_id: str, template_label: str, params: list):
    """Сохраняет параметры шаблона категории в базу знаний, с проверкой на явные несоответствия
    (например, автомобильные поля в неавтомобильной категории — признак ошибки в определении шаблона)."""
    from app.db.session import SessionLocal
    from app.models.avito_category import AvitoCategory, AvitoCategoryParam

    car_only_fields = {"VIN", "Make", "Model", "CarType", "FuelType", "Transmission", "BodyType", "DriveType", "Doors"}
    param_names = {p.get("name") for p in params}
    is_suspicious = (slug != "cars" and "gruzoviki" not in slug and len(param_names & car_only_fields) >= 3)

    if is_suspicious:
        return {"status": "skipped", "reason": "suspicious_mismatch", "matched_car_fields": list(param_names & car_only_fields)}

    db = SessionLocal()
    try:
        cat = db.query(AvitoCategory).filter(AvitoCategory.template_id == template_id).first()
        if not cat:
            cat = AvitoCategory(slug=slug, name=name, template_id=template_id, template_label=template_label)
            db.add(cat)
            db.commit()

        db.query(AvitoCategoryParam).filter(AvitoCategoryParam.template_id == template_id).delete()
        for p in params:
            db.add(AvitoCategoryParam(
                template_id=template_id,
                name=p.get("name", ""),
                required=p.get("required", False),
                description=(p.get("description") or "")[:2000]
            ))
        db.commit()
        return {"status": "ok", "saved_params": len(params)}
    finally:
        db.close()


def get_category_params_from_db(template_id: str) -> list:
    """Достаёт сохранённые параметры категории из базы знаний."""
    from app.db.session import SessionLocal
    from app.models.avito_category import AvitoCategoryParam
    db = SessionLocal()
    try:
        rows = db.query(AvitoCategoryParam).filter(AvitoCategoryParam.template_id == template_id).all()
        return [{"name": r.name, "required": r.required, "description": r.description} for r in rows]
    finally:
        db.close()


def ai_fill_category_fields(template_id: str, title: str, price: str, characteristics: dict) -> dict:
    """Через ИИ сопоставляет характеристики товара с обязательными полями категории Avito.
    Значения выбираются СТРОГО из списка допустимых (allowed_values), извлечённого из базы знаний -
    ИИ не может придумать значение, которого нет в реальном списке Avito."""
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole
    import os, json as _json, re as _re

    params = get_category_params_from_db(template_id)
    skip_names = {"Уникальный идентификатор объявления", "Начало размещения", "Окончание размещения",
                  "Способ размещения", "Услуга продвижения", "Номер объявления на Авито", "Контактное лицо",
                  "Номер телефона", "Адрес", "Широта", "Долгота", "Идентификатор адреса", "Название объявления",
                  "Описание объявления", "Ссылки на фото", "Названия фото", "Ссылка на видео", "Способ связи",
                  "Настройка цены целевого действия", "Настройка цены целевого действия: автоматическая",
                  "Настройка цены целевого действия: ручная", "Интернет звонки", "Устройства для приёма звонков",
                  "Способ доставки", "Вес (Для Доставки)", "Длина (Для Доставки)", "Высота (Для Доставки)",
                  "Ширина (Для Доставки)", "Возвраты", "Субсидирование доставки", "Цена", "URL видеофайла",
                  "Категория"}  # Категория задаётся отдельно на уровне аккаунта, не через это поле

    # универсальные поля с фиксированными значениями, которые не нужно доверять ИИ гадать
    HARDCODED_ALLOWED = {
        "Состояние": ["Новое", "Б/у"],
    }

    relevant = []
    for p in params:
        if not p.get("required") or p.get("name") in skip_names:
            continue
        name = p["name"]
        if name in HARDCODED_ALLOWED:
            relevant.append({"name": name, "allowed": HARDCODED_ALLOWED[name]})
            continue
        desc = p.get("description") or ""
        m = _re.search(r"ДОПУСТИМЫЕ ЗНАЧЕНИЯ: (.+)$", desc)
        allowed = [v.strip() for v in m.group(1).split(",")] if m else None
        relevant.append({"name": name, "allowed": allowed})

    if not relevant:
        return {}

    chars_text = "\n".join([f"- {k}: {v}" for k, v in characteristics.items()]) if characteristics else "нет данных"
    fields_lines = []
    for f in relevant:
        if f["allowed"]:
            fields_lines.append(f"- {f['name']}: ОБЯЗАТЕЛЬНО выбери РОВНО ОДНО значение из списка: {f['allowed']}")
        else:
            fields_lines.append(f"- {f['name']}: свободное значение по смыслу товара")
    fields_text = "\n".join(fields_lines)

    prompt = f"""Ты заполняешь обязательные поля объявления на Avito для товара.

Товар: {title}
Цена: {price}
Характеристики: {chars_text}

Поля для заполнения:
{fields_text}

КРИТИЧЕСКИ ВАЖНО: там где указан список допустимых значений, ты ОБЯЗАН выбрать значение ТОЧНО как оно написано в списке, без изменений, сокращений или синонимов. Не придумывай новые варианты.

Ответь СТРОГО в формате JSON без пояснений: {{"ИмяПоля1": "значение1", "ИмяПоля2": "значение2"}}"""

    try:
        raw = chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.1, max_tokens=500
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = _json.loads(raw)
        if not isinstance(result, dict):
            return {}

        # ФИНАЛЬНАЯ ПРОВЕРКА: если для поля был список допустимых значений, а ИИ вернул то, чего там нет — отбрасываем
        validated = {}
        allowed_map = {f["name"]: f["allowed"] for f in relevant if f["allowed"]}
        for k, v in result.items():
            if k in allowed_map:
                if v in allowed_map[k]:
                    validated[k] = v
                else:
                    print(f"[ai_fill] ИИ вернул недопустимое значение для {k}: {v!r}, поле пропущено")
            else:
                validated[k] = v
        return validated
    except Exception as e:
        print(f"[ai_fill] ошибка автозаполнения полей: {e}")
        return {}


async def fetch_template_params_with_values_headed(template_id: str):
    """Как fetch_template_params_headed, но дополнительно раскрывает КАЖДЫЙ параметр кликом
    и вытаскивает точные допустимые значения (enum), где они есть."""
    from playwright.async_api import async_playwright
    import re as _re

    url = f"https://www.avito.ru/autoload/documentation/templates/{template_id}"
    result = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        page = await browser.new_page()
        async def _block_media(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_media)
        await page.goto(url, timeout=25000, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # получаем базовый список параметров как раньше
        base_params = await fetch_template_params_headed(template_id)

        # для каждого ОБЯЗАТЕЛЬНОГО поля кликаем и вытаскиваем "Одно из значений"
        for param in base_params:
            if not param.get("required"):
                continue
            name = param.get("name")
            try:
                el = page.get_by_text(name, exact=True).first
                await el.click()
                await page.wait_for_timeout(400)
                text = await page.inner_text("body")
                idx = text.find(name)
                if idx == -1:
                    continue
                chunk = text[idx:idx+800]
                if "Одно из значений" in chunk:
                    # вытаскиваем строки-значения между "Одно из значений N" и "Пример заполнения"
                    m = _re.search(r"Одно из значений \d+(.*?)Пример заполнения", chunk, _re.DOTALL)
                    if m:
                        raw_values = m.group(1)
                        values = [v.strip("— \n\t") for v in raw_values.split("—") if v.strip("— \n\t")]
                        param["allowed_values"] = values
            except Exception:
                continue

        result = base_params
        await browser.close()
    return result
