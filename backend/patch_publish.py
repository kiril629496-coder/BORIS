# -*- coding: utf-8 -*-
"""Врезка 1/3: контрактная валидация в POST /drafts/publish.
Проверяются ТОЛЬКО новые объявления (new_feed_items). existing_feed не трогается.
XML не меняется. Атомарность: один невалидный новый -> не сохраняется ни один новый."""
import shutil, time, py_compile, sys, hashlib

P = "app/api/avito.py"
src = open(P, encoding="utf-8").read()
print("SHA до правки:", hashlib.sha256(src.encode()).hexdigest())
if "feed_contract" in src:
    print("СТОП: врезка уже применена"); sys.exit(0)

A_OLD = "    source_batch_id: int | None = None\n"
A_NEW = """    source_batch_id: int | None = None
    # Контракт категории Avito. Пустые = контракт не определён (contract_required).
    # Заполняются экраном выбора категории; автоподстановка из category_id запрещена.
    template_id: str = ""
    category_leaf_id: str = ""
    category_path: str = ""
    contract_version: str = ""
"""

B_OLD = """        timezone=d.get("timezone", "")
    )
"""
B_NEW = """        timezone=d.get("timezone", ""),
        template_id=d.get("template_id") or "",
        category_leaf_id=d.get("category_leaf_id") or "",
        category_path=d.get("category_path") or "",
        contract_version=d.get("contract_version") or "",
    )
"""

C_OLD = "    _save_feed_items(req.account_id, existing_feed + new_feed_items)\n"
C_NEW = '''    # --- Контрактная проверка категории (врезка 1/3) ---
    # Проверяются ТОЛЬКО новые объявления. existing_feed уже сохранён и повторной
    # проверке не подлежит: у старых записей template_id пуст, и они не должны
    # блокировать публикацию новых. Атомарность: один невалидный новый элемент с
    # контрактом -> не сохраняется НИ ОДИН новый, старый фид остаётся прежним.
    _new_with = [it for it in new_feed_items if str(getattr(it, "template_id", "") or "").strip()]
    _new_without = [it for it in new_feed_items if not str(getattr(it, "template_id", "") or "").strip()]
    _contract_report = None
    if _new_with:
        from app.api.feed_contract import validate_feed_batch as _vfb
        _contract_report = _vfb(_new_with, {})
        if not _contract_report["ok"]:
            _titles = {it.id: it.title for it in new_feed_items}
            _audit_log(req.account_id, "publish_contract_failed",
                       f"Публикация отменена: {len(_contract_report['blocked'])} объявлений не прошли контракт категории",
                       actor="boris")
            return {
                "status": "error",
                "contract_status": "contract_failed",
                "message": "Часть объявлений не прошла проверку категории. Публикация отменена, черновики сохранены.",
                "checked": len(_new_with),
                "blocked": _contract_report["blocked"],
                "blocked_count": len(_contract_report["blocked"]),
                "by_code": _contract_report["by_code"],
                "without_contract": [it.id for it in _new_without],
                "contract_report": [
                    {"item_id": r["id"], "title": _titles.get(r["id"], ""),
                     "template_id": r["template_id"], "category_leaf": r["category_leaf"],
                     "code": e["code"], "field": e.get("field", ""),
                     "label": e.get("label", "") or e.get("field", ""),
                     "current_value": e.get("value", ""),
                     "allowed_values": e.get("allowed_values", []),
                     "format_rule": e.get("format_rule", ""),
                     "message": e.get("message", "")}
                    for r in _contract_report["items"] if not r["valid"] for e in r["errors"]
                ],
            }

    _save_feed_items(req.account_id, existing_feed + new_feed_items)
'''

D_OLD = '    return {"status": "ok", "published": len(to_publish)}\n'
D_NEW = '''    # Диагностика контракта в успешном ответе. Существующие поля и значение
    # status не меняются - фронт может быть на них завязан.
    _contract_status = "contract_required" if _new_without else "success"
    return {
        "status": "ok",
        "published": len(to_publish),
        "contract_status": _contract_status,
        "checked": len(_new_with),
        "blocked": [],
        "blocked_count": 0,
        "without_contract": [
            str(getattr(it, "id", "") or ("batch_index:%d" % idx))
            for idx, it in enumerate(_new_without)
        ],
        "contract_message": (
            "Часть объявлений опубликована без проверки параметров категории. "
            "Определите категорию Avito для полной проверки."
            if _new_without else ""
        ),
    }
'''

subs = [(A_OLD, A_NEW), (B_OLD, B_NEW), (C_OLD, C_NEW), (D_OLD, D_NEW)]
for old, _ in subs:
    n = src.count(old)
    print("якорь x%s: %r" % (n, old.strip().splitlines()[0][:60]))
    if n != 1:
        print("СТОП: якорь не уникален"); sys.exit(1)

new_src = src
for old, new in subs:
    new_src = new_src.replace(old, new, 1)

bak = P + ".bak_publish_hook_" + str(int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(new_src)
py_compile.compile(P, doraise=True)
print("\nВРЕЗКА 1/3 ПРИМЕНЕНА (/drafts/publish)")
print("бэкап:", bak)
print("строк было/стало:", len(src.splitlines()), "->", len(new_src.splitlines()))
print("SHA после:", hashlib.sha256(new_src.encode()).hexdigest())
