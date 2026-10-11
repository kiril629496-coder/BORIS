# -*- coding: utf-8 -*-
"""Исполнители операций Command Center (этап 2).

Каждый исполнитель — обычная функция сервисного слоя. HTTP-вызовов в
собственные ручки здесь нет и быть не должно: Command Center и endpoint
обязаны сходиться в один слой.

Подписи: fn(db, user, ctx) -> dict, где ctx = {entities, account_id,
command_id, operation}. Возврат: status, answer, changes, actions.
"""
import json

from app.services import command_executor as E

ROUTES = (
    (("сообщен", "диалог", "инбокс", "переписк", "чат"), "/messages", "Сообщения", "Здесь BORIS собирает диалоги и помогает отвечать клиентам."),
    (("аналитик", "статистик"), "/dashboard/analytics", "Аналитика", "Здесь можно подробно посмотреть показатели и динамику."),
    (("баннер",), "/dashboard/campaign/banners", "Баннеры", "Здесь можно создавать и выбирать баннеры для объявлений."),
    (("фотостуд", "фото", "картинк", "изображен"), "/dashboard/campaign/photo", "Фотостудия", "Здесь можно подготовить изображения для объявлений."),
    (("подключ", "аккаунт авито"), "/dashboard/connect-avito", "Подключить аккаунты Авито", "Подключите Avito API, чтобы BORIS мог работать с аккаунтом."),
    (("объявлен", "кампан", "реклам", "продвижен"), "/dashboard/campaign", "Объявления", "Здесь создаются, проверяются и готовятся к публикации объявления."),
    (("маркетинг",), "/dashboard/marketing", "Маркетинг", "Здесь BORIS следит за KPI и улучшает рекламу."),
    (("продаж", "crm", "срм"), "/dashboard/crm", "Продажи", "Здесь находятся сделки, клиенты и задачи."),
    (("соцсет", "социаль", "вконтакте", "телеграм", "постинг", "посты"), "/social", "Социальные сети", "Здесь BORIS готовит и публикует контент."),
    (("знани", "память", "факт"), "/memory", "База знаний", "Здесь хранится то, что BORIS должен знать о бизнесе."),
    (("режим борис", "режим работы"), "/dashboard/work-mode", "Режим BORISа", "Здесь настраивается уровень самостоятельности BORIS."),
    (("ии-сотруд", "ai-сотруд"), "/ai", "ИИ-сотрудники", "Здесь настраиваются виртуальные сотрудники BORIS."),
    (("сайт",), "/dashboard/sitebuild", "Создание сайтов", "Здесь можно создать сайт для бизнеса."),
    (("тариф", "оплат"), "/dashboard/billing", "Тариф и оплата", "Здесь находятся тариф и оплата BORIS."),
    (("данные компан",), "/dashboard/company", "Данные компании", "Здесь заполняются данные компании для работы BORIS."),
    (("сотрудник", "доступ", "пользовател"), "/team", "Пользователи и доступы", "Здесь настраиваются сотрудники и права доступа."),
    (("сценар",), "/dashboard/scenarios", "Бизнес-сценарии", "Здесь можно запускать готовые сценарии BORIS."),
    (("главн", "домой", "кабинет"), "/dashboard/command-center", "Главная", "Здесь видна общая картина по аккаунту."),
)

DEFAULT_ROUTE = ("/dashboard/home", "Главная")


def resolve_route(section):
    s = str(section or "").strip().lower()
    if not s:
        return DEFAULT_ROUTE
    for keys, route, title, hint in ROUTES:
        for k in keys:
            if k in s:
                return route, title, hint
    return DEFAULT_ROUTE[0], DEFAULT_ROUTE[1], "Здесь видна общая картина по аккаунту."


def op_navigate(db, user, ctx):
    section = (ctx.get("entities") or {}).get("section")
    route, title, hint = resolve_route(section)
    return {"status": "ok",
            "answer": "Открываю раздел «%s». %s" % (title, hint),
            "route": route, "route_title": title, "hint": hint}


def op_smalltalk(db, user, ctx):
    topic = str((ctx.get("entities") or {}).get("topic") or "").strip().lower()
    if topic == "audit_help":
        from app.models.storage import Storage
        account_id = str(ctx.get("account_id") or getattr(user, "account_id", "") or "").strip()
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "viral_gateway_context",
        ).first() if account_id else None
        try:
            audit_ctx = json.loads(row.value or "{}") if row else {}
        except Exception:
            audit_ctx = {}
        score = audit_ctx.get("score")
        connected = bool(audit_ctx.get("connected"))
        problems = [str(x or "") for x in (audit_ctx.get("main_problems") or []) if str(x or "").strip()]
        if score is None:
            return {
                "status": "ok",
                "answer": "Последний аудит пока не найден. Откройте Audit Gateway и завершите первый аудит.",
                "route": "/audit",
                "route_title": "Мой аудит",
            }
        labels = {
            "unanswered": "клиенты без ответа",
            "response": "скорость ответа",
            "slow_response": "скорость ответа",
            "weak_ads": "эффективность объявлений",
            "followup": "follow-up",
            "reactivation": "клиенты для возврата",
            "kpi_gap": "выполнение KPI",
        }
        human = [labels.get(x, x) for x in problems]
        suffix = (" Главные точки: " + ", ".join(human) + ".") if human else ""
        return {
            "status": "ok",
            "answer": (
                f"Я помню ваш последний BORIS Score: {int(score)}/100. "
                + ("Он рассчитан на реальных данных BORIS." if connected else "Это предварительная оценка.")
                + suffix
                + " Могу открыть аудит или перейти к первому инструменту для исправления."
            ),
            "route": "/audit",
            "route_title": "Мой аудит",
            "hint": "Контекст аудита сохранён в BORIS.",
        }
    if topic == "onboarding_help":
        from sqlalchemy import text as _t
        acc = ctx.get("account_id")
        row = db.execute(_t(
            "select avito_client_id, avito_client_secret, avito_user_id, "
            "company_description, client_goal from accounts where account_id=:a"
        ), {"a": acc}).fetchone()
        connected = bool(row and row[0] and row[1] and row[2])
        company_ready = bool(row and str(row[3] or "").strip())
        goal_ready = bool(row and str(row[4] or "").strip())
        camp = db.execute(_t(
            "select id, status from campaigns where default_account_id=:a order by id desc limit 1"
        ), {"a": acc}).fetchone()
        campaign_exists = bool(camp)
        steps = [
            {"n": 1, "title": "Подключить аккаунт Авито", "route": "/dashboard/connect-avito", "hint": "Проверить Client ID, Client Secret и доступ API.", "state": "done" if connected else "todo"},
            {"n": 2, "title": "Заполнить данные бизнеса", "route": "/dashboard/company", "hint": "BORIS использует их в текстах, рекламе и ответах.", "state": "done" if company_ready else "todo"},
            {"n": 3, "title": "Выбрать цель и KPI", "route": "/dashboard/marketing", "hint": "Задать план лидов, CPL и бизнес-цель.", "state": "done" if goal_ready else "todo"},
            {"n": 4, "title": "Создать и проверить объявления", "route": "/dashboard/campaign", "hint": "Продолжить текущую кампанию, проверить заголовки, тексты и параметры.", "state": "active" if campaign_exists else "todo"},
            {"n": 5, "title": "Подготовить изображения", "route": "/dashboard/campaign/photo", "hint": "Создать или подобрать релевантные фото.", "state": "todo"},
            {"n": 6, "title": "Подготовить баннеры", "route": "/dashboard/campaign/banners", "hint": "Выбрать готовые баннеры перед публикацией.", "state": "todo"},
            {"n": 7, "title": "Проверить и отправить в Авито", "route": "/dashboard/campaign", "hint": "Финальная проверка и подтверждение публикации.", "state": "todo"},
        ]
        next_step = next((st for st in steps if st["state"] != "done"), steps[-1])
        done_count = sum(1 for st in steps if st["state"] == "done")
        return {
            "status": "ok",
            "answer": (
                "Проверил текущую настройку. Готово %d из %d базовых шагов. Следующий шаг — %s. %s"
                % (done_count, len(steps), next_step["title"].lower(), next_step["hint"])
            ),
            "route": next_step["route"], "route_title": next_step["title"],
            "hint": next_step["hint"], "steps": steps, "next_step": next_step["n"],
        }
    if topic == "clarify":
        return {
            "status": "ok",
            "answer": (
                "Уточните, пожалуйста, что хотите получить. Я могу проверить аккаунт, показать цифры, "
                "найти клиента, помочь создать объявления или подсказать следующий шаг настройки."
            ),
        }
    return {"status": "ok",
            "answer": "На связи. Скажите, что нужно сделать — "
                      "найти клиента, создать объявление или показать цифры."}


E.register("ui.navigate", op_navigate)
E.register("chat.smalltalk", op_smalltalk)


# --- client_search -----------------------------------------------------
# Ищем по карточкам messenger_leads: имя, компания, объявление. В модель
# ничего не уходит — исполнитель работает только с базой.

def op_leads_search(db, user, ctx):
    from sqlalchemy import text as _t

    acc = ctx.get("account_id")
    q = str((ctx.get("entities") or {}).get("client") or "").strip()
    like = "%" + q + "%"
    rows = db.execute(_t(
        "select avito_chat_id, coalesce(contact_name,''), coalesce(company,''),"
        " coalesce(item_title,''), coalesce(stage,''), has_phone, last_msg_at"
        " from messenger_leads"
        " where account_id = :a"
        "   and (:empty = 1 or contact_name ilike :lk or company ilike :lk"
        "        or item_title ilike :lk)"
        " order by last_msg_at desc nulls last limit 10"),
        {"a": acc, "empty": 1 if not q else 0, "lk": like}).fetchall()

    found = []
    for r in rows:
        name = r[1] or r[2] or r[3] or "Без имени"
        lm = r[6]
        found.append({"chat_id": r[0], "name": name, "company": r[2],
                      "item": r[3], "stage": r[4], "has_phone": bool(r[5]),
                      "last_msg_at": (lm.isoformat() if hasattr(lm, "isoformat")
                                      else (int(lm) if lm is not None else None))})

    if not found:
        return {"status": "ok",
                "answer": ("По запросу «%s» никого не нашёл" % q) if q
                          else "В этом аккаунте пока нет карточек клиентов",
                "clients": [], "route": "/messages", "route_title": "Сообщения",
                "hint": "Откройте сообщения, чтобы посмотреть реальные диалоги."}

    head = ("Нашёл %d по запросу «%s»" % (len(found), q)) if q \
        else "Последние %d клиентов" % len(found)
    lines = []
    for c in found[:5]:
        lines.append("• %s — %s%s" % (
            c["name"], c["item"][:40] or "без объявления",
            " · телефон есть" if c["has_phone"] else ""))
    return {"status": "ok", "answer": head + ":\n" + "\n".join(lines),
            "clients": found, "route": "/messages", "route_title": "Сообщения",
            "hint": "Откройте сообщения, чтобы продолжить работу с клиентом."}


E.register("crm.leads_search", op_leads_search)


# --- task_create / reminder_create --------------------------------------
# Обе фразы ведут в одну операцию: напоминание — это задача со временем.

def normalize_task(entities):
    from app.services import crm_service as C

    ent = entities or {}
    d = str(ent.get("date") or "").strip()[:10]
    t = str(ent.get("time") or "").strip()[:5]
    title = str(ent.get("title") or ent.get("text") or "").strip()
    ttype = str(ent.get("task_type") or "").strip()
    if ttype not in C.TASK_TYPES:
        ttype = "позвонить" if "звон" in title.lower() else "другое"
    client = str(ent.get("client") or "").strip()
    if not title:
        # Модель не всегда кладёт действие в title. Собираем сами, иначе
        # в CRM попадёт бесполезный заголовок «Задача».
        if ttype != "другое":
            title = ttype[:1].upper() + ttype[1:]
            if client:
                title += " — " + client
        elif client:
            title = "Задача по клиенту " + client
    return {"date": d, "time": t, "title": (title or "Задача")[:300],
            "type": ttype, "client": client}


def _human_task(n):
    when = n["date"] or "дата не распознана"
    if n["time"]:
        when += " в " + n["time"]
    who = (" по клиенту " + n["client"]) if n["client"] else ""
    return "Задача «%s»%s на %s (%s)" % (n["title"], who, when, n["type"])


def prev_task_create(db, user, ctx):
    n = normalize_task(ctx.get("entities"))
    return {"summary": _human_task(n),
            "changes": [{"entity": "crm_tasks", "field": "новая задача",
                         "after": _human_task(n), "human": _human_task(n)}]}


def op_task_create(db, user, ctx):
    from app.services import crm_service as C

    n = normalize_task(ctx.get("entities"))
    if not n["date"]:
        return {"status": "failed", "error_code": "bad_date",
                "answer": "Не понял, на какую дату ставить задачу"}
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    try:
        new_id = C.create_task(db, uid, ctx.get("account_id"), n["title"],
                               n["date"], due_time=n["time"],
                               task_type=n["type"],
                               comment=("Клиент: " + n["client"]) if n["client"] else "")
    except ValueError as e:
        return {"status": "failed", "error_code": str(e),
                "answer": "Не удалось создать задачу: %s" % e}
    return {"status": "ok", "answer": "Готово. " + _human_task(n),
            "task_id": new_id,
            "changes": [{"entity": "crm_tasks", "field": "id",
                         "after": new_id, "human": _human_task(n)}]}


E.register("crm.task_create", op_task_create)
E.register_preview("crm.task_create", prev_task_create)


# --- crm_add_note -------------------------------------------------------
# Заметка ложится в карточку messenger_leads. Клиента ищем сначала по
# открытому на экране объекту (entity_context), потом по имени из команды.

def _find_lead(db, ctx):
    from sqlalchemy import text as _t

    acc = ctx.get("account_id")
    et, eid = ctx.get("entity_type"), ctx.get("entity_id")
    cols = ("select id, coalesce(contact_name,''), coalesce(company,''),"
            " coalesce(item_title,''), coalesce(note,'') from messenger_leads")
    if et == "dialog" and eid:
        row = db.execute(_t(
            cols + " where account_id=:a and avito_chat_id=:c limit 1"),
            {"a": acc, "c": str(eid)}).fetchone()
        if row:
            return row
    if et == "lead" and eid:
        row = db.execute(_t(
            cols + " where account_id=:a and id=:i limit 1"),
            {"a": acc, "i": int(eid)}).fetchone()
        if row:
            return row
    q = str((ctx.get("entities") or {}).get("client") or "").strip()
    if not q:
        return None
    return db.execute(_t(
        cols + " where account_id=:a and (contact_name ilike :lk"
        "   or company ilike :lk or item_title ilike :lk)"
        " order by last_msg_at desc nulls last limit 1"),
        {"a": acc, "lk": "%" + q + "%"}).fetchone()


def _lead_title(lead):
    """Подпись карточки: имя, компания, объявление — что заполнено."""
    return lead[1] or lead[2] or lead[3] or ("карточка #%s" % lead[0])


def _note_text(ctx):
    ent = ctx.get("entities") or {}
    return str(ent.get("text") or ent.get("title") or "").strip()[:1000]


def prev_note_add(db, user, ctx):
    lead = _find_lead(db, ctx)
    txt = _note_text(ctx)
    who = _lead_title(lead) if lead else \
        (str((ctx.get("entities") or {}).get("client") or "") or "клиент не определён")
    s = "Заметка для «%s»: %s" % (who[:60], txt or "текст не распознан")
    return {"summary": s,
            "changes": [{"entity": "messenger_leads", "field": "note",
                         "before": (lead[4][:80] if lead else None),
                         "after": txt, "human": s}]}


def op_note_add(db, user, ctx):
    import datetime as _d
    from sqlalchemy import text as _t

    lead = _find_lead(db, ctx)
    txt = _note_text(ctx)
    if not txt:
        return {"status": "failed", "error_code": "empty_note",
                "answer": "Не понял текст заметки"}
    if not lead:
        return {"status": "failed", "error_code": "lead_not_found",
                "answer": "Не нашёл, к какому клиенту привязать заметку"}
    stamp = _d.date.today().strftime("%d.%m")
    line = "%s: %s" % (stamp, txt)
    db.execute(_t(
        "update messenger_leads set note = case when coalesce(note,'') = ''"
        " then :n else note || chr(10) || :n end, updated_at = now()"
        " where id = :i"), {"n": line, "i": lead[0]})
    db.commit()
    who = _lead_title(lead)[:60]
    return {"status": "ok", "answer": "Записал заметку для «%s»" % who,
            "lead_id": lead[0],
            "changes": [{"entity": "messenger_leads", "field": "note",
                         "after": line, "human": "Заметка для «%s»" % who}]}


E.register("crm.lead_note_add", op_note_add)
E.register_preview("crm.lead_note_add", prev_note_add)


# --- crm_create_or_update ----------------------------------------------
# Правим карточку клиента. Имена колонок берём ТОЛЬКО из белого списка —
# модель не может дотянуться до произвольного поля таблицы.

LEAD_FIELDS = {
    "contact_name": "контактное лицо",
    "company": "компания",
    "object_place": "адрес или объект",
    "rent_term": "срок аренды",
    "need_type": "что нужно",
    "cost_rub": "сумма, ₽",
}


def _lead_changes(ctx):
    ent = ctx.get("entities") or {}
    out = {}
    for key in LEAD_FIELDS:
        v = ent.get(key)
        if v is None or str(v).strip() == "":
            continue
        if key == "cost_rub":
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if not digits:
                continue
            out[key] = int(digits[:9])
        else:
            out[key] = str(v).strip()[:200]
    return out


def _lead_current(db, lead_id, keys):
    from sqlalchemy import text as _t

    if not keys:
        return {}
    cols = ", ".join(sorted(keys))
    row = db.execute(_t("select " + cols + " from messenger_leads where id=:i"),
                     {"i": lead_id}).fetchone()
    return dict(zip(sorted(keys), row)) if row else {}


def prev_lead_update(db, user, ctx):
    lead = _find_lead(db, ctx)
    ch = _lead_changes(ctx)
    if not ch:
        return {"summary": "Не понял, какие поля карточки менять", "changes": []}
    who = _lead_title(lead)[:60] if lead else "клиент не определён"
    cur = _lead_current(db, lead[0], ch.keys()) if lead else {}
    items = []
    for k, v in ch.items():
        items.append({"entity": "messenger_leads", "field": LEAD_FIELDS[k],
                      "before": cur.get(k), "after": v,
                      "human": "%s: %s" % (LEAD_FIELDS[k], v)})
    return {"summary": "Карточка «%s» — %s" % (
        who, "; ".join(i["human"] for i in items)), "changes": items}


def op_lead_update(db, user, ctx):
    from sqlalchemy import text as _t

    lead = _find_lead(db, ctx)
    ch = _lead_changes(ctx)
    if not ch:
        return {"status": "failed", "error_code": "nothing_to_update",
                "answer": "Не понял, какие поля карточки менять"}
    if not lead:
        return {"status": "failed", "error_code": "lead_not_found",
                "answer": "Не нашёл, какую карточку править"}
    sets = ", ".join("%s = :%s" % (k, k) for k in sorted(ch))
    params = dict(ch)
    params["i"] = lead[0]
    db.execute(_t("update messenger_leads set " + sets +
                  ", updated_at = now() where id = :i"), params)
    db.commit()
    who = _lead_title(lead)[:60]
    return {"status": "ok",
            "answer": "Обновил карточку «%s»: %s" % (
                who, "; ".join("%s — %s" % (LEAD_FIELDS[k], ch[k]) for k in sorted(ch))),
            "lead_id": lead[0],
            "changes": [{"entity": "messenger_leads", "field": LEAD_FIELDS[k],
                         "after": ch[k]} for k in sorted(ch)]}


E.register("crm.lead_update", op_lead_update)
E.register_preview("crm.lead_update", prev_lead_update)


# --- analytics_query ----------------------------------------------------
# Срезы статистики лежат в storage под ключами daily_stats:ГГГГ-ММ-ДД,
# по 345 КБ на аккаунт и день. Разбор делаем В POSTGRES через
# jsonb_array_elements — тянуть мегабайты в Python нельзя.

def _last_stat_key(db, account_id):
    from sqlalchemy import text as _t

    row = db.execute(_t(
        "select key from storage where account_id=:a"
        "   and key like 'daily_stats:%' order by key desc limit 1"),
        {"a": account_id}).fetchone()
    return row[0] if row else None


def _agg(db, account_id, keys):
    """Суммы по срезам: объявлений, активных, просмотры, контакты."""
    from sqlalchemy import text as _t

    row = db.execute(_t(
        "select count(*), count(*) filter (where e->>'status' = 'active'),"
        " coalesce(sum(coalesce((e->>'views')::int, 0)), 0),"
        " coalesce(sum(coalesce((e->>'contacts')::int, 0)), 0)"
        " from storage s,"
        " lateral jsonb_array_elements((s.value::jsonb)->'items') e"
        " where s.account_id = :a and s.key = any(:keys)"),
        {"a": account_id, "keys": list(keys)}).fetchone()
    return tuple(int(x or 0) for x in row) if row else (0, 0, 0, 0)


def _conv(views, contacts):
    return round(contacts * 100.0 / views, 2) if views else 0.0


def _stat_line(label, n, active, views, contacts):
    return ("%s: объявлений %d (активных %d), просмотров %d, "
            "обращений %d, конверсия %.2f%%"
            % (label, n, active, views, contacts, _conv(views, contacts)))


def op_analytics_daily(db, user, ctx):
    acc = ctx.get("account_id")
    ent = ctx.get("entities") or {}
    requested_day = str(ent.get("date") or "").strip()
    key = ("daily_stats:" + requested_day) if requested_day else _last_stat_key(db, acc)
    if not key:
        return {"status": "ok",
                "answer": "По этому аккаунту статистика ещё не собиралась",
                "route": "/dashboard/analytics", "route_title": "Аналитика"}
    # Проверяем наличие именно запрошенного дня. Нельзя выдавать вчера за «сегодня».
    if requested_day:
        from sqlalchemy import text as _t
        exists = db.execute(_t("select 1 from storage where account_id=:a and key=:k limit 1"),
                            {"a": acc, "k": key}).fetchone()
        if not exists:
            last = _last_stat_key(db, acc)
            suffix = (" Последние доступные данные — за " + last.split(":", 1)[1] + ".") if last else ""
            return {"status": "ok",
                    "answer": "За %s данные ещё не собраны.%s" % (requested_day, suffix),
                    "period": requested_day, "views": 0, "contacts": 0, "conversion": 0.0,
                    "route": "/dashboard/analytics", "route_title": "Аналитика",
                    "hint": "Откройте аналитику, чтобы посмотреть доступные периоды."}
    day = key.split(":", 1)[1]
    n, active, views, contacts = _agg(db, acc, [key])
    return {"status": "ok", "answer": _stat_line("За " + day, n, active, views, contacts),
            "period": day, "views": views, "contacts": contacts,
            "conversion": _conv(views, contacts),
            "route": "/dashboard/analytics", "route_title": "Аналитика",
            "hint": "Откройте аналитику, чтобы посмотреть показатели подробнее."}


def op_analytics_week(db, user, ctx):
    import datetime as _d

    acc = ctx.get("account_id")
    key = _last_stat_key(db, acc)
    if not key:
        return {"status": "ok",
                "answer": "По этому аккаунту статистика ещё не собиралась"}
    last = _d.date.fromisoformat(key.split(":", 1)[1])
    keys = ["daily_stats:" + (last - _d.timedelta(days=i)).isoformat()
            for i in range(7)]
    n, active, views, contacts = _agg(db, acc, keys)
    return {"status": "ok",
            "answer": ("За 7 дней по %s: просмотров %d, обращений %d, "
                       "конверсия %.2f%%" % (last.isoformat(), views, contacts,
                                             _conv(views, contacts))),
            "period": "week", "views": views, "contacts": contacts,
            "conversion": _conv(views, contacts),
            "route": "/dashboard/analytics", "route_title": "Аналитика",
            "hint": "Откройте аналитику, чтобы посмотреть динамику за период."}


def op_analytics_accounts(db, user, ctx):
    """Сводка по ВСЕМ аккаунтам владельца за последний собранный день.
    Недельную глубину сюда не берём: 20 аккаунтов x 7 дней это ~50 МБ
    разбора на одну команду. Для периода нужен предрасчитанный агрегат."""
    from sqlalchemy import text as _t
    from app.services import command_limits as _L

    owner = _L.resolve_billing_owner(db, ctx.get("account_id"))
    if not owner:
        return {"status": "failed", "error_code": "no_billing_owner",
                "answer": "У аккаунта не задан владелец"}
    kind, oid = owner
    key = _last_stat_key(db, ctx.get("account_id"))
    if not key:
        return {"status": "ok", "answer": "Статистика ещё не собиралась"}

    rows = db.execute(_t(
        "select s.account_id, coalesce(a.name, s.account_id),"
        " coalesce(sum(coalesce((e->>'views')::int, 0)), 0),"
        " coalesce(sum(coalesce((e->>'contacts')::int, 0)), 0)"
        " from storage s join accounts a on a.account_id = s.account_id,"
        " lateral jsonb_array_elements((s.value::jsonb)->'items') e"
        " where a.owner_user_id = :u and s.key = :k"
        " group by 1, 2 order by 4 desc, 3 desc"),
        {"u": oid, "k": key}).fetchall()
    if not rows:
        return {"status": "ok", "answer": "За %s срезов по аккаунтам нет"
                % key.split(":", 1)[1]}

    total_v = sum(int(r[2]) for r in rows)
    total_c = sum(int(r[3]) for r in rows)
    lines = ["Итого по %d аккаунтам за %s: просмотров %d, обращений %d, "
             "конверсия %.2f%%" % (len(rows), key.split(":", 1)[1], total_v,
                                   total_c, _conv(total_v, total_c))]
    for r in rows[:8]:
        lines.append("• %s — обращений %d, просмотров %d"
                     % (str(r[1])[:34], int(r[3]), int(r[2])))
    return {"status": "ok", "answer": "\n".join(lines),
            "accounts": [{"account_id": r[0], "name": r[1],
                          "views": int(r[2]), "contacts": int(r[3])}
                         for r in rows]}


def op_analytics_revenue(db, user, ctx):
    """Себестоимость AI по категориям за 30 дней. ТОЛЬКО внутренний оператор BORIS.

    Независимая проверка до любого SQL: реестр и исполнитель защищают данные
    порознь, чтобы прямой вызов не обошёл право.
    """
    from app.services.command_policy import _sys_role as _sr, OWNER as _OW
    if _sr(user) != _OW:
        return {"status": "failed", "error_code": "forbidden",
                "answer": "Внутренняя экономика платформы недоступна"}
    from sqlalchemy import text as _t
    from app.usage import category_of
    from app.services import command_limits as _L

    owner = _L.resolve_billing_owner(db, ctx.get("account_id"))
    if not owner:
        return {"status": "failed", "error_code": "no_billing_owner",
                "answer": "У аккаунта не задан владелец"}
    kind, oid = owner
    rows = db.execute(_t(
        "select u.operation, sum(u.cost_rub), count(*)"
        " from api_usage u join accounts a on a.account_id = u.account_id"
        " where a.owner_user_id = :u"
        "   and u.created_at > now() - interval '30 days'"
        " group by 1"), {"u": oid}).fetchall()
    if not rows:
        return {"status": "ok", "answer": "За 30 дней расходов не записано"}

    by_cat = {}
    for op, cost, cnt in rows:
        c = category_of(op)
        agg = by_cat.setdefault(c, [0.0, 0])
        agg[0] += float(cost or 0)
        agg[1] += int(cnt or 0)
    total = sum(v[0] for v in by_cat.values())
    lines = ["Себестоимость AI за 30 дней: %.2f ₽" % total]
    for c, v in sorted(by_cat.items(), key=lambda x: -x[1][0])[:8]:
        lines.append("• %s — %.2f ₽ (%d вызовов)" % (c, v[0], v[1]))
    return {"status": "ok", "answer": "\n".join(lines),
            "total_rub": round(total, 2),
            "by_category": {c: round(v[0], 2) for c, v in by_cat.items()}}


E.register("analytics.daily", op_analytics_daily)
E.register("analytics.week", op_analytics_week)
E.register("analytics.accounts", op_analytics_accounts)
E.register("analytics.revenue", op_analytics_revenue)


def op_analytics_manager(db, user, ctx):
    """Качество звонков за 30 дней: сколько разобрано, средняя оценка и
    какие пункты чек-листа проваливаются чаще всего.

    ⚠ Разреза ПО МЕНЕДЖЕРАМ здесь нет и быть не может: в call_analysis
    не хранится, кто вёл звонок. Отдаём то, что в данных есть."""
    from sqlalchemy import text as _t
    from app.services import command_limits as _L

    owner = _L.resolve_billing_owner(db, ctx.get("account_id"))
    if not owner:
        return {"status": "failed", "error_code": "no_billing_owner",
                "answer": "У аккаунта не задан владелец"}
    kind, oid = owner

    head = db.execute(_t(
        "select count(*), avg(nullif(ca.analysis->>'score','')::numeric),"
        " coalesce(sum(ca.minutes), 0)"
        " from call_analysis ca join accounts a on a.account_id = ca.account_id"
        " where a.owner_user_id = :u"
        "   and ca.created_at > now() - interval '30 days'"),
        {"u": oid}).fetchone()
    total = int(head[0] or 0)
    if not total:
        return {"status": "ok",
                "answer": "За 30 дней разборов звонков нет"}
    avg_score = float(head[1] or 0)
    minutes = float(head[2] or 0)

    weak = db.execute(_t(
        "select e->>'item', count(*) filter (where (e->>'done')::boolean is false),"
        " count(*)"
        " from call_analysis ca join accounts a on a.account_id = ca.account_id,"
        " lateral jsonb_array_elements(ca.analysis->'checklist') e"
        " where a.owner_user_id = :u"
        "   and ca.created_at > now() - interval '30 days'"
        " group by 1 order by 2 desc limit 5"), {"u": oid}).fetchall()

    lines = ["Звонков разобрано за 30 дней: %d (%.0f мин), средняя оценка %.1f"
             % (total, minutes, avg_score)]
    if weak:
        lines.append("Чаще всего пропускают:")
        for item, missed, cnt in weak:
            if int(missed or 0) == 0:
                continue
            lines.append("• %s — %d из %d" % (str(item)[:60], int(missed), int(cnt)))
    return {"status": "ok", "answer": "\n".join(lines),
            "calls": total, "avg_score": round(avg_score, 2),
            "minutes": round(minutes, 1)}


E.register("analytics.manager", op_analytics_manager)


# --- knowledge_add ------------------------------------------------------
# Пишем в client_facts тем же способом, что ручка /api/memory/answer:
# уникальность по (account_id, fact_hash), повтор ОБНОВЛЯЕТ факт.
# Категории — фактические, из базы; выдумывать новую нельзя.

FACT_CATEGORIES = {
    "faq": "частый вопрос",
    "cena": "цена",
    "usluga": "услуга",
    "tovar": "товар",
    "preimushchestvo": "преимущество",
    "rule": "правило работы",
    "garantiya": "гарантия",
    "stil": "стиль общения",
}
DEFAULT_CATEGORY = "rule"


def _fact_parts(ctx):
    ent = ctx.get("entities") or {}
    cat = str(ent.get("category") or "").strip().lower()
    if cat not in FACT_CATEGORIES:
        cat = DEFAULT_CATEGORY
    value = str(ent.get("text") or "").strip()
    name = str(ent.get("title") or "").strip()
    if not name and value:
        name = value[:60]
    return cat, name[:255], value[:4000]


def prev_fact_add(db, user, ctx):
    cat, name, value = _fact_parts(ctx)
    if not value:
        return {"summary": "Не понял, какой факт записать", "changes": []}
    s = ("В базу знаний, раздел «%s»: %s"
         % (FACT_CATEGORIES[cat], value))
    return {"summary": s,
            "changes": [{"entity": "client_facts", "field": FACT_CATEGORIES[cat],
                         "after": value, "human": s}]}


def op_fact_add(db, user, ctx):
    import datetime as _d
    import hashlib as _h
    from sqlalchemy import text as _t

    cat, name, value = _fact_parts(ctx)
    if not value:
        return {"status": "failed", "error_code": "empty_fact",
                "answer": "Не понял, какой факт записать"}
    acc = ctx.get("account_id")
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    who = "command:%s" % uid
    now = _d.datetime.utcnow()
    fh = _h.md5((cat + "|" + name.lower()).encode("utf-8")).hexdigest()

    db.execute(_t(
        "INSERT INTO client_facts (account_id, category, name, value,"
        " source_type, source_ref, source_date, confidence, status,"
        " confirmed_by, confirmed_at, fact_hash, extracted_at, updated_at)"
        " VALUES (:a, :c, :n, :v, 'client', 'command_center', :t, 100,"
        " 'confirmed', :w, :t, :h, :t, :t)"
        " ON CONFLICT (account_id, fact_hash) DO UPDATE SET"
        " value = EXCLUDED.value, status = 'confirmed', confidence = 100,"
        " confirmed_by = EXCLUDED.confirmed_by,"
        " confirmed_at = EXCLUDED.confirmed_at,"
        " updated_at = EXCLUDED.updated_at"),
        {"a": acc, "c": cat, "n": name, "v": value, "w": who, "t": now, "h": fh})
    db.commit()
    return {"status": "ok",
            "answer": "Записал в базу знаний, раздел «%s»: %s"
                      % (FACT_CATEGORIES[cat], value[:120]),
            "category": cat,
            "changes": [{"entity": "client_facts", "field": FACT_CATEGORIES[cat],
                         "after": value}]}


E.register("knowledge.fact_add", op_fact_add)
E.register_preview("knowledge.fact_add", prev_fact_add)
