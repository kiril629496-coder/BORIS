"""Стадия онбординга — ВЫЧИСЛЯЕТСЯ из фактических данных, а не хранится отдельно:
хранимый статус неизбежно разъедется с реальностью."""
from datetime import datetime, timedelta
from sqlalchemy import text
from app.db.session import SessionLocal

STAGES = [
    (1, "Зарегистрирован", "Аккаунт создан, дальше нужно подключить Avito"),
    (2, "Avito подключён", "Ключи API на месте"),
    (3, "Категория определена", "Борис понял, что вы продаёте"),
    (4, "Объявления созданы", "Черновики готовы к выгрузке"),
    (5, "Фид выгружен", "Объявления ушли на Avito"),
    (6, "Идёт статистика", "Показы и контакты собираются"),
]


def stage_of(account_id: str, db=None) -> dict:
    own = db is None
    db = db or SessionLocal()
    try:
        a = db.execute(text("""SELECT avito_client_id, company_description, company_niche, created_at
                               FROM accounts WHERE account_id=:a"""), {"a": account_id}).fetchone()
        if not a:
            return {"status": "error", "message": "аккаунт не найден"}

        keys = {r[0] for r in db.execute(
            text("SELECT key FROM storage WHERE account_id=:a"), {"a": account_id}).fetchall()}
        recent = {(datetime.utcnow() - timedelta(days=i)).strftime("daily_stats:%Y-%m-%d") for i in range(4)}

        st = 1
        if a[0]:
            st = 2
            if "detected_category" in keys:
                st = 3
                if "gen_ads" in keys or "drafts" in keys:
                    st = 4
                    if "feed_items" in keys:
                        st = 5
                        if keys & recent:
                            st = 6

        num, name, hint = STAGES[st - 1]
        return {
            "status": "ok",
            "account_id": account_id,
            "стадия": num,
            "название": name,
            "подсказка": hint,
            "всего_стадий": len(STAGES),
            "avito_подключён": bool(a[0]),
            "нет_данных_о_компании": not ((a[1] or "").strip() or (a[2] or "").strip()),
            "создан": a[3].isoformat() if a[3] else None,
        }
    finally:
        if own:
            db.close()


def track(account_id: str, db=None) -> dict:
    """Считает стадию и фиксирует переход. Возвращает стадию + сколько часов на ней стоит."""
    own = db is None
    db = db or SessionLocal()
    try:
        st = stage_of(account_id, db)
        if st.get("status") != "ok":
            return st
        cur = st["стадия"]
        row = db.execute(text("SELECT stage, reached_at, nudged_at, nudge_count FROM onboarding_progress WHERE account_id=:a"),
                         {"a": account_id}).fetchone()
        now = datetime.utcnow()
        if not row:
            db.execute(text("INSERT INTO onboarding_progress (account_id, stage, reached_at) VALUES (:a,:s,:t)"),
                       {"a": account_id, "s": cur, "t": now})
            db.commit()
            reached, nudged, ncount = now, None, 0
        elif row[0] != cur:
            db.execute(text("UPDATE onboarding_progress SET stage=:s, reached_at=:t, nudged_at=NULL, nudge_count=0 WHERE account_id=:a"),
                       {"a": account_id, "s": cur, "t": now})
            db.commit()
            reached, nudged, ncount = now, None, 0
        else:
            reached, nudged, ncount = row[1], row[2], row[3] or 0
        hours = round((now - reached).total_seconds() / 3600, 1) if reached else 0
        st["часов_на_стадии"] = hours
        st["замер"] = cur < 6 and hours >= 24
        st["напоминаний_послано"] = ncount
        st["последнее_напоминание"] = nudged.isoformat() if nudged else None
        return st
    finally:
        if own:
            db.close()


def stuck_accounts(min_hours: int = 24) -> list:
    """Кто замер: стадия ниже 6 и стоит на ней дольше min_hours."""
    db = SessionLocal()
    try:
        ids = [r[0] for r in db.execute(text("SELECT account_id FROM accounts WHERE billing_mode='auto' OR billing_mode IS NULL")).fetchall()]
        out = []
        for a in ids:
            r = track(a, db)
            if r.get("status") == "ok" and r.get("стадия", 6) < 6 and r.get("часов_на_стадии", 0) >= min_hours:
                out.append(r)
        return sorted(out, key=lambda x: -x["часов_на_стадии"])
    finally:
        db.close()


# Тексты подсказок под стадию: что именно человеку сделать дальше.
NUDGES = {
    1: ("Осталось подключить Avito",
        "Вы зарегистрировались в БОРИСЕ, но Avito ещё не подключён — без него я не вижу ваши объявления. "
        "Откройте «Профиль и настройки» и вставьте ключи Avito API, это пара минут."),
    2: ("Расскажите, что вы продаёте",
        "Avito подключён — спасибо. Теперь откройте вкладку «Объявления»: я сам разберу ваш товар "
        "и подберу категорию Avito, от вас нужно только подтвердить."),
    3: ("Готов сделать первые объявления",
        "Категорию я определил. Осталось нажать одну кнопку — соберу тексты и заголовки под ваш товар, "
        "покажу на проверку до публикации."),
    4: ("Объявления готовы, но ещё не на Avito",
        "Черновики собраны и ждут выгрузки. Проверьте их во вкладке «Объявления» и отправьте на Avito — "
        "до этого момента они никому не показываются."),
    5: ("Ждём первую статистику",
        "Объявления выгружены. Показы и контакты появятся в течение суток — я соберу их сам "
        "и покажу во вкладке «Статистика»."),
}

MAX_NUDGES_PER_STAGE = 3
NUDGE_COOLDOWN_HOURS = 24


def nudge(account_id: str, force: bool = False, db=None) -> dict:
    """Подсказать клиенту, что делать дальше. Не чаще раза в сутки и не больше 3 раз на стадию —
    навязчивость злит сильнее, чем молчание."""
    import json as _json
    own = db is None
    db = db or SessionLocal()
    try:
        st = track(account_id, db)
        if st.get("status") != "ok":
            return st
        stage = st["стадия"]
        if stage >= 6:
            return {"status": "skip", "reason": "клиент дошёл до конца"}
        if not force and not st.get("замер"):
            return {"status": "skip", "reason": "ещё не замер, часов на стадии: %s" % st["часов_на_стадии"]}

        row = db.execute(text("SELECT nudged_at, nudge_count FROM onboarding_progress WHERE account_id=:a"),
                         {"a": account_id}).fetchone()
        nudged_at, ncount = (row[0], row[1] or 0) if row else (None, 0)
        if not force:
            if ncount >= MAX_NUDGES_PER_STAGE:
                return {"status": "skip", "reason": "лимит подсказок на стадии исчерпан"}
            if nudged_at and (datetime.utcnow() - nudged_at) < timedelta(hours=NUDGE_COOLDOWN_HOURS):
                return {"status": "skip", "reason": "подсказка уже была сегодня"}

        title, body = NUDGES.get(stage, ("", ""))
        if not title:
            return {"status": "skip", "reason": "нет текста для стадии %s" % stage}

        # 1) уведомление в кабинет
        srow = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='notifications'"),
                          {"a": account_id}).fetchone()
        items = []
        if srow and srow[0]:
            try:
                items = _json.loads(srow[0])
            except Exception:
                items = []
        items.append({"ts": datetime.utcnow().replace(microsecond=0).isoformat(),
                      "text": "💡 %s — %s" % (title, body), "related_results": [], "read": False})
        val = _json.dumps(items[-50:], ensure_ascii=False)
        if srow:
            db.execute(text("UPDATE storage SET value=:v WHERE account_id=:a AND key='notifications'"),
                       {"v": val, "a": account_id})
        else:
            db.execute(text("INSERT INTO storage (account_id, key, value) VALUES (:a,'notifications',:v)"),
                       {"a": account_id, "v": val})

        # 2) дублируем в Telegram, если клиент его подключил
        tg = "не отправляли"
        chat = db.execute(text("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"),
                          {"a": account_id}).fetchone()
        if chat and chat[0]:
            try:
                from app.telegram_bot import send_telegram_message
                r = send_telegram_message(chat[0], "<b>%s</b>\n\n%s" % (title, body))
                tg = "отправлено" if r.get("ok") else "ошибка: %s" % r.get("error")
            except Exception as e:
                tg = "ошибка: %s" % str(e)[:80]

        db.execute(text("UPDATE onboarding_progress SET nudged_at=:t, nudge_count=:c WHERE account_id=:a"),
                   {"t": datetime.utcnow(), "c": ncount + 1, "a": account_id})
        db.commit()
        return {"status": "ok", "стадия": stage, "заголовок": title,
                "подсказок_на_стадии": ncount + 1, "telegram": tg}
    finally:
        if own:
            db.close()


# --- «Сделаю за тебя» ------------------------------------------------------
# Борис доводит работу до ЧЕРНОВИКОВ. Публикация всегда остаётся за клиентом:
# ошибка в категории не должна уехать на Avito от его имени.

MAX_AUTO_PRODUCTS = 20


def _clean_url(raw: str) -> str:
    """У клиентов в поле сайта попадается мусор: «https://site.ru/, » с запятой и пробелом."""
    if not raw:
        return ""
    u = raw.strip().split(",")[0].strip().rstrip(".,;")
    if not u:
        return ""
    if not u.startswith("http"):
        u = "https://" + u
    return u


def do_for(account_id: str, force: bool = False, db=None) -> dict:
    """Делает за клиента следующий шаг. Возвращает, что именно сделано."""
    import json as _json
    own = db is None
    db = db or SessionLocal()
    try:
        st = track(account_id, db)
        if st.get("status") != "ok":
            return st
        stage = st["стадия"]
        if stage >= 4:
            return {"status": "skip", "reason": "клиент дошёл до черновиков сам"}
        if not force and not st.get("замер"):
            return {"status": "skip", "reason": "ещё не замер (%sч)" % st["часов_на_стадии"]}

        row = db.execute(text("SELECT auto_done_stage FROM onboarding_progress WHERE account_id=:a"),
                         {"a": account_id}).fetchone()
        if not force and row and row[0] == stage:
            return {"status": "skip", "reason": "на этой стадии уже делали"}

        acc = db.execute(text("""SELECT company_niche, company_description, company_website
                                 FROM accounts WHERE account_id=:a"""), {"a": account_id}).fetchone()
        niche = (acc[0] or "").strip() if acc else ""
        site = _clean_url(acc[2] if acc else "")
        done = []

        # Стадия 2 → 3: определить категорию
        if stage == 2:
            if not niche and not site:
                return {"status": "skip", "reason": "нет ни ниши, ни сайта — нечего определять"}
            try:
                from app.api.avito import detect_category_endpoint
                r = detect_category_endpoint({"account_id": account_id, "niche": niche})
                if r.get("status") == "ok" or r.get("category"):
                    done.append("определил категорию: %s" % (r.get("category") or "—"))
                else:
                    return {"status": "error", "message": "категория не определилась: %s" % r.get("message")}
            except Exception as e:
                return {"status": "error", "message": "категория: %s" % str(e)[:200]}

        # Стадия 3 → 4: подтянуть товары и собрать черновики
        if stage == 3:
            if not site:
                return {"status": "skip", "reason": "у клиента не указан сайт — товары взять неоткуда"}
            try:
                from app.api.parser import (parse_site, ParseRequest, _save_parsed_products,
                                            parsed_products_to_drafts, ProductsToDraftsRequest)
                pr = parse_site(ParseRequest(url=site, account_id=account_id, limit=MAX_AUTO_PRODUCTS))
                products = (pr or {}).get("products") or []
                if not products:
                    return {"status": "skip", "reason": "с сайта %s ничего не выгрузилось" % site}
                srow = db.execute(text("SELECT 1 FROM storage WHERE account_id=:a AND key='parsed_products'"),
                                  {"a": account_id}).fetchone()
                if not srow:
                    _save_parsed_products(account_id, site, products)
                done.append("выгрузил с сайта %d товаров" % len(products))

                n = min(len(products), MAX_AUTO_PRODUCTS)
                dr = parsed_products_to_drafts(ProductsToDraftsRequest(
                    account_id=account_id, indices=list(range(n)),
                    topic=niche, batch_label="Борис подготовил"))
                made = dr.get("created") or dr.get("count") or n
                done.append("собрал %s черновиков" % made)
            except Exception as e:
                return {"status": "error", "message": "черновики: %s" % str(e)[:200]}

        if not done:
            return {"status": "skip", "reason": "нечего делать на стадии %s" % stage}

        db.execute(text("""UPDATE onboarding_progress SET auto_done_stage=:s, auto_done_at=NOW()
                           WHERE account_id=:a"""), {"s": stage, "a": account_id})

        txt = ("Пока вы были заняты, я сделал часть работы за вас: " + ", ".join(done) +
               ". Загляните в кабинет — проверьте и, если всё нравится, отправляйте на Avito. "
               "Публиковать сам я не стал: последнее слово за вами.")
        srow = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='notifications'"),
                          {"a": account_id}).fetchone()
        items = []
        if srow and srow[0]:
            try:
                items = _json.loads(srow[0])
            except Exception:
                items = []
        items.append({"ts": datetime.utcnow().replace(microsecond=0).isoformat(),
                      "text": "\U0001F916 " + txt, "related_results": [], "read": False})
        val = _json.dumps(items[-50:], ensure_ascii=False)
        if srow:
            db.execute(text("UPDATE storage SET value=:v WHERE account_id=:a AND key='notifications'"),
                       {"v": val, "a": account_id})
        else:
            db.execute(text("INSERT INTO storage (account_id, key, value) VALUES (:a,'notifications',:v)"),
                       {"a": account_id, "v": val})

        chat = db.execute(text("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"),
                          {"a": account_id}).fetchone()
        if chat and chat[0]:
            try:
                from app.telegram_bot import send_telegram_message
                send_telegram_message(chat[0], txt)
            except Exception:
                pass
        db.commit()
        return {"status": "ok", "стадия": stage, "сделано": done}
    finally:
        if own:
            db.close()
