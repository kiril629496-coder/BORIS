from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.db.session import Base


class BusinessScenario(Base):
    """Верхняя бизнес-цель клиента. ШАГИ ЗДЕСЬ НЕ ХРАНЯТСЯ - они описаны
    константой в app/scenarios.py, а их статус вычисляется из фактов.
    В базе только то, что вычислить нельзя: выбор клиента и ручные отметки.
    Таблица создана вручную (см. отчёт аудита), create_all её не трогает."""

    __tablename__ = "business_scenarios"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String(255), nullable=False, index=True)
    user_id = Column(Integer, nullable=True)
    scenario_type = Column(String(50), nullable=False)
    input_data = Column(Text, nullable=False, default="{}")
    steps_state = Column(Text, nullable=False, default="{}")
    status = Column(String(20), nullable=False, default="active")
    # наблюдаемость: время, длительность, найдено объектов, выполнено действий,
    # рекомендованный следующий сценарий. Отдельной сущности не заводим.
    metrics = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
