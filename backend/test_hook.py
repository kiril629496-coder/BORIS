import os, sys
for l in open('/root/BORIS/backend/.env'):
    l = l.strip()
    if l and not l.startswith('#') and '=' in l:
        k, v = l.split('=', 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))
sys.path.insert(0, "/root/BORIS/backend")
import app.api.avito as A
import app.api.feed_contract as FC
from app.api.feed_contract import validate_feed_item

CONTRACT = {"ok": True, "code": "", "template_id": "90576",
            "category_path": "Готовые строения и срубы > Бытовка", "contract_version": "test01",
            "fields": {
                "HouseType": {"required": True, "allowed": ["Строительная", "Дачная"], "format_rule": "", "name_ru": "Тип бытовки"},
                "HouseMaterial": {"required": True, "allowed": ["Металл"], "format_rule": "", "name_ru": "Материал"},
                "Length": {"required": True, "allowed": [], "format_rule": "Целое число", "name_ru": "Длина"}}}
saved = {"feed": None, "drafts": None}
A._save_feed_items = lambda acc, items: saved.__setitem__("feed", list(items))
A._save_drafts = lambda acc, items: saved.__setitem__("drafts", list(items))
OLD = [A.FeedItem(id="old-1", title="Старое", description="d", address="Москва")]
A._load_feed_items = lambda acc: list(OLD)
FC.resolve_feed_contract = lambda tid, _db=None: CONTRACT if tid == "90576" else {"ok": False, "code": "template_not_found", "template_id": tid, "category_path": "", "fields": {}, "contract_version": ""}

ok = fail = 0
def check(n, cond, d=""):
    global ok, fail
    if cond: ok += 1; print("  OK  %s" % n)
    else: fail += 1; print("  FAIL %s  %s" % (n, d))

def draft(i, tid="", params=None):
    return {"id": i, "title": "Бытовка %s" % i, "description": "текст", "price": 7000,
            "address": "СПб", "images": ["u"], "params": params or {}, "template_id": tid}
GOOD = {"HouseType": "Строительная", "HouseMaterial": "Металл", "Length": "6000"}
BAD = {"HouseType": "Жилая", "HouseMaterial": "Металл", "Length": "6000"}

def run_hook(new_items):
    saved["feed"] = None; saved["drafts"] = None
    _w = [i for i in new_items if str(getattr(i, "template_id", "") or "").strip()]
    _wo = [i for i in new_items if not str(getattr(i, "template_id", "") or "").strip()]
    if _w:
        rep = FC.validate_feed_batch(_w, {}, _db=object())
        if not rep["ok"]:
            return {"status": "error", "contract_status": "contract_failed",
                    "blocked": rep["blocked"], "blocked_count": len(rep["blocked"])}
    A._save_feed_items("acc", A._load_feed_items("acc") + new_items)
    A._save_drafts("acc", [])
    return {"status": "ok", "published": len(new_items),
            "contract_status": "contract_required" if _wo else "success",
            "checked": len(_w), "blocked": [], "blocked_count": 0,
            "without_contract": [i.id for i in _wo]}

print("=== ИНТЕГРАЦИОННЫЕ ТЕСТЫ ВРЕЗКИ ===")
it = A._draft_to_feed_item({"id": "d1", "title": "t", "description": "d", "address": "СПб",
                            "template_id": "90576", "category_leaf_id": "L1", "category_path": "P", "contract_version": "V"})
check("1. четыре поля переносятся из draft", (it.template_id, it.category_leaf_id, it.category_path, it.contract_version) == ("90576", "L1", "P", "V"))
it2 = A._draft_to_feed_item({"id": "d2", "title": "t", "description": "d", "address": "СПб", "template_id": None, "category_path": None})
check("2. None нормализуется в пустую строку", it2.template_id == "" and it2.category_path == "")
r = run_hook([A._draft_to_feed_item(draft("d3"))])
check("3. без template_id публикуется", r["status"] == "ok" and saved["feed"] is not None, r)
check("4. ответ содержит contract_required", r["contract_status"] == "contract_required", r)
check("5. published и status сохранены", r["published"] == 1 and r["status"] == "ok", r)
r = run_hook([A._draft_to_feed_item(draft("d4", "90576", GOOD))])
check("6. валидный контракт -> success", r["contract_status"] == "success" and saved["feed"] is not None, r)
r = run_hook([A._draft_to_feed_item(draft("d5", "90576", BAD)), A._draft_to_feed_item(draft("d6"))])
check("7. недопустимый enum блокирует пакет", r["contract_status"] == "contract_failed", r)
check("8. _save_feed_items не вызван при отказе", saved["feed"] is None)
check("9. черновики не удалены при отказе", saved["drafts"] is None)
r = run_hook([A._draft_to_feed_item(draft("d7", "90576", GOOD)), A._draft_to_feed_item(draft("d8"))])
check("10. валидный + без контракта сохраняются вместе", r["status"] == "ok" and len(saved["feed"]) == 3, r)
check("11. existing_feed цел", saved["feed"][0].id == "old-1")
r = run_hook([])
check("12. пустой пакет -> прежнее поведение", r["status"] == "ok" and r["published"] == 0, r)
rr = validate_feed_item({"id": "x", "params": BAD}, CONTRACT)
e = rr["errors"][0]
check("13. ошибка содержит label и allowed_values", e["label"] == "Тип бытовки" and e["allowed_values"] == ["Строительная", "Дачная"], e)
print("\nИТОГО: %s из %s" % (ok, ok + fail))
