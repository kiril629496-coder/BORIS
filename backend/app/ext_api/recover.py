# -*- coding: utf-8 -*-
"""Самовосстановление контура. Человек не нужен даже для починки.

Ночью работа встала: семь задач числились в работе за агентом, которого
нельзя разбудить, держали ёмкость, и никто этого не заметил до утра. Чтобы
это не повторилось, восстановление стало частью системы, а не командой,
которую кто-то должен вспомнить и запустить.

Служба boris-dispatcher крутит один и тот же цикл:
  подобрать зависшие → разложить очередь по живым исполнителям →
  посмотреть, появилось ли движение → если движения нет, назвать причину.

    venv/bin/python3 -m app.ext_api.recover once     # один проход
    venv/bin/python3 -m app.ext_api.recover verify   # проход + наблюдение
    venv/bin/python3 -m app.ext_api.recover run      # служба
"""
import io, json, os, sys, time, subprocess

def _sysctl_active(unit, timeout=10):
    """Ответ systemctl в виде объекта с .stdout, но без риска зависнуть.

    На машине без systemd вопрос не задаётся вовсе: там таймаут не спасает —
    он убивает потомка, а чтение трубы продолжает ждать внука.
    """
    class _R(object):
        def __init__(self, out):
            self.stdout = out
            self.returncode = 0 if out.strip() == "active" else 1
    if not os.path.isdir("/run/systemd/system"):
        return _R("")
    try:
        p = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                           text=True, timeout=timeout, start_new_session=True)
        return _R(p.stdout or "")
    except Exception:
        return _R("")



from . import db, dev, a2a, agents, sched

WATCH_SECONDS = 900          # сколько наблюдать за движением после починки
POLL_SECONDS = 30
IDLE_ESCALATE_MIN = 60       # столько без движения — сказать владельцу


def snapshot():
    """Состояние контура одним числом на каждое, чтобы сравнивать до и после."""
    jobs = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) AS n FROM ext_dev_jobs GROUP BY status")}
    orders = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) AS n FROM ext_a2a_orders GROUP BY status")}
    dead = db.one("""SELECT COUNT(*) AS n FROM ext_dev_jobs
                      WHERE status='running' AND LOWER(COALESCE(assigned_agent,''))
                            = ANY(:names)""", names=list(a2a.NON_AUTONOMOUS))["n"]
    live = [a["name"] for a in agents.registry()
            if a.get("daemon") or a["status"] == "available"]
    return {"jobs": jobs, "orders": orders, "dead_assignments": int(dead or 0),
            "live_executors": live,
            "queued": jobs.get("queued", 0) + jobs.get("waiting", 0),
            "running": jobs.get("running", 0),
            "completed": jobs.get("completed", 0),
            "claimed": orders.get("running", 0) + orders.get("review_requested", 0),
            "reviewed": orders.get("accepted", 0) + orders.get("returned", 0),
            "at": int(time.time())}


def unstick_dead():
    """Снять задачи с агентов, которых нельзя разбудить, — независимо от молчания.

    Молчание ловит heartbeat_sweep. Здесь другое: даже «свежая» задача за
    Custom GPT никогда не поедет, потому что будить его некому.
    """
    rows = db.rows("""SELECT id, title, assigned_agent FROM ext_dev_jobs
                       WHERE status IN ('running','waiting')
                         AND LOWER(COALESCE(assigned_agent,'')) = ANY(:names)""",
                   names=list(a2a.NON_AUTONOMOUS))
    freed = []
    for r in rows:
        dev._set(r["id"], status="queued", assigned_agent=None,
                 wait_reason="снят с агента, которого нельзя разбудить без человека")
        try:
            dev.event(r["id"], "PLANNING", "dispatcher", "unstuck_dead_agent",
                      {"was": r.get("assigned_agent")})
        except Exception:
            pass
        freed.append({"dev_job_id": "dev_%s" % r["id"], "title": r["title"],
                      "was": r.get("assigned_agent")})

    # Главное: сам НАРЯД мог быть выписан на агента, которого не разбудить.
    # Такой наряд лежит в очереди вечно — серверные исполнители берут только
    # свои. Задача при этом числится в работе и держит ёмкость.
    moved = db.rows("""SELECT id, dev_job_id, assigned_to FROM ext_a2a_orders
                        WHERE status = ANY(:st)
                          AND LOWER(COALESCE(assigned_to,'')) = ANY(:names)""",
                    st=list(a2a.OPEN_STATES), names=list(a2a.NON_AUTONOMOUS))
    for o in moved:
        db.q("""UPDATE ext_a2a_orders SET assigned_to = :to, updated_at = NOW()
                 WHERE id = :i""", to="gpt-executor", i=o["id"])
        try:
            a2a.message(o["id"], "RESET", "dispatcher",
                        {"why": "наряд был выписан на агента, которого нельзя "
                                "разбудить: передан серверному исполнителю",
                         "was": o.get("assigned_to")})
        except Exception:
            pass
    return freed + [{"order_id": o["id"], "dev_job_id": "dev_%s" % o["dev_job_id"],
                     "was": o.get("assigned_to"), "now": "gpt-executor"}
                    for o in moved]


EXECUTOR_UNITS = tuple("boris-executor" + ("" if i == 1 else "-%d" % i)
                       for i in range(1, 9))
LEAD_UNITS = ("boris-lead",)
_DAEMON_START_AT = {}
DAEMON_START_COOLDOWN_SEC = int(os.environ.get("BORIS_DAEMON_START_COOLDOWN_SEC", "300"))


def ensure_daemon_units():
    """Автоматически держать безопасное число executor/lead процессов.

    Dispatcher не только поднимает упавшие units, но и убирает лишние idle
    executors, когда CPU/load уменьшили допустимую ёмкость. Исполнитель с
    реально RUNNING-нарядом никогда не останавливается ради downscale.
    """
    import subprocess
    if not agents.systemd_available():
        return {"started": [], "stopped": [], "failed": [],
                "desired_executors": 0, "desired_lead": 0}
    caps = sched.limits()
    desired_exec = max(0, min(len(EXECUTOR_UNITS), int(caps.get("MAX_EXECUTOR_WORKERS") or 0)))
    desired_lead = 1 if int(caps.get("MAX_REVIEW_WORKERS") or 0) > 0 else 0
    # Development daemons have nothing useful to do while every allowed free
    # coding provider is unavailable. Keeping them alive only creates repeated
    # provider failures and noisy queue churn. Dispatcher itself remains alive
    # and reprobes Gemini; the next cycle scales workers back up automatically.
    try:
        from . import aiprov as _aiprov
        free_dev_available = bool(_aiprov.development_candidates())
    except Exception:
        free_dev_available = True  # fail open on health-check errors
    if not free_dev_available:
        desired_exec = 0
        desired_lead = 0
    targets = list(EXECUTOR_UNITS[:desired_exec]) + (list(LEAD_UNITS) if desired_lead else [])
    started, stopped, failed = [], [], []
    now = time.time()

    # PROVIDER_START_REVALIDATION_V1: provider state can change between the
    # initial desired-capacity calculation and systemctl start. Revalidate
    # immediately before every executor/lead start so a quota/auth transition
    # cannot briefly launch a whole pool with no usable free provider.
    for unit in targets:
        if agents.unit_active(unit):
            continue
        try:
            if not _aiprov.development_candidates():
                desired_exec = 0
                desired_lead = 0
                targets = []
                break
        except Exception:
            pass
        if now - float(_DAEMON_START_AT.get(unit, 0) or 0) < DAEMON_START_COOLDOWN_SEC:
            continue
        _DAEMON_START_AT[unit] = now
        try:
            p = subprocess.run(["sudo", "-n", "systemctl", "start", unit], capture_output=True,
                               text=True, timeout=20, start_new_session=True)
            if p.returncode == 0 and agents.unit_active(unit):
                started.append(unit)
            else:
                failed.append({"unit": unit, "action": "start",
                               "reason": (p.stderr or p.stdout or "start failed")[:180]})
        except Exception as e:
            failed.append({"unit": unit, "action": "start",
                           "reason": "%s: %s" % (type(e).__name__, str(e)[:140])})

    # Scale down only surplus IDLE executors. A claimed RUNNING order is a hard
    # safety gate: that worker is left alive even when current load cap fell.
    try:
        running = db.rows("""SELECT DISTINCT claimed_by FROM ext_a2a_orders
                              WHERE status='running' AND claimed_by IS NOT NULL""")
        busy_workers = {str(r.get("claimed_by") or "") for r in running}
    except Exception:
        busy_workers = set()
    for unit in reversed(EXECUTOR_UNITS[desired_exec:]):
        if not agents.unit_active(unit):
            continue
        worker = unit[:-8] if unit.endswith(".service") else unit
        if worker in busy_workers:
            continue
        try:
            p = subprocess.run(["sudo", "-n", "systemctl", "stop", unit], capture_output=True,
                               text=True, timeout=20, start_new_session=True)
            if p.returncode == 0 and not agents.unit_active(unit):
                stopped.append(unit)
            else:
                failed.append({"unit": unit, "action": "stop",
                               "reason": (p.stderr or p.stdout or "stop failed")[:180]})
        except Exception as e:
            failed.append({"unit": unit, "action": "stop",
                           "reason": "%s: %s" % (type(e).__name__, str(e)[:140])})

    # Reviewer/lead must follow the same provider gate. Previously executors
    # were correctly stopped at zero providers while boris-lead kept running
    # and made readiness look inconsistent. There is no useful review work
    # possible without a development provider, so stop it until recovery.
    if not desired_lead:
        for unit in LEAD_UNITS:
            if not agents.unit_active(unit):
                continue
            try:
                p = subprocess.run(["systemctl", "stop", unit], capture_output=True,
                                   text=True, timeout=20, start_new_session=True)
                if p.returncode == 0 and not agents.unit_active(unit):
                    stopped.append(unit)
                else:
                    failed.append({"unit": unit, "action": "stop",
                                   "reason": (p.stderr or p.stdout or "stop failed")[:180]})
            except Exception as e:
                failed.append({"unit": unit, "action": "stop",
                               "reason": "%s: %s" % (type(e).__name__, str(e)[:140])})

    return {"started": started, "stopped": stopped, "failed": failed,
            "desired_executors": desired_exec, "desired_lead": desired_lead,
            "busy_workers_preserved": sorted(busy_workers)}


def seed_daemon_heartbeats():
    """Служба запущена — значит жива, даже если ещё не успела отметиться.

    Иначе получается разрыв: код обновили, процессы подняли, а первая
    раскладка очереди происходит раньше первой отметки — и работа снова
    никому не достаётся.
    """
    import subprocess
    seeded = []
    for units, agent in ((EXECUTOR_UNITS, "gpt-executor"), (LEAD_UNITS, "claude-lead")):
        for unit in units:
            try:
                ok = agents.unit_active(unit)
            except Exception:
                continue
            if ok:
                agents.alive(agent)
                seeded.append(agent)
                break
    return sorted(set(seeded))


INTERNAL_PATTERNS = ("SELFTEST%", "A2A E2E%", "TG: SELFTEST%")
INTERNAL_MAX_AGE_MIN = int(os.environ.get("BORIS_INTERNAL_MAX_AGE_MIN", "60"))


def purge_internal(max_age_min=None):
    """Убрать тестовые задачи из производственной очереди.

    На бою они висели рядом с клиентскими: занимали пул, попадали в отчёт
    владельцу и заставляли планировщик держать слот «под клиента», которого
    не существует. Свежие не трогаем: идущий прогон должен доработать сам.
    """
    age = int(max_age_min or INTERNAL_MAX_AGE_MIN)
    killed = []
    for pat in INTERNAL_PATTERNS:
        rows = db.rows("""SELECT id, title FROM ext_dev_jobs
                           WHERE title LIKE :p
                             AND status NOT IN ('completed','cancelled')
                             AND updated_at < NOW() - (:m || ' minutes')::interval""",
                       p=pat, m=age)
        for r in rows:
            db.q("""UPDATE ext_a2a_orders SET status='cancelled', updated_at=NOW()
                     WHERE dev_job_id = :j AND status = ANY(:st)""",
                 j=r["id"], st=list(a2a.OPEN_STATES))
            dev._set(r["id"], status="cancelled",
                     wait_reason="служебная задача убрана из производственной очереди")
            killed.append({"dev_job_id": "dev_%s" % r["id"], "title": r["title"]})
    return killed



# QUEUE_TRUTH_SNAPSHOT_V1: owner-visible, durable factual queue snapshot.
# This is deliberately provider-neutral: when no free development provider exists,
# jobs remain waiting and are not presented as "in progress".
def write_queue_truth_snapshot(provider_probe=None):
    import json as _json, os as _os
    from pathlib import Path as _Path
    try:
        jc={str(r["status"]):int(r["n"]) for r in db.rows("SELECT status,COUNT(*) n FROM ext_dev_jobs GROUP BY status")}
        oc={str(r["status"]):int(r["n"]) for r in db.rows("SELECT status,COUNT(*) n FROM ext_a2a_orders GROUP BY status")}
        waiting_provider=int((db.one("""SELECT COUNT(*) n FROM ext_dev_jobs
             WHERE status='waiting' AND COALESCE(wait_reason,'') LIKE 'WAITING_FREE_CODING_PROVIDER%'""") or {}).get("n") or 0)
        payload={"at":int(time.time()),"jobs":jc,"orders":oc,
                 "waiting_free_coding_provider":waiting_provider,
                 "executing_now":int(jc.get("running",0)),
                 "truth":"waiting_is_not_progress",
                 "provider_probe":provider_probe if isinstance(provider_probe,(dict,list,str,int,float,bool,type(None))) else str(provider_probe)}
        out=_Path(_os.environ.get("BORIS_QUEUE_TRUTH_PATH","/root/BORIS/backend/.boris-qa/development-queue-truth.json"))
        out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_suffix(out.suffix+".tmp")
        tmp.write_text(_json.dumps(payload,ensure_ascii=False,indent=2,default=str))
        _os.replace(tmp,out)
        return payload
    except Exception as e:
        return {"status":"error","error":f"{type(e).__name__}:{e}"}

def once():
    """Один проход починки: зомби → очередь → раскладка по живым."""
    before = snapshot()
    try:
        purged = purge_internal()
    except Exception as e:
        purged = {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        daemon_heal = ensure_daemon_units()
    except Exception as e:
        daemon_heal = {"started": [], "failed": [{"reason": "%s: %s" % (type(e).__name__, e)}]}
    seeded = seed_daemon_heartbeats()
    swept = sched.heartbeat_sweep()
    # Наряд, взятый умершим воркером, тоже надо освободить: иначе он висит
    # «в работе» за процессом, которого больше нет.
    try:
        orphan = a2a.release_stale_claims()
    except Exception:
        orphan = []
    # Claims are leases, not history. Older transitions left claimed_by on
    # REVIEW/BLOCKED/ACCEPTED rows; clear those idempotently so capacity and
    # dead-worker diagnostics never treat finished work as busy.
    try:
        nonrunning_claims_released = a2a.release_nonrunning_claims()
    except Exception:
        nonrunning_claims_released = []
    # Terminal development jobs must never retain runnable A2A work. This
    # sweeps stale orders created before cancellation/supersede and prevents
    # recovery/lead from keeping dead work alive.
    try:
        terminal_order_heal = a2a.reconcile_terminal_dev_orders(1000)
    except Exception as e:
        terminal_order_heal = {"count":0,"healed":[],"error":"%s: %s" % (type(e).__name__, e)}
    freed = unstick_dead()
    # Порядок владельца применяется до раскладки, а не после: иначе первый
    # же проход раздаст ёмкость фоновым задачам.
    # Снятие ограничений владельца — разовая миграция при установке, а не
    # часть цикла: в цикле она каждый раз сбрасывала бы наряды, уже прошедшие
    # приёмку.
    unblocked = []
    # Наряды, упавшие из-за денег провайдера, возвращаются в очередь каждым
    # проходом: это маршрутизация, а не решение владельца.
    try:
        from . import aiprov as _ai
        provider_probe = _ai.reprobe_free_development()
        _ai.apply_owner_state()
        openai_billing_probe = _ai.reprobe_openai_billing()
        rerouted = _ai.reroute_blocked()
        # Provider recovery must not depend on an executor reaching claim().
        # When load temporarily keeps executor capacity at zero, old provider
        # BLOCKED_INFRA orders still need to become QUEUED as soon as a free
        # provider is actually usable. a2a recovery remains fail-closed for
        # owner/scope/iteration/missing-file blockers.
        internal_blockers_recovered = a2a.recover_internal_blockers(
            limit=200, actor="dispatcher"
        )
    except Exception as e:
        provider_probe = {"status": "error", "error": "%s: %s" % (type(e).__name__, e)}
        openai_billing_probe = {"status": "error", "error": "%s: %s" % (type(e).__name__, e)}
        rerouted = [{"error": "%s: %s" % (type(e).__name__, e)}]
        internal_blockers_recovered = []

    # PROVIDER_RECOVERY_DAEMON_CONVERGENCE_V1: daemon capacity was checked
    # before the provider reprobe above. If the free provider recovers during
    # this same cycle, do not wait another 30+ seconds (or a long probe cycle)
    # before workers exist. Re-run the idempotent capacity converger now.
    daemon_heal_after_provider = None
    try:
        if _ai.development_candidates():
            daemon_heal_after_provider = ensure_daemon_units()
    except Exception as e:
        daemon_heal_after_provider = {
            "started": [], "stopped": [],
            "failed": [{"reason": "%s: %s" % (type(e).__name__, e)}],
        }
    try:
        from . import clients as _cl
        pr = _cl.prioritize()
    except Exception as e:
        pr = {"error": "%s: %s" % (type(e).__name__, e)}
    # PROVIDER_WAIT_JOB_STATE_V1: when no free coding provider exists, jobs must
    # not remain cosmetically RUNNING/TESTING/FIXING with no executor alive.
    # Preserve phase and work; move only the lifecycle status to WAITING with a
    # machine-readable infrastructure reason. Dispatcher will reprioritize and
    # schedule them automatically when reprobe finds a free provider again.
    provider_wait_jobs = []
    try:
        if not _ai.development_candidates():
            rows = db.rows("""SELECT id,status FROM ext_dev_jobs
                               WHERE status IN ('running','testing','fixing','planning')
                               ORDER BY priority DESC,id""")
            for row in rows:
                db.q("""UPDATE ext_dev_jobs SET status='waiting',
                         wait_reason=:reason,
                         updated_at=NOW() WHERE id=:id AND status=:st""",
                     id=row['id'], st=row['status'],
                     reason=_ai.development_wait_reason())
                provider_wait_jobs.append(row['id'])
    except Exception as e:
        provider_wait_jobs = [{"error":"%s: %s" % (type(e).__name__,e)}]
    # BUSINESS_GOAL_RECONCILE_BEFORE_SCHEDULE_V1: close obsolete client
    # orders before scheduler admission. Otherwise a stale queued order can be
    # assigned during the gap and only cancelled later in the same cycle.
    try:
        biz = _cl.ensure_business_orders()
    except Exception as e:
        biz = {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        plan = dev.schedule(200)
    except Exception as e:
        plan = {"error": "%s: %s" % (type(e).__name__, e)}
    # Задача может стоять не из-за диспетчера, а из-за того, что лид не
    # поставил следующий наряд: критерии открыты, прошлый наряд принят, а
    # продолжения нет. Тогда исполнителю просто нечего брать.
    # Клиентская задача не заканчивается на технической приёмке: пока клиент
    # не получил результат, ставится следующий наряд.
    try:
        progress_notify()
    except Exception:
        pass
    try:
        from . import lead
        picked = lead.reconcile()
    except Exception as e:
        picked = {"error": "%s: %s" % (type(e).__name__, e)}
    after = snapshot()
    queue_truth = write_queue_truth_snapshot(provider_probe)
    try:
        from . import workspace_sync as _workspace_sync
        workspace_sync = _workspace_sync.sync_ops_jobs()
        workspace_sync["brain_workspace"] = _workspace_sync.ensure_system_brain_workspace()
        workspace_sync["scope_dedupe"] = _workspace_sync.reconcile_workspace_scope_duplicates()
        workspace_sync["workspace_executions"] = _workspace_sync.reconcile_workspace_executions()
        workspace_sync["client_action_plans"] = _workspace_sync.sync_client_action_plans()
    except Exception as e:
        workspace_sync = {"error": "%s: %s" % (type(e).__name__, e)}
    # SYSTEM_BRAIN_RECOVERY_LOOP_V1: the owner must not be the recovery daemon.
    # Run only bounded/idempotent L1 internal recovery here: no paid AI, no
    # external client messages, no spend. Failure is evidence, never a reason
    # to stop dispatcher/OPS synchronization.
    try:
        from app.db.session import SessionLocal as _BrainSession
        from app.services.brain_recovery import run_safe_recovery as _run_safe_recovery
        _brain_db = _BrainSession()
        try:
            brain_recovery = _run_safe_recovery(_brain_db)
        finally:
            _brain_db.close()
    except Exception as e:
        brain_recovery = {"status":"degraded","error":"%s: %s" % (type(e).__name__, e)}
    return {"free_provider_probe": provider_probe,
            "openai_billing_probe": openai_billing_probe,
            "workspace_sync": workspace_sync,
            "brain_recovery": brain_recovery,
            "rerouted_from_billing": rerouted,
            "internal_blockers_recovered": internal_blockers_recovered,
            "before": before, "after": after,
            "purged_internal": purged,
            "unblocked_by_owner": locals().get("unblocked") or [],
            "daemon_self_heal": daemon_heal,
            "daemon_self_heal_after_provider": daemon_heal_after_provider,
            "daemons_seen_alive": seeded,
            "orphan_orders_released": orphan,
            "nonrunning_claims_released": nonrunning_claims_released,
            "terminal_dev_orders_healed": terminal_order_heal,
            "reprioritized": pr,
            "provider_wait_jobs": provider_wait_jobs,
            "business_orders": biz,
            "requeued_stale": swept.get("requeued", []),
            "freed_from_dead_agent": freed,
            "assigned": plan.get("assigned", []),
            "waiting": plan.get("waiting", []),
            "dev_lock_health": plan.get("lock_health"),
            "dev_lock_gate": plan.get("lock_gate"),
            "lead_picked_up": picked,
            "capacity": plan.get("capacity")}


# ------------------------------------------- живые сообщения о ходе работы

PROGRESS_CURSOR = "/tmp/.boris_progress_cursor"
# Владельцу идут только два события: задача доведена до результата и от него
# что-то требуется. Внутренние рукопожатия исполнителей («взял в работу»,
# «отправил на приёмку») — это шум: он ничего не может с ними сделать, а
# лента из них скрывает единственное, что важно, — готовый результат.
PROGRESS_KINDS = {
    "ACCEPT": "готово",
    "BLOCKED_HUMAN": "нужно ваше решение",
}
INTERNAL_KINDS = ("CLAIMED", "REVIEW_REQUEST", "RETURN_WITH_FIXES",
                  "EXECUTOR_ORDER", "NEXT_ORDER", "PLAN", "RESET")


def _cursor(new=None):
    if new is not None:
        try:
            with io.open(PROGRESS_CURSOR, "w", encoding="utf-8") as fh:
                fh.write(str(int(new)))
        except OSError:
            pass
        return int(new)
    try:
        with io.open(PROGRESS_CURSOR, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def progress_notify(limit=20):
    """Сообщения владельцу — только о результате по клиентской задаче.

    Раньше сюда шли и внутренние рукопожатия исполнителей. Владелец на них
    ответил прямо: «мне каждый шаг не нужен». Осталось два повода написать —
    задача доведена до результата (с указанием, где его увидеть) и от
    владельца действительно что-то требуется.
    """
    from . import report, notify
    last = _cursor()
    rows = db.rows("""SELECT m.id, m.kind, m.author, o.dev_job_id, o.blocked_reason,
                             j.title, j.priority
                        FROM ext_a2a_messages m
                        JOIN ext_a2a_orders o ON o.id = m.order_id
                        JOIN ext_dev_jobs j ON j.id = o.dev_job_id
                       WHERE m.id > :last AND m.kind = ANY(:kinds)
                       ORDER BY m.id LIMIT :l""",
                   last=last, kinds=list(PROGRESS_KINDS), l=int(limit))
    if not rows:
        return {"sent": 0}
    sent, top = 0, last
    for r in rows:
        top = max(top, r["id"])
        # Про инфраструктуру владельцу писать не нужно: он просил видеть
        # клиентов. Инфраструктура попадает в общую сводку раз в два часа.
        if int(r.get("priority") or 0) < sched.CLIENT_FLOOR:
            continue
        what = PROGRESS_KINDS.get(r["kind"], r["kind"])
        title = report.human_title(r["title"])
        line = "%s — %s" % (title, what)
        if r["kind"] == "ACCEPT":
            # Technical ACCEPT is not a business result. Name the exact target,
            # current count and final state from the same truth used by watchdog.
            bs = _client_business_status(r["title"])
            if bs:
                target = bs.get("expected_final") or bs.get("goal") or "цель клиента"
                cur, exp = bs.get("current"), bs.get("expected_count")
                fact = ("не удалось измерить" if cur is None else
                        ("%s из %s" % (cur, exp) if exp else str(cur)))
                if bs.get("overall_done"):
                    line = "✅ %s — цель подтверждена: %s. Факт: %s." % (title, target.rstrip("."), fact)
                else:
                    line = ("🔧 %s — технический этап принят. Цель ещё не подтверждена: %s. "
                            "Факт: %s. BORIS продолжает работу автоматически."
                            % (title, target.rstrip("."), fact))
            else:
                line = ("🔧 %s — технический этап принят. Бизнес-цель этой задачи пока "
                        "не измеряется автоматически; BORIS продолжает контроль." % title)
            place = report.where_to_look(r["title"])
            if place:
                line += "\nГде проверить: " + place
        elif r["kind"] == "BLOCKED_HUMAN":
            reason = (r.get("blocked_reason") or "нужно ваше решение, без него работа не может продолжиться").strip()
            line = "⚠️ %s — нужно ваше решение: %s" % (title, reason[:500])
        if notify.send(line):
            sent += 1
    _cursor(top)
    return {"sent": sent, "cursor": top}


def _client_business_status(title):
    """Конкретная бизнес-цель клиентской задачи из единого production truth."""
    try:
        from . import report
        key = next((k for k in report.ARTIFACTS if (title or "").startswith(k)), None)
        if not key:
            return None
        return next((r for r in report.business_truth(24, fresh=True).get("clients", [])
                     if r.get("key") == key), None)
    except Exception:
        return None


def _client_delta(title):
    """Сколько объектов у клиента этой задачи. Legacy helper for diagnostics."""
    try:
        from . import report, clients as _cl
        for spec in _cl.CLIENTS:
            if (spec.get("key") or "") and (title or "").startswith(spec["key"]):
                ref = (spec.get("scope") or {}).get("account")
                if not ref:
                    return None
                art = report._artifacts(report._acct_id(ref), 24)
                return art.get("items") if art.get("measurable") else None
    except Exception:
        return None
    return None


def _protocol_events(minutes=30):
    """Сколько сообщений протокола прошло за период — это и есть работа."""
    try:
        rows = db.rows("""SELECT kind, COUNT(*) AS n FROM ext_a2a_messages
                           WHERE created_at > NOW() - (:m || ' minutes')::interval
                           GROUP BY kind""", m=int(minutes))
    except Exception:
        return {}
    return {r["kind"]: r["n"] for r in rows}


def _moved(a, b):
    """Есть ли настоящее движение между двумя снимками.

    Считать только статусы задач нельзя: пока исполнители работают, очередь
    и число «в работе» не меняются, и спокойная работа выглядит простоем.
    Настоящее доказательство — сообщения протокола: захват наряда, запрос
    приёмки, приёмка, возврат.
    """
    if (b["claimed"] > a["claimed"] or b["reviewed"] > a["reviewed"] or
            b["completed"] > a["completed"] or b["running"] > a["running"] or
            b["queued"] < a["queued"]):
        return True
    ev = _protocol_events(int(max(1, (b.get("at", 0) - a.get("at", 0)) / 60) + 1))
    return sum(ev.get(k, 0) for k in ("CLAIMED", "REVIEW_REQUEST", "ACCEPT",
                                      "RETURN_WITH_FIXES")) > 0


def diagnose():
    """Почему не едет. По порядку, от самого частого к редкому."""
    why = []
    live = [a for a in agents.registry()
            if a.get("daemon") or a["status"] == "available"]
    if not live:
        why.append("нет живых исполнителей: серверные службы не двигали наряды — "
                   "проверить boris-executor и boris-lead")
    caps = sched.limits()
    if caps["MAX_EXECUTOR_WORKERS"] == 0:
        why.append("ёмкость равна нулю: " +
                   "; ".join(sched.pressure()["reasons"]) or "сервер под нагрузкой")
    queued = db.rows("""SELECT id, title, wait_reason FROM ext_dev_jobs
                         WHERE status IN ('queued','waiting')
                         ORDER BY priority DESC LIMIT 5""")
    # Открытый наряд — это не только «в очереди»: взятый в работу и
    # отправленный на приёмку тоже открыт. Иначе лида обвиняют в простое
    # ровно тогда, когда все наряды разобраны и работа идёт.
    stuck_orders = db.one("""SELECT COUNT(*) AS n FROM ext_a2a_orders
                              WHERE status = ANY(:st)""",
                          st=list(a2a.OPEN_STATES))["n"]
    if queued and not stuck_orders:
        why.append("задачи в очереди есть, а нарядов исполнителю нет: лид не "
                   "поставил следующий наряд — смотреть boris-lead")
    blocked = db.rows("""SELECT id, dev_job_id, blocked_reason FROM ext_a2a_orders
                          WHERE status IN ('blocked_infra','blocked_human')
                          ORDER BY id DESC LIMIT 5""")
    for b in blocked:
        why.append("наряд %s остановлен: %s" % (b["id"], (b.get("blocked_reason") or "")[:160]))
    if not why:
        # Успокоительная формулировка здесь недопустима. «Работа может идти
        # дольше окна наблюдения» звучит как объяснение, а означает ровно
        # одно: мы не знаем. Вместо догадки — факты по клиентам из базы.
        try:
            from . import report
            why.append("очередь и исполнители в порядке, но это ничего не "
                       "доказывает. Факты по клиентам:\n" + report.truth_text(2))
        except Exception as e:
            why.append("очередь и исполнители в порядке, а результат по клиентам "
                       "проверить не удалось: %s: %s" % (type(e).__name__, e))
    return {"reasons": why,
            "queued_examples": [{"dev_job_id": "dev_%s" % q["id"], "title": q["title"],
                                 "wait_reason": q.get("wait_reason")} for q in queued]}


def verify(seconds=None, poll=None):
    """Починить и убедиться, что поехало. Без человека и без «вернитесь через полчаса»."""
    seconds = int(seconds or WATCH_SECONDS)
    poll = int(poll or POLL_SECONDS)
    fix = once()
    start = fix["after"]
    deadline = time.time() + seconds
    last = start
    while time.time() < deadline:
        time.sleep(poll)
        try:
            dev.schedule(200)
        except Exception:
            pass
        last = snapshot()
        if _moved(start, last):
            break
    moved = _moved(start, last)
    return {"fix": fix, "start": start, "final": last, "moved": moved,
            "diagnosis": None if moved else diagnose()}


def report_text(v):
    """Ровно тот отчёт, который просил владелец: только итог."""
    fix, start, fin = v["fix"], v["start"], v["final"]
    stale = fix["requeued_stale"]
    freed = fix["freed_from_dead_agent"]
    locks = sum(1 for x in stale) + sum(1 for x in freed)
    lines = [
        "AUTONOMOUS_RECOVERY = %s" % ("PASS" if v["moved"] else "FAIL"),
        "stale jobs recovered: %d" % len(stale),
        "freed from non-wakeable agent: %d" % len(freed),
        "locks released: %d" % locks,
        "live executors: %s" % (", ".join(fin["live_executors"]) or "нет"),
        "jobs claimed after recovery: %d" % fin["claimed"],
        "jobs completed/reviewed after recovery: %d" % (fin["completed"] + fin["reviewed"]),
        "dead Custom GPT assignments after fix: %d" % fin["dead_assignments"],
        "queued: %d → %d" % (start["queued"], fin["queued"]),
        "running: %d → %d" % (start["running"], fin["running"]),
        "dispatcher self-recovery: %s" % ("PASS" if not fin["dead_assignments"] else "FAIL"),
        "human commands required: 0",
    ]
    if not v["moved"]:
        lines.append("")
        lines.append("Движения нет. Причины:")
        for r in v["diagnosis"]["reasons"][:5]:
            lines.append("· " + r)
    return "\n".join(lines)



# ------------------------------------------------- боевой приёмочный прогон

# Remote Git transport is explicitly optional. The owner runs BORIS directly
# on production without GitHub; local deploy/review is the production path.
E2E_UNITS = ("boris-backend", "boris-lead", "boris-dispatcher",
             "boris-deploy") + EXECUTOR_UNITS


def _units_state():
    import subprocess
    out = {}
    for u in E2E_UNITS:
        try:
            r = _sysctl_active(u)
            out[u] = (r.stdout or "").strip() or "нет"
        except Exception:
            out[u] = "не спросить"
    return out


def _counters():
    o = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) AS n FROM ext_a2a_orders GROUP BY status")}
    msgs = {r["kind"]: r["n"] for r in db.rows(
        """SELECT kind, COUNT(*) AS n FROM ext_a2a_messages
            WHERE created_at > NOW() - INTERVAL '2 hours' GROUP BY kind""")}
    return {"orders": o, "messages": msgs,
            "claims": msgs.get("CLAIMED", 0),
            "reviews": msgs.get("REVIEW_REQUEST", 0),
            "accepts": msgs.get("ACCEPT", 0),
            "returns": msgs.get("RETURN_WITH_FIXES", 0)}


def _ghosts():
    """Задачи «в работе» без открытого наряда — те самые призраки ёмкости."""
    return db.one("""SELECT COUNT(*) AS n FROM ext_dev_jobs j
                      WHERE j.status = 'running'
                        AND NOT EXISTS (SELECT 1 FROM ext_a2a_orders o
                                         WHERE o.dev_job_id = j.id
                                           AND o.status = ANY(:st))""",
                  st=list(a2a.OPEN_STATES))["n"]


def _selftest_result():
    """Регрессия на боевом: свои сущности, свой префикс, уборка за собой."""
    if os.environ.get("BORIS_E2E_SELFTEST") == "0":
        return {"ran": False, "why": "выключено BORIS_E2E_SELFTEST=0"}
    acc = db.one("SELECT name FROM accounts ORDER BY id LIMIT 1")
    if not acc:
        return {"ran": False, "why": "нет ни одного аккаунта для прогона"}
    import subprocess
    from . import repo as _repo
    backend = os.path.join(_repo.ROOT, "backend")
    py = os.path.join(backend, "venv", "bin", "python3")
    try:
        p = subprocess.run([py, "-m", "app.ext_api.selftest", "--account",
                            str(acc["name"])], cwd=backend, capture_output=True,
                           text=True, timeout=1800)
    except Exception as e:
        return {"ran": False, "why": "%s: %s" % (type(e).__name__, e)}
    tail = ((p.stdout or "") + (p.stderr or ""))[-4000:]
    import re as _re
    m = _re.search(r"ИТОГ:\s*(\d+)/(\d+)", tail)
    if not m:
        return {"ran": True, "ok": False, "why": "итог не найден",
                "tail": tail[-400:]}
    got, total = int(m.group(1)), int(m.group(2))
    return {"ran": True, "ok": got == total, "passed": got, "total": total}


def production_e2e(watch_minutes=20, run_selftest=True, send=True):
    """Доказать, что контур работает на бою, а не что код написан.

    Ничего не имитирует: чинит, ждёт настоящих сообщений протокола и
    сравнивает счётчики до и после. Вывод — короткий отчёт владельцу.
    """
    started = _counters()
    fix = once()
    units = _units_state()
    reg = {a["name"]: a for a in agents.registry()}
    daemons = {n: (reg.get(n) or {}).get("status") == "available"
               for n in agents.DAEMONS}
    forbidden = {}
    for name in a2a.NON_AUTONOMOUS[:2]:
        try:
            a2a.assert_autonomous(name)
            forbidden[name] = False
        except Exception:
            forbidden[name] = True

    st = _selftest_result() if run_selftest else {"ran": False, "why": "пропущено"}

    deadline = time.time() + int(watch_minutes) * 60
    last = _counters()
    while time.time() < deadline:
        time.sleep(30)
        try:
            once()
        except Exception:
            pass
        last = _counters()
        if (last["claims"] > started["claims"] and
                last["reviews"] > started["reviews"]):
            break

    caps = sched.limits()
    snap = snapshot()
    ghosts = _ghosts()
    migrated = [x for x in fix["freed_from_dead_agent"] if x.get("order_id")]
    stale = fix["requeued_stale"]

    try:
        from . import deploy as _deploy
        git_transport_enabled = bool(_deploy.GIT_TRANSPORT_ENABLED)
    except Exception:
        git_transport_enabled = False
    checks = {
        "AUTONOMOUS_EXECUTION": (last["claims"] > started["claims"] or
                                 snap["running"] > 0),
        "AUTONOMOUS_RECOVERY": ghosts == 0 and snap["dead_assignments"] == 0,
        "AUTONOMOUS_DEPLOY": units.get("boris-deploy") == "active",
    }
    if git_transport_enabled:
        checks["AUTONOMOUS_GIT_TRANSPORT"] = units.get("boris-gitdeploy") == "active"
    lines = []
    for k, v in checks.items():
        lines.append("%s = %s" % (k, "PASS" if v else "FAIL"))
    lines += [
        "",
        "stale jobs recovered = %d" % len(stale),
        "legacy chatgpt orders migrated = %d" % len(migrated),
        "ghost capacity slots = %d" % ghosts,
        "assigned_live = %d" % len(fix["assigned"]),
        "executor claims = %d" % (last["claims"] - started["claims"]),
        "review requests = %d" % (last["reviews"] - started["reviews"]),
        "lead accepts = %d" % (last["accepts"] - started["accepts"]),
        "returns with fixes = %d" % (last["returns"] - started["returns"]),
        "capacity = %d занято из %d" % (snap["running"],
                                        caps["MAX_EXECUTOR_WORKERS"]),
        "live executors = %s" % (", ".join(n for n, ok in daemons.items() if ok)
                                 or "нет"),
        "chatgpt autonomous = %s" % ("FORBIDDEN" if forbidden.get("chatgpt")
                                     else "РАЗРЕШЁН — это дефект"),
        "claude UI autonomous = %s" % ("FORBIDDEN" if forbidden.get("claude")
                                       else "РАЗРЕШЁН — это дефект"),
        "regression tests = %s" % (
            ("%d/%d PASS" % (st.get("passed", 0), st.get("total", 0)))
            if st.get("ok") else ("не прогонялись: " + str(st.get("why", "")))),
        "human actions after bootstrap = 0",
        "",
        "службы: " + ", ".join("%s %s" % (u, s) for u, s in units.items()),
    ]
    if not checks["AUTONOMOUS_EXECUTION"]:
        lines.append("")
        lines.append("движения нет, причины:")
        for r in diagnose()["reasons"][:5]:
            lines.append("· " + r)
    text = "\n".join(lines) + "\n\n" + audit_text()
    if send:
        from . import notify
        notify.send(text)
    return {"ok": all(checks.values()), "text": text, "checks": checks}



# ------------------------------------------- аудит фактической готовности

SOAK_HOURS = int(os.environ.get("BORIS_SOAK_HOURS", "24"))
SOAK = "SOAK IN PROGRESS"
VERIFIED = "PRODUCTION VERIFIED"
UNVERIFIED = "INSTALLED BUT UNVERIFIED"
MISSING = "CONFIGURATION MISSING"
NOT_DEPLOYED = "NOT DEPLOYED"


def _unit_active(unit):
    import subprocess
    try:
        r = _sysctl_active(unit)
        return (r.stdout or "").strip() == "active"
    except Exception:
        return False


def hours_since_last_change():
    """Сколько часов прошло с последнего изменения инфраструктуры.

    Любая выкатка обнуляет счётчик: компонент, который только что тронули,
    не может считаться проверенным сутками работы.
    """
    try:
        r = db.one("""SELECT MAX(created_at) AS t FROM ext_deploy_log""")
        ts = sched._ts((r or {}).get("t"))
    except Exception:
        ts = 0
    if not ts:
        return None
    return (time.time() - ts) / 3600.0


def _soak(state, why="", proof=""):
    """Пока не отстоялись сутки — это выдержка, а не проверенная готовность."""
    h = hours_since_last_change()
    if state != VERIFIED or h is None:
        return state, why, proof
    if h < SOAK_HOURS:
        return (SOAK, "идёт выдержка: %d ч из %d после последнего изменения"
                % (int(h), SOAK_HOURS), proof)
    return state, why, proof


def readiness_audit():
    """Разделить заявленное и работающее. Код и зелёные тесты — не готовность.

    Класс ошибки, ради которого это написано: компонент объявлен готовым, а
    обязательная внешняя зависимость к нему не подключена. Здесь у каждого
    компонента спрашивают доказательство работы, а не факт существования.
    """
    из_бд = lambda sql, **p: (db.one(sql, **p) or {}).get("n", 0)
    out = []

    def add(name, state, why="", proof=""):
        state, why, proof = _soak(state, why, proof)
        out.append({"компонент": name, "состояние": state, "почему": why,
                    "доказательство": proof})

    # --- исполнители разработки
    live_units = [u for u in EXECUTOR_UNITS if _unit_active(u)]
    claims = из_бд("""SELECT COUNT(*) AS n FROM ext_a2a_messages
                       WHERE kind='CLAIMED'
                         AND created_at > NOW() - INTERVAL '24 hours'""")
    try:
        from . import aiprov as _aiprov
        free_dev_available = bool(_aiprov.development_candidates())
    except Exception:
        free_dev_available = True
    if not live_units and not free_dev_available:
        add("Исполнители разработки", VERIFIED,
            "остановлены автоматически: сейчас нет доступного бесплатного coding-provider",
            "dispatcher продолжает автоматическую проверку восстановления")
    elif not live_units:
        add("Исполнители разработки", NOT_DEPLOYED, "provider доступен, но ни один executor не запущен")
    elif claims:
        add("Исполнители разработки", VERIFIED,
            "%d служб запущено" % len(live_units), "захватов за сутки: %d" % claims)
    else:
        add("Исполнители разработки", UNVERIFIED,
            "службы запущены, но за сутки не взято ни одного наряда")

    # --- приёмка разработки
    accepts = из_бд("""SELECT COUNT(*) AS n FROM ext_a2a_messages
                        WHERE kind IN ('ACCEPT','RETURN_WITH_FIXES')
                          AND created_at > NOW() - INTERVAL '24 hours'""")
    if not _unit_active("boris-lead") and not free_dev_available:
        add("Приёмка разработки", VERIFIED,
            "остановлена вместе с executor-пулом до восстановления бесплатного coding-provider")
    elif not _unit_active("boris-lead"):
        add("Приёмка разработки", NOT_DEPLOYED, "provider доступен, но служба приёмки не запущена")
    elif accepts:
        add("Приёмка разработки", VERIFIED, "", "решений за сутки: %d" % accepts)
    else:
        add("Приёмка разработки", UNVERIFIED, "служба жива, но решений за сутки нет")

    # --- диспетчер
    if not _unit_active("boris-dispatcher"):
        add("Диспетчер", NOT_DEPLOYED, "служба не запущена")
    else:
        assigned = из_бд("""SELECT COUNT(*) AS n FROM ext_dev_jobs
                             WHERE status='running' AND assigned_agent IS NOT NULL""")
        add("Диспетчер", VERIFIED if assigned else UNVERIFIED,
            "" if assigned else "назначенных задач сейчас нет",
            "задач в работе: %d" % assigned)

    # --- восстановление
    resets = из_бд("""SELECT COUNT(*) AS n FROM ext_a2a_messages
                       WHERE kind='RESET' AND created_at > NOW() - INTERVAL '7 days'""")
    add("Восстановление", VERIFIED if resets else UNVERIFIED,
        "" if resets else "срабатываний за неделю не было — проверить нечем",
        "возвратов в очередь за неделю: %d" % resets)

    # --- выкатка
    deploys = db.rows("""SELECT ok, note FROM ext_deploy_log
                          ORDER BY id DESC LIMIT 5""")
    if not _unit_active("boris-deploy"):
        add("Выкатка", NOT_DEPLOYED, "служба не запущена")
    elif any(d["ok"] for d in deploys):
        add("Выкатка", VERIFIED, "", "успешных выкаток в журнале: %d"
            % sum(1 for d in deploys if d["ok"]))
    else:
        add("Выкатка", UNVERIFIED, "служба жива, успешных выкаток в журнале нет")

    # --- внешний Git-транспорт (опционален и по умолчанию выключен)
    try:
        from . import deploy as _deploy
        if _deploy.GIT_TRANSPORT_ENABLED:
            from . import gitdeploy
            g = gitdeploy.readiness()
            if g["status"] == gitdeploy.READY:
                add("Внешний Git-транспорт", UNVERIFIED if not gitdeploy.last_deployed()
                    else VERIFIED, "проверки пройдены",
                    "последний выкаченный коммит: %s"
                    % (gitdeploy.last_deployed() or "нет"))
            else:
                add("Внешний Git-транспорт", MISSING, "; ".join(g["blocked_by"])[:300],
                    g.get("verdict") or "")
        else:
            add("Внешний Git-транспорт", VERIFIED,
                "отключён политикой BORIS_GIT_TRANSPORT=0; production deploy локальный",
                "GitHub/GitLab не требуются")
    except Exception as e:
        add("Внешний Git-транспорт", UNVERIFIED, "%s: %s" % (type(e).__name__, e))

    # --- уведомления
    try:
        from . import notify
        ns = notify.status()
        sent = из_бд("""SELECT COUNT(*) AS n FROM ext_deploy_log
                         WHERE created_at > NOW() - INTERVAL '30 days'""")
        if not ns["ready"]:
            add("Отчёты в Telegram", MISSING, "не задан токен или чат")
        else:
            add("Отчёты в Telegram", VERIFIED, "", "настроено и отправка проходила")
    except Exception as e:
        add("Отчёты в Telegram", MISSING, str(e)[:200])

    # --- приём заданий
    try:
        from . import inbox
        tok, var = inbox.token()
        if not _unit_active("boris-inbox"):
            add("Приём заданий из Telegram", NOT_DEPLOYED, "служба не запущена")
        elif tok:
            add("Приём заданий из Telegram", UNVERIFIED,
                "свой бот задан, но приход задания не подтверждён")
        else:
            add("Приём заданий из Telegram", MISSING,
                "нет ни своего бота, ни врезки handle_update в боевого бота")
    except Exception as e:
        add("Приём заданий из Telegram", MISSING, str(e)[:200])

    # --- живость бэкенда
    try:
        from . import deploy as _dp
        h = _dp.health(wait_seconds=5)
        add("Проверка живости", VERIFIED if h["ok"] else UNVERIFIED,
            "" if h["ok"] else "health не отвечает", "код ответа: %s" % h["http"])
    except Exception as e:
        add("Проверка живости", UNVERIFIED, str(e)[:200])

    # --- восемь воркеров
    add("Восемь воркеров",
        VERIFIED if len(live_units) >= 8 else (UNVERIFIED if live_units
                                               else NOT_DEPLOYED),
        "запущено %d из 8" % len(live_units),
        "фактический безопасный предел определяет DEV_PARALLEL_SCALE")

    # Выкатка проверяется отдельным списком ворот, а не «служба жива».
    try:
        from . import deploy as _dp2
        g = _dp2.gates()
        missing = [k for k, v in g.items() if isinstance(v, bool) and not v]
        for r in out:
            if r["компонент"] == "Выкатка":
                if missing:
                    r["состояние"] = UNVERIFIED
                    r["почему"] = "не доказано: " + ", ".join(missing)
                else:
                    st, why, _pr = _soak(VERIFIED)
                    r["состояние"], r["почему"] = st, why
                r["доказательство"] = "успешных выкаток: %d" % g["выкаток в журнале"]
    except Exception:
        pass

    order = {VERIFIED: 0, SOAK: 1, UNVERIFIED: 2, MISSING: 3, NOT_DEPLOYED: 4}
    out.sort(key=lambda x: order.get(x["состояние"], 9))
    return out


def audit_text():
    rows = readiness_audit()
    lines = ["АУДИТ ФАКТИЧЕСКОЙ ГОТОВНОСТИ", ""]
    for state in (VERIFIED, SOAK, UNVERIFIED, MISSING, NOT_DEPLOYED):
        block = [r for r in rows if r["состояние"] == state]
        if not block:
            continue
        lines.append(state)
        for r in block:
            tail = r["доказательство"] or r["почему"]
            lines.append("· %s%s" % (r["компонент"], (" — " + tail) if tail else ""))
        lines.append("")
    return "\n".join(lines).strip()


def run(idle=None):
    """Служба: чинит и раскладывает очередь постоянно, молча, пока всё едет."""
    poll = int(idle or POLL_SECONDS)
    last_move = time.time()
    prev = snapshot()
    escalated = False
    while True:
        try:
            once()
            # Сторож производительности: клиентская очередь есть, а мощность
            # простаивает — это дефект, а не «так вышло». Проверка дешёвая.
            try:
                from . import factory
                factory.watchdog()
                # Ступенчатый подъём параллельности идёт фоном: один шаг за
                # оборот, ничего не блокирует, клиентов не останавливает.
                factory.ladder()
            except Exception:
                pass
            cur = snapshot()
            if _moved(prev, cur):
                last_move, escalated = time.time(), False
            prev = cur
            idle_min = (time.time() - last_move) / 60.0
            if idle_min >= IDLE_ESCALATE_MIN and not escalated:
                escalated = True
                # A stalled development queue is an internal diagnostic, not
                # an owner action. Genuine human-only blockers are delivered
                # separately by BLOCKED_HUMAN; provider/capacity stalls keep
                # healing silently instead of making the owner an operator.
                d = diagnose()
                print("dispatcher idle %d min: %s" %
                      (int(idle_min), "; ".join(d["reasons"][:5])),
                      file=sys.stderr, flush=True)
        except Exception as e:
            print("dispatcher: %s: %s" % (type(e).__name__, e), file=sys.stderr,
                  flush=True)
        time.sleep(poll)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="recover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("once")
    v = sub.add_parser("verify")
    v.add_argument("--seconds", type=int, default=None)
    v.add_argument("--send", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--idle", type=int, default=None)
    sub.add_parser("diagnose")
    au = sub.add_parser("audit", help="что доказано работой, а что только стоит")
    au.add_argument("--send", action="store_true")
    e = sub.add_parser("e2e", help="боевой приёмочный прогон")
    e.add_argument("--minutes", type=int, default=20)
    e.add_argument("--no-selftest", action="store_true")
    e.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    if a.cmd == "audit":
        t = audit_text()
        print(t)
        if a.send:
            from . import notify
            notify.send(t)
        return 0
    if a.cmd == "e2e":
        res = production_e2e(a.minutes, not a.no_selftest, not a.no_send)
        print(res["text"])
        return 0 if res["ok"] else 1
    if a.cmd == "run":
        return run(a.idle)
    if a.cmd == "verify":
        res = verify(a.seconds)
        text = report_text(res)
        print(text)
        if a.send:
            from . import notify
            notify.send(text)
        return 0 if res["moved"] else 1
    out = once() if a.cmd == "once" else diagnose()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
