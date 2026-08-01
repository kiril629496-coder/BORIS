"""Шифрование чувствительных полей в БД (avito_client_id/avito_client_secret) через Fernet.
Ключ - FERNET_KEY в .env. decrypt_secret() терпимо относится к ещё не мигрированным (plaintext)
значениям и к отсутствию ключа - возвращает вход как есть, а не падает, чтобы не блокировать
работу до миграции/при неправильной конфигурации."""
import os

from cryptography.fernet import Fernet, InvalidToken

_KEY = os.environ.get("FERNET_KEY")
_fernet = Fernet(_KEY.encode()) if _KEY else None


def encrypt_secret(plain: str) -> str:
    if not plain or not _fernet:
        return plain
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value or not _fernet:
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        # ещё не мигрировано (открытый текст) либо не наш формат - отдаём как есть
        return value
