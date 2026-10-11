import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
TS = int(time.time())
MC, MS = "app/mop_core.py", "app/api/messenger.py"
mc, ms = open(MC, encoding="utf-8").read(), open(MS, encoding="utf-8").read()
if "card_no_ai" in mc:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

# --- якоря ---
A1 = '    "card_posted",\n)\n'
A2 = ('    "new": ("analyzing", "deleted", "human_required", "no_reply_required"),\n')
A3 = ('def begin_incoming(db, account_id, avito_chat_id, avito_message_id, incoming_text,\n'
      '                   item_id=None, item_title=None, client_name=None):\n')
A4 = ('        if status == "new":\n'
      '            got, _prev = set_status(db, draft_id, "analyzing", ("new",),\n'
      '                                    "analyzing_started", channel="telegram",\n'
      '                                    actor_type="system")\n'
      '            need_generate = got is not None\n'
      '            if got is not None:\n'
      '                status = "analyzing"\n')
A5 = ('def _draft(db, draft_id):\n'
      '    row = db.execute(text("SELECT * FROM mop_drafts WHERE id = :i"), {"i": draft_id}).fetchone()\n'
      '    return dict(row._mapping) if row else None\n')
A6 = ('    lines.append("Время: %s · <a href=\\"%s\\">Диалог</a>" % (\n'
      '        when.strftime("%H:%M") if when else "—", chat_url))\n')
A7 = ('        lines.append("«%s»" % d["reply_text"].strip())\n'
      '    if d.get("status") == "send_failed" and d.get("send_error"):\n')
A8 = ('def _process_account_messenger_check(account_id: str, telegram_chat_id: str):\n'
      '\n'
      '    # GATE: пакет менеджера оплачен? нет остатка сообщений — не отвечаем\n'
      '    try:\n'
      '        if not get_manager_balance(account_id).get("active"):\n'
      '            return\n'
      '    except Exception as _e:\n'
      '        print(f"MANAGER_GATE_ERR ({account_id}): {_e}", flush=True)\n')
A9 = ('    db = _SL()\n'
      '    try:\n'
      '        st = _mc.begin_incoming(db, account_id, str(chat_id),\n'
      '                                str(avito_message_id), incoming_text or "")\n')

for src, frag, name in ((mc, A1, "EVENTS"), (mc, A2, "TRANSITIONS new"),
                        (mc, A3, "begin_incoming sig"), (mc, A4, "ветка new"),
                        (mc, A5, "_draft"), (mc, A6, "строка ссылок"),
                        (mc, A7, "блок reply_text"),
                        (ms, A8, "гейт"), (ms, A9, "вызов begin_incoming")):
    n = src.count(frag)
    if n != 1:
        print("СТОП: якорь «%s» найден %d раз (нужно 1)" % (name, n)); sys.exit(1)

mc = mc.replace(A1, '    "card_posted", "card_no_ai",\n)\n', 1)
mc = mc.replace(A2, '    "new": ("analyzing", "draft_ready", "deleted",\n'
                    '            "human_required", "no_reply_required"),\n', 1)
mc = mc.replace(A3, 'def begin_incoming(db, account_id, avito_chat_id, avito_message_id, incoming_text,\n'
                    '                   item_id=None, item_title=None, client_name=None,\n'
                    '                   ai_enabled=True):\n', 1)
mc = mc.replace(A4, '        if status == "new":\n'
                    '            if ai_enabled:\n'
                    '                got, _prev = set_status(db, draft_id, "analyzing", ("new",),\n'
                    '                                        "analyzing_started", channel="telegram",\n'
                    '                                        actor_type="system")\n'
                    '                need_generate = got is not None\n'
                    '                if got is not None:\n'
                    '                    status = "analyzing"\n'
                    '            else:\n'
                    '                got, _prev = set_status(db, draft_id, "draft_ready", ("new",),\n'
                    '                                        "card_no_ai", channel="telegram",\n'
                    '                                        actor_type="system")\n'
                    '                if got is not None:\n'
                    '                    status = "draft_ready"\n', 1)
mc = mc.replace(A5, 'def _has_event(db, draft_id, event):\n'
                    '    """Было ли событие у карточки. Причина берётся из журнала, не из пустоты полей."""\n'
                    '    return bool(db.execute(text(\n'
                    '        "SELECT 1 FROM mop_draft_events WHERE draft_id = :i AND event = :e LIMIT 1"\n'
                    '    ), {"i": draft_id, "e": event}).scalar())\n'
                    '\n'
                    '\n'
                    'def _draft(db, draft_id):\n'
                    '    row = db.execute(text("SELECT * FROM mop_drafts WHERE id = :i"), {"i": draft_id}).fetchone()\n'
                    '    if not row:\n'
                    '        return None\n'
                    '    d = dict(row._mapping)\n'
                    '    # ссылка на объявление: приоритет — тот же item_id, иначе свежайшая непустая\n'
                    '    d["item_url"] = db.execute(text(\n'
                    '        "SELECT item_url FROM messenger_messages"\n'
                    '        " WHERE account_id = :a AND avito_chat_id = :c"\n'
                    '        "   AND item_url IS NOT NULL AND item_url <> \'\'"\n'
                    '        " ORDER BY (item_id IS NOT DISTINCT FROM :it) DESC, id DESC LIMIT 1"\n'
                    '    ), {"a": d["account_id"], "c": d["avito_chat_id"],\n'
                    '        "it": d.get("item_id")}).scalar()\n'
                    '    return d\n', 1)
mc = mc.replace(A6, '    _links = ["<a href=\\"%s\\">Диалог в Avito</a>" % chat_url]\n'
                    '    if d.get("item_url"):\n'
                    '        _links.insert(0, "<a href=\\"%s\\">Объявление</a>" % d["item_url"])\n'
                    '    lines.append("Время: %s · %s" % (\n'
                    '        when.strftime("%H:%M") if when else "—", " · ".join(_links)))\n', 1)
mc = mc.replace(A7, '        lines.append("«%s»" % d["reply_text"].strip())\n'
                    '    elif _has_event(db, d["id"], "card_no_ai"):\n'
                    '        lines.append("")\n'
                    '        lines.append("<i>AI-черновик недоступен — пакет МОП не подключён</i>")\n'
                    '    if d.get("status") == "send_failed" and d.get("send_error"):\n', 1)

ms = ms.replace(A8, 'def _process_account_messenger_check(account_id: str, telegram_chat_id: str):\n'
                    '\n'
                    '    # Уведомление о входящем — базовая функция, работает без пакета.\n'
                    '    # Пакет проверяется в _mop_incoming и ограничивает ТОЛЬКО вызов модели.\n', 1)
ms = ms.replace(A9, '    try:\n'
                    '        _ai_enabled = bool(get_manager_balance(account_id).get("active"))\n'
                    '    except Exception as _e:\n'
                    '        print("MANAGER_GATE_ERR (%s): %s" % (account_id, _e), flush=True)\n'
                    '        _ai_enabled = False\n'
                    '\n'
                    '    db = _SL()\n'
                    '    try:\n'
                    '        st = _mc.begin_incoming(db, account_id, str(chat_id),\n'
                    '                                str(avito_message_id), incoming_text or "",\n'
                    '                                ai_enabled=_ai_enabled)\n', 1)

NEW = {MC: mc, MS: ms}
for p, s in NEW.items():
    try:
        compile(s, p, "exec")
    except SyntaxError as e:
        print("СТОП: синтаксис ДО записи —", p, e); sys.exit(1)
baks = {}
for p, s in NEW.items():
    bak = "%s.bak_bridgeA_%d" % (p, TS)
    shutil.copy2(p, bak); baks[p] = bak
    open(p, "w", encoding="utf-8").write(s)
open("/tmp/bridgeA_ts.txt", "w").write(str(TS))
try:
    for p in NEW:
        py_compile.compile(p, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПОДЭТАП A ПРИМЕНЁН, py_compile OK, import app.main OK")
    for p, b in baks.items():
        print("  бэкап:", b)
except Exception as e:
    for p, b in baks.items():
        shutil.copy2(b, p)
    print("ОТКАТ обоих файлов:", str(e)[:200]); sys.exit(1)
