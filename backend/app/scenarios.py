# -*- coding: utf-8 -*-
"""Каталог бизнес-сценариев БОРИСа и вычисление прогресса.

ГЛАВНЫЙ ПРИНЦИП: прогресс НЕ хранится, прогресс ВЫЧИСЛЯЕТСЯ.
В business_scenarios лежит только выбор клиента и его ручные отметки.
Факт (подключён ли Avito, есть ли черновики, идёт ли статистика) всегда
берётся из живых данных - поэтому полоса прогресса физически не может
разойтись с лестницей онбординга.

Все формы значений сверены с боевой базой 28.07, ничего не угадано.
"""

import json
from datetime import datetime, timedelta

from app.db.session import SessionLocal
from app.models.storage import Storage
from sqlalchemy import text as _sqltext


# ---------------------------------------------------------------- storage

def _load(db, account_id, key, default=None):
    """Читает JSON-значение из storage. Никогда не бросает исключение."""
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == key).first()
        if not row or row.value is None:
            return default
        return json.loads(row.value)
    except Exception:
        return default


def _save(db, account_id, key, value):
    """Пишет JSON-значение в storage. Никогда не бросает исключение."""
    try:
        blob = json.dumps(value, ensure_ascii=False)
        row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == key).first()
        if row:
            row.value = blob
        else:
            db.add(Storage(account_id=account_id, key=key, value=blob))
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _latest_daily_stats(db, account_id, max_days_back=7):
    """Последняя доступная сводка daily_stats за N дней. Возвращает (дата, данные)."""
    today = datetime.now().date()
    for back in range(0, max_days_back + 1):
        d = (today - timedelta(days=back)).isoformat()
        v = _load(db, account_id, "daily_stats:" + d)
        if isinstance(v, dict):
            return d, v
    return None, None


# ------------------------------------------------------------------ facts

def collect_facts(account_id, db=None):
    """Один проход по базе — все факты, нужные и рабочему столу, и сценариям.
    Ничего не пишет. Любое отсутствие данных даёт честный ноль, а не выдумку."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    f = {"account_id": account_id}
    try:
        acc = db.execute(_sqltext(
            "SELECT name, avito_client_id, company_niche, company_website, "
            "company_description, company_advantages, telegram_chat_id, "
            "client_goal, client_goal_text FROM accounts WHERE account_id=:a"
        ), {"a": account_id}).fetchone()

        f["account_name"] = (acc[0] if acc else "") or ""
        f["avito_connected"] = bool(acc and acc[1])
        f["has_niche"] = bool(acc and (acc[2] or "").strip())
        f["has_site"] = bool(acc and (acc[3] or "").strip())
        f["has_description"] = bool(acc and (acc[4] or "").strip())
        f["has_advantages"] = bool(acc and (acc[5] or "").strip())
        f["telegram"] = bool(acc and acc[6])
        f["client_goal"] = (acc[7] if acc else "") or ""
        f["taught"] = f["has_description"] or f["has_advantages"]

        # ДОСТУП, а не «тариф». Клиент легально работает тремя способами:
        # безлимит по индивидуальной сделке, стандартный тариф, ручная оплата.
        # Раньше учитывался только tier — отсюда ложное «Нет активного тарифа»
        # у четырёх оплативших клиентов на unlimited (аудит 28.07).
        billing = _load(db, account_id, "billing", {}) or {}
        tier = billing.get("tier") or "none"
        f["billing_tier"] = tier
        f["billing_usage"] = billing.get("usage") or {}
        f["unlimited"] = bool(billing.get("unlimited"))
        pay_ok, pay_until = _payment_active(_load(db, account_id, "payment_status", {}) or {})
        f["payment_active"] = pay_ok
        f["payment_until"] = pay_until
        f["has_active_access"] = bool(
            f["unlimited"] or tier not in ("none", "", None) or pay_ok)

        # черновики: drafts и gen_ads - оба списки объявлений
        drafts = _load(db, account_id, "drafts", []) or []
        gen_ads = _load(db, account_id, "gen_ads", []) or []
        f["drafts_count"] = len(drafts) if isinstance(drafts, list) else 0
        f["gen_ads_count"] = len(gen_ads) if isinstance(gen_ads, list) else 0
        f["ready_count"] = max(f["drafts_count"], f["gen_ads_count"])

        feed = _load(db, account_id, "feed_items", []) or []
        f["feed_count"] = len(feed) if isinstance(feed, list) else 0

        cat = _load(db, account_id, "detected_category", {}) or {}
        f["category"] = cat.get("category") or ""
        f["has_category"] = bool(f["category"])

        kpi = _load(db, account_id, "kpi_settings", {}) or {}
        f["kpi_set"] = bool(kpi.get("target_leads_per_day"))
        f["kpi"] = kpi

        cpx = _load(db, account_id, "cpx_advice", {}) or {}
        f["advisor_ready"] = bool(cpx.get("generated_at"))
        f["advisor_summary"] = (cpx.get("summary") or "")[:300]

        ap = _load(db, account_id, "autopilot_settings", {}) or {}
        f["autopilot_mode"] = ap.get("mode") or "always_ask"

        # статистика: items[] с views/contacts/conversion
        sdate, stats = _latest_daily_stats(db, account_id)
        f["stats_date"] = sdate
        f["has_stats"] = bool(stats)
        items = (stats or {}).get("items") or []
        f["items_total"] = (stats or {}).get("items_count") or len(items)
        f["active_items"] = sum(1 for i in items if i.get("status") == "active")
        f["views"] = sum(int(i.get("views") or 0) for i in items)
        f["contacts"] = sum(int(i.get("contacts") or 0) for i in items)
        f["dead_items"] = sum(1 for i in items
                              if int(i.get("views") or 0) >= 10
                              and int(i.get("contacts") or 0) == 0)
        f["zero_view_items"] = sum(1 for i in items if int(i.get("views") or 0) == 0)

        notif = _load(db, account_id, "notifications", []) or []
        f["unread_notifications"] = sum(
            1 for n in notif if isinstance(n, dict) and not n.get("read"))
        f["notifications"] = [n for n in reversed(notif) if isinstance(n, dict)][:5]

        # стадия онбординга - единственный измеритель факта для запуска Avito
        try:
            from app.onboarding import stage_of
            st = stage_of(account_id, db=db) if _accepts_db() else stage_of(account_id)
            if isinstance(st, dict) and st.get("status") == "ok":
                f["stage"] = int(st.get("стадия") or 1)
                f["stage_name"] = st.get("название") or ""
                f["stage_hint"] = st.get("подсказка") or ""
                f["stage_total"] = int(st.get("всего_стадий") or 6)
            else:
                f["stage"] = 1
                f["stage_name"] = ""
                f["stage_hint"] = ""
                f["stage_total"] = 6
        except Exception as e:
            f["stage"] = 1
            f["stage_name"] = ""
            f["stage_hint"] = ""
            f["stage_total"] = 6
            f["stage_error"] = str(e)[:200]
    finally:
        if own_db:
            db.close()
    return f


def _accepts_db():
    """stage_of умеет принимать готовую сессию — проверяем один раз."""
    try:
        import inspect
        from app.onboarding import stage_of
        return "db" in inspect.signature(stage_of).parameters
    except Exception:
        return False


# ------------------------------------------------------------------ журнал

_RU_FALLBACK = {
    "set_autopilot": "Изменён режим работы",
    "set_kpi_settings": "Изменена цель по лидам",
    "set_republish_settings": "Изменены настройки перепубликации",
    "delete_kpi_settings": "Удалена цель по лидам",
    "delete_republish_settings": "Сброшены настройки перепубликации",
    "edit_item": "Изменено объявление",
    "publish_validation_ok": "Проверка перед публикацией пройдена",
    "publish_validation_failed": "Публикация остановлена проверкой",
    "feed_blocked": "Фид заблокирован проверкой",
    "republish_apply": "Перепубликация неэффективных объявлений",
    "kpi_plan_execute": "Выполнен план по лидам",
    "kpi_autopilot_run": "Автопилот отработал",
    "cpx_apply_archive": "Снято продвижение",
    "cpx_apply_bid": "Изменена ставка",
    "auto_duplicate": "Созданы дубли объявлений",
    "auto_duplicate_run": "Размножение объявлений",
    "robokassa_paid": "Поступила оплата",
}


def _humanize(entry):
    """Пробуем канонический переводчик из avito.py; если он недоступен -
    свой словарь. Импорт внутри функции, чтобы не связываться с avito.py
    на этапе загрузки модуля (файл активно правится)."""
    try:
        from app.api.avito import _humanize_entry
        return _humanize_entry(dict(entry))
    except Exception:
        actor = str(entry.get("actor") or "")
        action = str(entry.get("action") or "")
        phrase = _RU_FALLBACK.get(action, action)
        details = str(entry.get("details") or "")
        e = dict(entry)
        e["who"] = "🤖 Борис" if actor.startswith("boris") else "👤 Вы"
        e["human"] = (phrase + (" — " + details if details else "")).strip()
        return e


def journal(account_id, db=None, limit=10, only_today=False):
    """Записи журнала. Формат записи: {ts, actor, action, details}."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        log = _load(db, account_id, "audit_log", []) or []
        if not isinstance(log, list):
            return []
        if only_today:
            today = datetime.now().date().isoformat()
            log = [e for e in log if str(e.get("ts", "")).startswith(today)]
        out = [_humanize(e) for e in reversed(log) if isinstance(e, dict)]
        return out[:limit] if limit else out
    finally:
        if own_db:
            db.close()


def today_summary(account_id, db=None):
    """Блок «Сегодня BORIS» — ТОЛЬКО по реально залогированным действиям."""
    rows = journal(account_id, db=db, limit=0, only_today=True)
    boris, mine, by_action = 0, 0, {}
    for e in rows:
        actor = str(e.get("actor") or "")
        if actor.startswith("boris"):
            boris += 1
        elif actor == "user":
            mine += 1
        a = str(e.get("action") or "")
        by_action[a] = by_action.get(a, 0) + 1
    lines = []
    for a, n in sorted(by_action.items(), key=lambda x: -x[1])[:6]:
        lines.append({"action": a,
                      "title": _RU_FALLBACK.get(a, a),
                      "count": n})
    return {"total": len(rows), "by_boris": boris, "by_user": mine,
            "lines": lines, "empty": len(rows) == 0}


# --------------------------------------------------------------- сценарии

SOON = [
    ("find_problem", "Найти причину отсутствия заявок"),
    ("new_niche", "Проверить новую нишу"),
    ("new_city", "Выйти в новый город"),
    ("season", "Подготовиться к сезону"),
    ("social", "Запустить социальные сети"),
    ("automate_sales", "Автоматизировать продажи"),
    ("website", "Создать сайт"),
]

CATALOG = {
    "avito_start": {
        "title": "Запустить Авито с нуля",
        "lead": "Перенесу товары с вашего сайта в Авито: разберу каталог, соберу объявления "
                "с уникальными текстами, проверю фид валидатором Авито. "
                "На выходе готовые к выгрузке объявления. Занимает 15-30 минут.",
        "modules": ["Аккаунты", "Категории", "Объявления", "Фид"],
        "plan": ["Я проверю, подключён ли аккаунт Avito",
                 "Я разберу ваш сайт и покажу найденные товары",
                 "Вы отметите, какие товары переносить",
                 "Я соберу объявления и сделаю тексты уникальными",
                 "Я проверю фид официальным валидатором Avito",
                 "Вы посмотрите результат и запустите выгрузку"],
        "fields": [
            {"key": "niche", "label": "Что продаёте", "type": "text",
             "placeholder": "например: кухни на заказ", "required": True},
            {"key": "city", "label": "Город", "type": "text", "required": True},
            {"key": "site", "label": "Ссылка на сайт (если есть)", "type": "text",
             "placeholder": "оставьте пустым, если сайта нет", "required": False},
        ],
    },
    "more_leads": {
        "title": "Получить больше заявок",
        "lead": "Проверю объявления, цель по лидам, спрос, продвижение и работу с обращениями.",
        "modules": ["Статистика", "Маркетинг", "Советник", "Продажи"],
        "plan": ["Найду объявления с показами, но без обращений",
                 "Помогу задать цель по заявкам и их цену",
                 "Включу Советника по продвижению в ваших рамках",
                 "Покажу, что обновить в слабых объявлениях",
                 "Предложу подключить ИИ-менеджера для ответов"],
        "fields": [
            {"key": "city", "label": "Город", "type": "text", "required": True},
            {"key": "direction", "label": "Направление", "type": "text", "required": True},
            {"key": "target_leads", "label": "Сколько заявок в день нужно",
             "type": "number", "required": True},
            {"key": "max_cpl", "label": "Максимальная цена заявки, ₽",
             "type": "number", "required": True},
        ],
    },
    "revive": {
        "title": "Оживить кабинет",
        "lead": "Покажу, какие объявления перестали работать, и подготовлю замену. "
                "На выходе: список слабых объявлений с причинами и готовый к выгрузке фид. "
                "Занимает 5-10 минут.",
        "modules": ["Объявления", "Маркетинг", "Дубли"],
        "plan": ["Я найду объявления без просмотров и без обращений",
                 "Я запомню пороги, по которым считать объявление слабым",
                 "Я размножу те объявления, которые приносят заявки",
                 "Вы решите, какие слабые объявления снять",
                 "Вы запустите выгрузку фида на Avito"],
        "fields": [
            {"key": "limit", "label": "Сколько объявлений готовы обновить",
             "type": "number", "required": True},
            {"key": "touch_price", "label": "Можно ли менять цены",
             "type": "select", "options": ["нет", "да"], "required": True},
        ],
    },
}


def _steps(scenario_type, f):
    """Шаги и их ФАКТИЧЕСКИЙ статус. tab — ключ activeTab старого кабинета,
    route — отдельный маршрут. auto=True означает, что шаг закрывается сам."""
    s = f.get("stage", 1)
    if scenario_type == "avito_start":
        st = f.get("_start") or {}
        parsed = st.get("parsed", 0)
        drafts = st.get("drafts", 0)
        uniq = st.get("uniquified", 0)
        return [
            {"key": "connect", "title": "Подключить Avito",
             "hint": "Ключи Avito API в профиле аккаунта. Без них выгрузка невозможна.",
             "route": "/agency", "done": bool(st.get("has_keys")) or s >= 2, "auto": True},
            {"key": "parse", "title": "Разобрать ваш сайт",
             "hint": "Открою каталог и соберу карточки товаров. "
                     "Это только чтение сайта — ничего никуда не публикую.",
             "action": "parse_site", "auto": True, "done": parsed > 0},
            {"key": "review", "title": "Посмотреть найденные товары",
             "hint": "Найдено %d %s. Отметьте те, что переносить не нужно."
                     % (parsed, _plural(parsed, "товар", "товара", "товаров")),
             "action": "parsed_products", "auto": False, "done": bool(st.get("reviewed"))},
            {"key": "photos", "title": "Загрузить фотографии товаров",
             "hint": "Подтяну изображения с карточек — без фото объявление не пройдёт модерацию.",
             "auto": True, "done": bool(st.get("gallery_done"))},
            {"key": "seller", "title": "Настроить, как показывать ваши объявления",
             "hint": "Семь настроек, одинаковых для всех ваших объявлений: как с вами "
                     "связываться, кому продаёте, состояние товара. Спрошу один раз — "
                     "дальше подставлю везде сам.",
             "action": "seller_defaults", "auto": False,
             "done": bool(st.get("seller_ready"))},
            {"key": "drafts", "title": "Создать черновики объявлений",
             "hint": "Черновики видны только вам и на Avito не уходят. "
                     "Создано: %d." % drafts,
             "action": "to_drafts", "auto": False, "done": drafts > 0},
            {"key": "uniq", "title": "Сделать тексты уникальными",
             "hint": "Перепишу заголовки и описания, чтобы Avito не считал объявления копиями. "
                     "Уникализировано: %d из %d." % (uniq, drafts),
             "action": "rewrite_draft", "auto": False, "done": drafts > 0 and uniq >= drafts},
            {"key": "fields", "title": "Проверить обязательные поля категории",
             "hint": "Сверю заполнение с требованиями Avito по вашей категории.",
             "action": "card_fields", "auto": True, "done": bool(st.get("fields_ok"))},
            {"key": "check", "title": "Проверить фид валидатором Avito",
             "hint": "Соберу файл из черновиков и прогоню через официальную проверку Avito. "
                     "Живой фид при этом не меняется.",
             "action": "feed_check", "auto": True, "done": st.get("feed_ok") is True},
            {"key": "publish", "title": "Проверить объявления и выполнить выгрузку",
             "hint": "Выгрузку запускаете вы. Я подготовил и проверил — решение за вами.",
             "tab": "listings", "auto": False, "done": False},
        ]
    if scenario_type == "more_leads":
        return [
            {"key": "check_ads", "title": "Проверить объявления",
             "hint": "Смотрим, у каких объявлений есть показы, но нет контактов",
             "tab": "listings", "done": f.get("has_stats") and f.get("dead_items", 0) == 0,
             "auto": False},
            {"key": "kpi", "title": "Задать цель по лидам",
             "hint": "Сколько заявок в день и по какой цене",
             "tab": "marketing", "done": bool(f.get("kpi_set")), "auto": True},
            {"key": "advisor", "title": "Включить Советника по продвижению",
             "hint": "БОРИС держит расход в заданных рамках",
             "tab": "marketing", "done": bool(f.get("advisor_ready")), "auto": True},
            {"key": "weak", "title": "Обновить слабые объявления",
             "hint": "Заголовки, описания и фотографии у объявлений без контактов",
             "tab": "listings", "done": False, "auto": False},
            {"key": "teach", "title": "Обучить БОРИСа бизнесу",
             "hint": "Чем точнее описание, тем лучше тексты и ответы клиентам",
             "tab": "company", "done": bool(f.get("taught")), "auto": True},
            {"key": "sales", "title": "Подключить ИИ-менеджера",
             "hint": "Отвечает в чатах Avito и собирает контакты",
             "tab": "sales", "done": False, "auto": False},
        ]
    if scenario_type == "revive":
        rv = f.get("_revive") or {}
        cands = rv.get("candidates", 0)
        feed_n = f.get("feed_count") or 0
        return [
            {"key": "find", "title": "Найти объявления, которые не работают",
             "hint": "Показы есть, а обращений нет — или объявление давно без просмотров. "
                     "Это только поиск: ничего не снимаю и не меняю.",
             "action": "republish_check", "auto": True,
             "done": bool(rv.get("checked"))},
            {"key": "settings", "title": "Задать, какое объявление считать слабым",
             "hint": "Ниже два порога. По ним я буду отбирать кандидатов в следующий раз.",
             "action": "set_republish_settings", "auto": True,
             "done": bool(rv.get("settings_ready"))},
            {"key": "apply", "title": "Просмотреть список и решить",
             "hint": "Ничего не снимаю сам — это ваше решение. Список выше, "
                     "в кабинете видно каждое объявление целиком: фото, цену, статистику.",
             "tab": "listings", "auto": False, "done": False},
            {"key": "dupes", "title": "Размножить объявления, которые приносят заявки",
             "hint": "Копии с уникальным текстом — больше показов при том же товаре. "
                     "Сначала покажу план, создам только после вашей второй кнопки.",
             "action": "auto_duplicate_run", "auto": False, "done": False},
            {"key": "publish", "title": "Выгрузить обновлённый фид на Avito",
             "hint": "Сейчас в фиде %d %s. Выгрузку запускаете вы — я публикацию не начинаю."
                     % (feed_n, _plural(feed_n, "позиция", "позиции", "позиций")),
             "tab": "listings", "auto": False, "done": False},
        ]

    return []


def scenario_progress(scenario_type, facts, steps_state=None):
    # шаги avito_start читают факты и сохранённые результаты действий
    if scenario_type == "avito_start":
        ss = steps_state or {}
        stt = dict(facts.get("_start") or {})
        stt["reviewed"] = bool((ss.get("review") or {}).get("state") == "done")
        if (ss.get("seller") or {}).get("result"):
            stt["seller_ready"] = True
        stt["gallery_done"] = bool((ss.get("photos") or {}).get("result"))
        stt["fields_ok"] = bool((ss.get("fields") or {}).get("result"))
        if (ss.get("check") or {}).get("result"):
            stt["feed_ok"] = True
        facts = dict(facts)
        facts["_start"] = stt

    # шаги revive читают факты и сохранённые результаты действий
    if scenario_type == "revive":
        rv = dict(facts.get("_revive") or {})
        rv["checked"] = bool((steps_state or {}).get("find", {}).get("result"))
        facts = dict(facts)
        facts["_revive"] = rv
    """Собирает шаги со статусами. Ручная отметка перекрывает только те шаги,
    которые нельзя определить автоматически (auto=False)."""
    steps_state = steps_state or {}
    raw = _steps(scenario_type, facts)
    out, next_marked = [], False
    for st in raw:
        manual = (steps_state.get(st["key"]) or {}).get("state")
        if st.get("done"):
            status = "completed"
        elif manual == "done":
            status = "completed"
        elif manual == "skipped":
            status = "skipped"
        elif manual == "later":
            status = "later"
        elif not next_marked:
            status = "current"
            next_marked = True
        else:
            status = "pending"
        item = dict(st)
        item["status"] = status
        item["manual"] = manual
        # результат последнего действия — чтобы клиент видел его после перезагрузки
        item["result"] = (steps_state.get(st["key"]) or {}).get("result")
        item["result_at"] = (steps_state.get(st["key"]) or {}).get("result_at")
        item.pop("done", None)
        out.append(item)
    total = len(out)
    done = sum(1 for i in out if i["status"] in ("completed", "skipped"))
    return {
        "steps": out,
        "total": total,
        "done": done,
        "percent": int(round(done * 100.0 / total)) if total else 0,
        "current": next((i["key"] for i in out if i["status"] == "current"), None),
    }


# --------------------------------------------------- что требует внимания

_MSK_OFFSET_HOURS = 3   # проект живёт по Москве (support.py: MSK_OFFSET=3), сервер в UTC


def _today_msk():
    """Календарная дата по Москве. Без сдвига с 00:00 до 03:00 МСК оплата
    «действует по сегодня» считалась бы истёкшей на сутки раньше."""
    from datetime import datetime, timedelta
    return (datetime.utcnow() + timedelta(hours=_MSK_OFFSET_HOURS)).date()


def _payment_active(payment_status):
    """Действует ли ручная оплата: paid_at + period_days >= сегодня (МСК).
    Возвращает (действует, дата_окончания|None).
    Любой мусор в данных даёт False, но НИКОГДА не исключение — overview
    не должен падать из-за кривой даты в storage."""
    from datetime import date, timedelta
    try:
        p = payment_status or {}
        paid_at = str(p.get("paid_at") or "").strip()
        days = int(p.get("period_days") or 0)
        if not paid_at or days <= 0:
            return False, None
        until = date.fromisoformat(paid_at[:10]) + timedelta(days=days)
        return until >= _today_msk(), until.isoformat()
    except Exception:
        return False, None


def revive_facts(account_id, db=None):
    """Факты сценария «Оживить кабинет». Никакой своей логики отбора —
    зовём готовый republish_check из avito.py. Импорт ленивый: avito.py
    большой и активно правится, на загрузке модуля его дёргать не надо."""
    out = {"candidates": 0, "candidates_list": [], "settings": None,
           "settings_ready": False, "error": ""}
    own = db is None
    if own:
        db = SessionLocal()
    try:
        st = _load(db, account_id, "republish_settings", None)
        out["settings"] = st
        out["settings_ready"] = bool(st)
        try:
            from app.api.avito import republish_check
            r = republish_check(account_id) or {}
            cands = r.get("candidates") or []
            out["candidates"] = len(cands)
            out["candidates_list"] = [
                {"id": c.get("id"), "title": _short_title(c.get("title")),
                 "reason": c.get("reason"), "views": c.get("views"),
                 "contacts": c.get("contacts")} for c in cands[:20]]
            out["disabled"] = bool(r.get("disabled"))
        except Exception as e:
            out["error"] = str(e)[:200]
    finally:
        if own:
            db.close()
    return out


def start_facts(account_id, db=None):
    """Факты сценария «Запустить Авито с нуля». Своей логики нет — читаем
    то, что уже сложили парсер и генератор черновиков. Импорт ленивый:
    avito.py большой и активно правится."""
    out = {"has_keys": False, "parsed": 0, "drafts": 0,
           "uniquified": 0, "feed_ok": None, "error": ""}
    own = db is None
    if own:
        db = SessionLocal()
    try:
        try:
            from app.models.account import Account
            acc = db.query(Account).filter(Account.account_id == account_id).first()
            out["has_keys"] = bool(acc and getattr(acc, "avito_client_id", None))
        except Exception as e:
            out["error"] = str(e)[:200]

        sd = _load(db, account_id, "seller_defaults", None) or {}
        out["seller_ready"] = bool(isinstance(sd, dict) and len(sd) >= 3)
        out["seller_count"] = len(sd) if isinstance(sd, dict) else 0

        pp = _load(db, account_id, "parsed_products", None)
        if isinstance(pp, list):
            out["parsed"] = len(pp)
        elif isinstance(pp, dict):
            out["parsed"] = len(pp.get("products") or [])

        try:
            from app.api.avito import _load_drafts
            dr = _load_drafts(account_id) or []
            out["drafts"] = len(dr)
            # уникализированным считаем черновик со спинтаксом в заголовке
            out["uniquified"] = sum(
                1 for d in dr
                if "{" in str((d or {}).get("title", "")) and "|" in str((d or {}).get("title", "")))
        except Exception as e:
            if not out["error"]:
                out["error"] = str(e)[:200]
    finally:
        if own:
            db.close()
    return out


def _short_title(t):
    """Заголовки в фиде — спинтакс {вар1|вар2|...}. Клиенту показываем первый вариант."""
    t = str(t or "")
    if "{" in t and "|" in t:
        head = t.split("{")[0].strip()
        first = t.split("{")[1].split("|")[0].strip()
        return (head + " " + first).strip()[:70]
    return t[:70]


def _plural(n, one, few, many):
    """Русское склонение: 1 объявление, 2 объявления, 5 объявлений."""
    n = abs(int(n)); d10 = n % 10; d100 = n % 100
    if d10 == 1 and d100 != 11:
        return one
    if 2 <= d10 <= 4 and not (12 <= d100 <= 14):
        return few
    return many


def attention(facts):
    """Карточки главной. КАЖДАЯ строится на реальном поле — см. collect_facts.
    priority: critical | important | opportunity | info"""
    a, f = [], facts

    if not f.get("avito_connected"):
        a.append({"priority": "critical", "key": "no_avito",
                  "title": "Avito не подключён",
                  "text": "Без ключей Avito API я не вижу ваши объявления и не могу работать.",
                  "action": "Подключить Avito", "route": "/agency"})

    if not f.get("has_active_access"):
        a.append({"priority": "critical", "key": "no_access",
                  "title": "Не подтверждена активная оплата",
                  "text": "Подключите тариф или напишите нам, чтобы активировать доступ.",
                  "action": "Выбрать тариф", "tab": "billing"})

    # Только если готовых БОЛЬШЕ, чем уже выгруженных в фид: иначе это те же
    # самые объявления, которые давно на Avito, и карточка врёт клиенту.
    not_exported = (f.get("ready_count") or 0) - (f.get("feed_count") or 0)
    if not_exported > 0:
        a.append({"priority": "opportunity", "key": "drafts",
                  "title": "К проверке готово %d %s" % (not_exported, _plural(not_exported, "объявление", "объявления", "объявлений")),
                  "text": "Проверьте их и отправьте на Avito — публикацию запускаете вы.",
                  "action": "Открыть объявления", "tab": "listings"})

    if f.get("avito_connected") and not f.get("has_category"):
        a.append({"priority": "important", "key": "no_category",
                  "title": "Категория Avito ещё не определена",
                  "text": "Я подберу её сам по вашей нише или сайту — нужно только подтвердить.",
                  "action": "Определить категорию", "tab": "listings"})

    if not f.get("taught"):
        a.append({"priority": "important", "key": "not_taught",
                  "title": "БОРИС ещё не изучил ваш бизнес",
                  "text": "Добавьте описание и преимущества — это улучшит объявления, "
                          "баннеры, посты и ответы клиентам.",
                  "action": "Обучить БОРИСа", "tab": "company"})

    if f.get("has_stats") and f.get("dead_items"):
        a.append({"priority": "important", "key": "dead_items",
                  "title": "%d %s с показами, но без контактов" % (f["dead_items"], _plural(f["dead_items"], "объявление", "объявления", "объявлений")),
                  "text": "Показы идут, обращений нет — обычно дело в фотографиях, "
                          "заголовке или цене.",
                  "action": "Посмотреть объявления", "tab": "listings"})

    if f.get("kpi_set") and not f.get("advisor_ready"):
        a.append({"priority": "opportunity", "key": "advisor",
                  "title": "Советник по продвижению не активирован",
                  "text": "Укажите максимальную цену контакта и дневной бюджет — "
                          "я буду держать расход в этих рамках.",
                  "action": "Настроить Советника", "tab": "marketing"})

    if f.get("avito_connected") and f.get("active_items") == 0 \
            and not f.get("ready_count") and f.get("has_stats"):
        a.append({"priority": "critical", "key": "no_active",
                  "title": "Нет активных объявлений",
                  "text": "Создайте первое объявление или опубликуйте готовые черновики.",
                  "action": "Создать объявление", "tab": "listings"})

    if f.get("unread_notifications"):
        a.append({"priority": "info", "key": "notifications",
                  "title": "Непрочитанных уведомлений: %d" % f["unread_notifications"],
                  "text": "Есть сообщения от БОРИСа по вашему аккаунту.",
                  "action": "Открыть", "tab": "settings"})

    order = {"critical": 0, "important": 1, "opportunity": 2, "info": 3}
    a.sort(key=lambda x: order.get(x["priority"], 9))
    return a[:5]


def recommendations(facts, db=None):
    """Рекомендации из СУЩЕСТВУЮЩЕГО механизма: тексты NUDGES под стадию
    плюс последние уведомления. Своего движка не заводим."""
    out = []
    stage = facts.get("stage", 1)
    try:
        from app.onboarding import NUDGES
        title, body = NUDGES.get(stage, ("", ""))
        if title:
            out.append({"priority": "important", "source": "onboarding",
                        "title": title, "text": body})
    except Exception:
        pass
    for n in facts.get("notifications", [])[:3]:
        out.append({"priority": "info", "source": "notification",
                    "title": "Сообщение БОРИСа",
                    "text": str(n.get("text") or "")[:400],
                    "ts": n.get("ts"), "read": bool(n.get("read"))})
    return out
