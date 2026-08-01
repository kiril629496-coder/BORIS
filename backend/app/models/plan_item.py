from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from app.db.base import Base

class PlanItem(Base):
    __tablename__ = "plan_items"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True)
    text = Column(String)  # текст пункта плана
    status = Column(String, default="planned")  # planned | needs_launch | in_progress | done | cancelled
    source = Column(String, default="user")  # user | boris
    linked_task_id = Column(Integer, nullable=True)  # если пункт создан оркестратором — ссылка на Task.id
    due_date = Column(DateTime, nullable=True)  # дата/время для календаря
    remind_at = Column(DateTime, nullable=True)  # момент напоминания
    reminder_sent = Column(Boolean, default=False)  # отправлено ли уже уведомление
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
