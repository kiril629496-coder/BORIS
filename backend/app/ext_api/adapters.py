# -*- coding: utf-8 -*-
"""Слой адаптеров агентов: AgentController → AgentAdapter → provider.

Контракт один на всех провайдеров: create_job · execute_job · continue_job ·
handoff_job · get_status · cancel_job. Gateway не знает, кто там за адаптером,
и это важно — включение Claude после ответа Anthropic не потребует правки
оркестратора, только строки в таблице ext_agent_adapters.

Четыре режима подключения:

  actions          агент сам ходит в Gateway (ChatGPT Actions, любой раннер с
                   ключом). Работает СЕЙЧАС, отдельного API-биллинга не требует.
  subscription_sdk агент запускается на стороне пользователя внутри его
                   подписки. Архитектурно готов, выключен до письменного
                   подтверждения Anthropic.
  api              прямые вызовы провайдерского API. ВЫКЛЮЧЕН: это отдельный
                   биллинг, включается только человеком.
  mock             только для автотестов архитектуры handoff, реальным
                   исполнением не является и никогда так не помечается.
"""
import os, json
from . import db
from .errors import ApiError

MODES = ("actions", "subscription_sdk", "api", "mock")

DEFAULTS = [
    {"agent": "chatgpt", "mode": "actions", "enabled": True, "reason": None,
     "note": "ChatGPT ходит в Gateway через Actions по ключу агента; "
             "отдельного API-биллинга не требует"},
    {"agent": "claude", "mode": "subscription_sdk", "enabled": False,
     "reason": "ANTHROPIC_POLICY_CONFIRMATION_PENDING",
     "note": "включить только после письменного подтверждения Anthropic по "
             "подписочной авторизации; Anthropic API не использовать"},
    {"agent": "mock", "mode": "mock", "enabled": False,
     "reason": "TEST_ONLY",
     "note": "включается переменной EXT_API_ALLOW_TEST_HOOKS=1, только для "
             "проверки архитектуры передачи задач"},
]


def bootstrap():
    for d in DEFAULTS:
        db.q("""INSERT INTO ext_agent_adapters (agent, mode, enabled, reason, note)
                VALUES (:a,:m,:e,:r,:n) ON CONFLICT (agent) DO NOTHING""",
             a=d["agent"], m=d["mode"], e=d["enabled"], r=d["reason"], n=d["note"])


def all_adapters():
    bootstrap()
    rows = db.rows("SELECT * FROM ext_agent_adapters ORDER BY agent")
    out = []
    for r in rows:
        enabled = bool(r["enabled"])
        reason = r.get("reason")
        if r["mode"] == "mock":
            enabled = os.environ.get("EXT_API_ALLOW_TEST_HOOKS") == "1"
            reason = None if enabled else "TEST_ONLY"
        if r["mode"] == "api" and enabled:
            # страховка: режим api не может оказаться включённым молча
            enabled, reason = False, "API_BILLING_REQUIRED"
        out.append({"agent": r["agent"], "mode": r["mode"], "enabled": enabled,
                    "reason": reason, "note": r.get("note"),
                    "billing_mode": ("subscription" if r["mode"] in ("actions", "subscription_sdk")
                                     else ("api" if r["mode"] == "api" else "none")),
                    "real_execution": r["mode"] != "mock",
                    "updated_at": r.get("updated_at")})
    return out


def get(agent):
    for a in all_adapters():
        if a["agent"] == (agent or "").lower():
            return a
    raise ApiError("VALIDATION_ERROR", f"адаптер для агента «{agent}» не зарегистрирован")


def set_config(key, agent, enabled=None, mode=None, note=None):
    """Включение адаптера — действие человека, не агента."""
    if "admin" not in key["scope_set"]:
        raise ApiError("FORBIDDEN_SCOPE", "конфигурация адаптера меняется ключом со scope admin")
    if (key.get("actor_type") or "ai_agent") == "ai_agent":
        raise ApiError("FORBIDDEN_SCOPE",
                       "ключ AI-агента не может включать адаптеры — нужен ключ человека")
    cur = get(agent)
    new_mode = mode or cur["mode"]
    if new_mode not in MODES:
        raise ApiError("VALIDATION_ERROR", "mode должен быть один из: " + ", ".join(MODES))
    if new_mode == "api" and enabled:
        raise ApiError("API_BILLING_REQUIRED",
                       "режим api означает отдельный биллинг провайдера. "
                       "Включение требует отдельного явного решения: сначала выдайте "
                       "разрешение расходов через /costs/approvals, затем повторите запрос "
                       "с mode=api и confirm_billing=true")
    if new_mode == "subscription_sdk" and enabled and cur["reason"] == "ANTHROPIC_POLICY_CONFIRMATION_PENDING":
        raise ApiError("ADAPTER_DISABLED",
                       "адаптер ждёт письменного подтверждения Anthropic по подписочной "
                       "авторизации. Снимите причину явно: сначала PATCH reason=NULL "
                       "ключом человека, потом enabled=true")
    db.q("""UPDATE ext_agent_adapters SET mode=:m, enabled=:e, note=COALESCE(:n, note),
                updated_at=NOW() WHERE agent=:a""",
         a=agent.lower(), m=new_mode, e=bool(enabled if enabled is not None else cur["enabled"]),
         n=note)
    return get(agent)


def clear_reason(key, agent, note=None):
    """Снять блокирующую причину — тоже только человеком и явно."""
    if "admin" not in key["scope_set"] or (key.get("actor_type") or "ai_agent") == "ai_agent":
        raise ApiError("FORBIDDEN_SCOPE", "нужен ключ человека со scope admin")
    db.q("""UPDATE ext_agent_adapters SET reason=NULL, note=COALESCE(:n, note),
                updated_at=NOW() WHERE agent=:a""", a=agent.lower(), n=note)
    return get(agent)


# ============================================================ сам контракт

class AgentAdapter:
    """Один интерфейс на всех провайдеров."""

    def __init__(self, cfg):
        self.agent = cfg["agent"]
        self.mode = cfg["mode"]
        self.enabled = cfg["enabled"]
        self.reason = cfg["reason"]
        self.cfg = cfg

    # -- служебное ---------------------------------------------------------

    def _guard(self):
        if self.enabled:
            return
        if self.mode == "api":
            raise ApiError("API_BILLING_REQUIRED",
                           f"адаптер «{self.agent}» работает только через отдельный API-биллинг, "
                           "который вы не разрешали")
        raise ApiError("ADAPTER_DISABLED",
                       f"адаптер «{self.agent}» выключен: {self.reason or 'причина не указана'}")

    def describe(self):
        return {"agent": self.agent, "mode": self.mode, "enabled": self.enabled,
                "reason": self.reason, "billing_mode": self.cfg["billing_mode"],
                "real_execution": self.cfg["real_execution"],
                "contract": ["create_job", "execute_job", "continue_job",
                             "handoff_job", "get_status", "cancel_job"]}

    # -- контракт ----------------------------------------------------------

    def create_job(self, dev_job):
        """Назначение задачи агенту. Саму задачу создаёт оркестратор."""
        self._guard()
        from . import dev
        dev._set(dev_job["id"], assigned_agent=self.agent, status="queued", wait_reason=None)
        dev.event(dev_job["id"], dev_job["current_phase"], self.agent, "adapter_create",
                  {"mode": self.mode})
        return {"agent": self.agent, "mode": self.mode, "dev_job_id": dev_job["dev_job_id"],
                "accepted": True}

    def execute_job(self, dev_job):
        """Запуск работы.

        В режиме actions Gateway никого не дёргает: агент забирает задачу сам,
        своим ключом. Поэтому честный ответ — awaiting_agent, а не running.
        """
        self._guard()
        from . import dev
        if self.mode == "actions":
            dev._set(dev_job["id"], status="running", assigned_agent=self.agent)
            dev.event(dev_job["id"], dev_job["current_phase"], self.agent, "adapter_execute",
                      {"mode": "actions", "note": "агент забирает задачу сам"})
            return {"status": "awaiting_agent", "mode": "actions", "agent": self.agent,
                    "pull_endpoint": "GET /api/v1/dev/jobs?status=running",
                    "report_endpoint": "POST /api/v1/dev/jobs/{id}/phase"}
        if self.mode == "mock":
            dev._set(dev_job["id"], status="running", assigned_agent=self.agent)
            return {"status": "running", "mode": "mock", "agent": self.agent,
                    "warning": "mock-адаптер не является реальным исполнением"}
        raise ApiError("ADAPTER_DISABLED",
                       f"режим «{self.mode}» не активирован для агента «{self.agent}»")

    def continue_job(self, dev_job, context=None):
        """Продолжение уже начатой задачи — с сохранённым контекстом."""
        self._guard()
        from . import dev
        dev._set(dev_job["id"], assigned_agent=self.agent, status="running")
        dev.event(dev_job["id"], dev_job["current_phase"], self.agent, "adapter_continue",
                  {"has_context": bool(context)})
        return {"status": "running", "agent": self.agent, "resumed_phase": dev_job["current_phase"],
                "context_kept": True}

    def handoff_job(self, dev_job, to_agent, reason=None, context=None):
        """Передача другому адаптеру со структурированным контекстом."""
        self._guard()
        target = get(to_agent)
        if not target["enabled"]:
            raise ApiError("ADAPTER_DISABLED",
                           f"принимающий адаптер «{to_agent}» выключен: {target['reason']}")
        return {"from": self.agent, "to": to_agent, "reason": reason,
                "context": structured_context(dev_job, extra=context)}

    def get_status(self, dev_job):
        from . import dev
        j = dev.get(dev_job["id"] if isinstance(dev_job, dict) else dev_job)
        return {"dev_job_id": j["dev_job_id"], "status": j["status"],
                "current_phase": j["current_phase"], "assigned_agent": j["assigned_agent"],
                "qa_status": j["qa_status"], "next_action": j.get("next_action")}

    def cancel_job(self, dev_job):
        from . import dev
        dev._set(dev_job["id"], status="cancelled", assigned_agent=None)
        dev.event(dev_job["id"], dev_job["current_phase"], self.agent, "adapter_cancel", {})
        return {"dev_job_id": dev_job["dev_job_id"], "status": "cancelled"}


def adapter_for(agent):
    return AgentAdapter(get(agent))


def structured_context(dev_job, extra=None):
    """Ровно то, что нужно принимающему агенту — и ничего лишнего.

    Никакой истории переписки, никаких секретов, никаких чужих клиентов.
    """
    from . import auth
    phases = dev_job.get("phases") or {}
    completed = sorted([p for p, v in phases.items() if v.get("state") == "pass"])
    remaining = [p for p in (dev_job.get("required_phases") or [])
                 if (phases.get(p) or {}).get("state") != "pass"]
    ctx = {
        "job_id": dev_job["dev_job_id"],
        "goal": dev_job["goal"],
        "title": dev_job["title"],
        "current_phase": dev_job["current_phase"],
        "status": dev_job["status"],
        "files_changed": dev_job.get("files_changed") or [],
        "files_scope": (dev_job.get("scope") or {}).get("files"),
        "completed_steps": completed,
        "remaining_steps": remaining,
        "errors": (dev_job.get("errors") or [])[-5:],
        "tests": (dev_job.get("tests") or [])[-5:],
        "decisions": (dev_job.get("decisions") or [])[-5:],
        "acceptance_criteria": [{"text": c["text"], "done": c["done"]}
                                for c in (dev_job.get("acceptance_criteria") or [])],
        "next_action": dev_job.get("next_action") or f"продолжить фазу {dev_job['current_phase']}",
        "agent_history": [{"phase": p, "by": v.get("by"), "state": v.get("state")}
                          for p, v in phases.items() if v.get("by")],
    }
    if extra:
        ctx["handoff_note"] = auth._redact(extra)
    return ctx
