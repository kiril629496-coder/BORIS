"""Фоновый добытчик шаблона Avito: терпеливо ждёт окна между 429.
Запуск: nohup venv/bin/python3 fetch_template_125593.py > /tmp/tmpl.log 2>&1 &
"""
import os, sys, time, json, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
for line in open(_env, encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

TID = os.environ.get("TMPL_ID", "125593")
PATH = "Услуги > Предложение услуг > Искусство > Музыка, стихи, песни на заказ"
CID = "музыка, стихи, песни на заказ"

from app.services.category_resolver import fetch_template_fields_via_http, _SKIP_TAGS
from app.db.session import SessionLocal
from app.models.category_template import CategoryTemplate

for attempt in range(1, 121):          # до 2 часов
    try:
        fields = fetch_template_fields_via_http(TID)
    except Exception as e:
        fields = None
        print("попытка %d: ошибка %s" % (attempt, str(e)[:100]), flush=True)
    if fields:
        fields = [f for f in fields if f.get("tag") not in _SKIP_TAGS]
        if not fields:
            print("попытка %d: поля пустые после фильтра" % attempt, flush=True)
        else:
            db = SessionLocal()
            try:
                fr = {f["tag"]: f["format_rule"] for f in fields if f.get("format_rule")}
                ev = {f["tag"]: f["allowed_values"] for f in fields if f.get("allowed_values")}
                row = db.query(CategoryTemplate).filter(CategoryTemplate.category_id == CID).first()
                if row:
                    row.template_id = TID; row.category_name = PATH
                    row.required_fields = json.dumps(fields, ensure_ascii=False)
                    row.field_rules = json.dumps(fr, ensure_ascii=False)
                    row.enum_values = json.dumps(ev, ensure_ascii=False)
                else:
                    db.add(CategoryTemplate(category_id=CID, category_name=PATH, template_id=TID,
                                            required_fields=json.dumps(fields, ensure_ascii=False),
                                            field_rules=json.dumps(fr, ensure_ascii=False),
                                            enum_values=json.dumps(ev, ensure_ascii=False)))
                db.commit()
                print("ГОТОВО на попытке %d: полей %d, обязательных %s" % (
                    attempt, len(fields), [f["tag"] for f in fields if f.get("required")]), flush=True)
            finally:
                db.close()
            break
    else:
        print("попытка %d: пусто (429 или заглушка)" % attempt, flush=True)
    time.sleep(random.randint(45, 90))
else:
    print("за 2 часа окно не поймали", flush=True)
