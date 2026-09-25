# -*- coding: utf-8 -*-
"""Сторож владельца: доказательство результата, а не активности.

Владелец закрывает ноутбук. С этого момента доказательством работы не
считается ничего из внутренней жизни системы — ни занятые воркеры, ни
принятые наряды, ни зелёные тесты. Считается одно: изменилось ли состояние
продукта у клиента.

Отсюда главное правило модуля: восемь занятых воркеров и сто закрытых нарядов
при нулевой прибавке у клиентов — это КРАСНЫЙ, а не зелёный.

    venv/bin/python3 -m app.ext_api.watchdog once
    venv/bin/python3 -m app.ext_api.watchdog run
"""
import io, json, os, subprocess, time

from . import db
from app.services.exception_observability import observe_suppressed

HEARTBEAT = "/tmp/.boris_watchdog_beat"
STATE = "/tmp/.boris_watchdog_state"
NOTIFY_STATE = "/tmp/.boris_watchdog_notify_state"
ERROR_NOTIFY_STATE = "/tmp/.boris_watchdog_error_notify_state"
REGISTRY_RECOVERY_STATE = "/tmp/.boris_watchdog_registry_recovery_state"
COVERAGE_RECOVERY_STATE = "/tmp/.boris_watchdog_coverage_recovery_state"
PERIOD_MIN = int(os.environ.get("BORIS_WATCHDOG_PERIOD_MIN", "30"))
REGISTRY_RECOVERY_COOLDOWN_SEC = int(
    os.environ.get("BORIS_WATCHDOG_REGISTRY_RECOVERY_COOLDOWN_SEC", "3600")
)
COVERAGE_RECOVERY_COOLDOWN_SEC = int(
    os.environ.get("BORIS_WATCHDOG_COVERAGE_RECOVERY_COOLDOWN_SEC", "3600")
)
STALL_SLA_MIN = int(os.environ.get("BORIS_STALL_SLA_MIN", "60"))
FRONTEND_RUNTIME_TIMER = "boris-frontend-retention.timer"
WATCHDOG_CHECK_TIMER = "boris-watchdog-check.timer"
NAMES = {
    "CLIENT_ANTON": "Антон",
    "CLIENT_PLANETA_MEBELI": "Планета",
    "CLIENT_VASILY": "Василий",
    "CLIENT_ANDREY_PENZA": "Андрей",
    "CLIENT_BYTOVKI_REPORTS": "Бытовки — ежедневные отчёты",
    "DEV_CALLS_REPORT_FIX": "Бытовки — сводка звонков",
    "CLIENT_CABINET": "Клиентский кабинет",
}
# Keep queue semantics aligned with queuefix/a2a. running and review_requested
# are live orders too; omitting them caused false duplicate/coverage signals.
RUNNABLE = ("queued", "running", "review_requested", "returned", "blocked_human", "blocked_infra")


def _state(new=None):
    if new is not None:
        try:
            with io.open(STATE, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(new, ensure_ascii=False))
        except OSError:
            pass
        return new
    try:
        with io.open(STATE, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except Exception:
        return {}


def beat():
    try:
        with io.open(HEARTBEAT, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass


def _notify_signature(rep):
    """Only material owner-facing state belongs in the Telegram signature.

    Elapsed minutes and other counters intentionally stay out: otherwise the same
    unchanged problem would become a "new" report every watchdog interval.
    """
    owner_blockers=sorted(c.get("key") for c in rep.get("clients",[]) if c.get("blocker"))
    payload={
        "system": rep.get("system"),
        "false_green": bool(rep.get("false_green")),
        "stalled": sorted(rep.get("stalled") or []),
        "queue_duplicates": int((rep.get("queue") or {}).get("duplicates") or 0),
        "queue_anomaly": bool((rep.get("queue") or {}).get("anomaly")),
        "owner_blockers": owner_blockers,
        "coverage_ok": bool(rep.get("coverage_ok",True)),
    }
    # Preserve the historical healthy signature so deploying this guard does not
    # itself create a one-off owner notification. Add evidence only for a real,
    # unrecovered infrastructure problem.
    if not bool((rep.get("frontend_runtime_supervision") or {}).get("ok", True)):
        payload["frontend_runtime_timer_ok"] = False
    if not bool((rep.get("watchdog_check_supervision") or {}).get("ok", True)):
        payload["watchdog_check_timer_ok"] = False
    if not rep.get("business_registry_ok", True):
        payload["business_registry_ok"] = False
        payload["business_registry_fault"] = _registry_fault_signature(
            rep.get("business_registry") or {}
        )
    return json.dumps(payload,ensure_ascii=False,sort_keys=True)


def _notify_changed(rep):
    sig=_notify_signature(rep)
    try:
        with io.open(NOTIFY_STATE,encoding="utf-8") as fh:
            prev=fh.read().strip()
    except Exception:
        prev=""
    if sig==prev:
        return False
    try:
        with io.open(NOTIFY_STATE,"w",encoding="utf-8") as fh:
            fh.write(sig)
    except OSError:
        pass
    return True


def _runtime_error_should_notify(exc, cooldown_sec=21600):
    """Не спамить владельца одинаковым падением цикла; детали остаются в journal."""
    sig = type(exc).__name__ + ":" + str(exc)[:180]
    now = int(time.time())
    prev_sig, prev_at = "", 0
    try:
        with io.open(ERROR_NOTIFY_STATE, encoding="utf-8") as fh:
            saved = json.loads(fh.read() or "{}")
            prev_sig, prev_at = saved.get("sig") or "", int(saved.get("at") or 0)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=129)
    if sig == prev_sig and now - prev_at < int(cooldown_sec):
        return False
    try:
        with io.open(ERROR_NOTIFY_STATE, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"sig": sig, "at": now}, ensure_ascii=False))
    except OSError:
        pass
    return True


def _registry_fault_signature(health):
    health = health or {}
    payload = {
        "missing_contract": sorted(health.get("missing_contract") or []),
        "extra_contract": sorted(health.get("extra_contract") or []),
        "missing_spec": sorted(health.get("missing_spec") or []),
        "incomplete_contract": health.get("incomplete_contract") or {},
        "incomplete_spec": health.get("incomplete_spec") or {},
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _registry_recovery_allowed(health, cooldown_sec=None):
    """Bound repeated recovery attempts for the same persistent registry fault."""
    if (health or {}).get("ok", True):
        try:
            os.unlink(REGISTRY_RECOVERY_STATE)
        except OSError:
            pass
        return True

    cooldown = int(
        REGISTRY_RECOVERY_COOLDOWN_SEC
        if cooldown_sec is None else cooldown_sec
    )
    sig = _registry_fault_signature(health)
    now = int(time.time())
    saved = {}
    try:
        with io.open(REGISTRY_RECOVERY_STATE, encoding="utf-8") as fh:
            saved = json.loads(fh.read() or "{}")
    except Exception:
        saved = {}
    if saved.get("sig") == sig and now - int(saved.get("at") or 0) < cooldown:
        return False
    try:
        with io.open(REGISTRY_RECOVERY_STATE, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"sig": sig, "at": now}, ensure_ascii=False))
    except OSError:
        pass
    return True


def _coverage_fault_signature(coverage):
    coverage = coverage or {}
    rows = coverage.get("rows") or []
    missing = sorted(
        str(row.get("req") or "")
        for row in rows
        if row.get("status") == "NOT_CREATED" and row.get("req")
    )
    counts = coverage.get("counts") or {}
    return json.dumps({
        "missing": missing,
        "mapped": counts.get("mapped"),
        "total": counts.get("total"),
    }, ensure_ascii=False, sort_keys=True)


def _coverage_recovery_allowed(coverage, healthy=False, cooldown_sec=None):
    """Bound repeated master.ensure_coverage() attempts for the same gap."""
    if healthy:
        try:
            os.unlink(COVERAGE_RECOVERY_STATE)
        except OSError:
            pass
        return True
    cooldown = int(
        COVERAGE_RECOVERY_COOLDOWN_SEC
        if cooldown_sec is None else cooldown_sec
    )
    sig = _coverage_fault_signature(coverage)
    now = int(time.time())
    saved = {}
    try:
        with io.open(COVERAGE_RECOVERY_STATE, encoding="utf-8") as fh:
            saved = json.loads(fh.read() or "{}")
    except Exception:
        saved = {}
    if saved.get("sig") == sig and now - int(saved.get("at") or 0) < cooldown:
        return False
    try:
        with io.open(COVERAGE_RECOVERY_STATE, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"sig": sig, "at": now}, ensure_ascii=False))
    except OSError:
        pass
    return True


def missed_heartbeat(limit_min=None):
    """Молчание сторожа — это не покой, а отдельная авария."""
    # Ноль — это порог, а не «не задано». Проверка «or» считала его
    # отсутствием и подставляла час: сторож выглядел живым всегда.
    limit = int(PERIOD_MIN * 2 if limit_min is None else limit_min)
    try:
        with io.open(HEARTBEAT, encoding="utf-8") as fh:
            last = int(fh.read().strip())
    except Exception:
        return True, None
    age = (time.time() - last) / 60.0
    return age > limit, int(age)


def frontend_runtime_supervision(timer=FRONTEND_RUNTIME_TIMER):
    """Keep the frontend runtime-watch timer alive without creating a second supervisor.

    The watchdog service runs as root in production. It verifies both runtime state
    (active) and reboot persistence (enabled), self-heals each independently, then
    verifies again. It returns structured evidence instead of raising so a timer
    problem cannot kill the whole owner-watchdog loop.
    """
    def ctl(*args):
        try:
            return subprocess.run(
                ["systemctl", *args],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:
            return type("_FailedCtl", (), {
                "returncode": 255,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
            })()

    def state(action):
        proc = ctl(action, timer)
        return proc.returncode == 0, (proc.stdout or proc.stderr or "").strip()

    active_before, active_detail_before = state("is-active")
    enabled_before, enabled_detail_before = state("is-enabled")
    actions = []
    errors = []

    if not enabled_before:
        proc = ctl("enable", timer)
        if proc.returncode == 0:
            actions.append("enabled")
        else:
            errors.append("enable: " + (proc.stderr or proc.stdout or str(proc.returncode)).strip())

    if not active_before:
        proc = ctl("start", timer)
        if proc.returncode == 0:
            actions.append("started")
        else:
            errors.append("start: " + (proc.stderr or proc.stdout or str(proc.returncode)).strip())

    active_after, active_detail_after = state("is-active")
    enabled_after, enabled_detail_after = state("is-enabled")
    ok = bool(active_after and enabled_after)
    return {
        "timer": timer,
        "ok": ok,
        "healed": bool(actions) and ok,
        "actions": actions,
        "errors": errors,
        "active_before": active_before,
        "enabled_before": enabled_before,
        "active_after": active_after,
        "enabled_after": enabled_after,
        "active_detail_before": active_detail_before,
        "enabled_detail_before": enabled_detail_before,
        "active_detail_after": active_detail_after,
        "enabled_detail_after": enabled_detail_after,
    }


def recover_owner_watchdog(service="boris-watchdog.service", wait_seconds=100):
    """Restart a silent owner-watchdog and prove that heartbeat resumed.

    The external check timer calls this only after missed_heartbeat() is true.
    Restart is non-blocking so a hung process cannot hang the checker itself;
    verification waits for both a fresh heartbeat and an active systemd unit.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "restart", "--no-block", service],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "service": service,
            "error": f"{type(exc).__name__}: {exc}",
            "heartbeat_age_min": None,
        }

    if proc.returncode != 0:
        return {
            "ok": False,
            "service": service,
            "error": (proc.stderr or proc.stdout or str(proc.returncode)).strip(),
            "heartbeat_age_min": None,
        }

    deadline = time.monotonic() + max(1, int(wait_seconds))
    last_age = None
    while time.monotonic() < deadline:
        missed, last_age = missed_heartbeat(limit_min=1)
        active = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if not missed and active.returncode == 0:
            return {
                "ok": True,
                "service": service,
                "heartbeat_age_min": last_age,
                "active": True,
            }
        time.sleep(1)

    return {
        "ok": False,
        "service": service,
        "error": "heartbeat_did_not_resume",
        "heartbeat_age_min": last_age,
    }


def _apply_infra_guards(rep, acted):
    """Apply infrastructure supervision to the final post-recovery report.

    This must run after business recovery because snapshot_report() may be called
    again during that flow. Running here prevents a later report replacement from
    erasing an unrecovered infrastructure failure.
    """
    guards = (
        (
            "frontend_runtime_supervision",
            FRONTEND_RUNTIME_TIMER,
            "frontend runtime timer",
            "фоновый контроль мобильного интерфейса и запуска рекламных кампаний",
        ),
        (
            "watchdog_check_supervision",
            WATCHDOG_CHECK_TIMER,
            "watchdog dead-hand timer",
            "внешний контроль heartbeat основного сторожа",
        ),
    )
    for key, timer, label, owner_text in guards:
        guard = frontend_runtime_supervision(timer)
        rep[key] = guard
        if guard.get("healed"):
            acted.append(
                f"{label} восстановлен: "
                + ",".join(guard.get("actions") or [])
            )
        elif not guard.get("ok"):
            rep["system"] = "RED"
            acted.append(f"{label} не восстановлен")
            rep["text"] += (
                "\n\n🚨 BORIS не смог автоматически восстановить "
                + owner_text
                + ". Автовосстановление уже выполнено насколько возможно; "
                  "требуется проверка production-инфраструктуры."
            )
    return rep


def clients():
    """Клиенты по конкретной бизнес-цели и production-артефакту.

    Никаких абстрактных «результатов»: источник истины тот же, что у
    business_gate — ожидаемый финал, фактическое количество, прогресс и
    конкретное состояние следующего шага.
    """
    from . import report, clients as _clients
    truth = report.business_truth(2, fresh=True)
    rows = {r.get("key"): r for r in truth.get("clients", [])}
    out = []
    # BUSINESS_GOAL is the source of truth. NAMES only improves presentation;
    # adding a new client/business goal must automatically put it under
    # watchdog DETECT/RECOVER without a second manual registry edit.
    for key, goal_text in _clients.BUSINESS_GOAL.items():
        src = rows.get(key) or {}
        name = NAMES.get(key) or src.get("expected_final") or str(goal_text) or "Клиентская цель"
        cur = src.get("current") if src.get("measurable") else None
        delta = int(src.get("delta") or 0)
        state = src.get("state") or "NOT_MEASURABLE"
        stalled = state in ("STALLED", "DEAD_WORKER", "NO_OPEN_ORDER",
                            "FAILED_VALIDATION", "BLOCKED")
        row = {
            "key": key, "name": name,
            "goal": src.get("goal") or "",
            "expected_final": src.get("expected_final") or "",
            "current": cur, "delta": delta,
            "progress": src.get("progress"), "expected_count": src.get("expected_count"),
            "requires_feed": src.get("requires_feed"), "feed_count": src.get("feed_count"),
            "feed_ok": src.get("feed_ok"), "show_live": src.get("show_live"),
            "live_required": src.get("live_required"), "live_count": src.get("live_count"),
            "live_ok": src.get("live_ok"),
            "overall_done": src.get("overall_done"),
            "unit": src.get("unit"),
            "state": state, "proof": src.get("proof") or "",
            "where": src.get("where"),
            "moving": delta > 0, "stalled": stalled,
            "order": src.get("order_id"), "status": src.get("order_status"),
            "phase": src.get("phase"), "worker": src.get("worker"),
            "blocker": (src.get("proof") if state == "BLOCKED" else None),
        }
        out.append(row)
    return out


def master():
    """MASTER-задача целиком: её нельзя потерять за инфраструктурными P0."""
    try:
        from . import master as _m
        keys = [it[0] for it in _m.BACKLOG]
    except Exception as e:
        return {"total": 0, "error": "%s: %s" % (type(e).__name__, e)}
    from . import a2a
    counts = {"DONE": 0, "IN_PROGRESS": 0, "QUEUED": 0, "BLOCKED": 0,
              "NOT_STARTED": 0}
    for key in keys:
        j = db.one("""SELECT id, status FROM ext_dev_jobs WHERE title LIKE :p
                       ORDER BY id DESC LIMIT 1""", p=key + "%")
        if not j:
            counts["NOT_STARTED"] += 1
            continue
        if j["status"] == "completed":
            counts["DONE"] += 1
            continue
        o = db.one("""SELECT status FROM ext_a2a_orders WHERE dev_job_id = :i
                       AND status = ANY(:st) ORDER BY id DESC LIMIT 1""",
                   i=j["id"], st=list(a2a.OPEN_STATES) + list(RUNNABLE))
        if not o:
            counts["NOT_STARTED"] += 1
        elif str(o["status"]).startswith("blocked"):
            counts["BLOCKED"] += 1
        elif o["status"] in ("running", "review_requested"):
            counts["IN_PROGRESS"] += 1
        else:
            counts["QUEUED"] += 1
    counts["total"] = len(keys)
    return counts


def queue_health():
    """Инварианты после инцидента с массовой разблокировкой."""
    from . import a2a
    run = list(a2a.OPEN_STATES) + list(RUNNABLE)
    dupes = db.rows("""SELECT dev_job_id, COUNT(*) AS n FROM ext_a2a_orders
                        WHERE status = ANY(:st) GROUP BY dev_job_id
                       HAVING COUNT(*) > 1""", st=run)
    q = (db.one("SELECT COUNT(*) AS n FROM ext_a2a_orders WHERE status='queued'")
         or {}).get("n") or 0
    prev = (_state() or {}).get("queue", q)
    return {"queued": q, "growth": q - prev, "duplicates": len(dupes),
            "anomaly": (q - prev) > max(200, prev * 2)}


def snapshot_report():
    """Один отчёт владельцу: только то, что меняет его картину мира."""
    from . import factory, clients as _clients
    cl = clients()
    m = master()
    business_registry = _clients.business_registry_health()
    # Покрытие требований проверяется каждым проходом: требование без задачи
    # теряется молча, и заметить это можно только сверкой.
    try:
        from . import master as _m
        coverage_info = _m.coverage()
        c = coverage_info["counts"]
        cov = "%d/%d%s" % (c["mapped"], c["total"],
                           "" if c["pass"] else " — RED")
        cov_ok = c["pass"]
    except Exception as e:
        coverage_info = {
            "counts": {"mapped": None, "total": None, "pass": False},
            "rows": [],
            "error": "%s: %s" % (type(e).__name__, e),
        }
        cov, cov_ok = "не проверено: %s" % type(e).__name__, False
    qh = queue_health()
    w = factory.workers()
    moving = sum(1 for c in cl if c["moving"])
    delta = sum(c["delta"] or 0 for c in cl if (c["delta"] or 0) > 0)
    stalled = [c for c in cl if c["stalled"]]
    # Внутренняя активность без прироста является false-green только когда
    # хотя бы одна контролируемая клиентская цель ЕЩЁ НЕ закрыта. Если все
    # цели подтверждены, работа фоновых исполнителей не превращает GREEN в RED.
    unfinished = [c for c in cl if not c.get("overall_done")]
    false_green = bool(unfinished) and (w["client_busy"] > 0 or w["active"] > 0) and delta == 0
    system = (
        "RED"
        if (
            false_green
            or qh["anomaly"]
            or qh["duplicates"]
            or not cov_ok
            or not business_registry.get("ok")
        )
        else ("YELLOW" if (stalled or (unfinished and delta == 0)) else "GREEN")
    )

    # Владелец получает бизнес-смысл, а не внутренние имена метрик/воркеров.
    status_ru = {"GREEN": "✅ Всё работает", "YELLOW": "⚠️ Есть задержки",
                 "RED": "🚨 Требуется автоматическое исправление"}
    lines = ["BORIS — контроль работы", "", status_ru[system]]
    if delta > 0:
        lines.append("За последние 2 часа у клиентских задач прибавилось объектов: +%d." % delta)
    else:
        lines.append("Ниже — фактическое состояние каждой клиентской цели; без абстрактного слова «результат».")
    if not cov_ok:
        lines.append("Контроль неполный — BORIS автоматически восстанавливает внутренние проверки.")
    if qh.get("duplicates"):
        lines.append("BORIS обнаружил %d конфликтующих задач в очереди и автоматически оставит по одной актуальной." % int(qh["duplicates"]))
    if qh.get("anomaly"):
        lines.append("Очередь задач резко выросла. Массовые изменения остановлены до автоматической стабилизации.")
    if not business_registry.get("ok"):
        lines.append(
            "Внутренний контроль бизнес-целей неполон: BORIS обнаружил цель без "
            "полного контракта результата или без безопасного сценария восстановления. "
            "Автоматическое восстановление внутреннего контура запущено."
        )
    lines.append("")
    state_ru = {
        "NO_OPEN_ORDER": "следующий рабочий шаг не поставлен — BORIS восстанавливает его автоматически",
        "STALLED": "нет нового изменения и нет свежего события — BORIS перезапускает работу",
        "DEAD_WORKER": "исполнитель перестал подтверждать работу — BORIS переназначает задачу",
        "FAILED_VALIDATION": "последняя проверка не пройдена — BORIS исправляет и проверяет повторно",
        "BLOCKED": "есть технический блокер",
        "WAITING_REVIEW": "изменение готово и проходит автоматическую приёмку",
        "WAITING_DEPLOY": "изменение готово и ожидает безопасной выкладки",
        "WORKING_LONG_TASK": "работа продолжается; есть свежее подтверждение выполнения",
        "MOVING": "за последние 2 часа есть фактическое изменение",
        "DONE": "цель достигнута и подтверждена фактическими данными",
    }
    for c in cl:
        goal = (c.get("expected_final") or c.get("goal") or "цель не описана").strip()
        progress = c.get("progress")
        target = c.get("expected_count")
        if c.get("current") is None:
            fact = "не удалось измерить"
        elif target:
            fact = "%s из %s (%s%%)" % (c.get("current"), target,
                                         progress if progress is not None else "?")
        else:
            fact = str(c.get("current"))
        done = bool(c.get("overall_done"))
        lines.append("%s %s" % ("✅" if (c.get("moving") or done) else "🔧", c["name"]))
        lines.append("Цель: %s." % goal.rstrip("."))
        lines.append("Факт сейчас: %s." % fact)
        if c.get("requires_feed"):
            if c.get("feed_ok"):
                lines.append("Фид: подтверждён · %s карточек." % (
                    c.get("feed_count") if c.get("feed_count") is not None else "количество не измерено"))
            else:
                lines.append("Фид: %s карточек из необходимых %s — ещё не подтверждён." % (
                    c.get("feed_count") if c.get("feed_count") is not None else "не измерено",
                    c.get("expected_count") or "?"))
        if c.get("show_live"):
            lines.append("Avito: подтверждено %s опубликованных объявлений%s." % (
                c.get("live_count") if c.get("live_count") is not None else "не измерено",
                "" if c.get("live_required") else " — это отдельный внешний этап, не часть готовности внутреннего фида"))
        lines.append("Сейчас: %s." % state_ru.get(c.get("state"), "BORIS проверяет следующий шаг"))
        if c.get("where"):
            lines.append("Где проверить: %s." % c["where"])
        lines.append("")
    owner = [c for c in cl if c["blocker"]]
    lines.append("")
    if owner:
        lines.append("Нужно ваше действие: да — %d задач(и)." % len(owner))
    else:
        lines.append("От вас действий не требуется.")
    if false_green:
        lines.append("BORIS обнаружил расхождение статуса и результата и уже запустил исправление.")

    _state({"clients": {c["key"]: {"current": c["current"],
                                   "progress": c.get("progress"),
                                   "state": c.get("state")} for c in cl},
            "queue": qh["queued"], "at": int(time.time())})
    return {
        "text": "\n".join(lines),
        "system": system,
        "false_green": false_green,
        "stalled": [c["key"] for c in stalled],
        "clients": cl,
        "master": m,
        "queue": qh,
        "delta": delta,
        "coverage_ok": cov_ok,
        "coverage": coverage_info,
        "business_registry": business_registry,
        "business_registry_ok": bool(business_registry.get("ok")),
    }


def once(send=True):
    """Проход сторожа: посмотреть, починить что можно, доложить."""
    beat()
    rep = snapshot_report()
    acted = []

    # Восстановление покрытия — действие, поэтому живёт здесь, а не в
    # формировании отчёта: отчёт обязан быть безвредным. Один и тот же
    # неисправимый gap нельзя лечить на каждом тике create->cancel циклом.
    coverage_fault = not rep.get("coverage_ok", True)
    if coverage_fault:
        coverage_retry_allowed = _coverage_recovery_allowed(
            rep.get("coverage") or {}
        )
    else:
        _coverage_recovery_allowed({}, healthy=True)
        coverage_retry_allowed = True

    if coverage_fault and coverage_retry_allowed:
        try:
            from . import master as _m
            r = _m.ensure_coverage()
            acted.append(
                "восстановление покрытия запущено: задач %d, нарядов %d"
                % (
                    len(r.get("created") or []),
                    len(r.get("orders_created") or []),
                )
            )
            # Report the post-heal truth, not the stale pre-heal snapshot.
            rep = snapshot_report()
            if rep.get("coverage_ok", True):
                _coverage_recovery_allowed({}, healthy=True)
                acted.append("покрытие требований восстановлено и проверено")
            else:
                acted.append(
                    "покрытие остаётся неполным; повтор того же восстановления "
                    "ограничен безопасным интервалом"
                )
        except Exception as e:
            acted.append("покрытие не восстановлено: %s" % type(e).__name__)
    elif coverage_fault:
        acted.append(
            "повторное восстановление того же покрытия отложено до следующего "
            "безопасного окна"
        )
    # Duplicate active orders are an internal queue defect, not an owner task.
    # Repair automatically, but only inside a small hard cap and only when the
    # queue itself is not showing explosive growth. queuefix snapshots every
    # changed row before mutation and is rollbackable.
    if rep["queue"].get("duplicates") and not rep["queue"].get("anomaly"):
        try:
            from . import queuefix
            fixed=queuefix.apply(max_rows=100)
            acted.append("дубли очереди убраны: %d" % int(fixed.get("superseded") or 0))
            rep=snapshot_report()
        except Exception as e:
            acted.append("дубли очереди не убраны безопасно: %s" % type(e).__name__)
    registry_fault = not rep.get("business_registry_ok", True)
    if registry_fault:
        registry_retry_allowed = _registry_recovery_allowed(
            rep.get("business_registry") or {}
        )
    else:
        _registry_recovery_allowed({"ok": True})
        registry_retry_allowed = True

    should_recover = bool(
        rep["stalled"]
        or rep["false_green"]
        or (registry_fault and registry_retry_allowed)
    )
    if should_recover:
        try:
            from . import recover, factory
            factory.kick_review()
            recover.once()
            acted.append("восстановление запущено")
            if registry_fault:
                # DETECT -> RECOVER is not enough: verify the post-heal truth.
                post = snapshot_report()
                if post.get("business_registry_ok", True):
                    _registry_recovery_allowed({"ok": True})
                    acted.append("контур бизнес-целей восстановлен и проверен")
                else:
                    acted.append(
                        "контур бизнес-целей автоматически не восстановлен; "
                        "система остаётся в аварийном состоянии"
                    )
                rep = post
        except Exception as e:
            acted.append("восстановление не удалось: %s: %s"
                         % (type(e).__name__, e))
    elif registry_fault:
        acted.append(
            "повторное восстановление того же внутреннего дефекта отложено "
            "до следующего безопасного окна"
        )
    if rep["queue"]["anomaly"]:
        # Аномальный рост очереди — повод остановить любые массовые правки
        # до вмешательства, а не «посмотреть потом».
        acted.append("аномальный рост очереди: массовые правки остановлены")
        os.environ["BORIS_MAX_AFFECTED_ROWS"] = "0"

    # Infrastructure supervision belongs to the final report state because
    # business recovery above may replace rep with a fresh snapshot.
    rep = _apply_infra_guards(rep, acted)

    if send and _notify_changed(rep):
        try:
            from . import notify
            notify.send(rep["text"])
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=753)
    rep["acted"] = acted
    return rep


def run(period_min=None):
    p = int(period_min or PERIOD_MIN)
    while True:
        try:
            once()
        except Exception as e:
            # Техническая причина нужна в журнале, а не владельцу в Telegram.
            print("WATCHDOG_LOOP_ERROR %s: %s" % (type(e).__name__, e), flush=True)
            if _runtime_error_should_notify(e):
                try:
                    from . import notify
                    notify.send("🚨 BORIS — контроль работы временно дал сбой. "
                                "Служба продолжает цикл и автоматически восстановит проверки. "
                                "От вас действий не требуется.")
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=773)
        time.sleep(p * 60)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="watchdog")
    ap.add_argument("cmd", choices=["once", "run", "beat", "check"])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.cmd == "once":
        print(once(send=not a.quiet)["text"])
    elif a.cmd == "check":
        missed, age = missed_heartbeat()
        if missed:
            print("WATCHDOG_HEARTBEAT_MISSED")
            recovery = recover_owner_watchdog()
            if recovery.get("ok"):
                print(
                    "WATCHDOG_HEARTBEAT_RECOVERED age=%s"
                    % recovery.get("heartbeat_age_min")
                )
            else:
                print(
                    "WATCHDOG_HEARTBEAT_UNRECOVERED error=%s"
                    % recovery.get("error", "unknown")
                )
                if _runtime_error_should_notify(
                    RuntimeError("watchdog_heartbeat_unrecovered"),
                    cooldown_sec=21600,
                ):
                    try:
                        from . import notify
                        notify.send(
                            "🚨 BORIS — основной сторож перестал обновляться и "
                            "не восстановился после автоматического перезапуска. "
                            "Нужна проверка production-инфраструктуры."
                        )
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=812)
                return 2
        else:
            print("heartbeat в норме (%s мин назад)" % age)
    elif a.cmd == "beat":
        beat()
    else:
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
