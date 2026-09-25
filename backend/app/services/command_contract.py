# -*- coding: utf-8 -*-
"""Контракт BorisCommand — единая структура команды для всех каналов.

Инварианты (этап 1):
  * модель возвращает СТРУКТУРУ, а не действие;
  * operation / risk / permission / units проставляет реестр, а не модель;
  * account_id из входа — подсказка, право подтверждает сервер;
  * останавливает выполнение clarify_question, а НЕ confidence;
  * confidence хранится только для аналитики качества парсера.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

SOURCES = ("text", "voice")

RISKS = ("green", "yellow", "red")

STATUSES = (
    "parsed",
    "awaiting_confirm",
    "confirmed",
    "executing",
    "done",
    "failed",
    "expired",
    "cancelled",
)

# Белый список типов сущностей. Свободный тип = свободный объект в запросе,
# поэтому расширяется только правкой этого кортежа.
ENTITY_TYPES = ("dialog", "lead", "item", "call", "task", "account")

CONFIRM_TTL_MINUTES = 15

# Типы шагов будущего оркестратора. В MVP шаг всегда ровно один, но каждая
# новая операция обязана уметь стать шагом одного BorisCommand.
STEP_TYPES = (
    "operation",
    "generate_banners", "publish_listings", "ab_test",
    "report_back", "analyze_competitors", "collect_stats",
)


class CommandInput(BaseModel):
    command_id: Optional[str] = None
    source: str = "text"
    text: Optional[str] = None
    transcript: Optional[str] = None
    account_id: Optional[str] = None
    page_context: Optional[Dict[str, Any]] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    locale: str = "ru"
    device: Optional[str] = None
    audio_duration: Optional[float] = None
    audio_format: Optional[str] = None
    transcription_model: Optional[str] = None

    def user_text(self) -> str:
        """Что реально уходит в разбор: у голоса это расшифровка."""
        if self.source == "voice":
            # Клиенты старых/новых версий могут прислать расшифровку как
            # transcript или text. Голосовая команда не должна превращаться
            # в пустую строку из-за транспортного имени поля.
            return (self.transcript or self.text or "").strip()
        return (self.text or self.transcript or "").strip()


class ProposedChange(BaseModel):
    """Изменение ВНУТРЕННИХ данных BORIS (yellow)."""
    entity: str
    field: Optional[str] = None
    before: Optional[Any] = None
    after: Optional[Any] = None
    human: Optional[str] = None


class ProposedAction(BaseModel):
    """Внешнее действие или действие с деньгами (red)."""
    operation: str
    target: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    human: Optional[str] = None


class Attachment(BaseModel):
    kind: str
    title: Optional[str] = None
    url: Optional[str] = None
    data: Optional[Any] = None
    expires_at: Optional[str] = None


class CommandStep(BaseModel):
    type: str = "operation"
    operation: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class CommandOutput(BaseModel):
    command_id: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    entities: Optional[Dict[str, Any]] = None
    operation: Optional[str] = None
    risk_level: Optional[str] = None
    permission: Optional[str] = None
    units: int = 0
    steps: Optional[List[CommandStep]] = None
    proposed_changes: Optional[List[ProposedChange]] = None
    proposed_actions: Optional[List[ProposedAction]] = None
    needs_confirmation: bool = False
    human_summary: Optional[str] = None
    clarify_question: Optional[str] = None
    answer: Optional[str] = None
    attachments: Optional[List[Attachment]] = None
    stage: Optional[str] = None
    status: str = "parsed"
    expires_at: Optional[str] = None
    error_code: Optional[str] = None


ERRORS = {
    "no_billing_owner": "У аккаунта не задан владелец — команды недоступны",
    "feature_disabled": "Центр управления BORIS сейчас недоступен для этого аккаунта",
    "voice_disabled": "Голосовое управление доступно в тарифе Voice",
    "pool_exhausted": "Команды на этот период закончились",
    "pool_expired": "Период пакета команд истёк",
    "forbidden_account": "Аккаунт недоступен",
    "forbidden_operation": "Недостаточно прав для этого действия",
    "unknown_intent": "Уточните, что нужно сделать — например: найти клиента, создать объявление или показать цифры",
    "not_implemented": "Операция ещё не подключена",
    "expired": "Подтверждение просрочено, повторите команду",
    "already_charged": "Команда уже выполнена",
    "pool_exhausted_after_success": "Выполнено, но пакет команд исчерпан",
    "already_final": "Команда уже завершена",
    "idempotency_conflict": "Этот идентификатор команды уже использован для другого запроса",
    "executor_outcome_uncertain": "Результат выполнения не удалось подтвердить. BORIS не повторяет действие автоматически",
    "stale_preexecution": "Команда не дошла до выполнения и безопасно остановлена. Повторите её новой командой",
    "control_plane_capture_failed": "Команда безопасно остановлена: BORIS не смог записать её в единую память требований",
}
