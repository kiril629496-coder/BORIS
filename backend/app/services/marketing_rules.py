# -*- coding: utf-8 -*-
"""Платформенные правила оптимизации BORIS.

Хранятся в client_facts: account_id='__platform__', category='rule_marketing',
scope='platform', status='hypothesis'. Параметры — в колонке params (jsonb),
редакция — в rule_version.

ВАЖНО: это НЕ коммерческая гипотеза кампании (campaign_items.hypothesis_id).
Здесь — правило оптимизации BORIS: optimization_rule_id + rule_version.

Fail-closed: любая неполнота (нет правила, чужой статус, битые params,
нет ключа, нет версии) -> get_optimization_rule вернёт None,
и вызывающий код обязан работать ровно как раньше.
"""

import json
import hashlib

from sqlalchemy import text

PLATFORM_ACCOUNT = "__platform__"
CATEGORY = "rule_marketing"
SCOPE = "platform"
STATUS_ACTIVE = "hypothesis"
STATUS_SUPERSEDED = "superseded"
REQUIRED_KEYS = ("min_conversion", "bid_multiplier", "min_views", "applies_to")


def fact_hash(name, version):
    """Свой хеш: включает версию, поэтому новая редакция не затирает старую."""
    raw = "%s|%s|v%s" % (CATEGORY, name, version)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _clean_params(raw):
    """Возвращает нормализованные параметры или None. Ничего не додумывает."""
    p = raw
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            return None
    if not isinstance(p, dict):
        return None
    for k in REQUIRED_KEYS:
        if k not in p:
            return None
    try:
        mc = float(p["min_conversion"])
        bm = float(p["bid_multiplier"])
        mv = int(p["min_views"])
    except Exception:
        return None
    if not (0.0 < mc < 1.0):
        return None
    if bm <= 0.0:
        return None
    if mv < 0:
        return None
    at = p.get("applies_to")
    if not isinstance(at, str) or not at:
        return None
    return {"min_conversion": mc, "bid_multiplier": bm,
            "min_views": mv, "applies_to": at}


def get_optimization_rule(db, applies_to):
    """Активное правило для точки применения. None = правила нет, работаем как раньше."""
    try:
        rows = db.execute(text(
            "SELECT id, name, value, params, rule_version FROM client_facts"
            " WHERE account_id = :a AND category = :c AND scope = :s"
            "   AND status = :st"
            " ORDER BY rule_version DESC NULLS LAST, id DESC"),
            {"a": PLATFORM_ACCOUNT, "c": CATEGORY,
             "s": SCOPE, "st": STATUS_ACTIVE}).all()
    except Exception:
        return None
    for r in rows:
        params = _clean_params(r[3])
        if not params:
            continue
        if params["applies_to"] != applies_to:
            continue
        if r[4] is None:
            continue
        try:
            ver = int(r[4])
        except Exception:
            continue
        return {"optimization_rule_id": int(r[0]),
                "rule_version": ver,
                "name": r[1],
                "text": r[2] or "",
                "params": params}
    return None


def matches(rule, views_7d, contacts_7d):
    """Подходит ли объявление под правило. Возвращает (bool, причина словами)."""
    if not rule:
        return (False, "правило оптимизации не задано")
    p = rule["params"]
    try:
        v = int(views_7d or 0)
        c = int(contacts_7d or 0)
    except Exception:
        return (False, "нет данных о просмотрах и обращениях")
    if v < p["min_views"]:
        return (False, "мало просмотров для вывода: %s при пороге %s"
                % (v, p["min_views"]))
    if v <= 0:
        return (False, "нет просмотров за неделю")
    conv = float(c) / float(v)
    if conv < p["min_conversion"]:
        return (False, "конверсия %.1f%% ниже порога %.1f%%"
                % (conv * 100.0, p["min_conversion"] * 100.0))
    return (True, "конверсия %.1f%% при %s просмотрах за неделю — выше порога %.1f%%"
            % (conv * 100.0, v, p["min_conversion"] * 100.0))


def proposed_bid(rule, current_bid):
    """Предлагаемая ставка по множителю правила. None = не применять."""
    if not rule:
        return None
    try:
        cur = float(current_bid)
    except Exception:
        return None
    if cur <= 0:
        return None
    return round(cur * rule["params"]["bid_multiplier"])


def snapshot(rule):
    """Слепок правила для payload замера — чтобы понять историю без join."""
    if not rule:
        return None
    return {"optimization_rule_id": rule["optimization_rule_id"],
            "rule_version": rule["rule_version"],
            "name": rule["name"],
            "params": dict(rule["params"])}


def save_rule(db, name, text_value, params, author, apply=False):
    """Создаёт правило или НОВУЮ редакцию. Старая уходит в superseded, не переписывается."""
    clean = _clean_params(params)
    if not clean:
        return {"status": "error", "reason": "params не прошли валидацию"}
    cur = db.execute(text(
        "SELECT id, rule_version, params FROM client_facts"
        " WHERE account_id = :a AND category = :c AND scope = :s AND name = :n"
        " ORDER BY rule_version DESC NULLS LAST, id DESC LIMIT 1"),
        {"a": PLATFORM_ACCOUNT, "c": CATEGORY, "s": SCOPE, "n": name}).first()
    if cur:
        old = _clean_params(cur[2])
        if old == clean:
            return {"status": "unchanged", "optimization_rule_id": int(cur[0]),
                    "rule_version": int(cur[1] or 1)}
        version = int(cur[1] or 1) + 1
    else:
        version = 1
    if not apply:
        return {"status": "dry", "would_create_version": version,
                "params": clean}
    if cur:
        db.execute(text(
            "UPDATE client_facts SET status = :st, updated_at = now()"
            " WHERE id = :i"), {"st": STATUS_SUPERSEDED, "i": int(cur[0])})
    row = db.execute(text(
        "INSERT INTO client_facts (account_id, category, name, value, source_type,"
        " source_ref, confidence, status, fact_hash, scope, params, rule_version,"
        " extracted_at, updated_at)"
        " VALUES (:a, :c, :n, :v, 'owner', :src, 50, :st, :h, :s, CAST(:p AS jsonb),"
        " :ver, now(), now()) RETURNING id"),
        {"a": PLATFORM_ACCOUNT, "c": CATEGORY, "n": name, "v": text_value,
         "src": author, "st": STATUS_ACTIVE, "h": fact_hash(name, version),
         "s": SCOPE, "p": json.dumps(clean, ensure_ascii=False), "ver": version}).first()
    if cur:
        db.execute(text("UPDATE client_facts SET superseded_by = :new WHERE id = :i"),
                   {"new": int(row[0]), "i": int(cur[0])})
    db.commit()
    return {"status": "ok", "optimization_rule_id": int(row[0]), "rule_version": version}
