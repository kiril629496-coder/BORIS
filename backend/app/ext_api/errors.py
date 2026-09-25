# -*- coding: utf-8 -*-
"""Единый контракт ошибок. Наружу — код и человеческая формулировка,
traceback остаётся во внутреннем логе.
"""
import logging, traceback

log = logging.getLogger("boris.ext_api")

# код -> (http, retryable, текст по умолчанию)
CODES = {
    "UNAUTHORIZED":               (401, False, "ключ не передан или неверен"),
    "KEY_REVOKED":                (401, False, "ключ отозван"),
    "FORBIDDEN_SCOPE":            (403, False, "ключу не выдан нужный scope"),
    "FORBIDDEN_ACCOUNT":          (403, False, "ключу не разрешён этот аккаунт"),
    "RATE_LIMITED":               (429, True,  "превышен лимит запросов для ключа"),
    "IDEMPOTENCY_CONFLICT":       (409, False, "этот idempotency_key уже использован с другим телом"),
    "VALIDATION_ERROR":           (400, False, "тело запроса не проходит валидацию"),
    "UNKNOWN_OPERATION":          (400, False, "такой операции нет в реестре"),
    "NOT_IMPLEMENTED":            (501, False, "у операции нет штатного исполнителя"),
    "ACCOUNT_NOT_FOUND":          (404, False, "аккаунт не найден"),
    "ACCOUNT_AMBIGUOUS":          (400, False, "под запрос подходит несколько аккаунтов"),
    "JOB_NOT_FOUND":              (404, False, "задача не найдена"),
    "JOB_NOT_RETRYABLE":          (409, False, "эту задачу нельзя перезапустить в текущем статусе"),
    "JOB_NOT_CANCELLABLE":        (409, False, "эту задачу нельзя отменить в текущем статусе"),
    "CONFIRMATION_REQUIRED":      (403, False, "операция требует явного подтверждения"),
    "EXTERNAL_PUBLISH_DISABLED":  (403, False, "внешняя публикация в этой версии шлюза выключена"),
    "PAID_ACTION_DISABLED":       (403, False, "платные действия в этой версии шлюза выключены"),
    "PAYMENT_APPROVAL_REQUIRED":  (402, False, "операция требует реальных денег — нужно явное разрешение пользователя"),
    "BILLING_MODE_FORBIDDEN":     (402, False, "переход на API-биллинг запрещён без разрешения пользователя"),
    "API_BILLING_REQUIRED":       (402, False, "этот путь работает только через отдельный API-биллинг"),
    "ADAPTER_DISABLED":           (403, False, "адаптер агента выключен"),
    "ACCEPTANCE_NOT_PASSED":      (409, False, "критерии приёмки не пройдены"),
    "AVITO_CREDENTIALS_MISSING":  (424, False, "у аккаунта нет ключей Avito"),
    "MISSING_INPUT":              (400, False, "не хватает входных данных"),
    "TOO_MANY_ITEMS":             (413, False, "в одном вызове слишком много элементов"),
    "DEPENDENCY_UNAVAILABLE":     (424, True,  "недоступна зависимость production"),
    "POSTFLIGHT_MISMATCH":        (500, False, "фактическое состояние не совпало с ожидаемым"),
    "POSTFLIGHT_UNVERIFIED":      (500, False, "результат не удалось подтвердить фактическим состоянием"),
    "HANDOFF_NOT_FOUND":          (404, False, "передача не найдена"),
    "INTERNAL":                   (500, False, "внутренняя ошибка"),
}

TRANSIENT_MARKERS = ("timeout", "timed out", "502", "503", "504", "connection reset",
                     "connection aborted", "temporarily unavailable", "too many requests",
                     "rate limit", "deadlock detected", "server closed the connection")


class ApiError(Exception):
    """Ошибка с кодом из контракта. Никогда не несёт наружу traceback."""

    def __init__(self, code, message=None, retryable=None, details=None):
        http, retry_default, default_msg = CODES.get(code, CODES["INTERNAL"])
        self.code = code
        self.http = http
        self.message = message or default_msg
        self.retryable = retry_default if retryable is None else retryable
        self.details = details or {}
        super().__init__(f"{code}: {self.message}")

    def body(self, request_id=None, job_id=None, status="failed"):
        out = {"status": status, "code": self.code, "message": self.message,
               "retryable": self.retryable, "job_id": job_id, "request_id": request_id}
        if self.details:
            out["details"] = self.details
        return out


def classify(exc):
    """Транзиентная ошибка -> можно ретраить. Бизнес-ошибка -> BLOCKED."""
    if isinstance(exc, ApiError):
        return exc.code, exc.retryable
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(m in text for m in TRANSIENT_MARKERS):
        return "DEPENDENCY_UNAVAILABLE", True
    return "INTERNAL", False


def log_internal(request_id, where, exc):
    log.error("[ext_api][%s] %s: %s\n%s", request_id, where, exc,
              "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[:8000])
