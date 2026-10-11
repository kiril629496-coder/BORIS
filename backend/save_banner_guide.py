import json, sys
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
GUIDE = {
  "version": "1.0",
  "updated": "2026-08-06",
  "rule": "Баннер целиком рисует OpenAI gpt-image-2. Текст поверх картинки не накладываем.",
  "endpoint": "POST /api/banners/full_ai",
  "fields": {
    "exact_text": "Заголовок 2-4 слова, конкретный факт. Пример: «Закалённое стекло 8-10 мм»",
    "exact_subtitle": "Расшифровка фактом. Пример: «толщина 8 и 10 мм, каленое, с полировкой кромки»",
    "exact_price": "Только подтверждённая цена клиента. Ставить примерно на 4 баннера из 10",
    "raw_description": "Сцена: объект, материал, свет, фон",
    "format": "infographic | extended_pc | extended_mobile | max_pc | max_mobile",
    "quality": "medium",
    "count": "до 5 вариантов за раз"
  },
  "prompt_example": {
    "exact_text": "Закалённое стекло 8-10 мм",
    "exact_subtitle": "толщина 8 и 10 мм, каленое, с полировкой кромки",
    "exact_price": "от 8 030 ₽",
    "raw_description": "стеклянная перегородка под углом, яркий блик на полированной кромке стекла, хромированный профиль, тёмная контрастная плитка позади, капли воды"
  },
  "rules": [
    "Заголовок — факт, а не лозунг. «Гарантия 1 год» лучше, чем «Качество и надёжность»",
    "Подзаголовок расшифровывает заголовок цифрами и условиями",
    "Цена только реальная: из прайса клиента или из его же объявлений",
    "Для прозрачных товаров (стекло, плёнка) в сцене обязательно указывать блики, отражения и контрастный фон, иначе товара не видно",
    "Тёмный фон и один акцентный цвет читаются в ленте лучше светлых",
    "Сцену описывать предметно: объект, материал, свет, окружение. Без слов «красиво», «премиально»",
    "На каждый баннер — одна мысль. Десять баннеров = десять разных зацепок",
    "Не писать на баннере то, чего клиент не подтвердил: сроки, скидки, бесплатность"
  ],
  "antipatterns": [
    "Накладывать текст поверх готового фото — выглядит как две несвязанные части",
    "Лозунги вместо фактов в подзаголовке",
    "Отключённая цена на всех баннерах сразу",
    "Прозрачный товар на нейтральном светлом фоне"
  ]
}

db = SessionLocal()
rows = db.execute(text("select distinct account_id from storage where key='banner_library'")).fetchall()
targets = ["_global"] + [r[0] for r in rows]
print("=== куда положить руководство:", targets)
for acc in targets:
    row = db.execute(text("select id from storage where account_id=:a and key='banner_prompt_guide'"), {"a": acc}).fetchone()
    print("   %-40s %s" % (acc, "ОБНОВИТ" if row else "СОЗДАСТ"))
    if APPLY:
        v = json.dumps(GUIDE, ensure_ascii=False)
        if row:
            db.execute(text("update storage set value=:v where id=:i"), {"v": v, "i": row[0]})
        else:
            db.execute(text("insert into storage (account_id, key, value) values (:a,'banner_prompt_guide',:v)"), {"a": acc, "v": v})
if APPLY:
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
