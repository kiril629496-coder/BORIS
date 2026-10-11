import sys
sys.path.insert(0, "/root/BORIS/backend")
from app.api.feed_contract import (validate_feed_item, validate_feed_batch,
    CODE_TEMPLATE_ID_MISSING, CODE_TEMPLATE_NOT_FOUND, CODE_ENUM_NOT_ALLOWED,
    CODE_UNKNOWN_PARAM, CODE_NEEDS_INPUT, CODE_CONTRACT_REQUIRED, CODE_FORMAT,
    SRC_CONST)

CONTRACT = {"ok": True, "code": "", "template_id": "90576",
            "category_path": "Готовые строения и срубы > Бытовка",
            "contract_version": "abc123",
            "fields": {
                "HouseType": {"required": True, "allowed": ["Строительная", "Дачная", "Пост охраны"],
                              "format_rule": "", "name_ru": "Тип бытовки"},
                "HouseMaterial": {"required": True, "allowed": ["Металл"], "format_rule": "", "name_ru": "Материал"},
                "Length": {"required": True, "allowed": [], "format_rule": "Целое число", "name_ru": "Длина"},
                "Width": {"required": True, "allowed": [], "format_rule": "Целое число", "name_ru": "Ширина"},
                "Delivery": {"required": False, "allowed": ["Да", "Нет", "Обсуждается"], "format_rule": "", "name_ru": "Доставка"},
            }}
NOT_FOUND = {"ok": False, "code": CODE_TEMPLATE_NOT_FOUND, "template_id": "999999",
             "category_path": "", "fields": {}, "contract_version": ""}
NO_ID = {"ok": False, "code": CODE_TEMPLATE_ID_MISSING, "template_id": "",
         "category_path": "", "fields": {}, "contract_version": ""}

def codes(r): return sorted(e["code"] for e in r["errors"])
ok = fail = 0
def check(n, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print("  OK  %s" % n)
    else: fail += 1; print("  FAIL %s  %s" % (n, detail))

base = {"id": "t", "params": {"HouseType": "Строительная", "HouseMaterial": "Металл",
                              "Length": "6000", "Width": "2400"}}
print("=== 12 РЕГРЕССИОННЫХ ТЕСТОВ ===")
r = validate_feed_item(dict(base), NO_ID)
check("1. нет template_id -> блок", not r["valid"] and CODE_TEMPLATE_ID_MISSING in codes(r), codes(r))
r = validate_feed_item(dict(base), NOT_FOUND)
check("2. шаблона нет -> блок", not r["valid"] and CODE_TEMPLATE_NOT_FOUND in codes(r), codes(r))
r = validate_feed_item({"id": "t", "params": dict(base["params"], HouseType="Жилая")}, CONTRACT)
check("3. HouseType=Жилая вне enum -> блок", not r["valid"] and CODE_ENUM_NOT_ALLOWED in codes(r), codes(r))
r = validate_feed_item(dict(base), CONTRACT)
check("4. допустимый HouseType принят", r["valid"], r["errors"])
r = validate_feed_item({"id": "t", "params": {"HouseMaterial": "Металл", "Length": "6000", "Width": "2400"}}, CONTRACT)
check("5. обязательное пустое -> needs_input", not r["valid"] and CODE_NEEDS_INPUT in codes(r), codes(r))
r = validate_feed_item({"id": "t", "params": {"HouseType": "Строительная", "Length": "6000", "Width": "2400"}}, CONTRACT)
filled = {f["field"]: f for f in r["filled"]}
check("6. единственное допустимое -> автозаполнено", r["valid"] and filled.get("HouseMaterial", {}).get("source") == SRC_CONST, r["errors"])
r = validate_feed_item({"id": "t", "params": dict(base["params"], Cvet="Синий")}, CONTRACT)
check("7. неизвестный параметр -> блок", not r["valid"] and CODE_UNKNOWN_PARAM in codes(r), codes(r))
r = validate_feed_item({"id": "t", "params": {"HouseMaterial": "Металл", "Length": "6000", "Width": "2400"}},
                       CONTRACT, seller_defaults={"HouseType": "Офисная"})
check("8. значение продавца вне enum -> блок", not r["valid"] and CODE_ENUM_NOT_ALLOWED in codes(r), codes(r))
batch = validate_feed_batch([{"id": "direct", "params": dict(base["params"])}], _db=object())
check("9. запись без template_id -> contract_required", not batch["ok"] and batch["by_code"].get(CODE_CONTRACT_REQUIRED) == 1, batch["by_code"])
r = validate_feed_item(dict(base), CONTRACT)
vals = {f["field"]: f["value"] for f in r["filled"]}
check("10. валидное не меняется", r["valid"] and vals["HouseType"] == "Строительная" and vals["Length"] == "6000", vals)
r = validate_feed_item({"id": "t", "params": dict(base["params"], Length="шесть метров")}, CONTRACT)
check("11. Length по правилу шаблона -> блок при тексте", not r["valid"] and CODE_FORMAT in codes(r), codes(r))
before = dict(base["params"])
r = validate_feed_item({"id": "t", "params": dict(before)}, CONTRACT)
check("12. валидатор не мутирует params", before == base["params"], before)
print("\nИТОГО: %s из %s" % (ok, ok + fail))
