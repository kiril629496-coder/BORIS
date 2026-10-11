import asyncio, hashlib, json, re, sys, datetime
from bs4 import BeautifulSoup

TID = sys.argv[1]
DATE = datetime.date.today().isoformat()
BASE = "/root/BORIS/backend/contracts/%s_%s" % (TID, DATE)
URL = "https://www.avito.ru/autoload/documentation/templates/%s" % TID
SEL = '[class^="field-info-info-container"]'

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
                html = await grab(p); break
            except Exception as ex:
                print("попытка", a, "не удалась:", type(ex).__name__, str(ex)[:90])
    if not html:
        print("=== СТРАНИЦА НЕ ПОЛУЧЕНА"); sys.exit(1)
    open(BASE + ".html", "w").write(html)
    soup = BeautifulSoup(html, "lxml")
    fields = []
    for d in soup.select('div[id^="field-"]'):
        parts = [x.strip() for x in re.sub(r"\s+", " ", d.get_text(" ~ ", strip=True)).split("~") if x.strip()]
        ex = ""
        for i, p_ in enumerate(parts):
            if p_.startswith("Пример заполнения") and i + 1 < len(parts):
                ex = parts[i + 1]; break
        fields.append({"tag": d.get("id")[6:], "label": parts[0] if parts else "",
                       "required": "Обязательный" in parts[:3], "example": ex})
    json.dump({"template_id": TID, "captured": DATE, "source": URL, "fields": fields},
              open(BASE + "_normalized.json", "w"), ensure_ascii=False, indent=1)
    print("=== ШАБЛОН", TID, "| полей:", len(fields), "| обязательных:", sum(f["required"] for f in fields))
    for f in fields:
        if f["required"]:
            print("%-24s %-34s %s" % (f["tag"], f["label"][:34], f["example"][:32]))
    print("SHA256", hashlib.sha256(open(BASE + ".html", "rb").read()).hexdigest()[:32])

asyncio.run(main())
