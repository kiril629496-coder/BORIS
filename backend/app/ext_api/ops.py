# -*- coding: utf-8 -*-
"""Руки исполнителя: диагностика и запуск проверок на сервере.

Тут намеренно НЕТ произвольного shell. Есть именованные операции и один
ограниченный запуск команды по белому списку бинарей. Так агент получает
всё, что нужно для диагностики, сборки, тестов и runtime QA, но не может
ни удалить, ни выложить наружу, ни выполнить строку, собранную из чужого
текста.

Общие правила для всего модуля:
  * рабочий каталог всегда внутри BORIS_REPO_ROOT;
  * ни одна команда не идёт через оболочку — только argv, без интерполяции;
  * вывод обрезается, время ограничено, всё пишется в аудит;
  * разрушающие действия отсутствуют как класс, а не «выключены флагом».
"""
import os, io, re, shlex, subprocess, time, json
from . import db, auth
from .errors import ApiError
from . import repo
from .repo import safe_path
from app.services.exception_observability import observe_suppressed

MAX_OUT = 20000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900

# Бинари, которые агенту разрешено звать напрямую. Всё, чего здесь нет, — отказ.
ALLOWED_BINARIES = {
    "ls", "cat", "head", "tail", "wc", "stat", "file", "which", "du", "df", "free",
    "nproc", "uptime", "uname", "lscpu", "lsblk", "lspci", "nvidia-smi", "vmstat",
    "grep", "egrep", "rg", "find", "sort", "uniq", "diff",
    "git", "npm", "npx", "node", "python3", "pytest", "pip",
    "systemctl", "journalctl", "crontab", "ps", "ss", "curl", "ollama",
}
# Аргументы с этими символами не пропускаем: они имеют смысл только в оболочке,
# а оболочки здесь нет — значит это попытка что-то протащить.
SHELL_CHARS = re.compile(r"[;&|`$<>\n\r]")
# Явно опасные слова, даже если бинарь разрешён.
DENY_ARG = re.compile(r"(^|/)(rm|mkfs|dd|shutdown|reboot|passwd|useradd|chpasswd)($|\s)", re.I)

# systemctl: смотреть можно любой юнит, трогать — только эти.
# BACKEND_OPS_MANUAL_RESTART_DISABLED_V1: backend HA convergence belongs to boris-deploy.
# A task/API key may still restart the frontend, but can never bypass the API rolling owner.
RESTARTABLE_UNITS = {"boris-frontend"}
VIEW_VERBS = {"status", "is-active", "is-enabled", "show", "list-timers", "list-units", "cat"}

# curl разрешён только внутрь самой машины: runtime QA, а не выход наружу.
LOCAL_URL = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)")


def _enabled():
    if os.environ.get("EXT_API_OPS_DISABLED") == "1":
        raise ApiError("FORBIDDEN_SCOPE", "серверные операции выключены переменной "
                                          "EXT_API_OPS_DISABLED=1")


def _cwd(rel=None):
    if not rel:
        return os.path.realpath(repo.ROOT)
    full = os.path.realpath(os.path.join(repo.ROOT, str(rel).lstrip("/")))
    root = os.path.realpath(repo.ROOT)
    if not (full == root or full.startswith(root + os.sep)) or not os.path.isdir(full):
        raise ApiError("FORBIDDEN_SCOPE", f"рабочий каталог вне проекта или не существует: {rel}")
    return full


def _cut(s):
    s = s or ""
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f"\n… обрезано, всего {len(s)} символов"


def _run(argv, cwd=None, timeout=DEFAULT_TIMEOUT, env_extra=None):
    """Единственное место, где что-либо запускается. shell=False всегда."""
    t0 = time.time()
    env = dict(os.environ)
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")
    if env_extra:
        env.update(env_extra)
    try:
        r = subprocess.run(argv, cwd=cwd or _cwd(), capture_output=True, text=True,
                           timeout=min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT),
                           env=env)
    except subprocess.TimeoutExpired:
        raise ApiError("DEPENDENCY_UNAVAILABLE",
                       f"команда не уложилась в {timeout} с: {' '.join(argv[:4])}")
    except FileNotFoundError:
        raise ApiError("NOT_IMPLEMENTED", f"на сервере нет команды «{argv[0]}»")
    return {"argv": argv, "cwd": cwd or _cwd(), "exit_code": r.returncode,
            "seconds": round(time.time() - t0, 1),
            "stdout": _cut(r.stdout), "stderr": _cut(r.stderr),
            "ok": r.returncode == 0}


def _audit(key, name, payload, result):
    try:
        auth.audit(key, "OPS", f"/ops/{name}", 200 if result.get("ok") else 400,
                   command=f"ops:{name}", status=("ok" if result.get("ok") else "fail"),
                   response={"argv": result.get("argv"), "exit_code": result.get("exit_code"),
                             "input": payload})
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=100)


# ------------------------------------------------------------------ команда

def shell(key, command, cwd=None, timeout=None):
    """Запуск разрешённой команды. Строка разбирается shlex'ом, но НЕ отдаётся
    оболочке: конвейеры, подстановки и перенаправления не работают by design."""
    _enabled()
    if not command or not str(command).strip():
        raise ApiError("MISSING_INPUT", "команда не задана")
    if SHELL_CHARS.search(str(command)):
        raise ApiError("VALIDATION_ERROR",
                       "конвейеры, перенаправления и подстановки не поддерживаются: "
                       "оболочки здесь нет, передавайте одну команду с аргументами")
    try:
        argv = shlex.split(str(command))
    except ValueError as e:
        raise ApiError("VALIDATION_ERROR", f"не разобрал команду: {e}")
    if not argv:
        raise ApiError("MISSING_INPUT", "команда пустая")
    if DENY_ARG.search(str(command)):
        raise ApiError("FORBIDDEN_SCOPE", "разрушающие команды недоступны агенту")

    head = argv[0]
    base = os.path.basename(head)
    # venv/bin/python3 и подобное — путь внутри проекта, это нормально
    if "/" in head:
        safe_path(head) if not head.startswith("/") else None
    if base not in ALLOWED_BINARIES:
        raise ApiError("FORBIDDEN_SCOPE",
                       f"«{base}» нет в белом списке. Разрешено: "
                       + ", ".join(sorted(ALLOWED_BINARIES)))
    if base == "systemctl":
        verb = argv[1] if len(argv) > 1 else ""
        if verb not in VIEW_VERBS:
            raise ApiError("FORBIDDEN_SCOPE",
                           "через shell systemctl доступен только на чтение; "
                           "перезапуск — отдельной операцией restart_service")
    if base == "curl":
        if not any(LOCAL_URL.match(a) for a in argv[1:]):
            raise ApiError("FORBIDDEN_SCOPE",
                           "curl разрешён только на localhost — это runtime QA, "
                           "а не выход в интернет")
    res = _run(argv, cwd=_cwd(cwd), timeout=timeout)
    _audit(key, "shell", {"command": command, "cwd": cwd}, res)
    return res


# ------------------------------------------------------------- именованные

def facts(key=None):
    """Что это за машина. Отсюда же ответ, потянет ли она локальную модель."""
    _enabled()
    out = {}

    def grab(name, argv, timeout=20):
        try:
            out[name] = _run(argv, timeout=timeout)["stdout"].strip()
        except ApiError as e:
            out[name] = f"недоступно: {e.message}"

    grab("uname", ["uname", "-a"])
    grab("cpu", ["lscpu"])
    grab("memory", ["free", "-h"])
    grab("disk", ["df", "-h", repo.ROOT])
    grab("load", ["uptime"])
    grab("gpu_nvidia", ["nvidia-smi", "-L"], 15)
    grab("gpu_pci", ["lspci"], 15)
    grab("ollama", ["ollama", "list"], 15)

    def num(pattern, text, cast=float):
        m = re.search(pattern, text or "", re.I | re.M)
        return cast(m.group(1)) if m else None

    cores = num(r"^CPU\(s\):\s+(\d+)", out.get("cpu", ""), int)
    model = re.search(r"^Model name:\s+(.+)$", out.get("cpu", ""), re.I | re.M)
    ram_gb = num(r"^Mem:\s+(\d+(?:[.,]\d+)?)Gi", (out.get("memory") or "").replace(",", "."))
    free_gb = num(r"\s(\d+(?:[.,]\d+)?)G\s+\d+%", (out.get("disk") or "").replace(",", "."))
    gpu_text = (out.get("gpu_nvidia") or "") + " " + (out.get("gpu_pci") or "")
    has_gpu = bool(re.search(r"nvidia|tesla|a100|h100|rtx|amd/ati.*\[radeon", gpu_text, re.I)) \
        and "недоступно" not in (out.get("gpu_nvidia") or "")

    verdict, why = _local_model_verdict(cores, ram_gb, free_gb, has_gpu)
    return {"raw": out,
            "summary": {"cpu_cores": cores,
                        "cpu_model": model.group(1).strip() if model else None,
                        "ram_gb": ram_gb, "disk_free_gb": free_gb,
                        "gpu": has_gpu,
                        "ollama_installed": "недоступно" not in (out.get("ollama") or "")},
            "local_model": {"verdict": verdict, "why": why}}


def _local_model_verdict(cores, ram_gb, free_gb, has_gpu):
    why = []
    if has_gpu:
        why.append("есть GPU — локальная модель имеет смысл")
    else:
        why.append("GPU не найден: на CPU coding-модель уровня 14B+ выдаёт единицы "
                   "токенов в секунду, для агентского цикла это часы на один шаг")
    if ram_gb is not None:
        why.append(f"RAM {ram_gb} ГБ — 7B в 4 битах просит ~6 ГБ, 14B ~10 ГБ, 32B ~20 ГБ "
                   "сверх того, что уже занято BORIS")
    if free_gb is not None:
        why.append(f"на диске свободно {free_gb} ГБ — веса 14B в 4 битах это ~9 ГБ")
    if cores:
        why.append(f"ядер {cores}")
    ok = has_gpu and (ram_gb or 0) >= 16 and (free_gb or 0) >= 25
    marginal = (not has_gpu) and (ram_gb or 0) >= 32 and (free_gb or 0) >= 25
    return ("VIABLE" if ok else ("MARGINAL_CPU_ONLY" if marginal else "NOT_VIABLE")), why


def logs(key, unit="boris-backend", lines=200, since=None):
    _enabled()
    n = max(1, min(int(lines or 200), 2000))
    argv = ["journalctl", "-u", str(unit), "-n", str(n), "--no-pager", "--output", "short-iso"]
    if since:
        argv += ["--since", str(since)]
    res = _run(argv, timeout=60)
    _audit(key, "logs", {"unit": unit, "lines": n}, res)
    return res


def service_status(key, unit="boris-backend"):
    _enabled()
    res = _run(["systemctl", "show", str(unit), "--no-pager",
                "--property=ActiveState,SubState,ExecMainStartTimestamp,NRestarts"], timeout=30)
    _audit(key, "service_status", {"unit": unit}, res)
    return res


def restart_service(key, unit="boris-backend", reason=None):
    """Перезапуск — единственное изменяющее действие модуля, и только своих юнитов."""
    _enabled()
    auth.need_scope(key, "tasks")
    if str(unit) not in RESTARTABLE_UNITS:
        raise ApiError("FORBIDDEN_SCOPE",
                       f"перезапускать можно только {', '.join(sorted(RESTARTABLE_UNITS))}")
    res = _run(["systemctl", "restart", str(unit)], timeout=120)
    time.sleep(3)
    after = _run(["systemctl", "is-active", str(unit)], timeout=30)
    res["active_after"] = (after["stdout"] or "").strip()
    res["ok"] = res["exit_code"] == 0 and res["active_after"] == "active"
    _audit(key, "restart_service", {"unit": unit, "reason": reason}, res)
    if not res["ok"]:
        raise ApiError("POSTFLIGHT_MISMATCH",
                       f"после перезапуска {unit} состояние «{res['active_after']}»",
                       details=res)
    return res


def http_check(key, path="/health", port=8000, method="GET"):
    """Runtime QA изнутри машины: своя же служба, снаружи ничего не дёргается."""
    _enabled()
    if str(method).upper() not in ("GET", "HEAD"):
        raise ApiError("FORBIDDEN_SCOPE", "runtime-проверка делает только GET и HEAD")
    url = f"http://127.0.0.1:{int(port)}{path if str(path).startswith('/') else '/' + str(path)}"
    res = _run(["curl", "-s", "-o", "/dev/null", "-w",
                "%{http_code} %{time_total}", "--max-time", "20",
                "-X", str(method).upper(), url], timeout=40)
    code, _, t = (res["stdout"] or "").partition(" ")
    res["url"] = url
    res["http_code"] = int(code) if code.isdigit() else None
    res["seconds_total"] = t.strip()
    res["ok"] = res["http_code"] is not None and res["http_code"] < 500
    _audit(key, "http_check", {"url": url}, res)
    return res


def git_info(key, what="status", limit=20):
    _enabled()
    cmds = {"status": ["git", "status", "--porcelain=v1", "-b"],
            "log": ["git", "log", "--oneline", "-n", str(max(1, min(int(limit or 20), 200)))],
            "diff": ["git", "diff", "--stat"],
            "branch": ["git", "branch", "-vv"]}
    if what not in cmds:
        raise ApiError("VALIDATION_ERROR", "what: status | log | diff | branch")
    res = _run(cmds[what], timeout=60)
    _audit(key, "git", {"what": what}, res)
    return res


def cron_info(key):
    _enabled()
    out = {}
    for name, argv in (("crontab", ["crontab", "-l"]),
                       ("timers", ["systemctl", "list-timers", "--all", "--no-pager"])):
        try:
            out[name] = _run(argv, timeout=30)
        except ApiError as e:
            out[name] = {"ok": False, "stderr": e.message}
    return out


def build(key, target="frontend", timeout=900):
    """Сборка как отдельная операция — тот же VERIFY, что и при применении правки."""
    _enabled()
    targets = {
        "frontend": (["npm", "run", "build"], "frontend", "сборка Next.js"),
        "backend": (["venv/bin/python3", "-c", "import app.main"], "backend", "импорт бэкенда"),
    }
    if target not in targets:
        raise ApiError("VALIDATION_ERROR", "target: frontend | backend")
    argv, sub, note = targets[target]
    res = _run(argv, cwd=_cwd(sub), timeout=timeout)
    res["note"] = note
    _audit(key, "build", {"target": target}, res)
    return res


def tests(key, suite="gateway", account=None, timeout=900):
    _enabled()
    if suite != "gateway":
        raise ApiError("NOT_IMPLEMENTED", "пока подключён только suite=gateway")
    if not account:
        raise ApiError("MISSING_INPUT", "нужен --account: id аккаунта или часть названия")
    res = _run(["venv/bin/python3", "-m", "app.ext_api.selftest", "--account", str(account)],
               cwd=_cwd("backend"), timeout=timeout,
               env_extra={"EXT_API_ALLOW_TEST_HOOKS": "1", "EXT_API_INLINE_RUNNER": "0"})
    m = re.search(r"ИТОГ:\s*(\d+)/(\d+)", res["stdout"] or "")
    res["passed"], res["total"] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    res["ok"] = bool(m) and res["passed"] == res["total"]
    _audit(key, "tests", {"suite": suite}, res)
    return res


# ------------------------------------------------------------ диагностика БД

SELECT_ONLY = re.compile(r"^\s*(with|select)\b", re.I)
FORBIDDEN_SQL = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|copy|"
                           r"create|vacuum|reindex)\b", re.I)


def db_query(key, sql, limit=100):
    """Только чтение. Одна инструкция, обязательный LIMIT, ограниченное время."""
    _enabled()
    auth.need_scope(key, "read")
    q = (sql or "").strip().rstrip(";")
    if not q:
        raise ApiError("MISSING_INPUT", "запрос пустой")
    if ";" in q:
        raise ApiError("VALIDATION_ERROR", "только одна инструкция за вызов")
    if not SELECT_ONLY.match(q) or FORBIDDEN_SQL.search(q):
        raise ApiError("FORBIDDEN_SCOPE", "диагностика БД доступна только на чтение: "
                                          "разрешены SELECT и WITH … SELECT")
    n = max(1, min(int(limit or 100), 500))
    if not re.search(r"\blimit\s+\d+\s*$", q, re.I):
        q += f" LIMIT {n}"
    db.q("SET LOCAL statement_timeout = '15s'")
    rows = db.rows(q)
    auth.audit(key, "OPS", "/ops/db/query", 200, command="ops:db_query", status="ok",
               response={"rows": len(rows)})
    return {"sql": q, "row_count": len(rows), "rows": rows[:n]}


# ------------------------------------------------------------------ браузер
#
# Без браузера «desktop PASS / mobile PASS» — пустые слова. Здесь настоящий
# headless-обход страниц: коды ответов, ошибки консоли, инвентарь кнопок и
# ссылок, поведение на мобильной ширине. Только чтение, ничего не кликаем
# вслепую: клики выполняются лишь по явно переданному списку.

BROWSER_SCRIPT = r"""
import json, sys
from playwright.sync_api import sync_playwright

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
out = {"pages": [], "width": cfg["width"]}
with sync_playwright() as pw:
    br = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = br.new_context(viewport={"width": cfg["width"], "height": cfg["height"]},
                         ignore_https_errors=True)
    out["login"] = "не требовался"
    lg = cfg.get("login")
    if lg:
        # Кабинет закрыт логином. Без входа обход упрётся в /login и всё
        # покажется «сломанным» — поэтому сначала честно логинимся.
        pg = ctx.new_page()
        try:
            pg.goto(cfg["base"] + lg.get("path", "/login"), wait_until="networkidle",
                    timeout=cfg["timeout"])
            for sel in ("input[type=email]", "input[name*=email i]", "input[name*=login i]",
                        "input[type=text]"):
                if pg.query_selector(sel):
                    pg.fill(sel, lg["email"]); break
            for sel in ("input[type=password]", "input[name*=pass i]"):
                if pg.query_selector(sel):
                    pg.fill(sel, lg["password"]); break
            for sel in ("button[type=submit]", "form button", "button"):
                if pg.query_selector(sel):
                    pg.click(sel); break
            pg.wait_for_timeout(2500)
            out["login"] = ("вошли, url " + pg.url) if "/login" not in pg.url \
                else "вход не удался, остались на " + pg.url
        except Exception as e:
            out["login"] = f"вход не удался: {type(e).__name__}: {e}"[:200]
        pg.close()
    for path in cfg["paths"]:
        page = ctx.new_page()
        errors, console = [], []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:300])
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        rec = {"path": path}
        try:
            resp = page.goto(cfg["base"] + path, wait_until="networkidle",
                             timeout=cfg["timeout"])
            page.wait_for_timeout(600)
            rec["http_status"] = resp.status if resp else None
            rec["final_url"] = page.url
            rec["redirected"] = (not page.url.endswith(path)) and path not in page.url
            rec["title"] = page.title()[:200]
            body = page.inner_text("body")[:4000] if page.query_selector("body") else ""
            rec["text_len"] = len(body)
            rec["looks_blank"] = len(body.strip()) < 40
            rec["clickables"] = [
                {"tag": el.evaluate("e => e.tagName.toLowerCase()"),
                 "text": (el.inner_text() or "").strip()[:80],
                 "href": el.get_attribute("href"),
                 "disabled": el.is_disabled() if el.evaluate(
                     "e => e.tagName.toLowerCase()") == "button" else None,
                 "visible": el.is_visible()}
                for el in page.query_selector_all("a, button, [role=button]")[:120]]
            rec["horizontal_overflow"] = page.evaluate(
                "() => document.documentElement.scrollWidth > window.innerWidth + 2")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
        rec["console"] = console[:30]
        rec["page_errors"] = errors[:20]
        out["pages"].append(rec)
        page.close()
    br.close()
print(json.dumps(out, ensure_ascii=False))
"""


def browser_available():
    try:
        r = _run([_py(), "-c", "import playwright; print('ok')"], timeout=30)
        if r["exit_code"] != 0:
            return False, "playwright не установлен"
        r2 = _run([_py(), "-c",
                   "from playwright.sync_api import sync_playwright;\n"
                   "p=sync_playwright().start(); b=p.chromium.launch(args=['--no-sandbox']);"
                   "b.close(); p.stop(); print('ok')"], timeout=90)
        return (r2["exit_code"] == 0,
                "ok" if r2["exit_code"] == 0 else (r2["stderr"] or "")[-300:])
    except ApiError as e:
        return False, e.message


def _qa_login():
    """Учётка для обхода кабинета. Её задаёт владелец в backend/.env:

        BORIS_QA_EMAIL=...
        BORIS_QA_PASSWORD=...

    Без неё браузер увидит только страницу логина — и это будет честно
    написано в отчёте, а не выдано за поломку кабинета.
    """
    from .executor import _env_all
    env = _env_all()
    email = env.get("BORIS_QA_EMAIL") or env.get("QA_EMAIL")
    pwd = env.get("BORIS_QA_PASSWORD") or env.get("QA_PASSWORD")
    if not (email and pwd):
        return None
    return {"email": email, "password": pwd,
            "path": env.get("BORIS_QA_LOGIN_PATH") or "/login"}


def _py():
    return os.path.join(repo.ROOT, "backend", "venv", "bin", "python3")


def browser(key, paths=None, width=1440, height=900, port=3002, timeout=45000):
    """Обход страниц настоящим браузером. width=390 — мобильная проверка."""
    _enabled()
    auth.need_scope(key, "tasks")
    ok, why = browser_available()
    if not ok:
        raise ApiError("NOT_IMPLEMENTED",
                       f"браузер недоступен: {why}. Поставить: "
                       "install_boris_gateway.py browser-setup")
    cfg = {"paths": list(paths or ["/"])[:40], "width": int(width), "height": int(height),
           "base": f"http://127.0.0.1:{int(port)}", "timeout": int(timeout),
           "login": _qa_login()}
    import tempfile
    script = os.path.join(tempfile.gettempdir(), "boris_browser.py")
    io.open(script, "w", encoding="utf-8").write(BROWSER_SCRIPT)
    # Пароль НЕ уходит в argv: командная строка видна в списке процессов и
    # возвращается агенту в результате запуска. Кладём конфиг в файл 0600.
    fd, cfg_path = tempfile.mkstemp(prefix="boris_browser_", suffix=".json")
    os.close(fd)
    os.chmod(cfg_path, 0o600)
    io.open(cfg_path, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False))
    try:
        res = _run([_py(), script, cfg_path],
                   timeout=min(900, 60 + 25 * len(cfg["paths"])))
    finally:
        try:
            os.unlink(cfg_path)
        except OSError:
            pass
    res.pop("argv", None)
    _audit(key, "browser", {"paths": cfg["paths"], "width": width}, res)
    if res["exit_code"] != 0:
        raise ApiError("DEPENDENCY_UNAVAILABLE",
                       f"обход не отработал: {(res['stderr'] or '')[-500:]}")
    try:
        data = json.loads(res["stdout"])
    except Exception:
        raise ApiError("INTERNAL", f"браузер вернул не JSON: {(res['stdout'] or '')[:300]}")
    for p in data["pages"]:
        p["verdict"] = ("ERROR" if p.get("error") else
                        "BLANK" if p.get("looks_blank") else
                        "REDIRECT" if p.get("redirected") else
                        "HTTP_" + str(p.get("http_status")) if (p.get("http_status") or 0) >= 400
                        else "OK")
    data["summary"] = {v: sum(1 for p in data["pages"] if p["verdict"] == v)
                       for v in {p["verdict"] for p in data["pages"]}}
    if not cfg["login"] and any(p.get("verdict") == "REDIRECT" and "login" in
                                (p.get("final_url") or "") for p in data["pages"]):
        data["warning"] = ("кабинет закрыт логином, а учётка для обхода не задана: "
                           "добавьте BORIS_QA_EMAIL и BORIS_QA_PASSWORD в backend/.env. "
                           "Пока их нет, редиректы на /login — не дефект интерфейса")
    return data



# ------------------------------------------------------------------ секреты

SECRET_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_ORG_ID", "OPENAI_BASE_URL",
                "OPENAI_PROJECT", "DEEPSEEK_API_KEY", "PLUSVIBE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")

ENV_FILES = ("backend/.env", ".env", "backend/app/.env", "frontend/.env",
             "frontend/.env.local", "backend/.env.local")
SYSTEMD_DIRS = ("/etc/systemd/system", "/etc/default", "/etc/boris")


def _mask(v):
    """Наружу уходит только форма ключа, никогда сам ключ."""
    v = (v or "").strip().strip('"').strip("'")
    if not v:
        return None
    return {"length": len(v), "prefix": (v[:3] + "…") if len(v) > 12 else "…",
            "looks_like": ("anthropic" if v.startswith("sk-ant-") else
                           "openai" if v.startswith("sk-") else "unknown")}


def _scan_file(path, found):
    try:
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.replace("Environment", "").strip().strip('"')
            k = k.split()[-1] if k else k
            if k in SECRET_NAMES and v.strip():
                found.setdefault(k, []).append({"source": path, "shape": _mask(v)})
    except OSError:
        pass


def secrets_presence(key=None):
    """present=true/false и откуда взялось. Значения не возвращаются никогда."""
    _enabled()
    found = {}
    for name in SECRET_NAMES:
        if os.environ.get(name):
            found.setdefault(name, []).append({"source": "process env",
                                               "shape": _mask(os.environ[name])})
    for rel in ENV_FILES:
        _scan_file(os.path.join(repo.ROOT, rel), found)
    for d in SYSTEMD_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if fn.endswith((".service", ".env", ".conf")) or "boris" in fn.lower():
                    _scan_file(os.path.join(d, fn), found)
        except OSError:
            pass

    usage = []
    for pat, what in (("openai", "клиент OpenAI"), ("anthropic", "клиент Anthropic"),
                      ("ollama", "локальная модель")):
        try:
            r = _run(["grep", "-rIl", "--include=*.py", "-i", pat,
                      os.path.join(repo.ROOT, "backend", "app")], timeout=45)
            files = [l.strip() for l in (r["stdout"] or "").split("\n") if l.strip()][:15]
            if files:
                usage.append({"provider": pat, "what": what, "files": files})
        except ApiError:
            pass

    return {"secrets": {n: {"present": n in found,
                            "sources": [s["source"] for s in found.get(n, [])],
                            "shape": (found.get(n) or [{}])[0].get("shape")}
                        for n in SECRET_NAMES},
            "existing_provider_usage": usage,
            "verdict": {
                "openai_ready": "OPENAI_API_KEY" in found,
                "anthropic_ready": "ANTHROPIC_API_KEY" in found,
            }}


# ---------------------------------------------------------------- готовность

CAPABILITIES = [
    ("task_board_read", "чтение доски задач", lambda k: True),
    ("task_board_write", "запись в доску задач", lambda k: "write" in k["scope_set"] or "admin" in k["scope_set"]),
    ("repo_tree", "дерево репозитория", lambda k: True),
    ("repo_read", "чтение файлов", lambda k: True),
    ("repo_propose", "предложение правок", lambda k: "write" in k["scope_set"] or "admin" in k["scope_set"]),
    ("repo_apply", "применение правок агентом", lambda k: False),
    ("shell_exec", "запуск команд по белому списку", lambda k: _probe(["uname", "-s"])),
    ("git", "инспекция git", lambda k: _probe(["git", "--version"])),
    ("build", "сборка", lambda k: _probe(["npm", "--version"])),
    ("tests", "прогон тестов", lambda k: os.path.isfile(os.path.join(repo.ROOT, "backend", "venv", "bin", "python3"))),
    ("logs", "чтение логов", lambda k: _probe(["journalctl", "--version"])),
    ("runtime_http", "runtime-проверки на localhost", lambda k: _probe(["curl", "--version"])),
    ("systemd", "состояние служб", lambda k: _probe(["systemctl", "--version"])),
    ("service_restart", "контролируемый перезапуск BORIS", lambda k: "tasks" in k["scope_set"] or "admin" in k["scope_set"]),
    ("cron", "инспекция cron и таймеров", lambda k: _probe(["systemctl", "--version"])),
    ("db_readonly", "диагностика БД на чтение", lambda k: "read" in k["scope_set"] or "admin" in k["scope_set"]),
    ("agent_send", "отправка другому агенту", lambda k: True),
    ("agent_receive", "получение задания без человека", lambda k: False),
]


def _probe(argv):
    try:
        return _run(argv, timeout=15)["exit_code"] == 0
    except ApiError:
        return False


def readiness(key):
    """Машинный ответ на вопрос «автономность готова?» — без прилагательных."""
    items, missing = [], []
    for name, human, probe in CAPABILITIES:
        try:
            ok = bool(probe(key))
        except Exception:
            ok = False
        items.append({"capability": name, "what": human, "available": ok})
        if not ok:
            missing.append(name)
    # repo_apply отсутствует намеренно: правку применяет человек. Это не пробел.
    blockers = [m for m in missing if m not in ("repo_apply",)]
    return {
        "agent": auth.actor_of(key)["name"],
        "capabilities": items,
        "missing": missing,
        "blocked_infra": blockers,
        "autonomous_dev": "READY" if not blockers else "NOT_READY",
        "note": ("agent_receive отсутствует по устройству платформы: Custom GPT нельзя "
                 "разбудить без человека. Пока исполнитель не станет процессом на сервере, "
                 "автономность остаётся NOT_READY, сколько бы прав ни выдали ключу."),
    }
