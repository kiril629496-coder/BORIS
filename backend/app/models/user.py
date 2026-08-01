from sqlalchemy import Column, Integer, String, DateTime, Boolean, func, Numeric
from app.db.base import Base

class User(Base):
    """Пользователь SaaS-платформы БОРИС.
    role: owner (Кирилл, видит все аккаунты) | client (обычный клиент, видит только свой).
    Один клиентский пользователь = один account_id (жёсткая привязка), у owner account_id = None."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="client")
    ref_code = Column(String, nullable=True)          # персональный код менеджера
    referred_by = Column(String, nullable=True)       # email менеджера, который привёл клиента
    referred_at = Column(DateTime, nullable=True)
    commission_rate = Column(Numeric(5, 2), default=0)
    planned_accounts = Column(String, nullable=True)  # сколько Avito-аккаунтов планирует вести — сегментация, на цену НЕ влияет  # "owner" или "client"
    account_id = Column(String, index=True, nullable=True)  # None у owner, слаг account_id у клиента
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)
    subscription_expires_at = Column(DateTime, nullable=True)  # триал/подписка - 4 дня с регистрации

    # --- подтверждение email (миграция 001_email_verification.sql) ---
    # status — единственный гейт доступа в кабинет. is_active оставлен как был:
    # "выключен вручную владельцем". Значения ограничены констрейнтом ck_users_status.
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verified_at = Column(DateTime, nullable=True)
    status = Column(String(32), default="active", nullable=False)  # pending_verification | active | blocked
    email_normalized = Column(String, unique=True, index=True, nullable=True)  # lower(btrim(email)), плюс-теги сохраняются
    trial_started_at = Column(DateTime, nullable=True)  # общий 4-дневный триал выдавался — защита от повторной выдачи
