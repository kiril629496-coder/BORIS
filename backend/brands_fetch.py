import asyncio, re, sys

URL = "https://www.avito.ru/web/1/catalogs/content/feed/dushevye_ugolki_peregorodki_i_dveri.xml"

async def main():
    from playwright.async_api import async_playwright
    body = None
    async with async_playwright() as p:
        for a in (1, 2, 3):
            b = await p.chromium.launch(headless=False, args=["--no-sandbox"])
            try:
                pg = await b.new_page()
                r = await pg.goto(URL, timeout=60000, wait_until="domcontentloaded")
                print("попытка", a, "| HTTP:", r.status if r else "-")
                if r and r.status == 200:
                    body = await r.text()
                    break
            except Exception as ex:
                print("попытка", a, "ошибка:", type(ex).__name__, str(ex)[:80])
            finally:
                await b.close()
            await asyncio.sleep(3)
    if not body:
        print("=== СПРАВОЧНИК НЕ ПОЛУЧЕН"); sys.exit(1)

    open("/tmp/brands.xml", "w").write(body)
    print("=== получено байт:", len(body))
    print("=== начало:", re.sub(r"\s+", " ", body[:200]))
    tags = re.findall(r"<([A-Za-z_][A-Za-z0-9_]*)[ >]", body)
    from collections import Counter
    print("=== теги:", Counter(tags).most_common(6))
    vals = re.findall(r"<(?:Brand|brand|Name|name|Value|value|Item|item)>([^<]+)<", body)
    print("=== значений:", len(vals), "| первые 12:", vals[:12])
    for w in ("Без бренда", "Другой", "Нет бренда", "Не указан", "Прочие", "Своё", "Нет данных"):
        hits = [v for v in vals if w.lower() in v.lower()]
        if hits:
            print("=== ВАРИАНТ ПОД СВОЁ ПРОИЗВОДСТВО:", w, "->", hits[:3])

asyncio.run(main())
