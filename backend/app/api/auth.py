"""SaaS-авторизация BORIS: регистрация, логин, JWT-токены, зависимость get_current_user
для защиты эндпоинтов. Роли: owner (владелец, видит всё) | client (свой account_id).
"""
import os
import datetime
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
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Секрет для подписи JWT — берём из .env, если нет — генерируем предупреждение.
# В продакшене ОБЯЗАТЕЛЬНО задать в .env, иначе токены будут инвалидироваться при каждом рестарте.
JWT_SECRET = os.environ.get("JWT_SECRET", "CHANGE_ME_IN_ENV_TEMPORARY_DEV_SECRET_" + os.urandom(8).hex())
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30  # токен живёт 30 дней — обычная практика для SaaS-веба

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def create_access_token(user_id: int) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> User:
    """FastAPI-зависимость: достаёт пользователя из JWT-токена в заголовке Authorization.
    Используется на защищённых эндпоинтах: def endpoint(user: User = Depends(get_current_user))."""
    if not token:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Некорректный или просроченный токен")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=401, detail="Пользователь не найден или деактивирован")
        return user
    finally:
        db.close()


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Более строгая зависимость: только для эндпоинтов уровня владельца SaaS."""
    if user.role != "owner":
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


async def check_account_access(request: Request, user=Depends(get_current_user_or_internal)):
    """Единая зависимость изоляции мультитенантности - вешается на роутеры целиком (см. main.py),
    а не копипастится в каждый эндпоинт. user is None (internal/localhost) и role="owner" -
    пропускаем без проверки. Для client достаём account_id из запроса (path -> query -> JSON-тело
    -> form-тело, в этом порядке) и сравниваем с user.account_id - при несовпадении 403. Если в
    запросе вообще нет account_id - нечего изолировать, пропускаем."""
    if user is None or user.role == "owner":
        return user

    # Исключение: /api/accounts/list сам фильтрует по user.account_id и физически не может
    # отдать чужой аккаунт, поэтому не требует account_id в запросе. Без этого клиент без
    # аккаунта получал 403 вместо пустого списка — из-за чего у новичка не открывался визард.
    if request.url.path == "/api/accounts/list":
        return user

    # Исключение: кабинет агентства работает на уровне ВЛАДЕЛЬЦА, а не отдельного аккаунта —
    # оба эндпоинта сами фильтруют по owner_user_id и чужого не отдадут.
    if request.url.path in ("/api/accounts/agency_overview", "/api/accounts/billing_preview", "/api/accounts/add_account"):
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
        raise HTTPException(status_code=403, detail="Не указан account_id")
    if account_id != user.account_id:
        # Клиент может владеть несколькими аккаунтами (два Avito у одного бизнеса).
        # Сравнения с одним полем user.account_id недостаточно — проверяем владельца
        # тем же паттерном, что уже работает в accounts.py.
        _db = SessionLocal()
        try:
            _owned = _db.query(Account).filter(
                Account.account_id == account_id,
                Account.owner_user_id == user.id).first()
        finally:
            _db.close()
        if _owned is None:
            raise HTTPException(status_code=403, detail="Нет доступа к этому аккаунту")
    return user


# ==================== эндпоинты ====================

class RegisterRequest(BaseModel):
    planned_accounts: str = ""
    turnstile_token: str = ""
    ref: str = ""
    email: EmailStr
    password: str
    account_name: str = ""  # опционально: сразу создать связанный Avito-аккаунт


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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

        # Капча проверяется ТОЛЬКО здесь, на backend. Токен с фронта доверия не имеет.
        if _ts.is_enabled():
            if not (req.turnstile_token or "").strip():
                logger.warning("register: капча не передана")
                return {"status": "error", "code": "captcha_required",
                        "message": "Подтвердите, что вы не робот."}
            _ok, _why = _ts.verify(req.turnstile_token, _ip)
            if not _ok:
                logger.warning("register: капча не пройдена (%s)", _why)
                return {"status": "error", "code": "captcha_invalid",
                        "message": "Проверка не пройдена. Попробуйте ещё раз."}
        else:
            logger.warning("register: TURNSTILE_SECRET_KEY не задан, регистрация закрыта")
            return {"status": "error", "code": "captcha_unavailable",
                    "message": "Регистрация временно недоступна. Попробуйте позже."}

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
            db.commit()         # единственный коммит: User и Account вместе
        except Exception:
            db.rollback()       # пользователь без аккаунта не остаётся
            raise
        db.refresh(user)

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

        # Код подтверждения. В базе только хеш, сам код уходит одним письмом.
        _vid, _code = _vf.issue(db, user.id, _norm, _ip)
        _vf.log_event(db, "send", _norm, _ip)
        db.commit()

        if not _qa:
            _sent, _why = _ml.send_verification_code(user.email, _code)
            if not _sent:
                logger.warning("register: письмо с кодом не ушло (%s)", _why)
        else:
            logger.info("register: QA-регистрация, письмо не отправляется")

        token = create_access_token(user.id)
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
def login(req: LoginRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == req.email, User.is_active == True).first()
        if not user or not verify_password(req.password, user.password_hash):
            # Одинаковый ответ на "нет пользователя" и "неверный пароль" —
            # чтобы злоумышленник не мог перебирать email'ы для выяснения кто зарегистрирован
            raise HTTPException(status_code=401, detail="Неверный email или пароль")

        user.last_login_at = datetime.datetime.utcnow()
        db.commit()

        # уведомление владельцу в Telegram о входе клиента (не критично ко входу)
        try:
            import os as _os
            from app.telegram_bot import send_telegram_message
            _chat = _os.environ.get("DIRECTOR_CHAT_ID")
            if _chat:
                send_telegram_message(_chat,
                    f"\U0001F513 <b>Вход клиента БОРИС</b>\n"
                    f"Email: {user.email}\n"
                    f"Время: {user.last_login_at.strftime('%d.%m.%Y %H:%M')} UTC")
        except Exception as _e:
            print("telegram notify failed:", _e)

        token = create_access_token(user.id)
        resp = {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role,
                         "account_id": user.account_id,
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


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Проверить свой токен и получить свои данные — используется фронтом при загрузке страницы."""
    return {"status": "ok", "user": {"id": user.id, "email": user.email, "role": user.role, "account_id": user.account_id}}


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
        sent, why = _ml.send_verification_code(user.email, code)
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
        if user.trial_started_at is None:
            user.trial_started_at = now
            user.subscription_expires_at = now + datetime.timedelta(days=4)
            logger.warning("verify-email: триал выдан пользователю id=%s", user.id)

        db.commit()
        db.refresh(user)

        token = create_access_token(user.id)
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
