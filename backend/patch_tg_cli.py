#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Добавляет в monitor_auth.py режимы --status / --dialogs / --read."""
import io, time

P = "monitor_auth.py"
NEW_FUNCS = '''
# --- режимы проверки существующей сессии (этап 1) -----------------------------

def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except Exception:  # noqa: BLE001
        pass


def _open_existing():
    """Открывает клиента из СУЩЕСТВУЮЩЕЙ сессии. Нового кода не запрашивает."""
    api_id = os.getenv("MONITOR_TG_API_ID")
    api_hash = os.getenv("MONITOR_TG_API_HASH")
    enc = os.getenv("MONITOR_TG_SESSION_ENC")
    if not (api_id and api_hash and enc):
        print("Нет MONITOR_TG_API_ID / MONITOR_TG_API_HASH / MONITOR_TG_SESSION_ENC в .env")
        return None
    try:
        from app.crypto_utils import decrypt_secret
        from app.monitoring.telegram import build_client
        client = build_client(decrypt_secret(enc), int(api_id), api_hash)
    except Exception as e:  # noqa: BLE001
        print("Не удалось открыть сессию:", type(e).__name__)
        return None
    if not client.is_user_authorized():
        print("authorized=false — Telegram сессию не принял")
        client.disconnect()
        return None
    return client


def cmd_status() -> int:
    _load_env()
    client = _open_existing()
    if client is None:
        return 2
    try:
        me = client.get_me()
        print("telegram_user_id:", me.id)
        print("username:", getattr(me, "username", None) or "-")
        print("first_name:", getattr(me, "first_name", None) or "-")
        print("authorized=true")
    finally:
        client.disconnect()
    return 0


def _dialog_type(d) -> str:
    e = d.entity
    if getattr(d, "is_user", False):
        return "private"
    if getattr(e, "broadcast", False):
        return "channel"
    if getattr(e, "megagroup", False):
        return "supergroup"
    return "group"


def cmd_dialogs(limit: int) -> int:
    _load_env()
    client = _open_existing()
    if client is None:
        return 2
    try:
        n = 0
        for d in client.iter_dialogs(limit=limit):
            uname = getattr(d.entity, "username", None)
            print("%-16s %-11s %-20s %s" % (
                d.id, _dialog_type(d), ("@" + uname) if uname else "-",
                (d.name or "")[:40]))
            n += 1
        print("--- всего диалогов:", n)
    finally:
        client.disconnect()
    return 0


def cmd_read(dialog_id: int, limit: int) -> int:
    _load_env()
    client = _open_existing()
    if client is None:
        return 2
    try:
        entity = client.get_entity(dialog_id)
        n = 0
        for m in client.iter_messages(entity, limit=limit):
            text = (m.message or "").replace("\\n", " ")[:300]
            print("id=%s | %s | from=%s | reply_to=%s" % (
                m.id, m.date.isoformat(), m.sender_id, m.reply_to_msg_id))
            print("   ", text)
            n += 1
        print("--- прочитано сообщений:", n)
        print("OUTBOUND_MESSAGES=0 JOIN_ACTIONS=0 AI_ANALYZE=0 DB_WRITES=0")
    finally:
        client.disconnect()
    return 0

'''

DISPATCH = '''if __name__ == "__main__":
    _a = sys.argv[1:]
    if _a and _a[0] == "--status":
        raise SystemExit(cmd_status())
    elif _a and _a[0] == "--dialogs":
        raise SystemExit(cmd_dialogs(int(_a[1]) if len(_a) > 1 else 50))
    elif _a and _a[0] == "--read":
        _lim = 20
        if "--limit" in _a:
            _lim = int(_a[_a.index("--limit") + 1])
        raise SystemExit(cmd_read(int(_a[1]), _lim))
    else:
        raise SystemExit(main())
'''

src = io.open(P, encoding="utf-8").read()
if "def cmd_status()" in src:
    print("Уже применён — ничего не делаю.")
    raise SystemExit(0)

A1 = "def main() -> int:"
A2 = 'if __name__ == "__main__":\n    raise SystemExit(main())'
for a, name in ((A1, "def main"), (A2, "блок __main__")):
    c = src.count(a)
    if c != 1:
        print("СТОП: якорь %s найден %d раз, ожидалась 1. Файл не тронут." % (name, c))
        raise SystemExit(1)

out = src.replace(A1, NEW_FUNCS.lstrip("\n") + "\n" + A1, 1)
out = out.replace(A2, DISPATCH.rstrip("\n"), 1)

import ast
ast.parse(out)

bak = P + ".bak_cli_%d" % int(time.time())
io.open(bak, "w", encoding="utf-8").write(src)
io.open(P, "w", encoding="utf-8").write(out)
print("Патч применён. Бэкап:", bak)
print("Строк было:", len(src.splitlines()), "стало:", len(out.splitlines()))
