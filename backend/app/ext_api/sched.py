# -*- coding: utf-8 -*-
"""Адаптивный планировщик параллельной разработки.

Второго оркестратора здесь нет: это довесок к существующему диспетчеру
dev.schedule(). Он отвечает на один вопрос — сколько задач можно вести
одновременно ПРЯМО СЕЙЧАС на этом железе, и какие из них тяжёлые.

Числа не выдуманы: они считаются из фактических CPU, RAM, load average и
swap, с обязательным резервом под production. Если сервер поджало —
планировщик сам сужает параллелизм, а не роняет боевой BORIS.

Что здесь есть:
  facts()             — измерения среды
  limits()            — сколько воркеров и тяжёлых слотов допустимо сейчас
  weight_of()         — вес задачи: light / medium / heavy / exclusive
  effective_priority()— приоритет со старением, чтобы P2/P3 не голодали
  admission()         — решение по конкретной задаче: пускать или ждать
  heartbeat_sweep()   — подобрать зависшие задачи и вернуть их в очередь
  usage()             — расход моделей за период, для поиска аномалий
"""
import io, json, os, re, time
from app.services.exception_observability import observe_suppressed

# ---------------------------------------------------------------- измерения

def _int(s, default=0):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def _meminfo():
    out = {}
    try:
        with io.open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                out[k.strip()] = _int(v.strip().split(" ")[0]) // 1024  # МБ
    except OSError:
        pass
    return out


def _loadavg():
    try:
        with io.open("/proc/loadavg", encoding="utf-8") as fh:
            parts = fh.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return 0.0, 0.0, 0.0


def _cpu_counters():
    """Read aggregate CPU counters from /proc/stat."""
    try:
        with io.open("/proc/stat", encoding="utf-8") as fh:
            parts = fh.readline().split()
        if not parts or parts[0] != "cpu":
            return None
        values = [float(x) for x in parts[1:9]]
        while len(values) < 8:
            values.append(0.0)
        return values
    except (OSError, ValueError, IndexError):
        return None


def _cpu_activity(sample_seconds=0.15):
    """Short real CPU sample so decaying loadavg cannot freeze dev by itself.

    Linux load average can stay above the CPU count for minutes after a burst,
    even when CPUs are already mostly idle. We still keep loadavg as the
    conservative pressure signal, but this sample gives a strictly bounded
    one-worker recovery floor when there is demonstrable headroom.
    """
    a = _cpu_counters()
    if not a:
        return {}
    time.sleep(max(0.05, float(sample_seconds)))
    b = _cpu_counters()
    if not b:
        return {}
    d = [max(0.0, b[i] - a[i]) for i in range(min(len(a), len(b)))]
    total = sum(d)
    if total <= 0:
        return {}
    user, nice, system, idle, iowait, irq, softirq, steal = (d + [0.0] * 8)[:8]
    busy = user + nice + system + irq + softirq
    return {
        "cpu_busy_pct": round(100.0 * busy / total, 1),
        "cpu_idle_pct": round(100.0 * idle / total, 1),
        "cpu_iowait_pct": round(100.0 * iowait / total, 1),
        "cpu_steal_pct": round(100.0 * steal / total, 1),
    }


def facts():
    m = _meminfo()
    l1, l5, l15 = _loadavg()
    cpu = os.cpu_count() or 1
    total = m.get("MemTotal", 0)
    avail = m.get("MemAvailable", m.get("MemFree", 0))
    swap_total = m.get("SwapTotal", 0)
    swap_used = swap_total - m.get("SwapFree", 0)
    out = {"cpu": cpu, "ram_total_mb": total, "ram_available_mb": avail,
           "swap_total_mb": swap_total, "swap_used_mb": swap_used,
           "load1": l1, "load5": l5, "load15": l15,
           "load_per_cpu": round(l1 / cpu, 2) if cpu else 0.0,
           "measured_at": int(time.time())}
    out.update(_cpu_activity())
    return out


# ------------------------------------------------------------------- пороги
# Резерв под боевой BORIS. Dev-воркеры живут на том, что осталось, а не
# наоборот: production приоритетнее любой задачи из очереди.
RESERVE_RAM_MB = _int(os.environ.get("BORIS_RESERVE_RAM_MB"), 1800)
RESERVE_LOAD_PER_CPU = float(os.environ.get("BORIS_RESERVE_LOAD") or 0.85)
# Review is a single light, mostly provider/network-bound worker. It may drain
# already finished code a little above the executor admission load threshold,
# but never during severe CPU/iowait/steal pressure.
REVIEW_LOAD_PER_CPU_MAX = float(os.environ.get("BORIS_REVIEW_LOAD_MAX") or 1.25)

# Сколько памяти реально просит задача каждого веса. Значения — стартовые,
# нагрузочный тест на боевом сервере их уточняет и кладёт в окружение.
WEIGHT_RAM_MB = {
    "light": _int(os.environ.get("BORIS_RAM_LIGHT"), 250),
    "medium": _int(os.environ.get("BORIS_RAM_MEDIUM"), 700),
    "heavy": _int(os.environ.get("BORIS_RAM_HEAVY"), 1900),
    "exclusive": _int(os.environ.get("BORIS_RAM_HEAVY"), 1900),
}
WEIGHT_ORDER = ("light", "medium", "heavy", "exclusive")

# Фазы и операции по весу. Сборка фронта и полная регрессия — тяжёлые,
# рестарт службы и миграция схемы — исключительные: строго по одной.
PHASE_WEIGHT = {"PLANNING": "light", "REVIEW": "light", "BACKEND": "medium",
                "FRONTEND": "heavy", "QA": "heavy", "DEPLOY": "exclusive"}
OP_WEIGHT = {"repo_read": "light", "repo_tree": "light", "shell_diag": "light",
             "db_query": "light", "backend_tests": "medium",
             "frontend_typecheck": "medium", "frontend_build": "heavy",
             "full_regression": "heavy", "browser": "heavy",
             "service_restart": "exclusive", "db_migration": "exclusive"}
EXCLUSIVE_LOCKS = ("boris-backend-restart", "boris-frontend-restart",
                   "db-schema", "deploy", "global-config")

P0_PRIORITY = _int(os.environ.get("BORIS_P0_PRIORITY"), 90)
AGING_STEP_MIN = _int(os.environ.get("BORIS_AGING_STEP_MIN"), 30)
AGING_CAP = _int(os.environ.get("BORIS_AGING_CAP"), 25)
STALE_MINUTES = _int(os.environ.get("BORIS_STALE_MINUTES"), 45)

# Порог клиентских задач: всё, что от него и выше, идёт раньше
# любой продуктовой работы и любого инцидента.
CLIENT_FLOOR = _int(os.environ.get("BORIS_CLIENT_FLOOR"), 95)

# Сколько задач НЕ по клиентам может идти одновременно. Инфраструктура важна,
# но клиент ждёт живого результата, а не нашего благоустройства.
INFRA_SLOTS = _int(os.environ.get("BORIS_INFRA_SLOTS"), 1)


SCALE_FILE = "backend/.ext_scale.env"


def _scale_caps():
    """Потолок ступенчатого подъёма. Файл, а не переменная окружения: предел
    задаёт одна служба, а читают его все процессы, включая уже запущенные."""
    try:
        from . import repo
        path = os.path.join(repo.ROOT, SCALE_FILE)
        out = {}
        with io.open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        return out
    except Exception:
        return {}


def _env_cap(name):
    v = os.environ.get(name) or _scale_caps().get(name)
    return _int(v, 0) if v else 0


# Принудительные пределы: нужны нагрузочному тесту, который поднимает
# конкурентность ступенями, и приёмочным прогонам. В боевом режиме None —
# числа считаются от железа.
FORCE_LIMITS = None


def limits(f=None):
    """Сколько воркеров можно держать одновременно прямо сейчас.

    Считается по двум независимым ограничителям — памяти и процессору, —
    берётся меньшее. Ручной потолок из окружения только опускает планку,
    поднять её выше измеренной он не может: иначе смысл измерений теряется.
    """
    f = f or facts()
    free_for_dev = max(0, f["ram_available_mb"] - RESERVE_RAM_MB)

    by_ram = free_for_dev // max(1, WEIGHT_RAM_MB["medium"])
    cpu_budget = max(0.0, f["cpu"] * RESERVE_LOAD_PER_CPU - f["load1"])
    by_cpu = int(cpu_budget)

    # LOADAVG_DECAY_RECOVERY_FLOOR_V1: loadavg is intentionally slow to
    # decay. If a fresh /proc/stat sample proves that CPUs are mostly idle,
    # iowait/steal are low and RAM is available, keep exactly one executor
    # slot instead of freezing the whole development queue for several
    # minutes after a finished burst. This is a floor, never a scale-up.
    instant_headroom = bool(
        f.get("cpu_idle_pct") is not None
        and float(f.get("cpu_idle_pct") or 0) >= 50.0
        and float(f.get("cpu_iowait_pct") or 0) <= 15.0
        and float(f.get("cpu_steal_pct") or 0) <= 10.0
        and free_for_dev >= WEIGHT_RAM_MB["medium"]
        and not (f["swap_total_mb"] and f["swap_used_mb"] > f["swap_total_mb"] * 0.25)
    )
    if by_cpu <= 0 and by_ram > 0 and instant_headroom:
        by_cpu = 1

    # FRACTIONAL_CPU_BUDGET_RECOVERY_FLOOR_V1:
    # int(cpu_budget) used to turn a healthy positive margin such as 0.8 CPU
    # into zero executors even while pressure() correctly reported no pressure.
    # Keep exactly one bounded slot when current load is below the production
    # ceiling and fresh CPU/RAM facts show that one worker is safe.
    fractional_headroom = bool(
        by_cpu <= 0
        and by_ram > 0
        and cpu_budget > 0.0
        and float(f.get("load_per_cpu") or 0) <= RESERVE_LOAD_PER_CPU
        and float(f.get("cpu_busy_pct") or 0) <= 75.0
        and float(f.get("cpu_iowait_pct") or 0) <= 10.0
        and float(f.get("cpu_steal_pct") or 0) <= 10.0
        and free_for_dev >= WEIGHT_RAM_MB["medium"]
        and not (f["swap_total_mb"] and f["swap_used_mb"] > f["swap_total_mb"] * 0.25)
    )
    if fractional_headroom:
        by_cpu = 1

    real_cpu_pressure = bool(
        float(f.get("cpu_busy_pct") or 0) > 75.0
        or float(f.get("cpu_iowait_pct") or 0) > 10.0
        or float(f.get("cpu_steal_pct") or 0) > 10.0
    )
    executors = 0 if real_cpu_pressure else max(0, min(by_ram, by_cpu))
    heavy = max(0, min(free_for_dev // max(1, WEIGHT_RAM_MB["heavy"]),
                       max(1, f["cpu"] // 4)))
    # Один тяжёлый слот держим доступным даже под нагрузкой, иначе сборка
    # фронта не пройдёт никогда; но только если память под неё реально есть.
    if heavy == 0 and free_for_dev >= WEIGHT_RAM_MB["heavy"]:
        heavy = 1
    review_floor = bool(
        free_for_dev >= WEIGHT_RAM_MB["light"]
        and float(f.get("load_per_cpu") or 0) <= REVIEW_LOAD_PER_CPU_MAX
        and float(f.get("cpu_busy_pct") or 0) <= 90.0
        and float(f.get("cpu_iowait_pct") or 0) <= 20.0
        and float(f.get("cpu_steal_pct") or 0) <= 20.0
        and not (f["swap_total_mb"] and f["swap_used_mb"] > f["swap_total_mb"] * 0.25)
    )
    reviews = 1 if (executors or review_floor) else 0
    if os.environ.get("BORIS_REVIEW_PARALLEL") == "1" and executors >= 2:
        reviews = min(2, executors)

    caps = {"MAX_EXECUTOR_WORKERS": executors,
            "MAX_REVIEW_WORKERS": reviews,
            "MAX_HEAVY_BUILD_SLOTS": heavy,
            "MAX_RUNTIME_QA_SLOTS": min(heavy, 1) if heavy else 0,
            "MAX_EXCLUSIVE_SLOTS": 1}
    for name, env in (("MAX_EXECUTOR_WORKERS", "BORIS_MAX_EXECUTORS"),
                      ("MAX_REVIEW_WORKERS", "BORIS_MAX_REVIEWS"),
                      ("MAX_HEAVY_BUILD_SLOTS", "BORIS_MAX_HEAVY"),
                      ("MAX_RUNTIME_QA_SLOTS", "BORIS_MAX_QA")):
        cap = _env_cap(env)
        if cap:
            caps[name] = min(caps[name], cap)
    if FORCE_LIMITS:
        caps.update({k: int(v) for k, v in FORCE_LIMITS.items() if k.startswith("MAX")})
        caps["source"] = "пределы заданы принудительно (нагрузочный тест)"
        caps["facts"] = f
        caps["ram_free_for_dev_mb"] = free_for_dev
        return caps
    caps["source"] = "измерено на этой машине"
    caps["facts"] = f
    caps["ram_free_for_dev_mb"] = free_for_dev
    return caps


def pressure(f=None):
    """Сервер поджало? Тогда новых тяжёлых задач не начинаем."""
    f = f or facts()
    why = []
    if f["ram_available_mb"] - RESERVE_RAM_MB < WEIGHT_RAM_MB["medium"]:
        why.append("свободной памяти меньше резерва production")
    high_load = f["load_per_cpu"] > RESERVE_LOAD_PER_CPU
    if high_load:
        why.append("load average выше безопасного порога")
    if float(f.get("cpu_busy_pct") or 0) > 75.0:
        why.append("CPU занят выше безопасного порога")
    if float(f.get("cpu_iowait_pct") or 0) > 10.0:
        why.append("слишком высокий CPU iowait")
    if float(f.get("cpu_steal_pct") or 0) > 10.0:
        why.append("гипервизор отбирает слишком много CPU")
    if f["swap_total_mb"] and f["swap_used_mb"] > f["swap_total_mb"] * 0.25:
        why.append("система ушла в swap")
    instant_headroom = bool(
        high_load
        and f.get("cpu_idle_pct") is not None
        and float(f.get("cpu_idle_pct") or 0) >= 50.0
        and float(f.get("cpu_iowait_pct") or 0) <= 15.0
        and float(f.get("cpu_steal_pct") or 0) <= 10.0
        and f["ram_available_mb"] - RESERVE_RAM_MB >= WEIGHT_RAM_MB["medium"]
        and not (f["swap_total_mb"] and f["swap_used_mb"] > f["swap_total_mb"] * 0.25)
    )
    historical_load_only = bool(
        instant_headroom
        and why == ["load average выше безопасного порога"]
    )
    return {
        "under_pressure": bool(why),
        "reasons": why,
        "facts": f,
        "instant_headroom": instant_headroom,
        "historical_load_only": historical_load_only,
    }


# --------------------------------------------------------------------- веса

def weight_of(job):
    """Вес задачи: по её блокировкам, фазе и заявленным операциям."""
    locks = job.get("locks") or []
    for lk in locks:
        base = str(lk).split(":")[-1].strip()
        if base in EXCLUSIVE_LOCKS or str(lk).startswith("exclusive:"):
            return "exclusive"
    best = "light"
    for op in (job.get("ops") or []):
        w = OP_WEIGHT.get(str(op).strip())
        if w and WEIGHT_ORDER.index(w) > WEIGHT_ORDER.index(best):
            best = w
    ph = PHASE_WEIGHT.get((job.get("current_phase") or job.get("phase") or "").upper())
    if ph and WEIGHT_ORDER.index(ph) > WEIGHT_ORDER.index(best):
        best = ph
    return best


def effective_priority(job, now=None):
    """Приоритет со старением: висящая задача постепенно поднимается.

    Без этого поток P0/P1 навсегда затирает P2/P3 — классическое голодание.
    Потолок прибавки не даёт косметике обогнать инцидент.
    """
    base = _int(job.get("priority"), 50)
    created = job.get("queued_at") or job.get("created_at")
    ts = _ts(created)
    if not ts:
        return base
    minutes = max(0, ((now or time.time()) - ts) / 60.0)
    aged = base + min(AGING_CAP, int(minutes // max(1, AGING_STEP_MIN)))
    # Старение спасает от голодания, но не должно поднимать фоновую задачу
    # выше клиентской. Клиентские живут от CLIENT_FLOOR и выше.
    if base < CLIENT_FLOOR:
        return min(aged, CLIENT_FLOOR - 1)
    return aged


def _ts(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except (TypeError, ValueError, OSError):
            pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return 0
    import calendar
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


# ------------------------------------------------------------------ допуск

def is_client(job):
    return _int(job.get("priority"), 50) >= CLIENT_FLOOR


def new_state(caps=None, running=None, client_queue=0):
    """Счётчики занятости для серии решений admission() подряд."""
    caps = caps or limits()
    used = {"light": 0, "medium": 0, "heavy": 0, "exclusive": 0}
    total = 0
    infra = 0
    for j in (running or []):
        used[weight_of(j)] += 1
        total += 1
        if not is_client(j):
            infra += 1
    return {"caps": caps, "used": used, "total": total, "infra": infra,
            "client_queue": int(client_queue),
            "pressure": pressure(caps.get("facts"))}


def admission(job, state):
    """Пускать задачу в работу или держать в очереди — с человеческой причиной.

    Возвращает {"admit": bool, "weight": …, "reason": …}. Причина потом
    ложится в wait_reason и видна владельцу на доске, а не теряется.
    """
    caps, used = state["caps"], state["used"]
    w = weight_of(job)
    prio = _int(job.get("priority"), 50)
    is_p0 = prio >= P0_PRIORITY

    def no(reason):
        return {"admit": False, "weight": w, "reason": reason}

    if w == "exclusive":
        if used["exclusive"] >= caps["MAX_EXCLUSIVE_SLOTS"] or state["total"] > 0:
            return no("исключительная операция ждёт, пока освободится сервер")
        return {"admit": True, "weight": w, "reason": "исключительный слот свободен"}

    if not is_client(job):
        if state.get("infra", 0) >= INFRA_SLOTS:
            return no("лимит инфраструктурных задач: не больше %d одновременно, "
                      "остальная ёмкость под клиентов" % INFRA_SLOTS)
        free = caps["MAX_EXECUTOR_WORKERS"] - state["total"]
        if state.get("client_queue", 0) > 0 and free <= 1:
            return no("лимит: последний свободный слот держим под клиента, "
                      "в очереди клиентских задач %d" % state["client_queue"])

    if used["exclusive"]:
        return no("лимит: идёт исключительная операция — рестарт, деплой или "
                  "миграция схемы, остальное ждёт")

    if state["total"] >= caps["MAX_EXECUTOR_WORKERS"]:
        # Ёмкость может просесть до нуля под нагрузкой. Инцидент P0 всё равно
        # должен пройти — но строго один и только если память под него есть.
        if not (is_p0 and state["total"] == 0 and w != "heavy"
                and caps.get("ram_free_for_dev_mb", 0) >= WEIGHT_RAM_MB[w]):
            return no("занята вся безопасная ёмкость: %d из %d, лимит посчитан по "
                      "фактическим CPU, RAM и load этой машины"
                      % (state["total"], caps["MAX_EXECUTOR_WORKERS"]))

    if w == "heavy":
        if used["heavy"] >= caps["MAX_HEAVY_BUILD_SLOTS"]:
            return no("лимит тяжёлых слотов: занято %d из %d — сборки и регрессия "
                      "не запускаются пачкой"
                      % (used["heavy"], caps["MAX_HEAVY_BUILD_SLOTS"]))
        if state["pressure"]["under_pressure"]:
            return no("сервер под нагрузкой: " +
                      "; ".join(state["pressure"]["reasons"]))

    if state["pressure"]["under_pressure"] and not is_p0:
        recovery_floor = bool(
            state["pressure"].get("historical_load_only")
            and state["pressure"].get("instant_headroom")
            and state["total"] == 0
            and caps["MAX_EXECUTOR_WORKERS"] >= 1
            and w in ("light", "medium")
        )
        if not recovery_floor:
            return no("сервер под нагрузкой, пускаем только P0: " +
                      "; ".join(state["pressure"]["reasons"]))
        return {
            "admit": True,
            "weight": w,
            "reason": (
                "один безопасный recovery-слот: load average ещё не успел "
                "снизиться, но свежий CPU-срез показывает свободный ресурс"
            ),
        }

    return {"admit": True, "weight": w,
            "reason": "есть ёмкость (%d из %d)" % (state["total"] + 1,
                                                   caps["MAX_EXECUTOR_WORKERS"])}


def take(state, job, weight=None):
    """Отметить, что задача занята — для следующих решений в этом же проходе."""
    w = weight or weight_of(job)
    state["used"][w] = state["used"].get(w, 0) + 1
    state["total"] += 1
    if not is_client(job):
        state["infra"] = state.get("infra", 0) + 1
    else:
        state["client_queue"] = max(0, state.get("client_queue", 0) - 1)
    return state


# ------------------------------------------------------- зависшие и расход

def heartbeat_sweep(stale_minutes=None, apply=True):
    """Задача числится running, но давно молчит — вернуть её в очередь.

    Молчание определяем по последнему событию задачи. Блокировки при этом
    освобождаются: иначе один упавший воркер держит ресурс вечно.
    """
    from . import db, dev
    limit_min = int(stale_minutes or STALE_MINUTES)
    # Пульс задачи — это ЛЮБОЕ движение: событие задачи или движение её нарядов.
    # Если считать только события dev, работающая задача выглядит замолчавшей и
    # диспетчер начинает выдёргивать её из-под живого исполнителя.
    rows = db.rows("""SELECT j.id, j.title, j.assigned_agent, j.current_phase,
                             j.locks_json,
                             GREATEST(
                               COALESCE((SELECT MAX(created_at) FROM ext_dev_events e
                                          WHERE e.dev_job_id = j.id), j.updated_at),
                               COALESCE((SELECT MAX(o.updated_at) FROM ext_a2a_orders o
                                          WHERE o.dev_job_id = j.id), j.updated_at),
                               j.updated_at) AS last_event,
                             j.updated_at
                        FROM ext_dev_jobs j
                       WHERE j.status = 'running'""")
    now = time.time()
    stale = []
    for r in rows:
        beat = _ts(r.get("last_event")) or _ts(r.get("updated_at"))
        if not beat:
            continue
        idle_min = (now - beat) / 60.0
        if idle_min < limit_min:
            continue
        stale.append({"dev_job_id": "dev_%s" % r["id"], "title": r.get("title"),
                      "agent": r.get("assigned_agent"),
                      "phase": r.get("current_phase"),
                      "idle_minutes": int(idle_min)})
        if apply:
            dev._set(r["id"], status="queued", assigned_agent=None,
                     wait_reason="воркер замолчал на %d мин — задача возвращена "
                                 "в очередь, блокировки освобождены" % int(idle_min))
            try:
                dev.event(r["id"], r.get("current_phase") or "PLANNING",
                          "scheduler", "requeued_stalled",
                          {"idle_minutes": int(idle_min),
                           "agent": r.get("assigned_agent")})
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=542)
    return {"checked": len(rows), "stale_minutes": limit_min,
            "requeued": stale if apply else [], "found": stale}


def usage(days=7):
    """Расход моделей по дням: чтобы видеть аномалии, а не гадать."""
    from . import db
    rows = db.rows("""SELECT provider, model,
                             COUNT(*) AS calls,
                             COALESCE(SUM(prompt_tokens), 0) AS input_tokens,
                             COALESCE(SUM(completion_tokens), 0) AS output_tokens,
                             COALESCE(SUM(tool_calls), 0) AS tool_calls
                        FROM ext_a2a_runs
                       WHERE started_at > NOW() - (:d || ' days')::interval
                       GROUP BY provider, model
                       ORDER BY 5 DESC""", d=int(days))
    loops = db.rows("""SELECT id, dev_job_id, iteration, max_iterations, status
                         FROM ext_a2a_orders
                        WHERE iteration >= GREATEST(max_iterations - 1, 3)
                          AND status NOT IN ('accepted', 'cancelled')
                        ORDER BY iteration DESC LIMIT 20""")
    return {"days": int(days), "by_model": [dict(r) for r in rows],
            "runaway_candidates": [
                {"order_id": r["id"], "dev_job_id": "dev_%s" % r["dev_job_id"],
                 "iteration": "%s/%s" % (r["iteration"], r["max_iterations"]),
                 "status": r["status"]} for r in loops]}


def report():
    """Одна картинка состояния для владельца и для доски."""
    f = facts()
    caps = limits(f)
    return {"facts": f, "limits": {k: v for k, v in caps.items()
                                   if k.startswith("MAX") or k == "ram_free_for_dev_mb"},
            "pressure": pressure(f)}


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="sched")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    sw = sub.add_parser("sweep")
    sw.add_argument("--dry", action="store_true")
    sw.add_argument("--minutes", type=int, default=None)
    us = sub.add_parser("usage")
    us.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.cmd == "report":
        out = report()
    elif a.cmd == "sweep":
        out = heartbeat_sweep(a.minutes, apply=not a.dry)
    else:
        out = usage(a.days)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
