# -*- coding: utf-8 -*-
"""Фактическая производительность фабрики. Не «8 воркеров установлено», а
сколько их реально работало, на чём и с каким результатом.

Владелец сформулировал требование точно: «зарегистрировано 8, а реально
работало 1–2» обязано быть видно как проблема. Поэтому здесь ничего не
берётся из конфигурации — только из журнала протокола, запусков исполнителей
и артефактов клиентов.

    venv/bin/python3 -m app.ext_api.report --audit --hours 3
"""
import io, json, os, subprocess, time

from . import db, sched
from app.services.exception_observability import observe_suppressed

WATCHDOG_STATE = "/tmp/.boris_factory_watchdog"
WATCHDOG_MINUTES = int(os.environ.get("BORIS_WATCHDOG_MIN", "15"))
NO_DELTA_SLA_MIN = int(os.environ.get("BORIS_NO_DELTA_SLA_MIN", "60"))


# ------------------------------------------------------------------ утилиты

def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _pct(values, p):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    idx = min(len(vals) - 1, int(round((p / 100.0) * (len(vals) - 1))))
    return vals[idx]


def _ts(value):
    return sched._ts(value)


def _mins(a, b):
    ta, tb = _ts(a), _ts(b)
    if not ta or not tb or tb < ta:
        return None
    return (tb - ta) / 60.0


def _fmt(minutes):
    if minutes is None:
        return "нет данных"
    if minutes < 60:
        return "%d мин" % int(round(minutes))
    return "%.1f ч" % (minutes / 60.0)


# ------------------------------------------------------------------ воркеры

def _units_of(agent):
    """Системные юниты агента. DAEMON_UNITS — это карта «агент → службы», и
    перебирать её ключами значит спрашивать systemd про имя агента, а не про
    службу: на бою это дало «зарегистрировано 2, живых 0» при восьми живых
    исполнителях."""
    from . import agents
    du = getattr(agents, "DAEMON_UNITS", {}) or {}
    if isinstance(du, dict):
        return list(du.get(agent) or ())
    return list(du)


def _executor_units():
    return _units_of("gpt-executor")


def _lead_units():
    return _units_of("claude-lead")


def _units_active(units=None):
    from . import agents
    units = _executor_units() if units is None else list(units)
    active = []
    for unit in units:
        try:
            ok = agents.unit_active(unit)
        except Exception:
            continue
        if ok:
            active.append(unit)
    return active


def workers():
    """Кто зарегистрирован, кто жив и кто чем занят прямо сейчас."""
    from . import agents, a2a
    registered = _executor_units()
    active = _units_active(registered)
    # Executor is busy only while it is actually executing a RUNNING order.
    # REVIEW_REQUESTED belongs to the lead/review lane: counting the executor
    # there made long-finished claims from old reviews look like live workers.
    busy = db.rows("""SELECT claimed_by, priority, id, dev_job_id FROM ext_a2a_orders
                       WHERE status = :st AND claimed_by IS NOT NULL""",
                   st=a2a.RUNNING)
    client = sorted({r["claimed_by"] for r in busy
                     if int(r.get("priority") or 0) >= sched.CLIENT_FLOOR})
    infra = sorted({r["claimed_by"] for r in busy
                    if int(r.get("priority") or 0) < sched.CLIENT_FLOOR})
    safe = sched.limits().get("MAX_EXECUTOR_WORKERS")
    return {"registered": len(registered), "active": len(active),
            "client_busy": len(client), "infra_busy": len(infra),
            "idle": max(0, len(active) - len(client) - len(infra)),
            "max_safe": safe, "client_workers": client, "infra_workers": infra,
            "active_units": active}


# ------------------------------------------------------- реальная одновременность

def _intervals(hours):
    """Отрезки реальной работы: когда исполнитель действительно был занят."""
    rows = db.rows("""SELECT r.started_at AS a, COALESCE(r.finished_at, NOW()) AS b,
                             o.priority AS p
                        FROM ext_a2a_runs r
                        JOIN ext_a2a_orders o ON o.id = r.order_id
                       WHERE r.started_at > NOW() - (:h || ' hours')::interval""",
                   h=int(hours))
    if not rows:
        # Запусков не записано — берём срок владения нарядом. Это грубее, но
        # честнее, чем показать ноль там, где работа шла.
        rows = db.rows("""SELECT claimed_at AS a, updated_at AS b, priority AS p
                            FROM ext_a2a_orders
                           WHERE claimed_at IS NOT NULL
                             AND claimed_at > NOW() - (:h || ' hours')::interval""",
                       h=int(hours))
    out = []
    for r in rows:
        a, b = _ts(r["a"]), _ts(r["b"])
        if a and b and b >= a:
            out.append((a, b, int(r.get("p") or 0)))
    return out


def concurrency(hours=3):
    """Средняя и пиковая одновременность — по минутам, а не по описанию."""
    iv = _intervals(hours)
    now = time.time()
    start = now - hours * 3600
    step = 60
    samples, peak, peak_client = [], 0, 0
    client_minutes = infra_minutes = 0.0
    t = start
    while t <= now:
        n = sum(1 for a, b, _p in iv if a <= t <= b)
        c = sum(1 for a, b, p in iv if a <= t <= b and p >= sched.CLIENT_FLOOR)
        samples.append(c)
        peak = max(peak, n)
        peak_client = max(peak_client, c)
        client_minutes += c
        infra_minutes += (n - c)
        t += step
    avg = round(sum(samples) / float(len(samples) or 1), 2)
    return {"avg_client": avg, "peak_client": peak_client, "peak_any": peak,
            "client_worker_hours": round(client_minutes / 60.0, 2),
            "infra_worker_hours": round(infra_minutes / 60.0, 2),
            "measured_intervals": len(iv)}


# ------------------------------------------------------------------- воронка

def funnel(hours=3):
    rows = db.rows("""SELECT m.kind, o.priority AS p, COUNT(*) AS n
                        FROM ext_a2a_messages m
                        JOIN ext_a2a_orders o ON o.id = m.order_id
                       WHERE m.created_at > NOW() - (:h || ' hours')::interval
                       GROUP BY m.kind, o.priority""", h=int(hours))
    out = {"claimed": 0, "review": 0, "accept": 0, "returned": 0, "blocked": 0}
    for r in rows:
        if int(r.get("p") or 0) < sched.CLIENT_FLOOR:
            continue
        k, n = r["kind"], int(r["n"])
        if k == "CLAIMED":
            out["claimed"] += n
        elif k == "REVIEW_REQUEST":
            out["review"] += n
        elif k == "ACCEPT":
            out["accept"] += n
        elif k == "RETURN_WITH_FIXES":
            out["returned"] += n
        elif k in ("BLOCKED_HUMAN", "BLOCKED_INFRA"):
            out["blocked"] += n
    try:
        out["deployed"] = (db.one("""SELECT COUNT(*) AS n FROM ext_deploy_log
                                      WHERE ok AND created_at > NOW() -
                                            (:h || ' hours')::interval""",
                                  h=int(hours)) or {}).get("n") or 0
    except Exception:
        out["deployed"] = 0
    from . import a2a
    st = db.rows("""SELECT status, COUNT(*) AS n FROM ext_a2a_orders
                     WHERE priority >= :p GROUP BY status""", p=sched.CLIENT_FLOOR)
    by = {r["status"]: int(r["n"]) for r in st}
    out["blocked_now"] = by.get("blocked_human", 0) + by.get("blocked_infra", 0)
    out["open_now"] = sum(by.get(s, 0) for s in a2a.OPEN_STATES)
    return out


# ------------------------------------------------------------------- скорость

def timings(hours=24, limit=60):
    """Из чего складывается время задачи. Без этого «долго» — это не диагноз."""
    orders = db.rows("""SELECT id, created_at, priority FROM ext_a2a_orders
                         WHERE priority >= :p
                           AND updated_at > NOW() - (:h || ' hours')::interval
                         ORDER BY id DESC LIMIT :l""",
                     p=sched.CLIENT_FLOOR, h=int(hours), l=int(limit))
    queue, execu, rev_wait, rev_time, iters, total = [], [], [], [], [], []
    for o in orders:
        msgs = db.rows("""SELECT kind, created_at FROM ext_a2a_messages
                           WHERE order_id = :i ORDER BY id""", i=o["id"])
        first = {}
        for m in msgs:
            first.setdefault(m["kind"], m["created_at"])
        claimed = first.get("CLAIMED")
        review = first.get("REVIEW_REQUEST")
        verdict = first.get("ACCEPT") or first.get("RETURN_WITH_FIXES")
        if claimed:
            queue.append(_mins(o["created_at"], claimed))
        if claimed and review:
            execu.append(_mins(claimed, review))
        if review and verdict:
            rev_wait.append(_mins(review, verdict))
        if verdict:
            total.append(_mins(o["created_at"], verdict))
        iters.append(sum(1 for m in msgs if m["kind"] == "RETURN_WITH_FIXES"))
    dep = db.rows("""SELECT created_at FROM ext_deploy_log
                      WHERE ok AND created_at > NOW() - (:h || ' hours')::interval
                      ORDER BY id DESC LIMIT 20""", h=int(hours))
    return {"orders_measured": len(orders),
            "median_order": _median(total), "p90_order": _pct(total, 90),
            "median_queue_wait": _median(queue),
            "median_execution": _median(execu),
            "median_review_wait": _median(rev_wait),
            "median_review": _median(rev_time) if rev_time else _median(rev_wait),
            "avg_fix_iterations": (round(sum(iters) / float(len(iters)), 2)
                                   if iters else None),
            "deploys": len(dep)}


def bottlenecks(t=None):
    t = t or timings()
    named = [("ожидание очереди", t.get("median_queue_wait")),
             ("выполнение", t.get("median_execution")),
             ("ожидание приёмки", t.get("median_review_wait")),
             ("выкатка", t.get("median_review"))]
    have = [(n, v) for n, v in named if v is not None]
    have.sort(key=lambda x: x[1], reverse=True)
    return have[:3]


# --------------------------------------------------- независимость от чата

def chat_independence():
    """Может ли закрытый браузер остановить клиентское исполнение.

    Доказательство не в словах: исполнение живёт в системных службах, а
    движение протокола за последний час создано серверными агентами. Если
    оба факта на месте — чат ни на что не влияет.
    """
    active = _units_active()
    lead = _units_active(_lead_units())
    need = _executor_units() + _lead_units()
    moved = (db.one("""SELECT COUNT(*) AS n FROM ext_a2a_messages
                        WHERE created_at > NOW() - INTERVAL '60 minutes'""")
             or {}).get("n") or 0
    authors = [r["author"] for r in db.rows(
        """SELECT DISTINCT author FROM ext_a2a_messages
            WHERE created_at > NOW() - INTERVAL '60 minutes' AND author IS NOT NULL""")]
    from . import a2a
    human = [a for a in authors if str(a).lower() in a2a.NON_AUTONOMOUS]
    ok = bool(active) and bool(lead) and moved > 0 and not human
    return {"pass": ok, "active_units": active + lead, "required_units": need,
            "protocol_events_60m": moved, "authors": authors,
            "non_autonomous_authors": human,
            "why": ("" if ok else
                    ("нет живых служб исполнения" if not active else
                     "не запущена служба приёмки boris-lead" if not lead else
                     ("за час не было ни одного события протокола" if not moved
                      else "в цепочке участвовал агент, которого нельзя разбудить: "
                           + ", ".join(human))))}


# ------------------------------------------------------- очередь приёмки

REVIEW_SLA_MIN = int(os.environ.get("BORIS_REVIEW_SLA_MIN", "20"))
# Один проход — не больше пяти нарядов: приёмка не должна
# превращаться в длинную блокирующую пачку.
MAX_KICK_PER_PASS = 5


def review_queue(minutes=None):
    """Работа сделана и лежит у лида. Пока он не решил — клиент не получил
    ничего, а исполнители при этом свободны. Это самый дорогой простой:
    мощность есть, работа есть, а результата нет."""
    from . import a2a
    limit = int(REVIEW_SLA_MIN if minutes is None else minutes)
    rows = db.rows("""SELECT o.id, o.dev_job_id, o.updated_at, o.priority, j.title
                        FROM ext_a2a_orders o
                        JOIN ext_dev_jobs j ON j.id = o.dev_job_id
                       WHERE o.status = :st AND o.priority >= :p
                       ORDER BY o.updated_at""", st=a2a.REVIEW, p=sched.CLIENT_FLOOR)
    out = []
    for r in rows:
        age = _mins(r["updated_at"], time.time())
        out.append({"order_id": r["id"], "title": r["title"],
                    "priority": int(r.get("priority") or 0),
                    "minutes": int(age or 0), "over_sla": (age or 0) >= limit})
    # Сначала клиенты, внутри клиентов — самые старые. Инфраструктурный наряд
    # не должен обрабатываться раньше клиентского только потому, что он старше.
    out.sort(key=lambda x: (-x["priority"], -x["minutes"]))
    return out


def lead_decisions(hours=3):
    return (db.one("""SELECT COUNT(*) AS n FROM ext_a2a_messages
                       WHERE kind IN ('ACCEPT','RETURN_WITH_FIXES')
                         AND created_at > NOW() - (:h || ' hours')::interval""",
                   h=int(hours)) or {}).get("n") or 0


def review_blocker():
    """Почему приёмка не выносит решений — выясняем сами, на сервере.

    reconcile() здесь бесполезен: он двигает уже ПРИНЯТЫЕ наряды между
    фазами и к очереди на приёмку не относится вовсе. Решение выносит
    lead.handle(). Поэтому пробуем именно его — на самом старом наряде — и
    ловим настоящую ошибку вместо догадки.
    """
    import traceback
    from . import lead, a2a
    out = {"unit_active": bool(_units_active(_lead_units())),
           "provider": None, "probe": None, "verdict": None, "error": None}
    try:
        out["provider"] = lead.provider()
    except Exception as e:
        out["provider"] = "не определён: %s: %s" % (type(e).__name__, e)
    try:
        out["probe"] = lead.probe()
    except Exception as e:
        out["probe"] = {"error": "%s: %s" % (type(e).__name__, e)}
    stuck = [r for r in review_queue() if r["over_sla"]]
    out["stuck"] = stuck
    if not stuck:
        out["cause"] = "очередь приёмки пуста"
        return out
    oldest = stuck[0]["order_id"]
    out["tried_order"] = oldest
    try:
        res = lead.handle(a2a.get(oldest))
        out["verdict"] = (res or {}).get("status")
        out["cause"] = "приёмка работает: наряд %s → %s" % (oldest, out["verdict"])
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        out["traceback"] = traceback.format_exc()[-1500:]
        out["cause"] = "приёмка падает на наряде %s: %s" % (oldest, out["error"])
    out["journal"] = _journal_lead()
    return out


def _journal_lead(lines=60):
    try:
        r = subprocess.run(["journalctl", "-u", "boris-lead", "-n", str(int(lines)),
                            "--no-pager"], capture_output=True, text=True, timeout=60)
        return (r.stdout or "")[-2500:]
    except Exception:
        return ""


def kick_review():
    """Разбудить приёмку и сказать правду, если она не просыпается."""
    from . import lead, a2a
    stuck = [r for r in review_queue() if r["over_sla"]]
    res = {"stuck": stuck, "acted": False, "escalated": False, "decided": []}
    if not stuck:
        return res
    # Вердикт выносит штатный обработчик приёмки. Второго механизма принятия
    # решений здесь нет и быть не должно.
    for r in stuck[:MAX_KICK_PER_PASS]:
        oid = r["order_id"]
        try:
            fresh = a2a.get(oid)
        except Exception as e:
            res["decided"].append({"order_id": oid, "outcome": "НЕ НАЙДЕН",
                                   "why": "%s: %s" % (type(e).__name__, e)})
            continue
        if fresh.get("status") != a2a.REVIEW:
            # Наряд уже увели: другой проход или сама служба. Второй вердикт
            # по одному наряду недопустим.
            res["decided"].append({"order_id": oid, "outcome": "УЖЕ ИЗМЕНИЛСЯ",
                                   "status": fresh.get("status")})
            continue
        try:
            done = lead.handle(fresh)
            st = (done or {}).get("status")
            res["decided"].append({
                "order_id": oid,
                "outcome": ("РЕШЕНИЕ ПРИНЯТО" if st and st != a2a.REVIEW
                            else "РЕШЕНИЯ НЕТ"),
                "status": st})
            res["acted"] = True
        except Exception as e:
            # Не break: один сбойный наряд не имеет права закрывать дорогу
            # клиентским. Раньше именно так инфраструктурный наряд 150
            # блокировал приёмку Антона и Пензы.
            res["decided"].append({"order_id": oid, "outcome": "ОШИБКА",
                                   "why": "%s: %s" % (type(e).__name__, e)})
            continue
    still = [r for r in review_queue() if r["over_sla"]]
    res["still_stuck"] = still
    if still:
        diag = review_blocker()
        res["diagnosis"] = {k: diag[k] for k in ("unit_active", "provider",
                                                 "cause", "error") if k in diag}
        # Review backlog is an internal engineering condition. Keep it in the
        # diagnostic result and let recover/lead self-heal it; do not turn the
        # owner into an operator for executor/reviewer plumbing.
        res["escalated"] = False
    return res


# ------------------------------------------------------------------ сторож

def _watch_state(new=None):
    if new is not None:
        try:
            with io.open(WATCHDOG_STATE, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(new, ensure_ascii=False))
        except OSError:
            pass
        return new
    try:
        with io.open(WATCHDOG_STATE, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except Exception:
        return {}


def backlog():
    return (db.one("""SELECT COUNT(*) AS n FROM ext_dev_jobs
                       WHERE status IN ('queued','waiting') AND priority >= :p""",
                   p=sched.CLIENT_FLOOR) or {}).get("n") or 0


def watchdog(act=True, now=None):
    """Есть клиентская очередь, а мощность простаивает — это дефект.

    Пятнадцати минут достаточно, чтобы отличить нормальную паузу между
    нарядами от простоя. Дальше причина ищется автоматически, и владельца
    для этого будить не нужно.
    """
    now = time.time() if now is None else now
    w = workers()
    q = backlog()
    free = max(0, min(int(w["max_safe"] or 0), q) - w["client_busy"])
    state = _watch_state()
    res = {"backlog": q, "client_busy": w["client_busy"],
           "max_safe": w["max_safe"], "underused": free, "acted": False}
    # No free development provider means there is deliberately no executor
    # capacity to consume. Do not classify that as an internal underuse fault
    # every 15 minutes; dispatcher owns provider reprobe and will restore the
    # pool automatically when one becomes usable.
    try:
        from . import aiprov as _aiprov
        if q and not _aiprov.development_candidates():
            _watch_state({"since": None})
            res["underused"] = 0
            res["verdict"] = "ожидание восстановления бесплатного coding-provider"
            return res
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=478)
    if not q or free <= 0:
        _watch_state({"since": None})
        res["verdict"] = "мощность занята по делу"
        return res
    since = state.get("since") or now
    _watch_state({"since": since})
    res["minutes_underused"] = int((now - since) / 60)
    if res["minutes_underused"] < WATCHDOG_MINUTES:
        res["verdict"] = "простой замечен, жду подтверждения"
        return res
    from . import recover
    res["diagnosis"] = recover.diagnose().get("reasons")
    if act:
        try:
            kick_review()
            recover.once()
            res["acted"] = True
        except Exception as e:
            res["recover_error"] = "%s: %s" % (type(e).__name__, e)
    _watch_state({"since": None})
    res["verdict"] = "простой дольше %d мин — запустил восстановление" % WATCHDOG_MINUTES
    return res


# ------------------------------------------- ступенчатый подъём параллельности
# Резкий переход 4 → 8 не проверяет ничего: если станет хуже, будет непонятно,
# от чего именно. Поэтому ступени, выдержка на каждой и сравнение с предыдущей.
# Потолок никогда не поднимается выше измеренного машиной — ступень может
# только опустить планку, но не выдумать мощность.

LADDER_FILE = "backend/.ext_scale.env"
LADDER_STATE = "/tmp/.boris_scale_ladder.json"
LADDER_LEVELS = (4, 5, 6, 7, 8)
LADDER_HOLD_MIN = int(os.environ.get("BORIS_LADDER_HOLD_MIN", "20"))


def _ladder_state(new=None):
    if new is not None:
        try:
            with io.open(LADDER_STATE, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(new, ensure_ascii=False, default=str))
        except OSError:
            pass
        return new
    try:
        with io.open(LADDER_STATE, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except Exception:
        return {}


def _set_level(level):
    from . import repo
    path = os.path.join(repo.ROOT, LADDER_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("# ступень нагрузочного подъёма, ставит app.ext_api.factory\n"
                     "BORIS_MAX_EXECUTORS=%d\n" % int(level))
        return True
    except OSError:
        return False


def measure(hours=1):
    """Снимок ступени: нагрузка машины и то, что реально произвела фабрика."""
    f = sched.facts()
    t = timings(max(1, hours))
    truth = None
    try:
        from . import report
        truth = report.business_truth(hours, fresh=True)
    except Exception:
        truth = {"clients": []}
    delta = sum(int(c.get("delta") or 0) for c in truth.get("clients", []))
    errors = (db.one("""SELECT COUNT(*) AS n FROM ext_a2a_orders
                         WHERE status IN ('blocked_infra','blocked_human')""")
              or {}).get("n") or 0
    stalled = 0
    try:
        from . import report as _r
        stalled = len(_r.stalled())
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=562)
    return {"cpu": f.get("cpu"), "load1": f.get("load1"),
            "ram_available_mb": f.get("ram_available_mb"),
            "review_queue": len(review_queue()),
            "median_order": t.get("median_order"),
            "results_per_hour": round(delta / float(hours or 1), 2),
            "errors": int(errors), "stalled": stalled,
            "concurrency": concurrency(max(1, hours))["avg_client"]}


def _worse(prev, cur):
    """Стало ли хуже. Одна метрика ничего не доказывает — смотрим набор."""
    if not prev:
        return []
    bad = []
    if cur["errors"] > prev["errors"]:
        bad.append("выросли ошибки: %d → %d" % (prev["errors"], cur["errors"]))
    if cur["review_queue"] > max(2, prev["review_queue"] * 2):
        bad.append("раздулась очередь приёмки: %d → %d"
                   % (prev["review_queue"], cur["review_queue"]))
    if cur["stalled"] > prev["stalled"]:
        bad.append("больше вставших задач: %d → %d" % (prev["stalled"], cur["stalled"]))
    a, b = prev.get("median_order"), cur.get("median_order")
    if a and b and b > a * 1.3:
        bad.append("медленнее наряд: %s → %s" % (_fmt(a), _fmt(b)))
    if cur["load1"] and cur["cpu"] and cur["load1"] > cur["cpu"] * 1.5:
        bad.append("перегрузка машины: load %.1f при %d ядрах"
                   % (cur["load1"], cur["cpu"]))
    if prev.get("results_per_hour") and cur["results_per_hour"] < prev["results_per_hour"]:
        bad.append("меньше результатов в час: %s → %s"
                   % (prev["results_per_hour"], cur["results_per_hour"]))
    return bad


def ladder(now=None, act=True):
    """Один шаг лестницы. Вызывается диспетчером, ничего не блокирует."""
    now = time.time() if now is None else now
    st = _ladder_state()
    if st.get("final"):
        return {"done": True, "max_safe": st["final"], "history": st.get("history")}
    if not st:
        st = {"level": LADDER_LEVELS[0], "since": now, "history": {}}
        if act:
            _set_level(st["level"])
        _ladder_state(st)
        return {"level": st["level"], "started": True}
    held = (now - float(st.get("since") or now)) / 60.0
    if held < LADDER_HOLD_MIN:
        return {"level": st["level"], "held_min": int(held),
                "verdict": "выдержка ступени ещё идёт"}
    cur = measure(1)
    prev_level = st["level"]
    hist = st.get("history") or {}
    hist[str(prev_level)] = cur
    prev = hist.get(str(prev_level - 1)) if prev_level > LADDER_LEVELS[0] else None
    bad = _worse(prev, cur)
    if bad:
        back = max(LADDER_LEVELS[0], prev_level - 1)
        st.update(level=back, since=now, history=hist, final=back, why=bad)
        if act:
            _set_level(back)
        _ladder_state(st)
        # Capacity tuning is internal self-heal. The owner should see its
        # business effect in the regular report, not implementation chatter.
        return {"level": back, "rolled_back_from": prev_level, "why": bad,
                "max_safe": back, "done": True}
    nxt = [l for l in LADDER_LEVELS if l > prev_level]
    if not nxt:
        st.update(history=hist, final=prev_level, since=now)
        _ladder_state(st)
        return {"level": prev_level, "max_safe": prev_level, "done": True}
    st.update(level=nxt[0], since=now, history=hist)
    if act:
        _set_level(nxt[0])
    _ladder_state(st)
    return {"level": nxt[0], "from": prev_level, "verdict": "ступень поднята"}


def ladder_report():
    st = _ladder_state()
    hist = st.get("history") or {}
    levels = sorted(int(k) for k in hist)
    first = hist.get(str(levels[0])) if levels else None
    last = hist.get(str(levels[-1])) if levels else None
    w = workers()
    L = ["REGISTERED WORKERS = %d" % w["registered"],
         "TESTED LEVELS = %s" % (",".join(str(x) for x in levels) or "нет"),
         "MAX SAFE CONCURRENCY = %s" % (st.get("final") or st.get("level") or "ещё меряю"),
         "AVG CONCURRENCY = %s" % concurrency(3)["avg_client"],
         "PEAK CONCURRENCY = %s" % concurrency(3)["peak_client"]]
    if first and last:
        L += ["CLIENT RESULTS/HOUR BEFORE = %s" % first["results_per_hour"],
              "CLIENT RESULTS/HOUR AFTER = %s" % last["results_per_hour"],
              "MEDIAN ORDER TIME BEFORE = %s" % _fmt(first.get("median_order")),
              "MEDIAN ORDER TIME AFTER = %s" % _fmt(last.get("median_order")),
              "REGRESSIONS = %d/%d" % (len(st.get("why") or []), len(levels))]
    L.append("RECOMMENDED PRODUCTION LIMIT = %s"
             % (st.get("final") or st.get("level") or "—"))
    return "\n".join(L)


# ------------------------------------------------- последняя миля клиента
# Вопрос не «работает ли фабрика», а «где именно результат предыдущего шага
# существует, а следующий побочный эффект в production не происходит».
# Отвечаем по цепочке, шаг за шагом, и называем ПЕРВЫЙ разрыв.

def _artifacts_between(account_id, a, b):
    cols = db.columns("campaign_items") or {}
    acc = "account_id" if "account_id" in cols else None
    tcol = "created_at" if "created_at" in cols else None
    if not (acc and tcol and account_id is not None):
        return None
    try:
        return (db.one("SELECT COUNT(*) AS n FROM campaign_items "
                       "WHERE CAST(%s AS TEXT) = :a AND %s BETWEEN :x AND :y"
                       % (acc, tcol), a=str(account_id), x=a, y=b)
                or {}).get("n") or 0
    except Exception:
        return None


def trace(key="CLIENT_ANTON"):
    """Пройти путь одного клиента до конечного артефакта и найти первый разрыв."""
    from . import clients, a2a, report
    out = {"client": key, "steps": [], "first_broken": None, "root_cause": None,
           "counts": {"orders": 0, "accepted": 0, "open": 0, "claimed": 0,
                      "patches": 0, "deploys_ok": 0,
                      "artifacts_during_orders": 0}}
    job = clients._existing(key)
    if not job:
        out["first_broken"] = "ЗАДАЧА"
        out["root_cause"] = "клиентской задачи нет в работе"
        return out
    jid = job["id"]
    out["dev_job_id"] = "dev_%s" % jid
    ref = next(((sp.get("scope") or {}).get("account") for sp in clients.CLIENTS
                if sp.get("key") == key), None)
    acc = report._acct_id(ref) if ref else None
    out["account_ref"], out["account_id"] = ref, acc
    orders = sorted(a2a.listing(dev_job_id="dev_%s" % jid, limit=50),
                    key=lambda x: x["order_id"])
    patches = db.rows("""SELECT id, status, created_at FROM ext_dev_patches
                          WHERE dev_job_id = :j ORDER BY id""", j=jid)
    deploys = db.rows("""SELECT id, ok, note, created_at FROM ext_deploy_log
                          ORDER BY id DESC LIMIT 20""")
    for o in orders:
        try:
            full = a2a.get(o["order_id"])
        except Exception:
            # Наряд исчез между выборкой и разбором — путь от этого не рвётся.
            continue
        kinds = [m["kind"] for m in (full.get("messages") or [])]
        made = _artifacts_between(acc, full.get("created_at"), full.get("updated_at"))
        out["steps"].append({
            "order_id": o["order_id"], "phase": o.get("phase"),
            "status": o.get("status"), "claimed_by": full.get("claimed_by"),
            "INPUT": (full.get("goal") or "")[:120],
            "OUTPUT": ("есть доказательства" if full.get("last_evidence")
                       else "доказательств нет"),
            "NEXT CONSUMER": ("лид" if o.get("status") == a2a.REVIEW else
                              "исполнитель" if o.get("status") in a2a.OPEN_STATES
                              else "следующая фаза"),
            "ACTUAL SIDE EFFECT": ("объектов создано: %s" % made
                                   if made is not None else "измерить нечем"),
            "kinds": kinds})
    accepted = [o for o in orders if o["status"] == a2a.ACCEPTED]
    open_ = [o for o in orders if o["status"] in a2a.OPEN_STATES]
    claimed = []
    for o in open_:
        try:
            if a2a.get(o["order_id"]).get("claimed_by"):
                claimed.append(o)
        except Exception:
            continue
    total_made = sum(x["ACTUAL SIDE EFFECT"].split(": ")[-1].isdigit() and
                     int(x["ACTUAL SIDE EFFECT"].split(": ")[-1]) or 0
                     for x in out["steps"])
    applied = [d for d in deploys if d["ok"]]

    if accepted and not open_:
        out["first_broken"] = "ACCEPT → следующий наряд"
        out["root_cause"] = ("наряд принят, но продолжения нет: не ставится "
                             "следующая фаза")
    elif open_ and not claimed:
        out["first_broken"] = "наряд → исполнитель"
        out["root_cause"] = "наряд открыт, но никем не взят: раскладка очереди"
    elif patches and not applied:
        out["first_broken"] = "правка → выкатка"
        out["root_cause"] = ("правки исполнителя есть (%d), но ни одной "
                             "успешной выкатки нет" % len(patches))
    elif not patches and not total_made:
        out["first_broken"] = "работа исполнителя → запись в production"
        out["root_cause"] = ("исполнитель отчитывается доказательствами, но не "
                             "создаёт ни правок, ни объектов клиента: наряд "
                             "закрывается разбором, а не продуктом")
    elif total_made and not (report.business_truth(24, fresh=True)):
        out["first_broken"] = "БД → счётчик правды"
        out["root_cause"] = "объекты созданы, но измерение показывает ноль"
    else:
        out["first_broken"] = "разрыв не найден на этом пути"
        out["root_cause"] = ("объектов за время нарядов: %s, правок: %d, "
                             "успешных выкаток: %d"
                             % (total_made, len(patches), len(applied)))
    out["counts"] = {"orders": len(orders), "accepted": len(accepted),
                     "open": len(open_), "claimed": len(claimed),
                     "patches": len(patches), "deploys_ok": len(applied),
                     "artifacts_during_orders": total_made}
    return out


def trace_text(key="CLIENT_ANTON"):
    t = trace(key)
    L = ["ПУТЬ КЛИЕНТА: %s" % key,
         "аккаунт: %s (id %s)" % (t.get("account_ref"), t.get("account_id")), ""]
    for s_ in t["steps"][-6:]:
        L.append("наряд %s · %s · %s · исполнитель %s"
                 % (s_["order_id"], s_["phase"], s_["status"],
                    s_["claimed_by"] or "не взят"))
        L.append("   вход: %s" % s_["INPUT"])
        L.append("   выход: %s" % s_["OUTPUT"])
        L.append("   дальше: %s" % s_["NEXT CONSUMER"])
        L.append("   побочный эффект: %s" % s_["ACTUAL SIDE EFFECT"])
    c = t.get("counts") or {}
    L += ["", "нарядов: %s · принято: %s · открыто: %s · взято: %s"
          % (c.get("orders"), c.get("accepted"), c.get("open"), c.get("claimed")),
          "правок исполнителя: %s · успешных выкаток: %s · объектов создано: %s"
          % (c.get("patches"), c.get("deploys_ok"), c.get("artifacts_during_orders")),
          "", "FIRST BROKEN TRANSITION = %s" % t["first_broken"],
          "ROOT CAUSE = %s" % t["root_cause"]]
    return "\n".join(L)


# ------------------------------------------------- сводка за время отсутствия
# Владелец закрывает ноутбук. Когда он его откроет, ему нужен не поток
# событий, а ответ: работало ли без него, что продвинулось, что встало и что
# сервер уже сделал сам.

OFFLINE_MARK = "/tmp/.boris_owner_seen"


def owner_seen(now=None):
    now = time.time() if now is None else now
    try:
        with io.open(OFFLINE_MARK, "w", encoding="utf-8") as fh:
            fh.write(str(int(now)))
    except OSError:
        pass
    return now


def _away_hours(now=None):
    now = time.time() if now is None else now
    try:
        with io.open(OFFLINE_MARK, encoding="utf-8") as fh:
            seen = float(fh.read().strip())
    except Exception:
        seen = now - 3600
    return max(0.25, (now - seen) / 3600.0)


def offline_summary(hours=None, mark=True):
    """Что было, пока владельца не было."""
    h = float(hours or _away_hours())
    from . import report, clients
    c = concurrency(max(1, int(round(h))))
    jobs_done = (db.one("""SELECT COUNT(*) AS n FROM ext_dev_jobs
                            WHERE status = 'completed'
                              AND updated_at > NOW() - (:h || ' hours')::interval""",
                        h=int(max(1, h))) or {}).get("n") or 0
    orders_done = (db.one("""SELECT COUNT(*) AS n FROM ext_a2a_orders
                              WHERE status = 'accepted'
                                AND updated_at > NOW() - (:h || ' hours')::interval""",
                          h=int(max(1, h))) or {}).get("n") or 0
    repairs = (db.one("""SELECT COUNT(*) AS n FROM ext_dev_events
                          WHERE created_at > NOW() - (:h || ' hours')::interval
                            AND kind IN ('unstuck_dead_agent','RESET','LOOP_GUARD')""",
                      h=int(max(1, h))) or {}).get("n") or 0
    human = (db.one("""SELECT COUNT(*) AS n FROM ext_a2a_orders
                        WHERE status = 'blocked_human'""") or {}).get("n") or 0
    truth = report.business_truth(max(1, int(round(h))), fresh=True)
    keys = focus_keys()
    rows = [x for x in truth["clients"] if not keys or x["key"] in keys]
    delta = sum(int(x.get("delta") or 0) for x in rows)
    moved = any(int(x.get("delta") or 0) > 0 for x in rows)
    ok = bool(orders_done or jobs_done or delta)
    L = ["АВТОНОМНАЯ РАБОТА = %s" % ("PASS" if ok else "FAIL"),
         "РАБОТАЛО БЕЗ ВЛАДЕЛЬЦА = %d ч %02d мин" % (int(h), int((h % 1) * 60)),
         "СРЕДНЯЯ ПАРАЛЛЕЛЬНОСТЬ = %s" % c["avg_client"],
         "МАКСИМАЛЬНАЯ ПАРАЛЛЕЛЬНОСТЬ = %s" % c["peak_client"],
         "ЗАВЕРШЕНО ЗАДАЧ = %d" % jobs_done,
         "ЗАВЕРШЕНО НАРЯДОВ = %d" % orders_done,
         "РЕАЛЬНЫХ КЛИЕНТСКИХ РЕЗУЛЬТАТОВ = %d" % delta,
         "АВТОМАТИЧЕСКИХ REPAIR/RECOVERY = %d" % repairs,
         "HUMAN INTERVENTIONS = %d" % human, ""]
    for x in rows:
        head = ("%s — было %s → стало %s"
                % (x["name"], x["baseline"], x["current"]))
        if int(x.get("delta") or 0) > 0:
            L.append(head + " | +%d за период | смотреть: %s"
                     % (x["delta"], x["where"] or "—"))
            L.append("   ещё делается: %s" % (x.get("phase") or "следующая фаза"))
        else:
            L.append(head + " | без движения | смотреть: %s" % (x["where"] or "—"))
            L.append("   STALLED → причина: %s" % x["proof"])
            L.append("   что сделано само: восстановление и пинок приёмки по "
                     "расписанию, %d автоматических вмешательств" % repairs)
            L.append("   следующий шаг: %s"
                     % ("довести наряд %s" % x["order_id"] if x.get("order_id")
                        else "поставить следующий наряд"))
    if mark:
        owner_seen()
    return "\n".join(L)


# ------------------------------------------------------------------- аудит

def focus_keys():
    from . import clients
    return list(getattr(clients, "FOCUS_CLIENTS", ()) or ())


def audit(hours=3):
    from . import report
    w = workers()
    c = concurrency(hours)
    f = funnel(hours)
    t = timings(max(hours, 24))
    truth = report.business_truth(2)
    keys = focus_keys()
    if keys:
        # Владелец смотрит на четверых. Остальные задачи в его аудит не
        # попадают — иначе главный вердикт размывается тем, чего он сейчас
        # не заказывал.
        truth = dict(truth, clients=[c for c in truth["clients"]
                                     if c["key"] in keys])
        truth["moving"] = sum(1 for c in truth["clients"] if c["state"] == "MOVING")
        truth["stalled"] = sum(1 for c in truth["clients"]
                               if c["state"] in ("STALLED", "DEAD_WORKER",
                                                 "NO_OPEN_ORDER",
                                                 "FAILED_VALIDATION", "BLOCKED"))
        truth["done"] = sum(1 for c in truth["clients"]
                            if (c.get("progress") or 0) >= 100)
    delta = sum(int(x.get("delta") or 0) for x in truth["clients"])
    rq = review_queue()
    return {"hours": hours, "workers": w, "concurrency": c, "funnel": f,
            "review_queue": rq, "lead_decisions": lead_decisions(hours),
            "timings": t, "bottlenecks": bottlenecks(t), "truth": truth,
            "business_delta": delta,
            "results_per_hour": round(delta / float(hours or 1), 2),
            "chat": chat_independence(), "backlog": backlog(),
            "false_green": report.false_green(2)}


def summary_lines(hours=3):
    """Первый экран отчёта владельцу."""
    a = audit(hours)
    w, c, t = a["workers"], a["concurrency"], a["timings"]
    bn = a["bottlenecks"]
    return [
        "CLIENT PRODUCTION MOVING = %s" % ("YES" if a["business_delta"] else "NO"),
        "CLIENTS WITH REAL DELTA = %d · CLIENTS STALLED = %d · CLIENTS DONE = %d"
        % (a["truth"]["moving"], a["truth"]["stalled"], a["truth"]["done"]),
        "CLIENT WORKERS BUSY = %d / SAFE %s · IDLE = %d"
        % (w["client_busy"], w["max_safe"], w["idle"]),
        "AVG CONCURRENCY %dH = %s · PEAK = %d" % (hours, c["avg_client"],
                                                  c["peak_client"]),
        "RESULTS/HOUR = %s · MEDIAN RESULT TIME = %s"
        % (a["results_per_hour"], _fmt(t["median_order"])),
        "BIGGEST BOTTLENECK = %s" % (("%s %s" % (bn[0][0], _fmt(bn[0][1])))
                                     if bn else "нет данных"),
    ]


def audit_text(hours=3):
    a = audit(hours)
    w, c, f, t = a["workers"], a["concurrency"], a["funnel"], a["timings"]
    L = ["LAST %dH CLIENT AUDIT" % hours, ""]
    L.append("REGISTERED WORKERS = %d" % w["registered"])
    L.append("ACTIVE WORKERS = %d" % w["active"])
    L.append("CLIENT BUSY NOW = %d" % w["client_busy"])
    L.append("INFRA BUSY NOW = %d" % w["infra_busy"])
    L.append("IDLE NOW = %d" % w["idle"])
    L.append("MAX SAFE CONCURRENCY = %s" % w["max_safe"])
    L.append("ACTUAL AVG CONCURRENCY %dH = %s" % (hours, c["avg_client"]))
    L.append("ACTUAL PEAK CONCURRENCY %dH = %d" % (hours, c["peak_client"]))
    L.append("WORKER-HOURS CLIENT = %s · INFRA = %s"
             % (c["client_worker_hours"], c["infra_worker_hours"]))
    L.append("")
    L.append("CLIENT ORDERS CLAIMED = %d" % f["claimed"])
    L.append("SENT TO REVIEW = %d" % f["review"])
    L.append("ACCEPTED = %d" % f["accept"])
    L.append("RETURNED WITH FIXES = %d" % f["returned"])
    L.append("REACHED PRODUCTION = %d" % f.get("deployed", 0))
    L.append("REAL BUSINESS DELTA = %d" % a["business_delta"])
    L.append("BLOCKED NOW = %d · OPEN NOW = %d" % (f["blocked_now"], f["open_now"]))
    L.append("BACKLOG = %d" % a["backlog"])
    _rq = a["review_queue"]
    L.append("REVIEW QUEUE = %d%s" % (len(_rq),
             ("" if not _rq else " · самый старый %s" % _fmt(_rq[0]["minutes"]))))
    L.append("LEAD DECISIONS %dH = %d" % (hours, a["lead_decisions"]))
    L.append("")
    L.append("MEDIAN ORDER TIME = %s" % _fmt(t["median_order"]))
    L.append("P90 ORDER TIME = %s" % _fmt(t["p90_order"]))
    L.append("MEDIAN QUEUE WAIT = %s" % _fmt(t["median_queue_wait"]))
    L.append("MEDIAN EXECUTION = %s" % _fmt(t["median_execution"]))
    L.append("MEDIAN REVIEW WAIT = %s" % _fmt(t["median_review_wait"]))
    L.append("AVERAGE FIX ITERATIONS = %s" % t["avg_fix_iterations"])
    L.append("CLIENT RESULTS/HOUR = %s" % a["results_per_hour"])
    if a["bottlenecks"]:
        L.append("BIGGEST BOTTLENECK = " + " · ".join(
            "%s %s" % (n, _fmt(v)) for n, v in a["bottlenecks"]))
    L.append("")
    L.append("CLIENT PRODUCTION MOVING = %s"
             % ("YES" if a["business_delta"] else "NO"))
    if a["false_green"]["false_green"]:
        L.append("FALSE GREEN = ДА — %s" % a["false_green"]["why"])
    L.append("CHAT INDEPENDENCE = %s%s"
             % ("PASS" if a["chat"]["pass"] else "FAIL",
                "" if a["chat"]["pass"] else " — " + a["chat"]["why"]))
    L.append("")
    L.append("4 CLIENTS PRODUCTION MOVING = %s"
             % ("YES" if a["business_delta"] else "NO"))
    L.append("")
    for c in a["truth"]["clients"]:
        L.append("%s: было %s → стало %s | delta %+d | %s | %s"
                 % (c["name"], c["baseline"], c["current"], c["delta"],
                    ("%d%%" % c["progress"]) if c["progress"] is not None else "?",
                    c["where"] or "—"))
        L.append("   цель: %s" % (c["goal"] or "—"))
        L.append("   последний реальный результат: %s"
                 % ("никогда" if c["last_result_min"] is None
                    else "%d мин назад" % c["last_result_min"]))
        if c.get("order_id"):
            L.append("   наряд %s (%s), исполнитель %s, в работе %s"
                     % (c["order_id"], c.get("phase") or "—",
                        c.get("worker") or "не взят",
                        _fmt(c.get("minutes_on_order"))))
        else:
            L.append("   открытого наряда нет")
        if c["state"] != "MOVING":
            L.append("   %s — %s" % (c["state"], c["proof"]))
    return "\n".join(L)
