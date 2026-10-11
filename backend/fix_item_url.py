import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
TS = int(time.time())
MODEL = "app/models/messenger_message.py"
SLOTS = "app/api/inbox_slots.py"
sm, ss = open(MODEL, encoding="utf-8").read(), open(SLOTS, encoding="utf-8").read()

if "item_url" in sm and "chat_url" in ss:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A0 = "    item_title = Column(String, nullable=True)\n"
A1 = ("            \"       account_id, avito_chat_id, item_id, item_title, direction,"
      " text, avito_created_at, item_owner_id \"\n")
A2 = "        for acc, cid, item_id, item_title, direction, txt, created, iowner in rows:\n"
A3 = ("                \"item_url\": \"https://www.avito.ru/profile/messenger/channel/\""
      " + str(cid),\n")
A4 = ("            \"SELECT direction, text, avito_created_at, item_title, msg_type,"
      " content_type, media_ref \"\n")
A5 = "        } for (d, t, c, _title, mt, ct, mr) in rows]\n"
A6 = ("            \"item_url\": \"https://www.avito.ru/profile/messenger/channel/\""
      " + str(avito_chat_id),\n")

for src, frag, name in ((sm, A0, "модель item_title"), (ss, A1, "SELECT dialogs"),
                        (ss, A2, "распаковка dialogs"), (ss, A3, "item_url dialogs"),
                        (ss, A4, "SELECT thread"), (ss, A5, "распаковка thread"),
                        (ss, A6, "item_url thread")):
    n = src.count(frag)
    if n != 1:
        print("СТОП: якорь «%s» найден %d раз (нужно 1)" % (name, n)); sys.exit(1)

sm = sm.replace(A0, A0 + "    item_url = Column(String, nullable=True)\n", 1)
ss = ss.replace(A1, ("            \"       account_id, avito_chat_id, item_id, item_title,"
                     " direction, text, avito_created_at, item_owner_id, item_url \"\n"), 1)
ss = ss.replace(A2, ("        for acc, cid, item_id, item_title, direction, txt, created,"
                     " iowner, iurl in rows:\n"), 1)
ss = ss.replace(A3, ("                \"item_url\": iurl or \"\",\n"
                     "                \"chat_url\": \"https://www.avito.ru/profile/messenger/channel/\""
                     " + str(cid),\n"), 1)
ss = ss.replace(A4, ("            \"SELECT direction, text, avito_created_at, item_title,"
                     " msg_type, content_type, media_ref, item_url \"\n"), 1)
ss = ss.replace(A5, "        } for (d, t, c, _title, mt, ct, mr, _iu) in rows]\n", 1)
ss = ss.replace(A6, ("            \"item_url\": next((r[7] for r in rows if r[7]), \"\"),\n"
                     "            \"chat_url\": \"https://www.avito.ru/profile/messenger/channel/\""
                     " + str(avito_chat_id),\n"), 1)

NEW = {MODEL: sm, SLOTS: ss}
for p, s in NEW.items():
    try:
        compile(s, p, "exec")
    except SyntaxError as e:
        print("СТОП: синтаксис ДО записи —", p, e); sys.exit(1)
baks = {}
for p, s in NEW.items():
    bak = "%s.bak_iurl_%d" % (p, TS)
    shutil.copy2(p, bak); baks[p] = bak
    open(p, "w", encoding="utf-8").write(s)
open("/tmp/iurl_ts.txt", "w").write(str(TS))
try:
    for p in NEW:
        py_compile.compile(p, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПРАВКА ПРИМЕНЕНА, py_compile OK, import app.main OK")
    for p, b in baks.items():
        print("  бэкап:", b)
except Exception as e:
    for p, b in baks.items():
        shutil.copy2(b, p)
    print("ОТКАТ обоих файлов:", str(e)[:200]); sys.exit(1)
