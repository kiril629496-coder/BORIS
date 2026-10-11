# -*- coding: utf-8 -*-
"""Журнал ответов МОПа. Текст пишет BORIS, отправку подтверждает человек —
в записи это разделено, иначе не отличить ответ сотрудника от ответа машины."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/messenger.py")

OLD = '''    text_to_send = req.text if req.text else draft["text"]
    result = send_message(draft["account_id"], draft["avito_chat_id"], text_to_send)
    return result'''

NEW = '''    text_to_send = req.text if req.text else draft["text"]
    result = send_message(draft["account_id"], draft["avito_chat_id"], text_to_send)
    if isinstance(result, dict) and result.get("status") == "ok":
        try:
            from app.services.action_log import log_action, ACTOR_USER
            _edited = bool(req.text and req.text != draft.get("text"))
            log_action(account_id=draft["account_id"],
                       action="Отправил ответ AI-менеджера",
                       object_kind="диалог", object_name=draft["avito_chat_id"],
                       before_val=(draft.get("text") or "")[:300] if _edited else "",
                       after_val=text_to_send[:300],
                       reason=("текст BORIS исправлен вручную и подтверждён"
                               if _edited else "черновик BORIS подтверждён без правок"),
                       actor=ACTOR_USER, source="messenger.approve_draft")
        except Exception:
            pass
    return result'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "action_log" in src:
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
bak = path + ".bak_moplog_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
