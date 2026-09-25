import os, py_compile, shutil, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend")
AU = os.path.join(BASE, "app/api/auth.py")
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

OLD = TG_BLOCK + "\n" + CODE_BLOCK
NEW = (CODE_BLOCK + "\n"
       "        # ВАЖНО: уведомление владельцу отправляется ПОСЛЕ письма с кодом.\n"
       "        # При недоступном Telegram клиент всё равно получает код вовремя.\n"
       + TG_BLOCK)

src = open(AU, encoding="utf-8").read()
if MARK in src:
    print("  патч уже применён, пропуск")
elif src.count(OLD) != 1:
    print("  ЯКОРЬ НАЙДЕН %d РАЗ — файл не тронут" % src.count(OLD))
else:
    shutil.copy2(AU, AU + ".before_notify_" + stamp)
    open(AU, "w", encoding="utf-8").write(src.replace(OLD, NEW))
    try:
        py_compile.compile(AU, doraise=True)
        print("  auth.py ok, бэкап .before_notify_%s" % stamp)
    except Exception as exc:
        shutil.copy2(AU + ".before_notify_" + stamp, AU)
        print("  auth.py СЛОМАН, ОТКАЧЕН (%s)" % type(exc).__name__)
