# -*- coding: utf-8 -*-
"""Состояние применения правила оптимизации к конкретному объявлению.

ЗАЧЕМ: не дать лестницу 12 -> 36 -> 108. Правило версии N применяется
к объявлению ОДИН раз; target всегда считается от baseline_bid,
зафиксированного при первом применении, а не от текущей ставки.

Хранение — существующий механизм storage (как cpx_measure), новых таблиц нет.
Ключ: optrule:{item_id}:{optimization_rule_id}:v{rule_version}

Fail-closed: любая неполнота -> can_propose = False.
"""

import json

from sqlalchemy import text

KEY_PREFIX = "optrule"

ST_PROPOSED = "proposed"                # решение сформировано, ждёт подтверждения
ST_AWAITING = "awaiting_confirmation"   # ушло в существующий confirmation gate
ST_APPLIED = "applied"                  # ставка изменена внешним контуром
ST_MEASURING = "measuring"              # идёт окно замера
ST_MEASURED = "measured"                # замер закрыт
ST_REJECTED = "rejected"                # человек отклонил
ST_BLOCKED = "blocked"                  # guard/лимиты не пропустили

OPEN_STATES = (ST_PROPOSED, ST_AWAITING, ST_APPLIED, ST_MEASURING)
FINAL_STATES = (ST_MEASURED, ST_REJECTED, ST_BLOCKED)


def key(item_id, rule_id, rule_version):
    return "%s:%s:%s:v%s" % (KEY_PREFIX, item_id, rule_id, rule_version)


def load(db, account_id, item_id, rule_id, rule_version):
    try:
        row = db.execute(text(
            "SELECT value FROM storage WHERE account_id = :a AND key = :k"),
            {"a": account_id, "k": key(item_id, rule_id, rule_version)}).first()
    except Exception:
        return None
    if not row:
        return None
    v = row[0]
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v or "{}")
    except Exception:
        return None


def load_any_version(db, account_id, item_id, rule_id):
    """Все записи по этому объявлению и правилу, любых версий."""
    try:
        rows = db.execute(text(
            "SELECT key, value FROM storage"
            " WHERE account_id = :a AND key LIKE :p"),
            {"a": account_id,
             "p": "%s:%s:%s:v%%" % (KEY_PREFIX, item_id, rule_id)}).fetchall()
    except Exception:
        return []
    out = []
    for k, v in rows:
        if isinstance(v, (dict, list)):
            out.append((k, v))
            continue
        try:
            out.append((k, json.loads(v or "{}")))
        except Exception:
            continue
    return out


def save(db, account_id, state, commit=True):
    payload = json.dumps(state, ensure_ascii=False)
    k = key(state["item_id"], state["optimization_rule_id"], state["rule_version"])
    row = db.execute(text(
        "SELECT id FROM storage WHERE account_id = :a AND key = :k"),
        {"a": account_id, "k": k}).first()
    if row:
        db.execute(text("UPDATE storage SET value = :v WHERE id = :i"),
                   {"v": payload, "i": row[0]})
    else:
        db.execute(text(
            "INSERT INTO storage (account_id, key, value) VALUES (:a, :k, :v)"),
            {"a": account_id, "k": k, "v": payload})
    if commit:
        db.commit()
    return k


def can_propose(states_same_version, states_any_version):
    """Можно ли формировать новое предложение по этому объявлению и правилу.

    Возвращает (bool, причина словами). Правило версии N — один раз на объявление.
    """
    for st in states_same_version:
        s = (st or {}).get("status")
        if s in OPEN_STATES:
            return (False, "правило этой версии уже применяется к объявлению "
                           "(состояние: %s)" % s)
        if s == ST_MEASURED:
            return (False, "правило этой версии уже применялось и замерено — "
                           "повторно только после новой редакции правила")
        if s == ST_REJECTED:
            return (False, "предложение по этому правилу было отклонено человеком")
    for _k, st in states_any_version:
        if (st or {}).get("status") in OPEN_STATES:
            return (False, "по объявлению не закрыт замер предыдущей редакции правила")
    return (True, "применений этой редакции правила к объявлению не было")


def effective_baseline(current_bid, states_any_version):
    """Ставка, от которой считается target.

    Если по этому правилу уже было применение — берём его baseline, а не текущую
    ставку. Именно это не даёт лестницу 12 -> 36 -> 108.
    """
    best = None
    for _k, st in states_any_version:
        b = (st or {}).get("baseline_bid")
        if b is None:
            continue
        try:
            b = float(b)
        except Exception:
            continue
        if best is None or b < best:
            best = b
    if best is not None:
        return (best, "baseline из предыдущего применения правила")
    try:
        cur = float(current_bid)
    except Exception:
        return (None, "текущая ставка неизвестна")
    if cur <= 0:
        return (None, "текущая ставка не задана")
    return (cur, "baseline = текущая ставка, применений не было")


def new_state(account_id, item_id, rule, baseline_bid, target_bid,
              views, contacts, conversion, reason, now_iso,
              measurement_window_days=7):
    """Контракт применения правила. Все поля журнала — здесь."""
    p = rule["params"]
    return {
        "optimization_rule_id": rule["optimization_rule_id"],
        "rule_version": rule["rule_version"],
        "rule_name": rule["name"],
        "rule_params": dict(p),
        "account_id": account_id,
        "item_id": str(item_id),
        "baseline_bid": baseline_bid,
        "target_bid": target_bid,
        "applied_bid": None,
        "applied_at": None,
        "measurement_window_days": measurement_window_days,
        "status": ST_PROPOSED,
        "created_at": now_iso,
        "evidence": {
            "views_7d": views,
            "contacts_7d": contacts,
            "conversion": conversion,
            "min_conversion": p["min_conversion"],
            "min_views": p["min_views"],
            "bid_multiplier": p["bid_multiplier"],
            "reason": reason,
        },
    }


def human_line(state):
    """Строка для журнала: было -> станет -> почему."""
    e = state.get("evidence") or {}
    conv = e.get("conversion")
    conv_s = "%.1f%%" % (conv * 100.0) if isinstance(conv, float) else "?"
    applied = state.get("applied_bid")
    to = applied if applied is not None else state.get("target_bid")
    return ("ставка %s -> %s: конверсия %s (%s обращений на %s просмотров за 7 дней) "
            "при пороге %.1f%% и минимуме %s просмотров; правило #%s версии %s"
            % (state.get("baseline_bid"), to, conv_s,
               e.get("contacts_7d"), e.get("views_7d"),
               float(e.get("min_conversion") or 0) * 100.0,
               e.get("min_views"), state.get("optimization_rule_id"),
               state.get("rule_version")))
