from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from app.db.base import Base

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True, default="default")
    task_type = Column(String, index=True)  # например: "generate_images", "generate_ads"
    status = Column(String, default="queued")  # queued | running | done | error | cancelled
    depends_on_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)  # стартует только
    # после status="done" предшественника; если тот "error"/"cancelled" - эта тоже "cancelled"
    payload = Column(String)  # JSON-строка с параметрами задачи
    result = Column(String, nullable=True)  # JSON-строка с результатом
    error_message = Column(String, nullable=True)
    run_at = Column(DateTime, nullable=True)  # если задано — задача ждёт этого момента (отложенное выполнение)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
