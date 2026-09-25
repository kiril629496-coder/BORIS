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
from app.services.exception_observability import observe_suppressed


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
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=50)
        return False


def _latest_daily_stats(db, account_id, max_days_back=7):
    """Репрезентативная daily_stats за N дней.

    Не берём «первый попавшийся ненулевой день»: после провалов сбора это может
    быть технический хвост на 2-3 объявления и он искажает кабинет.
    Выбираем лучший слепок по объёму items_count (или длине items), а при
    равенстве — более свежий день. Если все слепки пустые, возвращаем самый
    свежий валидный (fallback), чтобы UI не падал.
    """
    today = datetime.now().date()
    fallback = (None, None)
    best = (None, None)
    best_score = -1
    best_back = 10 ** 9

    for back in range(0, max_days_back + 1):
        d = (today - timedelta(days=back)).isoformat()
        v = _load(db, account_id, "daily_stats:" + d)
        if not isinstance(v, dict):
            continue

        if fallback == (None, None):
            fallback = (d, v)

        items = v.get("items") if isinstance(v.get("items"), list) else []
        raw_count = v.get("items_count")
        try:
            items_count = int(raw_count or 0)
        except Exception:
            items_count = 0

        score = max(items_count, len(items))
        if score > best_score or (score == best_score and back < best_back):
            best = (d, v)
            best_score = score
            best_back = back

    if best != (None, None):
        return best
    return fallback


def _daily_stats_gap_info(db, account_id, max_days_back=7):
    """Диагностика провалов сбора daily_stats: сколько последних дней подряд пустые."""
    today = datetime.now().date()
    consecutive_empty_days = 0

    for back in range(0, max_days_back + 1):
        d = (today - timedelta(days=back)).isoformat()
        v = _load(db, account_id, "daily_stats:" + d)
        if not isinstance(v, dict):
            consecutive_empty_days += 1
            continue

        items = v.get("items") if isinstance(v.get("items"), list) else []
        try:
            items_count = int(v.get("items_count") or 0)
        except Exception:
            items_count = 0

        if items_count <= 0 and len(items) == 0:
            consecutive_empty_days += 1
            continue
        break

    return {
        "consecutive_empty_days": consecutive_empty_days,
        "max_days_back": max_days_back,
        "possible_collection_gap": consecutive_empty_days >= 3,
    }


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
        if not f["has_niche"]:  # NICHE_RESOLVER_V1
            from app.services.niche_resolver import business_niche as _bn2
            f["has_niche"] = bool(_bn2(account_id, db=db))
        f["has_site"] = bool(acc and (acc[3] or "").strip())
        f["has_description"] = bool(acc and (acc[4] or "").strip())
        f["has_advantages"] = bool(acc and (acc[5] or "").strip())
        f["telegram"] = bool(acc and acc[6])
        f["client_goal"] = (acc[7] if acc else "") or ""
        # «Обучен» = у аккаунта есть реально сохранённые знания, а не только
        # два поля карточки компании. Обучение файлом кладёт материал в
        # Storage[boris_knowledge]; до 14.08 шаг teach этого не видел и
        # никогда не закрывался (аудит 14.08).
        # читаем сырым запросом: в boris_knowledge лежит текст, а не JSON,
        # и _load() на нём отдаёт default. Строго по account_id — глобальные
        # знания (account_id='global') обучением аккаунта не считаются.
        _kbrow = db.execute(_sqltext(
            "SELECT value FROM storage WHERE account_id=:a AND key='boris_knowledge'"
        ), {"a": account_id}).fetchone()
        _kb_len = len(str((_kbrow[0] if _kbrow else "") or "").strip())
        f["knowledge_len"] = _kb_len
        f["taught"] = bool(f["has_description"] or f["has_advantages"] or _kb_len)

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

        # ACCOUNT ACCESS CONTRACT V2: тариф/4-дневный trial принадлежат пользователю,
        # а сценарий запускается для любого его доступного аккаунта. Раньше
        # has_active_access смотрел только account-scoped storage[billing/payment],
        # поэтому новый подтверждённый пользователь с честным 4-дневным trial
        # получал ложный no_access на своём аккаунте. Также это ломало второй
        # подключённый аккаунт владельца при активной подписке пользователя.
        subscription_active = False
        subscription_until = None
        trial_active = False
        try:
            sub = db.execute(_sqltext("""
                SELECT u.subscription_expires_at, u.trial_started_at
                FROM users u
                WHERE u.status='active'
                  AND u.subscription_expires_at IS NOT NULL
                  AND u.subscription_expires_at >= NOW()
                  AND (
                    u.account_id=:a
                    OR EXISTS (SELECT 1 FROM accounts aa
                               WHERE aa.account_id=:a AND aa.owner_user_id=u.id)
                    OR EXISTS (SELECT 1 FROM user_account_access ua
                               WHERE ua.account_id=:a AND ua.user_id=u.id AND ua.can_view=TRUE)
                  )
                ORDER BY u.subscription_expires_at DESC
                LIMIT 1
            """), {"a": account_id}).fetchone()
            if sub and sub[0]:
                subscription_active = True
                subscription_until = sub[0].isoformat() if hasattr(sub[0], "isoformat") else str(sub[0])
                if sub[1]:
                    try:
                        trial_active = datetime.utcnow() <= (sub[1] + timedelta(days=4))
                    except Exception:
                        trial_active = False
        except Exception:
            # Старые account-level entitlement источники остаются рабочим fallback.
            subscription_active = False
        f["subscription_active"] = subscription_active
        f["subscription_until"] = subscription_until
        f["trial_active"] = trial_active
        f["has_active_access"] = bool(
            f["unlimited"] or tier not in ("none", "", None) or pay_ok or subscription_active)

        # черновики: drafts и gen_ads - оба списки объявлений
        drafts = _load(db, account_id, "drafts", []) or []
        gen_ads = _load(db, account_id, "gen_ads", []) or []
        f["drafts_count"] = len(drafts) if isinstance(drafts, list) else 0
        f["gen_ads_count"] = len(gen_ads) if isinstance(gen_ads, list) else 0
        f["ready_count"] = max(f["drafts_count"], f["gen_ads_count"])

        feed = _load(db, account_id, "feed_items", []) or []
        f["feed_count"] = len(feed) if isinstance(feed, list) else 0

        # Existing Campaign/Feed Factory state: resume prepared work instead of
        # asking the owner to start the same business task again.
        try:
            from app.models.campaign import Campaign
            from app.models.campaign_item import CampaignItem
            campaigns = (db.query(Campaign)
                .join(CampaignItem, CampaignItem.campaign_id == Campaign.id)
                .filter(CampaignItem.account_id == account_id)
                .order_by(Campaign.updated_at.desc(), Campaign.id.desc()).all())
            seen, cp = set(), []
            for c in campaigns:
                if c.id in seen: continue
                seen.add(c.id)
                rows = db.query(CampaignItem).filter(CampaignItem.campaign_id == c.id,
                    CampaignItem.account_id == account_id).all()
                media = approved = comments = 0
                for r in rows:
                    try: p = json.loads(r.payload_json or "{}") or {}
                    except Exception: p = {}
                    media += 1 if (p.get("images") or []) else 0
                    approved += 1 if r.status == "ready" else 0
                    comments += 1 if str(p.get("owner_review_comment") or "").strip() else 0
                try: ff = (json.loads(c.settings_json or "{}") or {}).get("feed_factory") or {}
                except Exception: ff = {}
                iv = ff.get("internal_validation") or {}
                xml = ff.get("official_xml_check") or ff.get("official_xmlcheck") or {}
                cp.append({"id": c.id, "name": c.name or "Кампания", "items": len(rows),
                    "media": media, "validated": int(iv.get("ready") or 0), "approved": approved,
                    "review_comments": comments, "ready_for_owner_approval": bool(ff.get("ready_for_owner_approval")),
                    "xml_pass": str(xml.get("status") or "").lower() in ("pass","ok"),
                    "cities": ff.get("cities") or [], "directions": ff.get("directions") or []})
            f["campaigns"] = cp
            f["campaign_ready"] = next((x for x in cp if x["ready_for_owner_approval"]), None)
        except Exception:
            f["campaigns"] = []; f["campaign_ready"] = None

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
        f["stats_gap"] = _daily_stats_gap_info(db, account_id)

        # Existing MOP/CRM funnel, read-only and account-scoped. These are the
        # same persisted facts used by /api/messenger/sales/economics; scenarios
        # orchestrate them instead of inventing a second sales engine.
        try:
            base = db.execute(_sqltext("""
              SELECT count(*) inquiries, count(*) FILTER (WHERE has_phone) phones
                FROM messenger_leads WHERE account_id=:a
            """), {"a": account_id}).mappings().one()
            engaged = db.execute(_sqltext("""
              SELECT count(*) FROM (SELECT avito_chat_id FROM messenger_messages
               WHERE account_id=:a AND direction='in' GROUP BY avito_chat_id HAVING count(*) >= 2) q
            """), {"a": account_id}).scalar() or 0
            mop = db.execute(_sqltext("""
              SELECT count(*) classified,
                count(*) FILTER (WHERE value LIKE '%\"qualification_stage\": \"QUALIFIED\"%' OR value LIKE '%\"qualification_stage\": \"TARGET_ACTION\"%') qualified,
                count(*) FILTER (WHERE value LIKE '%\"qualification_stage\": \"TARGET_ACTION\"%') target_actions
              FROM storage WHERE account_id=:a AND key LIKE 'mop_qualification:%'
            """), {"a": account_id}).mappings().one()
            deals = db.execute(_sqltext("""SELECT count(DISTINCT d.avito_chat_id) FROM boris_crm_deals d
              JOIN accounts a ON a.account_id=d.avito_account_id
              WHERE d.avito_account_id=:a AND d.source='avito' AND d.owner_user_id=a.owner_user_id"""), {"a": account_id}).scalar() or 0
            classified = int(mop["classified"] or 0)
            f["sales_funnel"] = {"inquiries": int(base["inquiries"] or 0), "engaged": int(engaged),
                "qualified": int(mop["qualified"] or 0) if classified else None,
                "phones": int(base["phones"] or 0),
                "target_actions": int(mop["target_actions"] or 0) if classified else None,
                "deals": int(deals), "qualification_coverage": classified}
        except Exception:
            f["sales_funnel"] = {}

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


# Записи про деньги и администрирование сотруднику не показываем:
# он видит рабочие действия, но не суммы оплат своего руководителя.
OWNER_ONLY_ACTIONS = {"set_payment", "admin_provision", "admin_create_client",
                      "set_tier", "billing", "payment", "upgrade_due"}


def journal(account_id, db=None, limit=10, only_today=False, role="owner"):
    """Записи журнала. Формат записи: {ts, actor, action, details}.

    role — роль смотрящего в этом аккаунте. Всё, кроме owner, получает
    журнал без денежных и административных записей.
    """
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
        if role != "owner":
            log = [e for e in log
                   if str(e.get("action") or "") not in OWNER_ONLY_ACTIONS]

        # Одинаковые подряд идущие записи схлопываем: повтор одной и той же
        # операции каждые несколько минут забивает экран.
        dedup, prev = [], None
        for e in log:
            if not isinstance(e, dict):
                continue
            sign = (str(e.get("action") or ""), str(e.get("details") or "")[:200])
            if sign == prev:
                continue
            prev = sign
            dedup.append(e)
        log = dedup

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

# BUSINESS_SCENARIO_CATALOG_EXECUTABLE_V1: every catalog scenario must project a non-empty executable step graph.
SOON = []

CATALOG = {
    "avito_start": {
        "title": "Запустить Авито с нуля",
        "lead": "Проведу от подключения Avito до готовой кампании: проверю API, помогу выбрать бизнес-цель, соберу объявления и фид, проверю его валидатором Avito. Для категории «Услуги» нужен активный тариф «Расширенный», для категории «Товары» — «Максимальный».",
        "modules": ["Аккаунты", "Бизнес-цель", "Категории", "Объявления", "Фид"],
        "plan": ["Я проверю подключение Avito по Client ID и Client Secret и доступ к автозагрузке",
                 "Проверю условие тарифа: Услуги — активный «Расширенный», Товары — активный «Максимальный»",
                 "Помогу зафиксировать бизнес-цель и KPI",
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
    "conversion_ab": {
        "title": "Масштабировать объявления, которые дают лиды",
        "lead": "Снижу базовые ставки, найду конверсионные объявления за 30 дней, проверю заполнение карточек и запущу контролируемый A/B-тест лучших заголовков на 30 объявлениях с окном измерения 3 дня.",
        "modules": ["Аналитика", "ИИ-маркетолог", "Объявления", "Browser Gateway"],
        "plan": [
            "Поставлю минимально достаточные ставки на активные объявления в пределах заданного потолка",
            "Отдельно выделю объявления, которые дали лиды за последние 30 дней, и не смешиваю их со слабыми",
            "Проверю обязательные и доступные поля каждого активного объявления и заполню только подтверждёнными данными",
            "Проанализирую заголовки объявлений, которые дали лиды за 30 дней",
            "Соберу A/B-партию до 30 объявлений с теми же доказанными паттернами заголовков без ложных обещаний и смысловых дублей",
            "Измеряю 3 полных дня: лиды, CPL, просмотры и конверсию; победителей продолжаю масштабировать, проигравшие не размножаю",
            "Для объявлений, созданных вне BORIS, использую Avito API там, где операция поддерживается, иначе существующий Browser Gateway / Mass Editor"
        ],
        "fields": [
            {"key":"max_bid","label":"Максимальная ставка, ₽","type":"number","required":True},
            {"key":"test_size","label":"Размер A/B-партии","type":"number","required":True},
            {"key":"measurement_days","label":"Окно измерения, дней","type":"number","required":True}
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
    "new_niche": {
        "title": "Проверить новую нишу",
        "lead": "Соберу тест новой ниши, а анализ конкурентов выполню существующим market/parser только после вашего подтверждения.",
        "modules": ["Анализ рынка", "Объявления", "Feed Factory"],
        "plan": ["Соберу факты бизнеса", "Зафиксирую продукт, регион и средний чек", "Проверю конкурентов и цены", "Сформирую решение идти/тестировать/не рекомендую", "Подготовлю минимальный тест объявлений"],
        "fields": [{"key":"product","label":"Что хотим продавать","type":"text","required":True},{"key":"region","label":"Регион","type":"text","required":True},{"key":"check","label":"Средний чек, ₽","type":"number","required":True},{"key":"model","label":"Модель бизнеса","type":"select","options":["производство","перепродажа","услуга"],"required":True}],
    },
    "new_city": {
        "title": "Выйти в новый город",
        "lead": "Сравню рынок нового города через существующий анализ конкурентов и подготовлю локальный запуск через Feed Factory.",
        "modules": ["Анализ рынка", "Объявления", "Feed Factory", "Аналитика"],
        "plan": ["Зафиксирую новый город", "Сравню цены и конкурентность", "Выберу лучшие текущие предложения", "Подготовлю локальные версии объявлений", "Проверю feed", "Измерю результат"],
        "fields": [{"key":"city","label":"Новый город","type":"text","required":True},{"key":"direction","label":"Направление или продукт","type":"text","required":True}],
    },
    "find_problem": {
        "title": "Найти причину отсутствия заявок",
        "lead": "Разберу воронку от показов до продажи, найду главный узкий участок и соберу план исправления.",
        "modules": ["Аналитика", "Объявления", "Маркетинг", "Продажи"],
        "plan": ["Соберу факты выбранного аккаунта", "Найду главный провал в воронке", "Подготовлю план исправления", "Проведу изменения через существующие инструменты", "Сравню результат до и после"],
        "fields": [{"key":"target","label":"Что хотите улучшить","type":"select","options":["заявки","продажи","стоимость заявки"],"required":True}],
    },
    "season": {
        "title": "Подготовиться к сезону",
        "lead": "Соберу сезонный план по объявлениям, контенту и продвижению на основе данных выбранного аккаунта.",
        "modules": ["Аналитика", "Объявления", "Маркетинг", "Соцсети"],
        "plan": ["Соберу доступную историю и текущие показатели", "Зафиксирую сезон и приоритет", "Подготовлю календарь 30/14/7 дней", "Подготовлю объявления и контент", "Измерю результат на старте сезона"],
        "fields": [{"key":"period","label":"К какому сезону или периоду готовимся","type":"text","required":True},{"key":"priority","label":"Приоритетный продукт или направление","type":"text","required":True}],
    },
    "social": {
        "title": "Запустить социальные сети",
        "lead": "Соберу контент-задачу, план публикаций и подготовлю проект в существующем Social без публикации без вашего подтверждения.",
        "modules": ["Соцсети", "Баннеры", "База знаний"],
        "plan": ["Соберу факты бизнеса", "Зафиксирую площадки и цель", "Подготовлю контент-план", "Подготовлю материалы", "Передам в существующий Social", "Покажу статистику после публикаций"],
        "fields": [{"key":"platforms","label":"Площадки","type":"select","options":["Telegram","VK","Telegram + VK"],"required":True},{"key":"goal","label":"Цель контента","type":"select","options":["продажи","доверие","прогрев","экспертность"],"required":True},{"key":"frequency","label":"Частота публикаций","type":"text","required":True}],
    },
    "automate_sales": {
        "title": "Настроить AI-менеджера",
        "lead": "Настрою цель, знания, правила, передачу человеку и контроль качества продаж для выбранного аккаунта.",
        "modules": ["Сообщения", "CRM", "Продажи", "Обучение"],
        "plan": ["Соберу состояние диалогов и знаний", "Зафиксирую цель AI-менеджера", "Проверю правила и график", "Подключу handoff и CRM", "Проведу обучение и контроль качества", "Покажу показатели реальных диалогов"],
        "fields": [{"key":"goal","label":"Что должен делать AI-менеджер","type":"select","options":["получить телефон","записать на замер","договориться о расчёте","назначить встречу","довести до заказа","другое"],"required":True},{"key":"schedule","label":"Рабочее время","type":"text","required":True}],
    },
    "website": {
        "title": "Создать сайт",
        "lead": "Соберу задачу, структуру и материалы и передам их в существующий Sitebuild. Не буду имитировать автоматическую сборку, если её нет.",
        "modules": ["База знаний", "Медиатека", "Создание сайтов"],
        "plan": ["Соберу факты бизнеса", "Зафиксирую цель сайта", "Подготовлю структуру и материалы", "Передам задачу в Sitebuild", "Покажу текущий статус и следующий шаг"],
        "fields": [{"key":"goal","label":"Главная цель сайта","type":"text","required":True},{"key":"format","label":"Какой сайт нужен","type":"select","options":["лендинг","сайт компании","каталог"],"required":True}],
    },
    "reactivation": {
        "title": "Вернуть потерянных клиентов",
        "lead": "Найду зависшие и потерянные диалоги выбранного аккаунта, разложу по причинам и проведу через существующую реактивацию.",
        "modules": ["CRM", "Сообщения", "Возврат клиентов", "Задачи"],
        "plan": ["Соберу зависшие обращения", "Разделю по причинам", "Определю приоритет возврата", "Подготовлю следующий контакт", "Передам ответы в работу", "Измерю возврат в диалог и продажу"],
        "fields": [{"key":"priority","label":"Кого возвращать в первую очередь","type":"select","options":["всех","с телефоном","получивших расчёт","отказавшихся из-за цены"],"required":True}],
    },
    "improve_sales": {
        "title": "Улучшить продажи",
        "lead": "Покажу, где теряются обращения после первого контакта, и соберу конкретные действия для менеджеров и AI-менеджера.",
        "modules": ["CRM", "Продажи", "Сообщения", "Задачи", "Возврат клиентов"],
        "plan": ["Соберу воронку и диалоги", "Найду стадии потерь", "Сопоставлю рекомендации руководителя продаж", "Создам список действий", "Верну зависшие обращения в работу", "Сравню конверсию до и после"],
        "fields": [{"key":"metric","label":"Что важнее улучшить","type":"select","options":["конверсию в телефон","конверсию во встречу","конверсию в продажу"],"required":True}],
    },
}


def _steps(scenario_type, f):
    """Шаги и их ФАКТИЧЕСКИЙ статус. tab — ключ activeTab старого кабинета,
    route — отдельный маршрут. auto=True означает, что шаг закрывается сам."""
    s = f.get("stage", 1)
    if scenario_type == "avito_start":
        campaign = f.get("campaign_ready") or {}
        if campaign:
            return [
                {"key":"context","title":"Собрать данные бизнеса и ассортимент","hint":"BORIS нашёл уже подготовленную кампанию и не начинает работу заново.","auto":True,"done":True},
                {"key":"prepare","title":"Подготовить объявления, фото и обязательные поля","hint":"Подготовлено %s объявлений, с фото %s." % (campaign.get("items",0),campaign.get("media",0)),"auto":True,"done":campaign.get("items",0)>0 and campaign.get("media",0)>=campaign.get("items",0)},
                {"key":"validate","title":"Проверить объявления и XML Avito","hint":"Проверено %s из %s. Проверка Avito: %s." % (campaign.get("validated",0),campaign.get("items",0),"пройдена" if campaign.get("xml_pass") else "требует внимания"),"auto":True,"done":campaign.get("validated",0)>=campaign.get("items",0) and campaign.get("xml_pass")},
                {"key":"approval","title":"Проверить и согласовать %s объявлений" % campaign.get("items",0),"hint":"Это настоящее решение владельца. До согласования BORIS ничего не публикует.","route":"/dashboard/campaign/%s" % campaign.get("id"),"auto":False,"done":campaign.get("approved",0)>=campaign.get("items",0)},
                {"key":"safe_export","title":"Подготовить безопасную выгрузку","hint":"После согласования BORIS сам собирает проверенный feed. Это не публикация.","route":"/dashboard/campaign/%s" % campaign.get("id"),"action":"campaign_safe_export","auto":True,"done":False},
                {"key":"publish","title":"Разрешить внешнюю публикацию","hint":"Safe feed готовится автоматически; отправка наружу остаётся отдельным решением владельца.","route":"/dashboard/campaign/%s" % campaign.get("id"),"auto":False,"owner_required":True,"done":False},
                {"key":"measure","title":"Измерить показы, обращения и стоимость заявки","hint":"После нового статистического окна BORIS сравнит бизнес-результат с baseline.","action":"scenario_measure","auto":True,"done":False},
            ]
        st = f.get("_start") or {}
        parsed = st.get("parsed", 0)
        drafts = st.get("drafts", 0)
        uniq = st.get("uniquified", 0)
        return [
            {"key": "connect", "title": "Подключить Avito через API",
             "hint": "Введите Client ID и Client Secret. BORIS проверит ключи и доступ к автозагрузке. Для категории «Услуги» нужен активный тариф «Расширенный», для категории «Товары» — активный тариф «Максимальный».",
             "route": "/dashboard/connect-avito", "done": bool(st.get("has_keys")) or s >= 2,
             "auto": False, "owner_required": True},
            {"key": "parse", "title": "Добавить ассортимент или разобрать сайт",
             "hint": "Если сайт указан — BORIS сам разберёт каталог. Если сайта нет, откройте ассортимент/Campaign и добавьте товары удобным способом; публикации без вас не будет.",
             "route": "/dashboard/campaign", "action": "parse_site", "auto": True, "done": parsed > 0},
            {"key": "review", "title": "Посмотреть найденные товары",
             "hint": "Найдено %d %s. Отметьте те, что переносить не нужно."
                     % (parsed, _plural(parsed, "товар", "товара", "товаров")),
             "action": "parsed_products", "auto": False, "done": bool(st.get("reviewed"))},
            {"key": "photos", "title": "Проверить и добавить фотографии товаров",
             "hint": "Если изображения пришли из каталога — BORIS использует их. Если фотографий не хватает, добавьте их в Campaign/ассортимент и подтвердите готовность.",
             "route": "/dashboard/campaign", "auto": False, "owner_required": True,
             "done": bool(st.get("gallery_done"))},
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
             "tab": "listings", "auto": False, "owner_required": True, "done": False},
            {"key": "measure", "title": "Измерить показы, обращения и стоимость заявки",
             "hint": "После фактической выгрузки BORIS дождётся нового окна статистики и сравнит результат с baseline.",
             "route": "/dashboard/analytics", "action": "scenario_measure", "auto": True, "done": False},
        ]
    if scenario_type == "more_leads":
        wf = f.get("_workflow") or {}
        prepared = bool((wf.get("diagnosis") or {}).get("result"))
        executed = bool((wf.get("execute") or {}).get("result"))
        return [
            {"key":"context","title":"Собрать plan/fact/KPI и статистику","hint":"BORIS читает реальные показатели выбранного аккаунта.","auto":True,"done":prepared},
            {"key":"diagnosis","title":"Найти главный bottleneck заявок","hint":"Один главный вывод по реальным данным, без списка равновесных гипотез.","auto":True,"done":prepared},
            {"key":"plan","title":"Выбрать существующую AI Marketing capability","hint":"Если оптимизация уже активна, BORIS продолжает её и не создаёт дубль.","auto":True,"done":prepared},
            {"key":"execute","title":"Продолжить существующую оптимизацию","hint":"Показываю: что не так → что сделал BORIS → чего ждём → что будет дальше.","route":"/dashboard/marketing","action":"marketing_prepare","auto":True,"done":executed},
            {"key":"measure","title":"Дождаться observation window и KPI_AFTER","hint":"Публикация ≠ бизнес-результат. После полного окна сравним заявки и CPL, затем штатный learning loop.","route":"/dashboard/marketing","action":"scenario_measure","auto":True,"done":False},
        ]
    if scenario_type == "conversion_ab":
        wf = f.get("_workflow") or {}
        prepared = bool((wf.get("diagnosis") or {}).get("result") or (wf.get("winners") or {}).get("result"))
        return [
            {"key":"context","title":"Собрать статистику и текущий KPI","hint":"BORIS читает реальные показатели аккаунта и не запускает платных действий.","auto":True,"done":prepared},
            {"key":"winners","title":"Найти рабочие объявления и текущий резерв","hint":"Использую существующий AI Marketing и A/B memory; новую платную оптимизацию не создаю.","route":"/dashboard/marketing","action":"marketing_prepare","auto":True,"done":prepared},
            {"key":"fields","title":"Проверить карточки перед тестом","hint":"Откройте объявления: BORIS не выдумывает недостающие характеристики и не меняет внешние карточки без штатного gate.","route":"/dashboard/campaign","auto":False,"owner_required":False,"done":False},
            {"key":"ab_plan","title":"Проверить A/B-память и выбрать тестовую партию","hint":"В разделе A/B видны фактические варианты, просмотры, обращения и конверсия. Подготовка тестовой партии не равна публикации.","route":"/dashboard/avito-ab-tests","auto":False,"owner_required":False,"done":False},
            {"key":"publish","title":"Разрешить внешний запуск выбранной A/B-партии","hint":"Публикация/изменение живых объявлений остаётся отдельным подтверждаемым действием; BORIS не обходит этот gate.","route":"/dashboard/avito-ab-tests","auto":False,"owner_required":True,"done":False},
            {"key":"measure","title":"Измерить A/B-результат после полного окна","hint":"Сравню обращения, CPL, просмотры и конверсию с baseline; рост просмотров без обращений не считается победой.","route":"/dashboard/analytics","action":"scenario_measure","auto":True,"done":False},
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
            {"key": "settings", "title": "Выбрать порог слабого объявления",
             "hint": "Это бизнес-правило владельца: сколько просмотров без обращения и сколько дней без просмотров считать слабым объявлением.",
             "action": "set_republish_settings", "auto": False, "owner_required": True,
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
             "hint": "Сейчас в фиде %d %s. Это внешнее действие: BORIS не обходит подтверждение владельца."
                     % (feed_n, _plural(feed_n, "позиция", "позиции", "позиций")),
             "tab": "listings", "auto": False, "owner_required": True, "done": False},
            {"key":"measure","title":"Измерить результат оживления кабинета","hint":"После новой статистики сравню просмотры и обращения с baseline; рост просмотров без обращений не считается успехом.","action":"scenario_measure","auto":True,"done":False},
        ]

    if scenario_type in ("more_leads", "new_niche", "new_city", "find_problem", "season", "social", "automate_sales", "website", "reactivation", "improve_sales"):
        wf = f.get("_workflow") or {}
        prepared = bool((wf.get("diagnosis") or {}).get("result"))
        execution = {
            "more_leads": ("Продолжить существующую оптимизацию", "/dashboard/marketing"),
            "new_niche": ("Проверить рынок и конкурентов", "/dashboard"),
            "new_city": ("Проверить рынок нового города", "/dashboard"),
            "find_problem": ("Исправить главный узкий участок", "/dashboard/marketing"),
            "season": ("Подготовить сезонный execution-plan", "/dashboard/campaign"),
            "social": ("Подготовить проект публикаций", "/social"),
            "automate_sales": ("Настроить работу AI-менеджера", "/ai"),
            "website": ("Передать задачу в создание сайта", "/dashboard/sitebuild"),
            "reactivation": ("Вернуть клиентов в работу", "/dashboard/crm"),
            "improve_sales": ("Выполнить план улучшения продаж", "/rop"),
        }[scenario_type]
        action_map={"more_leads":"marketing_prepare","new_niche":"city_research","new_city":"city_research","find_problem":"marketing_prepare","season":"season_prepare","social":"social_prepare","automate_sales":"mop_prepare","website":"sitebuild_prepare","reactivation":"reactivation_prepare","improve_sales":"sales_prepare"}
        er=((wf.get("execute") or {}).get("result") or {}) if isinstance(wf,dict) else {}
        city_wait = scenario_type in ("new_niche","new_city") and bool(er.get("task_id")) and er.get("task_status") != "done"
        city_needs_approval = scenario_type in ("new_niche","new_city") and not er.get("task_id")
        steps=[
            {"key":"context","title":"Собрать картину по аккаунту","hint":"БОРИС использует только данные выбранного аккаунта.","auto":True,"done":prepared},
            {"key":"diagnosis","title":"Понять, где главный резерв","hint":"Покажу факты и человеческий вывод, а не технические статусы.","auto":True,"done":prepared},
            {"key":"plan","title":"Собрать план BORIS","hint":"План сохраняется вместе со сценарием и переживает перезагрузку.","auto":True,"done":prepared},
            {"key":"execute","title":execution[0],"hint":("Внешний анализ уже выполняется; повторного подтверждения не требуется." if city_wait else "Действие выполняется существующим модулем BORIS; внешние/платные операции требуют отдельного подтверждения."),"route":execution[1],"action":action_map.get(scenario_type),"auto":not city_needs_approval,"owner_required":city_needs_approval,"done":False},
        ]
        if scenario_type in ("new_niche","new_city"):
            steps.append({"key":"prepare","title":"Подготовить минимальный тест без публикации","hint":"После анализа BORIS переиспользует существующую Campaign/Feed Factory, а если её нет — подготовит deterministic test spec без платной генерации.","route":"/dashboard/campaign","action":"market_launch_prepare","auto":True,"done":False})
        steps.append({"key":"measure","title":"Измерить результат и выбрать следующий шаг","hint":"Внутреннее действие уже выполнено, но бизнес-результат нужно измерить отдельно. BORIS сам дождётся нового периода данных и сравнит KPI с исходными показателями.","route":"/dashboard/analytics","action":"scenario_measure","auto":True,"done":False})
        return steps

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

    if scenario_type in ("more_leads", "conversion_ab", "new_niche", "new_city", "find_problem", "season", "social", "automate_sales", "website", "reactivation", "improve_sales"):
        facts = dict(facts)
        facts["_workflow"] = steps_state or {}

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
        # шаг не считается выполненным, если последнее действие вернуло ok: false.
        # ручную отметку клиента не перебиваем — это его осознанное решение.
        if (item["status"] == "completed" and manual != "done"
                and isinstance(item["result"], dict)
                and item["result"].get("ok") is False):
            item["status"] = "attention"
        item.pop("done", None)
        out.append(item)
    # навигация по шагам: номер и соседи. Порядок массива и есть порядок сценария
    for _i, _it in enumerate(out):
        _it["index"] = _i + 1
        _it["prev_key"] = out[_i - 1]["key"] if _i > 0 else None
        _it["next_key"] = out[_i + 1]["key"] if _i + 1 < len(out) else None
    total = len(out)
    done = sum(1 for i in out if i["status"] in ("completed", "skipped"))
    return {
        "steps": out,
        "total": total,
        "done": done,
        "percent": int(round(done * 100.0 / total)) if total else 0,
        "current": next((i["key"] for i in out if i["status"] == "current"), None),
    }


def recommend_scenario(facts):
    """Deterministic next business scenario from current account facts.

    This is a read-only recommendation, not a second decision engine. It uses
    the same account-scoped facts that already drive scenario diagnosis. The
    catalog can therefore answer "what should I do next?" without an AI call
    and without enabling a scenario that entitlement/capability gates deny.
    """
    facts = facts or {}
    active_items = int(facts.get("active_items") or 0)
    views = int(facts.get("views") or 0)
    contacts = int(facts.get("contacts") or 0)
    funnel = facts.get("sales_funnel") or {}

    if not facts.get("avito_connected") or active_items <= 0:
        return {
            "scenario_type": "avito_start",
            "reason": (
                "Сначала нужен рабочий источник объявлений: BORIS не видит "
                "подключённого Avito с активными объявлениями."
            ),
        }
    if not facts.get("has_stats"):
        return {
            "scenario_type": "find_problem",
            "reason": (
                "Нет свежего измерения результата. Сначала восстановим факты "
                "по показам, обращениям и воронке, а потом будем менять продвижение."
            ),
        }
    if views <= 0 or contacts <= 0:
        return {
            "scenario_type": "more_leads",
            "reason": (
                "Сейчас главный резерв до продажи — получить измеряемые обращения "
                "из существующих объявлений."
            ),
        }

    inquiries = int(funnel.get("inquiries") or 0)
    engaged = int(funnel.get("engaged") or 0)
    qualified = funnel.get("qualified")
    phones = int(funnel.get("phones") or 0)
    target_actions = funnel.get("target_actions")
    deals = int(funnel.get("deals") or 0)
    if inquiries and engaged < inquiries:
        return {
            "scenario_type": "improve_sales",
            "reason": "Часть обращений теряется сразу после первого контакта.",
        }
    if qualified is not None and engaged and int(qualified or 0) < engaged:
        return {
            "scenario_type": "automate_sales",
            "reason": "Диалоги есть, но не все клиенты доходят до квалификации.",
        }
    if qualified and phones < int(qualified):
        return {
            "scenario_type": "improve_sales",
            "reason": "Квалифицированные клиенты не всегда доходят до обмена контактом.",
        }
    if target_actions is not None and phones and int(target_actions or 0) < phones:
        return {
            "scenario_type": "improve_sales",
            "reason": "Телефоны получены, но часть клиентов не доходит до целевого действия.",
        }
    if target_actions and deals < int(target_actions):
        return {
            "scenario_type": "reactivation",
            "reason": "Есть целевые действия без сделки — стоит вернуть зависших клиентов.",
        }
    if int(facts.get("dead_items") or 0) > 0:
        return {
            "scenario_type": "revive",
            "reason": "Есть объявления с трафиком без результата — их стоит оживить.",
        }
    return {
        "scenario_type": "improve_sales",
        "reason": (
            "Критического провала до обращения не видно; следующий резерв — "
            "конверсия после первого контакта."
        ),
    }


def business_workflow_view(scenario_type, facts, input_data, progress):
    """Owner-facing execution contract built only from account-scoped facts."""
    inp = input_data or {}
    goal_map = {
        "more_leads": "Получить больше заявок: цель %s/день, CPL до %s ₽" % (inp.get("target_leads") or "—", inp.get("max_cpl") or "—"),
        "conversion_ab": "Масштабировать объявления с доказанной конверсией через контролируемый A/B-тест",
        "new_niche": "Проверить нишу «%s» в регионе %s" % (inp.get("product") or "новая ниша", inp.get("region") or "—"),
        "new_city": "Выйти с направлением «%s» в город %s" % (inp.get("direction") or "текущее направление", inp.get("city") or "—"),
        "find_problem": "Найти причину нехватки %s" % (inp.get("target") or "заявок"),
        "season": "Подготовиться к %s: %s" % (inp.get("period") or "сезону", inp.get("priority") or "приоритетное направление"),
        "social": "Запустить %s для цели «%s»" % (inp.get("platforms") or "соцсети", inp.get("goal") or "продажи"),
        "automate_sales": "AI-менеджер должен: %s" % (inp.get("goal") or "доводить обращения до следующего шага"),
        "website": "Создать %s: %s" % (inp.get("format") or "сайт", inp.get("goal") or "получать обращения"),
        "reactivation": "Вернуть потерянных клиентов: приоритет — %s" % (inp.get("priority") or "всех"),
        "improve_sales": "Улучшить %s" % (inp.get("metric") or "конверсию в продажу"),
    }
    current = "Активных объявлений: %s; просмотров: %s; обращений: %s" % (facts.get("active_items") or 0, facts.get("views") or 0, facts.get("contacts") or 0)
    if facts.get("stats_date"): current += "; данные за %s" % facts.get("stats_date")
    funnel = facts.get("sales_funnel") or {}
    if funnel:
        current += "; воронка продаж: обращений %s → продолжили %s → квалифицированы %s → телефон %s → целевое действие %s → сделка %s" % (funnel.get("inquiries",0), funnel.get("engaged",0), funnel.get("qualified") if funnel.get("qualified") is not None else "нет данных", funnel.get("phones",0), funnel.get("target_actions") if funnel.get("target_actions") is not None else "нет данных", funnel.get("deals",0))

    diagnosis=[]; next_scenario=None
    if not facts.get("has_stats"):
        diagnosis.append("Нет свежей статистики объявлений — сначала нужно восстановить измерение результата")
    elif (facts.get("views") or 0) == 0:
        diagnosis.append("Нет просмотров: проблема до открытия карточки — показы, спрос, география или видимость объявлений")
        next_scenario="more_leads"
    elif (facts.get("contacts") or 0) == 0:
        diagnosis.append("Просмотры есть, но обращений нет: проверить оффер, цену, фото, описание и конкурентность карточек")
        next_scenario="more_leads"
    elif funnel:
        i,e,q,ph,ta,de=(funnel.get("inquiries",0),funnel.get("engaged",0),funnel.get("qualified"),funnel.get("phones",0),funnel.get("target_actions"),funnel.get("deals",0))
        if i and e < i: diagnosis.append("Обращения есть, но часть клиентов не продолжает диалог — проверить скорость и качество первого ответа"); next_scenario="improve_sales"
        elif q is not None and e and q < e: diagnosis.append("Диалог продолжается, но часть клиентов не квалифицируется — усилить вопросы и обучение AI-менеджера"); next_scenario="automate_sales"
        elif q and ph < q: diagnosis.append("Клиенты квалифицированы, но телефон получают не у всех — улучшить переход к контакту"); next_scenario="improve_sales"
        elif ta is not None and ph and ta < ph: diagnosis.append("Контакт получен, но до целевого действия доходят не все — нужен контроль следующего шага и Attention"); next_scenario="improve_sales"
        elif ta and de < ta: diagnosis.append("Целевые действия есть, но сделок меньше — проверить работу менеджеров и возврат зависших клиентов"); next_scenario="reactivation"
        else: diagnosis.append("Критического провала в измеряемой воронке не видно; следующий резерв ищем по слабым объявлениям и повторным контактам")
    elif facts.get("dead_items"):
        diagnosis.append("Есть %s объявлений с просмотрами без обращений" % facts.get("dead_items")); next_scenario="more_leads"
    else: diagnosis.append("Данных продаж пока недостаточно для честного вывода; BORIS не подменяет их общим AI-советом")

    meta=CATALOG.get(scenario_type,{})
    steps=progress.get("steps") or []; cur=next((x for x in steps if x.get("status") in ("current","attention")),None)
    known=[]
    for label,key in (("подключение Avito","avito_connected"),("ниша бизнеса","has_niche"),("сайт","has_site"),("описание компании","has_description"),("преимущества","has_advantages"),("цель по лидам","kpi_set")):
        if facts.get(key): known.append(label)
    campaign = facts.get("campaign_ready") if scenario_type == "avito_start" else None
    if campaign:
        goal_map["avito_start"] = "Запустить подготовленную кампанию «%s»" % campaign.get("name")
        diagnosis = ["BORIS нашёл готовую кампанию: %s объявлений, %s/%s прошли проверку, %s/%s имеют фото, XML Avito %s." % (
            campaign.get("items",0), campaign.get("validated",0), campaign.get("items",0), campaign.get("media",0), campaign.get("items",0),
            "прошёл проверку" if campaign.get("xml_pass") else "требует внимания")]
    return {
        "goal": goal_map.get(scenario_type) or meta.get("title"), "done_definition": "Задача выполнена, когда план пройден, результат измерен на данных аккаунта и определён следующий бизнес-шаг.",
        "current_state": current, "diagnosis": diagnosis, "plan": meta.get("plan") or [],
        "boris_knows": known, "owner_questions": [x.get("label") for x in (meta.get("fields") or []) if x.get("required") and not str(inp.get(x.get("key")) or "").strip()],
        "automatic": ["Собрать факты выбранного аккаунта", "Сформировать диагностику по реальным данным", "Сохранить план, прогресс и результат", "Передать работу существующему модулю BORIS"],
        "owner_required": ("Проверить и согласовать %s объявлений. Публикация не выполняется без отдельного решения." % campaign.get("items",0)) if campaign else "Только недостающие бизнес-данные и подтверждение перед публикацией, отправкой сообщения или другим необратимым внешним действием.",
        "current_work": (cur or {}).get("title") or "Сценарий завершён", "result": "Выполнено %s из %s шагов" % (progress.get("done") or 0,progress.get("total") or 0),
        "measure": "Сравнить показатели воронки до и после выполнения", "next_scenario": next_scenario,
        "technical_completion": "Штатный модуль BORIS выполнил доступное действие или дошёл до безопасной внешней/owner границы.",
        "business_outcome": ({"more_leads":"Рост обращений при CPL в целевом диапазоне","conversion_ab":"A/B-победители дают больше обращений/конверсии без ухудшения CPL после полного окна измерения","improve_sales":"Рост конверсии после обращения в выбранное целевое действие/сделку","reactivation":"Потерянные клиенты вернулись в диалог и целевое действие","website":"Сайт получает измеряемые обращения","social":"Публикации дают измеряемый охват/обращения","new_niche":"Получены данные для решения тестировать нишу или отказаться","new_city":"Получены данные и результат локального теста","find_problem":"Главный bottleneck улучшился после изменения","season":"Сезонный план выполнен и результат измерен","automate_sales":"AI-менеджер улучшает измеряемую конверсию диалогов","avito_start":"После запуска появились измеряемые показы и обращения","revive":"Слабые объявления заменены и новые версии дают измеряемый результат"}.get(scenario_type) or "Измеримый бизнес-результат достигнут"),
        "measurement_window": "После фактического внешнего выполнения; сравнение baseline и следующего полного окна статистики. Для оптимизаций использовать штатное observation window.",
        "next_action": (cur or {}).get("hint") or ("Запустить следующий сценарий: %s" % CATALOG.get(next_scenario,{}).get("title") if next_scenario else "Проверить фактический результат и выбрать следующий сценарий.")
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
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1210)
    for n in facts.get("notifications", [])[:3]:
        out.append({"priority": "info", "source": "notification",
                    "title": "Сообщение БОРИСа",
                    "text": str(n.get("text") or "")[:400],
                    "ts": n.get("ts"), "read": bool(n.get("read"))})
    return out
