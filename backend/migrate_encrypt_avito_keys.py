"""
Разовая миграция: шифрует Fernet'ом уже сохранённые avito_client_id/avito_client_secret
существующих аккаунтов (сейчас лежат открытым текстом). decrypt_secret() безопасно
пропускает то, что уже зашифровано (или не расшифровывается) - повторный запуск не портит данные.
"""
from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from app.models.account import Account
from app.crypto_utils import encrypt_secret, decrypt_secret


def main():
    db = SessionLocal()
    try:
        accounts = db.query(Account).all()
        migrated = 0
        skipped = 0
        for acc in accounts:
            changed = False
            if acc.avito_client_id:
                # если decrypt(x) != x - уже зашифровано валидным Fernet-токеном, пропускаем
                if decrypt_secret(acc.avito_client_id) == acc.avito_client_id:
                    acc.avito_client_id = encrypt_secret(acc.avito_client_id)
                    changed = True
            if acc.avito_client_secret:
                if decrypt_secret(acc.avito_client_secret) == acc.avito_client_secret:
                    acc.avito_client_secret = encrypt_secret(acc.avito_client_secret)
                    changed = True
            if changed:
                migrated += 1
            else:
                skipped += 1
        db.commit()
        print(f"OK: зашифровано {migrated} аккаунтов, пропущено (уже зашифровано/пусто) {skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
