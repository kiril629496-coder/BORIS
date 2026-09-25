# -*- coding: utf-8 -*-
from pathlib import Path
from app.services.exception_observability import observe_suppressed
"""Серверный исполнитель: GPT как процесс, а не как вкладка в браузере.

Забирает наряд из a2a, работает инструментами BORIS (repo, ops, dev) и
возвращает REVIEW_REQUEST с настоящими доказательствами. Никакого человека
в цикле: наряд приходит от лида, замечания возвращаются сюда же.

    venv/bin/python3 -m app.ext_api.executor once      # один наряд
    venv/bin/python3 -m app.ext_api.executor run       # демон
    venv/bin/python3 -m app.ext_api.executor probe     # что за движок доступен

Провайдер выбирается сам: openai (боевой) или mock (тесты, без сети).
"""
import os, io, json, re, time, argparse, sys, threading, shutil, subprocess
from . import db, auth, repo, ops, dev, a2a
from .errors import ApiError

MAX_STEPS = 24                 # шагов диалога с моделью на одну итерацию
MAX_TOOL_OUTPUT = 12000
POLL_SECONDS = 10

AGENT = os.environ.get("EXT_API_EXECUTOR_AGENT", "gpt-executor")
# Личность конкретного процесса в пуле: gpt-executor-3 и так далее. Пул один,
# воркеров много — по имени видно, кто именно ведёт наряд.
WORKER = os.environ.get("BORIS_WORKER_NAME") or AGENT
WORKSPACE = os.environ.get("BORIS_WORKER_WORKSPACE") or ""

# Ключ исполнителя не ходит по HTTP: воркер живёт внутри того же процесса,
# что и шлюз. Права те же, что у обычного агентского ключа.
EXECUTOR_KEY = {"scope_set": {"read", "write", "tasks"}, "actor_type": "ai_agent",
                "actor_name": AGENT, "name": AGENT, "id": None,
                "max_items_per_call": 200, "allowed_accounts": None}
AUTONOMOUS_DEV_KEY = dict(EXECUTOR_KEY, actor_type="autonomous-dev")

MODEL_PREFERENCE = [r"^gpt-5.*codex", r"^gpt-5\.\d", r"^gpt-5$", r"^gpt-4\.1$",
                    r"^gpt-4o$", r"^o4-mini$", r"^gpt-4\.1-mini$"]


# ------------------------------------------------------------------ провайдер

def _api_key():
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(os.path.dirname(here))
    for cand in (os.path.join(backend, ".env"), os.path.join(backend, "app", ".env")):
        if os.path.exists(cand):
            for line in io.open(cand, encoding="utf-8", errors="ignore"):
                if line.strip().startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# --- транспорт -------------------------------------------------------------
#
# С российского IP OpenAI отвечает unsupported_country_region_territory. При
# этом сам BORIS в OpenAI ходит — значит рабочий путь в конфиге уже есть.
# Не выспрашиваем его у владельца: находим сами и проверяем перебором.

BASE_URL_VARS = ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_URL", "OPENAI_GATEWAY")
PROXY_VARS = ("OPENAI_PROXY", "OPENAI_HTTP_PROXY", "OPENAI_HTTPS_PROXY", "PROXY_URL",
              "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy",
              "AI_PROXY", "PROXY")
_TRANSPORT = None


def _backend_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _env_all():
    """Переменные из окружения и из .env — значения нужны для работы,
    наружу они не отдаются никогда."""
    vals = dict(os.environ)
    b = _backend_dir()
    for cand in (os.path.join(b, ".env"), os.path.join(b, "app", ".env")):
        if not os.path.exists(cand):
            continue
        for line in io.open(cand, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return vals


def _grep_sources(pattern, limit=40):
    """Как ходит в OpenAI сам BORIS — читаем его же исходники."""
    out, root = [], os.path.join(_backend_dir(), "app")
    rx = re.compile(pattern)
    for dirpath, dirnames, filenames in os.walk(root):
        if "ext_api" in dirpath or "__pycache__" in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            try:
                text = io.open(os.path.join(dirpath, fn), encoding="utf-8",
                               errors="ignore").read()
            except OSError:
                continue
            for m in rx.finditer(text):
                out.append(m.group(1))
                if len(out) >= limit:
                    return out
    return out


def _host(url):
    m = re.match(r"^[a-z]+://(?:[^@/]*@)?([^/:]+)", str(url or ""), re.I)
    return m.group(1) if m else None


def candidates():
    """Список способов дозвониться, в порядке правдоподобия."""
    env = _env_all()
    seen, out = set(), []

    def add(base, proxy, why):
        k = (base, proxy)
        if k in seen:
            return
        seen.add(k)
        out.append({"base_url": base, "proxy": proxy, "why": why})

    for v in BASE_URL_VARS:
        if env.get(v):
            add(env[v].rstrip("/"), None, f"{v} из конфигурации")
    for lit in _grep_sources(r"base_url\s*=\s*[\"\']([a-z]+://[^\"\']+)[\"\']"):
        add(lit.rstrip("/"), None, "base_url зашит в коде BORIS")
    for var in _grep_sources(r"base_url\s*=\s*os\.(?:environ\.get|getenv)\(\s*[\"\'](\w+)[\"\']"):
        if env.get(var):
            add(env[var].rstrip("/"), None, f"base_url берётся из {var}")
    for v in PROXY_VARS:
        if env.get(v):
            add(None, env[v], f"прокси из {v}")
    for var in _grep_sources(r"(?:proxy|proxies)\s*=\s*os\.(?:environ\.get|getenv)\(\s*[\"\'](\w+)[\"\']"):
        if env.get(var):
            add(None, env[var], f"прокси берётся из {var}")
    for lit in _grep_sources(r"(?:proxy|proxies)\s*=\s*[\"\']([a-z]+://[^\"\']+)[\"\']"):
        add(None, lit, "прокси зашит в коде BORIS")
    add(None, None, "напрямую")
    return out


def _opener(proxy):
    import urllib.request
    if not proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def _try(path, payload, method, timeout, base, proxy):
    import urllib.request, urllib.error
    key = _api_key()
    if not key:
        raise ApiError("AVITO_CREDENTIALS_MISSING",
                       "OPENAI_API_KEY не найден ни в окружении, ни в backend/.env")
    url = (base or "https://api.openai.com/v1").rstrip("/") + path
    from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
    external_payload, privacy_vault = safe_external_ai_payload(payload)
    req = urllib.request.Request(
        url, method=method,
        data=(json.dumps(external_payload).encode("utf-8") if external_payload is not None else None),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with _opener(proxy).open(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
            return restore_external_ai_output(body, privacy_vault)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:1200]
        if e.code in (401, 403):
            raise ApiError("UNAUTHORIZED", f"OpenAI отверг запрос: {body}")
        if e.code == 429:
            raise ApiError("RATE_LIMITED", f"OpenAI: лимит или баланс — {body}")
        raise ApiError("DEPENDENCY_UNAVAILABLE", f"OpenAI {e.code}: {body}")
    except ApiError:
        raise
    except Exception as e:
        raise ApiError("DEPENDENCY_UNAVAILABLE", f"не достучался: {type(e).__name__}: {e}")


def transport(force=False):
    """Подбирает рабочий способ один раз и запоминает."""
    global _TRANSPORT
    if _TRANSPORT and not force:
        return _TRANSPORT
    tried = []
    for c in candidates():
        try:
            _try("/models", None, "GET", 45, c["base_url"], c["proxy"])
            _TRANSPORT = dict(c, ok=True, tried=tried)
            return _TRANSPORT
        except ApiError as e:
            tried.append({"why": c["why"], "host": _host(c["base_url"] or c["proxy"]),
                          "error": f"{e.code}: {e.message[:160]}"})
    raise ApiError("DEPENDENCY_UNAVAILABLE",
                   "ни один способ добраться до OpenAI не сработал",
                   details={"tried": tried})


def _http(path, payload=None, method="POST", timeout=300):
    t = transport()
    return _try(path, payload, method, timeout, t["base_url"], t["proxy"])


def provider(order=None):
    """Чем выполнять этот наряд.

    Движок выбирается на уровне наряда, а не процесса: часть задач владелец
    прямо запретил гонять через OpenAI, потому что это его деньги, а Claude
    у нас уже оплачен подпиской. Раньше движок был один на всю службу — и
    такой запрет было нечем исполнить.
    """
    forced = (os.environ.get("EXT_API_EXECUTOR_PROVIDER") or "").lower()
    if order:
        scope = order.get("scope") or {}
        want = (scope.get("engine") or "").lower()
        if want in ("claude_code", "openai", "mock"):
            return want
    if forced:
        return forced

    # Спросить маршрутизатор, а не ходить в OpenAI вслепую. 21.08 у OpenAI
    # кончились кредиты, и все восемь исполнителей встали разом: каждый
    # получал «You have no credits remaining» и уходил в блокировку, хотя
    # оплаченная подписка Claude работала всё это время.
    try:
        from . import aiprov
        aiprov.apply_owner_state()
        able = aiprov.candidates(aiprov.CODING)

        if able:
            # candidates() is the single availability authority.
            # Never silently resurrect Claude after aiprov marked it unavailable.
            return able[0]
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=242)
    # FAIL CLOSED:
    # If aiprov has no real CODING provider, never silently fall back
    # to OpenAI or mock. OpenAI may have no credits and mock is not a
    # real development executor.
    raise ApiError(
        "DEPENDENCY_UNAVAILABLE",
        "нет доступного реального coding-провайдера; "
        "Claude Code/OpenAI недоступны, mock запрещён для реальной разработки",
    )


def pick_model():
    forced = os.environ.get("EXT_API_EXECUTOR_MODEL")
    if forced:
        return forced
    data = _http("/models", method="GET", timeout=60).get("data", [])
    names = sorted(m.get("id", "") for m in data)
    for pat in MODEL_PREFERENCE:
        hit = [n for n in names if re.match(pat, n)]
        if hit:
            return sorted(hit)[-1]
    if not names:
        raise ApiError("DEPENDENCY_UNAVAILABLE", "OpenAI не вернул ни одной модели")
    return names[-1]


def probe():
    """Что за движок реально доступен. Ни ключи, ни строки прокси наружу не идут."""
    p = provider()
    out = {"provider": p, "api_key_present": bool(_api_key())}
    if p != "openai":
        out.update({"model": "mock", "ok": True,
                    "note": "боевой ключ не найден или задан EXT_API_EXECUTOR_PROVIDER=mock"})
        return out
    try:
        t = transport(force=True)
        out["transport"] = {"kind": ("base_url" if t["base_url"] else
                                     ("proxy" if t["proxy"] else "direct")),
                            "why": t["why"], "host": _host(t["base_url"] or t["proxy"])}
        out["rejected"] = t.get("tried") or []
        out["model"] = pick_model()
        out["ok"] = True
    except ApiError as e:
        out["ok"] = False
        out["error"] = f"{e.code}: {e.message}"
        out["tried"] = (e.details or {}).get("tried")
    return out


# --------------------------------------------------------------- инструменты

def _cut(s):
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_TOOL_OUTPUT else s[:MAX_TOOL_OUTPUT] + "\n… обрезано"


def _tool_repo_tree(order, paths=None, **_):
    return repo.tree(paths)


def _tool_repo_read(order, paths=None, path=None, start=None, end=None, **_):
    if paths:
        return repo.read_many(paths)
    return repo.read_file(path, start or 1, end)


def _tool_propose_patch(order, edits=None, note=None, **_):
    return repo.propose(EXECUTOR_KEY, order["dev_job_id"], edits or [], note)


def _tool_apply_patch(order, patch_id=None, **_):
    if patch_id is None:
        raise ApiError("MISSING_INPUT", "apply_dev_patch: patch_id обязателен")
    return repo.autonomous_apply(AUTONOMOUS_DEV_KEY, int(patch_id))


def _tool_shell(order, command=None, cwd=None, timeout=None, **_):
    return ops.shell(EXECUTOR_KEY, command, cwd, timeout)


def _tool_build(order, target="frontend", **_):
    return ops.build(EXECUTOR_KEY, target)


def _tool_tests(order, suite="gateway", account=None, **_):
    return ops.tests(EXECUTOR_KEY, suite, account or os.environ.get("EXT_API_TEST_ACCOUNT"))


def _tool_logs(order, unit="boris-backend", lines=200, **_):
    return ops.logs(EXECUTOR_KEY, unit, lines)


def _tool_http(order, path="/health", port=8000, **_):
    return ops.http_check(EXECUTOR_KEY, path, port)


def _tool_git(order, what="status", **_):
    return ops.git_info(EXECUTOR_KEY, what)


def _tool_db(order, sql=None, limit=100, **_):
    return ops.db_query(EXECUTOR_KEY, sql, limit)


def _tool_browser(order, paths=None, width=1440, port=3002, **_):
    return ops.browser(EXECUTOR_KEY, paths, width, 900, port)


def _tool_facts(order, **_):
    return ops.facts(EXECUTOR_KEY)



def _tool_submit_review(
    order,
    summary=None,
    files_changed=None,
    build=None,
    tests=None,
    runtime=None,
    acceptance_criteria=None,
    evidence=None,
    blockers=None,
    safety=None,
    **_,
):
    """Submit executor evidence through the real A2A REVIEW_REQUEST transition."""
    from . import a2a, auth

    if not summary:
        return {
            "error": "MISSING_INPUT",
            "message": "submit_review: summary обязателен",
        }

    if not evidence:
        return {
            "error": "MISSING_INPUT",
            "message": "submit_review: evidence обязателен",
        }

    if not acceptance_criteria:
        return {
            "error": "MISSING_INPUT",
            "message": "submit_review: acceptance_criteria обязателен",
        }

    author = auth.actor_of(EXECUTOR_KEY)["name"]

    return a2a.submit_review(
        order["order_id"],
        author,
        summary,
        files_changed=files_changed or [],
        build=build,
        tests=tests,
        runtime=runtime,
        criteria=acceptance_criteria or [],
        evidence=evidence,
        blockers=blockers or [],
        safety=safety,
    )

TOOLS = {
    "repo_tree": (_tool_repo_tree, "Дерево файлов проекта. Секреты и бэкапы не отдаются.",
                  {"paths": {"type": "array", "items": {"type": "string"}}}),
    "repo_read": (_tool_repo_read, "Прочитать файл или несколько файлов проекта.",
                  {"path": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}},
                   "start": {"type": "integer"}, "end": {"type": "integer"}}),
    "propose_patch": (_tool_propose_patch,
                      "Предложить development patch. Это ещё не изменение на диске.",
                      {"edits": {"type": "array", "items": {
                          "type": "object",
                          "properties": {"path": {"type": "string"},
                                         "mode": {"type": "string", "enum": ["replace", "full"]},
                                         "old": {"type": "string"}, "new": {"type": "string"},
                                         "content": {"type": "string"}},
                          "required": ["path"]}},
                       "note": {"type": "string"}}),
    "apply_dev_patch": (_tool_apply_patch,
                        "Применить собственный proposed/approved patch через autonomous-dev policy. "
                        "Scope/locks/hard-deny/backup/verify/compile/tests обязательны; skip_verify нет.",
                        {"patch_id": {"type": "integer"}}),
    "shell": (_tool_shell, "Одна команда из белого списка, без оболочки и конвейеров.",
              {"command": {"type": "string"}, "cwd": {"type": "string"},
               "timeout": {"type": "integer"}}),
    "build": (_tool_build, "Сборка: frontend (npm run build) или backend (import app.main).",
              {"target": {"type": "string", "enum": ["frontend", "backend"]}}),
    "run_tests": (_tool_tests, "Прогон автотестов шлюза.",
                  {"suite": {"type": "string"}, "account": {"type": "string"}}),
    "logs": (_tool_logs, "Хвост журнала службы.",
             {"unit": {"type": "string"}, "lines": {"type": "integer"}}),
    "http_check": (_tool_http, "Runtime-проверка на localhost.",
                   {"path": {"type": "string"}, "port": {"type": "integer"}}),
    "git": (_tool_git, "Инспекция git: status, log, diff, branch.",
            {"what": {"type": "string"}}),
    "db_query": (_tool_db, "Диагностика БД только на чтение.",
                 {"sql": {"type": "string"}, "limit": {"type": "integer"}}),
    "browser": (_tool_browser,
                "Обойти страницы кабинета настоящим браузером: коды ответов, ошибки "
                "консоли, пустые экраны, редиректы, инвентарь кнопок и ссылок, "
                "горизонтальное переполнение. width=390 — мобильная проверка.",
                {"paths": {"type": "array", "items": {"type": "string"}},
                 "width": {"type": "integer"}, "port": {"type": "integer"}}),
    "server_facts": (_tool_facts, "Железо и окружение сервера.", {}),
    "submit_review": (_tool_submit_review,
                      "Предложить правку. old должен встречаться в текущем файле ровно один раз. "
                      "Правку применяет человек, поэтому сначала убедись, что фрагмент точный.",
                      {"edits": {"type": "array", "items": {
                          "type": "object",
                          "properties": {"path": {"type": "string"},
                                         "mode": {"type": "string", "enum": ["replace", "full"]},
                                         "old": {"type": "string"}, "new": {"type": "string"},
                                         "content": {"type": "string"}},
                          "required": ["path"]}},
                       "note": {"type": "string"}}),
}

SUBMIT = {
    "type": "function",
    "function": {
        "name": "submit_review",
        "description": ("Отдать работу на ревью лиду. Вызывать ОДИН раз, когда работа "
                        "сделана и проверена. Без evidence отчёт не примут."),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "что сделано, кратко"},
                "files_changed": {"type": "array", "items": {"type": "string"}},
                "build": {"type": "string", "description": "результат сборки словами + код возврата"},
                "tests": {"type": "string"},
                "runtime": {"type": "string"},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {"criterion": {"type": "string"},
                                             "status": {"type": "string",
                                                        "enum": ["PASS", "FAIL", "NOT_CHECKED"]},
                                             "evidence": {"type": "string"}},
                              "required": ["criterion", "status"]}},
                "evidence": {"type": "string",
                             "description": "настоящий вывод команд, не пересказ"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "safety": {"type": "string"},
            },
            "required": ["summary", "evidence", "acceptance_criteria"],
        },
    },
}


_STYLE = {}


def api_style(model):
    """Новые модели OpenAI живут на /v1/responses, старые — на /v1/chat/completions.
    Угадываем по имени, но окончательное слово за самим API: он прямо пишет,
    каким эндпоинтом пользоваться, и мы это запоминаем."""
    forced = os.environ.get("EXT_API_EXECUTOR_API")
    if forced:
        return forced
    if model in _STYLE:
        return _STYLE[model]
    guess = "responses" if re.match(r"^(gpt-5|o[134]|gpt-6)", model or "") else "chat"
    _STYLE[model] = guess
    return guess


def _switch_style(model, err_text):
    """Меняем стиль, если API сам подсказал другой эндпоинт."""
    low = (err_text or "").lower()
    if "v1/responses" in low:
        _STYLE[model] = "responses"
        return True
    if "v1/chat/completions" in low or "chat/completions" in low:
        _STYLE[model] = "chat"
        return True
    return False


def responses_tool_specs():
    """У /v1/responses описание инструмента плоское, без вложенного function."""
    specs = [{"type": "function", "name": n, "description": d,
              "parameters": {"type": "object", "properties": p}}
             for n, (_f, d, p) in TOOLS.items()]
    f = SUBMIT["function"]
    specs.append({"type": "function", "name": f["name"], "description": f["description"],
                  "parameters": f["parameters"]})
    return specs


def tool_specs():
    specs = []
    for name, (_fn, desc, props) in TOOLS.items():
        specs.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props}}})
    specs.append(SUBMIT)
    return specs


def call_tool(order, name, args):
    fn = TOOLS.get(name, (None,))[0]
    if not fn:
        return {"error": f"инструмента «{name}» нет"}
    try:
        return fn(order, **(args or {}))
    except ApiError as e:
        return {"error": e.code, "message": e.message, "details": e.details}
    except Exception as e:
        return {"error": "INTERNAL", "message": f"{type(e).__name__}: {e}"[:400]}


# ------------------------------------------------------------------ промпты

SYSTEM = """Ты — исполнитель разработки BORIS. Работаешь на сервере через инструменты,
человека в контуре нет.

Правила, которые не обсуждаются:
1. Никаких выдуманных результатов. Если ты не запускал сборку — не пиши, что она прошла.
   Доказательство это вывод команды, а не твоя уверенность.
2. Перед правкой ФАЙЛ ЧИТАЙ. Фрагмент old должен совпадать с диском байт в байт,
   иначе правку отклонят. Не подгоняй файл под память.
3. Работай только внутри scope наряда. Что перечислено в forbidden_changes — не трогать.
4. Каждый критерий приёмки закрывай отдельно, со своим доказательством.
   Не проверил — ставь NOT_CHECKED, это честнее, чем PASS наугад.
5. Если нужного инструмента нет — так и скажи в blockers. Не изображай, что сделал.
6. Для изменения кода порядок обязателен: repo_read → propose_patch → apply_dev_patch. Пока
   apply_dev_patch не вернул status=applied, файл НЕ считается изменённым.
7. После apply проверь runtime/E2E доступными инструментами. Compile/tests из apply считаются
   доказанными только по фактическому verify_output/exit_code.
8. Закончив, вызови submit_review ровно один раз.

Если тебе вернули замечания — исправляй именно их, перепроверяй затронутое
и присылай новое доказательство."""



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



def order_prompt(order):
    body = {
        "task_id": order["dev_job_id"], "order_id": order["order_id"],
        "phase": order["phase"], "goal": order["goal"], "scope": order["scope"],
        "acceptance_criteria": order["acceptance_criteria"],
        "forbidden_changes": order["forbidden_changes"],
        "required_qa": order["required_qa"],
        "required_evidence": order["required_evidence"],
        "iteration": order["iteration"],
    }
    text = "EXECUTOR_ORDER\n" + json.dumps(body, ensure_ascii=False, indent=2)
    rev = order.get("last_review") or {}
    if rev.get("fixes"):
        text += ("\n\nRETURN_WITH_FIXES — лид вернул работу. Исправь именно это:\n"
                 + json.dumps(rev["fixes"], ensure_ascii=False, indent=2))
        if rev.get("note"):
            text += f"\n\nКомментарий лида: {rev['note']}"
    try:
        cp = a2a.resume_checkpoint(order["order_id"])
    except Exception:
        cp = None
    if cp:
        text += (
            "\n\nRESUME_CHECKPOINT — продолжай тот же order, не повторяй уже "
            "доказанные шаги без необходимости. Поле _latest_transient, если есть, "
            "описывает только последнее состояние инфраструктуры и не отменяет "
            "доказанный прогресс:\n"
            + json.dumps(cp, ensure_ascii=False, indent=2, default=str)
        )
    return _development_contract() + "\n\n" + (
        text
    )


# -------------------------------------------------------------------- цикл

def _record(order_id, agent, prov, model, iteration):
    return db.one("""INSERT INTO ext_a2a_runs
                       (order_id, agent, provider, model, iteration, steps, tool_calls,
                        prompt_tokens, completion_tokens, outcome)
                     VALUES (:o,:a,:p,:m,:i,0,0,0,0,'running') RETURNING id""",
                  o=order_id, a=agent, p=prov, m=model, i=iteration)["id"]


def _finish(run_id, **f):
    sets = ", ".join(f"{k}=:{k}" for k in f)
    db.q(f"UPDATE ext_a2a_runs SET {sets}, finished_at=NOW() WHERE id=:__i",
         __i=run_id, **f)


def _run_claude(order, model=None):
    """Работа исполнителя на подписке Max: тот же CLI, что у приёмки.

    Никаких обращений к платному API здесь нет — это принципиально, задача
    так и поставлена: «ноль вызовов OpenAI».
    """
    from . import lead
    out = lead._ask_claude_code(SYSTEM + "\n\n" + order_prompt(order) +
                                "\n\nВерни ТОЛЬКО JSON с полями summary, "
                                "files_changed, build, tests, runtime, "
                                "acceptance_criteria, evidence, blockers, safety.",
                                mcp_mode="executor", dev_job_id=order["dev_job_id"],
                                actor_name=AGENT)
    return (out if isinstance(out, dict) else {"summary": str(out)}), 1, 0, 0, 0


_GEMINI_CONTEXT_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".sh", ".md", ".sql", ".yaml", ".yml"}
_GEMINI_CONTEXT_SKIP = (".bak", ".before", "backup", "archive", "__pycache__", ".next", "node_modules", ".git")
_GEMINI_CONTEXT_INCIDENTAL_MAX_FILE_BYTES = 180000
_GEMINI_CONTEXT_EXPLICIT_MAX_FILE_BYTES = 300000
_GEMINI_CONTEXT_MAX_TOTAL_BYTES = 650000


def _gemini_terms(order):
    raw = " ".join([str(order.get("goal") or ""), str(order.get("phase") or ""),
                    " ".join(str(x) for x in (order.get("locks") or []))]).lower()
    known = [x for x in ("telegram", "vk", "vkontakte", "posting", "social", "banner",
                         "avito", "mop", "rop", "crm", "proxy", "feed", "marketing")
             if x in raw]
    return known[:5]


def _gemini_context_candidates(order, max_files=28):
    """Bounded source snapshot for Gemini CLI; never copies secrets/backups.

    Development scopes are historically mixed: some are repo-root relative
    (backend/app/..., frontend/...), while canonical workstream PLANNING scopes
    are backend-relative (app/..., tests/...) and read-only paths live in
    inspection_paths/read_only_paths.  Resolve all of those forms here so the
    bounded workspace contains the files BORIS explicitly allowed the agent to
    inspect, without widening the agent to the whole repository.
    """
    found = []
    seen = set()

    def resolve(rel):
        raw = str(rel or "").strip().replace("\\", "/").rstrip("/")
        if not raw:
            return None, None
        while raw.startswith("./"):
            raw = raw[2:]
        candidates = []
        if raw == "../frontend":
            candidates.append("frontend")
        elif raw.startswith("../frontend/"):
            candidates.append("frontend/" + raw[len("../frontend/"):])
        elif raw == "../backend":
            candidates.append("backend")
        elif raw.startswith("../backend/"):
            candidates.append("backend/" + raw[len("../backend/"):])
        if not raw.startswith("../"):
            candidates.append(raw.lstrip("/"))
            # ext_api scopes commonly use backend-relative canonical paths.
            if not raw.startswith(("backend/", "frontend/", "services/", "docs/",
                                   "ai/", "docker/", "integrations/", "database/")):
                candidates.append("backend/" + raw.lstrip("/"))
        for cand in candidates:
            try:
                full = repo.safe_path(cand)
            except Exception:
                continue
            if os.path.exists(full):
                return cand, full
        return None, None

    def add(rel, explicit=False):
        rel, full = resolve(rel)
        if not rel or rel in seen:
            return
        if len(found) >= max_files:
            if explicit:
                raise ApiError(
                    "CONTEXT_TRUNCATED",
                    f"explicit scoped file omitted by file-count limit: {rel}",
                )
            return
        low = rel.lower()
        if any(x in low for x in _GEMINI_CONTEXT_SKIP):
            if explicit:
                raise ApiError(
                    "CONTEXT_TRUNCATED",
                    f"explicit scoped file denied by context safety rules: {rel}",
                )
            return
        ext = os.path.splitext(rel)[1].lower()
        if ext not in _GEMINI_CONTEXT_EXT:
            if explicit:
                raise ApiError(
                    "CONTEXT_TRUNCATED",
                    f"explicit scoped file has unsupported context type: {rel}",
                )
            return
        if not os.path.isfile(full):
            return
        size = os.path.getsize(full)
        limit = (
            _GEMINI_CONTEXT_EXPLICIT_MAX_FILE_BYTES
            if explicit else _GEMINI_CONTEXT_INCIDENTAL_MAX_FILE_BYTES
        )
        if size > limit:
            if explicit:
                raise ApiError(
                    "CONTEXT_TRUNCATED",
                    f"explicit scoped file too large for bounded context: "
                    f"{rel} ({size} bytes > {limit})",
                )
            return
        seen.add(rel)
        found.append(rel)

    scope = order.get("scope") or {}
    scope_keys = ("paths", "files", "allowed_paths", "inspection_paths", "read_only_paths")

    def scoped_values(obj):
        out = []
        for key in scope_keys:
            value = (obj or {}).get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                for item in value:
                    if item not in out:
                        out.append(item)
        return out

    scope_values = scoped_values(scope)
    if not scope_values:
        try:
            parent_scope = (dev.get(order.get("dev_job_id")) or {}).get("scope") or {}
            if scoped_values(parent_scope):
                scope = parent_scope
                scope_values = scoped_values(scope)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=805)

    # Directly scoped files are authoritative context and must be considered
    # before hints/search results. They may use the larger explicit-file bound,
    # but are never silently omitted.
    for root in scope_values:
        root_rel, full = resolve(root)
        if root_rel and full and os.path.isfile(full):
            add(root_rel, explicit=True)

    try:
        job_title = str((dev.get(order.get("dev_job_id")) or {}).get("title") or "")
    except Exception:
        job_title = ""
    task_low = (str(order.get("goal") or "") + " " + job_title).lower()
    hints = []
    if "telegram" in task_low or "в telegram" in task_low:
        hints += ["backend/posting_runner.py", "backend/app/api/posting.py",
                  "backend/tests/test_social_posting_ha_contract.py",
                  "backend/tests/test_social_banner_delivery.py",
                  "backend/tests/test_ocean_autopost_contract.py"]
    if "вконтакт" in task_low or " vк" in task_low or " vk" in task_low:
        hints += ["backend/posting_runner.py", "backend/app/api/posting.py",
                  "backend/posting_stats.py", "backend/tests/test_social_posting_ha_contract.py",
                  "backend/tests/test_social_banner_delivery.py",
                  "backend/tests/test_ocean_autopost_contract.py"]
    if "banner" in task_low or "баннер" in task_low:
        hints += ["backend/posting_runner.py", "backend/banner_generator.py",
                  "backend/app/services/image_service.py",
                  "backend/tests/test_social_banner_delivery.py",
                  "backend/tests/test_ocean_autopost_contract.py"]
    for rel in hints:
        add(rel)

    for root in scope_values:
        root_rel, full = resolve(root)
        if not root_rel or not full:
            continue
        if os.path.isfile(full):
            add(root_rel)
            continue
        if not os.path.isdir(full):
            continue
        for base, dirs, files in os.walk(full):
            dirs[:] = [d for d in dirs if not any(x in d.lower() for x in _GEMINI_CONTEXT_SKIP)]
            for name in sorted(files):
                rel = os.path.relpath(os.path.join(base, name), repo.ROOT)
                add(rel)
                if len(found) >= max_files:
                    break
            if len(found) >= max_files:
                break

    # Old backlog scopes sometimes point to a historical directory. Search a
    # few task-specific terms, but only return filenames and only inside backend.
    if len(found) < 3:
        terms = _gemini_terms(order)
        for term in terms:
            try:
                p = subprocess.run(
                    ["rg", "-l", "-i", "--glob", "*.py", "--glob", "*.ts", "--glob", "*.tsx",
                     "--glob", "!*.bak*", "--glob", "!*.before*", term, os.path.join(repo.ROOT, "backend")],
                    capture_output=True, text=True, timeout=12)
                for full in (p.stdout or "").splitlines()[:14]:
                    try:
                        rel = os.path.relpath(full, repo.ROOT)
                    except Exception:
                        continue
                    add(rel)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=875)
            if len(found) >= max_files:
                break
    return found


def _gemini_direct_scope_files(order):
    """Resolve only file paths explicitly named by the order/parent scope."""
    scope = order.get("scope") or {}
    keys = ("paths", "files", "allowed_paths", "inspection_paths", "read_only_paths")

    def vals(obj):
        out = []
        for key in keys:
            value = (obj or {}).get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                for item in value:
                    if item not in out:
                        out.append(item)
        return out

    raw_values = vals(scope)
    if not raw_values:
        try:
            raw_values = vals((dev.get(order.get("dev_job_id")) or {}).get("scope") or {})
        except Exception:
            raw_values = []

    out = []
    for value in raw_values:
        raw = str(value or "").strip().replace("\\", "/").rstrip("/")
        while raw.startswith("./"):
            raw = raw[2:]
        candidates = []
        if raw.startswith("../frontend/"):
            candidates.append("frontend/" + raw[len("../frontend/"):])
        elif raw.startswith("../backend/"):
            candidates.append("backend/" + raw[len("../backend/"):])
        elif raw == "../frontend":
            candidates.append("frontend")
        elif raw == "../backend":
            candidates.append("backend")
        elif raw and not raw.startswith("../"):
            candidates.append(raw.lstrip("/"))
            if not raw.startswith(("backend/", "frontend/", "services/", "docs/",
                                   "ai/", "docker/", "integrations/", "database/")):
                candidates.append("backend/" + raw.lstrip("/"))
        for rel in candidates:
            try:
                full = repo.safe_path(rel)
            except Exception:
                continue
            if os.path.isfile(full):
                if rel not in out:
                    out.append(rel)
                break
    return out


def _prepare_gemini_workspace(order):
    """Create a tiny disposable read-only-ish source snapshot for one order."""
    base = WORKSPACE or os.path.join(repo.ROOT, "updates", "workers", WORKER)
    os.makedirs(base, exist_ok=True)
    work = os.path.join(base, "gemini_order_%s" % int(order["order_id"]))
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    from app.services.ai_privacy import PrivacyVault, safe_external_ai_payload
    privacy_vault = PrivacyVault()
    explicit_files = set(_gemini_direct_scope_files(order))
    files = _gemini_context_candidates(order)
    copied = []
    total = 0
    for rel in files:
        try:
            src = repo.safe_path(rel)
            size = os.path.getsize(src)
            if total + size > _GEMINI_CONTEXT_MAX_TOTAL_BYTES:
                if rel in explicit_files:
                    raise ApiError(
                        "CONTEXT_TRUNCATED",
                        f"explicit scoped file exceeds bounded workspace total limit: "
                        f"{rel} (total would be {total + size} bytes > "
                        f"{_GEMINI_CONTEXT_MAX_TOTAL_BYTES})",
                    )
                continue
            dst = os.path.join(work, "source", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, "rb") as source_fh:
                raw_bytes = source_fh.read()
            try:
                raw_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                if rel in explicit_files:
                    raise ApiError(
                        "CONTEXT_TRUNCATED",
                        f"binary scoped file is not allowed in external AI workspace: {rel}",
                    )
                continue
            safe_text, privacy_vault = safe_external_ai_payload(
                raw_text, privacy_vault
            )
            with io.open(dst, "w", encoding="utf-8") as dst_fh:
                dst_fh.write(safe_text)
            os.chmod(dst, 0o444)
            copied.append(rel)
            total += size
        except ApiError:
            raise
        except Exception:
            if rel in explicit_files:
                raise ApiError(
                    "CONTEXT_TRUNCATED",
                    f"explicit scoped file could not be materialized: {rel}",
                )
            continue
    missing_explicit = sorted(explicit_files.difference(copied))
    if missing_explicit:
        raise ApiError(
            "CONTEXT_TRUNCATED",
            "explicit scoped files missing from bounded workspace: "
            + ", ".join(missing_explicit),
        )
    context = {
        "order_id": order.get("order_id"), "dev_job_id": order.get("dev_job_id"),
        "phase": order.get("phase"), "scope": order.get("scope") or {},
        "source_snapshot_files": copied, "source_snapshot_bytes": total,
        "note": "Files under source/ are bounded copies for analysis. Production source of truth remains /root/BORIS. Do not claim a production edit from changing this workspace."
    }
    safe_context, privacy_vault = safe_external_ai_payload(
        context, privacy_vault
    )
    with io.open(os.path.join(work, "BORIS_CONTEXT.json"), "w", encoding="utf-8") as fh:
        json.dump(safe_context, fh, ensure_ascii=False, indent=2, default=str)
    # Keep the reversible map only in this local process. Never write it into
    # the workspace where the external Gemini process could read it.
    context["_privacy_vault"] = privacy_vault
    return work, context


def _run_gemini(order, model=None):
    """Работа исполнителя через Gemini CLI с ограниченным task workspace."""
    from . import lead
    work, ctx = _prepare_gemini_workspace(order)
    prompt = (SYSTEM + "\n\n" + order_prompt(order) +
              "\n\nЛокальный workspace содержит bounded-копии исходников в source/. "
              "Это контекст чтения, а не production-файлы. Если задача требует правок, "
              "не притворяйся, что изменил production: верни точные proposed_edits "
              "(path, mode=replace, old, new) на основе прочитанных копий. "
              "Если задача диагностическая — proposed_edits оставь пустым.\n\n"
              "Верни ТОЛЬКО JSON с полями summary, proposed_edits, files_changed, "
              "build, tests, runtime, e2e, acceptance_criteria, evidence, blockers, safety.")
    out = lead._ask_gemini_cli(
        prompt, cwd=work, privacy_vault=ctx.get("_privacy_vault")
    )
    result = out if isinstance(out, dict) else {"summary": str(out)}
    result["_gemini_workspace"] = {"path": work, "files": len(ctx["source_snapshot_files"]),
                                    "bytes": ctx["source_snapshot_bytes"]}
    return result, 1, 0, 0, 0


def _materialize_model_patch(order, result):
    """Convert Gemini proposed_edits into a real BORIS patch record, never a direct write."""
    edits = result.pop("proposed_edits", None) or []
    if not edits:
        return None
    if not isinstance(edits, list):
        raise ApiError("VALIDATION_ERROR", "proposed_edits должен быть списком")
    job = dev.get(order["dev_job_id"])
    scope = job.get("scope") or {}
    if not (scope.get("server_owned") is True and scope.get("autonomous_dev") is True):
        # Old/non-server-owned jobs may be diagnosed by Gemini but may not gain
        # a hidden autonomous write path merely because the model suggested one.
        result.setdefault("blockers", []).append(
            "Модель предложила правку, но dev_job не имеет server_owned+autonomous_dev контракта; production не изменён.")
        return None
    patch = repo.propose(AUTONOMOUS_DEV_KEY, order["dev_job_id"], edits,
                         note="Gemini bounded-workspace proposal for order %s" % order["order_id"])
    result["_proposed_patch_id"] = patch.get("patch_id")
    result["files_changed"] = list(patch.get("files") or [])
    evidence = result.get("evidence")
    line = "BORIS_PATCH_PROPOSED id=%s files=%s" % (
        patch.get("patch_id"), ",".join(patch.get("files") or []))
    if isinstance(evidence, str):
        result["evidence"] = (evidence + "\n" + line).strip()
    elif isinstance(evidence, list):
        result["evidence"] = evidence + [line]
    else:
        result["evidence"] = line
    return patch


def _validate_real_change_evidence(order, result):
    files = list(result.get("files_changed") or [])
    if not files:
        return
    patches = repo.list_patches(order["dev_job_id"], limit=100)
    proposed_id = result.get("_proposed_patch_id")
    if proposed_id:
        matching = [p for p in patches if p.get("patch_id") == proposed_id and
                    p.get("status") in ("proposed", "approved", "applied") and
                    p.get("author") == AGENT]
        proven = {x for p in matching for x in (p.get("files") or [])}
        missing = [p for p in files if p not in proven]
        if not matching or missing:
            raise ApiError("POSTFLIGHT_UNVERIFIED",
                           "files_changed не подтверждены созданным BORIS patch: " + ", ".join(missing or files))
        return
    applied = [p for p in patches if p.get("status") == "applied" and p.get("author") == AGENT]
    if not applied:
        raise ApiError("POSTFLIGHT_UNVERIFIED",
                       "Executor заявил files_changed без BORIS patch/applied evidence")
    proven = set()
    for patch in applied:
        proven.update(patch.get("files") or [])
    missing = [p for p in files if p not in proven]
    if missing:
        raise ApiError("POSTFLIGHT_UNVERIFIED",
                       "files_changed не подтверждены applied patch: " + ", ".join(missing))


def _lease_loop(order_id, worker, stop, interval=20):
    """Keep a concrete worker + order lease fresh during blocking work."""
    from . import agents as _ag
    while not stop.wait(0 if not getattr(stop, "_boris_started", False) else interval):
        stop._boris_started = True
        try:
            _ag.alive(worker)
            if not a2a.heartbeat_claim(order_id, worker):
                return
        except Exception as _suppressed_exc:
            # Lease failure must not crash executor work; another daemon will
            # requeue it once the lease itself becomes stale.
            observe_suppressed(__name__, _suppressed_exc, line=1108)


def _run_provider(order, prov):
    model = ("mock" if prov == "mock"
             else ("claude code (подписка Max)" if prov == "claude_code"
                   else ("gemini cli" if prov == "gemini_cli"
                   else pick_model())))
    run_id = _record(order["order_id"], AGENT, prov, model, order["iteration"])
    steps = calls = ptok = ctok = 0
    try:
        if prov == "mock":
            result = _mock_run(order)
            calls = result.pop("_calls", 0)
        elif prov == "claude_code":
            result, steps, calls, ptok, ctok = _run_claude(order, model)
        elif prov == "gemini_cli":
            result, steps, calls, ptok, ctok = _run_gemini(order, model)
        else:
            result, steps, calls, ptok, ctok = _converse(order, model)
        _finish(run_id, steps=steps, tool_calls=calls, prompt_tokens=ptok,
                completion_tokens=ctok, outcome="provider_ok")
        return {"result": result, "provider": prov, "model": model,
                "steps": steps, "calls": calls, "ptok": ptok, "ctok": ctok}
    except ApiError as e:
        _finish(run_id, steps=steps, tool_calls=calls, prompt_tokens=ptok,
                completion_tokens=ctok, outcome="failed",
                error_text=f"{e.code}: {e.message}"[:2000])
        raise


def _set_order_if_live(order_id, **fields):
    """Mutate executor lifecycle only while the order is still open/live.

    A stale worker/test/provider callback may finish after the order was
    cancelled, superseded or accepted. Such a callback must never resurrect
    terminal work or violate the one-live-order-per-dev-job invariant.
    """
    try:
        current = a2a.get(int(order_id), with_messages=False)
    except Exception:
        return False
    if current.get("status") not in a2a.OPEN_STATES:
        try:
            a2a.message(int(order_id), "STALE_ORDER_STATE_WRITE_SKIPPED", AGENT, {
                "current_status": current.get("status"),
                "attempted_fields": sorted(fields.keys()),
            })
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1159)
        return False
    a2a._set(int(order_id), **fields)
    return True


def _run_with_provider_failover(order):
    """Development-only provider policy: Gemini primary; never paid fallback."""
    from . import aiprov
    forced = (os.environ.get("EXT_API_EXECUTOR_PROVIDER") or "").lower()
    scope = order.get("scope") or {}
    wanted = (scope.get("engine") or "").lower()
    requested = forced or wanted
    test_mode = requested == "mock" and os.environ.get("BORIS_DEV_ALLOW_MOCK") == "1"
    if requested:
        if requested in aiprov.DEV_PAID_DENY:
            a2a.message(order["order_id"], "DEV_PAID_PROVIDER_BLOCKED", AGENT,
                        {"provider": requested, "policy": "DENIED_FOR_DEV"})
            raise ApiError("DEPENDENCY_UNAVAILABLE",
                           f"development paid provider blocked by owner policy: {requested}")
        if requested == "mock" and test_mode:
            return _run_provider(order, "mock")
        if requested not in aiprov.development_candidates():
            raise ApiError("DEPENDENCY_UNAVAILABLE",
                           f"development provider unavailable by policy/state: {requested}")
        return _run_provider(order, requested)

    runners = {
        "gemini_cli": lambda: _run_provider(order, "gemini_cli"),
        "claude_code": lambda: _run_provider(order, "claude_code"),
    }
    def _on_switch(name, kind, detail):
        a2a.checkpoint(order["order_id"], AGENT, {
            "reason": "provider_failover",
            "provider": name,
            "kind": kind,
            "detail": detail[:500],
            "iteration": order.get("iteration"),
        })
        a2a.message(
            order["order_id"], "PROVIDER_FAILOVER", AGENT,
            {"provider": name, "kind": kind, "reason": detail[:500]},
        )

    routed = aiprov.call(
        aiprov.CODING,
        runners,
        idem_key=f"executor-provider:{order['order_id']}:{order['iteration']}",
        candidate_names=aiprov.development_candidates(),
        on_switch=_on_switch,
    )
    if not routed.get("ok"):
        attempts = routed.get("attempts") or []
        a2a.message(order["order_id"], "DEV_PAID_PROVIDER_BLOCKED", AGENT,
                    {"providers": sorted(aiprov.DEV_PAID_DENY),
                     "policy": "NO_PAID_FALLBACK_AFTER_GEMINI_FAILURE"})
        reason = "all free development providers unavailable; paid fallback denied: " + json.dumps(
            attempts[-6:], ensure_ascii=False, default=str)
        a2a.checkpoint(order["order_id"], AGENT, {
            "reason": "all_free_providers_unavailable",
            "provider_attempts": attempts[-6:],
            "iteration": order.get("iteration"),
        })
        a2a.message(order["order_id"], "EXECUTOR_ERROR", AGENT,
                    {"code": "DEPENDENCY_UNAVAILABLE", "message": reason[:1800],
                     "provider_attempts": attempts[-6:]})
        _set_order_if_live(order["order_id"], status=a2a.BLOCKED_INFRA,
                           blocked_reason=reason[:500])
        raise ApiError("DEPENDENCY_UNAVAILABLE", reason[:1800])
    return routed["result"]


def execute(order):
    """Одна итерация работы над нарядом: от получения до REVIEW_REQUEST."""
    worker = worker_name()
    lease_stop = threading.Event()
    lease_thread = threading.Thread(
        target=_lease_loop,
        args=(order["order_id"], worker, lease_stop),
        name=f"lease-{order['order_id']}",
        daemon=True,
    )
    lease_thread.start()
    steps = calls = ptok = ctok = 0
    try:
        attempt = _run_with_provider_failover(order)
        result = attempt["result"]
        prov = attempt["provider"]
        model = attempt["model"]
        steps, calls = attempt["steps"], attempt["calls"]
        ptok, ctok = attempt["ptok"], attempt["ctok"]
        _materialize_model_patch(order, result)
        _validate_real_change_evidence(order, result)
        o = a2a.submit_review(
            order["order_id"], AGENT,
            summary=result.get("summary"), files_changed=result.get("files_changed"),
            build=result.get("build"), tests=result.get("tests"),
            runtime=result.get("runtime"), e2e=result.get("e2e"),
            criteria=result.get("acceptance_criteria"),
            evidence=result.get("evidence"), blockers=result.get("blockers"),
            safety=result.get("safety"),
            execution={
                "worker": worker, "provider": prov, "model": model,
                "steps": steps, "tool_calls": calls,
                "prompt_tokens": ptok, "completion_tokens": ctok,
            })
        return o

    except ApiError as e:
        a2a.message(order["order_id"], "EXECUTOR_ERROR", AGENT,
                    {"code": e.code, "message": e.message})
        if e.code in (
            "UNAUTHORIZED",
            "RATE_LIMITED",
            "AVITO_CREDENTIALS_MISSING",
            "DEPENDENCY_UNAVAILABLE",
        ):
            # Fail closed when no real coding provider exists.
            #
            # ext_a2a_orders.assigned_to is NOT NULL, therefore we must
            # NOT clear assigned_to here and must NOT return the order to
            # the queue: doing so would immediately feed the same unavailable
            # executor again.
            #
            # Provider exhaustion/outage is an internal development failure,
            # never a human decision. Keep the same order and let A2A recovery
            # requeue it for routing/failover when a provider is available.
            _set_order_if_live(
                order["order_id"],
                status=a2a.BLOCKED_INFRA,
                blocked_reason=(
                    f"provider_unavailable: {e.message}"
                )[:500],
            )
        elif e.code in ("MISSING_INPUT", "VALIDATION_ERROR", "POSTFLIGHT_UNVERIFIED"):
            try:
                a2a.checkpoint(order["order_id"], AGENT, {
                    "reason": e.code,
                    "message": e.message[:1000],
                    "iteration": order.get("iteration"),
                })
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=1301)
            # The executor produced an invalid/incomplete REVIEW_REQUEST or
            # unverifiable evidence. This is an autonomous retry condition,
            # not a reason to strand the order in RUNNING forever. Return the
            # same order to the queue; the daemon pool will claim it again.
            a2a.release_order(
                order["order_id"],
                reason=f"executor retry after {e.code}: {e.message}",
                actor=AGENT,
            )
        else:
            _set_order_if_live(order["order_id"], status=a2a.RUNNING)
        raise
    finally:
        lease_stop.set()
        lease_thread.join(timeout=1)


def _converse(order, model):
    """Диалог с моделью до вызова submit_review. Стиль API выбирается сам."""
    for attempt in (1, 2):
        style = api_style(model)
        try:
            if style == "responses":
                return _loop_responses(order, model)
            return _loop_chat(order, model)
        except ApiError as e:
            if attempt == 1 and _switch_style(model, e.message):
                continue
            raise
    raise ApiError("DEPENDENCY_UNAVAILABLE", "не подобрал рабочий эндпоинт OpenAI")


def _loop_responses(order, model):
    tools = responses_tool_specs()
    payload = {"model": model, "instructions": SYSTEM, "tools": tools,
               "tool_choice": "auto",
               "input": [{"role": "user", "content": order_prompt(order)}]}
    steps = calls = ptok = ctok = 0
    while steps < MAX_STEPS:
        steps += 1
        resp = _http("/responses", payload)
        u = resp.get("usage") or {}
        ptok += int(u.get("input_tokens") or 0)
        ctok += int(u.get("output_tokens") or 0)
        out = resp.get("output") or []
        fcs = [o for o in out if o.get("type") == "function_call"]
        nxt = []
        if not fcs:
            nxt.append({"role": "user", "content":
                        "Ответ текстом не принимается. Работай инструментами "
                        "и заверши вызовом submit_review."})
        for fc in fcs:
            calls += 1
            name = fc.get("name")
            try:
                args = json.loads(fc.get("arguments") or "{}")
            except Exception:
                args = {}
            if name == "submit_review":
                return args, steps, calls, ptok, ctok
            nxt.append({"type": "function_call_output", "call_id": fc.get("call_id"),
                        "output": _cut(call_tool(order, name, args))})
        # состояние диалога держит сам OpenAI — досылаем только новое
        payload = {"model": model, "tools": tools, "tool_choice": "auto",
                   "previous_response_id": resp.get("id"), "input": nxt}
    raise ApiError("JOB_NOT_RETRYABLE",
                   f"исполнитель не завершил работу за {MAX_STEPS} шагов")


def _loop_chat(order, model):
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": order_prompt(order)}]
    steps = calls = ptok = ctok = 0
    while steps < MAX_STEPS:
        steps += 1
        resp = _http("/chat/completions", {"model": model, "messages": messages,
                                           "tools": tool_specs(), "tool_choice": "auto",
                                           "parallel_tool_calls": False})
        u = resp.get("usage") or {}
        ptok += int(u.get("prompt_tokens") or 0)
        ctok += int(u.get("completion_tokens") or 0)
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        messages.append({k: v for k, v in msg.items()
                         if k in ("role", "content", "tool_calls")})
        tcs = msg.get("tool_calls") or []
        if not tcs:
            messages.append({"role": "user", "content":
                             "Ответ текстом не принимается. Работай инструментами "
                             "и заверши вызовом submit_review."})
            continue
        for tc in tcs:
            calls += 1
            fname = (tc.get("function") or {}).get("name")
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except Exception:
                args = {}
            if fname == "submit_review":
                return args, steps, calls, ptok, ctok
            messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                             "content": _cut(call_tool(order, fname, args))})
    raise ApiError("JOB_NOT_RETRYABLE",
                   f"исполнитель не завершил работу за {MAX_STEPS} шагов")


# ------------------------------------------------------------------- mock

def _mock_run(order):
    """Детерминированный исполнитель для тестов: без сети, но с настоящими
    инструментами. На первой итерации намеренно оставляет один критерий
    непроверенным — чтобы контур RETURN_WITH_FIXES проверялся по-настоящему."""
    calls = 0
    tree = call_tool(order, "repo_tree", {"paths": (order.get("scope") or {}).get("paths")})
    calls += 1
    files = [f["path"] for f in (tree.get("files") or [])][:3]
    read = call_tool(order, "repo_read", {"paths": files}) if files else {"files": []}
    calls += 1
    uname = call_tool(order, "shell", {"command": "uname -s"})
    calls += 1
    ev = (f"repo_tree: файлов {len(tree.get('files') or [])}\n"
          f"repo_read: прочитано {len(read.get('files') or [])}\n"
          f"shell uname -s: код {uname.get('exit_code')}, вывод {(uname.get('stdout') or '').strip()}")
    crits = order["acceptance_criteria"]
    first = order["iteration"] == 0
    out = []
    for i, c in enumerate(crits):
        if first and i == len(crits) - 1:
            out.append({"criterion": c, "status": "NOT_CHECKED",
                        "evidence": "не успел проверить"})
        else:
            out.append({"criterion": c, "status": "PASS", "evidence": ev})
    return {"summary": ("первичный проход" if first else "замечания лида исправлены"),
            "files_changed": files, "build": "не запускалась: правок кода не было",
            "tests": "не запускались", "runtime": uname.get("stdout", "").strip(),
            "acceptance_criteria": out, "evidence": ev,
            "blockers": [], "safety": "изменений на диске не делал",
            "_calls": calls}


# ------------------------------------------------------------------- воркер

def worker_name():
    """Имя процесса в пуле. Если оно не задано службой — это сам пул.

    Читается на месте, а не при импорте: имя агента могут подменить
    (например, приёмочный прогон работает под своим именем), и подпись под
    сообщением обязана совпадать с тем, кто реально работает.
    """
    return os.environ.get("BORIS_WORKER_NAME") or AGENT


def once():
    # Do not consume/re-block queue items while every allowed FREE development
    # provider is cooling/unavailable. Paid OpenAI/Codex fallback is forbidden.
    from . import aiprov
    if not aiprov.development_candidates():
        return None
    got = a2a.claim(AGENT, 1, worker=worker_name())
    if not got:
        return None
    return execute(got[0])


def once_order(order_id):
    """Claim and execute exactly one A2A order, without scanning the queue.

    The caller selects only the order id. Pool identity remains AGENT
    (gpt-executor), so an external chat agent cannot impersonate the executor
    or consume an order assigned to another autonomous pool.
    """
    order = a2a.claim_order(int(order_id), AGENT, worker=worker_name())
    return execute(order)


def run(idle_seconds=POLL_SECONDS, max_orders=None):
    done = 0
    # Do not crash the daemon at startup merely because every provider is
    # temporarily unavailable. Provider selection belongs to each order and
    # participates in normal retry/failover.
    print(f"  исполнитель {AGENT}: autonomous retry/failover enabled")
    try:
        recovered = a2a.recover_stranded_executor_orders(AGENT, actor=worker_name())
        if recovered:
            print(f"  восстановлены зависшие наряды: {recovered}")
    except Exception as e:
        # Recovery must not prevent the executor daemon from serving fresh work.
        print(f"  startup recovery warning: {e}")
    while max_orders is None or done < max_orders:
        try:
            from . import agents as _ag
            _ag.alive(AGENT)
            if worker_name() != AGENT:
                _ag.alive(worker_name())
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1496)
        try:
            stale_minutes = int(os.environ.get("EXT_API_EXECUTOR_LEASE_MINUTES", "3"))
            a2a.release_stale_claims(stale_minutes)
            o = once()
        except ApiError as e:
            print(f"  [{e.code}] {e.message}")
            time.sleep(idle_seconds)
            continue
        if not o:
            time.sleep(idle_seconds)
            continue
        done += 1
        print(f"  наряд {o['order_id']} -> {o['status']} (итерация {o['iteration']})")
    return done


def main():
    ap = argparse.ArgumentParser(prog="executor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("once")
    r = sub.add_parser("run")
    r.add_argument("--idle", type=int, default=POLL_SECONDS)
    r.add_argument("--max", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "probe":
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "once":
        o = once()
        print(json.dumps(o or {"note": "нарядов нет"}, ensure_ascii=False,
                         indent=2, default=str))
        return 0
    run(a.idle, a.max)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
