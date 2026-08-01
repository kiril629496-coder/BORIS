# -*- coding: utf-8 -*-
"""Донастройка auth.py: машинные коды капчи + status в ответе login."""
import io, sys, time

PATH = sys.argv[1] if len(sys.argv) > 1 else "/root/BORIS/backend/app/api/auth.py"
s = io.open(PATH, encoding="utf-8").read()

if "captcha_required" in s:
    print("УЖЕ ПРИМЕНЁН — выхожу, файл не тронут")
    sys.exit(0)

def rep(old, new, name):
    global s
    n = s.count(old)
    assert n == 1, "якорь %s найден %d раз, ожидалась 1" % (name, n)
    s = s.replace(old, new)
    print("  ok:", name)

rep('''        if _ts.is_enabled():
            _ok, _why = _ts.verify(req.turnstile_token, _ip)
            if not _ok:
                logger.warning("register: капча не пройдена (%s)", _why)
                return {"status": "error", "code": "captcha_failed",
                        "message": "Не удалось подтвердить, что вы не робот. Обновите страницу."}
''',
'''        if _ts.is_enabled():
            if not (req.turnstile_token or "").strip():
                logger.warning("register: капча не передана")
                return {"status": "error", "code": "captcha_required",
                        "message": "Подтвердите, что вы не робот."}
            _ok, _why = _ts.verify(req.turnstile_token, _ip)
            if not _ok:
                logger.warning("register: капча не пройдена (%s)", _why)
                return {"status": "error", "code": "captcha_invalid",
                        "message": "Проверка не пройдена. Попробуйте ещё раз."}
''', "коды капчи в register")

rep('''    ok, why = _ts.verify(token, ip)
    if not ok:
        logger.warning("verification: капча не пройдена (%s)", why)
        return {"status": "error", "code": "captcha_failed",
                "message": "Не удалось подтвердить, что вы не робот. Обновите страницу."}
''',
'''    if not (token or "").strip():
        return {"status": "error", "code": "captcha_required",
                "message": "Подтвердите, что вы не робот."}
    ok, why = _ts.verify(token, ip)
    if not ok:
        logger.warning("verification: капча не пройдена (%s)", why)
        return {"status": "error", "code": "captcha_invalid",
                "message": "Проверка не пройдена. Попробуйте ещё раз."}
''', "коды капчи в resend/change")

rep('''        token = create_access_token(user.id)
        return {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role, "account_id": user.account_id}}
''',
'''        token = create_access_token(user.id)
        return {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role,
                         "account_id": user.account_id,
                         "status": getattr(user, "status", "active")}}
''', "status в ответе login")

io.open(PATH + ".before_codes_%d" % int(time.time()), "w", encoding="utf-8").write(
    io.open(PATH, encoding="utf-8").read())
io.open(PATH, "w", encoding="utf-8").write(s)
print("ЗАПИСАНО:", PATH)
