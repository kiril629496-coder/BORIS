# -*- coding: utf-8 -*-
"""Сводная запись в журнал о публикации партии объявлений.
ОДНА запись на партию, а не по записи на объявление — иначе журнал из
300 публикаций станет нечитаемым. Внутри список id и названий: автоматического
отката у публикации нет, но по этому списку видно, что снимать вручную."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/avito.py")

OLD = '''    _save_feed_items(req.account_id, existing_feed + new_feed_items)
    _save_drafts(req.account_id, remaining)'''

NEW = '''    _save_feed_items(req.account_id, existing_feed + new_feed_items)
    _save_drafts(req.account_id, remaining)
    # Журнал: одна сводная запись на всю партию (правило 0 Конституции).
    # Список id и названий нужен потому, что автоматического отката у публикации
    # нет — по нему клиент поймёт, что именно снимать вручную, если ошибся.
    try:
        from app.services.action_log import log_action, ACTOR_USER
        _pub = [(d.get("id") or "", d.get("title") or "без названия") for d in to_publish]
        _lines = ["%s — %s" % (i, t) for i, t in _pub[:40]]
        if len(_pub) > 40:
            _lines.append("...ещё %s" % (len(_pub) - 40))
        log_action(account_id=req.account_id,
                   action="Опубликовал объявления",
                   object_kind="объявления",
                   object_name="партия из %s шт." % len(_pub),
                   after_val="\\n".join(_lines),
                   reason="публикация подтверждённых черновиков после успешной "
                          "проверки валидатором Avito; автоматический откат невозможен",
                   actor=ACTOR_USER, source="avito.publish_drafts")
    except Exception:
        pass'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "avito.publish_drafts" in src:
    print("уже пропатчено"); sys.exit(0)
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
bak = path + ".bak_publog_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
