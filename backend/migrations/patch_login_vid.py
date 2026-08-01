# -*- coding: utf-8 -*-
"""login для pending-пользователя отдаёт verification_id и маску адреса,
чтобы экран /verify работал после повторного входа (в т.ч. в другом браузере)."""
import io, sys, time

PATH = sys.argv[1] if len(sys.argv) > 1 else "/root/BORIS/backend/app/api/auth.py"
s = io.open(PATH, encoding="utf-8").read()

if "pending_hint" in s:
    print("УЖЕ ПРИМЕНЁН — выхожу")
    sys.exit(0)

old = '''        token = create_access_token(user.id)
        return {"status": "ok", "access_token": token, "token_type": "bearer",
                "user": {"id": user.id, "email": user.email, "role": user.role,
                         "account_id": user.account_id,
                         "status": getattr(user, "status", "active")}}
'''
n = s.count(old)
assert n == 1, "якорь ответа login найден %d раз" % n

new = '''        token = create_access_token(user.id)
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
'''
s = s.replace(old, new, 1)
print("  ok: pending_hint в ответе login")

io.open(PATH + ".before_pendinghint_%d" % int(time.time()), "w", encoding="utf-8").write(
    io.open(PATH, encoding="utf-8").read())
io.open(PATH, "w", encoding="utf-8").write(s)
print("ЗАПИСАНО:", PATH)
