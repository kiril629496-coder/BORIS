"""
Разовый скрипт-миграция: создаёт (или обновляет пароль) владельца SaaS-платформы (role=owner,
account_id=None - видит все аккаунты). См. комментарий в app/api/auth.py:register.

Использование:
    venv/bin/python create_owner.py --email owner@example.com --password 'секрет123'
Или без аргументов - тогда берёт OWNER_EMAIL/OWNER_PASSWORD из .env.
Повторный запуск с тем же email НЕ создаёт дубликат - обновляет пароль существующего owner'а
(нужно явно передать --password, иначе просто сообщает, что пользователь уже существует).
"""
import argparse
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from app.models.user import User
from app.api.auth import hash_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=os.environ.get("OWNER_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("OWNER_PASSWORD"))
    args = parser.parse_args()

    if not args.email or not args.password:
        print("Нужен email и пароль: --email/--password либо OWNER_EMAIL/OWNER_PASSWORD в .env", file=sys.stderr)
        sys.exit(1)
    if len(args.password) < 6:
        print("Пароль должен быть минимум 6 символов", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == args.email).first()
        if existing:
            existing.password_hash = hash_password(args.password)
            existing.role = "owner"
            existing.account_id = None
            existing.is_active = True
            db.commit()
            print(f"OK: пароль владельца {args.email} обновлён (id={existing.id})")
        else:
            user = User(
                email=args.email,
                password_hash=hash_password(args.password),
                role="owner",
                account_id=None,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"OK: владелец {args.email} создан (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
