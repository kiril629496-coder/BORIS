# -*- coding: utf-8 -*-
import os, json
for l in open('/root/BORIS/backend/.env'):
    l = l.strip()
    if l and not l.startswith('#') and '=' in l:
        k, v = l.split('=', 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))
from sqlalchemy import text
from app.db.session import SessionLocal

NICHE = "bytovki"
STYLE = {
 "brand": "План Б", "niche": NICHE,
 "colors": {"brand": "#1B4FD8", "accent": "#FF4D2E", "graphite": "#141821",
            "paper": "#FFFFFF", "surface": "#F4F6FA"},
 "proportions": {"light_pct": [75, 80], "brand_pct": [15, 20], "accent_pct": [5, 10],
   "rule": "Оранжевый только на кнопке, цене, одном преимуществе, маркере направления и призыве."},
 "schemes": {
   "rent": {"primary": "#1B4FD8", "radius_px": 24, "badge": "АРЕНДА", "price_allowed": True},
   "sale": {"primary": "#141821", "radius_px": 6, "badge": "ПРОДАЖА", "price_allowed": False}},
 "typography": {"heading": "Montserrat Bold", "body": "Inter",
                "fallback": ["Arial", "Calibri"], "max_words": 12},
 "prices_allowed": {"обычная": "7 000 ₽/мес", "ИТР": "8 000 ₽/мес", "распашонка": "9 000 ₽/мес",
                    "залог": "10 000 ₽", "мебель": "500 ₽/шт", "минимальный срок": "30 дней"},
 "forbidden": ["в наличии без оговорки", "доставка сегодня", "от 3 дней", "650+ единиц",
   "90% возвращаются", "дешевле всех", "бесплатная доставка", "цены со старых сайтов",
   "акционные цены до подтверждения", "телефон и адрес до подтверждения",
   "гомоглифы", "любой текст от нейросети на картинке"],
 "photo_rules": {"order": "по убыванию качества, лучшее фото первым",
   "forbidden": ["чужой брендинг", "водяные знаки", "скриншоты из мессенджеров", "грязный фон"]},
}
