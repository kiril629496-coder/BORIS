# -*- coding: utf-8 -*-
"""Достройка блока draft в /thread до единого источника для кабинета.

1) Legacy-хранилище storage['_telegram_drafts'] читается как второй источник —
   иначе продажа (contour=legacy, 41 живой черновик) получала ложное
   «AI-менеджер не подключён».
2) Если после последнего входящего продавец уже ответил, черновик не отдаём:
   у legacy-записей нет дат, и без этого кабинет показывал бы устаревший ответ.
3) Для legacy отдаём draft_id и source — фронту нужно знать, каким путём слать.
"""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

A_OLD = '''            else:
                # Черновика нет: честно говорим, подключён ли вообще новый контур
                _c = db.execute(text(
                    "SELECT contour FROM mop_modes WHERE account_id = :a"),
                    {"a": account_id}).scalar()
                if (_c or "legacy") != "new":
                    draft = {"status": "not_connected", "text": "",
                             "reason": "AI-менеджер не подключён к этому аккаунту",
                             "can_retry": False, "updated_at": ""}'''

A_NEW = '''            else:
                # Второй источник — legacy-хранилище старого контура. Без него
                # аккаунт на legacy получал ложное «не подключён», хотя черновики
                # у него есть и кабинет их показывает через pending_drafts.
                import json as _lj
                for _lk, _lv in db.execute(text(
                        "SELECT key, value FROM storage"
                        " WHERE account_id = '_telegram_drafts'"
                        "   AND key LIKE 'messenger_draft:%'")).fetchall():
                    try:
                        _ld = _lj.loads(_lv)
                    except Exception:
                        continue
                    if (_ld.get("account_id") == account_id
                            and _ld.get("avito_chat_id") == avito_chat_id
                            and _ld.get("status") == "pending"):
                        draft = {"status": "draft_ready",
                                 "text": _ld.get("text") or "", "reason": "",
                                 "can_retry": False, "updated_at": "",
                                 "source": "legacy",
                                 "draft_id": str(_lk).split(":", 1)[1]}
                        break
                if draft is None:
                    _c = db.execute(text(
                        "SELECT contour FROM mop_modes WHERE account_id = :a"),
                        {"a": account_id}).scalar()
                    if (_c or "legacy") != "new":
                        draft = {"status": "not_connected", "text": "",
                                 "reason": "AI-менеджер не подключён к этому аккаунту",
                                 "can_retry": False, "updated_at": ""}'''

B_OLD = '''        except Exception as _de:
            print("[inbox_thread] draft lookup failed: " + repr(_de)[:150], flush=True)
        return {"status": "ok", "dialog": dialog, "messages": messages, "draft": draft}'''

B_NEW = '''        except Exception as _de:
            print("[inbox_thread] draft lookup failed: " + repr(_de)[:150], flush=True)
        # Продавец уже ответил после последнего входящего — черновик неактуален.
        # У legacy-записей нет дат, поэтому свежесть определяем только так.
        if draft and draft.get("status") in ("draft_ready", "human_required"):
            for _m in reversed(messages):
                if _m.get("msg_type") == "system":
                    continue
                if _m.get("direction") == "out":
                    draft = None
                break
        if draft is not None and "source" not in draft:
            draft["source"] = "mop"
        return {"status": "ok", "dialog": dialog, "messages": messages, "draft": draft}'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if '"source": "legacy"' in src:
    print("уже пропатчено"); sys.exit(0)
for nm, old in (("legacy-источник", A_OLD), ("фильтр ответа", B_OLD)):
    n = src.count(old)
    print("  %-16s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(A_OLD, A_NEW, 1).replace(B_OLD, B_NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_thread2_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
