import asyncio, hashlib, json, re, sys, datetime
from bs4 import BeautifulSoup
from app.api import avito_categories as AC

TID = "90576"
DATE = datetime.date.today().isoformat()
BASE = "/root/BORIS/backend/contracts/%s_%s" % (TID, DATE)
URL = "https://www.avito.ru/autoload/documentation/templates/%s" % TID
SEL = '[class^="field-info-info-container"]'
LAT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,}$")

async def grab(p):
    b = await p.chromium.launch(headless=False, args=["--no-sandbox"])
    try:
        pg = await b.new_page()
        async def block(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await pg.route("**/*", block)
        await pg.goto(URL, timeout=60000, wait_until="domcontentloaded")
        await pg.wait_for_selector(SEL, timeout=45000)
        await pg.wait_for_timeout(2000)
        return await pg.content()
    finally:
        await b.close()

async def main():
    from playwright.async_api import async_playwright
    html = None
    async with async_playwright() as p:
        for a in (1, 2, 3):
            try:
                html = await grab(p)
                print("=== HTML получен с попытки", a)
                break
            except Exception as ex:
                print("=== попытка", a, "не удалась:", type(ex).__name__, str(ex)[:110])
    if not html:
        print("=== СТРАНИЦА НЕ ПОЛУЧЕНА, НИЧЕГО НЕ ЗАПИСАНО")
        sys.exit(1)

    open(BASE + ".html", "w").write(html)

    soup = BeautifulSoup(html, "lxml")
    conts = soup.find_all(class_=re.compile(r"^field-info-info-container"))
    raw = []
    for c in conts:
        mi = c.find(class_=re.compile(r"^field-main-info-root"))
        if not mi:
            continue
        parts = mi.get_text(separator="|", strip=True).split("|")
        desc = c.find(class_=re.compile(r"^field-description-description"))
        raw.append({"parts": parts, "desc": desc.get_text(" ", strip=True) if desc else None})
    json.dump(raw, open(BASE + "_raw.json", "w"), ensure_ascii=False, indent=1)

    try:
        vals = await AC.fetch_template_params_with_values_headed(TID)
    except Exception as ex:
        print("=== enum-проход не удался:", type(ex).__name__, str(ex)[:110])
        vals = []
    by_name = {str(v.get("name")): v for v in vals if isinstance(v, dict)}

    fields = []
    for r in raw:
        parts = r["parts"]
        name_ru = parts[0] if parts else None
        api = None
        for t in parts[1:]:
            if LAT.match(t.strip()):
                api = t.strip()
                break
        v = by_name.get(str(name_ru), {})
        fields.append({
            "name_ru": name_ru,
            "api_name": api,
            "required": bool(v.get("required")) if v else ("Обязательный" in parts),
            "allowed_values": v.get("allowed_values"),
            "parts": parts,
            "description": r["desc"],
        })

    doc = {
        "template_id": TID,
        "captured": DATE,
        "source": URL,
        "fields_total": len(fields),
        "required": [f["name_ru"] for f in fields if f["required"]],
        "enums": {f["name_ru"]: f["allowed_values"] for f in fields if f["allowed_values"]},
        "warnings": [
            "Поле 'Названия фото': значения ['По телефону и в сообщениях','По телефону','В сообщениях'] "
            "являются АРТЕФАКТОМ сопоставления соседних HTML-блоков (это enum способа связи). "
            "К названиям фото отношения не имеют, в базу не вносить."
        ],
        "fields": fields,
    }
    json.dump(doc, open(BASE + "_normalized.json", "w"), ensure_ascii=False, indent=1)

    print("=== ПОЛЕЙ:", len(fields), "| ОБЯЗАТЕЛЬНЫХ:", len(doc["required"]))
    print("=== РУССКОЕ ИМЯ ~ API-ИМЯ ~ ОБЯЗАТЕЛЬНОСТЬ ~ ENUM")
    for f in fields:
        if f["required"]:
            print(" *", f["name_ru"], "~", f["api_name"], "~ ОБЯЗ ~",
                  json.dumps(f["allowed_values"], ensure_ascii=False))
    print("=== НЕОБЯЗАТЕЛЬНЫЕ (только имена) ===")
    print(", ".join("%s=%s" % (f["name_ru"], f["api_name"]) for f in fields if not f["required"]))
    for suf in (".html", "_raw.json", "_normalized.json"):
        p_ = BASE + suf
        h = hashlib.sha256(open(p_, "rb").read()).hexdigest()
        print("SHA256", p_, h)

asyncio.run(main())
