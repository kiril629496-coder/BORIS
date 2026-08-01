from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from app.db.base import Base

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, unique=True, index=True)  # слаг, например "otdushi"
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # чей это аккаунт (client) - owner видит все
    billing_mode = Column(String, default="auto")  # auto — счёт на владельца по числу аккаунтов; manual — клиенты Кирилла, лимиты руками
    name = Column(String)  # человекочитаемое название, например "От Души — поздравления"
    password_hash = Column(String, nullable=True)  # зарезервировано под будущий полноценный вход
    avito_client_id = Column(String, nullable=True)  # свой Avito API-ключ клиента
    avito_client_secret = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)  # для уведомлений/напоминаний BORIS-бота
    avito_user_id = Column(String, nullable=True)  # numeric id в личном кабинете Авито (для stats/balance)
    avito_login = Column(String, nullable=True)  # логин (email) от Avito, для будущего парсинга личного кабинета
    avito_password = Column(String, nullable=True)  # пароль от Avito, хранится открытым текстом (техдолг)
    comment = Column(String, nullable=True)
    company_website = Column(String, nullable=True)
    client_goal = Column(String, nullable=True)
    client_goal_text = Column(String, nullable=True)
    company_niche = Column(String, nullable=True)
    company_tone = Column(String, nullable=True)
    company_description = Column(String, nullable=True)
    company_advantages = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
