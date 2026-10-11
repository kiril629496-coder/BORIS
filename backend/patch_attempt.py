#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 2: ai_budget становится attempt-aware.

run_id — логическая серия попыток, attempt — номер, id — конкретная попытка
и единственный ключ для SELECT/UPDATE после разрешения. Ни одного боевого
запроса по одному run_id остаться не должно: при двух попытках такой UPDATE
мутирует обе строки и списывает деньги дважды.

DRY-RUN по умолчанию, запись по --apply.
"""
import io
import sys
import time

APPLY = "--apply" in sys.argv
F = "app/services/ai_budget.py"

R = []

R.append((
'''ST_EXPIRED = "expired"''',
'''ST_EXPIRED = "expired"
ST_OVERSPENT = "overspent"

# Можно ли создать НОВУЮ попытку после попытки в этом статусе.
# Единственный разрешённый случай — expired: резерв существовал, а AI-вызов
# доказанно не состоялся. Всё остальное либо выполнено, либо требует разбора.
RETRY_ALLOWED = {ST_EXPIRED: True}'''))

R.append((
'''def reserve(account_id, module, operation, run_id, est_kopeks,''',
'''def _current(db, run_id):
    """Последняя попытка run_id: (id, attempt, status).

    Единственное место, где run_id превращается в конкретную строку.
    Вызывать ОДИН раз в начале операции и дальше работать по id: между
    двумя вызовами может появиться новая попытка, и операция уедет на неё.
    """
    row = db.execute(text(
        "select id, attempt, status from ai_run_budget"
        " where run_id=:r order by attempt desc limit 1"),
        {"r": run_id}).fetchone()
    return (int(row[0]), int(row[1]), row[2]) if row else (None, 0, None)


def reserve(account_id, module, operation, run_id, est_kopeks,'''))

R.append((
'''        exist = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks from ai_run_budget"
            " where run_id=:r"), {"r": run_id}).fetchone()
        if exist:
            return {"status": "exists", "run_id": run_id,
                    "run_status": exist[0],
                    "reserved_kopeks": int(exist[1] or 0),
                    "committed_kopeks": int(exist[2] or 0)}
''',
''''''))

R.append((
'''        _expire_stale(db, kind, oid, resource)''',
'''        _expire_stale(db, kind, oid, resource)
        # Последнюю попытку читаем ПОСЛЕ истечения просроченных: иначе
        # истёкший резерв выглядит активным и блокирует законный повтор.
        # Блокировка строки product_limits уже взята выше, поэтому номер
        # попытки не может достаться двум процессам сразу.
        cur_id, cur_att, cur_st = _current(db, run_id)
        attempt = 1
        if cur_id is not None:
            if not RETRY_ALLOWED.get(cur_st):
                prev = db.execute(text(
                    "select reserved_kopeks, committed_kopeks from ai_run_budget"
                    " where id=:i"), {"i": cur_id}).fetchone()
                db.commit()
                return {"status": "exists", "run_id": run_id,
                        "attempt": cur_att, "run_status": cur_st,
                        "reserved_kopeks": int(prev[0] or 0),
                        "committed_kopeks": int(prev[1] or 0)}
            attempt = cur_att + 1'''))

R.append((
'''            "insert into ai_run_budget (run_id, billing_owner_kind, billing_owner_id,"
            " resource, module, operation, status, reserved_kopeks, committed_kopeks,"
            " calls_planned, calls_used, override_reason, created_at, updated_at, expires_at)"
            " values (:r,:k,:o,:res,:m,:op,:st,:rk,0,:cp,0,:ov,:ts,:ts,:exp)"''',
'''            "insert into ai_run_budget (run_id, attempt, billing_owner_kind,"
            " billing_owner_id, resource, module, operation, status, reserved_kopeks,"
            " committed_kopeks, calls_planned, calls_used, override_reason,"
            " created_at, updated_at, expires_at)"
            " values (:r,:att,:k,:o,:res,:m,:op,:st,:rk,0,:cp,0,:ov,:ts,:ts,:exp)"'''))

R.append((
'''            " values (:r,:att,:k,:o,:res,:m,:op,:st,:rk,0,:cp,0,:ov,:ts,:ts,:exp)"
            " on conflict (run_id) do nothing"),
            {"r": run_id, "k": kind, "o": oid, "res": resource, "m": module,''',
'''            " values (:r,:att,:k,:o,:res,:m,:op,:st,:rk,0,:cp,0,:ov,:ts,:ts,:exp)"
            " on conflict (run_id, attempt) do nothing"),
            {"r": run_id, "att": attempt, "k": kind, "o": oid, "res": resource, "m": module,'''))

R.append((
'''        row = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks, calls_planned,"
            " calls_used, expires_at from ai_run_budget where run_id=:r"),
            {"r": run_id}).fetchone()
        if not row:
            return {"allowed": False, "reason": "run_not_found"}''',
'''        att_id, _att, _st = _current(db, run_id)
        if att_id is None:
            return {"allowed": False, "reason": "run_not_found"}
        row = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks, calls_planned,"
            " calls_used, expires_at from ai_run_budget where id=:i"),
            {"i": att_id}).fetchone()
        if not row:
            return {"allowed": False, "reason": "run_not_found"}'''))

R.append((
'''            "update ai_run_budget set calls_used = calls_used + 1, updated_at=:ts"
            " where run_id=:r"), {"ts": _now(), "r": run_id})''',
'''            "update ai_run_budget set calls_used = calls_used + 1, updated_at=:ts"
            " where id=:i"), {"ts": _now(), "i": att_id})'''))

R.append((
'''        run = db.execute(text(
            "select billing_owner_kind, billing_owner_id, resource, status"
            " from ai_run_budget where run_id=:r"), {"r": run_id}).fetchone()
        if not run:
            return {"status": "run_not_found", "run_id": run_id}
        kind, oid, resource = run[0], int(run[1]), run[2]''',
'''        att_id, attempt, _st = _current(db, run_id)
        if att_id is None:
            return {"status": "run_not_found", "run_id": run_id}
        run = db.execute(text(
            "select billing_owner_kind, billing_owner_id, resource, status"
            " from ai_run_budget where id=:i"), {"i": att_id}).fetchone()
        if not run:
            return {"status": "run_not_found", "run_id": run_id}
        kind, oid, resource = run[0], int(run[1]), run[2]'''))

R.append((
'''            "insert into ai_run_calls (request_id, run_id, status, cost_kopeks, created_at)"
            " values (:q,:r,'pending',0,:ts) on conflict (request_id) do nothing"),
            {"q": request_id, "r": run_id, "ts": _now()})''',
'''            "insert into ai_run_calls (request_id, run_id, attempt, status,"
            " cost_kopeks, created_at)"
            " values (:q,:r,:att,'pending',0,:ts) on conflict (request_id) do nothing"),
            {"q": request_id, "r": run_id, "att": attempt, "ts": _now()})'''))

R.append((
'''            "update ai_run_budget set committed_kopeks = committed_kopeks + :k,"
            " updated_at=:ts where run_id=:r"),
            {"k": kop, "ts": _now(), "r": run_id})''',
'''            "update ai_run_budget set committed_kopeks = committed_kopeks + :k,"
            " updated_at=:ts where id=:i"),
            {"k": kop, "ts": _now(), "i": att_id})'''))

R.append((
'''                "update ai_run_budget set stop_reason='overspent', updated_at=:ts"
                " where run_id=:r"), {"ts": _now(), "r": run_id})''',
'''                "update ai_run_budget set status=:s, stop_reason='overspent',"
                " updated_at=:ts where id=:i"),
                {"s": ST_OVERSPENT, "ts": _now(), "i": att_id})'''))

R.append((
'''        row = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks from ai_run_budget"
            " where run_id=:r"), {"r": run_id}).fetchone()
        if not row:
            return {"status": "run_not_found", "run_id": run_id}
        st, rk, ck = row[0], int(row[1] or 0), int(row[2] or 0)''',
'''        att_id, _att, _cst = _current(db, run_id)
        if att_id is None:
            return {"status": "run_not_found", "run_id": run_id}
        row = db.execute(text(
            "select status, reserved_kopeks, committed_kopeks from ai_run_budget"
            " where id=:i"), {"i": att_id}).fetchone()
        if not row:
            return {"status": "run_not_found", "run_id": run_id}
        st, rk, ck = row[0], int(row[1] or 0), int(row[2] or 0)'''))

R.append((
'''            "update ai_run_budget set status=:s, updated_at=:ts,"
            " stop_reason=coalesce(:why, stop_reason) where run_id=:r"),
            {"s": new_status, "ts": _now(), "why": reason, "r": run_id})''',
'''            "update ai_run_budget set status=:s, updated_at=:ts,"
            " stop_reason=coalesce(:why, stop_reason) where id=:i"),
            {"s": new_status, "ts": _now(), "why": reason, "i": att_id})'''))


def main():
    src = io.open(F, encoding="utf-8").read()
    if "_current" in src:
        print("УЖЕ ПРОПАТЧЕН")
        return
    out = src
    for i, (old, new) in enumerate(R, 1):
        n = out.count(old)
        if n != 1:
            print("STOP: якорь %d встречается %d раз" % (i, n))
            print("   " + old.strip().splitlines()[0][:70])
            sys.exit(2)
        out = out.replace(old, new, 1)
    print("замен: %d | строк %d -> %d" % (len(R), src.count("\n"), out.count("\n")))

    tmp = F + ".new"
    io.open(tmp, "w", encoding="utf-8").write(out)
    import py_compile
    try:
        py_compile.compile(tmp, doraise=True)
        print("compile: OK")
    except Exception as e:
        print("STOP: compile не прошёл: %s" % str(e)[:200])
        sys.exit(3)

    left = [l for l in out.splitlines() if "where run_id=:r" in l]
    print("осталось запросов по run_id: %d" % len(left))
    for l in left:
        print("   " + l.strip()[:76])

    if not APPLY:
        import os
        os.remove(tmp)
        print("DRY-RUN. Файл не изменён. Для записи: --apply")
        return
    bak = "%s.bak_attempt_%d" % (F, int(time.time()))
    io.open(bak, "w", encoding="utf-8").write(src)
    io.open(F, "w", encoding="utf-8").write(out)
    import os
    os.remove(tmp)
    print("записано, бэкап %s" % bak)


if __name__ == "__main__":
    main()
