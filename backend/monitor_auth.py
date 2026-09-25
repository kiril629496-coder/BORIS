#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Одноразовая авторизация служебного Telegram-аккаунта для мониторинга.

Запускать ВРУЧНУЮ, в tmux, один раз:
    cd /root/BORIS/backend && venv/bin/python3 monitor_auth.py

Что делает:
    спрашивает номер, код из Telegram и пароль 2FA,
    получает session string,
    шифрует её существующим app/crypto_utils.encrypt_secret,
    печатает ТОЛЬКО зашифрованное значение для .env

Чего НЕ делает и не должен: не печатает и не пишет в лог session string,
код подтверждения, api_hash и номер телефона; не вступает ни в какие группы;
никому ничего не отправляет.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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
            text = (m.message or "").replace("\n", " ")[:300]
            print("id=%s | %s | from=%s | reply_to=%s" % (
                m.id, m.date.isoformat(), m.sender_id, m.reply_to_msg_id))
            print("   ", text)
            n += 1
        print("--- прочитано сообщений:", n)
        print("OUTBOUND_MESSAGES=0 JOIN_ACTIONS=0 AI_ANALYZE=0 DB_WRITES=0")
    finally:
        client.disconnect()
    return 0


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    api_id = os.getenv("MONITOR_TG_API_ID")
    api_hash = os.getenv("MONITOR_TG_API_HASH")
    if not (api_id and api_hash):
        print("Сначала пропишите в backend/.env:")
        print("  MONITOR_TG_API_ID=...")
        print("  MONITOR_TG_API_HASH=...")
        print("Получить их: my.telegram.org -> API development tools.")
        return 2

    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print("telethon не установлен. Установите в venv проекта:")
        print("  venv/bin/pip install telethon")
        return 2

    try:
        from app.crypto_utils import encrypt_secret
    except Exception as e:  # noqa: BLE001
        print(f"Не удалось импортировать app.crypto_utils: {e}")
        print("Проверьте путь модуля — шифровать сессию своим ключом нельзя.")
        return 2

    print("Авторизация служебного аккаунта.")
    print("ВАЖНО: это должен быть ОТДЕЛЬНЫЙ аккаунт, не личный аккаунт владельца.\n")

    with TelegramClient(StringSession(), int(api_id), api_hash, use_ipv6=True) as client:
        # Telethon сам запросит телефон, код и пароль 2FA в интерактивном режиме
        me = client.get_me()
        session_string = client.session.save()

    encrypted = encrypt_secret(session_string)
    del session_string

    print("\nАвторизован:", getattr(me, "first_name", "") or "", f"(id {me.id})")
    print("\nДобавьте в backend/.env одной строкой:\n")
    print(f"MONITOR_TG_SESSION_ENC={encrypted}")
    print("\nСаму строку сессии не сохраняйте и никуда не копируйте.")
    print("Прогревайте аккаунт постепенно: 5-10 источников в день, не больше.")
    return 0


if __name__ == "__main__":
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
