import os
from banner_generator import generate_infographic_banner

ACC = "evgeniy_peregorodki_12447"
OUT = "images/%s/banners/infographic" % ACC
os.makedirs(OUT, exist_ok=True)

SCENE = ("modern bathroom with frameless clear tempered glass shower partition, "
         "chrome hardware, light tiles, clean minimal interior, daylight")

ITEMS = [
    ("Закалённое стекло 8-10 мм", "Прочно и безопасно",       "diamond.svg",        SCENE + ", close-up of thick glass edge"),
    ("Замер и проект",            "Перед изготовлением",      "ruler.svg",          SCENE + ", measuring tape on glass"),
    ("Гарантия 1 год",            "На фурнитуру",             "award.svg",          SCENE + ", polished chrome hinges detail"),
    ("Душевая без поддона",       "Ровный пол, чистая линия", "droplet.svg",        SCENE + ", walk-in shower without tray, floor drain"),
    ("В нишу и в угол",           "Любая конфигурация",       "border-corners.svg", SCENE + ", corner glass enclosure"),
    ("По вашим размерам",         "Нестандарт не проблема",   "ruler-2.svg",        SCENE + ", narrow custom glass panel"),
    ("Монтаж под ключ",           "Изготовим и установим",    "tool.svg",           SCENE + ", installed glass panel, tools nearby"),
    ("Для новостройки",           "Установка после отделки",  "building.svg",       SCENE + ", brand new finished bathroom"),
    ("Замена старой шторки",      "Обновим за один выезд",    "refresh.svg",        SCENE + ", sliding glass door"),
    ("Для маленькой ванной",      "Не съедаем пространство",  "home.svg",           SCENE + ", very small compact bathroom"),
]

ok = 0
for i, (title, subtitle, icon, query) in enumerate(ITEMS, 1):
    out = "%s/evg_ai_%02d.png" % (OUT, i)
    if os.path.exists(out) and os.path.getsize(out) > 100000:
        print("%2d %-28s уже есть, пропускаю" % (i, title)); continue
    try:
        generate_infographic_banner(
            title=title, subtitle=subtitle, price_text="", icon_name=icon,
            accent_color="#4FC3F7", subtitle_color="#F0F0F0",
            output_path=out, photo_query=query, photo_source="ai",
            ai_quality="medium", ai_model="gpt-image-2",
            account_id=ACC, operation="banner_evgeniy_ai",
        )
        print("%2d %-28s %s (%d байт)" % (i, title, os.path.basename(out), os.path.getsize(out)))
        ok += 1
    except Exception as e:
        print("%2d %-28s ОШИБКА: %s" % (i, title, str(e)[:80]))
print("=== ГОТОВО: %d из %d | фон рисует OpenAI gpt-image-2, текст накладывается точь-в-точь" % (ok, len(ITEMS)))
