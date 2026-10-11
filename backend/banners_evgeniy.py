import glob, os
from banner_generator import generate_infographic_banner

ACC = "evgeniy_peregorodki_12447"
SRC = "images/%s/steklyannye_peregorodki" % ACC
OUT = "images/%s/banners/infographic" % ACC
ICONS = "icons/selected"
os.makedirs(OUT, exist_ok=True)

ITEMS = [
    ("Закалённое стекло 8-10 мм", "Прочно и безопасно",      ["diamond", "shield-check", "shield", "circle-check"]),
    ("Замер и проект",            "Перед изготовлением",     ["ruler", "ruler-2", "pencil", "circle-check"]),
    ("Гарантия 1 год",            "На фурнитуру",            ["award", "certificate", "rosette-discount-check", "circle-check"]),
    ("Душевая без поддона",       "Ровный пол, чистая линия",["droplet", "bath", "check", "circle-check"]),
    ("В нишу и в угол",           "Любая конфигурация",      ["layout", "border-corners", "square", "circle-check"]),
    ("По вашим размерам",         "Нестандарт не проблема",  ["ruler-2", "ruler", "settings", "circle-check"]),
    ("Монтаж под ключ",           "Изготовим и установим",   ["tool", "tools", "hammer", "circle-check"]),
    ("Для новостройки",           "Установка после отделки", ["building", "home", "building-community", "circle-check"]),
    ("Замена старой шторки",      "Обновим за один выезд",   ["refresh", "repeat", "exchange", "circle-check"]),
    ("Для маленькой ванной",      "Не съедаем пространство", ["home", "ruler", "check", "circle-check"]),
]

def pick(names):
    for n in names:
        if os.path.isfile("%s/%s.svg" % (ICONS, n)):
            return n + ".svg"
    return "circle-check.svg"

photos = sorted(glob.glob(SRC + "/*.jpg"), key=os.path.getsize, reverse=True)
ok = 0
for i, ((title, subtitle, icons), photo) in enumerate(zip(ITEMS, photos), 1):
    icon = pick(icons)
    out = "%s/evg_banner_%02d.png" % (OUT, i)
    try:
        generate_infographic_banner(
            title=title, subtitle=subtitle, price_text="", icon_name=icon,
            accent_color="#4FC3F7", subtitle_color="#F0F0F0",
            output_path=out, own_photo_path=photo,
            account_id=ACC, operation="banner_evgeniy",
        )
        print("%2d %-28s %-24s %d байт" % (i, title, icon, os.path.getsize(out)))
        ok += 1
    except Exception as e:
        print("%2d %-28s %-24s ОШИБКА: %s" % (i, title, icon, str(e)[:60]))
print("=== ГОТОВО: %d из %d" % (ok, len(ITEMS)))
