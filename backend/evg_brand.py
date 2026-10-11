import json, sys, time
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"

STYLE = {
    "brand": "Стеклянные перегородки",
    "niche": "peregorodki",
    "scene": "стеклянная душевая перегородка из закалённого стекла в ванной комнате",
    "colors": {"brand": "#0E7490", "accent": "#4FC3F7", "graphite": "#12181F",
               "paper": "#FFFFFF", "surface": "#F2F7FA"},
    "proportions": {"light_pct": [75, 80], "brand_pct": [15, 20], "accent_pct": [5, 10],
                    "rule": "Акцент только на призыве и одном преимуществе."},
    "schemes": {"custom": {"primary": "#0E7490", "radius_px": 16,
                           "badge": "НА ЗАКАЗ", "price_allowed": False}},
    "typography": {"heading": "Montserrat Bold", "body": "Inter",
                   "fallback": ["Arial", "Calibri"], "max_words": 12},
    "forbidden": ["дешевле всех", "бесплатно", "скидка до подтверждения",
                  "сроки без подтверждения клиента", "цены без подтверждения клиента",
                  "чужие бренды в тексте"],
}

T = [
    ("Закалённое стекло 8-10 мм", "Прочно и безопасно", "крупный план торца толстого стекла", "Материалы"),
    ("Замер и проект", "Перед изготовлением", "замер стеклянной перегородки рулеткой", "Процесс"),
    ("Гарантия 1 год", "На фурнитуру", "хромированная фурнитура крупным планом", "Гарантии"),
    ("Душевая без поддона", "Ровный пол, чистая линия", "душевая зона без поддона, трап в полу", "Типы"),
    ("В нишу и в угол", "Любая конфигурация", "угловое стеклянное ограждение", "Типы"),
    ("По вашим размерам", "Нестандарт не проблема", "узкая стеклянная перегородка нестандартного размера", "Преимущества"),
    ("Монтаж под ключ", "Изготовим и установим", "установленная стеклянная перегородка в ванной", "Процесс"),
    ("Для новостройки", "Установка после отделки", "новая ванная комната после отделки", "Сценарии"),
    ("Замена старой шторки", "Обновим за один выезд", "раздвижная стеклянная дверь в душ", "Сценарии"),
    ("Для маленькой ванной", "Не съедаем пространство", "компактная ванная со стеклянной стенкой", "Сценарии"),
]
LIB = [{"id": "peregorodki_%03d" % (i + 1), "niche": "peregorodki", "type": "Изготовление",
        "subtype": t[0].lower(), "block": t[3], "goal": "Продажа через Авито",
        "title": t[0], "subtitle": t[1], "has_price": False, "price": "",
        "photo": t[2], "style": STYLE["brand"], "scheme": "custom",
        "cta": "Рассчитать стоимость"} for i, t in enumerate(T)]

db = SessionLocal()
for key, val in (("brand_style", STYLE), ("banner_library", LIB)):
    row = db.execute(text("select id from storage where account_id=:a and key=:k"), {"a": ACC, "k": key}).fetchone()
    print("=== %s: %s | элементов: %s" % (key, "ОБНОВИТ" if row else "СОЗДАСТ",
          len(val) if isinstance(val, list) else len(val.keys())))
    if APPLY:
        v = json.dumps(val, ensure_ascii=False)
        if row:
            db.execute(text("update storage set value=:v where id=:i"), {"v": v, "i": row[0]})
        else:
            db.execute(text("insert into storage (account_id, key, value) values (:a,:k,:v)"),
                       {"a": ACC, "k": key, "v": v})
for t in LIB[:3]:
    print("   ", t["id"], "|", t["title"], "|", t["photo"])
if APPLY:
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
