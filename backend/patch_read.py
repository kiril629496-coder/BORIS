# -*- coding: utf-8 -*-
"""П.7: «не видно, прочитал клиент наш ответ или нет».

Avito отдаёт признак: в /messenger/v3/.../messages по каждому сообщению есть
isRead (bool) и read (unix-время прочтения). Проверено живым запросом 13.08:
отправлено 1786610927, прочитано 1786611015 — через 88 секунд.

Мы эти поля выбрасывали. Сохраняем и показываем у ИСХОДЯЩИХ.

ВАЖНО: колонку надо добавить И в таблицу, И в модель. В _store_messages_locally
стоит фильтр hasattr(MessengerMessage, k) — поле, которого нет в модели, молча
выбрасывается. На этом уже обожглись с item_url (03.08).
"""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
B = "/root/BORIS/backend"

JOBS = [
    (os.path.join(B, "app/models/messenger_message.py"),
     "    item_url = Column(String, nullable=True)",
     "    item_url = Column(String, nullable=True)\n"
     "    # П.7: признак прочтения ИСХОДЯЩЕГО покупателем. Avito отдаёт isRead и read.\n"
     "    is_read = Column(Boolean, nullable=True)\n"
     "    read_at = Column(Integer, nullable=True)",
     "модель", "is_read = Column"),
    (os.path.join(B, "app/api/messenger.py"),
     '                "item_url": _it_url,\n            }',
     '                "item_url": _it_url,\n'
     '                "is_read": (True if m.get("isRead") else (False if "isRead" in m else None)),\n'
     '                "read_at": (int(m.get("read")) if m.get("read") else None),\n'
     '            }',
     "разбор", '"is_read": (True if m.get("isRead")'),
    (os.path.join(B, "app/api/inbox_slots.py"),
     '            "SELECT direction, text, avito_created_at, item_title, msg_type, content_type, media_ref, item_url "',
     '            "SELECT direction, text, avito_created_at, item_title, msg_type, content_type, media_ref, item_url, is_read, read_at "',
     "выборка", "media_ref, item_url, is_read"),
    (os.path.join(B, "app/api/inbox_slots.py"),
     '        } for (d, t, c, _title, mt, ct, mr, _iu) in rows]',
     '            "is_read": bool(_ir) if _ir is not None else None,\n'
     '            "read_at_iso": _iso_ts(_ra),\n'
     '        } for (d, t, c, _title, mt, ct, mr, _iu, _ir, _ra) in rows]',
     "распаковка", '"read_at_iso"'),
]

print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
cache, stop = {}, False
for path, old, new, name, marker in JOBS:
    src = cache.get(path) or open(path, encoding="utf-8").read()
    if marker in src:
        print("  %-12s уже применено" % name); cache[path] = src; continue
    n = src.count(old)
    print("  %-12s якорь: %s" % (name, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); stop = True; break
    cache[path] = src.replace(old, new, 1)
if stop:
    sys.exit(1)
for p, out in cache.items():
    try:
        ast.parse(out)
    except SyntaxError as e:
        print("  СТОП: синтаксис сломан в %s: %s" % (os.path.basename(p), e)); sys.exit(1)
    print("  %s -> %s" % (os.path.basename(p), hashlib.sha256(out.encode()).hexdigest()[:12]))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
for p, out in cache.items():
    bak = p + ".bak_read_" + str(int(time.time()))
    open(bak, "w", encoding="utf-8").write(open(p, encoding="utf-8").read())
    open(p, "w", encoding="utf-8").write(out)
    print("  записан", os.path.basename(p))
