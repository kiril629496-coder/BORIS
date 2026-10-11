# -*- coding: utf-8 -*-
"""Журнал публикаций в соцсети. Нулевое правило Конституции: пост уходит
от имени компании наружу — клиент должен видеть, что и когда опубликовано."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "posting_runner.py")

A_OLD = '            _notify_posted(account_id, pname, theme, proj, results, post.get("banner"))'
A_NEW = A_OLD + '''
            try:
                from app.services.action_log import log_action, ACTOR_CRON
                _ch = []
                if isinstance(results.get("tg"), dict) and results["tg"].get("ok"): _ch.append("Telegram")
                if isinstance(results.get("vk"), dict) and results["vk"].get("ok"): _ch.append("ВКонтакте")
                log_action(account_id=account_id, action="Опубликовал пост",
                           object_kind="пост", object_name=str(theme)[:200],
                           after_val=", ".join(_ch) or "канал не определён",
                           reason="проект «%s», публикация сразу" % pname,
                           actor=ACTOR_CRON, source="posting_runner")
            except Exception:
                pass'''

B_OLD = '                rec["status"] = "published"; rec["ids"] = _ids'
B_NEW = B_OLD + '''
                try:
                    from app.services.action_log import log_action, ACTOR_CRON
                    _ch2 = []
                    if isinstance(results.get("tg"), dict) and results["tg"].get("ok"): _ch2.append("Telegram")
                    if isinstance(results.get("vk"), dict) and results["vk"].get("ok"): _ch2.append("ВКонтакте")
                    log_action(account_id=acc, action="Опубликовал пост по расписанию",
                               object_kind="пост",
                               object_name=(rec.get("text") or "")[:150],
                               after_val=", ".join(_ch2) or "канал не определён",
                               reason="время выхода %s" % (rec.get("publish_at") or "по расписанию"),
                               actor=ACTOR_CRON, source="posting_runner.due")
                except Exception:
                    pass'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "log_action" in src:
    print("уже пропатчено"); sys.exit(0)
out = src
for old, new, nm in ((A_OLD, A_NEW, "публикация сразу"), (B_OLD, B_NEW, "публикация по расписанию")):
    n = out.count(old)
    print("  %-26s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_postlog_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
