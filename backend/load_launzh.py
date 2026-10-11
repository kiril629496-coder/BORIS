import os, json, sys
for l in open('/root/BORIS/backend/.env'):
    l = l.strip()
    if l and not l.startswith('#') and '=' in l:
        k, v = l.split('=', 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))
from sqlalchemy import text
from app.db.session import SessionLocal
NICHE = "mebel"
ACC = "andrey_launzh_mebel_akkaunt_penza_82435"
STYLE = {
 "brand": "ЛАУНЖ", "niche": NICHE,
 "colors": {"brand": "#2F2F33", "accent": "#C08A4E", "graphite": "#1C1C1F",
            "paper": "#FFFFFF", "surface": "#F6F3EF"},
 "proportions": {"light_pct": [75, 80], "brand_pct": [15, 20], "accent_pct": [5, 10],
   "rule": "Тёплый акцент только на призыве и одном ключевом элементе. Большие заливки запрещены."},
 "schemes": {"custom": {"primary": "#2F2F33", "radius_px": 12, "badge": "НА ЗАКАЗ", "price_allowed": False}},
 "typography": {"heading": "Montserrat Bold", "body": "Inter",
                "fallback": ["Arial", "Calibri"], "max_words": 12},
 "prices_allowed": {},
 "forbidden": ["дешевле всех", "бесплатная доставка", "скидка без подтверждения",
   "любые цены на баннере", "гарантия без подтверждения", "любой текст от нейросети на картинке"],
 "photo_rules": {"order": "по убыванию качества, лучшее фото первым",
   "forbidden": ["чужой брендинг", "водяные знаки", "интерьеры из стоков"]},
}
T = [
 ("Кухни", "кухня прямая", "Кухня прямая 3 метра графит", "на заказ по вашим размерам, Пенза", "кухня графит в интерьере", "Рассчитать кухню"),
 ("Кухни", "кухня без предоплаты", "Сначала увидите, потом заплатите", "показываем проект до оплаты", "кухня крупным планом", "Посмотреть проект"),
 ("Шкафы-купе", "шкаф-купе", "Шкаф-купе 2000х2400", "на заказ по вашим размерам, Пенза", "шкаф-купе в интерьере", "Рассчитать шкаф"),
 ("Шкафы-купе", "гардеробная", "Гардеробная 1800х2400", "на заказ по вашим размерам, Пенза", "гардеробная система", "Обсудить проект"),
]
lib = [{"id": "%s_custom_%03d" % (NICHE, i), "niche": NICHE, "type": "На заказ",
        "subtype": st, "block": bl, "goal": "Продажа через Авито",
        "title": ti, "subtitle": sub, "has_price": False, "price": "",
        "photo": ph, "style": "ЛАУНЖ", "scheme": "custom", "cta": cta}
       for i, (bl, st, ti, sub, ph, cta) in enumerate(T, 1)]
APPLY = "--apply" in sys.argv
db = SessionLocal()
def put(key, val):
    v = json.dumps(val, ensure_ascii=False)
    row = db.execute(text("select id, length(value) from storage where account_id=:a and key=:k"),
                     {"a": ACC, "k": key}).fetchone()
    print("  %-16s %-26s -> %s симв" % (key, ("ОБНОВИТ (была %s)" % row[1]) if row else "СОЗДАСТ", len(v)))
    if APPLY:
        if row:
            db.execute(text("update storage set value=:v where id=:i"), {"v": v, "i": row[0]})
        else:
            db.execute(text("insert into storage (account_id, key, value) values (:a,:k,:v)"),
                       {"a": ACC, "k": key, "v": v})
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР (dry-run). Для записи добавьте --apply")
print("аккаунт:", ACC)
put("brand_style", STYLE)
put("banner_library", lib)
if APPLY:
    db.commit()
    print("\nЗАПИСАНО. Read-back:")
    for key in ("brand_style", "banner_library"):
        d = json.loads(db.execute(text("select value from storage where account_id=:a and key=:k"),
                                  {"a": ACC, "k": key}).scalar())
        print("  %-16s %s" % (key, ("%s шаблонов: %s" % (len(d), [x["id"] for x in d])) if isinstance(d, list)
                              else "ключи: %s" % sorted(d.keys())))
else:
    print("\nНичего не записано.")
db.close()
