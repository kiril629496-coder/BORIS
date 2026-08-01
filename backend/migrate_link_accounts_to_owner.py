"""Разовая миграция: привязывает все существующие Account без owner_user_id к owner-пользователю
(создан create_owner.py). Идемпотентна - трогает только строки с owner_user_id IS NULL."""
from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from app.models.account import Account
from app.models.user import User


def main():
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.role == "owner").first()
        if not owner:
            print("Нет ни одного owner-пользователя - сначала запустите create_owner.py")
            return
        accounts = db.query(Account).filter(Account.owner_user_id.is_(None)).all()
        for acc in accounts:
            acc.owner_user_id = owner.id
        db.commit()
        print(f"OK: привязано {len(accounts)} аккаунтов к owner {owner.email} (id={owner.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
