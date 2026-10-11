import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/api/messenger.py"
s = open(P, encoding="utf-8").read()
if "_queue_chat_ids" in s:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A1 = ("    try:\n"
      "        chats_result = fetch_chats(account_id, unread_only=True)\n"
      "        if chats_result.get(\"status\") != \"ok\":\n"
      "            return\n")
A2 = ("            if not last_message_id:\n"
      "                continue\n"
      "            already_processed = _get_last_processed_message_id(account_id, chat_id)\n"
      "            if already_processed == last_message_id:\n")
A3 = "def _mop_incoming(account_id, chat_id, avito_message_id, incoming_text, chat,\n"

for frag, name in ((A1, "fetch_chats"), (A2, "last_processed"), (A3, "_mop_incoming")):
    n = s.count(frag)
    if n != 1:
        print("СТОП: якорь «%s» найден %d раз (нужно 1)" % (name, n)); sys.exit(1)

HELPERS = '''def _queue_enabled_at(account_id):
    """Момент включения очереди. None — старый отбор."""
    from app.db.session import SessionLocal as _SL
    from sqlalchemy import text as _t
    db = _SL()
    try:
        return db.execute(_t("SELECT queue_enabled_at FROM mop_modes"
                             " WHERE account_id = :a"), {"a": account_id}).scalar()
    except Exception:
        return None
    finally:
        db.close()


def _queue_chat_ids(account_id, queue_at):
    """Чаты, где последнее содержательное сообщение — входящее без ответа."""
    from app.db.session import SessionLocal as _SL
    from sqlalchemy import text as _t
    qts = int(queue_at.timestamp()) if hasattr(queue_at, "timestamp") else int(queue_at)
    db = _SL()
    try:
        rows = db.execute(_t(
            "WITH last_msg AS ("
            "  SELECT DISTINCT ON (avito_chat_id)"
            "         avito_chat_id, direction, avito_message_id"
            "    FROM messenger_messages"
            "   WHERE account_id = :acc"
            "     AND COALESCE(msg_type, '') <> 'system'"
            "     AND avito_created_at > :qts"
            "   ORDER BY avito_chat_id, avito_created_at DESC)"
            " SELECT l.avito_chat_id FROM last_msg l"
            "  WHERE LOWER(l.direction) LIKE 'in%'"
            "    AND NOT EXISTS (SELECT 1 FROM mop_drafts d"
            "                     WHERE d.account_id = :acc"
            "                       AND d.avito_message_id = l.avito_message_id)"
        ), {"acc": account_id, "qts": qts}).all()
        return {str(r[0]) for r in rows}
    except Exception as e:
        print("QUEUE_SQL_ERR %s: %s" % (account_id, e), flush=True)
        return set()
    finally:
        db.close()


def _close_answered_externally(account_id, avito_chat_id):
    """Менеджер ответил прямо в Avito — активную карточку закрываем."""
    from app.db.session import SessionLocal as _SL
    from sqlalchemy import text as _t
    from app import mop_core as _mc
    db = _SL()
    try:
        row = db.execute(_t(
            "SELECT id, status, avito_message_id FROM mop_drafts"
            " WHERE account_id = :a AND avito_chat_id = :c"
            "   AND status IN ('draft_ready','in_progress','editing',"
            "                  'custom_waiting','waiting_confirm')"
            " ORDER BY id DESC LIMIT 1"), {"a": account_id, "c": avito_chat_id}).fetchone()
        if not row:
            return None
        d = dict(row._mapping)
        in_ts = db.execute(_t(
            "SELECT avito_created_at FROM messenger_messages"
            " WHERE account_id = :a AND avito_message_id = :m"),
            {"a": account_id, "m": d["avito_message_id"]}).scalar()
        if in_ts is None:
            print("QUEUE_WARN: draft %s — исходное сообщение %s не найдено"
                  % (d["id"], d["avito_message_id"]), flush=True)
            return None
        has_out = db.execute(_t(
            "SELECT 1 FROM messenger_messages"
            " WHERE account_id = :a AND avito_chat_id = :c"
            "   AND LOWER(direction) LIKE 'out%'"
            "   AND COALESCE(msg_type,'') <> 'system'"
            "   AND avito_created_at > :ts LIMIT 1"),
            {"a": account_id, "c": avito_chat_id, "ts": in_ts}).first()
        if not has_out:
            return None
        row2, _prev = _mc.set_status(
            db, d["id"], "sent", (d["status"],), "answered_externally",
            channel="avito", actor_type="human", actor_id="avito",
            extra_sql=", sent_at = now(), reply_author = 'manager',"
                      " locked_at = NULL, locked_by = NULL")
        if row2 is None:
            return None
        _mc.push_card(db, d["id"])
        print("QUEUE_CLOSED draft %s: ответ вручную в Avito" % d["id"], flush=True)
        return d["id"]
    except Exception as e:
        print("QUEUE_CLOSE_ERR %s/%s: %s" % (account_id, avito_chat_id, e), flush=True)
        return None
    finally:
        db.close()


'''

NEW1 = ('    _queue_at = _queue_enabled_at(account_id)\n'
        '    try:\n'
        '        if _queue_at is None:\n'
        '            chats_result = fetch_chats(account_id, unread_only=True)\n'
        '            if chats_result.get("status") != "ok":\n'
        '                return\n'
        '        else:\n'
        '            _raw = fetch_chats(account_id, unread_only=False)\n'
        '            if _raw.get("status") != "ok":\n'
        '                return\n'
        '            _all = _raw.get("chats", [])\n'
        '            for _ch in _all:\n'
        '                _cid = _ch.get("id")\n'
        '                if not _cid:\n'
        '                    continue\n'
        '                _mr = fetch_chat_messages(account_id, _cid)\n'
        '                if _mr.get("status") != "ok":\n'
        '                    continue\n'
        '                _rw = _mr.get("messages")\n'
        '                _ms = _rw.get("messages", []) if isinstance(_rw, dict) else (_rw or [])\n'
        '                if not _ms:\n'
        '                    continue\n'
        '                _cx = _ch.get("context") or {}\n'
        '                _iv = (_cx.get("value") or {}) if isinstance(_cx, dict) else {}\n'
        '                _store_messages_locally(account_id, _cid, _iv.get("title") or "", _ms,\n'
        '                                        {"id": _iv.get("id"), "title": _iv.get("title"),\n'
        '                                         "url": _iv.get("url"), "user_id": _iv.get("user_id")})\n'
        '                _close_answered_externally(account_id, _cid)\n'
        '            _wanted = _queue_chat_ids(account_id, _queue_at)\n'
        '            chats_result = {"status": "ok",\n'
        '                            "chats": [c for c in _all if str(c.get("id")) in _wanted]}\n')

NEW2 = ("            if not last_message_id:\n"
        "                continue\n"
        "            already_processed = _get_last_processed_message_id(account_id, chat_id)\n"
        "            if _queue_at is None and already_processed == last_message_id:\n")

s = s.replace(A3, HELPERS + A3, 1)
s = s.replace(A1, NEW1, 1)
s = s.replace(A2, NEW2, 1)

try:
    compile(s, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_queue_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(s)
open("/tmp/queue_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ОЧЕРЕДЬ ПРИМЕНЕНА, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
