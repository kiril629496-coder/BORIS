import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/api/messenger.py"
src = open(P, encoding="utf-8").read()
if "_real_last_incoming" in src:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A1 = '_SYSTEM_NUDGE_PREFIX = "[Системное сообщение]"\n'
A2 = ('            last_text = (last_message.get("content") or {}).get("text", "")\n'
      '            proactive_event = "viewed_phone" if _SYSTEM_NUDGE_PREFIX in last_text else None\n'
      '\n'
      '            if _contour == "new":\n'
      '                if _mop_incoming(account_id, chat_id, last_message_id,\n'
      '                                 last_text, chat, proactive_event) == "card_sent":\n'
      '                    _set_last_processed_message_id(account_id, chat_id, last_message_id)\n'
      '                continue\n')

for frag, name in ((A1, "константа"), (A2, "врезка")):
    n = src.count(frag)
    if n != 1:
        print("СТОП: якорь «%s» найден %d раз (нужно 1)" % (name, n)); sys.exit(1)

N1 = A1 + '''_AVITO_STUB_MARKERS = ("перейдите на подписку", "доступ к чатам")


def _real_last_incoming(account_id, chat_id):
    """Последнее содержательное входящее из v3. None — карточку не создавать.

    Список чатов (v2) без подписки отдаёт заглушку вместо текста сообщения,
    поэтому источником истины служат сообщения чата (v3).
    """
    mr = fetch_chat_messages(account_id, chat_id)
    if mr.get("status") != "ok":
        return None
    raw = mr.get("messages")
    msgs = raw.get("messages", []) if isinstance(raw, dict) else (raw or [])
    if not msgs:
        return None
    for m in msgs:
        if str(m.get("direction") or "").lower() != "in":
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        if (m.get("type") or "text") != "text":
            continue
        txt = ((m.get("content") or {}).get("text") or "").strip()
        if not txt:
            continue
        low = txt.lower()
        if any(s in low for s in _AVITO_STUB_MARKERS):
            print("MOP_STUB_SKIP %s/%s: заглушка вместо текста" % (account_id, chat_id),
                  flush=True)
            return None
        if txt.startswith(_SYSTEM_NUDGE_PREFIX):
            continue
        return {"id": mid, "text": txt}
    return None
'''

N2 = ('            last_text = (last_message.get("content") or {}).get("text", "")\n'
      '            proactive_event = "viewed_phone" if _SYSTEM_NUDGE_PREFIX in last_text else None\n'
      '\n'
      '            if _contour == "new":\n'
      '                _real = _real_last_incoming(account_id, chat_id)\n'
      '                if not _real:\n'
      '                    continue\n'
      '                real_text = _real["text"]\n'
      '                real_message_id = _real["id"]\n'
      '                proactive_event = ("viewed_phone"\n'
      '                                   if _SYSTEM_NUDGE_PREFIX in real_text else None)\n'
      '                if _mop_incoming(account_id, chat_id, real_message_id,\n'
      '                                 real_text, chat, proactive_event) == "card_sent":\n'
      '                    _set_last_processed_message_id(account_id, chat_id, real_message_id)\n'
      '                continue\n')

new = src.replace(A1, N1, 1).replace(A2, N2, 1)
try:
    compile(new, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_v3src_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(new)
open("/tmp/v3src_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПРИМЕНЕНО, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
