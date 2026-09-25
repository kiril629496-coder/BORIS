# -*- coding: utf-8 -*-
"""Единый журнал действий BORIS.

Нулевое правило Конституции: каждое действие записывается, шестью полями,
человеческим языком. Прежний _audit_log писал JSON-строкой в storage — при
семнадцати фоновых процессах записи затирают друг друга, и хранились только
последние 500. Здесь обычная таблица action_log.

Запись НИКОГДА не должна ломать действие: всё обёрнуто, ошибка только печатается.
"""
from sqlalchemy import text as _t

from app.db.session import SessionLocal

# Кто инициировал
ACTOR_BORIS = "boris"      # система сама
ACTOR_CRON = "cron"        # по расписанию
ACTOR_USER = "user"        # человек нажал кнопку
ACTOR_BORIS_AUTO = "boris_auto"   # автономное действие BORIS в рамках мандата


def log_action(account_id: str, action: str, object_name: str = "",
               object_kind: str = "", before_val: str = "", after_val: str = "",
               reason: str = "", actor: str = ACTOR_BORIS, user_id=None,
               source: str = "", trigger=None, mandate_id=None,
               mandate_version=None, request_id=None, balance_status=None,
               guard_reason=None, optimization_rule_id=None, rule_version=None):
    """Одна запись журнала.

    action       — что сделал, по-русски: «Ответил клиенту», «Снизил ставку»
    object_kind  — над чем: диалог | объявление | пост | звонок | доступ
    object_name  — НАЗВАНИЕ, а не идентификатор
    before/after — было и стало
    reason       — почему, фактами: «7 дней без просмотров»
    source       — какой процесс: inbox_send, posting_runner, cpx_advisor
    """
    try:
        db = SessionLocal()
        try:
            db.execute(_t(
                "INSERT INTO action_log (account_id, actor, user_id, source, action,"
                " object_kind, object_name, before_val, after_val, reason,"
                " trigger, mandate_id, mandate_version, request_id,"
                " balance_status, guard_reason, optimization_rule_id, rule_version)"
                " VALUES (:acc, :act, :uid, :src, :a, :ok, :on, :bv, :av, :r,"
                " :trg, :mid, :mver, :rid, :bst, :grr, :orid, :rver)"),
                {"acc": account_id or "", "act": actor, "uid": user_id,
                 "src": (source or "")[:48], "a": (action or "")[:64],
                 "ok": (object_kind or "")[:32], "on": (object_name or "")[:300],
                 "bv": (before_val or "")[:2000], "av": (after_val or "")[:2000],
                 "r": (reason or "")[:1000],
                 # ACTION_LOG_DB_LENGTH_GUARD_V1: truncate every bounded DB field
                 # before insert. Long deterministic measurement ids must never
                 # make the learning journal silently disappear.
                 "trg": ((str(trigger)[:32]) if trigger else None), "mid": mandate_id,
                 "mver": mandate_version, "rid": ((str(request_id)[:64]) if request_id else None),
                 "bst": ((str(balance_status)[:24]) if balance_status else None),
                 "grr": ((str(guard_reason)[:64]) if guard_reason else None),
                 "orid": optimization_rule_id, "rver": rule_version})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print("[action_log] не записалось: " + repr(e)[:150], flush=True)


def recent(account_id: str, limit: int = 50):
    """Последние записи для показа в кабинете."""
    db = SessionLocal()
    try:
        rows = db.execute(_t(
            "SELECT ts, actor, action, object_kind, object_name, before_val,"
            " after_val, reason, id FROM action_log WHERE account_id = :a"
            " ORDER BY ts DESC LIMIT :n"), {"a": account_id, "n": int(limit)}).fetchall()
    finally:
        db.close()
    who = {ACTOR_BORIS: "BORIS", ACTOR_CRON: "по расписанию", ACTOR_USER: "сотрудник"}
    out = []
    for r in rows:
        item = {"id": r[8], "когда": r[0].strftime("%d.%m.%Y %H:%M"),
                "кто": who.get(r[1], r[1]),
                "действие": r[2], "объект": r[4] or "", "тип": r[3] or ""}
        if r[5] or r[6]:
            item["было_стало"] = ((r[5] or "—") + " → " + (r[6] or "—"))[:300]
        if r[7]:
            item["почему"] = r[7]
        out.append(item)
    return out
