

def block_email_verification():
    """Подтверждение email: коды, попытки, блокировка, триал.

    Работает через СЕРВИСНЫЙ СЛОЙ, а не через /api/auth/register — так не нужен
    обход капчи в продовом коде, а все проверки из ТЗ всё равно покрываются.
    Сам /api/auth/verify-email дёргается по HTTP, то есть эндпоинт проверяется по-настоящему.
    Пользователи создаются свои, чужие данные не трогаются, за собой убирает.
    Идемпотентен: перед стартом удаляет хвосты прошлых прогонов по префиксу.
    """
    b = "EmailVerify"
    if not QA_WRITES:
        chk(b, "блок пропущен (нужны --registration-write-tests и BORIS_ALLOW_QA_WRITES=1)",
            True, "SKIP", "SKIP")
        return

    from sqlalchemy import create_engine, text
    sys.path.insert(0, "/root/BORIS/backend")
    from dotenv import load_dotenv
    load_dotenv("/root/BORIS/backend/.env")
    from app.services import verification as vf
    from app.api.auth import hash_password
    from app.db.session import SessionLocal

    url = os.environ["DATABASE_URL"].replace("+asyncpg", "").replace("+psycopg2", "")
    eng = create_engine(url)

    PREFIX = "qa-ev-"

    def purge():
        """Удаляет пользователей блока и всё связанное. Только по своему префиксу."""
        with eng.begin() as c:
            ids = [r[0] for r in c.execute(text(
                "SELECT id FROM users WHERE email LIKE :p"), {"p": PREFIX + "%"}).fetchall()]
            for uid in ids:
                c.execute(text("DELETE FROM email_verifications WHERE user_id = :u"), {"u": uid})
                c.execute(text("DELETE FROM accounts WHERE owner_user_id = :u"), {"u": uid})
            if ids:
                c.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": ids})
            c.execute(text("DELETE FROM auth_rate_events WHERE rate_key LIKE :p"), {"p": PREFIX + "%"})
            c.execute(text("DELETE FROM auth_blocks WHERE block_key LIKE :p"), {"p": "%" + PREFIX + "%"})
        return len(ids)

    def mk_user():
        """Новый pending-пользователь. Возвращает (id, email)."""
        em = "%s%d-%s@%s" % (PREFIX, int(time.time()),
                             "".join(random.choices(string.ascii_lowercase, k=4)), QA_DOMAIN)
        with eng.begin() as c:
            uid = c.execute(text(
                "INSERT INTO users (email, password_hash, role, is_active, created_at, "
                " email_verified, status, email_normalized) "
                "VALUES (:e, :ph, 'client', true, now(), false, 'pending_verification', :n) "
                "RETURNING id"),
                {"e": em, "ph": hash_password("QaEv2026!"), "n": em.lower()}).scalar()
        return uid, em

    def issue(uid, em):
        db = SessionLocal()
        vid, code = vf.issue(db, uid, em.lower(), "127.0.0.1")
        db.commit(); db.close()
        return vid, code

    def db_code_row(vid):
        with eng.connect() as c:
            return c.execute(text(
                "SELECT id, code_hash, attempts_count, used_at, expires_at "
                "FROM email_verifications WHERE verification_id = :v"), {"v": vid}).mappings().first()

    def user_row(uid):
        with eng.connect() as c:
            return c.execute(text(
                "SELECT status, email_verified, email_verified_at, trial_started_at, "
                "       subscription_expires_at FROM users WHERE id = :u"), {"u": uid}).mappings().first()

    def verify(vid, code):
        return requests.post(API + "/api/auth/verify-email",
                             json={"verification_id": vid, "code": code}, timeout=30)

    step = "старт"
    try:
        killed = purge()
        chk(b, "0. очистка хвостов прошлых прогонов", True, "удалено пользователей: %d" % killed)

        step = "1 выпуск кода"
        uid, em = mk_user()
        vid, code = issue(uid, em)
        row = db_code_row(vid)
        chk(b, "1. код выпущен, в базе только хеш", bool(row) and code not in str(row["code_hash"]),
            "hash[:12]=%s" % str(row["code_hash"])[:12])
        chk(b, "1. код шестизначный", len(code) == 6 and code.isdigit(), "len=%d" % len(code))

        step = "2 триал до подтверждения"
        u = user_row(uid)
        chk(b, "2. до подтверждения: статус pending", u["status"] == "pending_verification", u["status"])
        chk(b, "2. до подтверждения: триал НЕ выдан",
            u["trial_started_at"] is None and u["subscription_expires_at"] is None,
            "trial=%s sub=%s" % (u["trial_started_at"], u["subscription_expires_at"]))

        step = "3 неверный код"
        bad = "000000" if code != "000000" else "111111"
        j = verify(vid, bad).json()
        row = db_code_row(vid)
        chk(b, "3. неверный код отклонён", j.get("code") == "code_invalid", str(j)[:90])
        chk(b, "3. attempts_count вырос до 1", row["attempts_count"] == 1, str(row["attempts_count"]))
        chk(b, "3. в ответе есть attempts_left", j.get("attempts_left") == 4, str(j.get("attempts_left")))

        step = "4 верный код"
        j = verify(vid, code).json()
        chk(b, "4. верный код принят", j.get("status") == "ok", str(j)[:90])
        chk(b, "4. в ответе выдан токен сессии", bool(j.get("access_token")), str(bool(j.get("access_token"))))
        u = user_row(uid)
        chk(b, "4. статус стал active", u["status"] == "active", u["status"])
        chk(b, "4. email_verified=true", u["email_verified"] is True, str(u["email_verified"]))
        chk(b, "4. email_verified_at заполнен", u["email_verified_at"] is not None, str(u["email_verified_at"]))

        step = "5 триал после подтверждения"
        chk(b, "5. триал выдан после подтверждения",
            u["trial_started_at"] is not None and u["subscription_expires_at"] is not None,
            "trial=%s" % u["trial_started_at"])
        if u["subscription_expires_at"] and u["trial_started_at"]:
            days = (u["subscription_expires_at"] - u["trial_started_at"]).days
            chk(b, "5. срок триала 4 дня", days == 4, "дней=%s" % days)
        sub1 = u["subscription_expires_at"]

        step = "6 повторное использование"
        j = verify(vid, code).json()
        chk(b, "6. повторный ввод кода отклонён", j.get("code") == "code_used", str(j)[:90])
        u2 = user_row(uid)
        chk(b, "6. подписка НЕ продлена повторно", u2["subscription_expires_at"] == sub1,
            "было=%s стало=%s" % (sub1, u2["subscription_expires_at"]))

        step = "7 блокировка после пяти ошибок"
        uid2, em2 = mk_user()
        vid2, code2 = issue(uid2, em2)
        bad2 = "000000" if code2 != "000000" else "111111"
        codes_seen = [verify(vid2, bad2).json().get("code") for _ in range(5)]
        row2 = db_code_row(vid2)
        chk(b, "7. пять неверных попыток учтены", row2["attempts_count"] == 5, str(row2["attempts_count"]))
        chk(b, "7. пятая попытка вернула code_locked", codes_seen[-1] == "code_locked", str(codes_seen))
        j = verify(vid2, code2).json()
        chk(b, "7. после блокировки ВЕРНЫЙ код не принимается", j.get("code") == "code_locked", str(j)[:90])
        u3 = user_row(uid2)
        chk(b, "7. пользователь остался pending", u3["status"] == "pending_verification", u3["status"])

        step = "8 новый код гасит старый"
        uid3, em3 = mk_user()
        vid3, code3 = issue(uid3, em3)
        vid4, code4 = issue(uid3, em3)
        old = db_code_row(vid3)
        chk(b, "8. старый код погашен (used_at)", old["used_at"] is not None, str(old["used_at"]))
        j = verify(vid3, code3).json()
        chk(b, "8. старый код не принимается", j.get("code") == "code_used", str(j)[:90])
        j = verify(vid4, code4).json()
        chk(b, "8. новый код принимается", j.get("status") == "ok", str(j)[:90])

        step = "9 просроченный код"
        uid5, em5 = mk_user()
        vid5, code5 = issue(uid5, em5)
        with eng.begin() as c:
            c.execute(text("UPDATE email_verifications SET expires_at = now() - interval '1 minute' "
                           "WHERE verification_id = :v"), {"v": vid5})
        j = verify(vid5, code5).json()
        chk(b, "9. просроченный код отклонён", j.get("code") == "code_expired", str(j)[:90])
        u5 = user_row(uid5)
        chk(b, "9. просрочка не активировала пользователя",
            u5["status"] == "pending_verification" and u5["subscription_expires_at"] is None, u5["status"])

        step = "10 неизвестный verification_id"
        j = verify("no-such-verification-id-zzz", "123456").json()
        chk(b, "10. неизвестный id отклонён", j.get("code") == "code_invalid", str(j)[:90])

        step = "11 существующие пользователи"
        with eng.connect() as c:
            n_bad = c.execute(text(
                "SELECT count(*) FROM users WHERE id <= 24 AND (status <> 'active' "
                " OR email_verified IS NOT TRUE)")).scalar()
        chk(b, "11. старые пользователи сохранили доступ", n_bad == 0, "проблемных=%s" % n_bad)

    except Exception as e:
        chk(b, "блок упал на шаге: %s" % step, False, "%s: %s" % (type(e).__name__, str(e)[:160]))
        traceback.print_exc()
    finally:
        left = purge()
        chk(b, "12. очистка после прогона", True, "удалено пользователей: %d" % left)
