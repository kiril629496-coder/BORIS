# -*- coding: utf-8 -*-
"""Реестр агентов и контроллер выбора.

Доступность агента НЕ угадывается и НЕ имитируется: агент сам сообщает своё
состояние heartbeat-ом со своего ключа. Если он молчит дольше STALE_MINUTES,
состояние становится unknown — и контроллер считает его ненадёжным, но не
объявляет «лимит» за него.
"""
import json, os, subprocess
from . import db
from .errors import ApiError
from app.services.exception_observability import observe_suppressed

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



STATUSES = ("available", "limited", "unavailable")
STALE_MINUTES = 15

# Специализация для режима auto. Итог всё равно за контроллером.
SPECIALIZATION = {
    "code":          ["claude", "chatgpt"],
    "implementation": ["claude", "chatgpt"],
    "refactor":      ["claude", "chatgpt"],
    "qa":            ["claude", "chatgpt"],
    "review":        ["claude", "chatgpt"],
    "architecture":  ["chatgpt", "claude"],
    "planning":      ["chatgpt", "claude"],
    "business":      ["chatgpt", "claude"],
    "analysis":      ["chatgpt", "claude"],
}
MODES = ("auto", "claude_first", "chatgpt_first")


def _state(name):
    return db.one("SELECT * FROM ext_agent_state WHERE agent=:a", a=name)


def heartbeat(key, agent_name, status, note=None, priority=None):
    own = (key.get("actor_name") or key.get("name") or "").lower()
    if agent_name.lower() != own:
        raise ApiError("FORBIDDEN_SCOPE",
                       f"ключ агента «{own}» не может отмечать состояние агента «{agent_name}»")
    if status not in STATUSES:
        raise ApiError("VALIDATION_ERROR", "status должен быть available, limited или unavailable")
    db.q("""INSERT INTO ext_agent_state (agent, status, note, priority, last_seen, updated_at)
            VALUES (:a,:s,:n,:p,NOW(),NOW())
            ON CONFLICT (agent) DO UPDATE SET status=:s, note=:n,
                priority=COALESCE(:p, ext_agent_state.priority),
                last_seen=NOW(), updated_at=NOW()""",
         a=agent_name.lower(), s=status, n=(note or "")[:500], p=priority)
    return get(agent_name)


def _row(name, keys):
    st = _state(name) or {}
    stale = db.one("""SELECT (:ls IS NULL OR :ls < NOW() - (:m || ' minutes')::interval) AS stale""",
                   ls=st.get("last_seen"), m=STALE_MINUTES)["stale"] if st else True
    caps = sorted({s.strip() for k in keys for s in (k.get("scopes") or "").split(",") if s.strip()})
    return {
        "agent_id": name,
        "name": name,
        "type": (keys[0].get("actor_type") if keys else "ai_agent"),
        "status": ("unknown" if (stale or not st) else st.get("status")),
        "self_reported": st.get("status"),
        "note": st.get("note"),
        "priority": st.get("priority") or 50,
        "billing_mode": "subscription",
        "capabilities": caps,
        "keys": [{"id": k["id"], "name": k["name"], "active": k["active"],
                  "accounts": k.get("account_ids") or "все"} for k in keys],
        "availability": {"stale_after_min": STALE_MINUTES, "is_stale": bool(stale)},
        "last_seen": st.get("last_seen"),
    }


# Серверные демоны heartbeat не шлют: они не «отмечаются», они работают.
# Их живость видна по нарядам, которые они реально двигают.
DAEMONS = ("gpt-executor", "claude-lead")
DAEMON_ALIVE_MINUTES = 90

SYSTEMD_ROOT = "/run/systemd/system"


def systemd_available():
    """Управляет ли systemd этой машиной.

    Спрашивать systemctl там, где systemd не хозяин, — значит однажды
    получить процесс, который не возвращается: таймаут убивает потомка, а
    чтение трубы ждёт внука. Проверка дешевле любого таймаута.
    """
    return os.path.isdir(SYSTEMD_ROOT)


def unit_active(unit, timeout=10):
    """Жива ли служба. Без systemd ответ отрицательный, но мгновенный."""
    if not systemd_available():
        return False
    try:
        r = _sysctl_active(unit)
    except Exception:
        return False
    return (r.stdout or "").strip() == "active"



def alive(agent):
    """Служба отмечается при каждом обороте цикла — это и есть её пульс.

    Считать живость по движению нарядов нельзя: без работы демон выглядит
    мёртвым, а мёртвому работу не дают. Замкнутый круг, из которого система
    сама не выходит.
    """
    try:
        db.q("""INSERT INTO ext_agent_state (agent, status, note, priority,
                                             last_seen, updated_at)
                VALUES (:a, 'available', 'серверная служба', 60, NOW(), NOW())
                ON CONFLICT (agent) DO UPDATE SET status='available',
                    last_seen=NOW(), updated_at=NOW()""", a=agent.lower())
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=137)


# Какая служба кого воплощает. Запущенный процесс — самое надёжное
# доказательство живости: он не зависит ни от записи в базу, ни от того,
# была ли у службы работа.
DAEMON_UNITS = {
    "gpt-executor": tuple("boris-executor" + ("" if i == 1 else "-%d" % i)
                          for i in range(1, 9)),
    "claude-lead": ("boris-lead",),
}
_UNIT_CACHE = {"at": 0.0, "alive": set()}


def _units_alive():
    """Спрашиваем systemd, но не чаще раза в полминуты."""
    import subprocess, time as _t
    if _t.time() - _UNIT_CACHE["at"] < 30:
        return _UNIT_CACHE["alive"]
    alive = set()
    for agent, units in DAEMON_UNITS.items():
        for unit in units:
            try:
                r = _sysctl_active(unit)
            except Exception:
                continue
            if (r.stdout or "").strip() == "active":
                alive.add(agent)
                break
    _UNIT_CACHE["at"], _UNIT_CACHE["alive"] = _t.time(), alive
    return alive


def _daemon_rows():
    """Демон жив, если его служба запущена, он отметился или двигал наряды.

    Три независимых источника, и достаточно любого. Раньше источник был один
    — движение нарядов, — и из-за этого простой сам себя продлевал: работы
    нет, значит демон «мёртв», значит работы ему не дают.
    """
    seen = {}
    for agent in _units_alive():
        seen[agent] = {"agent": agent, "last_seen": None, "why": "служба запущена"}
    for sql in (
        """SELECT agent, last_seen FROM ext_agent_state
            WHERE agent = ANY(:names)
              AND last_seen > NOW() - (:m || ' minutes')::interval""",
        """SELECT assigned_to AS agent, MAX(updated_at) AS last_seen
             FROM ext_a2a_orders
            WHERE assigned_to = ANY(:names)
              AND updated_at > NOW() - (:m || ' minutes')::interval
            GROUP BY assigned_to"""):
        try:
            for r in db.rows(sql, names=list(DAEMONS), m=DAEMON_ALIVE_MINUTES):
                seen.setdefault(r["agent"], {"agent": r["agent"],
                                             "last_seen": r.get("last_seen"),
                                             "why": "отметилась сама"})
        except Exception:
            continue
    out = []
    for r in seen.values():
        out.append({
            "agent_id": r["agent"], "name": r["agent"], "type": "ai_agent",
            "status": "available", "self_reported": None,
            "note": "серверная служба: " + r.get("why", "жива"),
            "priority": 60, "billing_mode": "subscription",
            "capabilities": ["read", "write", "tasks"], "keys": [],
            "availability": {"stale_after_min": DAEMON_ALIVE_MINUTES,
                             "is_stale": False},
            "last_seen": r.get("last_seen"), "daemon": True})
    return out


def registry():
    keys = db.rows("""SELECT id, name, actor_type, actor_name, scopes, account_ids, active
                      FROM ext_api_keys WHERE active=TRUE ORDER BY id""")
    grouped = {}
    for k in keys:
        grouped.setdefault((k.get("actor_name") or k["name"]).lower(), []).append(k)
    for known in ("chatgpt", "claude"):
        grouped.setdefault(known, [])
    rows = [_row(n, ks) for n, ks in sorted(grouped.items())]
    have = {r["name"] for r in rows}
    return rows + [d for d in _daemon_rows() if d["name"] not in have]


def get(name):
    for a in registry():
        if a["name"] == name.lower():
            return a
    raise ApiError("VALIDATION_ERROR", f"агент «{name}» не зарегистрирован")


def pick(kind=None, mode="auto", exclude=None, require_alive=False):
    """Возвращает (агент, причина). Недоступных и молчащих пропускаем.

    require_alive=True запрещает запасной вариант «отдадим тому, кто молчит».
    Именно он однажды раздал семь задач агенту, который не работает: задачи
    сутки числились в работе, держали ёмкость, и ничего не двигалось.
    """
    if mode not in MODES:
        raise ApiError("VALIDATION_ERROR", "mode должен быть auto, claude_first или chatgpt_first")
    reg = {a["name"]: a for a in registry()}
    exclude = {e.lower() for e in (exclude or [])}

    if mode == "claude_first":
        order = ["claude", "chatgpt"]
        why = "режим claude_first"
    elif mode == "chatgpt_first":
        order = ["chatgpt", "claude"]
        why = "режим chatgpt_first"
    else:
        order = SPECIALIZATION.get((kind or "").lower(), ["claude", "chatgpt"])
        why = f"специализация под «{kind or 'общая задача'}»"

    if require_alive:
        # Автономный контур ведут серверные службы. Чат-агенты сюда не годятся
        # даже с отметкой: отметку там ставит человек, а не процесс.
        from .a2a import NON_AUTONOMOUS
        for a in reg.values():
            if (a.get("daemon") and a["name"] not in exclude
                    and a["status"] == "available"):
                return a, "живая серверная служба"
        for name in order:
            a = reg.get(name)
            if (a and name not in exclude and name not in NON_AUTONOMOUS
                    and a["status"] == "available"):
                return a, why
        return None, ("нет живого исполнителя: серверные службы не отвечают, "
                      "а чат-агентов автономный контур не использует")

    for name in order:
        a = reg.get(name)
        if not a or name in exclude:
            continue
        if a["status"] == "unavailable":
            continue
        if a["status"] == "limited":
            continue
        if a["status"] == "unknown":
            continue
        return a, why
    for name in order:
        a = reg.get(name)
        if a and name not in exclude and a["status"] != "unavailable":
            return a, why + " (запасной: подтверждённой доступности нет)"
    return None, "свободных агентов нет"
