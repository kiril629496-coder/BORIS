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

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
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
    raise SystemExit(main())
