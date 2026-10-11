"""SaaS-авторизация BORIS: регистрация, логин, JWT-токены, зависимость get_current_user
для защиты эндпоинтов. Роли: owner (владелец, видит всё) | client (свой account_id).
"""
import os
import datetime
import json
import hashlib
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
import re
from pydantic import BaseModel, EmailStr
import bcrypt
from jose import jwt, JWTError

from app.db.session import SessionLocal
from app.models.user import User
from app.models.account import Account
from app.models.storage import Storage
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Секрет для подписи JWT — берём из .env, если нет — генерируем предупреждение.
# В продакшене ОБЯЗАТЕЛЬНО задать в .env, иначе токены будут инвалидироваться при каждом рестарте.
JWT_SECRET = os.environ.get("JWT_SECRET", "CHANGE_ME_IN_ENV_TEMPORARY_DEV_SECRET_" + os.urandom(8).hex())
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = max(1, min(int(os.environ.get("BORIS_AUTH_SESSION_TTL_HOURS", "168")), 24 * 30))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def create_access_token(
    user_id: int,
    *,
    session_id: str | None = None,
    security_epoch: int = 0,
    expires_at: datetime.datetime | None = None,
) -> str:
    expire = expires_at or (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=JWT_EXPIRE_HOURS)
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "sev": int(security_epoch or 0),
    }
    if session_id:
        payload["sid"] = str(session_id)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _issue_user_session_token(db, user: User, request: Request) -> str:
    """Issue a server-side revocable session and bind the JWT to it."""
    from app.services.auth_sessions import issue_session
    epoch = int(getattr(user, "security_epoch", 0) or 0)
    sid, expires = issue_session(
        db,
        int(user.id),
        epoch,
        user_agent=request.headers.get("user-agent", ""),
        ip=(request.client.host if request.client else ""),
    )
    db.commit()
    return create_access_token(
        int(user.id),
        session_id=sid,
        security_epoch=epoch,
        expires_at=expires,
    )


def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> User:
    """FastAPI-зависимость: достаёт пользователя из JWT-токена в заголовке Authorization.
    Используется на защищённых эндпоинтах: def endpoint(user: User = Depends(get_current_user))."""
    if not token:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
        session_id = str(payload.get("sid") or "").strip()
        token_epoch = int(payload.get("sev") or 0)
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Некорректный или просроченный токен")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=401, detail="Пользователь не найден или деактивирован")
        current_epoch = int(getattr(user, "security_epoch", 0) or 0)
        if token_epoch != current_epoch:
            raise HTTPException(status_code=401, detail="Сеанс завершён. Выполните вход снова.")
        if session_id:
            from app.services.auth_sessions import validate_session
            if not validate_session(
                db,
                user_id=int(user.id),
                session_id=session_id,
                token_epoch=token_epoch,
                current_epoch=current_epoch,
            ):
                raise HTTPException(status_code=401, detail="Сеанс завершён. Выполните вход снова.")
        db.refresh(user)
        user._auth_session_id = session_id or None
        user._auth_security_epoch = token_epoch
        return user
    finally:
        db.close()


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Более строгая зависимость: только для эндпоинтов уровня владельца SaaS."""
    from app.services.platform_roles import is_platform_owner
    if not is_platform_owner(user):
        raise HTTPException(status_code=403, detail="Доступ только для владельца")
    return user


_TRUSTED_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def get_current_user_or_internal(request: Request, token: Optional[str] = Depends(oauth2_scheme)):
    """Пропускает ЛИБО валидный JWT, ЛИБО запрос с localhost без токена вообще.
    Нужно для массовой защиты роутеров через include_router(dependencies=[...]) в main.py:
    backend сам себя вызывает по HTTP (avito.py/plan_items.py self-calls на 127.0.0.1:8000) и
    3 cron-скрипта (kpi_autopilot_runner.py, daily_stats_collector.py, cpx_advisor_runner.py)
    тоже бьют строго в 127.0.0.1:8000 - у них нет пользовательского токена и не должно быть
    (это не запрос от лица клиента). Доверяем localhost целиком: сервер однопользовательский,
    shell-доступ туда и так означает root. Возвращает User для настоящих запросов, None для
    доверенных internal-вызовов - эндпоинты, которым не нужен user (большинство), просто не
    объявляют его в сигнатуре и получают эту зависимость только как охрану доступа."""
    client_host = request.client.host if request.client else None
    if client_host in _TRUSTED_LOCAL_HOSTS and not token:
        return None
    # Исключение: уведомление Робокассы об оплате приходит с их серверов и не может
    # иметь нашего токена. Подделать нельзя: внутри эндпоинта проверяется подпись
    # паролем #2, без неё запрос отбрасывается. Разрешаем ТОЛЬКО этот путь и только POST.
    if (request.method == "POST"
            and request.url.path == "/api/payments/robokassa/result"):
        return None
    user = get_current_user(token)
    # Гейт подтверждения почты. Стоит ТОЛЬКО здесь: internal-вызовы с localhost и
    # уведомление Робокассы вернулись выше через None и сюда не доходят, а
    # manager_router, legal.py и /me используют get_current_user напрямую и не гейтятся
    # (фронту нужен /me, чтобы узнать status и увести человека на экран ввода кода).
    if getattr(user, "status", None) == "pending_verification":
        raise HTTPException(status_code=403, detail={
            "code": "email_not_verified",
            "message": "Подтвердите электронную почту, чтобы продолжить",
        })
    return user


def _account_subscription_access_state(db, account_id: str) -> dict:
    """Read-only base-tariff access gate for client-facing account APIs.

    Delegate the paid clock to the shared subscription authority so Auth,
    Messenger and Inbox cannot disagree about the same account.
    """
    acc = db.query(Account).filter(Account.account_id == str(account_id)).first()
    if not acc:
        return {"allowed": True, "state": "account_missing"}
    from app.services.subscription_gate import base_subscription_access
    state = dict(base_subscription_access(db, str(account_id)))
    if state.get("state") == "expired":
        state["state"] = "subscription_expired"
    return state


def _user_subscription_scope_state(db, user: User) -> dict:
    """Gate cross-account screens when every accessible client account expired."""
    from sqlalchemy import text as _scope_text
    ids = set()
    if str(getattr(user, "account_id", "") or "").strip():
        ids.add(str(user.account_id))
    for row in db.query(Account.account_id).filter(Account.owner_user_id == user.id).all():
        if row and row[0]:
            ids.add(str(row[0]))
    for row in db.execute(_scope_text(
        "SELECT account_id FROM user_account_access "
        "WHERE user_id=:u AND can_view=TRUE"
    ), {"u": user.id}).fetchall():
        if row and row[0]:
            ids.add(str(row[0]))
    if not ids:
        return {"allowed": True, "state": "no_account_scope", "accounts": []}

    # Internal QA fixtures share owner scope for automated acceptance but
    # are not paid customer workspaces and must never keep an expired client open.
    ids = {
        aid for aid in ids
        if aid and not str(aid).lower().startswith("qa")
    }
    if not ids:
        return {"allowed": True, "state": "no_real_account_scope", "accounts": []}

    states = {
        aid: _account_subscription_access_state(db, aid)
        for aid in sorted(ids)
    }
    if any(bool(v.get("allowed")) for v in states.values()):
        return {"allowed": True, "state": "some_account_active_or_unknown", "accounts": states}
    if states and all(str(v.get("state") or "") == "subscription_expired" for v in states.values()):
        return {"allowed": False, "state": "all_accounts_subscription_expired", "accounts": states}
    return {"allowed": True, "state": "mixed_or_unknown", "accounts": states}


async def check_account_access(request: Request, user=Depends(get_current_user_or_internal)):
    """Единая зависимость изоляции мультитенантности - вешается на роутеры целиком (см. main.py),
    а не копипастится в каждый эндпоинт. user is None (internal/localhost) и role="owner" -
    пропускаем без проверки. Для client достаём account_id из запроса (path -> query -> JSON-тело
    -> form-тело, в этом порядке) и сравниваем с user.account_id - при несовпадении 403. Если в
    запросе вообще нет account_id - нечего изолировать, пропускаем."""
    if user is None or user.role == "owner":
        return user

    # UNPAID_ACCOUNT_PAYMENT_ESCAPE_V1:
    # Expired clients must still be able to see billing/payment state and pay.
    # Every account-scoped product API is gated later by billing.paid_until.
    if (
        request.url.path.startswith("/api/payments/")
        or request.url.path.startswith("/api/billing/")
    ):
        return user

    # Исключение: /api/accounts/list сам фильтрует по user.account_id и физически не может
    # отдать чужой аккаунт, поэтому не требует account_id в запросе. Без этого клиент без
    # аккаунта получал 403 вместо пустого списка — из-за чего у новичка не открывался визард.
    if request.url.path == "/api/accounts/list":
        return user

    # UNPAID_CROSS_ACCOUNT_ACCESS_GATE_V1:
    # Cross-account screens (CRM today, inbox aggregate, etc.) do not carry one
    # account_id. If every account available to this client has an explicitly
    # expired base subscription, close those screens too. Payment/list remain open.
    _scope_db = SessionLocal()
    try:
        _scope_subscription = _user_subscription_scope_state(_scope_db, user)
    finally:
        _scope_db.close()
    if not bool(_scope_subscription.get("allowed")):
        raise HTTPException(
            status_code=402,
            detail={
                "code": "subscription_expired",
                "message": "Оплаченный период закончился. Оплатите продление, чтобы открыть кабинет.",
                "scope_state": _scope_subscription.get("state"),
                "accounts": _scope_subscription.get("accounts"),
            },
        )

    # Исключение: сводка задач на сегодня кросс-аккаунтная по замыслу —
    # это шапка кабинета, а не экран одного аккаунта, поэтому account_id
    # в запросе нет и сравнивать нечего. Ручка сама отбирает задачи только
    # по аккаунтам этого пользователя: владение плюс user_account_access.
    if request.url.path == "/api/crm/today":
        return user

    # Исключение: кабинет агентства работает на уровне ВЛАДЕЛЬЦА, а не отдельного аккаунта —
    # оба эндпоинта сами фильтруют по owner_user_id и чужого не отдадут.
    if request.url.path in ("/api/accounts/agency_overview", "/api/accounts/billing_preview", "/api/accounts/add_account"):
        return user

    # Исключение: экран подключения аккаунтов инбокса работает на уровне ВЛАДЕЛЬЦА -
    # слоты выбираются по owner_user_id через _owner_scope, account_id в запросе нет.
    # Без этого клиент с оплаченными слотами получал 403 и не мог подключить Avito.
    if request.url.path in ("/api/inbox/slots", "/api/inbox/accounts"):
        return user
    # BORIS_TPL_GATE: шаблоны быстрых ответов к аккаунту НЕ привязаны -
    # ключ у них владелец, account_id в запросе нет и сравнивать нечего.
    # Без этого сохранение шаблона падало с 403 у всех, включая владельца.
    if request.url.path.startswith("/api/inbox/templates"):
        return user
    # Исключение: список диалогов единого окна. Фронт присылает account_id="all" —
    # это состояние интерфейса, а не аккаунт, и сравнение с ним всегда даёт 403.
    # Отдавать безопасно: эндпоинт сам собирает выборку через _connected_account_ids(db, user),
    # то есть только по подключённым аккаунтам этого пользователя, а при конкретном
    # account_id проверяет принадлежность и отвечает «Аккаунт не найден или не подключён».
    if request.url.path == "/api/inbox/dialogs":
        return user
    # Исключение: экран «Возврат клиентов». Ни одна ручка не принимает account_id как право:
    # список аккаунтов строится внутри через _my_accounts(db, user), действия проверяют
    # принадлежность кандидата тем же списком. Клиент видит только свои аккаунты.
    if request.url.path.startswith("/api/reactivation/"):
        return user

    # Исключение: отдача баннера постинга и чат с Борисом работают на уровне пользователя,
    # account_id в запросе нет. Баннер отдаётся строго по имени файла из своей папки,
    # чат не читает данные аккаунтов — чужого эти ручки раскрыть не могут.
    if request.url.path in ("/api/posting/banner", "/api/chat", "/api/chat/parse_task"):
        return user

    # Исключение: общие справочники (города cities_50k, станции метро metro_stations)
    # лежат под account_id="global" и одинаковы для всех клиентов. Разрешаем ТОЛЬКО
    # чтение: /api/storage/save под это исключение не попадает, изоляция не слабеет.
    if (request.method == "GET"
            and request.url.path == "/api/storage/load"
            and request.query_params.get("account_id") == "global"):
        return user

    account_id = request.path_params.get("account_id") or request.query_params.get("account_id")
    if not account_id:
        try:
            body = await request.json()
            if isinstance(body, dict):
                account_id = body.get("account_id")
        except Exception:
            try:
                form = await request.form()
                account_id = form.get("account_id")
            except Exception:
                account_id = None

    # client без account_id в запросе НЕ пропускаем: иначе эндпоинт подставит
    # свой дефолт (otdushi/default = чужой аккаунт). Требуем явного указания своего.
    if not account_id:
        # BORIS_OPTIONAL_ACCOUNT_ACCESS_V2
        # Some authenticated endpoints are owner/user scoped and
        # intentionally do not receive an account_id. In that case
        # account-level authorization is not applicable here.
        # Endpoints that DO pass account_id continue through the
        # ownership / user_account_access checks below.
        return user
    # MULTI_ACCOUNT_AUTH + UNPAID_ACCOUNT_ACCESS_GATE_V1:
    # First prove tenant access, then apply the reversible base-subscription gate.
    # Do not deactivate the User record: billing/payment endpoints remain usable,
    # and a renewed paid_until automatically restores the workspace.
    _db = SessionLocal()
    try:
        _owned = _db.query(Account).filter(
            Account.account_id == account_id,
            Account.owner_user_id == user.id,
        ).first()
        _granted = bool(account_id == user.account_id or _owned is not None)
        if not _granted:
            from sqlalchemy import text as _uaa_text
            _granted = bool(_db.execute(_uaa_text(
                "SELECT 1 FROM user_account_access"
                " WHERE user_id = :u AND account_id = :a"
                " AND can_view = TRUE LIMIT 1"
            ), {"u": user.id, "a": account_id}).fetchone())
        if not _granted:
            raise HTTPException(status_code=403, detail="Нет доступа к этому аккаунту")

        _subscription = _account_subscription_access_state(_db, str(account_id))
        # Existing Campaign state remains readable after expiry, but mutations,
        # provider actions, spend, and all non-GET routes stay subscription-gated.
        _read_only_campaign = (
            request.method == "GET"
            and request.url.path.startswith("/api/campaign/")
        )
        if not _read_only_campaign and not bool(_subscription.get("allowed")):
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "subscription_expired",
                    "message": "Срок оплаченного тарифа закончился. Оплатите продление, чтобы открыть кабинет.",
                    "account_id": str(account_id),
                    "paid_until": _subscription.get("paid_until"),
                },
            )
    finally:
        _db.close()
    return user


# ==================== эндпоинты ====================

class RegisterRequest(BaseModel):
    planned_accounts: str = ""
    turnstile_token: str = ""
    ref: str = ""
    email: EmailStr
    password: str
    account_name: str = ""  # опционально: сразу создать связанный Avito-аккаунт
    attribution_session_id: str = ""
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    utm_content: str = ""
    utm_term: str = ""
    landing_path: str = ""
    first_seen_at: str = ""
    qa: bool = False


def _auth_storage_get(db, account_id: str, key: str) -> dict:
    row = db.query(Storage).filter(
        Storage.account_id == str(account_id),
        Storage.key == str(key),
    ).order_by(Storage.id.desc()).first()
    if not row or not row.value:
        return {}
    try:
        value = json.loads(row.value)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _auth_storage_put(db, account_id: str, key: str, value: dict) -> None:
    row = db.query(Storage).filter(
        Storage.account_id == str(account_id),
        Storage.key == str(key),
    ).order_by(Storage.id.desc()).first()
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if row:
        row.value = raw
    else:
        db.add(Storage(account_id=str(account_id), key=str(key), value=raw))


def _register_attribution(req: RegisterRequest) -> dict:
    out = {}
    mapping = {
        "session_id": req.attribution_session_id,
        "utm_source": req.utm_source,
        "utm_medium": req.utm_medium,
        "utm_campaign": req.utm_campaign,
        "utm_content": req.utm_content,
        "utm_term": req.utm_term,
        "landing_path": req.landing_path,
        "first_seen_at": req.first_seen_at,
        "referral_code": req.ref,
    }
    for key, value in mapping.items():
        clean = str(value or "").strip()[:240]
        if clean:
            out[key] = clean
    if req.qa:
        out["qa"] = True
    if out:
        out["captured_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return out


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_key: str = ""


class LoginOtpVerifyRequest(BaseModel):
    challenge_id: str
    code: str
    device_key: str



def _make_fallback_captcha() -> dict:
    a = secrets.randbelow(8) + 2
    b = secrets.randbelow(8) + 2
    salt = secrets.token_urlsafe(10)
    answer_hash = hashlib.sha256(f"{salt}:{a+b}".encode("utf-8")).hexdigest()
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
    token = jwt.encode({
        "typ": "boris_captcha",
        "exp": exp,
        "salt": salt,
        "answer_hash": answer_hash,
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"status": "ok", "question": f"{a} + {b} = ?", "challenge_token": token}


def _verify_fallback_captcha(raw_token: str) -> tuple[bool, str]:
    prefix = "boris-fallback:"
    if not str(raw_token or "").startswith(prefix):
        return False, "not_fallback"
    try:
        rest = str(raw_token)[len(prefix):]
        challenge_token, answer = rest.rsplit(":", 1)
        payload = jwt.decode(challenge_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("typ") != "boris_captcha":
            return False, "bad_type"
        salt = str(payload.get("salt") or "")
        expected = str(payload.get("answer_hash") or "")
        actual = hashlib.sha256(f"{salt}:{str(answer).strip()}".encode("utf-8")).hexdigest()
        return secrets.compare_digest(actual, expected), "ok"
    except Exception:
        return False, "invalid"


@router.get("/captcha-fallback")
def captcha_fallback():
    return _make_fallback_captcha()


@router.post("/register")
def register(req: RegisterRequest, request: Request):
    """Самостоятельная регистрация клиента: создаётся User (role=client) и связанный Account.
    Владельца (Кирилла) первого создаём отдельным скриптом-миграцией с ролью owner."""
    db = SessionLocal()
    try:
        # Тестовый домен QA. Сравнение ТОЧНОЕ по домену, не подстрокой:
        # "user@notborisqa.ru" сюда не попадёт.
        _qa = req.email.split("@")[-1].lower() == "borisqa.ru"

        from app.services import turnstile as _ts, mailer as _ml, verification as _vf

        _ip = request.client.host if request.client else ""
        _norm = _vf.normalize_email(req.email)

        # Капча проверяется ТОЛЬКО здесь, на backend. Основной путь — Cloudflare
        # Turnstile. Если внешний виджет не загрузился (например, из-за VPN),
        # принимаем короткую first-party challenge с того же домена BORIS.
        _captcha_token = (req.turnstile_token or "").strip()
        if _captcha_token.startswith("boris-fallback:"):
            _ok, _why = _verify_fallback_captcha(_captcha_token)
            if not _ok:
                logger.warning("register: fallback captcha invalid (%s)", _why)
                return {"status": "error", "code": "captcha_invalid",
                        "message": "Проверка не пройдена. Попробуйте ещё раз."}
        elif _ts.is_enabled():
            if not _captcha_token:
                logger.warning("register: капча не передана")
                return {"status": "error", "code": "captcha_required",
                        "message": "Подтвердите, что вы не робот."}
            _ok, _why = _ts.verify(_captcha_token, _ip)
            if not _ok:
                logger.warning("register: капча не пройдена (%s)", _why)
                return {"status": "error", "code": "captcha_invalid",
                        "message": "Проверка не пройдена. Попробуйте ещё раз."}
        else:
            logger.warning("register: TURNSTILE_SECRET_KEY не задан; разрешён только BORIS fallback captcha")
            return {"status": "error", "code": "captcha_unavailable",
                    "message": "Проверка временно недоступна. Попробуйте ещё раз."}

        # Лимиты: по IP и по адресу. Redis нет, окна считаются в auth_rate_events.
        if _vf.blocked_until(db, "reg:ip:" + _ip) or _vf.blocked_until(db, "reg:em:" + _norm):
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много попыток. Повторите позже."}
        if _vf.count_events(db, "register", ip=_ip, minutes=60) >= _vf.MAX_REGISTER_PER_IP_HOUR:
            _vf.block(db, "reg:ip:" + _ip, "register_ip_hour")
            db.commit()
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много попыток. Повторите позже."}
        if _vf.count_events(db, "register", rate_key=_norm, minutes=60) >= _vf.MAX_EMAILS_PER_EMAIL_HOUR:
            _vf.block(db, "reg:em:" + _norm, "register_email_hour")
            db.commit()
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много попыток. Повторите позже."}
        _vf.log_event(db, "register", _norm, _ip)
        db.commit()

        if len(req.password) < 6:
            return {"status": "error", "message": "Пароль должен быть минимум 6 символов"}

        existing = db.query(User).filter(User.email_normalized == _norm).first()
        if existing is None:
            existing = db.query(User).filter(User.email == req.email).first()
        if existing:
            # Существование адреса не подтверждаем: ответ по форме такой же, как успешный.
            # Владельцу адреса уходит письмо о попытке регистрации — так он узнает,
            # что аккаунт уже есть, а посторонний не узнает ничего.
            try:
                _ml.send_mail(existing.email, "БОРИС — попытка регистрации",
                              "На ваш адрес пытались зарегистрировать аккаунт в БОРИСе.\n"
                              "Аккаунт уже существует — просто войдите.\n"
                              "Если это были не вы, ничего делать не нужно.\n\nboris-ai.pro")
            except Exception as _e:
                logger.warning("register: письмо о повторной регистрации не ушло (%s)",
                               type(_e).__name__)
            logger.warning("register: повторная регистрация на существующий адрес")
            return {"status": "pending", "verification_id": "",
                    "email_masked": _vf.mask_email(req.email),
                    "message": "Мы отправили код подтверждения на указанный адрес."}

        # Генерируем account_id из email (до @) + случайный хвост, чтобы избежать коллизий
        import re, random, string
        base_slug = re.sub(r"[^a-z0-9]", "", req.email.split("@")[0].lower())[:20] or "user"
        suffix = "".join(random.choices(string.digits, k=5))
        account_id = f"{base_slug}_{suffix}"

        ref_code = (req.ref or "").strip()
        manager = db.query(User).filter(User.ref_code == ref_code).first() if ref_code else None
        if ref_code and manager is None:
            # Код указан, но менеджера с таким кодом нет. Регистрацию не прерываем
            # и пользователю о существовании кода ничего не сообщаем.
            logger.warning("register: ref_code=%r указан, но менеджер не найден (email=%s)",
                           ref_code, req.email)

        # Триал НЕ выдаётся при регистрации: он стартует в /verify-email
        # после успешного подтверждения почты и ровно один раз.
        user = User(
            email=req.email,
            password_hash=hash_password(req.password),
            role="client",
            account_id=account_id,
            is_active=True,
            subscription_expires_at=None,
            status="pending_verification",
            email_verified=False,
            email_normalized=_norm,
            planned_accounts=(req.planned_accounts or "")[:16] or None,
            referred_by=(manager.email if manager else None),
            referred_at=(datetime.datetime.utcnow() if manager else None),
        )
        try:
            db.add(user)
            db.flush()          # получаем user.id, коммита ещё нет
            account = Account(account_id=account_id,
                              name=req.account_name or req.email.split("@")[0],
                              owner_user_id=user.id)
            db.add(account)
            attribution = _register_attribution(req)
            gateway_state = {
                "stage": "REGISTERED",
                "audit_completed": False,
                "latest_audit_id": None,
                "marketing_opt_in": False,
                "registered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if attribution:
                gateway_state["first_touch"] = attribution
            _auth_storage_put(db, account_id, "viral_gateway_state", gateway_state)
            db.commit()         # единственный коммит: User, Account и attribution вместе
        except Exception:
            db.rollback()       # пользователь без аккаунта не остаётся
            raise
        db.refresh(user)

        # Код подтверждения. В базе только хеш, сам код уходит одним письмом.
        _vid, _code = _vf.issue(db, user.id, _norm, _ip)
        _vf.log_event(db, "send", _norm, _ip)
        db.commit()

        if not _qa:
            _sent, _why = _ml.send_verification_code(user.email, _code, verification_id=_vid)
            if not _sent:
                logger.warning("register: письмо с кодом не ушло (%s)", _why)
        else:
            logger.info("register: QA-регистрация, письмо не отправляется")

        # ВАЖНО: уведомление владельцу отправляется ПОСЛЕ письма с кодом.
        # При недоступном Telegram клиент всё равно получает код вовремя.
        # уведомление владельцу в Telegram о новом клиенте (не критично к регистрации)
        try:
            import os as _os
            from app.telegram_bot import send_telegram_message
            _chat = _os.environ.get("DIRECTOR_CHAT_ID")
            if _qa:
                logger.info("register: QA-регистрация %s — внешние уведомления подавлены",
                            req.email)
            if _chat and not _qa:
                send_telegram_message(_chat,
                    f"\U0001F195 <b>Новый клиент БОРИС</b>\n"
                    f"Email: {user.email}\n"
                    f"Компания: {req.account_name or '—'}\n"
                    f"Статус: ожидает подтверждения почты")
        except Exception as _e:
            print("telegram notify failed:", _e)


        token = _issue_user_session_token(db, user, request)
        return {"status": "pending", "access_token": token, "token_type": "bearer",
                "verification_id": _vid,
                "email_masked": _vf.mask_email(user.email),
                "message": "Мы отправили код подтверждения на указанный адрес.",
                "user": {"id": user.id, "email": user.email, "role": user.role,
                         "account_id": user.account_id, "status": user.status}}
    finally:
        db.close()



class CreateManagerRequest(BaseModel):
    email: str
    password: str
    ref_code: str
    commission_rate: float = 0

@router.post("/create_manager")
def create_manager(req: CreateManagerRequest, current=Depends(get_current_user)):
    """Создать менеджера. Только owner. Роль manager нельзя получить самостоятельной регистрацией."""
    if getattr(current, "role", None) != "owner":
        return {"status": "error", "message": "Только владелец может создавать менеджеров"}
    db = SessionLocal()
    try:
        if len(req.password) < 6:
            return {"status": "error", "message": "Пароль минимум 6 символов"}
        code = re.sub(r"[^a-zA-Z0-9_-]", "", req.ref_code).lower()
        if not code:
            return {"status": "error", "message": "Некорректный код"}
        if db.query(User).filter(User.email == req.email).first():
            return {"status": "error", "message": "Email уже зарегистрирован"}
        if db.query(User).filter(User.ref_code == code).first():
            return {"status": "error", "message": "Такой ref_code занят"}
        u = User(email=req.email, password_hash=hash_password(req.password),
                 role="manager", account_id=None, is_active=True,
                 ref_code=code, commission_rate=req.commission_rate)
        db.add(u); db.commit(); db.refresh(u)
        return {"status": "ok", "manager": {"id": u.id, "email": u.email,
                "ref_code": u.ref_code, "link": "https://boris-ai.pro/?ref=" + u.ref_code}}
    finally:
        db.close()


@router.post("/login")
def login(req: LoginRequest, request: Request):
    db = SessionLocal()
    try:
        from app.services import verification as _vf
        _ip = request.client.host if request.client else ""
        _norm = _vf.normalize_email(req.email)
        if _vf.blocked_until(db, "login:ip:" + _ip) or _vf.blocked_until(db, "login:em:" + _norm):
            raise HTTPException(status_code=429, detail="Слишком много попыток входа. Повторите позже.")
        user = db.query(User).filter(User.email == req.email, User.is_active == True).first()
        if not user or not verify_password(req.password, user.password_hash):
            _vf.log_event(db, "login_failed", _norm, _ip)
            email_attempts = _vf.count_events(db, "login_failed", rate_key=_norm, minutes=15)
            ip_attempts = _vf.count_events(db, "login_failed", ip=_ip, minutes=15)
            if email_attempts >= 10:
                _vf.block(db, "login:em:" + _norm, "login_email_bruteforce", minutes=15)
            if ip_attempts >= 30:
                _vf.block(db, "login:ip:" + _ip, "login_ip_bruteforce", minutes=15)
            db.commit()
            # Одинаковый ответ на "нет пользователя" и "неверный пароль" —
            # чтобы злоумышленник не мог перебирать email'ы для выяснения кто зарегистрирован
            raise HTTPException(status_code=401, detail="Неверный email или пароль")

        # PRIMARY_CLIENT_ADMIN_NEW_DEVICE_OTP_V1:
        # platform owner remains the highest role. A client who owns one or more
        # account rows is the primary admin of that customer's workspace and must
        # confirm a new browser/phone by a six-digit email code once.
        from app.services import login_security as _login_sec
        _client_admin = bool(
            getattr(user, "role", "") != "owner"
            and getattr(user, "status", "") == "active"
            and bool(getattr(user, "email_verified", False))
            and _login_sec.is_primary_client_admin(user.id)
        )
        if _client_admin and not _login_sec.is_trusted(user.id, req.device_key):
            issued = _login_sec.issue(
                user.id, user.email, req.device_key,
                request.headers.get("user-agent", ""),
            )
            if issued.get("status") == "otp_required":
                return {
                    "status": "otp_required",
                    "challenge_id": issued.get("challenge_id"),
                    "expires_in_seconds": issued.get("expires_in_seconds"),
                    "email_masked": _login_sec.mask_email(user.email),
                    "client_admin": True,
                    "message": "Мы отправили код входа на почту главного администратора.",
                }
            if issued.get("status") == "cooldown":
                return {
                    "status": "otp_required",
                    "challenge_id": issued.get("challenge_id") or "",
                    "retry_after_seconds": issued.get("retry_after_seconds"),
                    "email_masked": _login_sec.mask_email(user.email),
                    "client_admin": True,
                    "message": "Код уже отправлен. Подождите немного и используйте последнее письмо.",
                }
            raise HTTPException(status_code=503, detail="Не удалось отправить код входа на почту")

        user.last_login_at = datetime.datetime.utcnow()
        db.commit()

        # уведомление владельцу в Telegram о входе клиента (не критично ко входу)
        try:
            import os as _os
            from app.telegram_bot import send_telegram_message
            _chat = _os.environ.get("DIRECTOR_CHAT_ID")
            # Synthetic production QA uses the reserved borisqa.ru domain.
            # It must exercise the real login path without impersonating a real
            # customer in the owner's Telegram notifications.
            _email = str(user.email or "").strip().lower()
            _qa_login = _email.rsplit("@", 1)[-1] == "borisqa.ru" if "@" in _email else False
            if _qa_login:
                logger.info("login: owner Telegram notification suppressed for BORIS QA account")
            elif _chat:
                send_telegram_message(_chat,
                    f"\U0001F513 <b>Вход клиента БОРИС</b>\n"
                    f"Email: {user.email}\n"
                    f"Время: {user.last_login_at.strftime('%d.%m.%Y %H:%M')} UTC")
        except Exception as _e:
            print("telegram notify failed:", _e)

        token = _issue_user_session_token(db, user, request)
        resp = {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role,
                         "account_id": user.account_id,
                         "client_admin": bool(_client_admin),
                         "status": getattr(user, "status", "active")}}
        # pending_hint: неподтверждённому отдаём последний живой verification_id и маску,
        # иначе экран /verify после повторного входа не знает, что подтверждать
        if getattr(user, "status", "") == "pending_verification":
            try:
                from app.services import verification as _vf
                from sqlalchemy import text as _text
                _row = db.execute(_text(
                    "SELECT verification_id FROM email_verifications "
                    "WHERE user_id = :uid AND used_at IS NULL AND expires_at > :now "
                    "ORDER BY id DESC LIMIT 1"),
                    {"uid": user.id, "now": datetime.datetime.utcnow()}).first()
                resp["verification_id"] = _row[0] if _row else ""
                resp["email_masked"] = _vf.mask_email(user.email)
            except Exception as _e:
                logger.warning("login: pending_hint не собран (%s)", type(_e).__name__)
        return resp
    finally:
        db.close()


@router.post("/login/verify-code")
def login_verify_code(req: LoginOtpVerifyRequest, request: Request):
    from app.services import login_security as _login_sec
    result = _login_sec.verify(
        req.challenge_id, req.code, req.device_key,
        request.headers.get("user-agent", ""),
    )
    status_code = str(result.get("status") or "")
    if status_code != "ok":
        messages = {
            "invalid": "Неверный код",
            "invalid_code": "Неверный код",
            "invalid_device": "Код был выдан для другого устройства",
            "expired": "Срок действия кода истёк. Войдите ещё раз.",
            "used": "Этот код уже использован. Войдите ещё раз.",
            "locked": "Слишком много попыток. Войдите ещё раз.",
        }
        return {
            "status": "error",
            "code": status_code or "invalid",
            "attempts_left": result.get("attempts_left"),
            "message": messages.get(status_code, "Не удалось подтвердить вход"),
        }

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(result["user_id"]), User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=401, detail="Пользователь не найден или деактивирован")
        if getattr(user, "status", None) == "blocked":
            raise HTTPException(status_code=403, detail="Учётная запись заблокирована")
        user.last_login_at = datetime.datetime.utcnow()
        db.commit()
        token = _issue_user_session_token(db, user, request)
        return {
            "status": "ok",
            "access_token": token,
            "token_type": "bearer",
            "trusted_until": result.get("trusted_until"),
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "account_id": user.account_id,
                "client_admin": True,
                "status": getattr(user, "status", "active"),
            },
        }
    finally:
        db.close()


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Проверить свой токен и получить свои данные — используется фронтом при загрузке страницы."""
    from app.services.platform_roles import is_private_platform_owner
    from app.services import login_security as _login_sec
    return {"status": "ok", "user": {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "account_id": user.account_id,
        "platform_admin": bool(is_private_platform_owner(user)),
        "client_admin": bool(getattr(user,"role","") != "owner" and _login_sec.is_primary_client_admin(user.id)),
    }}


# ==================== подтверждение email ====================
# Все три эндпоинта работают ДО подтверждения почты, поэтому гейт их не касается:
# verify-email вообще без авторизации (по публичному verification_id),
# resend и change — через get_current_user, который не гейтится.

class VerifyEmailRequest(BaseModel):
    verification_id: str
    code: str


class ResendVerificationRequest(BaseModel):
    turnstile_token: str = ""


class ChangeVerificationEmailRequest(BaseModel):
    email: EmailStr
    turnstile_token: str = ""


def _vf_captcha_ok(_ts, token: str, ip: str):
    """Общая проверка капчи для resend/change. Возвращает None если всё хорошо,
    иначе готовый словарь ошибки."""
    if not _ts.is_enabled():
        return {"status": "error", "code": "captcha_unavailable",
                "message": "Действие временно недоступно. Попробуйте позже."}
    if not (token or "").strip():
        return {"status": "error", "code": "captcha_required",
                "message": "Подтвердите, что вы не робот."}
    ok, why = _ts.verify(token, ip)
    if not ok:
        logger.warning("verification: капча не пройдена (%s)", why)
        return {"status": "error", "code": "captcha_invalid",
                "message": "Проверка не пройдена. Попробуйте ещё раз."}
    return None


def _vf_issue_and_send(db, _vf, _ml, user, norm: str, ip: str, qa: bool):
    """Выпуск кода + письмо. Код нигде не логируется."""
    vid, code = _vf.issue(db, user.id, norm, ip)
    _vf.log_event(db, "send", norm, ip)
    db.commit()
    if not qa:
        sent, why = _ml.send_verification_code(user.email, code, verification_id=vid)
        if not sent:
            logger.warning("verification: письмо с кодом не ушло (%s)", why)
    else:
        logger.info("verification: QA-адрес, письмо не отправляется")
    return vid


@router.post("/verify-email")
def verify_email(req: VerifyEmailRequest, request: Request):
    """Проверка шестизначного кода. Успех активирует пользователя и запускает
    общий четырёхдневный триал — ровно один раз (защита через trial_started_at)."""
    from app.services import verification as _vf
    db = SessionLocal()
    try:
        row = _vf.get_active(db, (req.verification_id or "").strip())
        if row is None:
            return {"status": "error", "code": "code_invalid", "message": "Неверный код"}

        if row["used_at"] is not None:
            return {"status": "error", "code": "code_used",
                    "message": "Этот код уже использован. Запросите новый."}

        if row["expires_at"] < datetime.datetime.utcnow():
            return {"status": "error", "code": "code_expired",
                    "message": "Срок действия кода истёк. Запросите новый."}

        if row["attempts_count"] >= row["max_attempts"]:
            return {"status": "error", "code": "code_locked",
                    "message": "Слишком много попыток. Запросите новый код."}

        if not _vf.check_code((req.code or "").strip(), row["code_hash"]):
            used = _vf.bump_attempt(db, row["id"])
            left = max(0, int(row["max_attempts"]) - used)
            if left == 0:
                _vf.block(db, "verify:" + str(row["email"]), "attempts_exhausted")
            db.commit()
            logger.warning("verify-email: неверный код, попыток осталось %d", left)
            if left == 0:
                return {"status": "error", "code": "code_locked", "attempts_left": 0,
                        "message": "Слишком много попыток. Запросите новый код."}
            return {"status": "error", "code": "code_invalid", "attempts_left": left,
                    "message": "Неверный код. Осталось попыток: %d" % left}

        # Гасим код ЭТИМ вызовом. Если rowcount = 0, значит кто-то успел раньше —
        # повторное подтверждение не должно второй раз продлевать подписку.
        if not _vf.mark_used(db, row["id"]):
            db.commit()
            return {"status": "error", "code": "code_used",
                    "message": "Этот код уже использован. Запросите новый."}

        user = db.query(User).filter(User.id == row["user_id"]).first()
        if user is None:
            db.commit()
            return {"status": "error", "code": "code_invalid", "message": "Неверный код"}

        now = datetime.datetime.utcnow()
        user.email_verified = True
        user.email_verified_at = now
        user.status = "active"
        if str(row["email"]):
            user.email_normalized = str(row["email"])

        # Общий четырёхдневный триал — только здесь и только один раз.
        trial_started_now = user.trial_started_at is None
        if trial_started_now:
            user.trial_started_at = now
            user.subscription_expires_at = now + datetime.timedelta(days=4)
            logger.warning("verify-email: триал выдан пользователю id=%s", user.id)

            # AUDIT_TRIAL_TRUTH_V1: this is the real product-side trial start.
            # Record it here, not on the marketing CTA. Keep the current audit
            # stage intact so AUDIT_STARTED/FULL_AUDIT_COMPLETED can continue
            # independently after email verification.
            account_id = str(getattr(user, "account_id", "") or "").strip()
            if account_id:
                gateway_state = _auth_storage_get(db, account_id, "viral_gateway_state")
                first_touch = dict(gateway_state.get("first_touch") or {})
                gateway_state["trial_started"] = True
                gateway_state["trial_started_at"] = now.replace(tzinfo=datetime.timezone.utc).isoformat()
                gateway_state["updated_at"] = now.replace(tzinfo=datetime.timezone.utc).isoformat()
                _auth_storage_put(db, account_id, "viral_gateway_state", gateway_state)

                event_id = "ve_" + secrets.token_hex(16)
                event = {
                    "event_id": event_id,
                    "event": "trial_started",
                    "stage": "TRIAL_STARTED",
                    "source": "verify_email",
                    "account_id": account_id,
                    "session_id": first_touch.get("session_id"),
                    "utm_source": first_touch.get("utm_source"),
                    "utm_medium": first_touch.get("utm_medium"),
                    "utm_campaign": first_touch.get("utm_campaign"),
                    "utm_content": first_touch.get("utm_content"),
                    "utm_term": first_touch.get("utm_term"),
                    "landing_path": first_touch.get("landing_path"),
                    "first_seen_at": first_touch.get("first_seen_at"),
                    "referral_code": first_touch.get("referral_code"),
                    "qa": bool(first_touch.get("qa")),
                    "created_at": now.replace(tzinfo=datetime.timezone.utc).isoformat(),
                }
                db.add(Storage(account_id="viral_event", key=f"event:{event_id}",
                               value=json.dumps(event, ensure_ascii=False, separators=(",", ":"))))
                stage_event_id = "ve_" + secrets.token_hex(16)
                db.add(Storage(account_id="viral_event", key=f"event:{stage_event_id}",
                               value=json.dumps({
                                   **event,
                                   "event_id": stage_event_id,
                                   "event": "gateway_stage",
                               }, ensure_ascii=False, separators=(",", ":"))))

        db.commit()
        db.refresh(user)

        token = _issue_user_session_token(db, user, request)
        return {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role,
                         "account_id": user.account_id, "status": user.status}}
    finally:
        db.close()


@router.post("/resend-verification")
def resend_verification(req: ResendVerificationRequest, request: Request,
                        current: User = Depends(get_current_user)):
    """Повторная отправка кода. Не чаще одного раза в 60 секунд,
    не больше 5 писем на адрес в час и 10 писем с одного IP в час."""
    from app.services import turnstile as _ts, mailer as _ml, verification as _vf
    db = SessionLocal()
    try:
        ip = request.client.host if request.client else ""
        user = db.query(User).filter(User.id == current.id).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        if user.status != "pending_verification":
            return {"status": "ok", "message": "Почта уже подтверждена"}

        err = _vf_captcha_ok(_ts, req.turnstile_token, ip)
        if err:
            return err

        norm = user.email_normalized or _vf.normalize_email(user.email)
        qa = str(user.email).split("@")[-1].lower() == "borisqa.ru"

        if _vf.blocked_until(db, "send:" + norm):
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много запросов. Повторите позже."}

        since = _vf.seconds_since_last(db, "send", norm)
        if since is not None and since < _vf.RESEND_COOLDOWN_SEC:
            wait = int(_vf.RESEND_COOLDOWN_SEC - since) + 1
            return {"status": "error", "code": "cooldown", "retry_after": wait,
                    "message": "Новый код можно запросить через %d с" % wait}

        if _vf.count_events(db, "send", rate_key=norm, minutes=60) >= _vf.MAX_EMAILS_PER_EMAIL_HOUR:
            _vf.block(db, "send:" + norm, "emails_per_hour")
            db.commit()
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много писем на этот адрес. Повторите через час."}

        if _vf.count_events(db, "send", ip=ip, minutes=60) >= _vf.MAX_EMAILS_PER_IP_HOUR:
            db.commit()
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много запросов. Повторите позже."}

        vid = _vf_issue_and_send(db, _vf, _ml, user, norm, ip, qa)
        return {"status": "ok", "verification_id": vid,
                "email_masked": _vf.mask_email(user.email),
                "message": "Новый код отправлен"}
    finally:
        db.close()


@router.post("/change-verification-email")
def change_verification_email(req: ChangeVerificationEmailRequest, request: Request,
                              current: User = Depends(get_current_user)):
    """Смена ошибочно введённого адреса до подтверждения.
    Пользователь остаётся pending_verification, старый код гасится."""
    from app.services import turnstile as _ts, mailer as _ml, verification as _vf
    db = SessionLocal()
    try:
        ip = request.client.host if request.client else ""
        user = db.query(User).filter(User.id == current.id).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        if user.status != "pending_verification":
            return {"status": "error", "code": "already_verified",
                    "message": "Почта уже подтверждена, адрес так не меняется"}

        err = _vf_captcha_ok(_ts, req.turnstile_token, ip)
        if err:
            return err

        norm = _vf.normalize_email(req.email)
        if not norm:
            return {"status": "error", "code": "bad_email", "message": "Некорректный адрес"}

        if _vf.blocked_until(db, "send:" + norm):
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много запросов. Повторите позже."}

        if _vf.count_events(db, "send", ip=ip, minutes=60) >= _vf.MAX_EMAILS_PER_IP_HOUR:
            return {"status": "error", "code": "rate_limited",
                    "message": "Слишком много запросов. Повторите позже."}

        taken = db.query(User).filter(User.email_normalized == norm,
                                      User.id != user.id).first()
        if taken is not None:
            # Существование чужого адреса не подтверждаем: ответ той же формы, что успех.
            logger.warning("change-verification-email: адрес занят, ответ обезличен")
            return {"status": "ok", "verification_id": "",
                    "email_masked": _vf.mask_email(req.email),
                    "message": "Код отправлен на новый адрес"}

        user.email = str(req.email)
        user.email_normalized = norm
        _vf.invalidate_active(db, user.id)
        db.commit()

        qa = norm.split("@")[-1] == "borisqa.ru"
        vid = _vf_issue_and_send(db, _vf, _ml, user, norm, ip, qa)
        return {"status": "ok", "verification_id": vid,
                "email_masked": _vf.mask_email(user.email),
                "message": "Код отправлен на новый адрес"}
    finally:
        db.close()


class RevokeSessionRequest(BaseModel):
    session_id: str


@router.get("/sessions")
def auth_sessions(user: User = Depends(get_current_user)):
    """List this user's server-side sessions; never returns token material or IP."""
    from app.services.auth_sessions import list_sessions
    db = SessionLocal()
    try:
        current_sid = str(getattr(user, "_auth_session_id", "") or "")
        items = list_sessions(db, int(user.id))
        for item in items:
            item["current"] = bool(current_sid and item["session_id"] == current_sid)
        return {"status": "ok", "sessions": items, "legacy_current": not bool(current_sid)}
    finally:
        db.close()


@router.post("/sessions/revoke")
def revoke_auth_session(body: RevokeSessionRequest, request: Request, user: User = Depends(get_current_user)):
    from app.services.auth_sessions import revoke_session
    sid = str(body.session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="Укажите сеанс")
    db = SessionLocal()
    try:
        changed = revoke_session(db, int(user.id), sid)
        if not changed:
            raise HTTPException(status_code=404, detail="Сеанс не найден")
        from app.services.messenger_security_audit import append_security_audit
        append_security_audit(db, action="session_revoke", user_id=int(user.id), entity_type="session", entity_id=sid, session_id=str(getattr(user, "_auth_session_id", "") or "") or None, request_id=request.headers.get("x-boris-trace-id"))
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(user_id=int(user.id), account_id=None, action="Завершил сеанс", object_kind="session", object_name="other_device", after="revoked")
        return {"status": "ok", "revoked": sid}
    finally:
        db.close()


@router.post("/sessions/revoke-all")
def revoke_all_auth_sessions(request: Request, user: User = Depends(get_current_user)):
    """Invalidate every previous token and issue one fresh current session."""
    from app.services.auth_sessions import issue_session, revoke_all_and_advance_epoch
    db = SessionLocal()
    try:
        new_epoch = revoke_all_and_advance_epoch(db, int(user.id), "user_revoke_all")
        sid, expires = issue_session(
            db,
            int(user.id),
            new_epoch,
            user_agent=request.headers.get("user-agent", ""),
            ip=(request.client.host if request.client else ""),
        )
        from app.services.messenger_security_audit import append_security_audit
        append_security_audit(db, action="session_revoke_all", user_id=int(user.id), entity_type="session", entity_id=sid, session_id=sid, request_id=request.headers.get("x-boris-trace-id"), details={"new_security_epoch": new_epoch})
        db.commit()
        token = create_access_token(
            int(user.id),
            session_id=sid,
            security_epoch=new_epoch,
            expires_at=expires,
        )
        from app.services.messenger_security import audit_security_event
        audit_security_event(user_id=int(user.id), account_id=None, action="Завершил все остальные сеансы", object_kind="session", object_name="all_other_devices", after="revoked")
        return {"status": "ok", "access_token": token, "token_type": "bearer"}
    finally:
        db.close()


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user)):
    """Server-side logout. Legacy tokens are invalidated through the epoch."""
    from app.services.auth_sessions import revoke_all_and_advance_epoch, revoke_session
    sid = str(getattr(user, "_auth_session_id", "") or "")
    db = SessionLocal()
    try:
        if sid:
            revoke_session(db, int(user.id), sid, "logout")
        else:
            revoke_all_and_advance_epoch(db, int(user.id), "legacy_logout")
        from app.services.messenger_security_audit import append_security_audit
        append_security_audit(db, action="logout", user_id=int(user.id), entity_type="session", entity_id=sid or "legacy", session_id=sid or None, request_id=request.headers.get("x-boris-trace-id"))
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(user_id=int(user.id), account_id=None, action="Вышел из BORIS", object_kind="session", object_name="current_device", after="logged_out")
        return {"status": "ok"}
    finally:
        db.close()
