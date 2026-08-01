from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, func
from app.db.base import Base


class AccountSlot(Base):
    """Оплаченный слот аккаунта. Живёт независимо от самого аккаунта:
    сначала оплачен и пуст, потом подключён, при неоплате — readonly.
    История переписки при освобождении слота НЕ удаляется."""
    __tablename__ = "account_slots"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    product = Column(String, nullable=False, default="inbox")
    slot_no = Column(Integer, nullable=False)
    # paid_empty | connecting | error | connected | readonly | released
    status = Column(String, nullable=False, default="paid_empty")
    account_id = Column(String, nullable=True, index=True)
    avito_user_id = Column(String, nullable=True)
    account_name = Column(String, nullable=True)
    display_phone = Column(String, nullable=True)   # если Avito не отдаёт — вводит клиент
    tariff_ok = Column(Boolean, nullable=True)      # None = не удалось определить
    last_check_at = Column(DateTime(timezone=True), nullable=True)
    last_check_result = Column(String, nullable=True)
    sync_status = Column(String, nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_error = Column(String, nullable=True)
    period_start = Column(DateTime(timezone=True), nullable=True)
    paid_until = Column(DateTime(timezone=True), nullable=True)
    price_rub = Column(Integer, nullable=True)
    payment_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class MessengerDialogState(Base):
    """Прочитано — отдельной таблицей, чтобы позже у каждого сотрудника
    были свои непрочитанные."""
    __tablename__ = "messenger_dialog_state"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)
    avito_chat_id = Column(String, nullable=False)
    last_read_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
