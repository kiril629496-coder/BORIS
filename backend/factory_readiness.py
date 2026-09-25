"""BORIS Factory A (FACTORY_A_V1): what BORIS knows, derives, migrates,
assigns - and what a human really has to answer.

READ-ONLY: no writes, no migration, no cron change, no client messages.
Client Config is an EXTERNAL CONTRACT here: app/client_config/* is owned by
another session and is never modified from this module.
"""

import json
import re
import subprocess
from collections import Counter, defaultdict

from sqlalchemy import text

from app.client_config import entitlements as E
from app.client_config import resolver as R
from app.client_config import schema as S
from app.db.session import SessionLocal

KNOWN = "KNOWN"
DERIVE = "DERIVE"
AUTO_MIGRATE = "AUTO_MIGRATE"
OPERATIONAL = "OPERATIONAL_CONFIG"
ASK = "ASK_CLIENT"
NOT_REQUIRED = "NOT_REQUIRED"

BLOCKER = "blocker"
SOON = "required_soon"
IMPROVE = "improvement"

PRODUCT_FIELDS = {
    "platform": {"channels.avito": BLOCKER, "access.roles": BLOCKER,
                 "profile.timezone": IMPROVE},
    "mop": {"profile.tone_of_voice": SOON, "kpi.business_hours": SOON,
            "knowledge.prompt_ref": IMPROVE, "knowledge.facts_scope": IMPROVE,
            "products.mop": IMPROVE, "schedule.mop_poll": SOON},
    "rop": {"products.rop": IMPROVE},
    "feed": {"profile.city": SOON, "products.feed": IMPROVE},
    "cpx": {"kpi.target_leads_per_day": BLOCKER, "kpi.max_cost_per_lead_rub": BLOCKER,
            "kpi.daily_budget_limit_rub": BLOCKER, "limits.max_bid_delta_pct": BLOCKER,
            "limits.max_actions_run": BLOCKER, "limits.daily_budget_rub": SOON,
            "automation.money_actions": BLOCKER, "schedule.autopilot": SOON,
            "products.cpx": IMPROVE},
    "autopilot": {"automation.mode": BLOCKER},
    "reports": {"channels.telegram_routes": SOON, "schedule.reports": IMPROVE},
}

ASK_FIELDS = ("kpi.target_leads_per_day", "kpi.max_cost_per_lead_rub",
              "kpi.daily_budget_limit_rub", "profile.tone_of_voice")

OPERATIONAL_FIELDS = ("channels.avito", "channels.telegram_routes", "access.roles",
                      "automation.mode", "automation.money_actions", "schedule.reports")

SCREENS = {
    "kpi.target_leads_per_day": "Цели и бюджет",
    "kpi.max_cost_per_lead_rub": "Цели и бюджет",
    "kpi.daily_budget_limit_rub": "Цели и бюджет",
    "profile.tone_of_voice": "Как общаться с покупателем",
    "kpi.business_hours": "Подтвердите режим работы",
}

TZ_BY_CITY = (
    ("москв", "Europe/Moscow"), ("подмосков", "Europe/Moscow"), ("спб", "Europe/Moscow"),
    ("петербур", "Europe/Moscow"), ("сочи", "Europe/Moscow"), ("пенз", "Europe/Moscow"),
    ("калуж", "Europe/Moscow"), ("екатеринбур", "Asia/Yekaterinburg"),
    ("новосибир", "Asia/Novosibirsk"), ("красноярск", "Asia/Krasnoyarsk"),
)


def _systemd_active(unit):
    """Cheap runtime fact; fail closed on any systemctl error."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return r.returncode == 0
    except Exception:
        return False


def _central_schedule_truth(db, field):
    """Prove centralized scheduler ownership instead of inventing per-account cron."""
    if field == "schedule.autopilot":
        if not _systemd_active("boris-ai-marketer.timer"):
            return None
        try:
            script = open("/root/BORIS/backend/ai_marketer_hourly.sh", "r", encoding="utf-8").read()
        except Exception:
            return None
        marker = "kpi_goal_runner.py --non-money-apply"
        if marker not in script:
            return None
        return {
            "value": {"enabled": True, "cadence": "hourly"},
            "source": "systemd:boris-ai-marketer.timer",
            "confidence": 1.0,
            "why": "central hourly marketer owns the active-account autopilot cycle",
        }

    if field == "schedule.mop_poll":
        if not _systemd_active("boris-background-worker.service"):
            return None
        row = db.execute(text(
            "SELECT state, extract(epoch from (now()-last_seen_at)) AS age_sec "
            "FROM reliability_heartbeats "
            "WHERE module='messenger' AND worker_id='poller' "
            "ORDER BY last_seen_at DESC LIMIT 1"
        )).fetchone()
        if not row:
            return None
        state = str(row[0] or "")
        try:
            age_sec = float(row[1])
        except Exception:
            return None
        if state != "ok" or age_sec < 0 or age_sec > 180:
            return None
        return {
            "value": {"enabled": True, "cadence": "continuous"},
            "source": "systemd:boris-background-worker.service|heartbeat:messenger/poller",
            "confidence": 1.0,
            "why": "central singleton messenger poller is running and heartbeat is fresh",
            "heartbeat_age_sec": round(age_sec, 1),
        }

    return None


def cron_to_cadence(expr):
    parts = expr.split()
    if len(parts) < 5:
        return None
    minute, hour = parts[0], parts[1]
    if minute.startswith("*/") and hour == "*":
        return "intraday"
    if hour == "*":
        return "hourly"
    if "," in hour or "-" in hour or hour.startswith("*/"):
        return "intraday"
    return "daily"


def client_cron(accounts):
    """Exact account_id match against real cron lines. No token heuristics."""
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except Exception:
        return {}
    found = defaultdict(list)
    for line in out.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        for acc in accounts:
            if acc in line:
                m = re.match(r"^(\S+ \S+ \S+ \S+ \S+)", line)
                expr = m.group(1) if m else ""
                job = "autopilot" if "ads_autopilot" in line else (
                    "mop_poll" if "messenger_runner" in line else (
                        "memory_extract" if "client_memory_runner" in line else (
                            "reports" if "client_report" in line or "rop_digest" in line else "other")))
                found[acc].append({"job": job, "cron": expr, "cadence": cron_to_cadence(expr)})
    return dict(found)


def tz_for(city):
    if not city:
        return None, 0.0
    low = str(city).lower()
    for key, tz in TZ_BY_CITY:
        if key in low:
            return tz, 0.9
    return None, 0.0


def _time_column(db, table):
    """Never guess a column name: ask information_schema."""
    rows = db.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
        " AND data_type LIKE 'timestamp%%' ORDER BY ordinal_position"), {"t": table}).fetchall()
    cols = [r[0] for r in rows]
    for pref in ("created_at", "ts", "sent_at", "msg_time", "created", "updated_at", "read_at"):
        if pref in cols:
            return pref
    return cols[0] if cols else None


def business_hours_candidate(db, account_id, col=None):
    col = col or _time_column(db, "messenger_messages")
    if not col:
        return None
    row = db.execute(text(
        "SELECT count(*), min(extract(hour from %s)), max(extract(hour from %s))"
        " FROM messenger_messages WHERE account_id=:a" % (col, col)), {"a": account_id}).fetchone()
    if not row or not row[0] or row[0] < 20:
        return None
    return {"candidate_value": "%02d:00-%02d:00" % (int(row[1]), int(row[2])),
            "source": "messenger_messages.%s" % col,
            "confidence": min(0.5 + row[0] / 2000.0, 0.8),
            "why": "окно фактической переписки (%d сообщений)" % row[0],
            "needs_confirmation": True}


def _storage_json(db, account_id, key):
    """Read the newest account-scoped JSON storage row without inventing defaults."""
    raw = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"
    ), {"a": account_id, "k": key}).scalar()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _canonical_kpi_write(db, account_id):
    """Latest structured KPI save coming from the authenticated KPI endpoint."""
    row = db.execute(text(
        "SELECT v.snapshot_json "
        "FROM control_requirement_versions v "
        "JOIN control_requirements r ON r.id=v.requirement_id "
        "WHERE r.scope_type=:scope_type AND r.scope_id=:a "
        "AND v.change_reason=:reason "
        "ORDER BY v.created_at DESC LIMIT 1"
    ), {
        "scope_type": "account",
        "a": account_id,
        "reason": "canonical KPI settings write",
    }).scalar()
    if not isinstance(row, dict):
        return {}
    source = row.get("source_json") or {}
    desired = row.get("desired_state_json") or {}
    if (
        source.get("last_sync_ref") != "api:set_kpi_settings"
        or source.get("last_sync_source") != "canonical_domain_write"
    ):
        return {}
    return desired if isinstance(desired, dict) else {}


def _authoritative_control_kpi(db, account_id):
    """Read KPI goals only from an active authoritative owner-rule projection.

    This is deliberately narrower than generic Control Plane state. A legacy
    production import is not owner consent by itself: accept only an active
    account rule that also carries direct owner/authenticated provenance.
    Budget authorization is NOT inferred here.
    """
    row = db.execute(text(
        "SELECT desired_state_json,source_json,raw_owner_text,created_by "
        "FROM control_requirements "
        "WHERE scope_type=:scope_type AND scope_id=:a "
        "AND key=:key AND status='active' "
        "AND superseded_by IS NULL "
        "ORDER BY version DESC,updated_at DESC LIMIT 1"
    ), {
        "scope_type": "account",
        "a": account_id,
        "key": "production.marketing.autopilot.%s" % account_id,
    }).mappings().first()
    if not row:
        return {}
    desired = row.get("desired_state_json") or {}
    source = row.get("source_json") or {}
    if not isinstance(desired, dict) or not isinstance(source, dict):
        return {}
    if not source.get("owner_rule") or source.get("authoritative_after_import") is not True:
        return {}
    source_type = str(source.get("type") or "").strip().lower()
    raw_owner_text = str(row.get("raw_owner_text") or "").strip()
    created_by = row.get("created_by")
    direct_owner_provenance = bool(raw_owner_text) or created_by is not None
    if "legacy" in source_type and not direct_owner_provenance:
        return {}
    return {
        "target_leads_per_day": desired.get("target_leads_per_day"),
        "max_cost_per_lead_rub": desired.get("max_cost_per_lead_rub"),
        "source": "control_requirements.owner_rule:%s" % source.get("owner_rule"),
    }


def _confirmed_profile_facts(db, account_id):
    """Use only client-confirmed facts for fallback city/tone evidence."""
    rows = db.execute(text(
        "SELECT name,value FROM client_facts "
        "WHERE account_id=:a AND status='confirmed' AND source_type='client' "
        "ORDER BY updated_at DESC NULLS LAST LIMIT 200"
    ), {"a": account_id}).fetchall()
    city = None
    city_source = None
    tone = None
    tone_source = None
    city_markers = (
        ("Санкт-Петербург", ("санкт-петер", "петербург", "спб", "ленинградск")),
        ("Москва", ("москва", "московск")),
        ("Екатеринбург", ("екатеринбург",)),
        ("Новосибирск", ("новосибирск",)),
        ("Красноярск", ("красноярск",)),
        ("Пенза", ("пенз",)),
        ("Сочи", ("сочи",)),
        ("Калуга", ("калуж",)),
    )
    for name, value in rows:
        name_text = str(name or "").strip()
        value_text = str(value or "").strip()
        blob = (name_text + " " + value_text).lower()
        if not city:
            for canonical_city, markers in city_markers:
                if any(marker in blob for marker in markers):
                    city = canonical_city
                    city_source = "client_facts.confirmed:%s" % name_text
                    break
        if not tone and "тон" in name_text.lower() and value_text:
            tone = value_text
            tone_source = "client_facts.confirmed:%s" % name_text
    return {
        "city": city,
        "city_source": city_source,
        "tone": tone,
        "tone_source": tone_source,
    }


def _active_money_mandates(db, account_id):
    """Canonical current money authority for Factory readiness.

    Match the production autonomy guard: revoked, inactive, future and expired
    rows are not authority. Prefer the ordinary paid KPI lane over launch-only
    authority when both are active.
    """
    rows = db.execute(text(
        "SELECT id,version,status,source,allowed_operations,daily_budget_rub,"
        "max_actions_run,max_actions_day,max_bid_delta_pct,min_bid_rub,max_bid_rub,"
        "reserve_rub,valid_from,valid_until,revoked_at,granted_by_user_id "
        "FROM money_mandates "
        "WHERE :a = ANY(account_scope) "
        "AND status='active' AND revoked_at IS NULL "
        "AND (valid_from IS NULL OR valid_from <= now()) "
        "AND (valid_until IS NULL OR valid_until > now()) "
        "ORDER BY CASE source WHEN 'paid_tariff_kpi' THEN 0 WHEN 'new_feed_launch' THEN 1 ELSE 2 END, id DESC"
    ), {"a": account_id}).mappings().all()
    return [dict(r) for r in rows]


def _effective_money_mandate(db, account_id):
    rows = _active_money_mandates(db, account_id)
    return rows[0] if rows else None


def products_of(db, account_id):
    out = ["platform"]
    for p in ("mop", "rop"):
        r = E.has_entitlement(db, account_id, p)
        if r.get("state") == "known" and r.get("allowed"):
            out.append(p)
    if _effective_money_mandate(db, account_id):
        out.append("cpx")
    if db.execute(text("SELECT 1 FROM storage WHERE account_id=:a AND key='feed_items' LIMIT 1"),
                  {"a": account_id}).fetchone():
        out.append("feed")
    if db.execute(text("SELECT 1 FROM tg_routes WHERE account_id=:a AND thread_key='reports' LIMIT 1"),
                  {"a": account_id}).fetchone():
        out.append("reports")
    if db.execute(text("SELECT 1 FROM storage WHERE account_id=:a AND key='autopilot_settings' LIMIT 1"),
                  {"a": account_id}).fetchone():
        out.append("autopilot")
    return out


def classify(db, account_id, field, eff, ctx):
    """-> (class, severity, evidence). Never invents a value."""
    node = eff.get(field) or {}
    required = {}
    for p in ctx["products"]:
        required.update(PRODUCT_FIELDS.get(p, {}))
    if field not in required:
        return (NOT_REQUIRED, IMPROVE, {"reason": "нет продукта, которому это поле нужно"})
    sev = required[field]
    if node.get("state") == S.KNOWN:
        return (KNOWN, sev, {"value": node.get("value"), "source": node.get("source")})

    mandate = ctx.get("mandate") or {}
    allowed_operations = set(mandate.get("allowed_operations") or [])
    raise_allowed = "cpx.raise_bid" in allowed_operations

    # FACTORY_ACTIVE_MONEY_MANDATE_TRUTH_V1
    # An already-active mandate is evidence of the current grant, not a request
    # to grant money authority again. The concrete operation list still remains
    # authoritative; lower-only never implies raise authority.
    if field == "automation.money_actions" and mandate:
        return (DERIVE, sev, {
            "candidate_value": True,
            "source": "money_mandates:%s" % mandate.get("id"),
            "confidence": 1.0,
            "why": "active non-revoked money mandate already exists",
            "allowed_operations": sorted(allowed_operations),
            "mandate_source": mandate.get("source"),
        })

    if field in ("kpi.daily_budget_limit_rub", "limits.daily_budget_rub") and mandate and not raise_allowed:
        return (NOT_REQUIRED, IMPROVE, {
            "reason": "active mandate is lower-only; no autonomous spend increase is authorized",
            "source": "money_mandates:%s" % mandate.get("id"),
            "allowed_operations": sorted(allowed_operations),
        })

    # ZERO_TOUCH_CANONICAL_CONNECTIVITY_V1
    # Factory readiness must use the same tenant truth as production access.
    # Missing client_config projection is not evidence that Avito/access are absent
    # when canonical production tables already prove them.
    if field == "channels.avito":
        client_id_len = db.execute(text(
            "SELECT length(coalesce(avito_client_id,'')) FROM accounts WHERE account_id=:a"
        ), {"a": account_id}).scalar()
        if client_id_len:
            return (DERIVE, sev, {
                "candidate_value": "avito:%s" % account_id,
                "source": "accounts.avito_client_id",
                "confidence": 1.0,
                "why": "Avito API connection is already present in the account",
            })

    if field == "access.roles":
        access = db.execute(text("""
            SELECT
                a.owner_user_id,
                EXISTS (
                    SELECT 1
                    FROM user_account_access u
                    WHERE u.account_id=a.account_id
                      AND u.can_view=TRUE
                ) AS has_view_grant
            FROM accounts a
            WHERE a.account_id=:a
            LIMIT 1
        """), {"a": account_id}).mappings().first()
        if access and (access.get("owner_user_id") is not None or access.get("has_view_grant")):
            return (DERIVE, sev, {
                "candidate_value": True,
                "source": "accounts.owner_user_id|user_account_access.can_view",
                "confidence": 1.0,
                "why": "tenant access is already proven by owner or explicit view grant",
            })

    # FACTORY_KPI_PROVENANCE_TRUTH_V1
    # Existing KPI values remove an owner question only when their origin is
    # explicit and auditable. Legacy/imported numbers without owner provenance
    # remain ASK_CLIENT.
    if field == "kpi.target_leads_per_day":
        kpi = ctx.get("kpi") or {}
        kpi_write = ctx.get("kpi_write") or {}
        value = kpi.get("target_leads_per_day")
        source = str(kpi.get("target_leads_source") or kpi.get("goal_source") or "")
        try:
            valid_value = float(value) > 0
        except Exception:
            valid_value = False
        explicit_source = (
            source == "explicit"
            or source.startswith("owner_confirmed")
            or source.startswith("authenticated_")
        )
        if valid_value and explicit_source:
            return (DERIVE, sev, {
                "candidate_value": float(value),
                "source": "storage:kpi_settings.%s" % ("target_leads_source" if kpi.get("target_leads_source") else "goal_source"),
                "confidence": 1.0,
                "why": "target leads already has explicit owner provenance",
            })
        try:
            exact_saved = valid_value and abs(float(value) - float(kpi_write.get("target_leads_per_day"))) < 0.000001
        except Exception:
            exact_saved = False
        if exact_saved:
            return (DERIVE, sev, {
                "candidate_value": float(value),
                "source": "control_requirement_versions:canonical KPI settings write",
                "confidence": 1.0,
                "why": "target leads exactly matches the latest authenticated KPI save",
            })
        control_kpi = ctx.get("control_kpi") or {}
        try:
            exact_control = valid_value and abs(float(value) - float(control_kpi.get("target_leads_per_day"))) < 0.000001
        except Exception:
            exact_control = False
        if exact_control:
            return (DERIVE, sev, {
                "candidate_value": float(value),
                "source": control_kpi.get("source"),
                "confidence": 1.0,
                "why": "target leads exactly matches the active authoritative owner rule",
            })

    if field == "kpi.max_cost_per_lead_rub":
        kpi = ctx.get("kpi") or {}
        # ZERO_TOUCH_PAUSED_MONEY_KPI_V1
        # A money-only KPI must not block client launch while bid automation is
        # explicitly disabled in canonical KPI settings. Fail closed again as
        # soon as bid_autopilot is re-enabled: no CPL is invented or migrated.
        if kpi.get("bid_autopilot") is False:
            return (NOT_REQUIRED, IMPROVE, {
                "reason": "bid autopilot is explicitly disabled; max CPL is not required until money automation is enabled",
                "source": "storage:kpi_settings.bid_autopilot",
            })
        kpi_write = ctx.get("kpi_write") or {}
        value = kpi.get("max_cost_per_lead_rub")
        source = str(kpi.get("max_cost_per_lead_source") or "")
        try:
            valid_value = float(value) > 0
        except Exception:
            valid_value = False
        explicit_source = (
            source == "explicit"
            or source.startswith("owner_confirmed")
            or source.startswith("authenticated_")
        )
        if valid_value and explicit_source:
            return (DERIVE, sev, {
                "candidate_value": float(value),
                "source": "storage:kpi_settings.max_cost_per_lead_source",
                "confidence": 1.0,
                "why": "max CPL already has explicit owner provenance",
            })
        try:
            exact_saved = valid_value and abs(float(value) - float(kpi_write.get("max_cost_per_lead_rub"))) < 0.000001
        except Exception:
            exact_saved = False
        if exact_saved:
            return (DERIVE, sev, {
                "candidate_value": float(value),
                "source": "control_requirement_versions:canonical KPI settings write",
                "confidence": 1.0,
                "why": "max CPL exactly matches the latest authenticated KPI save",
            })
        control_kpi = ctx.get("control_kpi") or {}
        try:
            exact_control = valid_value and abs(float(value) - float(control_kpi.get("max_cost_per_lead_rub"))) < 0.000001
        except Exception:
            exact_control = False
        if exact_control:
            return (DERIVE, sev, {
                "candidate_value": float(value),
                "source": control_kpi.get("source"),
                "confidence": 1.0,
                "why": "max CPL exactly matches the active authoritative owner rule",
            })

    if field == "kpi.daily_budget_limit_rub":
        kpi = ctx.get("kpi") or {}
        value = kpi.get("daily_budget_limit_rub")
        auth = kpi.get("daily_budget_authorization") or {}
        try:
            value_num = float(value)
            auth_num = float(auth.get("daily_budget_limit_rub"))
            actor_id = int(auth.get("authorized_by_user_id") or 0)
            exact_value = abs(value_num - auth_num) < 0.000001
        except Exception:
            value_num = None
            actor_id = 0
            exact_value = False
        if (
            value_num is not None
            and value_num >= 0
            and exact_value
            and actor_id > 0
            and auth.get("policy_version") == "MONEY_BUDGET_OWNER_PROVENANCE_V1"
            and auth.get("source") == "authenticated_set_kpi_settings"
            and auth.get("authorized_at")
        ):
            return (DERIVE, sev, {
                "candidate_value": value_num,
                "source": "storage:kpi_settings.daily_budget_authorization",
                "confidence": 1.0,
                "why": "daily budget is explicitly authorized by the account/platform owner",
            })

    if field.startswith("limits.") and ctx["mandate"]:
        col = field.split(".")[1]
        val = ctx["mandate"].get(col)
        if val is not None:
            return (AUTO_MIGRATE, sev, {"legacy_source": "money_mandates.%s" % col,
                                        "normalized_value": float(val),
                                        "transformation": "прямой перенос", "confidence": 1.0})
    if field in ("schedule.autopilot", "schedule.mop_poll"):
        central = _central_schedule_truth(db, field)
        if central:
            return (KNOWN, sev, central)
    if field.startswith("schedule.") and field != "schedule.reports":
        job = field.split(".")[1]
        for c in ctx["cron"]:
            if c["job"] == job:
                return (AUTO_MIGRATE, sev, {"legacy_source": "crontab: %s" % c["cron"],
                                            "normalized_value": {"enabled": True, "cadence": c["cadence"]},
                                            "transformation": "cron -> политика cadence", "confidence": 0.95})
        return (OPERATIONAL, sev, {"reason": "расписание назначает политика продукта"})
    if field.startswith("products."):
        p = field.split(".")[1]
        r = E.has_entitlement(db, account_id, p) if p in ("mop", "rop") else None
        if r and r.get("state") == "known":
            return (DERIVE, sev, {"candidate_value": bool(r["allowed"]), "source": r["source"],
                                  "confidence": 1.0, "why": "типизированный entitlement"})
        return (DERIVE, sev, {"candidate_value": p in ctx["products"], "source": "признак контура",
                              "confidence": 0.7, "why": "выведено из наличия мандата/фида"})
    if field == "profile.timezone":
        tz, conf = tz_for(ctx["city"])
        if tz:
            return (DERIVE, sev, {"candidate_value": tz, "source": "город %r" % ctx["city"],
                                  "confidence": conf, "why": "справочник город->часовой пояс"})
        return (DERIVE, sev, {"candidate_value": None, "source": None, "confidence": 0.0,
                              "why": "город неизвестен", "needs_confirmation": True})
    if field == "kpi.business_hours":
        if ctx["hours"]:
            return (DERIVE, sev, ctx["hours"])
        return (ASK, sev, {"reason": "переписки мало, вывести нельзя"})
    if field == "profile.city":
        if ctx["city"]:
            return (DERIVE, sev, {"candidate_value": ctx["city"], "source": ctx.get("city_source") or "canonical_account_city",
                                  "confidence": 1.0 if ctx.get("city_source") else 0.9,
                                  "why": "город уже сохранён в конфигурации клиента/фида"})
        return (ASK, sev, {"reason": "города нет ни в одном источнике"})
    if field == "profile.tone_of_voice" and ctx.get("tone"):
        return (DERIVE, sev, {"candidate_value": ctx["tone"],
                              "source": ctx.get("tone_source") or "client_facts.confirmed",
                              "confidence": 1.0,
                              "why": "тон общения уже подтверждён клиентом"})
    if field.startswith("knowledge."):
        return (DERIVE, sev, {"candidate_value": ctx["knowledge"].get(field.split(".")[1]),
                              "source": "client_facts / messenger_prompts", "confidence": 1.0,
                              "why": "считается из существующих таблиц"})
    if field in ASK_FIELDS:
        return (ASK, sev, {"reason": "бизнес-решение владельца"})
    if field in OPERATIONAL_FIELDS:
        return (OPERATIONAL, sev, {"reason": "назначает платформа/владелец"})
    return (OPERATIONAL, sev, {"reason": "по умолчанию операционное"})


def account_report(db, account_id, owner_id, cron_map):
    eff = R.effective(db, owner_id, account_id)["effective"]
    products = products_of(db, account_id)
    mandate = _effective_money_mandate(db, account_id)
    facts = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a"),
                       {"a": account_id}).fetchone()[0]
    prompts = db.execute(text("SELECT count(*) FROM messenger_prompts WHERE account_id=:a"
                              " AND is_active"), {"a": account_id}).fetchone()[0]
    kpi = _storage_json(db, account_id, "kpi_settings")
    kpi_write = _canonical_kpi_write(db, account_id)
    control_kpi = _authoritative_control_kpi(db, account_id)
    feed_prefs = _storage_json(db, account_id, "feed_prefs")
    profile_facts = _confirmed_profile_facts(db, account_id)
    projected_city = R.value_of(eff, "profile.city")
    city = projected_city or feed_prefs.get("city") or profile_facts.get("city")
    city_source = ("client_config.profile.city" if projected_city else
                   ("storage:feed_prefs.city" if feed_prefs.get("city") else profile_facts.get("city_source")))
    ctx = {"products": products, "mandate": mandate, "cron": cron_map.get(account_id, []),
           "city": city, "city_source": city_source, "kpi": kpi, "kpi_write": kpi_write,
           "control_kpi": control_kpi,
           "tone": profile_facts.get("tone"), "tone_source": profile_facts.get("tone_source"),
           "hours": business_hours_candidate(db, account_id),
           "knowledge": {"facts_scope": facts, "prompt_ref": prompts}}
    rows = {}
    for f in sorted(S.FIELDS):
        cls, sev, ev = classify(db, account_id, f, eff, ctx)
        rows[f] = {"class": cls, "severity": sev, "evidence": ev}
    required = [f for f, r in rows.items() if r["class"] != NOT_REQUIRED]
    known = [f for f in required if rows[f]["class"] == KNOWN]
    auto = [f for f in required if rows[f]["class"] in (DERIVE, AUTO_MIGRATE, OPERATIONAL)]
    ask = [f for f in required if rows[f]["class"] == ASK]
    blockers = [f for f in ask if rows[f]["severity"] == BLOCKER]
    return {"account_id": account_id, "products": products, "rows": rows,
            "required": len(required), "known": len(known), "boris_can": len(auto),
            "ask": ask, "screens": sorted(set(SCREENS.get(f, "Прочее") for f in ask)),
            "blockers": blockers,
            "self_pct": round(100.0 * (len(known) + len(auto)) / max(len(required), 1))}


def main():
    db = SessionLocal()
    try:
        accs = db.execute(text("""
            SELECT a.account_id, a.owner_user_id FROM accounts a
             WHERE NOT EXISTS (
                 SELECT 1 FROM reliability_kill_switches r
                  WHERE r.account_id=a.account_id AND r.blocked=true
                    AND r.module IN ('actions','background')
             )
             ORDER BY 1
        """)).fetchall()
        cron_map = client_cron([a for a, _ in accs])
        reps = [account_report(db, a, o, cron_map) for a, o in accs if o is not None]
        cls_total = Counter()
        for r in reps:
            for f, v in r["rows"].items():
                cls_total[v["class"]] += 1
        print("=== FACTORY A ===")
        print("аккаунтов:", len(reps), "| полей в контракте:", len(S.FIELDS),
              "| пропущено без владельца:", len(accs) - len(reps))
        for c, n in cls_total.most_common():
            print("  %-20s %4d" % (c, n))
        sc = Counter()
        for r in reps:
            for s in r["screens"]:
                sc[s] += 1
        print("--- человеческие экраны ---")
        for s, n in sc.most_common():
            print("  %-34s у %d аккаунтов" % (s, n))
        zero = sum(1 for r in reps if not r["ask"])
        print("  ноль вопросов: %d из %d | экранов в среднем: %.1f | BORIS закрывает сам: %.0f%%" % (
            zero, len(reps), sum(len(r["screens"]) for r in reps) / max(len(reps), 1),
            sum(r["self_pct"] for r in reps) / max(len(reps), 1)))
        blocked = [r for r in reps if r["blockers"]]
        print("--- блокеры старта (ASK + blocker): %d аккаунтов ---" % len(blocked))
        for r in blocked[:8]:
            print("    %-30s %s" % (r["account_id"][:30],
                                    ",".join(b.split(".")[-1] for b in r["blockers"])))
        am = Counter()
        for r in reps:
            for f, v in r["rows"].items():
                if v["class"] == AUTO_MIGRATE:
                    am[f] += 1
        print("--- AUTO_MIGRATE (доказуемый перенос, НЕ выполняется) ---")
        for f, n in am.most_common():
            print("  %-28s %d аккаунтов" % (f, n))
        with open("/root/BORIS/backend/run/factory_a.json", "w", encoding="utf-8") as fh:
            json.dump(reps, fh, ensure_ascii=False, default=str)
        print("=== полный отчёт: /root/BORIS/backend/run/factory_a.json ===")
    finally:
        db.close()


if __name__ == "__main__":
    main()
