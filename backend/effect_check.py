# -*- coding: utf-8 -*-
"""Замер результата действий BORIS — пятый предохранитель Конституции.

Отвечает на вопрос «помогло или нет». Ничего не меняет: берёт действия из
журнала, считает по снимкам daily_stats просмотры и обращения за неделю ДО
действия и за неделю ПОСЛЕ, пишет вердикт.

Горячие пути не трогает вовсе — снимки уже копятся сами (31-35 дней глубины).
"""
import sys, os, json, datetime

sys.path.insert(0, "/root/BORIS/backend")
for _l in open("/root/BORIS/backend/.env", encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.control_plane_adapters_ext import marketing_service_entitlement

APPLY = "--apply" in sys.argv
WINDOW = 7           # дней до и после
MEASURABLE = ("объявление", "объявления")


def _day_stats(db, account_id, day):
    r = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k"),
                   {"a": account_id, "k": "daily_stats:" + day}).fetchone()
    if not r:
        return {}
    v = r[0] if isinstance(r[0], (dict, list)) else json.loads(r[0] or "{}")
    out = {}
    for it in (v.get("items") or []):
        out[str(it.get("id"))] = (int(it.get("views") or 0), int(it.get("contacts") or 0))
    return out


def _window(db, account_id, d_from, days):
    """Сумма просмотров и обращений по объявлениям за окно дней."""
    total = {}
    for i in range(days):
        day = (d_from + datetime.timedelta(days=i)).isoformat()
        for iid, (v, c) in _day_stats(db, account_id, day).items():
            pv, pc = total.get(iid, (0, 0))
            total[iid] = (pv + v, pc + c)
    return total


def _process_cpx_measure_states(db):
    """
    Финализирует новые CPX measure states.

    Никаких изменений Avito здесь нет:
    функция только считает накопленный эффект и,
    если данных достаточно, переводит state в measured.
    """
    import json

    from app.models.storage import Storage
    from app.api import cpx_advisor

    rows = (
        db.query(Storage)
        .filter(Storage.key.like("cpx_measure:%"))
        .all()
    )

    print("[effect][cpx] measure states: %s" % len(rows))

    measured = 0
    waiting = 0
    errors = 0
    # CPX_EFFECT_ENTITLEMENT_CACHE_V1: one entitlement lookup per account, not
    # once per historical measurement row. This keeps the hourly DB-only
    # learning pass bounded on large portfolios.
    _entitlement_cache = {}

    for row in rows:
        try:
            state = json.loads(row.value)
        except Exception:
            print(
                "[effect][cpx] ERROR: invalid JSON:",
                row.account_id,
                row.key,
            )
            errors += 1
            continue

        if state.get("status") != "waiting_measurement":
            continue

        # CPX_MEASURE_EXPIRED_ENTITLEMENT_SUPERSEDE_V1: an ended paid money
        # period cannot produce a future scale decision. Close the local
        # measurement without provider I/O and without winner/loser learning.
        # Unknown entitlement is NOT auto-closed; only explicit expired truth.
        if row.account_id not in _entitlement_cache:
            try:
                _entitlement_cache[row.account_id] = marketing_service_entitlement(db, row.account_id) or {}
            except Exception:
                _entitlement_cache[row.account_id] = {}
        _ent = _entitlement_cache.get(row.account_id) or {}
        if str(_ent.get("state") or "") == "expired":
            state["status"] = "superseded"
            state["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            state["effect"] = "service_period_ended"
            state["decision"] = "do_not_learn_after_entitlement_end"
            state["reason_code"] = "service_period_ended"
            state["reason"] = "Оплаченный период AI-маркетолога завершён; денежное измерение закрыто без verdict и без продления услуги."
            state["scale_eligible"] = False
            state["service_entitlement"] = _ent
            row.value = json.dumps(state, ensure_ascii=False)
            db.commit()
            print("[effect][cpx] SUPERSEDED_EXPIRED:", row.account_id, state.get("item_id"), state.get("action"))
            continue

        item_id = state.get("item_id")
        action = state.get("action")

        if not item_id or not action:
            print(
                "[effect][cpx] ERROR: invalid state:",
                row.account_id,
                row.key,
            )
            errors += 1
            continue

        result = cpx_advisor._finalize_measure_state(
            db=db,
            account_id=row.account_id,
            item_id=int(item_id),
            action=str(action),
        )

        status = result.get("status")

        if status == "measured":
            measured += 1
            print(
                "[effect][cpx] MEASURED:",
                row.account_id,
                item_id,
                action,
                "effect=",
                result.get("effect"),
            )

        elif status == "waiting_measurement":
            waiting += 1
            print(
                "[effect][cpx] WAITING:",
                row.account_id,
                item_id,
                action,
                "reason=",
                result.get("reason"),
            )

        elif status in ("no_measurement", "already_finalized"):
            print(
                "[effect][cpx]",
                status,
                row.account_id,
                item_id,
                action,
            )

        else:
            errors += 1
            print(
                "[effect][cpx] ERROR:",
                row.account_id,
                item_id,
                action,
                result,
            )

    print(
        "[effect][cpx] summary:",
        "measured=%s" % measured,
        "waiting=%s" % waiting,
        "errors=%s" % errors,
    )

    return {
        "measured": measured,
        "waiting": waiting,
        "errors": errors,
    }


def main():
    db = SessionLocal()
    try:
        # CPX measure_state is the canonical short feedback loop and commits its
        # own durable learning journal. Do not mutate it from a dry-run probe.
        if APPLY:
            _process_cpx_measure_states(db)
        else:
            print("[effect][cpx] dry-run: canonical measure states not finalized")
        rows = db.execute(text(
            "SELECT id, ts, account_id, action, object_kind, object_name, reason"
            " FROM action_log WHERE object_kind = ANY(:k)"
            "   AND ts < now() - interval '%s days'"
            "   AND id NOT IN (SELECT action_id FROM action_effects)"
            " ORDER BY ts" % WINDOW), {"k": list(MEASURABLE)}).fetchall()
        print("[effect] действий к замеру: %s" % len(rows))
        for r in rows:
            aid, ts, acc, action, kind, name = r[0], r[1], r[2], r[3], r[4], r[5]
            d0 = ts.date()
            before = _window(db, acc, d0 - datetime.timedelta(days=WINDOW), WINDOW)
            after = _window(db, acc, d0, WINDOW)
            # ACTION_EFFECT_ITEM_SCOPE_V1: exact-item attribution for ad mutations.
            item_id = str(name or "").strip()
            if item_id and item_id.isdigit():
                bv, bc = before.get(item_id, (0, 0)); av, ac = after.get(item_id, (0, 0)); effect_scope = "item"
            else:
                bv = sum(v for v, _ in before.values()); bc = sum(c for _, c in before.values())
                av = sum(v for v, _ in after.values()); ac = sum(c for _, c in after.values()); effect_scope = "account_legacy"
            if not before and not after:
                verdict, note = "нет данных", "снимков за период нет"
            elif ac > bc:
                verdict, note = "помогло", "обращений стало больше"
            elif ac < bc:
                verdict, note = "не помогло", "обращений стало меньше"
            elif av > bv:
                verdict, note = "частично", "просмотров больше, обращений столько же"
            else:
                verdict, note = "без изменений", "показатели не изменились"
            print("  #%s %s | %s → %s | просмотры %s→%s, обращения %s→%s | %s"
                  % (aid, action, (name or "")[:34], verdict, bv, av, bc, ac, note))
            if APPLY:
                db.execute(text(
                    "INSERT INTO action_effects (action_id, account_id, item_id, views_before,"
                    " views_after, contacts_before, contacts_after, verdict, note)"
                    " VALUES (:i,:a,:item,:vb,:va,:cb,:ca,:v,:n)"),
                    {"i": aid, "a": acc, "item": item_id if effect_scope == "item" else None,
                     "vb": bv, "va": av, "cb": bc, "ca": ac, "v": verdict, "n": f"[{effect_scope}] {note}"})
        if APPLY:
            db.commit()
            print("[effect] вердикты записаны")
        else:
            print("[effect] ничего не записано, повторите с --apply")
    finally:
        db.close()


if __name__ == "__main__":
    main()
