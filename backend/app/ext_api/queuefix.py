# -*- coding: utf-8 -*-
"""Восстановление очереди после массовой разблокировки.

21.08 разблокировка сняла ограничение с 18 521 наряда разом: она брала все
записи с префиксом CLIENT_ и не различала, где действующая фаза задачи, а где
история за все круги исполнителей. Очередь выросла до 32 692 — планировщик
перестал видеть настоящих клиентов за прошлогодними нарядами.

Здесь это разбирается по правилам, которые владелец сформулировал точно:

  · канонический набор — по одному самому свежему наряду на задачу;
  · остальное не удаляется, а получает терминальный статус с причиной;
  · перед изменением делается снимок затронутых строк;
  · любая массовая правка обязана иметь потолок и останавливаться до
    UPDATE, если собирается тронуть больше ожидаемого.

    venv/bin/python3 -m app.ext_api.queuefix plan
    venv/bin/python3 -m app.ext_api.queuefix apply --max 40000
"""
import json, os, time

from . import db, a2a

SUPERSEDED = "cancelled_superseded"          # ровно 20 символов, влезает в колонку
REASON = "mass_unblock_recovery_20260821"
# Every non-terminal lifecycle state counts as an active order.  The old
# cleanup omitted running/review_requested, so it could report "0 duplicates"
# while the owner watchdog still saw two live orders for the same task.
RUNNABLE = ("queued", "running", "review_requested", "returned", "blocked_human", "blocked_infra")

# Задачи, которые нельзя потерять ни при каких обстоятельствах.
PROTECTED = ("CLIENT_ANTON", "CLIENT_PLANETA_MEBELI", "CLIENT_VASILY",
             "CLIENT_ANDREY_PENZA")


def snapshot(rows, tag=None):
    """Снимок затронутых строк до изменения. Без него откат — это разговоры.

    Каждый проход получает собственный tag: иначе несколько автономных уборок
    складывались под одним именем, и rollback мог воскресить старые дубли из
    предыдущих проходов вместе с последним изменением.
    """
    tag = tag or (REASON + "_" + str(int(time.time() * 1000)))
    if not rows:
        return {"saved": 0, "tag": tag}
    db.q('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */')
    for r in rows:
        db.q("""INSERT INTO ext_order_snapshots (tag, order_id, dev_job_id,
                                                 status, priority)
                VALUES (:t, :o, :j, :s, :p)""",
             t=tag, o=r["id"], j=r["dev_job_id"], s=r["status"],
             p=int(r.get("priority") or 0))
    return {"saved": len(rows), "tag": tag}


def rollback(tag=None):
    """Вернуть ровно один последний снимок, а не всю историю уборок."""
    if not tag:
        latest = db.one("SELECT tag FROM ext_order_snapshots ORDER BY id DESC LIMIT 1")
        tag = (latest or {}).get("tag")
    if not tag:
        return {"restored": 0, "tag": None}
    rows = db.rows("""SELECT order_id, status FROM ext_order_snapshots
                       WHERE tag = :t""", t=tag)
    for r in rows:
        db.q("UPDATE ext_a2a_orders SET status = :s, updated_at = NOW() "
             "WHERE id = :i", s=r["status"], i=r["order_id"])
    return {"restored": len(rows), "tag": tag}


def canonical():
    """По одному действующему наряду на задачу — самому свежему.

    «Свежий» здесь это наибольший id: наряды создаются последовательно, и
    последний по задаче и есть тот, на котором она стоит сейчас.
    """
    rows = db.rows("""SELECT MAX(id) AS keep_id, dev_job_id
                        FROM ext_a2a_orders
                       WHERE status = ANY(:st)
                       GROUP BY dev_job_id""", st=list(RUNNABLE))
    return {r["dev_job_id"]: r["keep_id"] for r in rows}


def plan():
    """Что будет сделано. Считается до всякого изменения."""
    keep = canonical()
    keep_ids = set(keep.values())
    rows = db.rows("""SELECT o.id, o.dev_job_id, o.status, o.priority, j.title
                        FROM ext_a2a_orders o
                        JOIN ext_dev_jobs j ON j.id = o.dev_job_id
                       WHERE o.status = ANY(:st)""", st=list(RUNNABLE))
    supersede = [r for r in rows if r["id"] not in keep_ids]
    protected = {}
    for r in rows:
        for key in PROTECTED:
            if (r["title"] or "").startswith(key) and r["id"] in keep_ids:
                protected[key] = {"order_id": r["id"], "status": r["status"]}
    return {"runnable_before": len(rows), "keep": len(keep_ids),
            "supersede": len(supersede), "protected": protected,
            "protected_found": len(protected), "rows": supersede}


class TooManyRows(Exception):
    """Предохранитель. Массовая правка обязана останавливаться до UPDATE."""


def apply(max_rows=None, dry_run=False):
    """Свести очередь к каноническому набору.

    max_rows — потолок. Если правка собирается тронуть больше, она не
    выполняется вовсе: лучше остановиться и позвать человека, чем повторить
    ту же беду с другой стороны.
    """
    p = plan()
    limit = int(max_rows if max_rows is not None
                else os.environ.get("BORIS_MAX_AFFECTED_ROWS", "40000"))
    if p["supersede"] > limit:
        raise TooManyRows(
            "правка затронула бы %d строк при потолке %d — остановлено до UPDATE"
            % (p["supersede"], limit))
    if len(p["protected"]) < len(PROTECTED):
        missing = [k for k in PROTECTED if k not in p["protected"]]
        # Не отменяем ничего, если клиентская задача не найдена: возможно,
        # у неё сейчас вообще нет открытого наряда, и это надо понять до, а
        # не после массовой правки.
        note = "не найден действующий наряд: " + ", ".join(missing)
    else:
        note = ""

    if dry_run:
        return {"dry_run": True, **{k: v for k, v in p.items() if k != "rows"},
                "note": note}

    snap = snapshot(p["rows"])
    keep_ids = list(canonical().values())
    db.q("""UPDATE ext_a2a_orders SET status = :sup, updated_at = NOW(),
                   blocked_reason = :why
             WHERE status = ANY(:st) AND NOT (id = ANY(:keep))""",
         sup=SUPERSEDED, why=REASON, st=list(RUNNABLE), keep=keep_ids)

    after = db.rows("""SELECT status, COUNT(*) AS n FROM ext_a2a_orders
                        GROUP BY status ORDER BY n DESC""")
    return {"snapshot": snap, "superseded": p["supersede"],
            "kept": len(keep_ids), "protected": p["protected"],
            "note": note, "after": {r["status"]: r["n"] for r in after}}


def invariants():
    """Проверка того, что после уборки система осталась целой."""
    dupes = db.rows("""SELECT dev_job_id, COUNT(*) AS n FROM ext_a2a_orders
                        WHERE status = ANY(:st) GROUP BY dev_job_id
                       HAVING COUNT(*) > 1""", st=list(RUNNABLE))
    alive = {}
    for key in PROTECTED:
        r = db.one("""SELECT o.id, o.status, o.claimed_by FROM ext_a2a_orders o
                        JOIN ext_dev_jobs j ON j.id = o.dev_job_id
                       WHERE j.title LIKE :p AND o.status = ANY(:st)
                       ORDER BY o.id DESC LIMIT 1""",
                   p=key + "%", st=list(a2a.OPEN_STATES) + list(RUNNABLE))
        if r:
            alive[key] = {"order_id": r["id"], "status": r["status"],
                          "worker": r.get("claimed_by")}
        else:
            # A proved business goal is terminal truth, not a lost client.
            # ensure_business_orders intentionally cancels the final order with
            # this marker so executors cannot mutate an already-correct client.
            done = db.one("""SELECT o.id, o.blocked_reason
                               FROM ext_a2a_orders o
                               JOIN ext_dev_jobs j ON j.id=o.dev_job_id
                              WHERE j.title LIKE :p
                              ORDER BY o.id DESC LIMIT 1""", p=key + "%")
            alive[key] = ({"order_id": done["id"],
                           "status": "business_goal_confirmed", "worker": None}
                          if done and done.get("blocked_reason") ==
                          "business_goal_already_confirmed" else None)
    dead = db.one("""SELECT COUNT(*) AS n FROM ext_a2a_orders
                      WHERE status = ANY(:st)
                        AND LOWER(COALESCE(assigned_to,'')) = ANY(:names)""",
                  st=list(a2a.OPEN_STATES),
                  names=list(a2a.NON_AUTONOMOUS))["n"]
    return {
        "ACTIVE_ORDERS_PER_TASK_MAX": (max(r["n"] for r in dupes) if dupes else 1),
        "DUPLICATE_ACTIVE_TASKS": len(dupes),
        "CLIENTS_PRESERVED": "%d/%d" % (sum(1 for v in alive.values() if v),
                                        len(PROTECTED)),
        "clients": alive,
        "DEAD_ASSIGNMENTS": int(dead or 0),
    }


def restore_client(key, dispatch=True):
    """Поднять клиенту самый свежий годный наряд из отменённых.

    Только если у задачи сейчас нет ни одного действующего: воскрешать
    наряд поверх работающего — значит создать второй одновременный, ровно то,
    от чего мы только что избавились.
    """
    job = db.one("""SELECT id, title, status FROM ext_dev_jobs
                     WHERE title LIKE :p AND status NOT IN ('completed','cancelled')
                     ORDER BY id DESC LIMIT 1""", p=key + "%")
    if not job:
        return {"key": key, "restored": None, "why": "задача не найдена"}
    live = db.one("""SELECT id FROM ext_a2a_orders WHERE dev_job_id = :i
                      AND status = ANY(:st) ORDER BY id DESC LIMIT 1""",
                  i=job["id"], st=list(a2a.OPEN_STATES) + list(RUNNABLE))
    if live:
        return {"key": key, "restored": None, "why": "действующий наряд уже есть",
                "order_id": live["id"]}
    latest = db.one("""SELECT id, blocked_reason FROM ext_a2a_orders
                        WHERE dev_job_id=:i ORDER BY id DESC LIMIT 1""", i=job["id"])
    if latest and latest.get("blocked_reason") == "business_goal_already_confirmed":
        return {"key": key, "restored": None,
                "why": "бизнес-цель уже подтверждена", "order_id": latest["id"]}
    cand = db.one("""SELECT id, phase FROM ext_a2a_orders
                      WHERE dev_job_id = :i AND status IN ('cancelled', :sup)
                      ORDER BY id DESC LIMIT 1""", i=job["id"], sup=SUPERSEDED)
    if not cand:
        return {"key": key, "restored": None, "why": "нечего поднимать"}
    a2a._set(cand["id"], status=a2a.QUEUED, blocked_reason=None, iteration=0,
             claimed_by=None, claimed_at=None)
    a2a.message(cand["id"], "RESET", "dispatcher",
                {"why": "восстановлен после разбора очереди: у задачи не осталось "
                        "ни одного действующего наряда"})
    out = {"key": key, "restored": cand["id"], "phase": cand.get("phase")}
    if dispatch:
        try:
            from . import dev
            out["dispatched"] = len(dev.schedule() or [])
        except Exception as e:
            out["dispatch_error"] = "%s: %s" % (type(e).__name__, e)
    return out


def restore_all(dispatch=True):
    return [restore_client(k, dispatch=dispatch) for k in PROTECTED]


def report():
    inv = invariants()
    from . import factory
    w = factory.workers()
    counts = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) AS n FROM ext_a2a_orders GROUP BY status")}
    lines = ["QUEUE AFTER = %d" % (counts.get("queued", 0)),
             "SUPERSEDED = %d" % (counts.get(SUPERSEDED, 0)),
             "PRESERVED ACTIVE = %s" % inv["CLIENTS_PRESERVED"],
             "DUPLICATE ACTIVE TASKS = %d" % inv["DUPLICATE_ACTIVE_TASKS"]]
    names = {"CLIENT_ANTON": "ANTON", "CLIENT_PLANETA_MEBELI": "PLANETA",
             "CLIENT_VASILY": "VASILY", "CLIENT_ANDREY_PENZA": "ANDREY"}
    for key, short in names.items():
        v = inv["clients"].get(key)
        lines.append("%s = %s" % (short, ("наряд %s · %s · %s" % (
            v["order_id"], v["status"], v["worker"] or "не взят"))
            if v else "НЕТ ДЕЙСТВУЮЩЕГО НАРЯДА"))
    lines.append("REGISTERED WORKERS = %d" % w["registered"])
    lines.append("CLIENT BUSY NOW = %d" % w["client_busy"])
    lines.append("IDLE NOW = %d" % w["idle"])
    lines.append("DEAD CUSTOM GPT ASSIGNMENTS = %d" % inv["DEAD_ASSIGNMENTS"])
    return "\n".join(lines)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="queuefix")
    ap.add_argument("cmd", choices=["plan", "apply", "report", "rollback",
                                    "restore"])
    ap.add_argument("--max", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "plan":
        p = apply(max_rows=a.max, dry_run=True)
        print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    elif a.cmd == "apply":
        try:
            r = apply(max_rows=a.max)
        except TooManyRows as e:
            print("ОСТАНОВЛЕНО: %s" % e)
            return 2
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        print()
        print(report())
    elif a.cmd == "restore":
        print(json.dumps(restore_all(), ensure_ascii=False, indent=2, default=str))
        print()
        print(report())
    elif a.cmd == "rollback":
        print(json.dumps(rollback(), ensure_ascii=False))
    else:
        print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
