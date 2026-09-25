# -*- coding: utf-8 -*-
"""BORIS Factory, Stage A: классификация полей. READ-ONLY, ничего не пишет."""
import sys, os, json, subprocess, collections, datetime
RUNTIME_BACKEND = os.environ.get("BORIS_RUNTIME_BACKEND_ROOT", "/root/BORIS/backend")
sys.path.insert(0, RUNTIME_BACKEND); os.chdir("/root/BORIS/backend")
from app.db.session import SessionLocal
from sqlalchemy import text as T
from app.client_config import schema as S, entitlements as E, collector as C

KNOWN, DERIVE, AUTO_MIGRATE, OPERATIONAL, ASK = \
    "KNOWN", "DERIVE", "AUTO_MIGRATE", "OPERATIONAL_CONFIG", "ASK_CLIENT"
CONFLICT, UNKNOWN, NOT_REQUIRED = "CONFLICT", "UNKNOWN", "NOT_REQUIRED"
CLASSES = (KNOWN, DERIVE, AUTO_MIGRATE, OPERATIONAL, ASK, CONFLICT, UNKNOWN, NOT_REQUIRED)
BLOCKER, SOON, IMPROVE = "blocker", "required_soon", "improvement"

# Политика поля: кто ОБЯЗАН его предоставить, если значения ещё нет.
# fallback_class · candidate (как добыть) · severity · owner · screen · продукт-потребитель
P = lambda c, cand, sev, owner, screen, prod, why: {
    "fallback": c, "candidate": cand, "severity": sev, "owner": owner,
    "screen": screen, "products": prod, "why": why}

POLICY = {
 "profile.city": P(DERIVE, "onboarding_form.city|feed_prefs.city", SOON, "client",
                   "goals", ("feed", "cpx"), "город есть в анкете и в настройках фида"),
 "profile.timezone": P(DERIVE, "city_to_timezone", IMPROVE, "platform", None, ("platform",),
                       "выводится из подтверждённого города, спрашивать не нужно"),
 "profile.tone_of_voice": P(ASK, "presets", IMPROVE, "client", "voice", ("mop",),
                            "стиль общения с покупателем - решение владельца, но пресетом"),
 "products.mop": P(AUTO_MIGRATE, "storage:billing->entitlement", SOON, "tariff", None, ("mop",),
                   "право определяется оплатой, а не вопросом клиенту"),
 "products.rop": P(AUTO_MIGRATE, "storage:billing->entitlement", SOON, "tariff", None, ("rop",),
                   "то же, что и МОП"),
 "products.cpx": P(AUTO_MIGRATE, "storage:cpx_advice|kpi_settings", SOON, "tariff", None, ("cpx",),
                   "подключение доказывается наличием контура ставок"),
 "products.reactivation": P(AUTO_MIGRATE, "reactivation_settings", IMPROVE, "tariff", None,
                            ("mop",), "таблица типизирована, доказывает и True и False"),
 "products.feed": P(AUTO_MIGRATE, "storage:feed_prefs|feed_items", SOON, "tariff", None, ("feed",),
                    "наличие фида доказывается данными"),
 "kpi.target_leads_per_day": P(ASK, "storage:kpi_settings", BLOCKER, "client", "goals", ("cpx",),
                               "цель по лидам - бизнес-решение, придумывать запрещено"),
 "kpi.max_cost_per_lead_rub": P(ASK, "storage:kpi_settings", BLOCKER, "client", "goals", ("cpx",),
                                "цена лида - деньги клиента"),
 "kpi.daily_budget_limit_rub": P(ASK, "storage:kpi_settings", BLOCKER, "client", "goals", ("cpx",),
                                 "дневной бюджет - деньги клиента"),
 "kpi.business_hours": P(DERIVE, "observed_activity", SOON, "client", "hours", ("mop", "cpx"),
                         "выводим из живой переписки и показываем на подтверждение"),
 "automation.mode": P(AUTO_MIGRATE, "storage:autopilot_settings", SOON, "owner", "automation",
                      ("cpx", "autopilot"), "режим уже задан в legacy, переносится"),
 "automation.money_actions": P(AUTO_MIGRATE, "money_mandates", BLOCKER, "owner", "automation",
                               ("autopilot",), "денежная автономность - мандат владельца"),
 "limits.max_actions_run": P(OPERATIONAL, "money_mandates|platform_cap", IMPROVE, "platform",
                             None, ("autopilot",), "технический предохранитель, не вопрос клиенту"),
 "limits.max_bid_delta_pct": P(OPERATIONAL, "money_mandates|platform_cap", IMPROVE, "platform",
                               None, ("autopilot",), "шаг ставки - безопасность платформы"),
 "limits.daily_budget_rub": P(AUTO_MIGRATE, "money_mandates", SOON, "owner", "automation",
                              ("autopilot",), "лимит расхода берётся из мандата"),
 "channels.avito": P(DERIVE, "accounts.avito_client_id", BLOCKER, "client", "connect",
                     ("platform",), "подключение доказывается наличием ключей"),
 "channels.telegram_routes": P(DERIVE, "tg_routes", IMPROVE, "owner", None, ("rop",),
                               "маршруты отчётов уже в типизированной таблице"),
 "knowledge.facts_scope": P(DERIVE, "client_facts", SOON, "platform", None, ("mop",),
                            "область знаний выводится из фактически загруженных фактов"),
 "knowledge.prompt_ref": P(DERIVE, "messenger_prompts", SOON, "platform", None, ("mop",),
                           "ссылка на промт есть в типизированной таблице"),
 "schedule.mop_poll": P(OPERATIONAL, "crontab->cadence", IMPROVE, "platform", None, ("mop",),
                        "частота опроса - внутренняя политика, клиент о ней не знает"),
 "schedule.autopilot": P(OPERATIONAL, "crontab->cadence", IMPROVE, "platform", None, ("cpx",),
                         "расписание автопилота - платформенное"),
 "schedule.memory_extract": P(OPERATIONAL, "crontab->cadence", IMPROVE, "platform", None, ("mop",),
                              "извлечение памяти - внутренний процесс"),
 "schedule.reports": P(OPERATIONAL, "platform_default", IMPROVE, "platform", None, ("rop",),
                       "частота отчётов - продуктовая политика"),
 "access.roles": P(DERIVE, "user_account_access", SOON, "owner", None, ("platform",),
                   "роли уже есть в типизированной таблице"),
}

SCREENS = {
 "connect": ("Подключение Avito", ["channels.avito"]),
 "goals": ("Цели и бюджет", ["profile.city", "kpi.target_leads_per_day",
                             "kpi.max_cost_per_lead_rub", "kpi.daily_budget_limit_rub"]),
 "hours": ("Режим работы (подтверждение)", ["kpi.business_hours"]),
 "voice": ("Как BORIS общается", ["profile.tone_of_voice"]),
 "automation": ("Разрешения автономности", ["automation.mode", "automation.money_actions",
                                            "limits.daily_budget_rub"]),
}
PRODUCTS = ("platform", "mop", "rop", "feed", "cpx", "autopilot")



def safe_exec(db, sql, params=None, mode="all"):
    """
    READ-ONLY SQL probe через SAVEPOINT.

    Ошибка конкретного необязательного источника не должна
    переводить всю SQLAlchemy/PostgreSQL transaction в aborted.

    mode:
      all    -> fetchall()
      one    -> fetchone()
      scalar -> scalar()
    """
    try:
        with db.begin_nested():
            q = db.execute(T(sql), params or {})

            if mode == "scalar":
                return q.scalar()

            if mode == "one":
                return q.fetchone()

            return q.fetchall()

    except Exception:
        return None


def st(db, acc, key):
    v = safe_exec(
        db,
        "SELECT value FROM storage WHERE account_id=:a AND key=:k",
        {"a": acc, "k": key},
        mode="scalar",
    )

    if not v:
        return None

    try:
        return json.loads(v)
    except Exception:
        return v


def one(db, sql, **kw):
    r = safe_exec(
        db,
        sql,
        kw,
        mode="one",
    )
    return r[0] if r else None


def detect_products(db, acc):
    """Какие контуры у клиента реально существуют. unknown != выключено."""
    out = {"platform": True}
    for p in ("mop", "rop"):
        d = E.has_entitlement(db, acc, p)
        out[p] = True if d.get("allowed") else (None if d.get("state") == E.UNKNOWN else False)
    out["feed"] = bool(st(db, acc, "feed_prefs") or st(db, acc, "feed_items"))
    out["cpx"] = bool(st(db, acc, "kpi_settings") or st(db, acc, "cpx_advice"))
    auto = st(db, acc, "autopilot_settings") or {}
    mode = (auto or {}).get("mode")
    out["autopilot"] = bool(mode and mode not in ("off", "manual"))
    return out


DERIVERS = {"profile.city": lambda db, a, ctx: d_city(db, a),
            "profile.timezone": lambda db, a, ctx: d_timezone(ctx.get("city")),
            "kpi.business_hours": lambda db, a, ctx: d_business_hours(db, a)}


def d_table(db, acc, field):
    """DERIVE/AUTO_MIGRATE из типизированных таблиц и legacy-хранилищ."""
    if field.startswith("schedule."):
        return d_schedule(field)
    if field == "channels.avito":
        n = one(db, "SELECT length(coalesce(avito_client_id,'')) FROM accounts WHERE account_id=:a", a=acc)
        return prov("avito:%s" % acc, "accounts.avito_client_id", 1.0,
                    "ключи API заполнены (%s симв)" % n) if n else None
    if field == "channels.telegram_routes":
        n = one(db, "SELECT count(*) FROM tg_routes WHERE account_id=:a", a=acc)
        return prov(n, "tg_routes", 1.0, "маршрутов в таблице: %s" % n) if n else None
    if field == "access.roles":
        n = one(db, "SELECT count(*) FROM user_account_access WHERE account_id=:a", a=acc)
        return prov(n, "user_account_access", 1.0, "ролей выдано: %s" % n) if n else None
    if field == "knowledge.facts_scope":
        n = one(db, "SELECT count(*) FROM client_facts WHERE account_id=:a", a=acc)
        return prov(n, "client_facts", 1.0, "фактов загружено: %s" % n) if n else None
    if field == "knowledge.prompt_ref":
        n = one(db, "SELECT count(*) FROM messenger_prompts WHERE account_id=:a", a=acc)
        return prov(n, "messenger_prompts", 1.0, "промтов: %s" % n) if n else None
    if field == "products.reactivation":
        r = one(db, "SELECT enabled FROM reactivation_settings WHERE account_id=:a", a=acc)
        return prov(bool(r), "reactivation_settings.enabled", 1.0,
                    "типизированная таблица доказывает и True и False") if r is not None else None
    if field in ("products.mop", "products.rop"):
        d = E.has_entitlement(db, acc, field.split(".")[1])
        if d.get("state") != E.UNKNOWN:
            return prov(bool(d.get("allowed")), "storage:billing->entitlements.decide", 1.0,
                        "право доказано оплатой (%s)" % d.get("reason"))
        return None
    if field == "products.feed":
        v = st(db, acc, "feed_prefs") or st(db, acc, "feed_items")
        return prov(True, "storage:feed_prefs|feed_items", 1.0, "фид присутствует") if v else None
    if field == "products.cpx":
        v = st(db, acc, "kpi_settings") or st(db, acc, "cpx_advice")
        return prov(True, "storage:kpi_settings|cpx_advice", 1.0, "контур ставок присутствует") if v else None
    if field == "automation.mode":
        m = (st(db, acc, "autopilot_settings") or {}).get("mode")
        return prov(m, "storage:autopilot_settings.mode", 0.95,
                    "перенос legacy-режима %r через LEGACY_MODE_MAP" % m) if m else None
    if field.startswith("limits.") or field == "automation.money_actions":
        r = db.execute(T("""SELECT daily_budget_rub, max_actions_run, max_bid_delta_pct FROM money_mandates
                            WHERE :a = ANY(account_scope) LIMIT 1"""), {"a": acc}).fetchone()
        if not r:
            return None
        val = {"limits.daily_budget_rub": r[0], "limits.max_actions_run": r[1],
               "automation.money_actions": True}.get(field)
        if field == "limits.max_bid_delta_pct":
            val = r[2]
        return prov(val, "money_mandates", 1.0, "мандат владельца") if val is not None else None
    if field.startswith("kpi."):
        k = st(db, acc, "kpi_settings") or {}
        m = {"kpi.target_leads_per_day": "target_leads_per_day",
             "kpi.max_cost_per_lead_rub": "max_cost_per_lead_rub",
             "kpi.daily_budget_limit_rub": "daily_budget_limit_rub"}.get(field)
        if m and k.get(m) is not None:
            return prov(k[m], "storage:kpi_settings.%s" % m, 1.0, "клиент уже задал цель ранее")
    return None


def classify(db, acc, field, node, products, ctx):
    pol = POLICY.get(field)
    tag, rule = S.FIELDS.get(field, ("?", "?"))
    base = {"field": field, "required_for": list(pol["products"]) if pol else [],
            "blocks_start": str(rule).startswith("block:"), "severity": pol["severity"] if pol else "?",
            "owner": pol["owner"] if pol else "?", "screen": pol["screen"] if pol else None,
            "reason": pol["why"] if pol else "POLICY_MISSING",
            "candidate_value": None, "candidate_source": None, "confidence": 0.0,
            "needs_confirmation": False, "current_source": None}
    if not pol:
        base["cls"] = UNKNOWN; return base
    live = [p for p in pol["products"] if products.get(p) is True]
    unknownp = [p for p in pol["products"] if products.get(p) is None]
    st_node = (node or {}).get("state")
    if st_node == S.KNOWN:
        base.update(cls=KNOWN, current_source=(node or {}).get("source"),
                    candidate_value=(node or {}).get("value"), confidence=1.0)
        return base
    if not live and not unknownp:
        base["cls"] = NOT_REQUIRED
        base["reason"] = "ни один потребитель поля не подключён: %s" % list(pol["products"])
        return base
    cand = (DERIVERS.get(field) or (lambda *a: None))(db, acc, ctx) or d_table(db, acc, field)
    if cand and cand.get("candidate_value") is not None:
        base.update(candidate_value=cand["candidate_value"], candidate_source=cand["source"],
                    confidence=cand["confidence"], needs_confirmation=cand["needs_confirmation"],
                    reason=cand["why"])
        base["cls"] = pol["fallback"] if pol["fallback"] in (DERIVE, AUTO_MIGRATE, OPERATIONAL) \
            else DERIVE
        return base
    if pol["fallback"] == OPERATIONAL or pol["owner"] == "platform":
        base["cls"] = OPERATIONAL
        base["reason"] = "назначает платформа: %s" % pol["candidate"]
        return base
    if pol["owner"] == "client":
        base["cls"] = ASK
        return base
    base["cls"] = UNKNOWN if pol["owner"] != "owner" else OPERATIONAL
    base["reason"] = "владелец решения: %s, источник пуст" % pol["owner"]
    return base


def run_account(db, acc):
    cu = db.connection().connection.cursor()
    try:
        legacy = C.build_config(C.gather(cu, acc))
    finally:
        cu.close()
    flat = {}
    for sec in S.SECTIONS:
        for n, node in (legacy.get(sec) or {}).items():
            flat["%s.%s" % (sec, n)] = node
    products = detect_products(db, acc)
    ctx = {"city": d_city(db, acc)}
    rows = [classify(db, acc, f, flat.get(f), products, ctx) for f in sorted(S.FIELDS.keys())]
    return products, rows

# =====================================================================
# FACTORY_A_FINISH_V1
# Недостающие деривации + агрегация.
# Client Config используется ТОЛЬКО как read-only контракт.
# =====================================================================

CITY_TZ = {
    "москва": "Europe/Moscow",
    "московская": "Europe/Moscow",
    "мо": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "спб": "Europe/Moscow",
    "пенза": "Europe/Moscow",
    "сочи": "Europe/Moscow",
    "калуга": "Europe/Moscow",
    "обнинск": "Europe/Moscow",
    "ижевск": "Europe/Samara",
    "уфа": "Asia/Yekaterinburg",
    "екатеринбург": "Asia/Yekaterinburg",
    "новосибирск": "Asia/Novosibirsk",
}

CRON_MAP = {
    "schedule.mop_poll": ("messenger_runner", "mop_runner"),
    "schedule.autopilot": ("ads_autopilot", "cpx", "kpi_goal_tick"),
    "schedule.memory_extract": ("client_memory_runner", "memory_extract"),
    "schedule.reports": ("exec_report", "rop_digest", "report"),
}

_TIME_COL = {}
_CRON = None


def prov(value, source, conf, why, confirm=False):
    return {
        "candidate_value": value,
        "source": source,
        "confidence": round(conf, 2),
        "why": why,
        "needs_confirmation": confirm,
    }


def d_city(db, acc):
    v = (st(db, acc, "feed_prefs") or {}).get("city")
    if v:
        return prov(
            v,
            "storage:feed_prefs.city",
            1.0,
            "город задан в настройках фида",
        )

    r = one(
        db,
        """SELECT o.form_data::text
           FROM user_onboarding o
           JOIN users u ON u.id=o.user_id
           WHERE u.account_id=:a
           ORDER BY o.updated_at DESC NULLS LAST
           LIMIT 1""",
        a=acc,
    )
    if r:
        try:
            import ast

            j = json.loads(r) if str(r).strip().startswith('{"') else ast.literal_eval(r)
            if isinstance(j, dict) and j.get("city"):
                return prov(
                    j["city"],
                    "user_onboarding.form_data.city",
                    0.9,
                    "город введён клиентом в анкете",
                    True,
                )
        except Exception:
            pass

    return None


def d_timezone(city_prov):
    if not city_prov or not city_prov.get("candidate_value"):
        return None

    raw = str(city_prov["candidate_value"]).strip().lower()
    key = raw.split(",")[0].split(" и ")[0]

    for k, tz in CITY_TZ.items():
        if key.startswith(k):
            return prov(
                tz,
                "derived:city_to_timezone(%s)" % city_prov["source"],
                city_prov["confidence"],
                "часовой пояс однозначен по городу %r"
                % city_prov["candidate_value"],
            )

    return None


def _time_col(db, table):
    if table not in _TIME_COL:
        r = db.execute(
            T(
                """SELECT column_name
                   FROM information_schema.columns
                   WHERE table_schema='public'
                     AND table_name=:t
                     AND (
                         data_type LIKE 'timestamp%'
                         OR column_name IN ('ts','created_at')
                     )
                   ORDER BY ordinal_position"""
            ),
            {"t": table},
        ).fetchall()

        _TIME_COL[table] = r[0][0] if r else None

    return _TIME_COL[table]


def d_business_hours(db, acc):
    """
    Выводим предполагаемое рабочее окно только из реальной активности.
    Меньше 20 событий -> ничего не придумываем.
    Всегда needs_confirmation=True.
    """
    for table in ("messenger_messages", "action_log"):
        col = _time_col(db, table)
        if not col:
            continue

        try:
            rows = safe_exec(
                db,
                'SELECT extract(hour from "%s") h, count(*) c '
                'FROM "%s" '
                "WHERE account_id=:a "
                "GROUP BY 1"
                % (col, table),
                {"a": acc},
                mode="all",
            )

            if rows is None:
                continue
        except Exception:
            continue

        total = sum(int(r[1]) for r in rows)
        if total < 20:
            continue

        hours = {int(r[0]): int(r[1]) for r in rows}

        best = None
        span = None

        for start in range(24):
            for length in range(6, 15):
                inside = sum(
                    hours.get((start + i) % 24, 0)
                    for i in range(length)
                )
                share = inside / float(total)

                # небольшой штраф за чрезмерно широкое окно
                score = share - length * 0.012

                if best is None or score > best:
                    best = score
                    span = (start, length, share)

        if span:
            s, length, share = span

            return prov(
                "%02d:00-%02d:00" % (s, (s + length) % 24),
                "observed_activity:%s(%s событий)" % (table, total),
                share,
                "в это окно попадает %d%% активности"
                % round(share * 100),
                True,
            )

    return None


def crontab_lines():
    global _CRON

    if _CRON is None:
        try:
            _CRON = subprocess.run(
                ["crontab", "-l"],
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        except Exception:
            _CRON = []

    return [
        line
        for line in _CRON
        if line.strip() and not line.strip().startswith("#")
    ]


def _cadence(expr):
    parts = expr.split()

    if parts and parts[0].startswith("*/"):
        return "intraday"

    if len(parts) > 1 and parts[1] == "*":
        return "hourly"

    return "daily"


def d_schedule(field):
    pats = CRON_MAP.get(field) or ()

    hits = [
        line
        for line in crontab_lines()
        if any(p in line for p in pats)
    ]

    if not hits:
        return None

    cad = _cadence(hits[0])

    return prov(
        {"enabled": True, "cadence": cad},
        "crontab(%d строк)" % len(hits),
        0.9,
        "перенос: %r -> cadence=%s"
        % (hits[0][:55], cad),
    )


# ---------------------------------------------------------------------
# DERIVERS
# ---------------------------------------------------------------------

DERIVERS = {
    "profile.city":
        lambda db, a, ctx: d_city(db, a),

    "profile.timezone":
        lambda db, a, ctx: d_timezone(ctx.get("city")),

    "kpi.business_hours":
        lambda db, a, ctx: d_business_hours(db, a),
}


def d_table(db, acc, field):
    """
    Доказуемые значения из существующих типизированных таблиц
    и legacy storage.

    Никаких записей.
    """
    if field.startswith("schedule."):
        return d_schedule(field)

    if field == "channels.avito":
        n = one(
            db,
            """SELECT length(coalesce(avito_client_id,''))
               FROM accounts
               WHERE account_id=:a""",
            a=acc,
        )

        if n:
            return prov(
                "avito:%s" % acc,
                "accounts.avito_client_id",
                1.0,
                "ключи API заполнены (%s симв)" % n,
            )

        return None

    if field == "channels.telegram_routes":
        try:
            n = one(
                db,
                "SELECT count(*) FROM tg_routes WHERE account_id=:a",
                a=acc,
            )
        except Exception:
            n = None

        if n:
            return prov(
                n,
                "tg_routes",
                1.0,
                "маршрутов в таблице: %s" % n,
            )

        return None

    if field == "access.roles":
        n = one(
            db,
            "SELECT count(*) FROM user_account_access WHERE account_id=:a",
            a=acc,
        )

        if n:
            return prov(
                n,
                "user_account_access",
                1.0,
                "ролей выдано: %s" % n,
            )

        return None

    if field == "knowledge.facts_scope":
        n = one(
            db,
            "SELECT count(*) FROM client_facts WHERE account_id=:a",
            a=acc,
        )

        if n:
            return prov(
                n,
                "client_facts",
                1.0,
                "фактов загружено: %s" % n,
            )

        return None

    if field == "knowledge.prompt_ref":
        try:
            n = one(
                db,
                "SELECT count(*) FROM messenger_prompts WHERE account_id=:a",
                a=acc,
            )
        except Exception:
            n = None

        if n:
            return prov(
                n,
                "messenger_prompts",
                1.0,
                "промтов: %s" % n,
            )

        return None

    if field == "products.reactivation":
        try:
            r = one(
                db,
                """SELECT enabled
                   FROM reactivation_settings
                   WHERE account_id=:a""",
                a=acc,
            )
        except Exception:
            r = None

        if r is not None:
            return prov(
                bool(r),
                "reactivation_settings.enabled",
                1.0,
                "типизированная таблица доказывает True/False",
            )

        return None

    if field in ("products.mop", "products.rop"):
        d = E.has_entitlement(
            db,
            acc,
            field.split(".")[1],
        )

        if d.get("state") != E.UNKNOWN:
            return prov(
                bool(d.get("allowed")),
                "storage:billing->entitlements.decide",
                1.0,
                "право доказано оплатой (%s)"
                % d.get("reason"),
            )

        return None

    if field == "products.feed":
        v = (
            st(db, acc, "feed_prefs")
            or st(db, acc, "feed_items")
        )

        if v:
            return prov(
                True,
                "storage:feed_prefs|feed_items",
                1.0,
                "фид присутствует",
            )

        return None

    if field == "products.cpx":
        v = (
            st(db, acc, "kpi_settings")
            or st(db, acc, "cpx_advice")
        )

        if v:
            return prov(
                True,
                "storage:kpi_settings|cpx_advice",
                1.0,
                "контур ставок присутствует",
            )

        return None

    if field == "automation.mode":
        m = (
            st(db, acc, "autopilot_settings")
            or {}
        ).get("mode")

        if m:
            return prov(
                m,
                "storage:autopilot_settings.mode",
                0.95,
                "перенос legacy-режима %r" % m,
            )

        return None

    if (
        field.startswith("limits.")
        or field == "automation.money_actions"
    ):
        try:
            r = safe_exec(
                db,
                """SELECT daily_budget_rub,
                          max_actions_run,
                          max_bid_delta_pct
                   FROM money_mandates
                   WHERE :a = ANY(account_scope)
                   LIMIT 1""",
                {"a": acc},
                mode="one",
            )
        except Exception:
            r = None

        if not r:
            return None

        val = {
            "limits.daily_budget_rub": r[0],
            "limits.max_actions_run": r[1],
            "automation.money_actions": True,
        }.get(field)

        if field == "limits.max_bid_delta_pct":
            val = r[2]

        if val is not None:
            return prov(
                val,
                "money_mandates",
                1.0,
                "мандат владельца",
            )

        return None

    if field.startswith("kpi."):
        k = st(db, acc, "kpi_settings") or {}

        m = {
            "kpi.target_leads_per_day":
                "target_leads_per_day",

            "kpi.max_cost_per_lead_rub":
                "max_cost_per_lead_rub",

            "kpi.daily_budget_limit_rub":
                "daily_budget_limit_rub",

        }.get(field)

        if m and k.get(m) is not None:
            return prov(
                k[m],
                "storage:kpi_settings.%s" % m,
                1.0,
                "клиент уже задал цель ранее",
            )

    return None


def classify(db, acc, field, node, products, ctx):
    pol = POLICY.get(field)
    tag, rule = S.FIELDS.get(field, ("?", "?"))

    base = {
        "field": field,
        "required_for":
            list(pol["products"]) if pol else [],

        "blocks_start":
            str(rule).startswith("block:"),

        "severity":
            pol["severity"] if pol else "?",

        "owner":
            pol["owner"] if pol else "?",

        "screen":
            pol["screen"] if pol else None,

        "reason":
            pol["why"] if pol else "POLICY_MISSING",

        "candidate_value": None,
        "candidate_source": None,
        "confidence": 0.0,
        "needs_confirmation": False,
        "current_source": None,
    }

    if not pol:
        base["cls"] = UNKNOWN
        return base

    live = [
        p
        for p in pol["products"]
        if products.get(p) is True
    ]

    unknownp = [
        p
        for p in pol["products"]
        if products.get(p) is None
    ]

    st_node = (node or {}).get("state")

    if st_node == S.KNOWN:
        base.update(
            cls=KNOWN,
            current_source=(node or {}).get("source"),
            candidate_value=(node or {}).get("value"),
            confidence=1.0,
        )
        return base

    if not live and not unknownp:
        base["cls"] = NOT_REQUIRED
        base["reason"] = (
            "ни один потребитель поля не подключён: %s"
            % list(pol["products"])
        )
        return base

    deriver = DERIVERS.get(field)

    cand = (
        deriver(db, acc, ctx)
        if deriver
        else None
    )

    if not cand:
        cand = d_table(db, acc, field)

    if cand and cand.get("candidate_value") is not None:
        base.update(
            candidate_value=cand["candidate_value"],
            candidate_source=cand["source"],
            confidence=cand["confidence"],
            needs_confirmation=cand["needs_confirmation"],
            reason=cand["why"],
        )

        if pol["fallback"] in (
            DERIVE,
            AUTO_MIGRATE,
            OPERATIONAL,
        ):
            base["cls"] = pol["fallback"]
        else:
            base["cls"] = DERIVE

        return base

    if (
        pol["fallback"] == OPERATIONAL
        or pol["owner"] == "platform"
    ):
        base["cls"] = OPERATIONAL
        base["reason"] = (
            "назначает платформа: %s"
            % pol["candidate"]
        )
        return base

    if pol["owner"] == "client":
        base["cls"] = ASK
        return base

    base["cls"] = (
        OPERATIONAL
        if pol["owner"] == "owner"
        else UNKNOWN
    )

    base["reason"] = (
        "владелец решения: %s, источник пуст"
        % pol["owner"]
    )

    return base


def run_account(db, acc):
    cu = db.connection().connection.cursor()

    try:
        legacy = C.build_config(
            C.gather(cu, acc)
        )
    finally:
        cu.close()

    flat = {}

    for sec in S.SECTIONS:
        for name, node in (
            legacy.get(sec) or {}
        ).items():
            flat["%s.%s" % (sec, name)] = node

    products = detect_products(db, acc)

    ctx = {
        "city": d_city(db, acc),
    }

    rows = [
        classify(
            db,
            acc,
            field,
            flat.get(field),
            products,
            ctx,
        )
        for field in sorted(S.FIELDS.keys())
    ]

    return products, rows


OUT = []


def log(s, quiet=False):
    OUT.append(str(s))

    if not quiet:
        print(s)


def screens_for(rows):
    out = set()

    for r in rows:
        needs_human = (
            r["cls"] == ASK
            or (
                r["cls"] == DERIVE
                and r["needs_confirmation"]
            )
        )

        if needs_human and r["screen"]:
            out.add(r["screen"])

    return out


def readiness(rows, products):
    out = {}

    for product in PRODUCTS:
        if (
            products.get(product) is not True
            and product != "platform"
        ):
            out[product] = "не подключён"
            continue

        mine = [
            r
            for r in rows
            if product in r["required_for"]
            and r["cls"] != NOT_REQUIRED
        ]

        blockers = [
            r
            for r in mine
            if r["severity"] == BLOCKER
            and r["cls"] in (
                ASK,
                UNKNOWN,
                CONFLICT,
            )
        ]

        if not blockers:
            out[product] = "ГОТОВ"
        else:
            out[product] = (
                "блок: "
                + ",".join(
                    b["field"].split(".")[-1]
                    for b in blockers
                )
            )

    return out


def main():
    db = SessionLocal()

    try:
        accs = [
            r[0]
            for r in db.execute(
                T(
                    """SELECT DISTINCT account_id
                       FROM client_config
                       WHERE account_id IS NOT NULL
                       ORDER BY 1"""
                )
            ).fetchall()
        ]

        log(
            "=== FACTORY A: полей в контракте %d, аккаунтов %d ==="
            % (len(S.FIELDS), len(accs))
        )

        missing = [
            f
            for f in S.FIELDS
            if f not in POLICY
        ]

        extra = [
            f
            for f in POLICY
            if f not in S.FIELDS
        ]

        log(
            "  POLICY_MISSING: %s"
            % (
                missing
                or "нет — политика покрывает контракт"
            )
        )

        log(
            "  POLICY_EXTRA: %s"
            % (
                extra
                or "нет"
            )
        )

        if missing or extra:
            raise RuntimeError(
                "Factory POLICY не совпадает с Client Config contract"
            )

        tot = collections.Counter()
        sev = collections.Counter()

        per_field = collections.defaultdict(
            collections.Counter
        )

        screens_hist = collections.Counter()

        ready_hist = collections.defaultdict(
            collections.Counter
        )

        per_acc = {}
        errors = []

        for acc in accs:
            try:
                products, rows = run_account(
                    db,
                    acc,
                )
            except Exception as e:
                errors.append(
                    (acc, str(e)[:160])
                )

                log(
                    "  ОШИБКА %s: %s"
                    % (acc, str(e)[:120])
                )

                # SELECT-ошибка не должна отравить
                # следующую проверку.
                try:
                    db.rollback()
                except Exception:
                    pass

                continue

            for r in rows:
                tot[r["cls"]] += 1
                per_field[r["field"]][r["cls"]] += 1

                if r["cls"] in (
                    ASK,
                    UNKNOWN,
                ):
                    sev[r["severity"]] += 1

            screens = screens_for(rows)

            screens_hist[
                len(screens)
            ] += 1

            rd = readiness(
                rows,
                products,
            )

            for product, verdict in rd.items():
                bucket = (
                    "ГОТОВ"
                    if verdict == "ГОТОВ"
                    else (
                        "не подключён"
                        if verdict == "не подключён"
                        else "не готов"
                    )
                )

                ready_hist[
                    product
                ][
                    bucket
                ] += 1

            per_acc[acc] = {
                "products": products,
                "screens": sorted(screens),
                "ready": rd,
                "rows": rows,
            }

        total_cells = sum(tot.values())

        log(
            "=== КЛАССЫ (%d ячеек) ==="
            % total_cells
        )

        for cls in CLASSES:
            log(
                "  %-18s %4d  %5.1f%%"
                % (
                    cls,
                    tot[cls],
                    100.0
                    * tot[cls]
                    / max(1, total_cells),
                )
            )

        log(
            "=== SEVERITY среди ASK/UNKNOWN ===  %s"
            % dict(sev)
        )

        log(
            "=== ЧЕЛОВЕЧЕСКИЕ ЭКРАНЫ НА КЛИЕНТА ==="
        )

        for n in sorted(screens_hist):
            log(
                "  %d экранов: %d аккаунтов"
                % (n, screens_hist[n])
            )

        avg = (
            sum(
                k * v
                for k, v in screens_hist.items()
            )
            / float(
                max(
                    1,
                    sum(screens_hist.values()),
                )
            )
        )

        log(
            "  СРЕДНЕЕ: %.2f экрана на клиента"
            % avg
        )

        log(
            "=== READINESS ПО ПРОДУКТАМ ==="
        )

        for product in PRODUCTS:
            log(
                "  %-10s %s"
                % (
                    product,
                    dict(ready_hist[product]),
                )
            )

        log(
            "=== ПОЛЯ: КЛАСС ПО БОЛЬШИНСТВУ ==="
        )

        for field in sorted(per_field):
            winner = per_field[
                field
            ].most_common(1)[0]

            log(
                "  %-30s %-18s %d/%d"
                % (
                    field,
                    winner[0],
                    winner[1],
                    len(accs),
                )
            )

        log(
            "=== ZERO-TOUCH: НОВЫЙ КЛИЕНТ БЕЗ ДАННЫХ ==="
        )

        try:
            products, rows = run_account(
                db,
                "__factory_probe_%s"
                % datetime.date.today(),
            )

            zc = collections.Counter(
                r["cls"]
                for r in rows
            )

            zs = screens_for(rows)

            blockers = [
                r
                for r in rows
                if r["cls"] == ASK
                and r["severity"] == BLOCKER
            ]

            log(
                "  классы: %s"
                % dict(zc)
            )

            log(
                "  человеческих экранов: %d -> %s"
                % (
                    len(zs),
                    sorted(zs),
                )
            )

            log(
                "  из них blocker-полей: %d"
                % len(blockers)
            )

        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass

            log(
                "  проба не удалась: %s"
                % str(e)[:120]
            )

        log(
            "=== ПЯТЬ КЛИЕНТСКИХ КАБИНЕТОВ ==="
        )

        for acc in (
            "prodazha_bytovok_25677",
            "3411770_94346",
            "garik_plitka_mo_58647",
            "pbi_artashes_plitka_mo_83993",
            "evgeniy_peregorodki_12447",
        ):
            v = per_acc.get(acc)

            if not v:
                log(
                    "  %-30s нет черновика Client Config"
                    % acc[:30]
                )
                continue

            log(
                "  %-30s экранов=%d %s"
                % (
                    acc[:30],
                    len(v["screens"]),
                    v["screens"],
                )
            )

            log(
                "       %s"
                % v["ready"]
            )

        log(
            "=== ERRORS: %d ==="
            % len(errors)
        )

        for acc, err in errors[:20]:
            log(
                "  %s -> %s"
                % (acc, err)
            )

        report = "/tmp/factory_a_report.txt"

        with open(
            report,
            "w",
            encoding="utf-8",
        ) as fh:

            fh.write(
                "\n".join(OUT)
            )

            fh.write(
                "\n\n=== ДЕТАЛИ ПО АККАУНТАМ ===\n"
            )

            for acc, v in sorted(
                per_acc.items()
            ):
                fh.write(
                    "\n## %s  products=%s\n"
                    % (
                        acc,
                        v["products"],
                    )
                )

                for r in v["rows"]:
                    fh.write(
                        "  %-30s %-18s %-14s conf=%.2f src=%s | %s\n"
                        % (
                            r["field"],
                            r["cls"],
                            r["severity"],
                            r["confidence"],
                            r["candidate_source"]
                            or r["current_source"]
                            or "-",
                            r["reason"][:90],
                        )
                    )

        log(
            "=== отчёт: %s ==="
            % report
        )

        if errors:
            # Анализ частично выполнен, но PASS
            # не выдаём молча.
            return 2

        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
