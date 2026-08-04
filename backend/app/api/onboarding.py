"""
Мастер первого запуска BORIS (онбординг владельца бизнеса).

Отличие от app/onboarding.py: тот модуль считает СТАДИИ КОНКРЕТНОГО
Avito-аккаунта (таблица onboarding_progress, ключ account_id) и здесь не
используется. Этот модуль хранит прохождение МАСТЕРА пользователем: мастер
идёт ДО подключения Avito, аккаунта в этот момент ещё нет.

Ответы копятся в user_onboarding.form_data (jsonb) ПЛОСКИМ объектом с
разрешённым набором ключей. В client_facts они переносятся отдельным шагом
ПОСЛЕ создания первого аккаунта, потому что client_facts.account_id NOT NULL.
Фиктивные аккаунты ради онбординга не создаются.

Время везде пишется серверным now() в SQL; в Python сравниваются только
naive-даты, как во всей базе проекта.
"""
import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as _sql

from app.db.session import SessionLocal
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Версия анкеты. Меняется, когда меняется состав вопросов.
FORM_VERSION = 1

# Дата запуска нового онбординга. Зафиксирована КОНСТАНТОЙ, не вычисляется
# через now(): все, кто зарегистрирован раньше, мастер не проходят.
# То же значение стоит в разовой SQL-инициализации.
LAUNCH_TS = "2026-08-04 10:45:00"

# Роли, которые мастер не проходят.
SKIP_ROLES = ("manager", "admin")

# Обязательных экранов в первой версии анкеты.
REQUIRED_STEPS = 2
MAX_STEP = 2

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped_existing_user"
FINISHED = (STATUS_COMPLETED, STATUS_SKIPPED)

# --- контракт form_data --------------------------------------------------
# Белый список текстовых полей: имя -> максимальная длина.
TEXT_FIELDS = {
    "company_name": 200,
    "company_niche": 200,
    "city": 120,
    "website": 500,
    "phone": 50,
    # P0.3: выбор ниши при неоднозначном резолве. Хранится рядом с ответами,
    # свободный текст company_niche при этом не меняется.
    "resolved_niche_slug": 64,
}
URL_FIELDS = ("website",)
LIST_FIELDS = ("channels",)
MAP_FIELDS = ("channel_links",)
ALLOWED_FIELDS = set(TEXT_FIELDS) | set(LIST_FIELDS) | set(MAP_FIELDS)

# В базе храним стабильные технические ключи каналов.
ALLOWED_CHANNELS = (
    "avito", "telegram", "vk", "website", "ozon", "wildberries",
    "yandex_maps", "2gis", "youtube", "other",
)
# Русские подписи с фронта приводим к техническим ключам.
CHANNEL_ALIASES = {
    "авито": "avito",
    "телеграм": "telegram",
    "телеграмм": "telegram",
    "вк": "vk",
    "вконтакте": "vk",
    "сайт": "website",
    "озон": "ozon",
    "вайлдберриз": "wildberries",
    "wb": "wildberries",
    "яндекс карты": "yandex_maps",
    "яндекс.карты": "yandex_maps",
    "2гис": "2gis",
    "ютуб": "youtube",
    "другое": "other",
}
MAX_CHANNELS = 20
MAX_LINK_LEN = 500

# Поля, без которых мастер нельзя завершить.
REQUIRED_TEXT = ("company_name", "company_niche", "city")


def _dumps(value) -> str:
    """JSON для jsonb-колонок: русский текст пишем как есть, не экранируя."""
    return json.dumps(value, ensure_ascii=False)


def _fail(code: str, fields=None, status: int = 422):
    detail = {"code": code}
    if fields:
        detail["fields"] = list(fields)
    return HTTPException(status_code=status, detail=detail)


def _pre_clean_url(raw: str) -> str:
    """Переиспользуем существующий _clean_url, если он импортируется.

    Импорт ленивый: на уровне модуля он мог бы дать цикл через app.main.
    Если импорт не удался — работает только локальная нормализация ниже.
    """
    try:
        from app.api.parser import _clean_url  # noqa: WPS433
    except Exception:
        return raw
    try:
        cleaned = _clean_url(raw)
    except Exception:
        return raw
    return cleaned if isinstance(cleaned, str) and cleaned else raw


_URL_RE = re.compile(r"^https?://[^\s/?#]+\.[^\s/?#]+(?:[/?#][^\s]*)?$", re.I)


def _normalize_url(raw: str, field: str) -> str:
    """Без сетевых запросов: чистим, дописываем схему, проверяем форму."""
    value = _pre_clean_url((raw or "").strip().strip(",;"))
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    if not value.lower().startswith(("http://", "https://")):
        raise _fail("onboarding_bad_url", [field])
    if len(value) > MAX_LINK_LEN:
        raise _fail("onboarding_field_too_long", [field])
    if not _URL_RE.match(value):
        raise _fail("onboarding_bad_url", [field])
    return value


def _norm_channel(raw, field: str) -> str:
    if not isinstance(raw, str):
        raise _fail("onboarding_bad_type", [field])
    key = raw.strip().lower()
    key = CHANNEL_ALIASES.get(key, key)
    if key not in ALLOWED_CHANNELS:
        raise _fail("onboarding_unknown_channel", [raw])
    return key


def _clean_form(payload: Dict[str, Any], stored: Dict[str, Any]) -> Dict[str, Any]:
    """Проверить и нормализовать входящие поля.

    Неизвестный ключ — 422, а не тихое удаление: иначе фронт ошибётся в
    названии поля, а пользователь решит, что данные сохранены.
    """
    if not isinstance(payload, dict):
        raise _fail("onboarding_bad_type", ["data"])

    unknown = [k for k in payload if k not in ALLOWED_FIELDS]
    if unknown:
        raise _fail("onboarding_unknown_fields", sorted(unknown))

    out: Dict[str, Any] = {}

    for key, limit in TEXT_FIELDS.items():
        if key not in payload:
            continue
        raw = payload[key]
        if raw is None:
            out[key] = None
            continue
        if not isinstance(raw, str):
            raise _fail("onboarding_bad_type", [key])
        value = raw.strip()
        if key == "phone":
            value = re.sub(r"\s+", " ", value)
        if len(value) > limit:
            raise _fail("onboarding_field_too_long", [key])
        if not value:
            out[key] = None
            continue
        if key in URL_FIELDS:
            value = _normalize_url(value, key)
        out[key] = value

    if "channels" in payload:
        raw = payload["channels"]
        if raw is None:
            out["channels"] = []
        else:
            if not isinstance(raw, list):
                raise _fail("onboarding_bad_type", ["channels"])
            if len(raw) > MAX_CHANNELS:
                raise _fail("onboarding_too_many_channels", ["channels"])
            seen = []
            for item in raw:
                key = _norm_channel(item, "channels")
                if key not in seen:      # дубли убираем, порядок сохраняем
                    seen.append(key)
            out["channels"] = seen

    if "channel_links" in payload:
        raw = payload["channel_links"]
        if raw is None:
            out["channel_links"] = {}
        else:
            if not isinstance(raw, dict):
                raise _fail("onboarding_bad_type", ["channel_links"])
            if len(raw) > MAX_CHANNELS:
                raise _fail("onboarding_too_many_channels", ["channel_links"])
            # ссылки разрешены только для уже выбранных каналов
            selected = out.get("channels")
            if selected is None:
                selected = list(stored.get("channels") or [])
            links = {}
            for name, url in raw.items():
                chan = _norm_channel(name, "channel_links")
                if chan not in selected:
                    raise _fail("onboarding_link_without_channel", [chan])
                if url is None or (isinstance(url, str) and not url.strip()):
                    continue
                if not isinstance(url, str):
                    raise _fail("onboarding_bad_type", ["channel_links." + chan])
                links[chan] = _normalize_url(url, "channel_links." + chan)
            out["channel_links"] = links

    return out


class StepBody(BaseModel):
    step: int
    data: Dict[str, Any] = {}


class SkipBody(BaseModel):
    block: str


def _fetch(db, user_id: int) -> Optional[dict]:
    r = db.execute(_sql(
        "SELECT user_id, status, current_step, skipped_blocks, form_data,"
        " form_version, started_at, completed_at"
        " FROM user_onboarding WHERE user_id = :u"), {"u": user_id}).fetchone()
    if not r:
        return None
    return {
        "user_id": r[0],
        "status": r[1],
        "current_step": int(r[2] or 0),
        "skipped_blocks": r[3] if r[3] is not None else [],
        "form_data": r[4] if r[4] is not None else {},
        "form_version": int(r[5] or FORM_VERSION),
        "started_at": r[6],
        "completed_at": r[7],
    }


def _is_existing_user(db, user) -> bool:
    """Кому мастер показывать не нужно.

    1) роль менеджера или админа;
    2) регистрация раньше даты запуска мастера;
    3) страховка — уже есть аккаунт с ключами Avito.
    """
    role = (getattr(user, "role", "") or "").lower()
    if role in SKIP_ROLES:
        return True

    created = getattr(user, "created_at", None)
    if created is not None:
        launch = datetime.strptime(LAUNCH_TS, "%Y-%m-%d %H:%M:%S")
        if created < launch:
            return True

    r = db.execute(_sql(
        "SELECT 1 FROM accounts WHERE owner_user_id = :u"
        " AND avito_client_id IS NOT NULL AND avito_client_id <> '' LIMIT 1"),
        {"u": getattr(user, "id", 0)}).fetchone()
    return bool(r)


def _ensure(db, user) -> dict:
    uid = getattr(user, "id", 0)
    row = _fetch(db, uid)
    if row:
        return row

    status = STATUS_SKIPPED if _is_existing_user(db, user) else STATUS_NOT_STARTED
    db.execute(_sql(
        "INSERT INTO user_onboarding (user_id, status, current_step, form_version,"
        " updated_at) VALUES (:u, :s, 0, :v, now())"
        " ON CONFLICT (user_id) DO NOTHING"),
        {"u": uid, "s": status, "v": FORM_VERSION})
    db.commit()
    return _fetch(db, uid) or {
        "user_id": uid, "status": status, "current_step": 0,
        "skipped_blocks": [], "form_data": {}, "form_version": FORM_VERSION,
        "started_at": None, "completed_at": None,
    }


def _public(row: dict) -> dict:
    """Единственный источник правды для редиректов и восстановления формы."""
    return {
        "status": row["status"],
        "current_step": row["current_step"],
        "form_version": row["form_version"],
        "required_steps": REQUIRED_STEPS,
        "skipped_blocks": row["skipped_blocks"],
        "form_data": row["form_data"],
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }


def _guard_finished(row: dict):
    if row["status"] in FINISHED:
        raise _fail("onboarding_already_finished", status=409)


# --- эндпоинты -----------------------------------------------------------

@router.get("/status")
def get_status(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return _public(_ensure(db, user))
    finally:
        db.close()


@router.post("/start")
def start(user=Depends(get_current_user)):
    """not_started -> in_progress. Идемпотентно; завершённый не переоткрывает."""
    db = SessionLocal()
    try:
        row = _ensure(db, user)
        if row["status"] in FINISHED:
            return _public(row)
        db.execute(_sql(
            "UPDATE user_onboarding SET status = :s,"
            " started_at = COALESCE(started_at, now()), updated_at = now()"
            " WHERE user_id = :u"),
            {"s": STATUS_IN_PROGRESS, "u": row["user_id"]})
        db.commit()
        return _public(_fetch(db, row["user_id"]))
    finally:
        db.close()


def _step_impl(body: StepBody, user) -> dict:
    """Общая логика POST /step и PATCH /step."""
    if body.step < 1 or body.step > MAX_STEP:
        raise _fail("onboarding_bad_step", ["step"])

    db = SessionLocal()
    try:
        row = _ensure(db, user)
        _guard_finished(row)

        # нельзя перескочить экран: шаг N доступен, когда достигнут N-1
        if body.step - 1 > row["current_step"]:
            raise _fail("onboarding_step_out_of_order", ["step"])

        stored = dict(row["form_data"] or {})
        clean = _clean_form(body.data or {}, stored)
        stored.update(clean)          # сливаем поля, старое не затираем

        reached = max(row["current_step"], body.step)
        db.execute(_sql(
            "UPDATE user_onboarding SET form_data = CAST(:d AS jsonb),"
            " current_step = :c, status = :s,"
            " started_at = COALESCE(started_at, now()), updated_at = now()"
            " WHERE user_id = :u"),
            {"d": _dumps(stored), "c": reached, "s": STATUS_IN_PROGRESS,
             "u": row["user_id"]})
        db.commit()
        return _public(_fetch(db, row["user_id"]))
    finally:
        db.close()


@router.post("/step")
def save_step(body: StepBody, user=Depends(get_current_user)):
    return _step_impl(body, user)


@router.patch("/step")
def patch_step(body: StepBody, user=Depends(get_current_user)):
    return _step_impl(body, user)


def _skip_impl(body: SkipBody, user) -> dict:
    name = (body.block or "").strip()[:64]
    if not name:
        raise _fail("onboarding_bad_block", ["block"])

    db = SessionLocal()
    try:
        row = _ensure(db, user)
        _guard_finished(row)

        blocks = list(row["skipped_blocks"] or [])
        if name not in blocks:
            blocks.append(name)
        db.execute(_sql(
            "UPDATE user_onboarding SET skipped_blocks = CAST(:b AS jsonb),"
            " updated_at = now() WHERE user_id = :u"),
            {"b": _dumps(blocks), "u": row["user_id"]})
        db.commit()
        return _public(_fetch(db, row["user_id"]))
    finally:
        db.close()


@router.post("/skip_block")
def skip_block_legacy(body: SkipBody, user=Depends(get_current_user)):
    return _skip_impl(body, user)


@router.post("/skip-block")
def skip_block(body: SkipBody, user=Depends(get_current_user)):
    return _skip_impl(body, user)


def _missing_required(form: Dict[str, Any]):
    missing = []
    for key in REQUIRED_TEXT:
        value = form.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    channels = form.get("channels")
    if not isinstance(channels, list) or not channels:
        missing.append("channels")
    return missing


@router.post("/complete")
def complete(user=Depends(get_current_user)):
    """Завершить мастер. Повторный вызов идемпотентен, данные не удаляются."""
    db = SessionLocal()
    try:
        row = _ensure(db, user)
        if row["status"] in FINISHED:
            return _public(row)

        missing = _missing_required(row["form_data"] or {})
        if missing:
            raise _fail("onboarding_required_fields_missing", missing)

        db.execute(_sql(
            "UPDATE user_onboarding SET status = :s, completed_at = now(),"
            " updated_at = now() WHERE user_id = :u"),
            {"s": STATUS_COMPLETED, "u": row["user_id"]})
        db.commit()
        return _public(_fetch(db, row["user_id"]))
    finally:
        db.close()


# --- P0.3: персональный анализ -------------------------------------------

from app import niches as _niches                     # noqa: E402
from app import analysis_onboarding as _analysis      # noqa: E402

# Внутренняя продуктовая аналитика BORIS. В Яндекс.Метрику ничего не уходит:
# в кабинете она намеренно отключена ради приватности клиентов.
ALLOWED_EVENTS = (
    "onboarding_analysis_opened",
    "onboarding_analysis_completed",
    "onboarding_niche_matched",
    "onboarding_niche_ambiguous",
    "onboarding_niche_not_found",
    "onboarding_niche_selected",
    "onboarding_scenarios_clicked",
)


class EventBody(BaseModel):
    event: str
    payload: Dict[str, Any] = {}


class NicheChoiceBody(BaseModel):
    slug: str


def _log_event(db, user_id, event, payload=None):
    """Пишет продуктовое событие. Любая ошибка здесь не должна ломать ответ."""
    try:
        db.execute(_sql(
            "INSERT INTO product_events (user_id, event, payload)"
            " VALUES (:u, :e, CAST(:p AS jsonb))"),
            {"u": user_id, "e": event, "p": _dumps(payload or {})})
        db.commit()
    except Exception:
        db.rollback()


@router.get("/analysis")
def analysis(user=Depends(get_current_user)):
    """Персональный анализ. Только form_data и База знаний, без вызовов модели."""
    db = SessionLocal()
    try:
        row = _ensure(db, user)
        form = row["form_data"] or {}

        chosen = (form.get("resolved_niche_slug") or "").strip()
        if chosen and _niches.get(chosen):
            resolution = _niches.NicheResolution("matched", slug=chosen)
            resolved_by = "user"
        else:
            resolution = _niches.resolve(form.get("company_niche") or "")
            resolved_by = "auto"

        out = _analysis.build(form, _niches, resolution)
        out["resolved_by"] = resolved_by
        out["onboarding_status"] = row["status"]
        out["knowledge"] = {"schema_version": _niches.health().get("schema_version"),
                            "niches_ok": _niches.health().get("niches_ok")}

        _log_event(db, row["user_id"], "onboarding_analysis_opened",
                   {"status": resolution.status, "resolved_by": resolved_by})
        _log_event(db, row["user_id"], "onboarding_niche_" + resolution.status,
                   {"slug": resolution.slug, "candidates": resolution.candidates})
        return out
    finally:
        db.close()


@router.post("/resolve_niche")
def resolve_niche(body: NicheChoiceBody, user=Depends(get_current_user)):
    """Выбор ниши при неоднозначном резолве.

    Отдельный эндпоинт, а не /step: экран анализа открывается уже после
    завершения мастера, а /step для завершённого отвечает 409.
    Свободный текст company_niche не меняется — сохраняется только выбор.
    """
    slug = (body.slug or "").strip()
    if not _niches.get(slug):
        raise _fail("onboarding_unknown_niche", [slug])

    db = SessionLocal()
    try:
        row = _ensure(db, user)
        form = dict(row["form_data"] or {})
        form["resolved_niche_slug"] = slug
        db.execute(_sql(
            "UPDATE user_onboarding SET form_data = CAST(:d AS jsonb), updated_at = now()"
            " WHERE user_id = :u"), {"d": _dumps(form), "u": row["user_id"]})
        db.commit()
        _log_event(db, row["user_id"], "onboarding_niche_selected", {"slug": slug})
        return _public(_fetch(db, row["user_id"]))
    finally:
        db.close()


@router.post("/event")
def product_event(body: EventBody, user=Depends(get_current_user)):
    """Продуктовое событие с фронта. Имена — только из белого списка."""
    if body.event not in ALLOWED_EVENTS:
        raise _fail("onboarding_unknown_event", [body.event])
    db = SessionLocal()
    try:
        _log_event(db, getattr(user, "id", 0), body.event, body.payload or {})
        return {"status": "ok"}
    finally:
        db.close()
