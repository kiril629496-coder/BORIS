# -*- coding: utf-8 -*-
from pathlib import Path
from app.services.exception_observability import observe_suppressed
"""Claude Lead как серверный процесс: ставит наряды и принимает работу.

Лид не пишет код. Он решает, что должно быть сделано, и проверяет, сделано ли
это на самом деле — своими же глазами, а не по словам исполнителя. Поэтому у
него есть инструменты чтения: репозиторий, логи, git, runtime, БД на чтение.

    venv/bin/python3 -m app.ext_api.lead probe
    venv/bin/python3 -m app.ext_api.lead once
    venv/bin/python3 -m app.ext_api.lead run

Владельцу уходит только DONE или настоящий BLOCKED_HUMAN.
"""
import os, io, json, re, time, argparse, subprocess, tempfile, sys
from . import db, auth, repo, ops, dev, a2a, notify
from .executor import _env_all, _grep_sources, _opener, _host, _backend_dir
from .errors import ApiError

AGENT = os.environ.get("EXT_API_LEAD_AGENT", "claude-lead")
MAX_STEPS = 16
MAX_TOOL_OUTPUT = 12000
POLL_SECONDS = 15

LEAD_KEY = {"scope_set": {"read", "write", "tasks"}, "actor_type": "ai_agent",
            "actor_name": AGENT, "name": AGENT, "id": None,
            "max_items_per_call": 200, "allowed_accounts": None}

BASE_URL_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE", "ANTHROPIC_URL")
PROXY_VARS = ("ANTHROPIC_PROXY", "AI_PROXY", "PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY",
              "ALL_PROXY", "https_proxy", "http_proxy", "PROXY")
MODEL_PREFERENCE = [r"opus", r"sonnet", r"haiku"]
API_VERSION = "2023-06-01"

_TRANSPORT = None


# ------------------------------------------------------------------ транспорт

def _api_key():
    env = _env_all()
    k = env.get("ANTHROPIC_API_KEY")
    return k.strip() if k else None


def _candidates():
    env = _env_all()
    seen, out = set(), []

    def add(base, proxy, why):
        k = (base, proxy)
        if k not in seen:
            seen.add(k)
            out.append({"base_url": base, "proxy": proxy, "why": why})

    for v in BASE_URL_VARS:
        if env.get(v):
            add(env[v].rstrip("/"), None, f"{v} из конфигурации")
    for v in PROXY_VARS:
        if env.get(v):
            add(None, env[v], f"прокси из {v}")
    for lit in _grep_sources(r"(?:proxy|proxies)\s*=\s*[\"\']([a-z]+://[^\"\']+)[\"\']"):
        add(None, lit, "прокси зашит в коде BORIS")
    add(None, None, "напрямую")
    return out


def _try(path, payload, method, timeout, base, proxy):
    import urllib.request, urllib.error
    key = _api_key()
    if not key:
        raise ApiError("AVITO_CREDENTIALS_MISSING",
                       "ANTHROPIC_API_KEY не найден ни в окружении, ни в backend/.env")
    url = (base or "https://api.anthropic.com/v1").rstrip("/") + path
    from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
    external_payload, privacy_vault = safe_external_ai_payload(payload)
    req = urllib.request.Request(
        url, method=method,
        data=(json.dumps(external_payload).encode("utf-8") if external_payload is not None else None),
        headers={"x-api-key": key, "anthropic-version": API_VERSION,
                 "Content-Type": "application/json"})
    try:
        with _opener(proxy).open(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
            return restore_external_ai_output(body, privacy_vault)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:1200]
        if e.code in (401, 403):
            raise ApiError("UNAUTHORIZED", f"Anthropic отверг запрос: {body}")
        if e.code == 429:
            raise ApiError("RATE_LIMITED", f"Anthropic: лимит или баланс — {body}")
        raise ApiError("DEPENDENCY_UNAVAILABLE", f"Anthropic {e.code}: {body}")
    except ApiError:
        raise
    except Exception as e:
        raise ApiError("DEPENDENCY_UNAVAILABLE", f"не достучался: {type(e).__name__}: {e}")


def transport(force=False):
    global _TRANSPORT
    if _TRANSPORT and not force:
        return _TRANSPORT
    tried = []
    for c in _candidates():
        try:
            _try("/models", None, "GET", 45, c["base_url"], c["proxy"])
            _TRANSPORT = dict(c, ok=True, tried=tried)
            return _TRANSPORT
        except ApiError as e:
            tried.append({"why": c["why"], "host": _host(c["base_url"] or c["proxy"]),
                          "error": f"{e.code}: {e.message[:160]}"})
    raise ApiError("DEPENDENCY_UNAVAILABLE",
                   "ни один способ добраться до Anthropic не сработал",
                   details={"tried": tried})


def _http(path, payload=None, method="POST", timeout=300):
    t = transport()
    return _try(path, payload, method, timeout, t["base_url"], t["proxy"])


def claude_cli():
    """Путь до Claude Code. Он работает по подписке Max, а не по API-кредитам."""
    for cand in (os.environ.get("CLAUDE_CLI"), "claude",
                 "/usr/local/bin/claude", "/usr/bin/claude",
                 os.path.expanduser("~/.npm-global/bin/claude"),
                 os.path.expanduser("~/.local/bin/claude")):
        if not cand:
            continue
        try:
            r = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    return None


def oauth_token():
    return _env_all().get("CLAUDE_CODE_OAUTH_TOKEN")


def provider():
    """Development reviewer policy: Gemini primary, no paid API fallback."""
    from . import aiprov
    forced = (os.environ.get("EXT_API_LEAD_PROVIDER") or "").lower()
    if forced:
        if forced in aiprov.DEV_PAID_DENY:
            raise ApiError("DEPENDENCY_UNAVAILABLE",
                           f"development paid reviewer blocked by owner policy: {forced}")
        if forced == "mock" and os.environ.get("BORIS_DEV_ALLOW_MOCK") == "1":
            return "mock"
        if forced not in aiprov.development_candidates():
            raise ApiError("DEPENDENCY_UNAVAILABLE",
                           f"development reviewer unavailable by policy/state: {forced}")
        return forced
    available = aiprov.development_candidates()
    return available[0] if available else None


def pick_model():
    forced = os.environ.get("EXT_API_LEAD_MODEL")
    if forced:
        return forced
    names = [m.get("id", "") for m in _http("/models", method="GET", timeout=60).get("data", [])]
    for pat in MODEL_PREFERENCE:
        hit = sorted(n for n in names if re.search(pat, n, re.I))
        if hit:
            return hit[-1]
    if not names:
        raise ApiError("DEPENDENCY_UNAVAILABLE", "Anthropic не вернул ни одной модели")
    return sorted(names)[-1]


def probe():
    p = provider()
    cli, tok = claude_cli(), bool(oauth_token())
    out = {"provider": p, "api_key_present": bool(_api_key()),
           "subscription": {"claude_cli": cli, "oauth_token": tok,
                            "usable": bool(cli and tok)}}
    if p == "claude_code":
        if not (cli and tok):
            out.update({"ok": False, "error": "нет claude CLI или CLAUDE_CODE_OAUTH_TOKEN"})
            return out
        r = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30)
        conf = _env_all()
        proxy = next((conf[k] for k in ("ANTHROPIC_PROXY", "AI_PROXY", "HTTPS_PROXY",
                                        "https_proxy", "ALL_PROXY", "PROXY_URL")
                      if conf.get(k)), None)
        out.update({"model": "claude code (подписка Max)", "ok": r.returncode == 0,
                    "version": (r.stdout or "").strip(),
                    "proxy_host": _host(proxy),
                    "note": "работает по подписке, API-кредиты не тратятся"})
        return out
    if p == "openai":
        from . import executor
        e = executor.probe()
        out.update({"model": e.get("model"), "ok": e.get("ok"),
                    "transport": e.get("transport"), "error": e.get("error"),
                    "note": "приёмка идёт на движке OpenAI: у ревьюера отдельная "
                            "роль и отдельный промпт, код писал не он"})
        return out
    if p != "anthropic":
        out.update({"model": "mock", "ok": True,
                    "note": "ключ не найден или задан EXT_API_LEAD_PROVIDER=mock"})
        return out
    try:
        t = transport(force=True)
        out["transport"] = {"kind": ("base_url" if t["base_url"] else
                                     ("proxy" if t["proxy"] else "direct")),
                            "why": t["why"], "host": _host(t["base_url"] or t["proxy"])}
        out["model"] = pick_model()
        out["ok"] = True
    except ApiError as e:
        out["ok"] = False
        out["error"] = f"{e.code}: {e.message}"
        out["tried"] = (e.details or {}).get("tried")
    return out


# ------------------------------------------------------- инструменты проверки

def _cut(x):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_TOOL_OUTPUT else s[:MAX_TOOL_OUTPUT] + "\n… обрезано"


CHECK_TOOLS = {
    "repo_tree": (lambda paths=None, **_: repo.tree(paths),
                  "Дерево файлов проекта.",
                  {"paths": {"type": "array", "items": {"type": "string"}}}),
    "repo_read": (lambda path=None, paths=None, start=None, end=None, **_:
                  (repo.read_many(paths) if paths else repo.read_file(path, start or 1, end)),
                  "Прочитать файл проекта и убедиться, что там действительно то, о чём отчитались.",
                  {"path": {"type": "string"},
                   "paths": {"type": "array", "items": {"type": "string"}},
                   "start": {"type": "integer"}, "end": {"type": "integer"}}),
    "shell": (lambda command=None, cwd=None, **_: ops.shell(LEAD_KEY, command, cwd),
              "Команда из белого списка — для собственной проверки.",
              {"command": {"type": "string"}, "cwd": {"type": "string"}}),
    "git": (lambda what="status", **_: ops.git_info(LEAD_KEY, what),
            "Что реально поменялось в рабочем дереве.", {"what": {"type": "string"}}),
    "logs": (lambda unit="boris-backend", lines=100, **_: ops.logs(LEAD_KEY, unit, lines),
             "Журнал службы.", {"unit": {"type": "string"}, "lines": {"type": "integer"}}),
    "http_check": (lambda path="/health", port=8000, **_: ops.http_check(LEAD_KEY, path, port),
                   "Runtime-проверка на localhost.",
                   {"path": {"type": "string"}, "port": {"type": "integer"}}),
    "db_query": (lambda sql=None, limit=100, **_: ops.db_query(LEAD_KEY, sql, limit),
                 "Диагностика БД на чтение.",
                 {"sql": {"type": "string"}, "limit": {"type": "integer"}}),
    "browser": (lambda paths=None, width=1440, port=3002, **_:
                ops.browser(LEAD_KEY, paths, width, 900, port),
                "Проверить страницы кабинета браузером самому: коды, пустые экраны, "
                "редиректы, кнопки, мобильная ширина.",
                {"paths": {"type": "array", "items": {"type": "string"}},
                 "width": {"type": "integer"}, "port": {"type": "integer"}}),
    "list_patches": (lambda dev_job_id=None, **_:
                     {"patches": repo.list_patches(dev_job_id)},
                     "Какие правки предложены и в каком они статусе.",
                     {"dev_job_id": {"type": "string"}}),
}

VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": ("Решение по наряду. ACCEPT только если каждый критерий подтверждён "
                    "доказательством, которое ты проверил. RETURN_WITH_FIXES требует "
                    "списка конкретных замечаний. BLOCKED_HUMAN — только если без "
                    "владельца объективно нельзя продолжить."),
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string",
                        "enum": ["ACCEPT", "RETURN_WITH_FIXES", "BLOCKED_HUMAN", "BLOCKED_INFRA"]},
            "note": {"type": "string"},
            "fixes": {"type": "array", "items": {
                "type": "object",
                "properties": {"failed_criterion": {"type": "string"},
                               "problem": {"type": "string"},
                               "required_correction": {"type": "string"}},
                "required": ["failed_criterion", "problem", "required_correction"]}},
            "blocked_reason": {"type": "string"},
        },
        "required": ["verdict"],
    },
}


PLAN_TOOL = {
    "name": "submit_plan",
    "description": ("Решение лида о следующем шаге задачи, когда прежние критерии "
                    "приёмки закрыты. NEXT — работа продолжается, назови фазу, цель и "
                    "новые проверяемые критерии. DONE — цель задачи достигнута целиком. "
                    "BLOCKED_HUMAN — дальше объективно нельзя без владельца."),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["NEXT", "DONE", "BLOCKED_HUMAN"]},
            "phase": {"type": "string"},
            "goal": {"type": "string", "description": "что именно сделать дальше"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"},
                                    "description": "проверяемые, а не общие слова"},
            "not_applicable_phases": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"phase": {"type": "string"},
                                         "why": {"type": "string"}},
                          "required": ["phase", "why"]},
                "description": "фазы, работы для которых в этой задаче нет — с причиной"},
            "reason": {"type": "string"},
        },
        "required": ["decision"],
    },
}


PLAN_SYSTEM = """Ты — технический лид разработки BORIS. Прежние критерии приёмки задачи
закрыты доказательствами. Реши, что дальше.

1. Сверь ЦЕЛЬ задачи с тем, что реально сделано. Цель обычно шире первых критериев:
   разведка закрывает свои критерии и рождает следующие.
2. Если до цели ещё далеко — decision NEXT: назови фазу, конкретную цель следующего
   шага и новые критерии приёмки. Критерии должны быть проверяемыми: «в базе N строк
   такого-то вида», «команда вернула такой-то код», а не «сделано хорошо».
3. DONE ставь только если цель задачи достигнута целиком, а не только её начало.
   Фазы, работы для которых в этой задаче нет, перечисли в not_applicable_phases
   с честной причиной — это будет записано и видно.
4. BLOCKED_HUMAN — только если без владельца объективно нельзя: деньги, доступ,
   отсутствующие данные от клиента, необратимое действие.
5. Проверяй фактами через свои инструменты, не выдумывай состояние.

Закончив, вызови submit_plan ровно один раз."""



# ---------------------------------------------------------------------------
# BORIS ROOT DEVELOPMENT CONTRACT
# ---------------------------------------------------------------------------

_DEV_CONTRACT_PATH = "/root/BORIS/AGENTS.md"

def _development_contract():
    p = Path(_DEV_CONTRACT_PATH)

    if not p.is_file():
        raise RuntimeError(
            "BORIS development contract missing: "
            + _DEV_CONTRACT_PATH
        )

    text = p.read_text(encoding="utf-8").strip()

    if not text:
        raise RuntimeError(
            "BORIS development contract is empty: "
            + _DEV_CONTRACT_PATH
        )

    return (
        "AUTHORITATIVE BORIS DEVELOPMENT CONTRACT\n"
        "==========================================\n"
        + text
    )



def _plan_prompt(job, prev):
    ev = (prev.get("last_evidence") or {})
    return _development_contract() + "\n\n" + (
        ("ЗАДАЧА\n" + json.dumps({
        "task": job["dev_job_id"], "title": job["title"], "goal": job["goal"],
        "scope": job.get("scope"), "current_phase": job["current_phase"],
        "required_phases": job.get("required_phases"),
        "phases_state": {k: (v or {}).get("state")
                         for k, v in (job.get("phases") or {}).items()},
        "closed_criteria": [{"criterion": c.get("text"),
                             "evidence": (c.get("evidence") or "")[:600]}
                            for c in job["acceptance_criteria"] if c.get("done")],
    }, ensure_ascii=False, indent=2)
        + "\n\nПОСЛЕДНЯЯ ПРИНЯТАЯ РАБОТА\n"
        + json.dumps({"summary": ev.get("summary"), "evidence": (ev.get("evidence") or "")[:3000],
                      "blockers": ev.get("blockers")}, ensure_ascii=False, indent=2))
                      )


PLAN_FORMAT = """
Ответь ТОЛЬКО одним JSON-объектом:
{"decision": "NEXT" | "DONE" | "BLOCKED_HUMAN",
 "phase": "BACKEND",
 "goal": "что сделать дальше, конкретно",
 "acceptance_criteria": ["проверяемый критерий", "..."],
 "not_applicable_phases": [{"phase": "UX", "why": "в задаче нет интерфейса"}],
 "reason": "одно предложение"}
"""


def plan(job, prev):
    """Что делать дальше, когда прежние критерии закрыты. Решает лид."""
    p = provider()
    if p is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE", "no free development reviewer available; paid fallback denied")
    if p == "mock":
        closed = [c for c in job["acceptance_criteria"] if c.get("done")]
        if len(closed) >= 3:
            return {"decision": "DONE", "reason": "цель достигнута",
                    "not_applicable_phases": [
                        {"phase": ph, "why": "работы этой фазы в задаче нет"}
                        for ph in (job.get("required_phases") or [])
                        if (job["phases"].get(ph) or {}).get("state") != "pass"]}
        return {"decision": "NEXT", "phase": "BACKEND", "goal": "продолжить работу",
                "acceptance_criteria": ["следующий проверяемый шаг"],
                "reason": "цель шире закрытых критериев"}
    if p == "claude_code":
        return _extract_json(_ask_claude_code(
            PLAN_SYSTEM + "\n\n" + _plan_prompt(job, prev) + "\n" + PLAN_FORMAT))
    if p == "gemini_cli":
        return _ask_gemini_cli(PLAN_SYSTEM + "\n\n" + _plan_prompt(job, prev) + "\n" + PLAN_FORMAT)
    raise ApiError("DEPENDENCY_UNAVAILABLE", f"development reviewer denied/unavailable: {p}")


def _tool_specs():
    specs = [{"name": n, "description": d, "input_schema":
              {"type": "object", "properties": p}} for n, (_f, d, p) in CHECK_TOOLS.items()]
    specs.append(VERDICT_TOOL)
    return specs


def _call_tool(name, args):
    fn = CHECK_TOOLS.get(name, (None,))[0]
    if not fn:
        return {"error": f"инструмента «{name}» нет"}
    try:
        return fn(**(args or {}))
    except ApiError as e:
        return {"error": e.code, "message": e.message}
    except Exception as e:
        return {"error": "INTERNAL", "message": f"{type(e).__name__}: {e}"[:300]}


SYSTEM = """Ты — технический лид разработки BORIS. Исполнитель прислал работу на приёмку.

Твоя задача — не поверить, а проверить. У тебя есть инструменты чтения:
репозиторий, git, логи, runtime, БД. Пользуйся ими.

Правила:
1. ACCEPT только если КАЖДЫЙ критерий приёмки подтверждён доказательством,
   которое ты сам увидел. Статус NOT_CHECKED — это не PASS.
2. «Сборка прошла» без вывода сборки — не доказательство. Возвращай.
3. RETURN_WITH_FIXES пиши конкретно: какой критерий провален, в чём именно
   проблема, что нужно исправить. Общие слова исполнителю бесполезны.
4. BLOCKED_HUMAN ставь только если без владельца объективно нельзя продолжить:
   деньги, отсутствующий доступ, необратимое действие. Нехватка инструмента —
   это BLOCKED_INFRA, владельца этим не беспокоят.
5. Не придирайся к мелочам вне критериев приёмки. Работа принимается по scope.
6. У тебя есть инструменты mcp__boris__*: db_query (SQL только на чтение), repo_read,
   repo_tree, shell, git, logs, http_check. Проверяй ими, а не Bash — Bash в этом
   режиме требует подтверждения человека и работать не будет.
7. Если проверить утверждение нечем, это НЕ значит, что оно ложное. Так и пиши:
   «проверено то-то, остальное подтвердить нечем». Возвращать работу можно только
   за то, что ты нашёл неверным или чего исполнитель вовсе не сделал.
8. Если критерий требует того, что вне возможностей обоих агентов, ставь
   BLOCKED_INFRA с точной формулировкой, чего не хватает, — но не гоняй
   исполнителя по кругу за одним и тем же.

Закончив проверку, вызови submit_verdict ровно один раз."""


def review_prompt(order):
    ev = order.get("last_evidence") or {}
    return _development_contract() + "\n\n" + (
        ("НАРЯД\n" + json.dumps({
        "order_id": order["order_id"], "task": order["dev_job_id"],
        "phase": order["phase"], "goal": order["goal"], "scope": order["scope"],
        "acceptance_criteria": order["acceptance_criteria"],
        "forbidden_changes": order["forbidden_changes"],
        "required_qa": order["required_qa"],
        "required_evidence": order["required_evidence"],
        "iteration": order["iteration"], "max_iterations": order["max_iterations"],
    }, ensure_ascii=False, indent=2)
        + "\n\nREVIEW_REQUEST ОТ ИСПОЛНИТЕЛЯ\n"
        + json.dumps(ev, ensure_ascii=False, indent=2))
        )


# ------------------------------------------------------------------- приёмка

def _openai_tools(verdict_name="submit_verdict"):
    specs = [{"type": "function", "name": n, "description": d,
              "parameters": {"type": "object", "properties": pr}}
             for n, (_f, d, pr) in CHECK_TOOLS.items()]
    spec = PLAN_TOOL if verdict_name == "submit_plan" else VERDICT_TOOL
    specs.append({"type": "function", "name": spec["name"],
                  "description": spec["description"],
                  "parameters": spec["input_schema"]})
    return specs


def _ask_openai(prompt, system=None, verdict_tool="submit_verdict"):
    """Тот же лид, но на движке OpenAI — когда подписки нет."""
    from . import executor
    model = os.environ.get("EXT_API_LEAD_MODEL") or executor.pick_model()
    tools = _openai_tools(verdict_tool)
    payload = {"model": model, "instructions": system or SYSTEM, "tools": tools,
               "tool_choice": "auto",
               "input": [{"role": "user", "content": prompt}]}
    for _ in range(MAX_STEPS):
        resp = executor._http("/responses", payload)
        out = resp.get("output") or []
        fcs = [o for o in out if o.get("type") == "function_call"]
        nxt = []
        if not fcs:
            nxt.append({"role": "user", "content":
                        "Ответ текстом не принимается. Проверь инструментами "
                        "и заверши вызовом submit_verdict."})
        for fc in fcs:
            try:
                args = json.loads(fc.get("arguments") or "{}")
            except Exception:
                args = {}
            if fc.get("name") == verdict_tool:
                return args
            nxt.append({"type": "function_call_output", "call_id": fc.get("call_id"),
                        "output": _cut(_call_tool(fc.get("name"), args))})
        payload = {"model": model, "tools": tools, "tool_choice": "auto",
                   "previous_response_id": resp.get("id"), "input": nxt}
    raise ApiError("JOB_NOT_RETRYABLE", f"лид не вынес решение за {MAX_STEPS} шагов")


VERDICT_FORMAT = """
Ответь ТОЛЬКО одним JSON-объектом, без пояснений вокруг:
{"verdict": "ACCEPT" | "RETURN_WITH_FIXES" | "BLOCKED_HUMAN" | "BLOCKED_INFRA",
 "note": "одно предложение",
 "fixes": [{"failed_criterion": "...", "problem": "...", "required_correction": "..."}],
 "blocked_reason": "только для BLOCKED_*"}
Файлы проекта можешь читать сам, чтобы проверить отчёт.
"""


def _extract_json(text):
    """Модель может обернуть JSON в текст — достаём аккуратно.

    Уже разобранное принимаем как есть: разбирать словарь второй раз —
    ошибка вызывающего, но падать из-за неё приёмка не должна.
    """
    if isinstance(text, dict):
        return text
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "ignore")
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=553)
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=559)
    raise ApiError("VALIDATION_ERROR", f"лид вернул не JSON: {t[:300]}")


def _ask_claude_code(prompt, tools=True, mcp_mode="lead", dev_job_id=None, actor_name=None):
    """Один вызов Claude Code, ответ — JSON. Подписка Max, кредиты не тратятся."""
    from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
    safe_prompt, privacy_vault = safe_external_ai_payload(str(prompt or ""))
    cli, tok = claude_cli(), oauth_token()
    if not cli:
        raise ApiError("NOT_IMPLEMENTED",
                       "на сервере нет Claude Code: npm i -g @anthropic-ai/claude-code")
    if not tok:
        raise ApiError("AVITO_CREDENTIALS_MISSING",
                       "нет CLAUDE_CODE_OAUTH_TOKEN — получите его командой "
                       "claude setup-token на своей машине")
    env = dict(os.environ)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    env.pop("ANTHROPIC_API_KEY", None)     # чтобы не свалиться на кредиты молча
    # С этого IP Anthropic отвечает 403. Рабочий путь — тот же прокси, что уже
    # прописан в конфигурации BORIS; в окружении процесса его может не быть,
    # поэтому переносим явно.
    conf = _env_all()
    proxy = next((conf[k] for k in ("ANTHROPIC_PROXY", "AI_PROXY", "HTTPS_PROXY",
                                    "https_proxy", "ALL_PROXY", "PROXY_URL")
                  if conf.get(k)), None)
    if proxy:
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
            env[var] = proxy
    no_proxy = conf.get("NO_PROXY") or conf.get("no_proxy")
    if no_proxy:
        env["NO_PROXY"] = env["no_proxy"] = no_proxy
    # Claude Code приходит со своими инструментами и про базу BORIS не знает.
    # Отдаём ему те же руки через MCP — иначе ревьюер не может проверить то,
    # что ему показали, и честно упирается в BLOCKED_INFRA.
    # CLAUDE_MCP_CONFIG_UNIQUE_TEMP_V1:
    # A fixed /tmp/boris_mcp.json is unsafe: different service users and parallel
    # workers can collide on ownership/content and turn a healthy subscription
    # provider into a false AUTH_ERROR. Use one private config per call and
    # remove it regardless of success/failure.
    cfg_fd, cfg = tempfile.mkstemp(
        prefix="boris_mcp_", suffix=".json", dir=tempfile.gettempdir()
    )
    try:
        with os.fdopen(cfg_fd, "w", encoding="utf-8") as _cfg_file:
            _cfg_file.write(json.dumps({
                "mcpServers": {"boris": {
                    "type": "stdio",
                    "command": os.path.join(_backend_dir(), "venv", "bin", "python3"),
                    "args": ["-m", "app.ext_api.mcp_server"],
                    "cwd": _backend_dir(),
                    "env": {"PYTHONUNBUFFERED": "1",
                            "BORIS_MCP_MODE": mcp_mode,
                            "BORIS_MCP_ACTOR": actor_name or AGENT,
                            "BORIS_DEV_JOB_ID": dev_job_id or ""}}}}, ensure_ascii=False))
        mcp_tools = ["db_query", "repo_tree", "repo_read", "shell", "git", "logs", "http_check"]
        if mcp_mode == "executor":
            mcp_tools += ["propose_patch", "autonomous_apply"]
        # CLAUDE_EXTERNAL_TOOL_PRIVACY_V1:
        # Built-in Read/Grep/Glob return file bytes directly to the external
        # Claude process and bypass our MCP privacy scrubber. Give Claude only
        # BORIS MCP tools, whose outputs are sanitized in mcp_server._text.
        allowed = ",".join(
            [f"mcp__boris__{t}" for t in mcp_tools] if tools else []
        )
        argv = [cli, "-p", safe_prompt, "--output-format", "json",
                "--mcp-config", cfg, "--allowedTools", allowed]
        if os.environ.get("EXT_API_LEAD_MODEL"):
            argv += ["--model", os.environ["EXT_API_LEAD_MODEL"]]
        try:
            r = subprocess.run(argv, cwd=_backend_dir(), capture_output=True, text=True,
                               timeout=int(os.environ.get("EXT_API_LEAD_TIMEOUT", "600")),
                               env=env)
        except subprocess.TimeoutExpired:
            raise ApiError("DEPENDENCY_UNAVAILABLE", "Claude Code не уложился в отведённое время")

        if r.returncode != 0:
            err = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
            raise ApiError("DEPENDENCY_UNAVAILABLE",
                           f"Claude Code завершился с кодом {r.returncode}: {err[:1200]}")

        try:
            payload = json.loads(r.stdout or "{}")
            payload = restore_external_ai_output(payload, privacy_vault)
        except Exception:
            raise ApiError("VALIDATION_ERROR",
                           "Claude Code вернул некорректный JSON-ответ")

        # `claude -p --output-format json` normally wraps the assistant text in
        # `result`. Keep compatibility with structured/dict output and older
        # wrappers used by local test harnesses.
        raw = payload.get("result") if isinstance(payload, dict) else payload
        if raw in (None, "") and isinstance(payload, dict):
            raw = payload.get("response") or payload.get("structured_output") or payload
        return _extract_json(raw)
    finally:
        try:
            os.unlink(cfg)
        except OSError:
            pass


def _ask_gemini_cli(prompt, cwd=None, privacy_vault=None):
    """Вызов Gemini CLI headless из ограниченного рабочего каталога.

    По умолчанию старое поведение сохраняется для совместимости. Executor
    передаёт специально подготовленный маленький workspace: так Gemini CLI не
    затягивает в каждый запрос дерево всего production backend с бэкапами и
    артефактами, а видит только нужный контекст задачи.
    """
    from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
    safe_prompt, privacy_vault = safe_external_ai_payload(
        str(prompt or ""), privacy_vault
    )
    workdir = cwd or os.environ.get("BORIS_WORKER_WORKSPACE") or _backend_dir()
    try:
        os.makedirs(workdir, exist_ok=True)
    except Exception:
        workdir = _backend_dir()
    argv = ["gemini", "-p", safe_prompt, "-o", "json", "--skip-trust"]
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    try:
        r = subprocess.run(argv, cwd=workdir, capture_output=True, text=True,
                           timeout=int(os.environ.get("EXT_API_LEAD_TIMEOUT", "600")),
                           env=env)
    except subprocess.TimeoutExpired:
        raise ApiError("DEPENDENCY_UNAVAILABLE", "Gemini CLI не уложился в отведённое время")

    if r.returncode != 0:
        detail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
        raise ApiError("DEPENDENCY_UNAVAILABLE", f"Gemini CLI упал: {detail[:4000]}")

    try:
        out = json.loads(r.stdout)
        out = restore_external_ai_output(out, privacy_vault)
        return _extract_json(out.get("response") or "{}")
    except Exception:
        raise ApiError("VALIDATION_ERROR", "Gemini CLI вернул некорректный JSON-ответ")


def decide(order):
    """Одна приёмка: от REVIEW_REQUEST до вердикта."""
    p = provider()
    if p is None:
        a2a.message(order["order_id"], "DEV_PAID_PROVIDER_BLOCKED", AGENT,
                    {"providers": ["openai", "codex", "anthropic_api"],
                     "policy": "DENIED_FOR_DEV", "reason": "no free reviewer available"})
        raise ApiError("DEPENDENCY_UNAVAILABLE", "no free development reviewer available; paid fallback denied")
    if p == "mock":
        return _mock_verdict(order)
    if p == "claude_code":
        # _ask_claude_code уже возвращает разобранный вердикт. Повторный
        # разбор словаря и был причиной суточного простоя приёмки:
        # AttributeError: 'dict' object has no attribute 'strip'.
        return _ask_claude_code(
            SYSTEM + "\n\n" + review_prompt(order) + "\n" + VERDICT_FORMAT)
    if p == "gemini_cli":
        # Reviewer must inspect the same bounded source snapshot as the executor.
        # The service-level BORIS_WORKER_WORKSPACE is only a container directory;
        # it is intentionally empty until we materialize one order's allowed
        # inspection/write scope into source/. Running Gemini in that empty base
        # was the root cause of false BLOCKED_INFRA ("no repository/files").
        from . import executor as _executor
        work, _ctx = _executor._prepare_gemini_workspace(order)
        return _ask_gemini_cli(
            SYSTEM + "\n\n" + review_prompt(order) + "\n" + VERDICT_FORMAT,
            cwd=work,
        )
    if p in ("openai", "codex", "anthropic", "anthropic_api"):
        a2a.message(order["order_id"], "DEV_PAID_PROVIDER_BLOCKED", AGENT,
                    {"provider": p, "policy": "DENIED_FOR_DEV"})
        raise ApiError("DEPENDENCY_UNAVAILABLE", f"paid development reviewer blocked: {p}")
    model = pick_model()
    messages = [{"role": "user", "content": review_prompt(order)}]
    for _ in range(MAX_STEPS):
        resp = _http("/messages", {"model": model, "max_tokens": 4000,
                                   "system": SYSTEM, "tools": _tool_specs(),
                                   "messages": messages})
        blocks = resp.get("content") or []
        messages.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not uses:
            messages.append({"role": "user", "content":
                             "Ответ текстом не принимается. Проверь инструментами "
                             "и заверши вызовом submit_verdict."})
            continue
        results = []
        for u in uses:
            if u.get("name") == "submit_verdict":
                return u.get("input") or {}
            results.append({"type": "tool_result", "tool_use_id": u.get("id"),
                            "content": _cut(_call_tool(u.get("name"), u.get("input")))})
        messages.append({"role": "user", "content": results})
    raise ApiError("JOB_NOT_RETRYABLE", f"лид не вынес решение за {MAX_STEPS} шагов")


def _mock_verdict(order):
    """Детерминированный лид для тестов: смотрит на статусы критериев."""
    ev = order.get("last_evidence") or {}
    bad = [c for c in (ev.get("acceptance_criteria") or [])
           if c.get("status") != "PASS"]
    if not ev.get("evidence"):
        bad = bad or [{"criterion": "доказательство", "status": "FAIL"}]
    if bad:
        return {"verdict": "RETURN_WITH_FIXES",
                "note": "не все критерии подтверждены",
                "fixes": [{"failed_criterion": c.get("criterion", "?"),
                           "problem": f"статус {c.get('status')}",
                           "required_correction": "проверить и приложить вывод"}
                          for c in bad]}
    return {"verdict": "ACCEPT", "note": "все критерии подтверждены доказательствами"}


def handle(order):
    try:
        v = decide(order)
    except ApiError as e:
        low = (e.message or "").lower()
        from . import aiprov
        weekly_retry = aiprov.weekly_limit_retry_after(f"{e.code}: {e.message}")
        if weekly_retry:
            aiprov.mark("claude_code", aiprov.RATE_LIMITED,
                        f"weekly limit: {e.message}"[:400], retry_after=weekly_retry)
            # provider() now sees the cooldown and immediately selects fallback.
            v = decide(order)
        elif e.code == "RATE_LIMITED" or "credit balance" in low or "quota" in low \
                or "insufficient" in low:
            # Reviewer quota/availability is infrastructure, not an owner
            # decision. Keep the work saved and fail closed without notifying
            # the owner; dispatcher will requeue only after a free development
            # provider is actually usable again.
            o = a2a.review(
                LEAD_KEY,
                order["order_id"],
                "BLOCKED_INFRA",
                blocked_reason=(
                    "provider_unavailable: review provider quota/cooling; "
                    "work preserved; automatic retry after free provider recovery"
                )[:500],
            )
            return o
        else:
            raise
    if not isinstance(v, dict):
        a2a.message(order["order_id"], "REVIEWER_ERROR", AGENT,
                    {"code": "EMPTY_VERDICT", "type": type(v).__name__})
        v = {"verdict": "BLOCKED_INFRA",
             "note": "review provider returned no structured verdict",
             "blocked_reason": "review provider returned no structured verdict"}
    o = a2a.review(LEAD_KEY, order["order_id"], v.get("verdict"), v.get("note"),
                   v.get("fixes"), v.get("blocked_reason"))
    if o["status"] == a2a.ACCEPTED:
        gate = repo.reviewed_order_apply_state(o)
        if gate.get("ready"):
            _advance(o)
        else:
            a2a.message(o["order_id"], "WAIT_AUTONOMOUS_APPLY", AGENT,
                        {"patch_ids": gate.get("waiting") or [],
                         "why": "accepted patch awaits autonomous-dev apply/runtime QA"})
    if o["status"] in (a2a.BLOCKED_HUMAN,):
        notify.blocked_human(o)
    return o


PHASE_GOALS = {
    "PLANNING": "разобраться в фактическом состоянии и составить план",
    "BACKEND": "сделать серверную часть: данные, схема, штатные исполнители",
    "FRONTEND": "сделать то, что видит клиент в кабинете",
    "UX": "проверить, что путь клиента понятен и без тупиков",
    "INTEGRATION": "связать части между собой и проверить связи фактами",
    "TESTING": "прогнать проверки и приложить их вывод",
    "REGRESSION": "убедиться, что ничего из работавшего раньше не сломалось",
    "ACCEPTANCE": "закрыть оставшиеся критерии приёмки доказательствами",
    "DEPLOYMENT": "довести изменения до рабочего состояния на сервере",
    "PRODUCTION_QA": "проверить результат на боевом контуре и показать факты",
}


def _close_criteria(order, job):
    """Canonical exact reconciliation; fuzzy text matching is forbidden."""
    try:
        result = a2a.reconcile_parent_acceptance(
            LEAD_KEY, order["order_id"], emit_message=False
        )
        return list(result.get("matched_indices") or [])
    except ApiError:
        return []


def _next_phase(job):
    ph = job.get("phases") or {}
    for name in job.get("required_phases") or []:
        st = (ph.get(name) or {}).get("state")
        if st not in ("pass", "skipped"):
            return name
    return None


def _open_criteria(job):
    out = []
    for c in job["acceptance_criteria"] or []:
        text = (c.get("text") if isinstance(c, dict) else str(c)) or ""
        done = (c.get("done") if isinstance(c, dict) else False)
        if not done and text:
            out.append(text)
    return out


def _dev_job_numeric_id(dev_job_id):
    """Normalize dev_123 / 123 without an unnecessary dev.get round-trip."""
    raw = str(dev_job_id or "").strip()
    if raw.startswith("dev_"):
        raw = raw[4:]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(dev.get(dev_job_id)["id"])


def _has_open_order(dev_job_id, assigned_to=None):
    """Exact DB gate for one live sibling on very large order histories.

    A BLOCKED_INFRA/BLOCKED_HUMAN order is still the current order for the
    development job. Treating only queued/running/review/returned as live made
    reconcile revisit an old ACCEPTED parent every dispatcher tick. The shared
    create_order() fence then returned the same blocked child idempotently, but
    lead still emitted a fresh NEXT_ORDER message on every pass.

    This query therefore covers both executable and blocked live states. A
    blocked child must be recovered/unblocked, not shadowed by replaying its
    accepted parent.
    """
    job_id = _dev_job_numeric_id(dev_job_id)
    sql = """SELECT id FROM ext_a2a_orders
             WHERE dev_job_id=:d AND status = ANY(:st)"""
    params = {
        "d": job_id,
        "st": list(a2a.OPEN_STATES) + [a2a.BLOCKED_HUMAN, a2a.BLOCKED_INFRA],
    }
    if assigned_to:
        sql += " AND assigned_to=:a"
        params["a"] = assigned_to
    sql += " ORDER BY id DESC LIMIT 1"
    return bool(db.one(sql, **params))


def _latest_accepted_order_id(dev_job_id, assigned_to=None):
    """Return the actual newest accepted sibling without bounded-list bias."""
    job_id = _dev_job_numeric_id(dev_job_id)
    sql = """SELECT id FROM ext_a2a_orders
             WHERE dev_job_id=:d AND status=:s"""
    params = {"d": job_id, "s": a2a.ACCEPTED}
    if assigned_to:
        sql += " AND assigned_to=:a"
        params["a"] = assigned_to
    else:
        sql += " AND assigned_to <> ALL(:ex)"
        params["ex"] = list(a2a.TEST_AGENTS)
    sql += " ORDER BY id DESC LIMIT 1"
    row = db.one(sql, **params)
    return row["id"] if row else None


def _next_order(job, prev, phase=None, goal_override=None):
    """Наряд на следующую фазу ставит лид — сам, без человека."""
    scope=job.get("scope") or {}
    # WORKSPACE_AUTOPILOT_PAUSE_V1: pause stops only the transition to the next
    # autonomous phase. The already running atomic step may finish and keep its
    # evidence, so resume can continue from facts instead of restarting work.
    if scope.get("workspace_autopilot") is False:
        return None
    phase = phase or _next_phase(job)
    if not phase:
        return None
    open_c = _open_criteria(job)
    if not open_c:
        if scope.get("server_owned") is True and scope.get("autonomous_dev") is True:
            open_c = [
                "Фаза %s подтверждена фактическими build/tests/runtime/e2e evidence "
                "и не создаёт новых регрессий." % phase
            ]
        else:
            return None
    if _has_open_order(job["dev_job_id"]):
        return None                      # по задаче уже есть незакрытый наряд
    ev = (prev.get("last_evidence") or {})
    goal = (f"Фаза {phase}: {goal_override or PHASE_GOALS.get(phase, 'продвинуть задачу')}.\n"
            f"Задача: {job['goal']}\n"
            f"Что уже принято на прошлой фазе: {(ev.get('summary') or '—')[:600]}\n"
            "Работай только тем, что доказуемо. Чего нельзя проверить — так и пиши.")
    return a2a.create_order(
        LEAD_KEY, job["dev_job_id"], phase, goal,
        scope=job.get("scope") or {}, acceptance=open_c,
        forbidden=prev.get("forbidden_changes") or [],
        required_qa=prev.get("required_qa") or [],
        required_evidence=prev.get("required_evidence") or [],
        assigned_to=prev["assigned_to"],
        max_iterations=prev.get("max_iterations") or 6, priority=60)


def _phase_evidence(ev):
    """Render accepted structured A2A evidence into the phase-pass evidence gate."""
    ev = ev or {}
    direct = str(ev.get("evidence") or "").strip()
    if direct:
        return direct[:20000]
    parts = []
    if ev.get("summary"):
        parts.append("summary: " + str(ev["summary"]))
    for name in ("tests", "qa", "build", "runtime"):
        value = ev.get(name)
        if value:
            parts.append(f"{name}: {value}")
    criteria = []
    for c in (ev.get("acceptance_criteria") or []):
        if isinstance(c, dict) and c.get("status") == "PASS":
            criteria.append(f"{c.get('criterion')}: {c.get('evidence') or 'PASS'}")
    if criteria:
        parts.append("acceptance: " + " | ".join(criteria))
    files = ev.get("files_changed") or []
    if files:
        parts.append("files_changed: " + ", ".join(map(str, files)))
    return "\n".join(parts).strip()[:20000]


def _advance(order):
    """Принят наряд — закрываем фазу его доказательством и идём дальше.

    Без этого шага задача встаёт: фаза закрыта, а следующего наряда никто не
    ставит. Ставит его лид, здесь же, и он же закрывает задачу, когда
    закрывать больше нечего.
    """
    ev = order.get("last_evidence") or {}
    phase_evidence = _phase_evidence(ev)
    before_job=dev.get(order["dev_job_id"])
    phase_state=((before_job.get("phases") or {}).get(order.get("phase")) or {}).get("state")
    if phase_state != "pass":
        try:
            dev.update_phase(LEAD_KEY, order["dev_job_id"], order["phase"], "pass",
                             evidence=phase_evidence,
                             files_changed=ev.get("files_changed"),
                             notes=f"принято лидом, итераций {order['iteration']}")
        except ApiError as e:
            a2a.message(order["order_id"], "PHASE_ADVANCE_FAILED", AGENT,
                        {"phase": order.get("phase"), "code": e.code,
                         "why": e.message, "has_structured_evidence": bool(ev)})
            return dev.get(order["dev_job_id"])

    job = dev.get(order["dev_job_id"])
    closed = _close_criteria(order, job)
    job = dev.get(order["dev_job_id"])

    if not _open_criteria(job):
        scope = job.get("scope") or {}
        if scope.get("server_owned") is True and scope.get("autonomous_dev") is True:
            remaining_phase = _next_phase(job)
            if remaining_phase:
                try:
                    nxt = _next_order(job, order, phase=remaining_phase)
                    if nxt:
                        a2a.message(order["order_id"], "NEXT_ORDER", AGENT,
                                    {"order_id": nxt["order_id"], "phase": nxt["phase"],
                                     "why": "required_phase_remaining"})
                except ApiError as e:
                    a2a.message(order["order_id"], "NEXT_ORDER_FAILED", AGENT,
                                {"why": e.message})
                return dev.get(job["dev_job_id"])
            try:
                job = dev.complete(
                    LEAD_KEY, job["dev_job_id"],
                    summary=(ev.get("summary") or "server-owned autonomous lifecycle completed")[:2000],
                )
            except ApiError as e:
                a2a.message(order["order_id"], "COMPLETE_REJECTED", AGENT,
                            {"why": e.message, "details": e.details})
                return dev.get(job["dev_job_id"])
            if job.get("status") == "completed":
                notify.done(job)
            return job

        # Критерии кончились — но для не-server-owned задач цель может быть
        # шире первых критериев; их следующий шаг по-прежнему решает лид.
        try:
            pl = plan(job, order)
        except ApiError as e:
            a2a.message(order["order_id"], "PLAN_FAILED", AGENT, {"why": e.message})
            return job
        decision = (pl.get("decision") or "").upper()
        a2a.message(order["order_id"], "PLAN", AGENT, pl)

        if decision == "BLOCKED_HUMAN":
            a2a._set(order["order_id"], status=a2a.BLOCKED_HUMAN,
                     blocked_reason=(pl.get("reason") or "нужно решение владельца")[:500])
            notify.blocked_human(a2a.get(order["order_id"], with_messages=False))
            return dev.get(job["dev_job_id"])

        if decision == "NEXT" and pl.get("acceptance_criteria"):
            dev.add_criteria(LEAD_KEY, job["dev_job_id"], pl["acceptance_criteria"],
                             why=pl.get("reason"))
            job = dev.get(job["dev_job_id"])
            try:
                nxt = _next_order(job, order, phase=pl.get("phase"),
                                  goal_override=pl.get("goal"))
                if nxt:
                    a2a.message(order["order_id"], "NEXT_ORDER", AGENT,
                                {"order_id": nxt["order_id"], "phase": nxt["phase"],
                                 "why": pl.get("reason")})
            except ApiError as e:
                a2a.message(order["order_id"], "NEXT_ORDER_FAILED", AGENT, {"why": e.message})
            return dev.get(job["dev_job_id"])

        # DONE: фазы без работы помечаем пропущенными с причиной — это решение
        # записывается, а не прячется под фальшивым PASS
        for na in (pl.get("not_applicable_phases") or []):
            try:
                dev.update_phase(LEAD_KEY, job["dev_job_id"], na.get("phase"), "skipped",
                                 notes=f"лид: {na.get('why')}"[:500])
            except ApiError:
                pass
        try:
            job = dev.complete(LEAD_KEY, job["dev_job_id"],
                               summary=(pl.get("reason") or ev.get("summary") or "")[:2000])
        except ApiError as e:
            a2a.message(order["order_id"], "COMPLETE_REJECTED", AGENT,
                        {"why": e.message, "details": e.details})
        if job.get("status") == "completed":
            notify.done(job)
        return job

    try:
        nxt = _next_order(job, order)
        if nxt:
            a2a.message(order["order_id"], "NEXT_ORDER", AGENT,
                        {"order_id": nxt["order_id"], "phase": nxt["phase"],
                         "closed_criteria": closed})
    except ApiError as e:
        a2a.message(order["order_id"], "NEXT_ORDER_FAILED", AGENT, {"why": e.message})
    return job


def resume_autopilot_job(dev_job_id):
    """Resume a paused Workspace job from its latest accepted facts.

    Never creates parallel work: if a live order already exists, it is reused.
    If the last accepted order had already advanced the phase, _advance is
    idempotent and only creates the next missing order / completion decision.
    """
    job=dev.get(dev_job_id)
    if job.get("status") in ("completed","cancelled","failed"):
        return {"status":"terminal","dev_job_id":job.get("dev_job_id")}
    if (job.get("scope") or {}).get("workspace_autopilot") is False:
        return {"status":"paused","dev_job_id":job.get("dev_job_id")}
    if _has_open_order(job["dev_job_id"]):
        return {"status":"already_running","dev_job_id":job.get("dev_job_id")}
    latest_id=_latest_accepted_order_id(job["dev_job_id"])
    if not latest_id:
        return {"status":"waiting_for_accepted_step","dev_job_id":job.get("dev_job_id")}
    latest=a2a.get(latest_id)
    result=_advance(latest)
    return {"status":"resumed","dev_job_id":job.get("dev_job_id"),"job":result}


def reconcile(only_assigned=None, limit=20):
    """Advance accepted server-owned jobs without global ACCEPTED-queue starvation.

    The previous implementation first took the global top-200 ACCEPTED orders.
    Because a2a.listing() orders by priority DESC,id, a valid newer server-owned
    job could be absent forever behind unrelated accepted backlog. Reconciliation
    is a per-dev-job state transition, so discover active server-owned jobs first
    and inspect each job's own latest accepted order.
    """
    moved = []
    # Newest active jobs first makes a freshly accepted server-owned transition
    # visible immediately while keeping the work bounded per daemon tick.
    for job in dev.listing(limit=max(200, int(limit) * 20)):
        scope = job.get("scope") or {}
        if not (scope.get("server_owned") is True and scope.get("autonomous_dev") is True):
            continue
        if job["status"] in ("completed", "cancelled"):
            continue
        jid = job["dev_job_id"]
        if _has_open_order(jid, assigned_to=only_assigned):
            continue                      # по задаче уже идёт работа
        latest_id = _latest_accepted_order_id(jid, assigned_to=only_assigned)
        if not latest_id:
            continue
        latest_full = a2a.get(latest_id)
        gate = repo.reviewed_order_apply_state(latest_full)
        if not gate or not gate.get("ready"):
            continue
        before = job["status"]
        _advance(latest_full)
        after = dev.get(jid)
        moved.append({"dev_job_id": jid, "from": before, "to": after["status"],
                      "phase": after["current_phase"]})
        if len(moved) >= limit:
            break
    return moved


def once(only_assigned=None):
    """Review one order and also recover one accepted server-owned lifecycle."""
    moved = reconcile(only_assigned, limit=1)
    from . import aiprov
    if not aiprov.development_candidates():
        return {"reconciled": moved} if moved else None
    pend = a2a.listing(status=a2a.REVIEW, assigned_to=only_assigned, limit=1,
                       exclude_assigned=(None if only_assigned else a2a.TEST_AGENTS))
    if not pend:
        return {"reconciled": moved} if moved else None
    try:
        order = a2a.get(pend[0]["order_id"])
    except ApiError:
        # Наряд исчез между выборкой и обработкой. Это не сбой приёмки:
        # пропускаем и идём дальше, а не роняем весь цикл.
        return None
    return handle(order)


def run(idle_seconds=POLL_SECONDS, max_reviews=None, only_assigned=None):
    done = 0
    print(f"  лид {AGENT}: провайдер {provider()}")
    while max_reviews is None or done < max_reviews:
        try:
            from . import agents as _ag
            _ag.alive(AGENT)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1187)
        try:
            o = once(only_assigned)
        except ApiError as e:
            print(f"  [{e.code}] {e.message}")
            time.sleep(idle_seconds)
            continue
        except Exception as e:
            # Раньше сюда падало всё, кроме ApiError, — и служба умирала на
            # каждом обороте, а очередь приёмки стояла сутками при живом
            # systemd. Цикл обязан пережить любой один наряд.
            import traceback
            print("  лид: сбой на наряде: %s: %s\n%s"
                  % (type(e).__name__, e, traceback.format_exc()[-800:]),
                  flush=True)
            time.sleep(idle_seconds)
            continue
        if not o:
            time.sleep(idle_seconds)
            continue
        done += 1
        if isinstance(o, dict) and o.get("reconciled") is not None:
            moved = o.get("reconciled") or []
            print(f"  лид: восстановлено lifecycle-переходов {len(moved)}")
            continue
        if isinstance(o, dict) and o.get("order_id") is not None:
            print(f"  наряд {o['order_id']} -> {o.get('status')}")
            continue
        # Fail-soft logging only. once() is allowed to return non-order
        # maintenance results; a malformed informational result must not kill
        # the long-running lead service and trigger a systemd restart storm.
        print(f"  лид: шаг выполнен без order_id: {str(o)[:300]}")
    return done


def main():
    ap = argparse.ArgumentParser(prog="lead")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("once")
    sub.add_parser("reconcile")
    r = sub.add_parser("run")
    r.add_argument("--idle", type=int, default=POLL_SECONDS)
    r.add_argument("--max", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "probe":
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "reconcile":
        print(json.dumps(reconcile(), ensure_ascii=False, indent=2, default=str))
        return 0
    if a.cmd == "once":
        o = once()
        print(json.dumps(o or {"note": "нарядов на приёмке нет"}, ensure_ascii=False,
                         indent=2, default=str))
        return 0
    run(a.idle, a.max)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
