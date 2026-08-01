# -*- coding: utf-8 -*-
"""
Патч app/api/auth.py: подтверждение email.
Все якоря сверены с реальным файлом. Если хоть один не найден или найден
не один раз — падаем ДО записи и ничего не трогаем.
"""
import io, os, re, sys, shutil, time

PATH = sys.argv[1] if len(sys.argv) > 1 else "/root/BORIS/backend/app/api/auth.py"
s = io.open(PATH, encoding="utf-8").read()

MARKER = "email_not_verified"
if MARKER in s:
    print("ПАТЧ УЖЕ ПРИМЕНЁН (найден маркер email_not_verified) — выхожу, файл не тронут")
    sys.exit(0)

def one(sub, name):
    n = s.count(sub)
    assert n == 1, "якорь %s найден %d раз, ожидалась 1" % (name, n)

def rep(old, new, name):
    global s
    one(old, name)
    s = s.replace(old, new, 1)
    print("  ok:", name)


REG_START = '@router.post("/register")'
REG_END = 'class CreateManagerRequest(BaseModel):'

def rep_reg(old, new, name):
    """Замена ТОЛЬКО внутри тела register.
    Якоря вроде 'token = create_access_token(user.id)' и блока telegram-уведомления
    встречаются ещё и в login — глобальная замена там сломала бы вход."""
    global s
    i = s.index(REG_START)
    j = s.index(REG_END, i)
    seg = s[i:j]
    n = seg.count(old)
    assert n == 1, "якорь %s внутри register найден %d раз, ожидалась 1" % (name, n)
    s = s[:i] + seg.replace(old, new, 1) + s[j:]
    print("  ok (в register):", name)

# ---------- 1. Гейт в get_current_user_or_internal ----------
rep(
    "    return get_current_user(token)\n",
    '''    user = get_current_user(token)
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
''',
    "гейт в get_current_user_or_internal")

# ---------- 2. Поле капчи в RegisterRequest ----------
rep(
    'class RegisterRequest(BaseModel):\n    planned_accounts: str = ""\n',
    'class RegisterRequest(BaseModel):\n    planned_accounts: str = ""\n    turnstile_token: str = ""\n',
    "turnstile_token в RegisterRequest")

# ---------- 3. Сигнатура register: нужен IP ----------
rep(
    "def register(req: RegisterRequest):\n",
    "def register(req: RegisterRequest, request: Request):\n",
    "сигнатура register")

# ---------- 4. Капча + лимиты + нормализация ----------
rep_reg(
    '        _qa = req.email.split("@")[-1].lower() == "borisqa.ru"\n',
    '''        _qa = req.email.split("@")[-1].lower() == "borisqa.ru"

        from app.services import turnstile as _ts, mailer as _ml, verification as _vf

        _ip = request.client.host if request.client else ""
        _norm = _vf.normalize_email(req.email)

        # Капча проверяется ТОЛЬКО здесь, на backend. Токен с фронта доверия не имеет.
        if _ts.is_enabled():
            _ok, _why = _ts.verify(req.turnstile_token, _ip)
            if not _ok:
                logger.warning("register: капча не пройдена (%s)", _why)
                return {"status": "error", "code": "captcha_failed",
                        "message": "Не удалось подтвердить, что вы не робот. Обновите страницу."}
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
''',
    "капча, лимиты, нормализация")

# ---------- 5. Уникальность по нормализованному адресу ----------
rep_reg(
    '''        existing = db.query(User).filter(User.email == req.email).first()
        if existing:
            return {"status": "error", "message": "Этот email уже зарегистрирован"}
''',
    '''        existing = db.query(User).filter(User.email_normalized == _norm).first()
        if existing is None:
            existing = db.query(User).filter(User.email == req.email).first()
        if existing:
            # Существование адреса не подтверждаем: ответ по форме такой же, как успешный.
            # Владельцу адреса уходит письмо о попытке регистрации — так он узнает,
            # что аккаунт уже есть, а посторонний не узнает ничего.
            try:
                _ml.send_mail(existing.email, "БОРИС — попытка регистрации",
                              "На ваш адрес пытались зарегистрировать аккаунт в БОРИСе.\\n"
                              "Аккаунт уже существует — просто войдите.\\n"
                              "Если это были не вы, ничего делать не нужно.\\n\\nboris-ai.pro")
            except Exception as _e:
                logger.warning("register: письмо о повторной регистрации не ушло (%s)",
                               type(_e).__name__)
            logger.warning("register: повторная регистрация на существующий адрес")
            return {"status": "pending", "verification_id": "",
                    "email_masked": _vf.mask_email(req.email),
                    "message": "Мы отправили код подтверждения на указанный адрес."}
''',
    "уникальность по email_normalized")

# ---------- 6. Триал больше НЕ выдаётся при регистрации ----------
rep_reg(
    "        trial_expires = datetime.datetime.utcnow() + datetime.timedelta(days=4)\n",
    "        # Триал НЕ выдаётся при регистрации: он стартует в /verify-email\n"
    "        # после успешного подтверждения почты и ровно один раз.\n",
    "удаление trial_expires")

rep_reg(
    "            subscription_expires_at=trial_expires,\n",
    "            subscription_expires_at=None,\n"
    "            status=\"pending_verification\",\n"
    "            email_verified=False,\n"
    "            email_normalized=_norm,\n",
    "pending_verification в конструкторе User")

# ---------- 7. Телеграм: триала ещё нет ----------
rep_reg(
    r'''                _exp = trial_expires.strftime("%d.%m.%Y")
                send_telegram_message(_chat,
                    f"\U0001F195 <b>Новый клиент БОРИС</b>\n"
                    f"Email: {user.email}\n"
                    f"Компания: {req.account_name or '—'}\n"
                    f"Триал до: {_exp}")
''',
    r'''                send_telegram_message(_chat,
                    f"\U0001F195 <b>Новый клиент БОРИС</b>\n"
                    f"Email: {user.email}\n"
                    f"Компания: {req.account_name or '—'}\n"
                    f"Статус: ожидает подтверждения почты")
''',
    "телеграм без триала")

# ---------- 8. Ответ register: выпуск кода и письмо ----------
rep_reg(
    '''        token = create_access_token(user.id)
        return {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role, "account_id": user.account_id}}
''',
    '''        # Код подтверждения. В базе только хеш, сам код уходит одним письмом.
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
''',
    "выпуск кода в ответе register")

# ---------- 9. Три новых эндпоинта в конец файла ----------
ENDPOINTS = '\n\n# ==================== подтверждение email ====================\n# Все три эндпоинта работают ДО подтверждения почты, поэтому гейт их не касается:\n# verify-email вообще без авторизации (по публичному verification_id),\n# resend и change — через get_current_user, который не гейтится.\n\nclass VerifyEmailRequest(BaseModel):\n    verification_id: str\n    code: str\n\n\nclass ResendVerificationRequest(BaseModel):\n    turnstile_token: str = ""\n\n\nclass ChangeVerificationEmailRequest(BaseModel):\n    email: EmailStr\n    turnstile_token: str = ""\n\n\ndef _vf_captcha_ok(_ts, token: str, ip: str):\n    """Общая проверка капчи для resend/change. Возвращает None если всё хорошо,\n    иначе готовый словарь ошибки."""\n    if not _ts.is_enabled():\n        return {"status": "error", "code": "captcha_unavailable",\n                "message": "Действие временно недоступно. Попробуйте позже."}\n    ok, why = _ts.verify(token, ip)\n    if not ok:\n        logger.warning("verification: капча не пройдена (%s)", why)\n        return {"status": "error", "code": "captcha_failed",\n                "message": "Не удалось подтвердить, что вы не робот. Обновите страницу."}\n    return None\n\n\ndef _vf_issue_and_send(db, _vf, _ml, user, norm: str, ip: str, qa: bool):\n    """Выпуск кода + письмо. Код нигде не логируется."""\n    vid, code = _vf.issue(db, user.id, norm, ip)\n    _vf.log_event(db, "send", norm, ip)\n    db.commit()\n    if not qa:\n        sent, why = _ml.send_verification_code(user.email, code)\n        if not sent:\n            logger.warning("verification: письмо с кодом не ушло (%s)", why)\n    else:\n        logger.info("verification: QA-адрес, письмо не отправляется")\n    return vid\n\n\n@router.post("/verify-email")\ndef verify_email(req: VerifyEmailRequest, request: Request):\n    """Проверка шестизначного кода. Успех активирует пользователя и запускает\n    общий четырёхдневный триал — ровно один раз (защита через trial_started_at)."""\n    from app.services import verification as _vf\n    db = SessionLocal()\n    try:\n        row = _vf.get_active(db, (req.verification_id or "").strip())\n        if row is None:\n            return {"status": "error", "code": "code_invalid", "message": "Неверный код"}\n\n        if row["used_at"] is not None:\n            return {"status": "error", "code": "code_used",\n                    "message": "Этот код уже использован. Запросите новый."}\n\n        if row["expires_at"] < datetime.datetime.utcnow():\n            return {"status": "error", "code": "code_expired",\n                    "message": "Срок действия кода истёк. Запросите новый."}\n\n        if row["attempts_count"] >= row["max_attempts"]:\n            return {"status": "error", "code": "code_locked",\n                    "message": "Слишком много попыток. Запросите новый код."}\n\n        if not _vf.check_code((req.code or "").strip(), row["code_hash"]):\n            used = _vf.bump_attempt(db, row["id"])\n            left = max(0, int(row["max_attempts"]) - used)\n            if left == 0:\n                _vf.block(db, "verify:" + str(row["email"]), "attempts_exhausted")\n            db.commit()\n            logger.warning("verify-email: неверный код, попыток осталось %d", left)\n            if left == 0:\n                return {"status": "error", "code": "code_locked", "attempts_left": 0,\n                        "message": "Слишком много попыток. Запросите новый код."}\n            return {"status": "error", "code": "code_invalid", "attempts_left": left,\n                    "message": "Неверный код. Осталось попыток: %d" % left}\n\n        # Гасим код ЭТИМ вызовом. Если rowcount = 0, значит кто-то успел раньше —\n        # повторное подтверждение не должно второй раз продлевать подписку.\n        if not _vf.mark_used(db, row["id"]):\n            db.commit()\n            return {"status": "error", "code": "code_used",\n                    "message": "Этот код уже использован. Запросите новый."}\n\n        user = db.query(User).filter(User.id == row["user_id"]).first()\n        if user is None:\n            db.commit()\n            return {"status": "error", "code": "code_invalid", "message": "Неверный код"}\n\n        now = datetime.datetime.utcnow()\n        user.email_verified = True\n        user.email_verified_at = now\n        user.status = "active"\n        if str(row["email"]):\n            user.email_normalized = str(row["email"])\n\n        # Общий четырёхдневный триал — только здесь и только один раз.\n        if user.trial_started_at is None:\n            user.trial_started_at = now\n            user.subscription_expires_at = now + datetime.timedelta(days=4)\n            logger.warning("verify-email: триал выдан пользователю id=%s", user.id)\n\n        db.commit()\n        db.refresh(user)\n\n        token = create_access_token(user.id)\n        return {"status": "ok", "access_token": token, "token_type": "bearer",\n                "user": {"id": user.id, "email": user.email, "role": user.role,\n                         "account_id": user.account_id, "status": user.status}}\n    finally:\n        db.close()\n\n\n@router.post("/resend-verification")\ndef resend_verification(req: ResendVerificationRequest, request: Request,\n                        current: User = Depends(get_current_user)):\n    """Повторная отправка кода. Не чаще одного раза в 60 секунд,\n    не больше 5 писем на адрес в час и 10 писем с одного IP в час."""\n    from app.services import turnstile as _ts, mailer as _ml, verification as _vf\n    db = SessionLocal()\n    try:\n        ip = request.client.host if request.client else ""\n        user = db.query(User).filter(User.id == current.id).first()\n        if user is None:\n            raise HTTPException(status_code=401, detail="Пользователь не найден")\n        if user.status != "pending_verification":\n            return {"status": "ok", "message": "Почта уже подтверждена"}\n\n        err = _vf_captcha_ok(_ts, req.turnstile_token, ip)\n        if err:\n            return err\n\n        norm = user.email_normalized or _vf.normalize_email(user.email)\n        qa = str(user.email).split("@")[-1].lower() == "borisqa.ru"\n\n        if _vf.blocked_until(db, "send:" + norm):\n            return {"status": "error", "code": "rate_limited",\n                    "message": "Слишком много запросов. Повторите позже."}\n\n        since = _vf.seconds_since_last(db, "send", norm)\n        if since is not None and since < _vf.RESEND_COOLDOWN_SEC:\n            wait = int(_vf.RESEND_COOLDOWN_SEC - since) + 1\n            return {"status": "error", "code": "cooldown", "retry_after": wait,\n                    "message": "Новый код можно запросить через %d с" % wait}\n\n        if _vf.count_events(db, "send", rate_key=norm, minutes=60) >= _vf.MAX_EMAILS_PER_EMAIL_HOUR:\n            _vf.block(db, "send:" + norm, "emails_per_hour")\n            db.commit()\n            return {"status": "error", "code": "rate_limited",\n                    "message": "Слишком много писем на этот адрес. Повторите через час."}\n\n        if _vf.count_events(db, "send", ip=ip, minutes=60) >= _vf.MAX_EMAILS_PER_IP_HOUR:\n            db.commit()\n            return {"status": "error", "code": "rate_limited",\n                    "message": "Слишком много запросов. Повторите позже."}\n\n        vid = _vf_issue_and_send(db, _vf, _ml, user, norm, ip, qa)\n        return {"status": "ok", "verification_id": vid,\n                "email_masked": _vf.mask_email(user.email),\n                "message": "Новый код отправлен"}\n    finally:\n        db.close()\n\n\n@router.post("/change-verification-email")\ndef change_verification_email(req: ChangeVerificationEmailRequest, request: Request,\n                              current: User = Depends(get_current_user)):\n    """Смена ошибочно введённого адреса до подтверждения.\n    Пользователь остаётся pending_verification, старый код гасится."""\n    from app.services import turnstile as _ts, mailer as _ml, verification as _vf\n    db = SessionLocal()\n    try:\n        ip = request.client.host if request.client else ""\n        user = db.query(User).filter(User.id == current.id).first()\n        if user is None:\n            raise HTTPException(status_code=401, detail="Пользователь не найден")\n        if user.status != "pending_verification":\n            return {"status": "error", "code": "already_verified",\n                    "message": "Почта уже подтверждена, адрес так не меняется"}\n\n        err = _vf_captcha_ok(_ts, req.turnstile_token, ip)\n        if err:\n            return err\n\n        norm = _vf.normalize_email(req.email)\n        if not norm:\n            return {"status": "error", "code": "bad_email", "message": "Некорректный адрес"}\n\n        if _vf.blocked_until(db, "send:" + norm):\n            return {"status": "error", "code": "rate_limited",\n                    "message": "Слишком много запросов. Повторите позже."}\n\n        if _vf.count_events(db, "send", ip=ip, minutes=60) >= _vf.MAX_EMAILS_PER_IP_HOUR:\n            return {"status": "error", "code": "rate_limited",\n                    "message": "Слишком много запросов. Повторите позже."}\n\n        taken = db.query(User).filter(User.email_normalized == norm,\n                                      User.id != user.id).first()\n        if taken is not None:\n            # Существование чужого адреса не подтверждаем: ответ той же формы, что успех.\n            logger.warning("change-verification-email: адрес занят, ответ обезличен")\n            return {"status": "ok", "verification_id": "",\n                    "email_masked": _vf.mask_email(req.email),\n                    "message": "Код отправлен на новый адрес"}\n\n        user.email = str(req.email)\n        user.email_normalized = norm\n        _vf.invalidate_active(db, user.id)\n        db.commit()\n\n        qa = norm.split("@")[-1] == "borisqa.ru"\n        vid = _vf_issue_and_send(db, _vf, _ml, user, norm, ip, qa)\n        return {"status": "ok", "verification_id": vid,\n                "email_masked": _vf.mask_email(user.email),\n                "message": "Код отправлен на новый адрес"}\n    finally:\n        db.close()\n'
assert '@router.post("/verify-email")' not in s, "эндпоинты уже есть"
s = s.rstrip() + "\n" + ENDPOINTS
print("  ok: три новых эндпоинта")

io.open(PATH + ".before_emailverify_%d" % int(time.time()), "w", encoding="utf-8").write(
    io.open(PATH, encoding="utf-8").read())
io.open(PATH, "w", encoding="utf-8").write(s)
print("ЗАПИСАНО:", PATH)
