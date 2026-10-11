# -*- coding: utf-8 -*-
"""Журнал генерации баннеров партией. Предел по тарифу здесь уже есть
(остаток баннеров режет количество), не хватало только записи."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/avito.py")

OLD = '''    _save_drafts(account_id, other + updated)
    return {"status": "ok", "updated": len(updated), "banners_generated": len(banner_urls), "batch_label": batch_label, "notice": banner_notice}'''

NEW = '''    _save_drafts(account_id, other + updated)
    try:
        from app.services.action_log import log_action, ACTOR_USER
        log_action(account_id=account_id, action="Сделал баннеры для объявлений",
                   object_kind="объявления",
                   object_name="партия «%s», %s черновиков" % (batch_label, len(updated)),
                   after_val="создано баннеров: %s" % len(banner_urls),
                   reason=(banner_notice or "баннер поставлен первым фото у каждого "
                                            "объявления партии"),
                   actor=ACTOR_USER, source="avito.apply_banner_to_batch")
    except Exception:
        pass
    return {"status": "ok", "updated": len(updated), "banners_generated": len(banner_urls), "batch_label": batch_label, "notice": banner_notice}'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
n = src.count(OLD)
print("  якорь встречается:", n)
if n != 1:
    print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(OLD, NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_bannerlog_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
