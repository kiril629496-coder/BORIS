import os, sys, json
for _l in open('.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
sys.path.insert(0, '/root/BORIS/backend')
from app.db.session import SessionLocal
from app.services.marketing_rules import save_rule, get_optimization_rule

APPLY = "--apply" in sys.argv

NAME = "Масштабирование объявлений с высокой конверсией"
TEXT = ("Если объявление имеет устойчиво высокую конверсию просмотр -> контакт "
        "и достаточную статистику, оно рассматривается как кандидат на увеличение "
        "платного трафика. Дополнительный трафик направляется в уже доказавшие "
        "эффективность объявления, а не одинаково во все.")
PARAMS = {"min_conversion": 0.10, "bid_multiplier": 3.0,
          "min_views": 100, "applies_to": "cpx_raise"}

db = SessionLocal()
res = save_rule(db, NAME, TEXT, PARAMS,
                author="ручное решение маркетолога BORIS", apply=APPLY)
print("save_rule:", json.dumps(res, ensure_ascii=False))
print("чтение:", json.dumps(get_optimization_rule(db, "cpx_raise"),
                            ensure_ascii=False))
if not APPLY:
    print("\nэто просмотр, повторите с --apply")
