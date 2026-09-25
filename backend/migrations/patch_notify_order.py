import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
AU = os.path.join(BASE, "app/api/auth.py")
TG = os.path.join(BASE, "app/telegram_bot.py")
stamp = str(int(time.time()))
MARK = "уведомление владельцу отправляется ПОСЛЕ"

TG_BLOCK = '''        # уведомление владельцу в Telegram о новом клиенте (не критично к регистрации)
        try:
            import os as _os
            from app.telegram_bot import send_telegram_message
            _chat = _os.environ.get("DIRECTOR_CHAT_ID")
            if _qa:
                logger.info("register: QA-регистрация %s — внешние уведомления подавлены",
                            req.email)
            if _chat and not _qa:
                send_telegram_message(_chat,
                    f"\\U0001F195 <b>Новый клиент БОРИС</b>\\n"
                    f"Email: {user.email}\\n"
                    f"Компания: {req.account_name or '—'}\\n"
                    f"Статус: ожидает подтверждения почты")
        except Exception as _e:
            print("telegram notify failed:", _e)
'''

CODE_BLOCK = '''        # Код подтверждения. В базе только хеш, сам код уходит одним письмом.
        _vid, _code = _vf.issue(db, user.id, _norm, _ip)
        _vf.log_event(db, "send", _norm, _ip)
        db.commit()
        if not _qa:
            _sent, _why = _ml.send_verification_code(user.email, _code, verification_id=_vid)
            if not _sent:
                logger.warning("register: письмо с кодом не ушло (%s)", _why)
        else:
            logger.info("register: QA-регистрация, письмо не отправляется")
'''

OLD = TG_BLOCK + CODE_BLOCK
NEW = (CODE_BLOCK +
       "        # ВАЖНО: уведомление владельцу отправляется ПОСЛЕ письма с кодом.\n"
       "        # При недоступном Telegram клиент всё равно получает код вовремя.\n"
       + TG_BLOCK)

TG_PAIRS = [
("        return requests.post(url, json=json_payload, timeout=12)",
 "        return requests.post(url, json=json_payload, timeout=4)"),
("def _telegram_post(url, json_payload, timeout=30):",
 "def _telegram_post(url, json_payload, timeout=8):"),
]


def patch(path, pairs):
    name = os.path.basename(path)
    src = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if src.count(old) != 1:
            print("  %-18s ЯКОРЬ %d раз: %s" % (name, src.count(old), old.strip()[:45]))
            return False
        src = src.replace(old, new)
    shutil.copy2(path, path + ".before_notify_" + stamp)
    open(path, "w", encoding="utf-8").write(src)
    try:
        py_compile.compile(path, doraise=True)
        print("  %-18s ok" % name)
        return True
    except Exception as exc:
        shutil.copy2(path + ".before_notify_" + stamp, path)
        print("  %-18s СЛОМАН, ОТКАЧЕН (%s)" % (name, type(exc).__name__))
        return False


if MARK in open(AU, encoding="utf-8").read():
    print("  патч уже применён, пропуск")
else:
    ok = patch(AU, [(OLD, NEW)])
    ok = patch(TG, TG_PAIRS) and ok
    print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
